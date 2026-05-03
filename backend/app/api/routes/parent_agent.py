"""POST /parent/agent/exec — execute a previously-staged proposal.

Looks up the token in ProposalStore, pops the (tool_name, args) tuple,
calls the registered tool, and writes a ParentActionLog entry if the
tool has an inverse_action. Returns a Receipt that iOS displays as a
post-confirm bubble.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.app.schemas.agent import Receipt
from backend.app.services.agent_tools import GLOBAL_REGISTRY
from backend.app.services.parent_action_log import (
    ParentActionLog, get_log as get_action_log,
)
from backend.app.services.proposal_store import (
    ProposalStore, get_proposal_store,
)

# Force tool modules to import so @tool decorators register into
# GLOBAL_REGISTRY at module load. shield_tools is intentionally NOT
# imported — that path stays on the legacy verb-table dispatcher in v1.
from backend.app.services.agent_tools import (  # noqa: F401
    read_tools, task_tools, reflection_tools, bypass_tools,
    lock_tools, vision_tools,
)


router = APIRouter(tags=["Parent Agent"])


class ExecBody(BaseModel):
    token: str


@router.post("/parent/agent/exec", response_model=Receipt)
async def exec_proposal(
    body: ExecBody,
    proposal_store: ProposalStore = Depends(get_proposal_store),
    log: ParentActionLog = Depends(get_action_log),
) -> Receipt:
    popped = proposal_store.pop(body.token)
    if popped is None:
        raise HTTPException(
            status_code=410, detail="proposal expired or already used",
        )

    tool_name, args = popped
    if tool_name not in GLOBAL_REGISTRY.tools:
        raise HTTPException(status_code=500, detail=f"tool gone: {tool_name}")
    meta = GLOBAL_REGISTRY.tools[tool_name]

    result = await GLOBAL_REGISTRY.call(tool_name, args)

    undo_token: str | None = None
    if meta.inverse_action:
        inverse_args = (
            meta.inverse_args_builder(args, result)
            if meta.inverse_args_builder else dict(args)
        )
        undo_token = log.record(
            action_type=tool_name, args=args,
            inverse_action=meta.inverse_action,
            inverse_args=inverse_args,
            source="agent",
        )
    return Receipt(
        tool=tool_name, args=args,
        summary=result.public_summary or tool_name,
        undo_token=undo_token,
    )
