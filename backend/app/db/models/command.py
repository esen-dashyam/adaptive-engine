"""Command + PendingBlob — queued lock/unlock commands and ephemeral Max-mode blobs."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, JSON, LargeBinary, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class AckStatus(str, enum.Enum):
    pending = "pending"
    confirmed_exact = "confirmed_exact"
    confirmed_fallback = "confirmed_fallback"
    failed = "failed"
    timeout = "timeout"


class Command(Base):
    __tablename__ = "evlin_commands"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evlin_families.id"), index=True
    )
    target_device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evlin_devices.id"), index=True
    )
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    picked_up_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ack_status: Mapped[AckStatus] = mapped_column(
        SAEnum(AckStatus, name="evlin_ack_status"), default=AckStatus.pending
    )
    ack_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


def _default_blob_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=10)


class PendingBlob(Base):
    """Ephemeral relay for Max-mode FamilyActivitySelection blobs.

    Inserted by parent device via /parent/commands/attach-blob.
    Fetched exactly once by child device via /child/pending-blob.
    Deleted on fetch OR at expires_at, whichever comes first.
    """
    __tablename__ = "evlin_pending_blobs"

    command_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evlin_commands.id"), primary_key=True
    )
    blob: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_default_blob_expiry
    )
