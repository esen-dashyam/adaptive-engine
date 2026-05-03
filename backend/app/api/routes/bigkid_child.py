"""Big-kid child mode — `/child/*` endpoints (spec §8.1–§8.10)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel

from backend.app.schemas.bigkid import (
    BypassCreateBody, BypassRequest, ChildStateResponse, EssaySubmitBody,
    QuizAnswerBody, QuizAnswerResponse, ReflectionRequest, ReflectionStep,
    Task, TimeConsumptionBody,
)
from backend.app.services.bigkid_store import BigKidStore, get_store, evidence_url


router = APIRouter(tags=["Big-Kid Child"])


def child_id_dep(x_child_id: str = Header(...)) -> UUID:
    """Auth shim: child identity comes from `X-Child-Id` header.
    v1 placeholder — real auth (paired token verification) lands in a follow-up plan.
    """
    try:
        return UUID(x_child_id)
    except ValueError as e:
        raise HTTPException(status_code=401, detail="invalid X-Child-Id") from e


@router.get("/child/state", response_model=ChildStateResponse)
def get_state(
    child: UUID = Depends(child_id_dep),
    store: BigKidStore = Depends(get_store),
) -> ChildStateResponse:
    return store.get_state(child)


@router.post("/child/task/{task_id}/evidence", response_model=Task)
async def submit_evidence(
    request: Request,
    task_id: UUID,
    photo: UploadFile = File(...),
    note: str | None = Form(default=None),
    child: UUID = Depends(child_id_dep),
    store: BigKidStore = Depends(get_store),
) -> Task:
    content = await photo.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="photo too large")
    # Build an absolute URL pointing back at our own /evidence route so the
    # parent app's AsyncImage can load it without knowing the server host.
    base = f"{request.url.scheme}://{request.url.netloc}"
    url = evidence_url(child, task_id, base=base)
    return store.submit_evidence(
        child, task_id, photo_url=url, photo_bytes=content, note=note,
    )


@router.get("/child/evidence/{child_id}/{task_id}.jpg")
def fetch_evidence(
    child_id: UUID, task_id: UUID,
    store: BigKidStore = Depends(get_store),
) -> Response:
    """Serve the stored evidence JPEG bytes back to the parent app.
    Public read (no X-Child-Id) so the parent can load via AsyncImage —
    URLs are unguessable UUIDs, acceptable for v1; revisit when Supabase
    Storage replaces the in-memory blob (spec §13 Q4)."""
    blob = store.get_evidence_blob(child_id, task_id)
    if blob is None:
        raise HTTPException(status_code=404, detail="evidence not found")
    return Response(content=blob, media_type="image/jpeg")


@router.post("/child/bypass", response_model=BypassRequest)
def create_bypass(
    body: BypassCreateBody,
    child: UUID = Depends(child_id_dep),
    store: BigKidStore = Depends(get_store),
) -> BypassRequest:
    return store.create_bypass(child, body.task_id, body.reason)


class StepCompleteBody(BaseModel):
    step: ReflectionStep


class NudgeResponse(BaseModel):
    ends_at: datetime


@router.post("/child/reflection/{rid}/step-complete", response_model=ReflectionRequest)
def reflection_step_complete(
    rid: UUID, body: StepCompleteBody,
    child: UUID = Depends(child_id_dep),
    store: BigKidStore = Depends(get_store),
) -> ReflectionRequest:
    return store.complete_reflection_step(child, rid, body.step)


@router.post("/child/reflection/{rid}/quiz-answer", response_model=QuizAnswerResponse)
def reflection_quiz_answer(
    rid: UUID, body: QuizAnswerBody,
    child: UUID = Depends(child_id_dep),
    store: BigKidStore = Depends(get_store),
) -> QuizAnswerResponse:
    is_correct, all_correct, score = store.answer_quiz_question(
        child, rid, question_index=body.question_index, selected_index=body.selected_index,
    )
    return QuizAnswerResponse(correct=is_correct, all_correct=all_correct, score=score)


@router.post("/child/reflection/{rid}/essay", response_model=ReflectionRequest)
def reflection_essay(
    rid: UUID, body: EssaySubmitBody,
    child: UUID = Depends(child_id_dep),
    store: BigKidStore = Depends(get_store),
) -> ReflectionRequest:
    return store.submit_essay(child, rid, text=body.text)


@router.post("/child/reflection/{rid}/nudge", response_model=NudgeResponse)
def reflection_nudge(
    rid: UUID,
    child: UUID = Depends(child_id_dep),
    store: BigKidStore = Depends(get_store),
) -> NudgeResponse:
    return NudgeResponse(ends_at=store.nudge_parent(child, rid))


@router.post("/child/reflection/{rid}/ack", response_model=None)
def reflection_ack(
    rid: UUID,
    child: UUID = Depends(child_id_dep),
    store: BigKidStore = Depends(get_store),
) -> Response:
    store.ack_reflection(child, rid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/child/daily-complete/ack", response_model=None)
def daily_complete_ack(
    child: UUID = Depends(child_id_dep),
    store: BigKidStore = Depends(get_store),
) -> Response:
    store.ack_daily_complete(child)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/child/screen-time-finished/ack", response_model=None)
def screen_time_finished_ack(
    child: UUID = Depends(child_id_dep),
    store: BigKidStore = Depends(get_store),
) -> Response:
    store.ack_screen_time_finished(child)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/child/time-consumption")
def time_consumption(
    body: TimeConsumptionBody,
    child: UUID = Depends(child_id_dep),
    store: BigKidStore = Depends(get_store),
) -> dict:
    minutes_left = store.record_time_use(child, minutes_used=body.minutes_used)
    return {"minutes_left": minutes_left}
