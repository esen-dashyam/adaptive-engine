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


@_pytest.mark.asyncio
async def test_review_submissions_with_no_pending_returns_empty(monkeypatch) -> None:
    from backend.app.services import bigkid_store
    bigkid_store._singleton = None
    from backend.app.services.agent_tools import GLOBAL_REGISTRY
    from backend.app.services.agent_tools import vision_tools  # noqa: F401

    cid = "33333333-3333-3333-3333-333333333333"
    result = await GLOBAL_REGISTRY.call("review_submissions", {"child_id": cid})
    assert result.public["verdicts"] == []


@_pytest.mark.asyncio
async def test_review_submissions_calls_multimodal_for_each(monkeypatch) -> None:
    from backend.app.services import bigkid_store
    bigkid_store._singleton = None
    store = bigkid_store.get_store()

    cid_str = "44444444-4444-4444-4444-444444444444"
    cid_uuid = UUID(cid_str)
    state = store.get_state(cid_uuid)
    task_id = state.tasks[0].id

    # Mark a task as submitted with a fake photo URL (we won't fetch it).
    store.submit_evidence(
        cid_uuid, task_id, photo_url="https://example.com/x.jpg",
        photo_bytes=None, note="all done",
    )

    # Stub the multimodal call.
    async def fake_mm(prompt, items):
        return [{"task_id": str(it["task_id"]), "looks_done": True,
                 "confidence": 0.9, "note": "looks fine",
                 "recommend_action": "approve"} for it in items]
    async def fake_fetch(url):
        return b"fakejpg"

    from backend.app.services.agent_tools import vision_tools
    monkeypatch.setattr(vision_tools, "_call_multimodal", fake_mm)
    monkeypatch.setattr(vision_tools, "_fetch_photo_bytes", fake_fetch)

    from backend.app.services.agent_tools import GLOBAL_REGISTRY
    result = await GLOBAL_REGISTRY.call("review_submissions", {"child_id": cid_str})
    verdicts = result.public["verdicts"]
    assert len(verdicts) == 1
    assert verdicts[0]["recommend_action"] == "approve"


@_pytest.mark.asyncio
async def test_assign_task_creates_task() -> None:
    from backend.app.services import bigkid_store
    bigkid_store._singleton = None
    from backend.app.services.agent_tools import GLOBAL_REGISTRY
    from backend.app.services.agent_tools import task_tools  # noqa: F401

    cid = "55555555-5555-5555-5555-555555555555"
    before = bigkid_store.get_store().get_state(UUID(cid)).tasks
    result = await GLOBAL_REGISTRY.call("assign_task", {
        "child_id": cid, "title": "Sweep porch",
        "description": "Sweep the front porch.", "category": "Chores",
        "due": "Today, 6 PM",
    })
    assert "task_id" in result.public
    after = bigkid_store.get_store().get_state(UUID(cid)).tasks
    assert len(after) == len(before) + 1


@_pytest.mark.asyncio
async def test_approve_task_marks_done() -> None:
    from backend.app.services import bigkid_store
    bigkid_store._singleton = None
    store = bigkid_store.get_store()
    cid_str = "66666666-6666-6666-6666-666666666666"
    cid = UUID(cid_str)
    task = store.get_state(cid).tasks[0]
    # Submit so approve is meaningful.
    store.submit_evidence(cid, task.id, photo_url="https://x", photo_bytes=None, note=None)

    from backend.app.services.agent_tools import GLOBAL_REGISTRY
    from backend.app.services.agent_tools import task_tools  # noqa: F401
    result = await GLOBAL_REGISTRY.call("approve_task", {
        "child_id": cid_str, "task_id": str(task.id),
    })
    assert result.public["status"] == "done"
