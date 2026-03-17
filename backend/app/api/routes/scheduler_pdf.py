"""Scheduler — PDF generation endpoints.

GET  /api/v1/scheduler/pdf/schedule-report/{student_id}
GET  /api/v1/scheduler/pdf/semester-calendar/{student_id}?months=3
GET  /api/v1/scheduler/pdf/course-overview/{course_id}
GET  /api/v1/scheduler/pdf/practice-problems?subject=Math&grade=5&count=10

Practice problems are generated dynamically via Gemini AI.
Static sample JSON files are used as fallback when Gemini is unavailable.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from loguru import logger

from backend.app.core.settings import settings
from backend.app.services.supabase_client import get_supabase
from backend.app.pdf.templates import (
    build_schedule_report_pdf,
    build_semester_calendar_pdf,
    build_course_overview_pdf,
    build_practice_problems_pdf,
)

router = APIRouter(prefix="/scheduler/pdf", tags=["Scheduler — PDF"])

SAMPLE_DIR = Path(__file__).resolve().parents[4] / "data" / "sample_problems"


# ── Gemini problem generation ─────────────────────────

_PRACTICE_SYSTEM_PROMPT = """You are Evlin's educational content generator. You create pedagogically sound
practice problems for homeschool students.

When generating practice problems:
1. Consider the subject, grade level, and difficulty.
2. Create problems that are age-appropriate and aligned with the curriculum.
3. Include a mix of problem types (short answer, word problems, true/false, essay).
4. Provide clear instructions for each problem.
5. Include detailed answer keys with explanations.
6. For Math: include computation, word problems, and conceptual questions.
7. For Science: include factual recall, application, and analysis questions.
8. For English/Reading/Writing: include grammar, comprehension, and writing prompts.
9. For History: include timeline, cause-effect, and critical thinking questions.
10. For Art/Music: include terminology, analysis, and creative questions.
11. For foreign languages: include translation, conjugation, and vocabulary.

Return problems as a JSON object with "title" and "problems" array."""


def _generate_problems_with_gemini(
    subject: str, grade: int, count: int, difficulty: str = "standard"
) -> dict | None:
    """Use Gemini to dynamically generate practice problems.

    Returns dict with "title" and "problems", or None on failure.
    """
    if not settings.gemini_api_key:
        logger.info("No GEMINI_API_KEY configured, skipping AI generation")
        return None

    try:
        from google import genai
        from google.genai import types

        prompt = f"""Generate exactly {count} practice problems for:
- Subject: {subject}
- Grade Level: {grade}
- Difficulty: {difficulty}

Return ONLY a JSON object with this structure:
{{
  "title": "descriptive title for this practice set",
  "problems": [
    {{
      "number": 1,
      "instruction": "what the student should do",
      "content": "the question content (math expression, passage, etc.)",
      "type": "short_answer",
      "points": 2,
      "answer": "the correct answer",
      "explanation": "why that's correct"
    }}
  ]
}}

