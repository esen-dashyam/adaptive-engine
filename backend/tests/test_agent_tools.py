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


import pytest as _pytest
from uuid import UUID


@_pytest.mark.asyncio
async def test_get_kid_state_returns_snapshot() -> None:
    from backend.app.services import bigkid_store
    bigkid_store._singleton = None  # reset
    from backend.app.services.agent_tools import GLOBAL_REGISTRY
    # Force tool import.
    from backend.app.services.agent_tools import read_tools  # noqa: F401

    cid = "11111111-1111-1111-1111-111111111111"
    result = await GLOBAL_REGISTRY.call("get_kid_state", {"child_id": cid})
    assert result.public["child_name"] == "Liam"
    assert len(result.public["tasks"]) == 3


@_pytest.mark.asyncio
async def test_list_pending_submissions_filters_status() -> None:
    from backend.app.services import bigkid_store
    bigkid_store._singleton = None
    from backend.app.services.agent_tools import GLOBAL_REGISTRY
    from backend.app.services.agent_tools import read_tools  # noqa: F401

    cid = "22222222-2222-2222-2222-222222222222"
    # No submissions yet.
    result = await GLOBAL_REGISTRY.call("list_pending_submissions", {"child_id": cid})
    assert result.public["submissions"] == []
