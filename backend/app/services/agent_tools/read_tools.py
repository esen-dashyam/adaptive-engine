"""Read-only tools — safe to call without confirmation. AI uses these
freely to inform responses without touching state."""
from __future__ import annotations

from uuid import UUID

from backend.app.services.agent_tools.decorator import tool, ToolResult, GLOBAL_REGISTRY
from backend.app.services.bigkid_store import get_store as get_bigkid_store


@tool(
    name="get_kid_state",
    description=(
        "Return the kid's current state: name, screen-time pool, all tasks "
        "(with status/phase/photo/note), reflection request if any, pending "
        "bypass requests. Call this when you need detail beyond what's in "
        "the auto-injected snapshot."
    ),
    requires_confirm=False,
    danger="low",
    registry=GLOBAL_REGISTRY,
)
async def get_kid_state(child_id: UUID) -> ToolResult:
    state = get_bigkid_store().get_state(child_id)
    return ToolResult(
        public=state.model_dump(mode="json"),
        public_summary=f"{state.child_name}: {len(state.tasks)} tasks, "
                       f"{state.minutes_left}/{state.minutes_max} min left",
    )


@tool(
    name="list_pending_submissions",
    description=(
        "Return only the kid's tasks that are submitted (status=submitted, "
        "phase=submitted) and awaiting parent review. Each item includes "
        "task_id, title, evidence_photo_url, evidence_note, submitted_at."
    ),
    requires_confirm=False,
    danger="low",
    registry=GLOBAL_REGISTRY,
)
async def list_pending_submissions(child_id: UUID) -> ToolResult:
    state = get_bigkid_store().get_state(child_id)
    pending = [
        {
            "task_id": str(t.id),
            "title": t.title,
            "evidence_photo_url": t.evidence_photo_url,
            "evidence_note": t.evidence_note,
        }
        for t in state.tasks
        if t.status.value == "submitted" and t.phase.value == "submitted"
    ]
    return ToolResult(
        public={"submissions": pending},
        public_summary=f"{len(pending)} pending submissions",
    )
