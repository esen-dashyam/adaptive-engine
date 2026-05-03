"""Pydantic schemas for big-kid child mode (spec §4 + §8.1)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TaskCategory(str, Enum):
    chores = "Chores"
    homework = "Homework"
    self_care = "Self-care"


class TaskStatus(str, Enum):
    todo = "todo"
    submitted = "submitted"
    done = "done"
    overdue = "overdue"


class TaskPhase(str, Enum):
    input = "input"
    submitted = "submitted"
    redo = "redo"


class BypassStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    denied = "denied"
    withdrawn = "withdrawn"


class ReflectionStatus(str, Enum):
    pending = "pending"
    submitted = "submitted"
    approved = "approved"


class ReflectionStep(str, Enum):
    video = "video"
    quiz = "quiz"
    writing = "writing"


class BypassRequest(BaseModel):
    id: UUID
    task_id: UUID
    reason: str
    status: BypassStatus
    parent_response: Optional[str] = None
    created_at: datetime
    responded_at: Optional[datetime] = None


class Task(BaseModel):
    id: UUID
    title: str
    description: str
    category: TaskCategory
    due: Optional[str] = None         # human-readable, e.g. "8:00 AM"
    status: TaskStatus
    phase: TaskPhase
    redo_reason: Optional[str] = None
    evidence_photo_url: Optional[str] = None
    evidence_note: Optional[str] = None     # kid's note attached to the photo
    bypass: Optional[BypassRequest] = None


class QuizQuestionPublic(BaseModel):
    """Quiz question without correctIndex — for child consumption."""
    q: str
    options: list[str]


class ReflectionRequest(BaseModel):
    id: UUID
    reason: str                            # raw parent input (kept for record)
    display_reason: Optional[str] = None   # Gemini-rephrased kid-facing version
    video_id: str
    video_title: str
    writing_prompt: str
    quiz: list[QuizQuestionPublic]

    # progress
    steps_completed: list[ReflectionStep] = Field(default_factory=list)
    quiz_score: Optional[int] = None
    essay_text: Optional[str] = None

    # lifecycle
    status: ReflectionStatus
    parent_note: Optional[str] = None
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None


class ChildStateResponse(BaseModel):
    """GET /child/state — full snapshot for routing layer (spec §8.1)."""
    child_name: str
    minutes_left: int
    minutes_max: int
    tasks: list[Task]
    reflection_request: Optional[ReflectionRequest] = None
    notify_parent_cooldown_ends_at: Optional[datetime] = None
    daily_complete_acknowledged: bool
    screen_time_finished_acknowledged: bool


class BypassCreateBody(BaseModel):
    task_id: UUID
    reason: str


class QuizAnswerBody(BaseModel):
    question_index: int
    selected_index: int


class QuizAnswerResponse(BaseModel):
    correct: bool
    all_correct: bool
    score: int


class EssaySubmitBody(BaseModel):
    text: str


class TimeConsumptionBody(BaseModel):
    minutes_used: int


class ParentReflectionTriggerBody(BaseModel):
    child_id: UUID
    reason: str


class ParentReflectionApproveBody(BaseModel):
    parent_note: Optional[str] = None


class ParentBypassRespondBody(BaseModel):
    decision: str   # "approve" | "deny"
    message: Optional[str] = None


class ParentTaskReviewBody(BaseModel):
    decision: str   # "approve" | "redo"
    redo_reason: Optional[str] = None
