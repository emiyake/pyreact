import asyncio
from pyreact.core.hook import HookContext
from pyreact.core.runtime import run_renders, schedule_rerender
from pyreact.input.bus import InputBus
from pyreact.input.providers.terminal import TerminalInput
from pyreact.web.nav_service import NavService


def run_terminal(app_component_fn, *, fps: int = 20, prompt: str = ">> "):
    async def _main():
        root = HookContext(app_component_fn.__name__, app_component_fn)
        schedule_rerender(root)

        bus = HookContext.get_service("input_bus", InputBus)

        # Debug helper: print VNode tree
        def _print_vnode_tree(_args: str = ""):
            try:
                BOLD = "\x1b[1m"
                CYAN = "\x1b[36m"
                RESET = "\x1b[0m"
                print(f"\n{BOLD}{CYAN}=== VNode Tree ==={RESET}")
                root.render_tree()
                print(f"{BOLD}{CYAN}=================={RESET}\n")
            except Exception:
                print("\x1b[90m[debug]\x1b[0m VNode tree not available yet.")

        # Debug helper: print current route
        def _print_current_route(_args: str = ""):
            try:
                navsvc = HookContext.get_service("nav_service", NavService)
                url = getattr(navsvc, "current", "/")
                BOLD = "\x1b[1m"
                CYAN = "\x1b[36m"
                RESET = "\x1b[0m"
                GRAY = "\x1b[90m"
                YELLOW = "\x1b[33m"
                print(f"\n{BOLD}{CYAN}=== Route ==={RESET}")
                print(f"{GRAY}url:{RESET} {YELLOW}{url}{RESET}")
                try:
                    path = navsvc.get_path()
                    params = navsvc.get_query_params()
                    frag = navsvc.get_fragment()
                    print(f"{GRAY}path:{RESET} {YELLOW}{path}{RESET}")
                    if params:
                        print(f"{GRAY}query:{RESET} {YELLOW}{params}{RESET}")
                    if frag:
                        print(f"{GRAY}fragment:{RESET} {YELLOW}{frag}{RESET}")
                except Exception:
                    pass
                print(f"{BOLD}{CYAN}=============== {RESET}\n")
            except Exception:
                print("\x1b[90m[debug]\x1b[0m Route not available yet.")

        # Command: navigate to a new route
        def _navigate_to(args: str = ""):
            dest = (args or "").strip()
            if not dest:
                GRAY = "\x1b[90m"
                RESET = "\x1b[0m"
                YELLOW = "\x1b[33m"
                print(
                    f"{GRAY}Usage:{RESET} {YELLOW}:nav /path[?query][#fragment]{RESET}"
                )
                return
            try:
                navsvc = HookContext.get_service("nav_service", NavService)
                navigate = getattr(navsvc, "navigate", None)
                if callable(navigate):
                    navigate(dest)
                else:
                    # Router not mounted yet; set current and schedule render
                    navsvc.current = dest
                    schedule_rerender(root)
            except Exception:
                print("\x1b[90m[debug]\x1b[0m Navigation service not ready.")

        # Register terminal commands (prefix with ':' or '/' e.g. :tree)
        ti = TerminalInput(
            bus,
            prompt=prompt,
            commands={
                "tree": _print_vnode_tree,
                "route": _print_current_route,
                "nav": _navigate_to,
                # Alias quit commands to stop the loop via built-in handling
                "quit": lambda _="": None,
                "exit": lambda _="": None,
                "q": lambda _="": None,
            },
        )
        ti.start()

        try:
            interval = 1.0 / max(1, fps)
            while not ti._stopping:
                await run_renders()
                await asyncio.sleep(interval)
        except (KeyboardInterrupt, SystemExit):
            pass

    asyncio.run(_main())
