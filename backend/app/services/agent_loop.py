"""AgentLoop — iterative Gemini function-calling around a ToolRegistry.

Spec §4. Max 3 iterations. Confirm-required tool calls are staged into
ProposalStore (returned to iOS as Proposals). Safe calls execute and
write to ParentActionLog (returned as Receipts with undo_token).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from loguru import logger

from backend.app.schemas.agent import AgentResponse, Proposal, Receipt
from backend.app.services.agent_tools.decorator import ToolRegistry
from backend.app.services.parent_action_log import ParentActionLog
from backend.app.services.proposal_store import ProposalStore


MAX_ITERATIONS = 3


class GeminiProtocol(Protocol):
    async def chat(self, **kwargs: Any) -> Any: ...


@dataclass
class AgentInput:
    message: str
    history: list[dict]
    child_device_id: UUID | None
    child_name: str
    state_snapshot: dict | None
    force_confirmations: list[str]


class AgentLoop:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        gemini: GeminiProtocol,
        action_log: ParentActionLog | None,
        proposal_store: ProposalStore | None,
    ) -> None:
        self.registry = registry
        self.gemini = gemini
        self.action_log = action_log
        self.proposal_store = proposal_store

    async def run(self, inp: AgentInput) -> AgentResponse:
        proposals: list[Proposal] = []
        receipts: list[Receipt] = []
        last_results: list[dict] = []

        for iteration in range(MAX_ITERATIONS):
            resp = await self.gemini.chat(
                history=inp.history,
                state_snapshot=inp.state_snapshot,
                user_message=inp.message if iteration == 0 else None,
                tool_results=last_results if iteration > 0 else None,
                tools=self.registry.declarations(),
                child_name=inp.child_name,
            )

            if not getattr(resp, "tool_calls", None):
                return AgentResponse(
                    message=getattr(resp, "text", "") or "",
                    proposals=proposals,
                    receipts=receipts,
                )

            last_results = []
            for call in resp.tool_calls:
                tool_meta = self.registry.tools.get(call.name)
                if tool_meta is None:
                    last_results.append({
                        "call_id": call.id, "name": call.name,
                        "status": "error",
                        "error": f"unknown tool: {call.name}",
                    })
                    continue

                if (
                    tool_meta.requires_confirm
                    and "force_all" not in inp.force_confirmations
                ):
                    if self.proposal_store is None:
                        last_results.append({
                            "call_id": call.id, "name": call.name,
                            "status": "error",
                            "error": "no proposal store",
                        })
                        continue
                    token = self.proposal_store.stage(
                        tool=call.name, args=call.args,
                    )
                    label = (
                        tool_meta.label_builder(call.args)
                        if tool_meta.label_builder else call.name
                    )
                    proposals.append(Proposal(
                        tool=call.name, args=call.args, label=label,
                        danger=tool_meta.danger, token=token,
                    ))
                    last_results.append({
                        "call_id": call.id, "name": call.name,
                        "status": "awaiting_user_confirm",
                    })
                    continue

                try:
                    result = await self.registry.call(call.name, call.args)
                except Exception as exc:
                    logger.warning("tool {} failed: {}", call.name, exc)
                    last_results.append({
                        "call_id": call.id, "name": call.name,
                        "status": "error", "error": str(exc),
                    })
                    continue

                undo_token: str | None = None
                if self.action_log is not None and tool_meta.inverse_action:
                    inverse_args = (
                        tool_meta.inverse_args_builder(call.args, result)
                        if tool_meta.inverse_args_builder
                        else dict(call.args)
                    )
                    undo_token = self.action_log.record(
                        action_type=call.name, args=call.args,
                        inverse_action=tool_meta.inverse_action,
                        inverse_args=inverse_args,
                        source="agent",
                    )
                receipts.append(Receipt(
                    tool=call.name, args=call.args,
                    summary=result.public_summary or call.name,
                    undo_token=undo_token,
                ))
                last_results.append({
                    "call_id": call.id, "name": call.name,
                    "status": "ok", "data": result.public,
                })

        # Iteration cap hit
        return AgentResponse(
            message=(
                "I tried a few approaches but couldn't finalize. "
                "What would you like me to do?"
            ),
            proposals=proposals,
            receipts=receipts,
        )
