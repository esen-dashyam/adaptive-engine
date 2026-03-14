"""Scheduler — Course endpoints (ported from Calendar Scheduler).

GET  /api/v1/scheduler/courses                       — list all courses
GET  /api/v1/scheduler/courses/{id}                  — get course detail
GET  /api/v1/scheduler/courses/search                — search/filter courses
GET  /api/v1/scheduler/students/{id}/courses         — student's enrolled courses
GET  /api/v1/scheduler/students/{id}/course-progress — per-course completion stats
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from backend.app.services.supabase_client import get_supabase

router = APIRouter(prefix="/scheduler", tags=["Scheduler — Courses"])


@router.get("/courses", summary="List all courses")
async def list_courses(
    active_only: bool = Query(True, description="Only return active courses"),
) -> list[dict[str, Any]]:
    """Return all courses ordered by subject then code."""
    try:
        sb = get_supabase()
        q = sb.table("courses").select("*")
        if active_only:
            q = q.eq("is_active", True)
        data = q.order("subject").order("code").execute().data
        return data
    except Exception as exc:
        logger.error("Failed to list courses: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/courses/search", summary="Search courses")
async def search_courses(
    subject: str | None = Query(None, description="Filter by subject"),
    grade_level: int | None = Query(None, ge=1, le=12, description="Filter by grade"),
    difficulty: str | None = Query(None, description="easy, standard, advanced"),
) -> list[dict[str, Any]]:
    """Search courses with optional filters."""
    try:
        sb = get_supabase()
        q = sb.table("courses").select("*").eq("is_active", True)
        if subject:
            q = q.eq("subject", subject)
        if grade_level:
            q = q.lte("grade_level_min", grade_level).gte("grade_level_max", grade_level)
        if difficulty:
            q = q.eq("difficulty", difficulty)
        data = q.order("code").execute().data
        return data
    except Exception as exc:
        logger.error("Failed to search courses: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/courses/{course_id}", summary="Get course by ID")
async def get_course(course_id: str) -> dict[str, Any]:
    """Return a single course's details."""
    try:
        sb = get_supabase()
        resp = sb.table("courses").select("*").eq("id", course_id).execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail="Course not found")
        return resp.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get course {}: {}", course_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/students/{student_id}/courses", summary="Student's enrolled courses")
async def get_student_courses(student_id: str) -> list[dict[str, Any]]:
    """Return courses a student is enrolled in (active schedules with course join)."""
    try:
        sb = get_supabase()
        data = (
            sb.table("schedules")
            .select("*, courses(*)")
            .eq("student_id", student_id)
            .eq("status", "active")
            .order("start_date")
            .execute()
            .data
        )
        return data
    except Exception as exc:
        logger.error("Failed to get courses for {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/students/{student_id}/course-progress", summary="Per-course completion stats")
async def get_course_progress(student_id: str) -> list[dict[str, Any]]:
    """Compute progress for each enrolled course from session_instances."""
    try:
        sb = get_supabase()
        schedules = (
            sb.table("schedules")
            .select("*, courses(*)")
            .eq("student_id", student_id)
            .eq("status", "active")
            .order("start_date")
            .execute()
            .data
        )

        result: list[dict[str, Any]] = []
        for sch in schedules:
            course = sch.get("courses") or {}
            all_sessions = (
                sb.table("session_instances")
                .select("id, status")
                .eq("schedule_id", sch["id"])
                .execute()
                .data
            )
            total = len(all_sessions)
            completed = sum(1 for s in all_sessions if s["status"] == "completed")
            progress = round((completed / total) * 100) if total > 0 else 0

            result.append(
                {
                    "schedule_id": sch["id"],
                    "course_id": course.get("id"),
                    "course_code": course.get("code"),
                    "title": course.get("title"),
                    "subject": course.get("subject"),
                    "duration_weeks": course.get("duration_weeks"),
                    "total_sessions": total,
                    "completed_sessions": completed,
                    "progress": progress,
                    "start_date": sch.get("start_date"),
                    "end_date": sch.get("end_date"),
                }
            )
        return result
    except Exception as exc:
        logger.error("Failed to get course progress for {}: {}", student_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))
