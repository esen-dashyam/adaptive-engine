"""Device lock / unlock tools — STUBS in v1.

The kid-app lock-screen UI honoring a server-driven lock state lands
in a follow-up plan. We register the tools here so the agent has a
clean place to acknowledge the request without trying a Profile-UI
workaround."""
from __future__ import annotations

from uuid import UUID

from backend.app.services.agent_tools.decorator import tool, ToolResult, GLOBAL_REGISTRY


@tool(
    name="lock_device",
    description=(
        "Acknowledge a parent's request to lock the kid's whole device for "
        "`minutes` minutes. NOTE: in this version, the lock is recorded but "
        "the kid app does not yet honor it visually. Tell the parent the "
        "request was noted and that the next release will activate it."
    ),
    requires_confirm=True,
    danger="high",
    inverse_action=None,
    label_builder=lambda args: f"Lock device for {args.get('minutes', '?')} min (noted, not yet active)",
    registry=GLOBAL_REGISTRY,
)
async def lock_device(child_id: UUID, minutes: int) -> ToolResult:
    return ToolResult(
        public={"requested_minutes": minutes, "active": False,
                "note": "lock recorded server-side only; kid app not yet wired"},
        public_summary=f"Lock noted ({minutes} min) — kid app will support in next release",
    )


@tool(
    name="unlock_device",
    description=(
        "Acknowledge a parent's unlock request. NOTE: same caveat as "
        "lock_device — kid app does not yet honor lock state in this version."
    ),
    requires_confirm=False,
    danger="low",
    inverse_action=None,
    label_builder=lambda _a: "Unlock device (noted)",
    registry=GLOBAL_REGISTRY,
)
async def unlock_device(child_id: UUID) -> ToolResult:
    return ToolResult(
        public={"active": False, "note": "kid app not yet wired"},
        public_summary="Unlock noted",
    )
