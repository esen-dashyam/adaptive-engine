"""Bypass-respond tool."""
from __future__ import annotations

from uuid import UUID

from backend.app.services.agent_tools.decorator import tool, ToolResult, GLOBAL_REGISTRY
from backend.app.services.bigkid_store import get_store as get_bigkid_store


@tool(
    name="respond_bypass",
    description=(
        "Approve or deny a bypass request the kid sent. decision must be "
        "'approve' or 'deny'. message is shown to the kid (optional but kind)."
    ),
    requires_confirm=True,
    danger="medium",
    inverse_action="respond_bypass",
    inverse_args_builder=lambda args, _r: {
        "bypass_id": args["bypass_id"],
        "decision": "deny" if args["decision"] == "approve" else "approve",
        "message": "Reverted",
    },
    label_builder=lambda args: f"{args['decision'].title()} bypass",
    registry=GLOBAL_REGISTRY,
)
async def respond_bypass(
    bypass_id: UUID, decision: str, message: str | None = None,
) -> ToolResult:
    bypass = get_bigkid_store().respond_bypass(
        bypass_id, decision=decision, message=message,
    )
    return ToolResult(
        public={"bypass_id": str(bypass.id), "status": bypass.status.value},
        public_summary=f"Bypass {bypass.status.value}",
    )
