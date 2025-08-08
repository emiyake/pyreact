from typing import Dict
from weakref import WeakSet
from core import VNode 
from runtime import schedule_rerender
import asyncio

import warnings

class HookContext:
    _services: Dict = {}

    @classmethod
    def get_service(cls, key, factory):
        return cls._services.setdefault(key, factory())

    def __init__(self, name, component_fn, *, props=None, key=None) -> None:
        self.name = name
        self.component_fn = component_fn  
        self.props = props or {}
        self.key = key

        self.hooks: list = []
        self.effects: list = []
        self.children: list["HookContext"] = []
        self.hook_idx: int = 0
        self._ctx_subs: list[WeakSet] = [] 


    def use_state(self, initial):
        idx = self.hook_idx
        if idx >= len(self.hooks):
            self.hooks.append(initial)

        def set_state(val):
            nonlocal idx
            if callable(val):
                val = val(self.hooks[idx])
            
            if val != self.hooks[idx]:
                self.hooks[idx] = val
                schedule_rerender(self)

        self.hook_idx += 1
        return self.hooks[idx], set_state

    def use_effect(self, effect_fn, deps):

        deps_key = None if deps is None else tuple(deps)    # [] → ()   (objeto imutável)
   
        idx = self.hook_idx

        if idx >= len(self.hooks):            # primeiro mount
            self.hooks.append((None, deps_key))
            self.effects.append((effect_fn, deps_key, idx))
        else:                                 # updates
            old_cleanup, old_deps = self.hooks[idx]
            if deps_key is not None and old_deps != deps_key: # deps mudaram
                self.effects.append((effect_fn, deps_key, idx))
                self.hooks[idx] = (old_cleanup, deps_key)

        self.hook_idx += 1

    def use_callback(self, fn, deps=None):

        deps_key = None if deps is None else tuple(deps)    # [] → ()   (objeto imutável)

        idx = self.hook_idx

        if idx >= len(self.hooks):            # primeira vez
            self.hooks.append((fn, deps_key))
        else:
            cached_fn, old_deps = self.hooks[idx]
            if deps_key is not None and old_deps != deps_key:
                self.hooks[idx] = (fn, deps_key)  # deps mudaram → novo fn
            else:
                fn = cached_fn                    # usa memo

        self.hook_idx += 1
        return fn
    
    def use_memo(self, factory, deps=None):

        key = None if deps is None else tuple(deps)  # [] → ()   (objeto imutável)
        idx = self.hook_idx

        if idx >= len(self.hooks):               # primeira vez
            self.hooks.append((factory(), key))
        else:
            value, old_key = self.hooks[idx]
            if key is not None and old_key != key:
                value = factory()                # deps mudaram
                self.hooks[idx] = (value, key)

        self.hook_idx += 1
        return self.hooks[idx][0]
    
    def use_context(self, ctx_like):
        ctx = getattr(ctx_like, "_ctx", ctx_like)

        subscribe = getattr(ctx_like, "_subscribe", None)
        if subscribe is not None:
            subscribe(self)                        
            subs_set = getattr(ctx_like, "_subs")

            
            if subs_set not in self.__dict__.setdefault("_ctx_subs", []):   # guarda referência p/ poder executar no unmount
                self._ctx_subs.append(subs_set)

        idx   = self.hook_idx
        value = ctx.get()

        if idx >= len(self.hooks):
            self.hooks.append(value)
        elif self.hooks[idx] != value:
            self.hooks[idx] = value
            schedule_rerender(self)

        self.hook_idx += 1
        return value

    def _run_cleanup_slot(self, slot):
        cleanup = slot[0] if isinstance(slot, tuple) else None
        if cleanup:
            try:
                if asyncio.iscoroutinefunction(cleanup):
                    asyncio.create_task(cleanup())
                else:
                    cleanup()
            except Exception:
                pass

    def unmount(self):
        
        for slot in self.hooks:                     # 1. clean-ups gerados por use_effect
            if isinstance(slot, tuple):
                cleanup, _deps = slot
                if cleanup:
                    try:
                        if asyncio.iscoroutinefunction(cleanup):
                            asyncio.create_task(cleanup())      # roda assíncrono em background
                        else:
                            cleanup()
                    except Exception:
                        pass

        for ws in getattr(self, "_ctx_subs", []):   # 2. remove-se dos WeakSets de assinantes de Context
            ws.discard(self)
            
        
        if hasattr(self, "_ctx_subs"):              # 3. libera a lista para GC
            self._ctx_subs.clear()

        for child in self.children:                 # 4. desmonta recursivamente os filhos
            child.unmount()

        
        self.children.clear()                       # 5. forca GC
        self.hooks.clear()
        self.effects.clear()

    def render(self):
        import core
        token = core._context_stack.set(self)

        try:
            self.hook_idx = 0
            self.effects = []

            old_children = self.children                # 1. guarda os filhos antigos e começa uma nova lista vazia
            self.children = []


            output = self.component_fn(__internal=True, **self.props)       # 2. executa a função do componente
            vnodes = output if isinstance(output, list) else [output]


            
            for idx, vnode in enumerate(vnodes):        # 3. reconciliação – reaproveita ou cria contextos-filho
                if not isinstance(vnode, VNode):
                    continue

                vnode_key = vnode.key if vnode.key is not None else f"__idx_{idx}"

                
                matched = next(                         # 4. procura match em old_children
                    (c for c in old_children
                     if (c.key if c.key is not None else f"__idx_{old_children.index(c)}") == vnode_key
                        and c.component_fn is vnode.component_fn),
                    None
                )

                if vnode.key is None:               # 5. warn se há irmãos duplicados sem key

                    dup = any(
                        (c.component_fn is vnode.component_fn and c.key is None)
                        for c in self.children
                    )
                    if dup:
                        warnings.warn(
                            f"\n\n⚠️ [HookContext] Sibling <{vnode.component_fn.__name__}> with no explicit 'key'; it can cause extra re-render.",
                            RuntimeWarning,
                            stacklevel=2,
                        )

                if matched is None:
                    matched = HookContext(
                        vnode.component_fn.__name__,
                        vnode.component_fn,
                        props=vnode.props,
                        key=vnode.key,
                    )
                else:
                    matched.props = vnode.props

                self.children.append(matched)

            
            for orphan in old_children:         # 6. desmonta órfãos recursivamente
                if orphan not in self.children:
                    orphan.unmount()

            
            for child in self.children:         # 7. renderiza recursivamente os filhos atuais
                child.render()
        finally:
            
            core._context_stack.reset(token)    # 8. restaura o componente anterior e o stack original

    async def run_effects(self):
        for fx, deps, idx in self.effects:
            cln, _ = self.hooks[idx]
            if cln:
                if asyncio.iscoroutinefunction(cln):
                    await cln()
                else:
                    cln()
            res = fx()
            if asyncio.iscoroutine(res):
                res = await res
            self.hooks[idx] = ((res if callable(res) else None), deps)

        for ch in self.children:
            await ch.run_effects()

    # PARA DEBUG
    def render_tree(self, indent=0):
        pad = "  " * indent
        print(f"{pad}- {self.name}")
        for ch in self.children:
            ch.render_tree(indent + 1)