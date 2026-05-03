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
        # Tool calls from the previous Gemini turn — must be replayed back
        # to Gemini paired with their function_response parts so the SDK
        # can chain reasoning correctly. Without this, the model often
        # re-emits the same call or 400s on contract violation.
        prior_tool_calls: list[Any] = []

        for iteration in range(MAX_ITERATIONS):
            resp = await self.gemini.chat(
                history=inp.history,
                state_snapshot=inp.state_snapshot,
                # Pass the original user message EVERY iteration so the
                # contents list always starts with a user turn — without
                # this the function_call replay on iter ≥ 1 has no user
                # turn before it and Gemini 400s with "function call turn
                # comes immediately after a user turn or function response".
                # agent_gemini.chat dedupes by merging into trailing user
                # content, so we don't double-send.
                user_message=inp.message,
                tool_results=last_results if iteration > 0 else None,
                prior_tool_calls=prior_tool_calls if iteration > 0 else None,
                tools=self.registry.declarations(),
                child_name=inp.child_name,
            )

            if not getattr(resp, "tool_calls", None):
                return AgentResponse(
                    message=getattr(resp, "text", "") or "",
                    proposals=proposals,
                    receipts=receipts,
                )

            # Remember this turn's calls so the next iteration can replay
            # them back as the model's prior function_call Content (Gemini
            # contract: user → model(function_call) → user(function_response)).
            prior_tool_calls = list(resp.tool_calls)

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
                undo_expires_iso: str | None = None
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
                    # Surface the absolute expiry so iOS Undo countdown
                    # is wall-clock-driven (survives view navigation).
                    entry = self.action_log.get(undo_token)
                    if entry is not None:
                        undo_expires_iso = entry.expires_at.isoformat()
                receipts.append(Receipt(
                    tool=call.name, args=call.args,
                    summary=result.public_summary or call.name,
                    undo_token=undo_token,
                    undo_expires_at=undo_expires_iso,
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
