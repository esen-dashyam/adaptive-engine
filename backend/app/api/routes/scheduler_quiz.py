"""Scheduler — Practice quiz endpoints.

Static quiz (JSON-based):
  GET  /api/v1/scheduler/quiz/questions?subject=Math&grade=5&count=10
  POST /api/v1/scheduler/quiz/score

AI-powered interactive quiz (Gemini + YouTube + Imagen + Canvas):
  POST /api/v1/scheduler/quiz/generate     → full pipeline, returns {quiz_id, html}
  GET  /api/v1/scheduler/quiz/<id>/html    → serve generated HTML
  POST /api/v1/scheduler/quiz/search-videos
  POST /api/v1/scheduler/quiz/generate-images
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(prefix="/scheduler/quiz", tags=["Scheduler — Quiz"])

SAMPLE_DIR = Path(__file__).resolve().parents[4] / "data" / "sample_problems"

# In-memory answer store  quiz_id -> {problems, created_at}
_active_quizzes: dict[str, dict[str, Any]] = {}
_QUIZ_TTL = 1800  # 30 minutes


# ── Helpers ─────────────────────────────────────────────


def _cleanup_expired() -> None:
    """Remove quizzes older than TTL."""
    now = time.time()
    expired = [k for k, v in _active_quizzes.items() if now - v["created_at"] > _QUIZ_TTL]
    for k in expired:
        del _active_quizzes[k]


def _load_sample_problems(subject: str, grade: int) -> list[dict]:
    """Load problems from sample JSON files.

    Tries exact match first (e.g. math_grade5.json), then falls back
    to any file matching the subject.
    """
    subject_lower = subject.lower().replace(" ", "_")

    # Exact match: math_grade5.json
    exact = SAMPLE_DIR / f"{subject_lower}_grade{grade}.json"
    if exact.exists():
        with open(exact) as f:
            data = json.load(f)
        return data.get("problems", [])

    # Partial match by subject keyword
    for path in SAMPLE_DIR.glob("*.json"):
        if subject_lower in path.stem.lower():
            with open(path) as f:
                data = json.load(f)
            return data.get("problems", [])

    # Fallback: try any available file
    json_files = list(SAMPLE_DIR.glob("*.json"))
    if json_files:
        with open(json_files[0]) as f:
            data = json.load(f)
        return data.get("problems", [])

    return []


def _get_subject_title(subject: str, grade: int) -> str:
    """Try to read the formal subject name from the JSON metadata."""
    subject_lower = subject.lower().replace(" ", "_")
    exact = SAMPLE_DIR / f"{subject_lower}_grade{grade}.json"
    if exact.exists():
        with open(exact) as f:
            data = json.load(f)
        return data.get("subject", subject)
    return subject


def _fuzzy_match(student: str, correct: str) -> bool:
    """Lenient string comparison for short answers."""
    s = student.lower().strip()
    c = correct.lower().strip()
    if not s:
        return False
    if s == c:
        return True
    # Check containment
    if s in c or c in s:
        return True
    # Handle "or" separated alternatives  e.g. "3/2 or 1 1/2"
    for variant in c.split(" or "):
        if s == variant.strip():
            return True
    return False


# ── Request Models ──────────────────────────────────────


class ScoreRequest(BaseModel):
    quiz_id: str
    answers: dict[str, str]  # question_id -> student_answer


class GenerateQuizRequest(BaseModel):
    topic: str
    subject: str
    grade: int = Field(ge=1, le=12)
    course_title: str = ""
    difficulty: str = "medium"
    num_questions: int = Field(default=5, ge=3, le=15)
    mode: str = "template"  # "template" | "surprise"
    template: str = "random"  # "card_flip" | "progress_quest" | "classic_test" | "timed_challenge" | "random"


class VideoSearchRequest(BaseModel):
    topic: str
    grade: int = Field(default=5, ge=1, le=12)
    subject: str = ""
    top_n: int = Field(default=3, ge=1, le=5)


class ImageGenRequest(BaseModel):
    topic: str
    subject: str = ""
    grade: int = Field(default=5, ge=1, le=12)
    num_steps: int = Field(default=4, ge=1, le=6)


# ── Endpoints ───────────────────────────────────────────


@router.get("/questions", summary="Get quiz questions (answers stripped)")
async def get_quiz_questions(
    subject: str = Query("Math", description="Subject name"),
    grade: int = Query(5, ge=1, le=12, description="Grade level"),
    count: int = Query(10, ge=1, le=30, description="Max questions"),
):
    """Load sample problems, shuffle, strip answers, and return as JSON."""
    _cleanup_expired()

    problems = _load_sample_problems(subject, grade)
    if not problems:
        raise HTTPException(
            status_code=404,
            detail=f"No problems found for {subject} grade {grade}. "
                   f"Available: math_grade5, science_grade6, english_grade7.",
        )

    random.shuffle(problems)
    problems = problems[:count]

    quiz_id = f"q_{int(time.time() * 1000)}"
    total_points = sum(p.get("points", 1) for p in problems)

    # Build questions (strip answer/explanation) and keep answer key
    questions: list[dict[str, Any]] = []
    answer_key: list[dict[str, Any]] = []
    for i, p in enumerate(problems, 1):
        qid = f"{quiz_id}_{i}"
        questions.append({
            "id": qid,
            "number": i,
            "instruction": p.get("instruction", ""),
            "content": p.get("content", ""),
            "type": p.get("type", "short_answer"),
            "points": p.get("points", 1),
        })
        answer_key.append({**p, "id": qid, "number": i})

    # Store answer key server-side
    _active_quizzes[quiz_id] = {
        "problems": answer_key,
        "created_at": time.time(),
    }

    subject_title = _get_subject_title(subject, grade)

    return {
        "quiz_id": quiz_id,
        "title": f"{subject_title} Practice Quiz — Grade {grade}",
        "subject": subject_title,
        "grade": grade,
        "total_points": total_points,
        "questions": questions,
    }


@router.post("/score", summary="Score submitted quiz answers")
async def score_quiz(body: ScoreRequest):
    """Compare submitted answers against the stored answer key."""
    _cleanup_expired()

    quiz = _active_quizzes.get(body.quiz_id)
    if not quiz:
        raise HTTPException(
            status_code=404,
            detail="Quiz not found or expired. Please start a new quiz.",
        )

    results: list[dict[str, Any]] = []
    earned = 0

    for p in quiz["problems"]:
        student_ans = body.answers.get(p["id"], "").strip()
        correct_ans = str(p.get("answer", ""))
        pts_possible = p.get("points", 1)

        if p.get("type") == "essay":
            # Essays need human review — award full points for MVP
            is_correct: bool | None = None
            pts = pts_possible
        elif p.get("type") == "true_false":
            is_correct = student_ans.lower() == correct_ans.lower()
            pts = pts_possible if is_correct else 0
        else:
            is_correct = _fuzzy_match(student_ans, correct_ans)
            pts = pts_possible if is_correct else 0

        earned += pts
        results.append({
            "id": p["id"],
            "number": p["number"],
            "instruction": p.get("instruction", ""),
            "content": p.get("content", ""),
            "type": p.get("type", "short_answer"),
            "points": pts_possible,
            "student_answer": student_ans,
            "correct_answer": correct_ans,
            "is_correct": is_correct,
            "earned_points": pts,
            "explanation": p.get("explanation", ""),
        })

    total = sum(p.get("points", 1) for p in quiz["problems"])
    pct = round((earned / total) * 100) if total > 0 else 0

    # Clean up used quiz
    del _active_quizzes[body.quiz_id]

    return {
        "quiz_id": body.quiz_id,
        "total_points": total,
        "earned_points": earned,
        "percentage": pct,
        "results": results,
    }


# ── AI-powered Quiz Endpoints ─────────────────────────────

# In-memory HTML store for generated quizzes
_generated_quizzes: dict[str, str] = {}  # quiz_id -> HTML


@router.post("/generate", summary="Generate full AI-powered interactive quiz")
async def generate_interactive_quiz(body: GenerateQuizRequest):
    """Run the quiz pipeline: concept → animation → questions → HTML.

    Videos and images are available via separate endpoints.
    Returns quiz_id and a URL to access the generated HTML.
    """
    from backend.app.services.quiz_pipeline import run_quiz_pipeline

    result = run_quiz_pipeline(
        topic=body.topic,
        subject=body.subject,
        grade=body.grade,
        course_title=body.course_title,
        difficulty=body.difficulty,
        num_questions=body.num_questions,
        mode=body.mode,
        template=body.template,
    )

    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])

    quiz_id = result["quiz_id"]
    html = result.get("quiz_html", "")
    if html:
        _generated_quizzes[quiz_id] = html

    return {
        "quiz_id": quiz_id,
        "html_url": f"/api/v1/scheduler/quiz/{quiz_id}/html",
        "concept": result.get("concept"),
        "num_questions": len(result.get("questions", [])),
    }


@router.get("/{quiz_id}/html", summary="Serve generated quiz HTML",
            response_class=HTMLResponse)
async def get_quiz_html(quiz_id: str):
    """Return the self-contained quiz HTML page for embedding in an iframe."""
    html = _generated_quizzes.get(quiz_id)
    if not html:
        raise HTTPException(status_code=404, detail="Quiz not found or expired")
    return HTMLResponse(content=html)


@router.post("/search-videos", summary="Search YouTube for educational videos")
async def search_videos(body: VideoSearchRequest):
    """Search YouTube for kid-friendly educational videos on a topic."""
    from backend.app.services.youtube_search import search_edu_videos

    results = search_edu_videos(
        topic=body.topic,
        grade=body.grade,
        subject=body.subject,
        top_n=body.top_n,
    )
    return {"videos": results}


@router.post("/generate-images", summary="Generate concept illustrations")
async def generate_images(body: ImageGenRequest):
    """Generate step-by-step educational illustrations using Gemini Imagen."""
    from backend.app.services.image_gen import generate_concept_images

    results = generate_concept_images(
        topic=body.topic,
        subject=body.subject,
        grade=body.grade,
        num_steps=body.num_steps,
    )
    return {
        "images": [
            {"label": label, "image_b64": b64}
            for label, b64 in results
        ]
    }
