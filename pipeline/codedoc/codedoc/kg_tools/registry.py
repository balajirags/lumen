"""ToolDef dataclass and ToolRegistry class."""

from dataclasses import dataclass
from typing import Any


# ── Tool Registry ───────────────────────────────────────────────────────────


@dataclass
class ToolDef:
    """Metadata for a registered tool function."""
    name: str
    description: str
    parameters: dict[str, dict]  # param_name -> {"type": ..., "description": ..., "default": ...}
    fn: Any


class ToolRegistry:
    """Simple decorator-based tool registry — no MCP needed."""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def tool(self, name: str | None = None, description: str | None = None):
        """Register a function as a tool."""
        def decorator(fn):
            tool_name = name or fn.__name__
            tool_desc = description or (fn.__doc__ or "").strip()
            # Introspect parameters from type hints + defaults
            import inspect
            sig = inspect.signature(fn)
            params = {}
            for pname, param in sig.parameters.items():
                ptype = "string"
                if param.annotation is int:
                    ptype = "integer"
                elif param.annotation is bool:
                    ptype = "boolean"
                elif param.annotation is float:
                    ptype = "number"
                info: dict[str, Any] = {"type": ptype}
                if param.default is not inspect.Parameter.empty:
                    info["default"] = param.default
                params[pname] = info
            self._tools[tool_name] = ToolDef(
                name=tool_name,
                description=tool_desc,
                parameters=params,
                fn=fn,
            )
            return fn
        return decorator

    def list_tools(self) -> list[ToolDef]:
        return list(self._tools.values())

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def call(self, name: str, **kwargs) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"Error: unknown tool '{name}'. Use 'help' to list tools."
        try:
            return tool.fn(**kwargs)
        except TypeError as e:
            return f"Error calling '{name}': {e}"
