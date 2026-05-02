"""In-memory big-kid state store. Seeded once per process from fixture JSON.

This is the v1 backend — keeps the API surface stable while a real DB schema
is decided. Replace with SQLAlchemy persistence in a follow-up plan.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from backend.app.schemas.bigkid import (
    BypassRequest, BypassStatus, ChildStateResponse, QuizQuestionPublic,
    ReflectionRequest, ReflectionStatus, ReflectionStep, Task, TaskCategory,
    TaskPhase, TaskStatus,
)


_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "bigkid_reflection_seed.json"


class BigKidStore:
    """Process-local store. Not multi-worker safe; suitable for dev/staging."""

    def __init__(self) -> None:
        self._states: dict[UUID, _ChildState] = {}
        self._reflection_correct: dict[tuple[UUID, UUID], list[int]] = {}

    # ---------- read ----------

    def get_state(self, child_id: UUID) -> ChildStateResponse:
        s = self._ensure_seeded(child_id)
        return s.snapshot()

    # ---------- task evidence ----------

    def submit_evidence(
        self, child_id: UUID, task_id: UUID, *, photo_url: str, note: str | None
    ) -> Task:
        s = self._ensure_seeded(child_id)
        task = s.task(task_id)
        task.evidence_photo_url = photo_url
        task.status = TaskStatus.submitted
        task.phase = TaskPhase.submitted
        if task.bypass and task.bypass.status == BypassStatus.pending:
            task.bypass.status = BypassStatus.withdrawn
            task.bypass.responded_at = datetime.now(timezone.utc)
        return task

    # ---------- bypass ----------

    def create_bypass(self, child_id: UUID, task_id: UUID, reason: str) -> BypassRequest:
        s = self._ensure_seeded(child_id)
        task = s.task(task_id)
        bypass = BypassRequest(
            id=uuid4(), task_id=task_id, reason=reason,
            status=BypassStatus.pending, created_at=datetime.now(timezone.utc),
        )
        task.bypass = bypass
        return bypass

    # ---------- parent task review ----------

    def parent_review_task(
        self, child_id: UUID, task_id: UUID, *, decision: str, redo_reason: str | None
    ) -> Task:
        s = self._ensure_seeded(child_id)
        task = s.task(task_id)
        if decision == "approve":
            task.status = TaskStatus.done
            task.phase = TaskPhase.submitted  # phase is irrelevant once done
        elif decision == "redo":
            task.status = TaskStatus.todo
            task.phase = TaskPhase.redo
            task.redo_reason = redo_reason
        else:
            raise ValueError(f"unknown decision: {decision}")
        s.recompute_time_pool()
        return task

    # ---------- reflection ----------

    def trigger_reflection(self, child_id: UUID, reason: str) -> ReflectionRequest:
        s = self._ensure_seeded(child_id)
        seed = _load_fixture()
        rid = uuid4()
        public_quiz = [QuizQuestionPublic(q=q["q"], options=q["options"]) for q in seed["quiz"]]
        correct = [int(q["correctIndex"]) for q in seed["quiz"]]
        req = ReflectionRequest(
            id=rid, reason=reason,
            video_id=seed["videoId"], video_title=seed["videoTitle"],
            writing_prompt=seed["writingPrompt"],
            quiz=public_quiz, status=ReflectionStatus.pending,
        )
        s.reflection = req
        self._reflection_correct[(child_id, rid)] = correct
        return req

    def trigger_reflection_with_content(
        self, child_id: UUID, *, reason: str,
        video_id: str, video_title: str, writing_prompt: str,
        quiz_public: list[QuizQuestionPublic], correct_indices: list[int],
    ) -> ReflectionRequest:
        s = self._ensure_seeded(child_id)
        rid = uuid4()
        req = ReflectionRequest(
            id=rid, reason=reason,
            video_id=video_id, video_title=video_title,
            writing_prompt=writing_prompt, quiz=quiz_public,
            status=ReflectionStatus.pending,
        )
        s.reflection = req
        self._reflection_correct[(child_id, rid)] = correct_indices
        return req

    def complete_reflection_step(
        self, child_id: UUID, rid: UUID, step: ReflectionStep
    ) -> ReflectionRequest:
        req = self._require_reflection(child_id, rid)
        if step not in req.steps_completed:
            req.steps_completed.append(step)
        return req

    def answer_quiz_question(
        self, child_id: UUID, rid: UUID, *, question_index: int, selected_index: int
    ) -> tuple[bool, bool, int]:
        req = self._require_reflection(child_id, rid)
        correct = self._reflection_correct[(child_id, rid)]
        is_correct = selected_index == correct[question_index]
        score_attr = "_answers"
        answers: dict[int, int] = getattr(req, score_attr, {})
        answers[question_index] = selected_index
        setattr(req, score_attr, answers)
        score = sum(1 for i, sel in answers.items() if sel == correct[i])
        all_correct = len(answers) == len(correct) and score == len(correct)
        if len(answers) == len(correct):
            req.quiz_score = score
            if score >= 4:
                if ReflectionStep.quiz not in req.steps_completed:
                    req.steps_completed.append(ReflectionStep.quiz)
        return is_correct, all_correct, score

    def submit_essay(self, child_id: UUID, rid: UUID, *, text: str) -> ReflectionRequest:
        req = self._require_reflection(child_id, rid)
        req.essay_text = text
        if ReflectionStep.writing not in req.steps_completed:
            req.steps_completed.append(ReflectionStep.writing)
        if {ReflectionStep.video, ReflectionStep.quiz, ReflectionStep.writing}.issubset(set(req.steps_completed)):
            req.status = ReflectionStatus.submitted
            req.submitted_at = datetime.now(timezone.utc)
        return req

    def nudge_parent(self, child_id: UUID, rid: UUID) -> datetime:
        s = self._ensure_seeded(child_id)
        if s.notify_cooldown_ends_at and s.notify_cooldown_ends_at > datetime.now(timezone.utc):
            return s.notify_cooldown_ends_at  # idempotent within window
        ends = datetime.now(timezone.utc) + timedelta(minutes=5)
        s.notify_cooldown_ends_at = ends
        return ends

    def parent_approve_reflection(
        self, child_id: UUID, rid: UUID, *, parent_note: str | None
    ) -> ReflectionRequest:
        req = self._require_reflection(child_id, rid)
        if req.status != ReflectionStatus.submitted:
            raise ValueError("can only approve a submitted reflection")
        req.status = ReflectionStatus.approved
        req.parent_note = parent_note
        req.approved_at = datetime.now(timezone.utc)
        return req

    def ack_reflection(self, child_id: UUID, rid: UUID) -> None:
        s = self._ensure_seeded(child_id)
        if s.reflection and s.reflection.id == rid:
            s.reflection = None
            s.notify_cooldown_ends_at = None

    # ---------- end-of-day acks ----------

    def ack_daily_complete(self, child_id: UUID) -> None:
        s = self._ensure_seeded(child_id)
        s.daily_complete_acknowledged = True

    def ack_screen_time_finished(self, child_id: UUID) -> None:
        s = self._ensure_seeded(child_id)
        s.screen_time_finished_acknowledged = True

    # ---------- time consumption ----------

    def record_time_use(self, child_id: UUID, *, minutes_used: int) -> int:
        s = self._ensure_seeded(child_id)
        s.minutes_left = max(0, s.minutes_left - minutes_used)
        return s.minutes_left

    # ---------- internal ----------

    def _reflection_correct_indices(self, child_id: UUID, rid: UUID) -> list[int]:
        return self._reflection_correct[(child_id, rid)]

    def _ensure_seeded(self, child_id: UUID) -> "_ChildState":
        if child_id not in self._states:
            self._states[child_id] = _ChildState.seed_default()
        return self._states[child_id]

    def _require_reflection(self, child_id: UUID, rid: UUID) -> ReflectionRequest:
        s = self._ensure_seeded(child_id)
        if not s.reflection or s.reflection.id != rid:
            raise ValueError(f"no active reflection {rid} for child {child_id}")
        return s.reflection


class _ChildState:
    """Internal representation; converts to ChildStateResponse on demand."""

    def __init__(
        self, *, child_name: str, minutes_max: int, tasks: list[Task],
    ) -> None:
        self.child_name = child_name
        self.minutes_max = minutes_max
        self.minutes_left = 0
        self.tasks = tasks
        self.reflection: ReflectionRequest | None = None
        self.notify_cooldown_ends_at: datetime | None = None
        self.daily_complete_acknowledged = False
        self.screen_time_finished_acknowledged = False

    @classmethod
    def seed_default(cls) -> "_ChildState":
        tasks = [
            Task(
                id=uuid4(), title="Make bed", description="Smooth the covers and fluff the pillow.",
                category=TaskCategory.chores, due="8:00 AM",
                status=TaskStatus.todo, phase=TaskPhase.input,
            ),
            Task(
                id=uuid4(), title="Math homework",
                description="Page 42, problems 1–10. Show your work.",
                category=TaskCategory.homework, due="6:00 PM",
                status=TaskStatus.todo, phase=TaskPhase.input,
            ),
            Task(
                id=uuid4(), title="Brush teeth",
                description="Two minutes, top and bottom.",
                category=TaskCategory.self_care, due="9:00 PM",
                status=TaskStatus.todo, phase=TaskPhase.input,
            ),
        ]
        return cls(child_name="Liam", minutes_max=120, tasks=tasks)

    def task(self, task_id: UUID) -> Task:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise ValueError(f"task {task_id} not found")

    def all_tasks_done(self) -> bool:
        return all(
            t.status == TaskStatus.done or
            (t.bypass is not None and t.bypass.status == BypassStatus.approved)
            for t in self.tasks
        )

    def recompute_time_pool(self) -> None:
        if self.all_tasks_done() and self.minutes_left == 0:
            self.minutes_left = self.minutes_max

    def snapshot(self) -> ChildStateResponse:
        return ChildStateResponse(
            child_name=self.child_name,
            minutes_left=self.minutes_left,
            minutes_max=self.minutes_max,
            tasks=[t.model_copy(deep=True) for t in self.tasks],
            reflection_request=self.reflection.model_copy(deep=True) if self.reflection else None,
            notify_parent_cooldown_ends_at=self.notify_cooldown_ends_at,
            daily_complete_acknowledged=self.daily_complete_acknowledged,
            screen_time_finished_acknowledged=self.screen_time_finished_acknowledged,
        )


def _load_fixture() -> dict:
    with _FIXTURE_PATH.open() as f:
        return json.load(f)


_singleton: BigKidStore | None = None


def get_store() -> BigKidStore:
    """FastAPI dependency injector."""
    global _singleton
    if _singleton is None:
        _singleton = BigKidStore()
    return _singleton


def stub_upload_evidence(child_id: UUID, task_id: UUID, content: bytes) -> str:
    """Placeholder upload — returns a fake stable URL.
    Replace with Supabase Storage upload in a follow-up task (per spec §13 Q4).
    """
    return f"https://storage.evlin.local/evidence/{child_id}/{task_id}/photo.jpg"
