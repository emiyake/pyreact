# hook.py ----------------------------------------------------
from typing import Dict
from weakref import WeakSet
from pyreact.core.core import VNode
from pyreact.core.runtime import schedule_rerender
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
        self._effect_slots: set[int] = set()
        # Track mount lifecycle to avoid rerenders after unmount
        self._mounted: bool = True

    def use_state(self, initial):
        idx = self.hook_idx
        if idx >= len(self.hooks):
            self.hooks.append(initial)

        def set_state(val):
            nonlocal idx
            # Ignore state updates after unmount
            if not getattr(self, "_mounted", True):
                return
            if callable(val):
                val = val(self.hooks[idx])

            if val != self.hooks[idx]:
                self.hooks[idx] = val
                schedule_rerender(self)

        self.hook_idx += 1
        return self.hooks[idx], set_state

    def use_reducer(self, reducer, initial, *, init_fn=None, deps=None):
        """
        Semantics similar to React.useReducer:
        - reducer(state, action) -> new_state
        - initial: initial state (used only on the first mount, unless 'deps' is provided)
        - init_fn(optional): lazy initializer init_fn(initial) -> state
        - deps(optional): if provided, when changed, the state is REINITIALIZED with init_fn(initial) or initial.
        """
        deps_key = None if deps is None else tuple(deps)  # [] → () (immutable object)
        idx = self.hook_idx

        if idx >= len(self.hooks):  # first mount
            state0 = init_fn(initial) if init_fn is not None else initial
            # Slot stores: (state, reducer, deps_key)
            self.hooks.append((state0, reducer, deps_key))
        else:  # updates
            state, old_reducer, old_deps = self.hooks[idx]

            if (
                deps_key is not None and old_deps != deps_key
            ):  # Reinit due to deps change (optional)
                state = init_fn(initial) if init_fn is not None else initial
                self.hooks[idx] = (state, reducer, deps_key)
            else:
                if (
                    old_reducer is not reducer or old_deps != deps_key
                ):  # Update reducer reference (dispatch uses the current reducer)
                    self.hooks[idx] = (
                        state,
                        reducer,
                        deps_key if deps is not None else old_deps,
                    )

        def dispatch(action):
            nonlocal idx
            # Ignore dispatch after unmount
            if not getattr(self, "_mounted", True):
                return
            s, r, dkey = self.hooks[idx]
            new_state = r(s, action)
            if new_state != s:
                self.hooks[idx] = (new_state, r, dkey)
                schedule_rerender(self)

        state, _r, _d = self.hooks[idx]
        self.hook_idx += 1
        return state, dispatch  # return the pair (state, dispatch)

    def use_effect(self, effect_fn, deps):
        deps_key = None if deps is None else tuple(deps)  # [] → () (immutable object)
        idx = self.hook_idx

        if idx >= len(self.hooks):  # first mount
            self.hooks.append((None, deps_key))
            self.effects.append((effect_fn, deps_key, idx))
            self._effect_slots.add(idx)
        else:  # updates
            old_cleanup, old_deps = self.hooks[idx]
            if deps_key is not None and old_deps != deps_key:  # deps changed
                self.effects.append((effect_fn, deps_key, idx))
                self.hooks[idx] = (old_cleanup, deps_key)

        self.hook_idx += 1

    def use_callback(self, fn, deps=None):
        deps_key = None if deps is None else tuple(deps)  # [] → () (immutable object)

        idx = self.hook_idx

        if idx >= len(self.hooks):  # first time
            self.hooks.append((fn, deps_key))
        else:
            cached_fn, old_deps = self.hooks[idx]
            if deps_key is not None and old_deps != deps_key:
                self.hooks[idx] = (fn, deps_key)  # deps changed → new fn
            else:
                fn = cached_fn  # use memo

        self.hook_idx += 1
        return fn

    def use_memo(self, factory, deps=None):
        key = None if deps is None else tuple(deps)  # [] → () (immutable object)
        idx = self.hook_idx

        if idx >= len(self.hooks):  # first time
            self.hooks.append((factory(), key))
        else:
            value, old_key = self.hooks[idx]
            if key is not None and old_key != key:
                value = factory()  # deps changed
                self.hooks[idx] = (value, key)

        self.hook_idx += 1
        return self.hooks[idx][0]

    def use_context(self, ctx_like):
        ctx = getattr(ctx_like, "_ctx", ctx_like)

        subscribe = getattr(ctx_like, "_subscribe", None)
        if subscribe is not None:
            subscribe(self)
            subs_set = getattr(ctx_like, "_subs")

            if subs_set not in self.__dict__.setdefault(
                "_ctx_subs", []
            ):  # keep reference to run on unmount
                self._ctx_subs.append(subs_set)

        idx = self.hook_idx
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
        # 1. Clean-ups generated by use_effect ONLY on tracked indices
        for idx in list(getattr(self, "_effect_slots", set())):
            if idx < len(self.hooks):
                self._run_cleanup_slot(self.hooks[idx])

        # 2. Remove Context subscriptions
        for ws in getattr(self, "_ctx_subs", []):
            ws.discard(self)

        if hasattr(self, "_ctx_subs"):
            self._ctx_subs.clear()

        # 3. Unmount children recursively
        for child in self.children:
            child.unmount()

        # 4. GC
        self.children.clear()
        self.hooks.clear()
        self.effects.clear()
        if hasattr(self, "_effect_slots"):
            self._effect_slots.clear()
        # mark as unmounted to skip future rerenders
        self._mounted = False

    def render(self):
        import pyreact.core.core as core

        token = core._context_stack.set(self)

        try:
            self.hook_idx = 0
            self.effects = []

            # 1. store old children and start a new empty list
            old_children = self.children
            self.children = []

            # 2. execute component function
            output = self.component_fn(__internal=True, **self.props)
            vnodes = output if isinstance(output, list) else [output]

            # 3. reconciliation – reuse or create child contexts
            for idx, vnode in enumerate(vnodes):
                if not isinstance(vnode, VNode):
                    continue

                vnode_key = vnode.key if vnode.key is not None else f"__idx_{idx}"

                # 4. search for match in old_children
                matched = next(
                    (
                        c
                        for c in old_children
                        if (
                            c.key
                            if c.key is not None
                            else f"__idx_{old_children.index(c)}"
                        )
                        == vnode_key
                        and c.component_fn is vnode.component_fn
                    ),
                    None,
                )

                # 5. warn if there are duplicate siblings without keys
                if vnode.key is None:
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

            # 6. recursively unmount orphans
            for orphan in old_children:
                if orphan not in self.children:
                    orphan.unmount()

            # 7. recursively render current children
            for child in self.children:
                child.render()
        finally:
            # 8. restore previous component and original stack
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

    # FOR DEBUGGING
    def render_tree(self, indent=0):
        pad = "  " * indent

        def _fmt_val(v, depth=0):
            # ANSI color helpers available via closure below
            if depth > 1:
                return f"{DIM}…{RESET}"
            if isinstance(v, (int, float)):
                return f"{FG_BLUE}{repr(v)}{RESET}"
            if isinstance(v, str):
                s = v.replace("\n", "\\n")
                text = s if len(s) <= 60 else s[:57] + "…"
                return f"{FG_YELLOW}{repr(text)}{RESET}"
            if v is None or isinstance(v, bool):
                return f"{FG_CYAN}{repr(v)}{RESET}"
            if isinstance(v, (list, tuple)):
                return f"{FG_CYAN}[{len(v)}]{RESET}"
            if isinstance(v, dict):
                items = []
                for i, (k, val) in enumerate(v.items()):
                    if i >= 5:
                        items.append(f"{DIM}…{RESET}")
                        break
                    if k == "children":
                        # Children can be very large; show only count
                        try:
                            clen = len(val)  # type: ignore[arg-type]
                        except Exception:
                            clen = "?"
                        items.append(
                            f"{FG_CYAN}children{RESET}=[{FG_YELLOW}{clen}{RESET}]"
                        )
                    else:
                        items.append(f"{FG_CYAN}{k}{RESET}={_fmt_val(val, depth + 1)}")
                body = ", ".join(items)
                return "{" + body + "}"
            if callable(v):
                name = getattr(v, "__name__", None) or getattr(
                    type(v), "__name__", "callable"
                )
                return f"{FG_GREEN}<fn {name}>{RESET}"
            return f"{FG_GREEN}<{type(v).__name__}>{RESET}"

        # ANSI helpers
        RESET = "\x1b[0m"
        # BOLD = "\x1b[1m"  # reserved for future use
        DIM = "\x1b[2m"
        FG_GRAY = "\x1b[90m"
        FG_YELLOW = "\x1b[33m"
        FG_MAGENTA = "\x1b[35m"
        FG_CYAN = "\x1b[36m"
        FG_BLUE = "\x1b[34m"
        FG_GREEN = "\x1b[32m"

        name_col = f"{FG_MAGENTA}{self.name}{RESET}"
        if getattr(self, "key", None) is not None:
            key_part = f" {FG_GRAY}key={RESET}{FG_YELLOW}{self.key!r}{RESET}"
        else:
            key_part = ""
        props_val = _fmt_val(getattr(self, "props", {}))
        props_part = f" {FG_GRAY}props={RESET}{props_val}"
        print(f"{pad}{FG_GRAY}-{RESET} {name_col}{key_part}{props_part}")
        for ch in self.children:
            ch.render_tree(indent + 1)
