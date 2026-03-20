"""Scheduler — Schedule & session endpoints (ported from Calendar Scheduler).

GET  /api/v1/scheduler/students/{id}/slots      — all weekly time slots
GET  /api/v1/scheduler/students/{id}/sessions    — sessions for a date range
GET  /api/v1/scheduler/students/{id}/today       — today's sessions
GET  /api/v1/scheduler/students/{id}/availability — availability windows
POST /api/v1/scheduler/sessions/{id}/checkin     — check in to a session
POST /api/v1/scheduler/sessions/mark-missed/{id} — auto-mark missed + prerequisite defer
GET  /api/v1/scheduler/sessions/unresolved-missed/{id} — missed sessions needing reschedule
GET  /api/v1/scheduler/sessions/{id}/reschedule-slots  — available reschedule time slots
POST /api/v1/scheduler/sessions/{id}/reschedule        — execute reschedule
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


class MarkMissedRequest(BaseModel):
    auto_reschedule: bool = True


class RescheduleRequest(BaseModel):
    new_date: str       # YYYY-MM-DD
    new_start: str      # HH:MM
    new_end: str        # HH:MM


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


# ── Mark-missed + prerequisite-aware defer ────────────────


def _find_dependent_standards(standard_codes: list[str]) -> list[str]:
    """Query Neo4j BUILDS_TOWARDS to find standards that depend on the given codes."""
    if not standard_codes:
        return []
    try:
        from backend.app.core.settings import settings
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        with driver.session() as s:
            result = s.run(
                """
                MATCH (a:StandardsFrameworkItem)-[:BUILDS_TOWARDS]->(b:StandardsFrameworkItem)
                WHERE a.statementCode IN $codes AND b.statementCode IS NOT NULL
                RETURN COLLECT(DISTINCT b.statementCode) AS dependent_codes
                """,
                codes=standard_codes,
            ).single()
            dependent = result["dependent_codes"] if result else []
        driver.close()
        return dependent
    except Exception as exc:
        logger.warning("Neo4j prerequisite lookup failed: {}", exc)
        return []


def _find_reschedule_slots(
    sb, student_id: str, missed_session: dict, days_ahead: int = 14
) -> list[dict]:
    """Sliding window to find available time slots for rescheduling."""
    from datetime import datetime

    duration_mins = 60  # default
    try:
        start = datetime.strptime(missed_session["start_time"][:5], "%H:%M")
        end = datetime.strptime(missed_session["end_time"][:5], "%H:%M")
        duration_mins = int((end - start).total_seconds() / 60)
    except Exception:
        pass

    # Get student availability
    avail = (
        sb.table("availability")
        .select("*")
        .eq("student_id", student_id)
        .order("day_of_week")
        .order("start_time")
        .execute()
        .data
    )

    today = date.today()
    candidates = []
    for day_offset in range(1, days_ahead + 1):
        check_date = today + timedelta(days=day_offset)
        dow = check_date.weekday()  # 0=Mon

        # Find availability windows for this day of week
        day_avail = [a for a in avail if a.get("day_of_week") == dow]
        if not day_avail:
            continue

        # Check existing sessions on that date
        schedules = _get_active_schedules(sb, student_id)
        existing = []
        for sch in schedules:
            existing.extend(
                sb.table("session_instances")
                .select("start_time,end_time")
                .eq("schedule_id", sch["id"])
                .eq("session_date", str(check_date))
                .neq("status", "cancelled")
                .execute()
                .data
            )

        for window in day_avail:
            w_start = datetime.strptime(window["start_time"][:5], "%H:%M")
            w_end = datetime.strptime(window["end_time"][:5], "%H:%M")
            pref = window.get("preference", "available")

            # Slide through window in 30-min increments
            slot_start = w_start
            while slot_start + timedelta(minutes=duration_mins) <= w_end:
                slot_end = slot_start + timedelta(minutes=duration_mins)

                # Check conflict with existing sessions
                conflict = False
                for ex in existing:
                    ex_s = datetime.strptime(ex["start_time"][:5], "%H:%M")
                    ex_e = datetime.strptime(ex["end_time"][:5], "%H:%M")
                    if slot_start < ex_e and slot_end > ex_s:
                        conflict = True
                        break

                if not conflict:
                    candidates.append({
                        "date": str(check_date),
                        "day_name": check_date.strftime("%A"),
                        "start_time": slot_start.strftime("%H:%M"),
                        "end_time": slot_end.strftime("%H:%M"),
                        "preference": pref,
                    })

                slot_start += timedelta(minutes=30)

        if len(candidates) >= 5:
            break

    # Sort: preferred first, then by date
    pref_order = {"preferred": 0, "available": 1, "avoid": 2}
    candidates.sort(key=lambda x: (pref_order.get(x["preference"], 1), x["date"]))
    return candidates[:5]


@router.post("/sessions/mark-missed/{student_id}", summary="Auto-mark missed + prerequisite defer")
async def mark_missed_sessions(
    student_id: str,
    body: MarkMissedRequest | None = None,
) -> dict[str, Any]:
    """Mark past pending sessions as missed. Optionally defer dependent sessions."""
    from datetime import datetime, timezone

    try:
        sb = get_supabase()
        today = date.today()
        now = datetime.now(timezone.utc).isoformat()
        auto_reschedule = body.auto_reschedule if body else True

        schedules = _get_active_schedules(sb, student_id)
        if not schedules:
            return {"missed": [], "deferred": [], "rescheduled": []}

        # 1. Find all past pending sessions
        missed_sessions = []
        for sch in schedules:
            rows = (
                sb.table("session_instances")
                .select("*")
                .eq("schedule_id", sch["id"])
                .eq("status", "pending")
                .lt("session_date", str(today))
                .execute()
                .data
            )
            missed_sessions.extend(rows)

        if not missed_sessions:
            return {"missed": [], "deferred": [], "rescheduled": []}

        # 2. Mark them as missed
        missed_ids = []
        for ms in missed_sessions:
            sb.table("session_instances").update({"status": "missed"}).eq("id", ms["id"]).execute()
            sb.table("checkin_log").insert({
                "session_instance_id": ms["id"],
                "action": "auto_miss",
                "performed_by": "system",
            }).execute()
            missed_ids.append(ms["id"])

        _enrich_sessions(missed_sessions, schedules)

        # 3. Prerequisite-aware defer: check if missed courses have dependents
        deferred_sessions = []
        missed_standard_codes = []
        course_lookup = {sch["id"]: sch.get("courses", {}) for sch in schedules}
        for ms in missed_sessions:
            course = course_lookup.get(ms.get("schedule_id"), {})
            codes = course.get("standard_codes", [])
            if codes:
                missed_standard_codes.extend(codes)

        if missed_standard_codes:
            dependent_codes = _find_dependent_standards(missed_standard_codes)
            if dependent_codes:
                # Find future sessions whose courses contain dependent standards
                for sch in schedules:
                    course = sch.get("courses", {})
                    course_codes = course.get("standard_codes", [])
                    if not course_codes:
                        continue
                    # Check if any course standard is in dependent list
                    overlap = set(course_codes) & set(dependent_codes)
                    if not overlap:
                        continue
                    # Find pending future sessions for this course
                    future = (
                        sb.table("session_instances")
                        .select("*")
                        .eq("schedule_id", sch["id"])
                        .eq("status", "pending")
                        .gte("session_date", str(today))
                        .lte("session_date", str(today + timedelta(days=7)))
                        .execute()
                        .data
                    )
                    for fs in future:
                        sb.table("session_instances").update({"status": "deferred"}).eq("id", fs["id"]).execute()
                        sb.table("checkin_log").insert({
                            "session_instance_id": fs["id"],
                            "action": "auto_defer",
                            "performed_by": "system",
                            "details": {"reason": "prerequisite_missed", "missed_codes": missed_standard_codes[:3]},
                        }).execute()
                        fs["course_title"] = course.get("title", "")
                        fs["subject"] = course.get("subject", "")
                        deferred_sessions.append(fs)

        # 4. Auto-reschedule missed (simple sliding window)
        rescheduled = []
        if auto_reschedule:
            for ms in missed_sessions[:3]:  # limit to 3 auto-reschedules
                slots = _find_reschedule_slots(sb, student_id, ms, days_ahead=7)
                if slots:
                    slot = slots[0]
                    new_session = sb.table("session_instances").insert({
                        "schedule_id": ms["schedule_id"],
                        "schedule_slot_id": ms["schedule_slot_id"],
                        "session_date": slot["date"],
                        "start_time": slot["start_time"],
                        "end_time": slot["end_time"],
                        "status": "pending",
                        "rescheduled_from": ms["id"],
                    }).execute().data[0]
                    sb.table("session_instances").update({"status": "rescheduled", "rescheduled_to": new_session["id"]}).eq("id", ms["id"]).execute()
                    sb.table("checkin_log").insert({
                        "session_instance_id": ms["id"],
                        "action": "reschedule",
                        "performed_by": "system",
                        "details": {"new_date": slot["date"], "new_start": slot["start_time"]},
                    }).execute()
                    rescheduled.append({"missed_id": ms["id"], "new_session": new_session})

        return {
            "missed": missed_sessions,
            "deferred": deferred_sessions,
            "rescheduled": rescheduled,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("mark-missed failed for {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/unresolved-missed/{student_id}", summary="Unresolved missed sessions")
async def get_unresolved_missed(student_id: str) -> list[dict[str, Any]]:
    """Return missed sessions that have not been rescheduled."""
    try:
        sb = get_supabase()
        schedules = _get_active_schedules(sb, student_id)
        if not schedules:
            return []

        all_missed = []
        for sch in schedules:
            rows = (
                sb.table("session_instances")
                .select("*")
                .eq("schedule_id", sch["id"])
                .eq("status", "missed")
                .is_("rescheduled_to", "null")
                .execute()
                .data
            )
            all_missed.extend(rows)

        _enrich_sessions(all_missed, schedules)
        return all_missed
    except Exception as exc:
        logger.error("unresolved-missed failed for {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/{missed_id}/reschedule-slots", summary="Available reschedule slots")
async def get_reschedule_slots(
    missed_id: str,
    student_id: str = Query(...),
    days_ahead: int = Query(14, ge=1, le=30),
) -> list[dict[str, Any]]:
    """Find available time slots to reschedule a missed session."""
    try:
        sb = get_supabase()
        missed = sb.table("session_instances").select("*").eq("id", missed_id).execute().data
        if not missed:
            raise HTTPException(status_code=404, detail="Session not found")
        return _find_reschedule_slots(sb, student_id, missed[0], days_ahead)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("reschedule-slots failed: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions/{missed_id}/reschedule", summary="Reschedule a missed session")
async def reschedule_session(missed_id: str, body: RescheduleRequest) -> dict[str, Any]:
    """Create a new session instance from a missed one."""
    try:
        sb = get_supabase()
        missed = sb.table("session_instances").select("*").eq("id", missed_id).execute().data
        if not missed:
            raise HTTPException(status_code=404, detail="Session not found")
        missed = missed[0]
        if missed["status"] not in ("missed", "deferred"):
            raise HTTPException(status_code=400, detail=f"Cannot reschedule session with status '{missed['status']}'")

        # Create new session
        new_session = sb.table("session_instances").insert({
            "schedule_id": missed["schedule_id"],
            "schedule_slot_id": missed["schedule_slot_id"],
            "session_date": body.new_date,
            "start_time": body.new_start,
            "end_time": body.new_end,
            "status": "pending",
            "rescheduled_from": missed_id,
        }).execute().data[0]

        # Update missed session
        sb.table("session_instances").update({
            "status": "rescheduled",
            "rescheduled_to": new_session["id"],
        }).eq("id", missed_id).execute()

        # Audit log
        sb.table("checkin_log").insert({
            "session_instance_id": missed_id,
            "action": "reschedule",
            "performed_by": "parent",
            "details": {"new_date": body.new_date, "new_start": body.new_start},
        }).execute()

        return new_session
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("reschedule failed: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc))
