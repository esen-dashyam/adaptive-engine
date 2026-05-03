"""Tests for the @tool decorator and ToolRegistry (Phase B)."""
from __future__ import annotations

import pytest

from backend.app.services.agent_tools.decorator import tool, ToolRegistry, ToolResult


def test_tool_decorator_registers_function() -> None:
    registry = ToolRegistry()

    @tool(
        registry=registry,
        name="add",
        description="Add two numbers",
        requires_confirm=False,
        danger="low",
    )
    async def add(a: int, b: int) -> ToolResult:
        return ToolResult(public={"sum": a + b})

    assert "add" in registry.tools
    decl = registry.declarations()
    assert decl[0]["name"] == "add"
    assert decl[0]["description"] == "Add two numbers"
    assert decl[0]["parameters"]["properties"]["a"]["type"] == "integer"


@pytest.mark.asyncio
async def test_tool_call_returns_result() -> None:
    registry = ToolRegistry()

    @tool(registry=registry, name="add", description="Add", requires_confirm=False, danger="low")
    async def add(a: int, b: int) -> ToolResult:
        return ToolResult(public={"sum": a + b})

    result = await registry.call("add", {"a": 1, "b": 2})
    assert result.public == {"sum": 3}


def test_unknown_tool_raises() -> None:
    registry = ToolRegistry()
    with pytest.raises(KeyError):
        registry.declarations_for("nonexistent")
