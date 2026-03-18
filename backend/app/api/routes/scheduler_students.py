"""Scheduler — Student endpoints (ported from Calendar Scheduler).

GET  /api/v1/scheduler/students                      — list all students
GET  /api/v1/scheduler/students/{id}                 — get student detail
GET  /api/v1/scheduler/students/{id}/stats           — check-in statistics
GET  /api/v1/scheduler/students/{id}/subject-progress — per-subject completion
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger

from backend.app.services.supabase_client import get_supabase

router = APIRouter(prefix="/scheduler/students", tags=["Scheduler — Students"])


@router.get("/", summary="List all students")
async def list_students() -> list[dict[str, Any]]:
    """Return all students ordered by first name."""
    try:
        sb = get_supabase()
        data = sb.table("students").select("*").order("first_name").execute().data
        return data
    except Exception as exc:
        logger.error("Failed to list students: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{student_id}", summary="Get student by ID")
async def get_student(student_id: str) -> dict[str, Any]:
    """Return a single student's details."""
    try:
        sb = get_supabase()
        resp = sb.table("students").select("*").eq("id", student_id).execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail="Student not found")
        return resp.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get student {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{student_id}/stats", summary="Check-in statistics")
async def get_checkin_stats(student_id: str) -> dict[str, Any]:
    """Return check-in completion stats for a student."""
    from datetime import date, timedelta, datetime

    try:
        sb = get_supabase()

        # Get active schedules
        schedules = (
            sb.table("schedules")
            .select("id")
            .eq("student_id", student_id)
            .eq("status", "active")
            .execute()
            .data
        )
        if not schedules:
            return {
                "total": 0, "completed": 0, "missed": 0,
                "pending": 0, "rescheduled": 0,
                "completion_rate": 0.0, "streak": 0,
                "week_completed": 0, "week_total": 0,
                "unique_days_completed": 0, "week_hours": 0.0,
            }

        today = date.today()
        schedule_ids = [sch["id"] for sch in schedules]

        all_sessions = []
        for sid in schedule_ids:
            resp = (
                sb.table("session_instances")
                .select("*")
                .eq("schedule_id", sid)
                .lte("session_date", str(today))
                .order("session_date", desc=True)
                .execute()
            )
            all_sessions.extend(resp.data)

        total = len(all_sessions)
        completed = sum(1 for s in all_sessions if s["status"] == "completed")
        missed = sum(1 for s in all_sessions if s["status"] == "missed")
        rescheduled = sum(1 for s in all_sessions if s["status"] == "rescheduled")
        pending = sum(1 for s in all_sessions if s["status"] == "pending")

        past_total = total - pending
        completion_rate = completed / past_total if past_total > 0 else 0.0

        # Unique school days (distinct dates with ≥1 completed session)
        unique_days_completed = len({
            s["session_date"] for s in all_sessions if s["status"] == "completed"
        })

        # Current streak
        streak = 0
        check_date = today - timedelta(days=1)
        while True:
            day_sessions = [s for s in all_sessions if s["session_date"] == str(check_date)]
            if not day_sessions:
                check_date -= timedelta(days=1)
                if check_date < today - timedelta(days=30):
                    break
                continue
            if all(s["status"] == "completed" for s in day_sessions):
                streak += 1
                check_date -= timedelta(days=1)
            else:
                break

        # This week
        week_start = today - timedelta(days=today.weekday())
        week_sessions = [s for s in all_sessions if s["session_date"] >= str(week_start)]
        week_completed = sum(1 for s in week_sessions if s["status"] == "completed")
        week_total = len(week_sessions)

        # Week hours: sum actual slot durations for completed sessions this week
        week_hours = 0.0
        completed_week_slots = [
            s["schedule_slot_id"] for s in week_sessions
            if s["status"] == "completed" and s.get("schedule_slot_id")
        ]
        if completed_week_slots:
            # Fetch slot durations
            slot_map: dict[str, float] = {}
            for sid in schedule_ids:
                slots = (
                    sb.table("schedule_slots")
                    .select("id, start_time, end_time")
                    .eq("schedule_id", sid)
                    .execute()
                    .data
                )
                for sl in slots:
                    t0 = datetime.strptime(sl["start_time"], "%H:%M:%S")
                    t1 = datetime.strptime(sl["end_time"], "%H:%M:%S")
                    slot_map[sl["id"]] = (t1 - t0).total_seconds() / 3600.0
            for slot_id in completed_week_slots:
                week_hours += slot_map.get(slot_id, 0.75)  # default 45 min

        return {
            "total": total,
            "completed": completed,
            "missed": missed,
            "rescheduled": rescheduled,
            "pending": pending,
            "completion_rate": completion_rate,
            "streak": streak,
            "week_completed": week_completed,
            "week_total": week_total,
            "unique_days_completed": unique_days_completed,
            "week_hours": round(week_hours, 1),
        }
    except Exception as exc:
        logger.error("Failed to get stats for {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{student_id}/subject-progress", summary="Per-subject completion stats")
async def get_subject_progress(student_id: str) -> list[dict[str, Any]]:
    """Aggregate session completion rates grouped by course subject."""
    try:
        sb = get_supabase()
        schedules = (
            sb.table("schedules")
            .select("*, courses(*)")
            .eq("student_id", student_id)
            .eq("status", "active")
            .execute()
            .data
        )

        subject_data: dict[str, dict[str, int]] = {}
        for sch in schedules:
            course = sch.get("courses") or {}
            subj = course.get("subject", "Unknown")
            sessions = (
                sb.table("session_instances")
                .select("id, status")
                .eq("schedule_id", sch["id"])
                .execute()
                .data
            )
            if subj not in subject_data:
                subject_data[subj] = {"total": 0, "completed": 0}
            subject_data[subj]["total"] += len(sessions)
            subject_data[subj]["completed"] += sum(
                1 for s in sessions if s["status"] == "completed"
            )

        return [
            {
                "subject": subj,
                "total_sessions": d["total"],
                "completed_sessions": d["completed"],
                "completion_rate": round((d["completed"] / d["total"]) * 100)
                if d["total"] > 0
                else 0,
            }
            for subj, d in subject_data.items()
        ]
    except Exception as exc:
        logger.error("Failed to get subject progress for {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))
