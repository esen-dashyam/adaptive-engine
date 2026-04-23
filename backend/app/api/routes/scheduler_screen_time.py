"""Scheduler — Screen Time control endpoints.

GET  /api/v1/scheduler/screen-time/status/{student_id}  — iOS polls for unlock status
POST /api/v1/scheduler/screen-time/rules                — parent sets unlock rules
GET  /api/v1/scheduler/screen-time/rules/{student_id}   — get current rules
POST /api/v1/scheduler/screen-time/events               — iOS logs unlock/relock events
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from backend.app.services.supabase_client import get_supabase

router = APIRouter(prefix="/scheduler/screen-time", tags=["Scheduler — Screen Time"])


# ── Request / Response models ────────────────────────────


class ScreenTimeRuleRequest(BaseModel):
    student_id: str
    required_sessions: int = Field(default=3, ge=1, le=20)
    unlock_minutes: int = Field(default=30, ge=5, le=480)
    active: bool = True


class ScreenTimeEventRequest(BaseModel):
    student_id: str
    event_type: str  # "unlock", "relock", "poll", "authorize"
    details: dict[str, Any] = {}


class ScreenTimeStatus(BaseModel):
    unlocked: bool
    completed_sessions: int
    required_sessions: int
    unlock_minutes: int = 0
    reason: str = ""  # "no_rules", "inactive", "not_enough", "unlocked"


# ── Helpers ──────────────────────────────────────────────


def _count_completed_today(sb, student_id: str) -> int:
    """Count how many sessions the student completed today."""
    today_str = str(date.today())

    # Get all active schedules for this student
    schedules = (
        sb.table("schedules")
        .select("id")
        .eq("student_id", student_id)
        .eq("status", "active")
        .execute()
        .data
    )
    if not schedules:
        return 0

    total = 0
    for sch in schedules:
        rows = (
            sb.table("session_instances")
            .select("id")
            .eq("schedule_id", sch["id"])
            .eq("session_date", today_str)
            .eq("status", "completed")
            .execute()
            .data
        )
        total += len(rows)
    return total


# ── Routes ───────────────────────────────────────────────


@router.get("/status/{student_id}", summary="Check unlock status (iOS polls this)")
async def get_unlock_status(student_id: str) -> dict[str, Any]:
    """Return whether the student has earned screen time today.

    The iOS app polls this every ~2 minutes to decide whether to
    shield or unshield managed apps.
    """
    try:
        sb = get_supabase()

        # 1. Get the student's screen time rules
        rules = (
            sb.table("screen_time_rules")
            .select("*")
            .eq("student_id", student_id)
            .execute()
            .data
        )
        if not rules:
            return ScreenTimeStatus(
                unlocked=False,
                completed_sessions=0,
                required_sessions=0,
                reason="no_rules",
            ).model_dump()

        rule = rules[0]
        if not rule.get("active", True):
            return ScreenTimeStatus(
                unlocked=False,
                completed_sessions=0,
                required_sessions=rule["required_sessions"],
                reason="inactive",
            ).model_dump()

        # 2. Count today's completed sessions
        completed = _count_completed_today(sb, student_id)
        required = rule["required_sessions"]
        unlock_minutes = rule["unlock_minutes"]

        if completed >= required:
            return ScreenTimeStatus(
                unlocked=True,
                completed_sessions=completed,
                required_sessions=required,
                unlock_minutes=unlock_minutes,
                reason="unlocked",
            ).model_dump()
        else:
            return ScreenTimeStatus(
                unlocked=False,
                completed_sessions=completed,
                required_sessions=required,
                unlock_minutes=unlock_minutes,
                reason="not_enough",
            ).model_dump()

    except Exception as exc:
        logger.error("Failed to get screen time status for {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/rules", summary="Set screen time rules for a student")
async def upsert_screen_time_rules(body: ScreenTimeRuleRequest) -> dict[str, Any]:
    """Create or update screen time rules. One rule per student (upsert)."""
    try:
        sb = get_supabase()
        data = {
            "student_id": body.student_id,
            "required_sessions": body.required_sessions,
            "unlock_minutes": body.unlock_minutes,
            "active": body.active,
        }
        result = (
            sb.table("screen_time_rules")
            .upsert(data, on_conflict="student_id")
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to save rules")
        return result.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to save screen time rules: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/rules/{student_id}", summary="Get screen time rules")
async def get_screen_time_rules(student_id: str) -> dict[str, Any]:
    """Return the screen time rules for a student."""
    try:
        sb = get_supabase()
        rules = (
            sb.table("screen_time_rules")
            .select("*")
            .eq("student_id", student_id)
            .execute()
            .data
        )
        if not rules:
            raise HTTPException(status_code=404, detail="No rules configured for this student")
        return rules[0]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get screen time rules for {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/events", summary="Log screen time event from iOS app")
async def log_screen_time_event(body: ScreenTimeEventRequest) -> dict[str, Any]:
    """iOS app logs unlock/relock/poll events for parent audit trail."""
    try:
        sb = get_supabase()
        result = (
            sb.table("screen_time_events")
            .insert({
                "student_id": body.student_id,
                "event_type": body.event_type,
                "details": body.details,
            })
            .execute()
        )
        return result.data[0] if result.data else {"ok": True}
    except Exception as exc:
        logger.error("Failed to log screen time event: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc))
