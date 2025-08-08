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

        if deps is None:
            deps_key = None
        else:
            deps_key = tuple(deps)            # [] → ()   (objeto imutável)

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
        if deps is None:
            deps_key = None      # nunca re-muda
        else:
            deps_key = tuple(deps)

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

        key = None if deps is None else tuple(deps)
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

            # guarda referência p/ poder executar no unmount
            if subs_set not in self.__dict__.setdefault("_ctx_subs", []):
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
        # 1. clean-ups gerados por use_effect
        # ------------------------------------------------------------------
        for slot in self.hooks:
            if isinstance(slot, tuple):
                cleanup, _deps = slot
                if cleanup:
                    try:
                        if asyncio.iscoroutinefunction(cleanup):
                            # roda assíncrono em background
                            asyncio.create_task(cleanup())
                        else:
                            cleanup()
                    except Exception:
                        pass

        # 2. remove-se dos WeakSets de assinantes de Context
        # ------------------------------------------------------------------
        for ws in getattr(self, "_ctx_subs", []):
            ws.discard(self)
            
        # 3. libera a lista para GC
        # ------------------------------------------------------------------
        if hasattr(self, "_ctx_subs"):
            self._ctx_subs.clear()

        # 4. desmonta recursivamente os filhos
        # ------------------------------------------------------------------
        for child in self.children:
            child.unmount()

        # 4. forca GC
        # ------------------------------------------------------------------
        self.children.clear()
        self.hooks.clear()
        self.effects.clear()

    def render(self):
        import core
        token = core._context_stack.set(self)

        try:
            self.hook_idx = 0
            self.effects = []

            # 1. guarda os filhos antigos e começa uma nova lista vazia
            # ------------------------------------------------------------------
            old_children = self.children
            self.children = []


            # 2. executa a função do componente
            # ------------------------------------------------------------------
            output = self.component_fn(__internal=True, **self.props)
            vnodes = output if isinstance(output, list) else [output]


            # 3. reconciliação – reaproveita ou cria contextos-filho
            # ------------------------------------------------------------------
            for idx, vnode in enumerate(vnodes):
                if not isinstance(vnode, VNode):
                    continue

                vnode_key = vnode.key if vnode.key is not None else f"__idx_{idx}"

                # procura match em old_children
                matched = next(
                    (c for c in old_children
                     if (c.key if c.key is not None else f"__idx_{old_children.index(c)}") == vnode_key
                        and c.component_fn is vnode.component_fn),
                    None
                )

                # ---------- warn se há irmãos duplicados sem key --------
                if vnode.key is None:

                    dup = any(
                        (c.component_fn is vnode.component_fn and c.key is None)
                        for c in self.children
                    )
                    if dup:
                        warnings.warn(
                            f"\n\n⚠️ [HookContext] Sibling <{vnode.component_fn.__name__}> sem 'key' explícita; pode causar re-montagens extras.",
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

            # 4. desmonta órfãos
            # ------------------------------------------------------------------
            for orphan in old_children:
                if orphan not in self.children:
                    orphan.unmount()          # executa clean-ups recursivamente

            # 5. renderiza recursivamente os filhos atuais
            # ------------------------------------------------------------------
            for child in self.children:
                child.render()
        finally:
            # restaura o componente anterior e o stack original
            core._context_stack.reset(token)

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