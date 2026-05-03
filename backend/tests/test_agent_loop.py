"""Unit tests for AgentLoop — Gemini stubbed, tool registry real."""
from __future__ import annotations

import pytest

from backend.app.services.agent_loop import AgentLoop, AgentInput
from backend.app.services.agent_tools.decorator import (
    ToolRegistry, ToolResult, tool,
)


@pytest.fixture
def fresh_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @tool(registry=reg, name="echo", description="Echo input",
          requires_confirm=False, danger="low")
    async def echo(msg: str) -> ToolResult:
        return ToolResult(public={"echoed": msg}, public_summary=msg)

    @tool(registry=reg, name="risky", description="Risky write",
          requires_confirm=True, danger="high")
    async def risky(x: int) -> ToolResult:
        return ToolResult(public={"x": x})

    return reg


def _stub_response(tool_calls=None, text=""):
    """Build a response object with .tool_calls and .text attributes."""
    obj = type("R", (), {})()
    obj.tool_calls = tool_calls or []
    obj.text = text
    return obj


def _stub_call(call_id, name, args):
    obj = type("C", (), {})()
    obj.id = call_id
    obj.name = name
    obj.args = args
    return obj


@pytest.mark.asyncio
async def test_safe_tool_executes_immediately(fresh_registry) -> None:
    """When AI calls a no-confirm tool, it runs and we return a receipt."""

    class StubGemini:
        def __init__(self) -> None:
            self.iterations = 0

        async def chat(self, **kw):
            self.iterations += 1
            if self.iterations == 1:
                return _stub_response(
                    tool_calls=[_stub_call("x", "echo", {"msg": "hi"})],
                    text="",
                )
            return _stub_response(text="Got it: hi")

    loop = AgentLoop(
        registry=fresh_registry, gemini=StubGemini(),
        action_log=None, proposal_store=None,
    )
    out = await loop.run(AgentInput(
        message="say hi", history=[], child_device_id=None,
        child_name="Liam", state_snapshot=None,
        force_confirmations=[],
    ))
    assert out.message == "Got it: hi"
    assert len(out.receipts) == 1
    assert out.receipts[0].tool == "echo"
    assert out.receipts[0].summary == "hi"
    assert len(out.proposals) == 0


@pytest.mark.asyncio
async def test_risky_tool_stages_as_proposal(fresh_registry) -> None:
    """When AI calls a confirm-required tool, we stage instead of running."""

    class StubGemini:
        def __init__(self) -> None:
            self.iterations = 0

        async def chat(self, **kw):
            self.iterations += 1
            if self.iterations == 1:
                return _stub_response(
                    tool_calls=[_stub_call("x", "risky", {"x": 1})],
                    text="",
                )
            return _stub_response(text="Awaiting confirm")

    from backend.app.services.proposal_store import ProposalStore
    loop = AgentLoop(
        registry=fresh_registry, gemini=StubGemini(),
        action_log=None, proposal_store=ProposalStore(),
    )
    out = await loop.run(AgentInput(
        message="do it", history=[], child_device_id=None,
        child_name="Liam", state_snapshot=None,
        force_confirmations=[],
    ))
    assert len(out.proposals) == 1
    assert out.proposals[0].tool == "risky"
    assert out.proposals[0].danger == "high"
    assert out.proposals[0].token  # nonempty
    assert len(out.receipts) == 0


@pytest.mark.asyncio
async def test_force_all_executes_confirm_required_directly(fresh_registry) -> None:
    """When force_confirmations contains 'force_all', confirm-required tools run."""

    class StubGemini:
        def __init__(self) -> None:
            self.iterations = 0

        async def chat(self, **kw):
            self.iterations += 1
            if self.iterations == 1:
                return _stub_response(
                    tool_calls=[_stub_call("x", "risky", {"x": 5})],
                    text="",
                )
            return _stub_response(text="Done")

    loop = AgentLoop(
        registry=fresh_registry, gemini=StubGemini(),
        action_log=None, proposal_store=None,
    )
    out = await loop.run(AgentInput(
        message="just do it", history=[], child_device_id=None,
        child_name="Liam", state_snapshot=None,
        force_confirmations=["force_all"],
    ))
    assert len(out.receipts) == 1
    assert out.receipts[0].tool == "risky"
    assert len(out.proposals) == 0


@pytest.mark.asyncio
async def test_unknown_tool_recovers(fresh_registry) -> None:
    """Unknown tool name yields error in last_results, model can try again."""

    class StubGemini:
        def __init__(self) -> None:
            self.iterations = 0

        async def chat(self, **kw):
            self.iterations += 1
            if self.iterations == 1:
                return _stub_response(
                    tool_calls=[_stub_call("x", "does_not_exist", {})],
                    text="",
                )
            return _stub_response(text="Sorry, I couldn't find that tool")

    loop = AgentLoop(
        registry=fresh_registry, gemini=StubGemini(),
        action_log=None, proposal_store=None,
    )
    out = await loop.run(AgentInput(
        message="x", history=[], child_device_id=None,
        child_name="Liam", state_snapshot=None,
        force_confirmations=[],
    ))
    assert "couldn't" in out.message.lower() or "sorry" in out.message.lower()
    assert len(out.receipts) == 0


@pytest.mark.asyncio
async def test_no_tool_calls_returns_text(fresh_registry) -> None:
    """If first response has no tool_calls, return text immediately."""

    class StubGemini:
        async def chat(self, **kw):
            return _stub_response(text="Just a chat reply")

    loop = AgentLoop(
        registry=fresh_registry, gemini=StubGemini(),
        action_log=None, proposal_store=None,
    )
    out = await loop.run(AgentInput(
        message="hi", history=[], child_device_id=None,
        child_name="Liam", state_snapshot=None,
        force_confirmations=[],
    ))
    assert out.message == "Just a chat reply"
    assert out.receipts == []
    assert out.proposals == []


@pytest.mark.asyncio
async def test_tool_result_passes_name_back(fresh_registry) -> None:
    """The adapter needs `name` in each tool_result entry to build a
    valid function_response Part. Verify it's included."""

    captured_results: list[list[dict]] = []

    class StubGemini:
        def __init__(self) -> None:
            self.iterations = 0

        async def chat(self, **kw):
            self.iterations += 1
            captured_results.append(kw.get("tool_results") or [])
            if self.iterations == 1:
                return _stub_response(
                    tool_calls=[_stub_call("call-1", "echo", {"msg": "yo"})],
                    text="",
                )
            return _stub_response(text="ok")

    loop = AgentLoop(
        registry=fresh_registry, gemini=StubGemini(),
        action_log=None, proposal_store=None,
    )
    await loop.run(AgentInput(
        message="hi", history=[], child_device_id=None,
        child_name="Liam", state_snapshot=None,
        force_confirmations=[],
    ))
    # Second iteration's tool_results must include name=echo
    assert len(captured_results) >= 2
    second = captured_results[1]
    assert any(r.get("name") == "echo" and r.get("status") == "ok" for r in second)
