# pyreact/input/focus.py

from typing import Dict, Set, Optional
from pyreact.core.hook import HookContext

class _InputFocus:
    def __init__(self):
        self.current = None
        # Track component hierarchy for automatic focus
        self.component_stack: list[str] = []
        self.active_components: Set[str] = set()
        # Global focus by route - only one component per route can have focus
        self.route_focus: Dict[str, str] = {}  # route -> token
        
    def acquire(self, token: str): 
        self.current = token
        
    def release(self, token: str):
        if self.current == token:
            self.current = None
            
    def is_current(self, token: str) -> bool:
        return self.current == token
    
    def acquire_route_focus(self, route: str, token: str):
        """Acquire exclusive focus for a specific route"""
        self.route_focus[route] = token
        
    def release_route_focus(self, route: str, token: str):
        """Release focus for a specific route"""
        if self.route_focus.get(route) == token:
            self.route_focus.pop(route, None)
            
    def has_route_focus(self, route: str, token: str) -> bool:
        """Check if this token has focus for the given route"""
        return self.route_focus.get(route) == token
    
    # New methods for automatic focus management
    def register_component(self, component_id: str):
        """Register a component as currently active/rendered"""
        self.active_components.add(component_id)
        
    def unregister_component(self, component_id: str):
        """Unregister a component when it's no longer rendered"""
        self.active_components.discard(component_id)
        
    def push_component_context(self, component_id: str):
        """Push a component onto the context stack (for nested components)"""
        self.component_stack.append(component_id)
        
    def pop_component_context(self, component_id: str):
        """Pop a component from the context stack"""
        if self.component_stack and self.component_stack[-1] == component_id:
            self.component_stack.pop()
            
    def get_active_component(self) -> Optional[str]:
        """Get the currently active component (last in stack)"""
        return self.component_stack[-1] if self.component_stack else None
    
    def should_focus(self, component_id: str, token: str) -> bool:
        """Determine if a component should have focus automatically"""
        active_component = self.get_active_component()
        
        
        # If there's an explicit focus, use that
        if self.current is not None:
            return self.is_current(token)
            
        # Otherwise, focus the active component
        return active_component == component_id

def get_focus():
    return HookContext.get_service("input_focus", _InputFocus)

def use_component_focus(component_name: str):
    """Hook to automatically register/unregister component focus"""
    from pyreact.core.core import hooks
    focus = get_focus()
    
    def register_effect():
        focus.push_component_context(component_name)
        focus.register_component(component_name)
        
        def cleanup():
            focus.pop_component_context(component_name)
            focus.unregister_component(component_name)
        return cleanup
    
    hooks.use_effect(register_effect, [component_name])