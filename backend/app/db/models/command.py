"""Command + PendingBlob — queued lock/unlock commands and ephemeral Max-mode blobs.

Command.payload shape (v2, 2026-04-24):
{
  "action": "shield" | "block" | "unshield" | "unblock"
          | "unshield_all" | "unblock_all" | "expand_library",
  "tier": "exactApp" | "savedList" | "category" | "all" | null,
  "target": {
    "bundle_id": str | null,
    "list_name": str | null,
    "list_id": str (UUID) | null,
    "category_hint": str | null,
    "target_all": bool,
    "target_child_id": str (UUID) | null,
    "target_display": str | null,
    "original_request": str,
    "has_pending_blob": bool,
    "force_downgrade": bool          # parent-confirmed B1 downgrade; skips merge rule on child
  },
  "duration_minutes": int | null,
  "issued_at": ISO8601 datetime
}

Legacy payloads (action=lock/unlock/lock_all/unlock_all) are still accepted
by Phase 11 migration logic.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, JSON, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class AckStatus(str, enum.Enum):
    pending = "pending"
    confirmed_exact = "confirmed_exact"
    confirmed_fallback = "confirmed_fallback"
    confirmed = "confirmed"                       # v2: verb-agnostic success (see ack_verb)
    pending_confirmation = "pending_confirmation"  # v2: B1-style child-requested confirm
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

    # v2 ack fields (plan Phase 6 Task 6.4):
    # - ack_verb: which verb succeeded (shield/block/unshield/unblock/unshield_all/unblock_all)
    # - ack_effective_state: coverage snapshot after mutation, for "Still shielded by ..." disclosure
    # - ack_card_id + ack_context: B1-style pending_confirmation payload
    ack_verb: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ack_effective_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ack_card_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ack_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)


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
