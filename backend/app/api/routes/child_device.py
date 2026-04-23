"""Child Device — endpoints for child phone to register and poll for actions.

POST /api/v1/child/register — Register a child device
GET  /api/v1/child/pending-actions/{child_id} — Poll for pending actions
POST /api/v1/child/ack/{action_id} — Acknowledge action was executed
POST /api/v1/parent/device-action — Parent sends an action to child's device
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(tags=["Child Device"])

# In-memory store (replace with Supabase table later)
_registered_children: dict[str, dict] = {}
_pending_actions: list[dict] = []
_pairing_codes: dict[str, str] = {}  # code -> child_id


# ── Models ──

class ChildRegisterRequest(BaseModel):
    child_id: str
    child_name: str
    device_name: str = "iPhone"


class PairRequest(BaseModel):
    code: str


class DeviceActionRequest(BaseModel):
    child_id: str
    action_type: str  # lock, unlock, block_category
    params: dict[str, Any] = Field(default_factory=dict)  # e.g. {"minutes": 30, "categories": ["games"]}


# ── Child endpoints ──

@router.post("/child/register", summary="Register a child device")
async def register_child(req: ChildRegisterRequest) -> dict:
    # Generate a 6-digit pairing code
    code = str(random.randint(100000, 999999))
    # Ensure unique
    while code in _pairing_codes:
        code = str(random.randint(100000, 999999))

    _registered_children[req.child_id] = {
        "child_id": req.child_id,
        "child_name": req.child_name,
        "device_name": req.device_name,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }
    _pairing_codes[code] = req.child_id
    logger.info("Child device registered: {} ({}) — pairing code: {}", req.child_name, req.child_id, code)
    return {"status": "registered", "child_id": req.child_id, "pairing_code": code}


@router.get("/child/pending-actions/{child_id}", summary="Poll for pending actions")
async def get_pending_actions(child_id: str) -> list[dict]:
    # Update last_seen
    if child_id in _registered_children:
        _registered_children[child_id]["last_seen"] = datetime.now(timezone.utc).isoformat()

    # Return unacknowledged actions for this child
    pending = [
        a for a in _pending_actions
        if a["child_id"] == child_id and not a.get("acknowledged", False)
    ]
    return pending


@router.post("/child/ack/{action_id}", summary="Acknowledge action was executed")
async def acknowledge_action(action_id: str) -> dict:
    for a in _pending_actions:
        if a["action_id"] == action_id:
            a["acknowledged"] = True
            a["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
            logger.info("Action acknowledged: {}", action_id)
            return {"status": "acknowledged"}
    raise HTTPException(status_code=404, detail="Action not found")


# ── Parent endpoint ──

@router.post("/parent/pair", summary="Pair with child device using code")
async def pair_with_child(req: PairRequest) -> dict:
    child_id = _pairing_codes.get(req.code)
    if not child_id:
        raise HTTPException(status_code=404, detail="Invalid pairing code")

    child = _registered_children.get(child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child device not found")

    logger.info("Parent paired with child {} via code {}", child_id, req.code)
    return {
        "status": "paired",
        "child_id": child_id,
        "child_name": child.get("child_name", ""),
    }


@router.post("/parent/device-action", summary="Send action to child device")
async def send_device_action(req: DeviceActionRequest) -> dict:
    action_id = str(uuid4())
    action = {
        "action_id": action_id,
        "child_id": req.child_id,
        "action_type": req.action_type,
        "params": req.params,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "acknowledged": False,
    }
    _pending_actions.append(action)
    logger.info("Action queued for {}: {} ({})", req.child_id, req.action_type, action_id)

    # Cleanup old acknowledged actions
    acknowledged = [a for a in _pending_actions if a.get("acknowledged")]
    if len(acknowledged) > 10:
        for old in acknowledged[:-10]:
            _pending_actions.remove(old)

    return {"status": "queued", "action_id": action_id}


# ── Status endpoint ──

@router.get("/parent/children", summary="List registered child devices")
async def list_children() -> list[dict]:
    return list(_registered_children.values())


# ===== New — Three-tier lock command queue (Phase 2 Task 2.9) =====

from datetime import datetime, timezone  # noqa: E402,F811
from uuid import UUID  # noqa: E402

from fastapi import Depends  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from backend.app.db.engine import get_async_session  # noqa: E402
from backend.app.db.models.command import Command, AckStatus, PendingBlob  # noqa: E402


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
