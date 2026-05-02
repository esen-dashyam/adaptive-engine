"""Big-kid parent endpoints used by the parent app + tests.
Full Gemini-driven reflection content is wired in Phase 9; this scaffold
uses fixture content so the v1 child flow is testable end-to-end.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from backend.app.schemas.bigkid import (
    ParentBypassRespondBody, ParentReflectionApproveBody,
    ParentReflectionTriggerBody, ParentTaskReviewBody,
    ReflectionRequest, Task,
)
from backend.app.services.bigkid_store import BigKidStore, get_store


router = APIRouter(tags=["Big-Kid Parent"])


@router.post("/parent/reflection/trigger", response_model=ReflectionRequest)
def trigger_reflection(
    body: ParentReflectionTriggerBody,
    store: BigKidStore = Depends(get_store),
) -> ReflectionRequest:
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
