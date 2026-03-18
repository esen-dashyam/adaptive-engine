"""Scheduler — Schedule & session endpoints (ported from Calendar Scheduler).

GET  /api/v1/scheduler/students/{id}/slots      — all weekly time slots
GET  /api/v1/scheduler/students/{id}/sessions    — sessions for a date range
GET  /api/v1/scheduler/students/{id}/today       — today's sessions
GET  /api/v1/scheduler/students/{id}/availability — availability windows
POST /api/v1/scheduler/sessions/{id}/checkin     — check in to a session
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from backend.app.services.supabase_client import get_supabase

router = APIRouter(prefix="/scheduler", tags=["Scheduler — Schedule"])


# ── Request / Response models ────────────────────────────


class CheckInRequest(BaseModel):
    notes: str | None = None


# ── Helpers ──────────────────────────────────────────────


def _get_active_schedules(sb, student_id: str) -> list[dict]:
    """Get active schedules with course info."""
    return (
        sb.table("schedules")
        .select("*, courses(*)")
        .eq("student_id", student_id)
        .eq("status", "active")
        .order("start_date")
        .execute()
        .data
    )


def _enrich_sessions(sessions: list[dict], schedules: list[dict]) -> list[dict]:
    """Add course_code, course_title, subject from schedule→course join."""
    course_lookup = {}
    for s in schedules:
        course_lookup[s["id"]] = s.get("courses", {})
    for row in sessions:
        course = course_lookup.get(row.get("schedule_id"), {})
        row["course_code"] = course.get("code", "")
        row["course_title"] = course.get("title", "")
        row["subject"] = course.get("subject", "")
    return sessions


# ── Routes ───────────────────────────────────────────────


@router.get("/students/{student_id}/slots", summary="Weekly time slots")
async def get_student_slots(student_id: str) -> list[dict[str, Any]]:
    """Return all schedule slots for a student across active schedules."""
    try:
        sb = get_supabase()
        schedules = _get_active_schedules(sb, student_id)
        all_slots = []
        for sch in schedules:
            slots = (
                sb.table("schedule_slots")
                .select("*")
                .eq("schedule_id", sch["id"])
                .order("day_of_week")
                .order("start_time")
                .execute()
                .data
            )
            course = sch.get("courses", {})
            for s in slots:
                s["course_title"] = course.get("title", "")
                s["course_code"] = course.get("code", "")
                s["subject"] = course.get("subject", "")
            all_slots.extend(slots)
        return all_slots
    except Exception as exc:
        logger.error("Failed to get slots for {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/students/{student_id}/availability", summary="Availability windows")
async def get_student_availability(student_id: str) -> list[dict[str, Any]]:
    """Return student's availability windows by day of week."""
    try:
        sb = get_supabase()
        data = (
            sb.table("availability")
            .select("*")
            .eq("student_id", student_id)
            .order("day_of_week")
            .order("start_time")
            .execute()
            .data
        )
        return data
    except Exception as exc:
        logger.error("Failed to get availability for {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/students/{student_id}/today", summary="Today's sessions")
async def get_today_sessions(student_id: str) -> list[dict[str, Any]]:
    """Return all sessions for today, enriched with course info."""
    try:
        sb = get_supabase()
        today = date.today()
        schedules = _get_active_schedules(sb, student_id)
        if not schedules:
            return []

        all_sessions = []
        for sch in schedules:
            resp = (
                sb.table("session_instances")
                .select("*")
                .eq("schedule_id", sch["id"])
                .eq("session_date", str(today))
                .order("start_time")
                .execute()
            )
            all_sessions.extend(resp.data)

        _enrich_sessions(all_sessions, schedules)
        all_sessions.sort(key=lambda x: str(x.get("start_time", "")))
        return all_sessions
    except Exception as exc:
        logger.error("Failed to get today's sessions for {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/students/{student_id}/sessions", summary="Sessions in date range")
async def get_sessions_range(
    student_id: str,
    start: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end: str = Query(..., description="End date (YYYY-MM-DD)"),
) -> list[dict[str, Any]]:
    """Return all sessions in a date range with course info."""
    try:
        sb = get_supabase()
        schedules = _get_active_schedules(sb, student_id)
        if not schedules:
            return []

        all_sessions = []
        for sch in schedules:
            resp = (
                sb.table("session_instances")
                .select("*")
                .eq("schedule_id", sch["id"])
                .gte("session_date", start)
                .lte("session_date", end)
                .order("session_date")
                .order("start_time")
                .execute()
            )
            all_sessions.extend(resp.data)

        _enrich_sessions(all_sessions, schedules)
        return all_sessions
    except Exception as exc:
        logger.error("Failed to get sessions for {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions/{session_id}/checkin", summary="Check in to a session")
async def check_in_session(
    session_id: str,
    body: CheckInRequest | None = None,
) -> dict[str, Any]:
    """Mark a session as completed (打卡)."""
    from datetime import datetime, timezone

    try:
        sb = get_supabase()
        now = datetime.now(timezone.utc).isoformat()
        notes = body.notes if body else None

        result = (
            sb.table("session_instances")
            .update({"status": "completed", "checked_in_at": now, "notes": notes})
            .eq("id", session_id)
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Session not found")

        # Log the check-in
        sb.table("checkin_log").insert({
            "session_instance_id": session_id,
            "action": "check_in",
            "performed_by": "parent",
            "details": {"notes": notes} if notes else {},
        }).execute()

        return result.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to check in session {}: {}", session_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))
