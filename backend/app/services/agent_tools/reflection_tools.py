"""Reflection-domain tools."""
from __future__ import annotations

from uuid import UUID

from backend.app.schemas.bigkid import QuizQuestionPublic
from backend.app.services import gemini_reflection
from backend.app.services.agent_tools.decorator import tool, ToolResult, GLOBAL_REGISTRY
from backend.app.services.bigkid_store import get_store as get_bigkid_store


@tool(
    name="propose_reflection",
    description=(
        "Trigger a reflection for the kid based on something they did wrong. "
        "Generates a kid-facing rephrasing, a 5-question quiz, a writing prompt, "
        "and locks the kid's device behind the reflection screen until done. "
        "Pass `reason` as plain English describing the action only ('called you "
        "a hurtful name', 'kept scrolling past bedtime')."
    ),
    requires_confirm=True,
    danger="high",
    inverse_action="cancel_reflection",
    inverse_args_builder=lambda args, r: {
        "child_id": args["child_id"], "rid": r.public["rid"],
    },
    label_builder=lambda args: "Send reflection",
    registry=GLOBAL_REGISTRY,
)
async def propose_reflection(child_id: UUID, reason: str) -> ToolResult:
    # Resolve through the module so monkeypatching works in tests.
    content = await gemini_reflection.generate_reflection_content(reason=reason)
    req = get_bigkid_store().trigger_reflection_with_content(
        child_id, reason=reason,
        display_reason=content.display_reason,
        video_id=content.video_id, video_title=content.video_title,
        writing_prompt=content.writing_prompt,
        quiz_public=[QuizQuestionPublic(q=q.q, options=q.options) for q in content.quiz],
        correct_indices=[q.correct_index for q in content.quiz],
    )
    return ToolResult(
        public={"rid": str(req.id), "display_reason": content.display_reason},
        public_summary=f"Reflection sent: {content.display_reason}",
    )


@tool(
    name="cancel_reflection",
    description=(
        "Cancel an active reflection — kid's device returns to normal. "
        "Use when parent says 'never mind' / 'undo that' / 'cancel it'."
    ),
    requires_confirm=True,
    danger="medium",
    inverse_action=None,
    label_builder=lambda args: "Cancel reflection",
    registry=GLOBAL_REGISTRY,
)
async def cancel_reflection(child_id: UUID, rid: UUID) -> ToolResult:
    # Use the store's ack_reflection to keep behavior consistent with the
    # revert dispatcher (clears reflection + resets cooldown).
    get_bigkid_store().ack_reflection(child_id, rid)
    return ToolResult(public={"cancelled": True}, public_summary="Reflection cancelled")


@tool(
    name="approve_reflection",
    description=(
        "Approve a kid-completed reflection. Required before the kid sees the "
        "celebratory completion screen. Use when parent says 'approve his "
        "reflection' or after they've reviewed the kid's essay."
    ),
    requires_confirm=True,
    danger="medium",
    inverse_action=None,
    label_builder=lambda args: "Approve reflection",
    registry=GLOBAL_REGISTRY,
)
async def approve_reflection(
    child_id: UUID, rid: UUID, parent_note: str | None = None,
) -> ToolResult:
    req = get_bigkid_store().parent_approve_reflection(
        child_id, rid, parent_note=parent_note,
    )
    return ToolResult(
        public={"rid": str(req.id), "status": req.status.value},
        public_summary="Reflection approved",
    )
