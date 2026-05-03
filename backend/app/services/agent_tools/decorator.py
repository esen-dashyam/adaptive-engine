"""@tool decorator + ToolRegistry. Inspects function signatures to
auto-build Gemini function-calling JSON schemas.

Each tool is an async callable returning a ToolResult. The decorator
attaches metadata (description, danger, confirmation requirement,
inverse for revert) used by the AgentLoop and the Gemini SDK.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, get_type_hints
from uuid import UUID


Danger = Literal["low", "medium", "high"]


@dataclass
class ToolResult:
    """Public result fed back to Gemini for the next iteration's reasoning,
    and surfaced in iOS receipts. Avoid leaking internals — keep public dict
    serializable + small."""
    public: dict[str, Any] = field(default_factory=dict)
    public_summary: str | None = None  # human-readable one-liner for receipts


@dataclass
class ToolMeta:
    name: str
    description: str
    requires_confirm: bool
    danger: Danger
    inverse_action: str | None
    inverse_args_builder: Callable[[dict, ToolResult], dict] | None
    label_builder: Callable[[dict], str] | None
    fn: Callable[..., Awaitable[ToolResult]]
    parameters_schema: dict


_PY_TYPE_TO_JSON: dict[type, str] = {
    str: "string", int: "integer", float: "number", bool: "boolean",
    UUID: "string", list: "array", dict: "object",
}


def _build_param_schema(fn: Callable) -> dict:
    sig = inspect.signature(fn)
    hints = get_type_hints(fn)
    props: dict[str, dict] = {}
    required: list[str] = []
    for param_name, param in sig.parameters.items():
        if param_name == "authorize_batch":
            # Hidden from Gemini — agent loop fills it from heuristic.
            continue
        ptype = hints.get(param_name, str)
        json_type = _PY_TYPE_TO_JSON.get(ptype, "string")
        prop: dict[str, Any] = {"type": json_type}
        if ptype is UUID:
            prop["format"] = "uuid"
        props[param_name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
    return {"type": "object", "properties": props, "required": required}


class ToolRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, ToolMeta] = {}

    def register(self, meta: ToolMeta) -> None:
        self.tools[meta.name] = meta

    def declarations(self) -> list[dict]:
        """Gemini function-calling schema list."""
        return [
            {"name": m.name, "description": m.description, "parameters": m.parameters_schema}
            for m in self.tools.values()
        ]

    def declarations_for(self, name: str) -> dict:
        if name not in self.tools:
            raise KeyError(f"unknown tool: {name}")
        m = self.tools[name]
        return {"name": m.name, "description": m.description, "parameters": m.parameters_schema}

    async def call(self, name: str, args: dict) -> ToolResult:
        if name not in self.tools:
            raise KeyError(f"unknown tool: {name}")
        meta = self.tools[name]
        # Coerce string UUIDs to UUID objects per the function signature.
        coerced = _coerce_args(meta.fn, args)
        return await meta.fn(**coerced)


def _coerce_args(fn: Callable, args: dict) -> dict:
    hints = get_type_hints(fn)
    out = {}
    for k, v in args.items():
        target = hints.get(k)
        if target is UUID and isinstance(v, str):
            out[k] = UUID(v)
        else:
            out[k] = v
    return out


GLOBAL_REGISTRY = ToolRegistry()


def tool(
    *,
    name: str,
    description: str,
    requires_confirm: bool,
    danger: Danger,
    inverse_action: str | None = None,
    inverse_args_builder: Callable[[dict, ToolResult], dict] | None = None,
    label_builder: Callable[[dict], str] | None = None,
    registry: ToolRegistry | None = None,
):
    target = registry if registry is not None else GLOBAL_REGISTRY

    def decorator(fn: Callable[..., Awaitable[ToolResult]]) -> Callable:
        meta = ToolMeta(
            name=name,
            description=description,
            requires_confirm=requires_confirm,
            danger=danger,
            inverse_action=inverse_action,
            inverse_args_builder=inverse_args_builder,
            label_builder=label_builder,
            fn=fn,
            parameters_schema=_build_param_schema(fn),
        )
        target.register(meta)
        return fn

    return decorator
