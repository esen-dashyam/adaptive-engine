"""Scheduler — Student endpoints (ported from Calendar Scheduler).

GET  /api/v1/scheduler/students          — list all students
GET  /api/v1/scheduler/students/{id}     — get student detail
GET  /api/v1/scheduler/students/{id}/stats — check-in statistics
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
    from datetime import date, timedelta

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
            }

        today = date.today()
        all_sessions = []
        for sch in schedules:
            resp = (
                sb.table("session_instances")
                .select("*")
                .eq("schedule_id", sch["id"])
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
        }
    except Exception as exc:
        logger.error("Failed to get stats for {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))