Rules:
- Mix question types: short_answer, word_problem, true_false, essay
- Points: 1 for true_false, 2-3 for short_answer, 3-4 for word_problem, 4-5 for essay
- Make problems progressively harder
- All content in English
- Ensure variety and creativity
- For "content" field: use it for the actual question material (e.g. math expression, sentence to analyze). Leave empty string if the instruction already contains the full question.
- Return ONLY valid JSON, no markdown formatting."""

        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_PRACTICE_SYSTEM_PROMPT,
                temperature=0.8,
                response_mime_type="application/json",
            ),
        )
        text = resp.text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0]
        data = json.loads(text)

        problems = data.get("problems", [])
        if not problems:
            logger.warning("Gemini returned empty problems list")
            return None

        logger.info(
            "Gemini generated %d problems for %s Grade %d", len(problems), subject, grade
        )
        return data
    except Exception as exc:
        logger.warning("Gemini problem generation failed: %s", exc)
        return None


# ── Helpers ─────────────────────────────────────────────

def _get_student(student_id: str) -> dict[str, Any]:
    """Fetch a student by ID from Supabase."""
    sb = get_supabase()
    resp = sb.table("students").select("*").eq("id", student_id).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Student not found")
    return resp.data[0]


def _get_schedules(student_id: str) -> list[dict[str, Any]]:
    """Fetch active schedules with nested course data."""
    sb = get_supabase()
    return (
        sb.table("schedules")
        .select("*, courses(*)")
        .eq("student_id", student_id)
        .eq("status", "active")
        .order("start_date")
        .execute()
        .data
    )


def _get_slots(schedule_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch all schedule slots for given schedule IDs, joined with course info."""
    if not schedule_ids:
        return []
    sb = get_supabase()
    all_slots: list[dict] = []
    for sid in schedule_ids:
        slots = (
            sb.table("schedule_slots")
            .select("*, schedules!inner(course_id, courses(code, title, subject))")
            .eq("schedule_id", sid)
            .execute()
            .data
        )
        for sl in slots:
            # Flatten course info onto slot for template compatibility
            sched = sl.pop("schedules", {}) or {}
            course = sched.get("courses") or {}
            sl["course_code"] = course.get("code", "")
            sl["course_title"] = course.get("title", "")
            sl["subject"] = course.get("subject", "")
            all_slots.append(sl)
    return all_slots


def _pdf_response(pdf_bytes: bytes, filename: str) -> Response:
    """Wrap PDF bytes in a FastAPI Response with correct headers."""
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )


# ── Endpoints ───────────────────────────────────────────

@router.get("/schedule-report/{student_id}", summary="Schedule report PDF")
async def schedule_report_pdf(student_id: str):
    """Generate a student schedule report PDF."""
    try:
        student = _get_student(student_id)
        schedules = _get_schedules(student_id)
        schedule_ids = [s["id"] for s in schedules]
        slots = _get_slots(schedule_ids)

        pdf_bytes = build_schedule_report_pdf(student, schedules, slots)
        name = f"{student['first_name']}_{student['last_name']}_Schedule.pdf"
        return _pdf_response(pdf_bytes, name)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to generate schedule report: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/semester-calendar/{student_id}", summary="Semester calendar PDF")
async def semester_calendar_pdf(
    student_id: str,
    months: int = Query(3, ge=1, le=12, description="Number of months"),
):
    """Generate a multi-month semester calendar PDF (landscape)."""
    try:
        student = _get_student(student_id)
        schedules = _get_schedules(student_id)
        schedule_ids = [s["id"] for s in schedules]
        slots = _get_slots(schedule_ids)

        pdf_bytes = build_semester_calendar_pdf(
            student, schedules, slots, num_months=months,
        )
        name = f"{student['first_name']}_{student['last_name']}_Calendar_{months}mo.pdf"
        return _pdf_response(pdf_bytes, name)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to generate semester calendar: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/course-overview/{course_id}", summary="Course overview PDF")
async def course_overview_pdf(course_id: str):
    """Generate a single course overview PDF."""
    try:
        sb = get_supabase()
        resp = sb.table("courses").select("*").eq("id", course_id).execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail="Course not found")
        course = resp.data[0]

        pdf_bytes = build_course_overview_pdf(course)
        name = f"{course['code']}_Overview.pdf"
        return _pdf_response(pdf_bytes, name)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to generate course overview: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/practice-problems", summary="Practice problems PDF (GET)")
async def practice_problems_pdf_get(
    subject: str = Query("Math", description="Subject name"),
    grade: int = Query(5, ge=1, le=12, description="Grade level"),
    count: int = Query(10, ge=1, le=30, description="Number of problems"),
    include_answers: bool = Query(True, description="Include answer key"),
):
    """Generate practice problems PDF from sample data (GET version)."""
    return _generate_practice_pdf(subject, grade, count, include_answers)


