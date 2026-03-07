"""
Chat session working memory and failure chain audit tables (PostgreSQL).

ChatSession  — one row per student, updated in place. Tracks which concept
               the tutor is currently working on, the active pedagogical
               strategy (Socratic / Visual / CRA), consecutive-struggle count,
               and when the student last sent a message (used for silence detection).

FailureChain — immutable audit log written every time the system detects a
               prerequisite gap via metacognitive signal or misconception analysis.
               Maps observed-failure node → LCA root-prerequisite node.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class ChatSession(Base):
    """
    Per-student working memory for the AI tutor.

    Upserted on every /chat/tutor call — MERGE on student_id.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Neo4j student_id is a plain string (not a PG UUID FK)
    student_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    # The Neo4j StandardsFrameworkItem identifier the tutor is currently focused on.
    current_node_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    current_node_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # pedagogical_strategy: "socratic" → "visual" → "cra" as struggles mount
    pedagogical_strategy: Mapped[str] = mapped_column(
        String(20), default="socratic",
        comment="socratic | visual | cra"
    )

    # How many consecutive incorrect attempts on the current node
    consecutive_struggles: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamp of the last student message — used to detect silence (> 120 s)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FailureChain(Base):
    """
    Immutable audit record written when the system detects a prerequisite gap.

    Every row answers the question:
      "Student {student_id} failed at {failed_node_code} because they lack
       mastery of prerequisite {root_prereq_code} ({hops_to_lca} hops away)."
    """

    __tablename__ = "failure_chains"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    student_id: Mapped[str] = mapped_column(String(128), index=True)

    # The concept where failure was observed
    failed_node_id: Mapped[str] = mapped_column(String(512))
    failed_node_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # The LCA result — the nearest mastered ancestor
    root_prereq_node_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    root_prereq_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # How the gap was detected
    signal_source: Mapped[str] = mapped_column(
        String(30),
        comment="misconception | metacognitive_negative | silence | consecutive_struggles"
    )

    hops_to_lca: Mapped[int | None] = mapped_column(Integer, nullable=True)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
