"""Child Device — command queue, ack, blob fetch, and heartbeat endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["Child Device"])

from backend.app.db.engine import get_async_session
from backend.app.db.models.command import AckStatus, Command, PendingBlob


class PendingCommandResponse(BaseModel):
    """Shape sent to the child device — embeds the payload inline."""
    command_id: UUID
    action: str
    tier: str | None
    target: dict
    duration_minutes: int | None
    issued_at: str


@router.get("/child/commands", response_model=list[PendingCommandResponse], tags=["Evlin Child Device"])
async def list_pending_commands(
    device_id: UUID,
    session: AsyncSession = Depends(get_async_session),
) -> list[PendingCommandResponse]:
    """Child polls: returns commands not yet acked. Marks picked_up_at on first fetch."""
    stmt = (
        select(Command)
        .where(Command.target_device_id == device_id, Command.ack_status == AckStatus.pending)
        .order_by(Command.created_at.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    now = datetime.now(timezone.utc)
    out: list[PendingCommandResponse] = []
    for c in rows:
        if c.picked_up_at is None:
            c.picked_up_at = now
        payload = c.payload or {}
        out.append(PendingCommandResponse(
            command_id=c.id,
            action=payload.get("action", "lock"),
            tier=payload.get("tier"),
            target=payload.get("target", {}),
            duration_minutes=payload.get("duration_minutes"),
            issued_at=payload.get("issued_at", ""),
        ))
    return out


class AckRequest(BaseModel):
    command_id: UUID
    status: str          # must match one of AckStatus values
    detail: dict | None = None


@router.post("/child/ack", tags=["Evlin Child Device"])
async def ack_command(
    req: AckRequest,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Child posts ack: confirmed_exact | confirmed_fallback | failed | timeout."""
    cmd = await session.get(Command, req.command_id)
    if cmd is None:
        raise HTTPException(404, "command not found")
    try:
        cmd.ack_status = AckStatus(req.status)
    except ValueError:
        raise HTTPException(400, f"invalid status: {req.status}")
    cmd.acked_at = datetime.now(timezone.utc)
    cmd.ack_detail = req.detail

    # Clean up any pending blob row — child has already consumed or failed
    blob_stmt = select(PendingBlob).where(PendingBlob.command_id == req.command_id)
    blob = (await session.execute(blob_stmt)).scalar_one_or_none()
    if blob is not None:
        await session.delete(blob)

    return {"ok": True}


class PendingBlobResponse(BaseModel):
    command_id: UUID
    blob_base64: str


@router.get("/child/pending-blob", response_model=PendingBlobResponse, tags=["Evlin Child Device"])
async def get_pending_blob(
    command_id: UUID,
    session: AsyncSession = Depends(get_async_session),
) -> PendingBlobResponse:
    """One-shot fetch + delete of a Max-mode selection blob.

    If the blob is expired or already consumed, returns 404.
    """
    import base64
    blob = await session.get(PendingBlob, command_id)
    if blob is None:
        raise HTTPException(404, "no pending blob (expired, never attached, or already fetched)")

    now = datetime.now(timezone.utc)
    if blob.expires_at < now:
        await session.delete(blob)
        raise HTTPException(410, "blob expired")

    encoded = base64.b64encode(blob.blob).decode()
    await session.delete(blob)
    return PendingBlobResponse(command_id=command_id, blob_base64=encoded)


# Heartbeat endpoint (Phase 5 finalizes; stub now so migrations aren't needed later)

class HeartbeatRequest(BaseModel):
    device_id: UUID


@router.post("/child/heartbeat", tags=["Evlin Child Device"])
async def heartbeat(
    req: HeartbeatRequest,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from backend.app.db.models.device import Device
    dev = await session.get(Device, req.device_id)
    if dev is None:
        raise HTTPException(404, "device not found")
    dev.last_heartbeat = datetime.now(timezone.utc)
    return {"ok": True}


class RegisterApnsRequest(BaseModel):
    device_id: UUID
    apns_token: str


@router.post("/child/register-apns", tags=["Evlin Child Device"])
async def register_apns(
    req: RegisterApnsRequest,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from backend.app.db.models.device import Device
    dev = await session.get(Device, req.device_id)
    if dev is None:
        raise HTTPException(404, "device not found")
    dev.apns_token = req.apns_token
    return {"ok": True}