@router.post("/practice-problems", summary="Practice problems PDF (POST)")
async def practice_problems_pdf_post(
    body: dict[str, Any] | None = None,
):
    """Generate practice problems PDF from sample data (POST version)."""
    body = body or {}
    subject = body.get("subject", "Math")
    grade = body.get("grade", 5)
    count = body.get("count", 10)
    include_answers = body.get("include_answers", True)
    return _generate_practice_pdf(subject, grade, count, include_answers)


def _generate_practice_pdf(
    subject: str, grade: int, count: int, include_answers: bool
) -> Response:
    """Generate practice problems PDF.

    Strategy:
    1. Try Gemini AI for dynamic, subject-specific generation
    2. Fall back to static sample JSON files if Gemini is unavailable
    """
    try:
        title = f"{subject} Practice Problems — Grade {grade}"
        problems: list[dict] = []

        # 1. Try Gemini AI generation first
        gemini_data = _generate_problems_with_gemini(subject, grade, count)
        if gemini_data:
            title = gemini_data.get("title", title)
            problems = gemini_data.get("problems", [])[:count]

        # 2. Fall back to static sample files
        if not problems:
            logger.info("Falling back to static sample data for %s", subject)
            problems = _load_sample_problems(subject, grade)
            if not problems:
                raise HTTPException(
                    status_code=404,
                    detail=f"No problems could be generated for {subject} grade {grade}. "
                           f"Please check that GEMINI_API_KEY is configured.",
                )
            random.shuffle(problems)
            problems = problems[:count]

        pdf_bytes = build_practice_problems_pdf(
            title=title,
            subject=subject,
            grade=grade,
            problems=problems,
            include_answers=include_answers,
        )
        name = f"{subject}_Grade{grade}_Practice.pdf"
        return _pdf_response(pdf_bytes, name)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to generate practice problems: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc))


_SUBJECT_ALIASES: dict[str, list[str]] = {
    "math": ["mathematics", "algebra", "geometry", "calculus"],
    "english": ["reading", "writing", "language_arts", "ela", "literature"],
    "science": ["biology", "chemistry", "physics", "earth_science"],
    "history": ["social_studies", "civics", "geography", "government"],
    "spanish": ["spanish", "espanol"],
    "art": ["visual_arts", "fine_arts", "drawing", "painting"],
    "music": ["band", "choir", "orchestra", "music_theory"],
}


def _resolve_subject_key(subject: str) -> str:
    """Map a course subject name to a sample-file key via aliases."""
    subject_lower = subject.lower().replace(" ", "_")
    # Direct match
    for key, aliases in _SUBJECT_ALIASES.items():
        if subject_lower == key or subject_lower in aliases:
            return key
    # Partial match
    for key, aliases in _SUBJECT_ALIASES.items():
        all_terms = [key, *aliases]
        if any(subject_lower in term or term in subject_lower for term in all_terms):
            return key
    return subject_lower


def _load_sample_problems(subject: str, grade: int) -> list[dict]:
    """Load problems from sample JSON files.

    Uses alias resolution to match subjects, e.g. "Reading" → english.
    Tries exact match first (e.g. math_grade5.json), then partial match.
    """
    subject_key = _resolve_subject_key(subject)

    # Exact match: math_grade5.json
    exact = SAMPLE_DIR / f"{subject_key}_grade{grade}.json"
    if exact.exists():
        with open(exact) as f:
            data = json.load(f)
        return data.get("problems", [])

    # Partial match by resolved key
    for path in SAMPLE_DIR.glob("*.json"):
        if subject_key in path.stem.lower():
            with open(path) as f:
                data = json.load(f)
            return data.get("problems", [])

    # Also try original subject name
    subject_lower = subject.lower().replace(" ", "_")
    for path in SAMPLE_DIR.glob("*.json"):
        if subject_lower in path.stem.lower():
            with open(path) as f:
                data = json.load(f)
            return data.get("problems", [])

    # Last resort: try any available file
    json_files = list(SAMPLE_DIR.glob("*.json"))
    if json_files:
        with open(json_files[0]) as f:
            data = json.load(f)
        return data.get("problems", [])

    return []
