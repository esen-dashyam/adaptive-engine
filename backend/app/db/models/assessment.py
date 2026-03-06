"""
Assessment session and per-question answer records (PostgreSQL).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base


class AssessmentSession(Base):
    """One row per completed assessment run."""

    __tablename__ = "assessment_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    grade: Mapped[str | None] = mapped_column(String(10), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(100), nullable=True)
    framework: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state_jurisdiction: Mapped[str | None] = mapped_column(String(50), nullable=True)

    num_questions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="Percentage correct 0-1"
    )
    phase: Mapped[str] = mapped_column(
        String(30), default="in_progress",
        comment="in_progress | evaluated | remediation | done"
    )

    # Full gap + remediation analysis stored as JSONB for easy querying
    gap_analysis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    remediation_plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    student: Mapped[Student] = relationship(back_populates="sessions")
    answers: Mapped[list[AssessmentAnswer]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class AssessmentAnswer(Base):
    """One row per question answered within a session."""

    __tablename__ = "assessment_answers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment_sessions.id", ondelete="CASCADE"), index=True
    )

    question_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    standard_code: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="LC StandardsFrameworkItem identifier"
    )
    category: Mapped[str | None] = mapped_column(
        String(20), nullable=True, comment="prerequisite | target"
    )
    dok_level: Mapped[int | None] = mapped_column(Integer, nullable=True)

    student_answer: Mapped[str | None] = mapped_column(String(10), nullable=True)
    correct_answer: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    mastery_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    mastery_after: Mapped[float | None] = mapped_column(Float, nullable=True)

    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped[AssessmentSession] = relationship(back_populates="answers")


# avoid circular import
from backend.app.db.models.student import Student  # noqa: E402, F401
