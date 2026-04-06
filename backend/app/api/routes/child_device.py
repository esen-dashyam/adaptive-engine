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
