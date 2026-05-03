"""Big-kid parent endpoints used by the parent app + tests.
Full Gemini-driven reflection content is wired in Phase 9; this scaffold
uses fixture content so the v1 child flow is testable end-to-end.
"""
from __future__ import annotations

import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from backend.app.schemas.bigkid import (
    ParentBypassRespondBody, ParentReflectionApproveBody,
    ParentReflectionTriggerBody, ParentTaskReviewBody,
    QuizQuestionPublic, ReflectionRequest, Task,
)
from backend.app.services.bigkid_store import BigKidStore, get_store
from backend.app.services.gemini_reflection import generate_reflection_content


router = APIRouter(tags=["Big-Kid Parent"])


@router.post("/parent/reflection/trigger", response_model=ReflectionRequest)
async def trigger_reflection(
    body: ParentReflectionTriggerBody,
    store: BigKidStore = Depends(get_store),
) -> ReflectionRequest:
    # Gemini-driven content path. Auto-enabled whenever GEMINI_API_KEY is
    # available — no separate feature flag — so the quiz + writing prompt
    # are always tailored to the parent's reason. Setting BIGKID_NO_GEMINI=1
    # forces the fixture path (offline / unit tests).
    use_gemini = (
        bool(os.environ.get("GEMINI_API_KEY"))
        and os.environ.get("BIGKID_NO_GEMINI", "0") != "1"
    )
    if use_gemini:
        try:
            content = await generate_reflection_content(reason=body.reason)
            return store.trigger_reflection_with_content(
                body.child_id, reason=body.reason,
                display_reason=content.display_reason,
                video_id=content.video_id, video_title=content.video_title,
                writing_prompt=content.writing_prompt,
                quiz_public=[QuizQuestionPublic(q=q.q, options=q.options) for q in content.quiz],
                correct_indices=[q.correct_index for q in content.quiz],
            )
        except Exception as e:
            # Fall through to fixture so the kid flow still works, but
            # surface the reason in logs so you can diagnose.
            print(f"[bigkid_parent] Gemini path failed, using fixture: {e!r}")
    return store.trigger_reflection(body.child_id, body.reason)


@router.post("/parent/reflection/{rid}/approve", response_model=ReflectionRequest)
def approve_reflection(
    rid: UUID, body: ParentReflectionApproveBody,
    store: BigKidStore = Depends(get_store),
) -> ReflectionRequest:
    # NOTE: child_id derivation from rid is left to the real auth implementation.
    # For v1 in-memory store we scan all states.
    for cid, s in store._states.items():  # noqa: SLF001
        if s.reflection and s.reflection.id == rid:
            return store.parent_approve_reflection(cid, rid, parent_note=body.parent_note)
    raise HTTPException(status_code=404, detail="reflection not found")


@router.post("/parent/task/{task_id}/review", response_model=Task)
def review_task(
    task_id: UUID, body: ParentTaskReviewBody,
    store: BigKidStore = Depends(get_store),
) -> Task:
    for cid, s in store._states.items():  # noqa: SLF001
        for t in s.tasks:
            if t.id == task_id:
                return store.parent_review_task(
                    cid, task_id, decision=body.decision, redo_reason=body.redo_reason,
                )
    raise HTTPException(status_code=404, detail="task not found")
