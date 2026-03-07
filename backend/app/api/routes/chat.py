"""
AI Tutor Chat endpoint.

POST /chat/tutor
  - Accepts the student's message + conversation history + assessment context
  - Uses a stronger Gemini reasoning model (gemini-2.5-pro by default)
  - Builds a rich system prompt from the student's actual assessment results
  - Returns a grounded, personalised tutoring response
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(prefix="/chat", tags=["Chat"])

CHAT_MODEL = "gemini-2.5-pro"


# ── Request / Response models ────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str    # "user" | "assistant"
    content: str


class TutorRequest(BaseModel):
    student_id: str = "default"
    grade: str = "K5"
    subject: str = "math"
    message: str
    history: list[ChatMessage] = Field(default_factory=list)
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Full EvalResult payload from /assessment/evaluate",
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_system_prompt(
    student_id: str,
    grade: str,
    subject: str,
    ctx: dict[str, Any],
) -> str:
    grade_label  = f"Grade {grade.replace('K', '')}"
    subject_name = "Mathematics" if subject.lower() == "math" else "English Language Arts"

    score_pct    = round((ctx.get("score", 0) or 0) * 100)
    theta        = float(ctx.get("theta", 0.0) or 0.0)
    grade_status = ctx.get("grade_status", "") or ""
    correct      = ctx.get("correct", 0)
    total        = ctx.get("total", 0)

    theta_desc = (
        "advanced"                        if theta >= 1.5  else
        "above average"                   if theta >= 0.5  else
        "on grade level"                  if theta >= -0.5 else
        "slightly below grade level"      if theta >= -1.5 else
        "significantly below grade level"
    )

    status_str = grade_status.replace("_", " ").title() if grade_status else "N/A"

    # Gaps section
    gaps = ctx.get("gaps", []) or []
    gap_lines = []
    for g in gaps[:8]:
        code    = g.get("code", "")
        desc    = g.get("description", "")
        mastery = round((g.get("mastery_prob", 0) or 0) * 100)
        blocked = " [HARD-BLOCKED — must fix this first]" if g.get("hard_blocked") else ""
        pri     = g.get("priority", "")
        gap_lines.append(f"  • {code}: {desc} | mastery {mastery}% | {pri} priority{blocked}")

    # Misconceptions section
    misconceptions = ctx.get("misconceptions", []) or []
    misc_lines = []
    for m in misconceptions[:5]:
        code = m.get("standard_code", "")
        misc = m.get("misconception", "")
        if misc:
            misc_lines.append(f"  • {code}: {misc}")

    # Recommendations section
    recs = ctx.get("recommendations", []) or []
    rec_lines = []
    for r in recs[:5]:
        code    = r.get("standard_code", "")
        desc    = r.get("description", "")
        why     = r.get("why_now", "")
        how     = r.get("how_to_start", "")
        mins    = r.get("estimated_minutes", "")
        minutes = f" (~{mins} min)" if mins else ""
        rec_lines.append(
            f"  • {code}: {desc}{minutes}"
            + (f"\n    Why now: {why}" if why else "")
            + (f"\n    How to start: {how}" if how else "")
        )

    # Results summary (wrong answers)
    results = ctx.get("results", []) or []
    wrong_lines = []
    for r in results:
        if not r.get("is_correct"):
            q   = r.get("question", "")[:100]
            sa  = r.get("student_answer", "")
            ca  = r.get("correct_answer", "")
            std = r.get("standard_code", "")
            wrong_lines.append(f"  • [{std}] Q: {q}… | Student: {sa} | Correct: {ca}")

    sections = [
        f"You are an expert, encouraging AI tutor for {student_id}, a {grade_label} {subject_name} student.",
        "You have just analysed this student's adaptive assessment results and know exactly where they stand.",
        "",
        "=== STUDENT PROFILE ===",
        f"Grade: {grade_label} | Subject: {subject_name} | Student: {student_id}",
        f"Score: {score_pct}% ({correct}/{total} correct) | Status: {status_str}",
        f"Rasch Ability θ = {theta:+.2f} ({theta_desc})",
    ]

    if gap_lines:
        sections += ["", f"=== KNOWLEDGE GAPS ({len(gaps)}) ==="] + gap_lines
    else:
        sections += ["", "=== KNOWLEDGE GAPS ===", "  No significant gaps detected — great work!"]

    if misc_lines:
        sections += ["", "=== DETECTED MISCONCEPTIONS ==="] + misc_lines

    if rec_lines:
        sections += ["", "=== NEXT LEARNING STEPS (ZPD Frontier) ==="] + rec_lines

    if wrong_lines:
        sections += ["", "=== QUESTIONS ANSWERED INCORRECTLY ==="] + wrong_lines[:6]

    # LLM metacognitive outputs (from judge_mastery + llm_recommendation_decider)
    mastery_verdicts  = ctx.get("mastery_verdicts", {}) or {}
    session_narrative = ctx.get("session_narrative", "") or ""
    focus_concept     = ctx.get("focus_concept", "") or ""

    if mastery_verdicts:
        verdict_lines = []
        for code, v in list(mastery_verdicts.items())[:8]:
            verdict    = v.get("verdict", "unknown")
            action     = v.get("next_action", "")
            confidence = v.get("confidence", 0.5)
            reasoning  = v.get("reasoning", "")
            verdict_lines.append(
                f"  • {code}: verdict={verdict} | action={action} "
                f"| confidence={confidence:.0%}\n    {reasoning}"
            )
        sections += ["", "=== LLM MASTERY VERDICTS ==="] + verdict_lines

    if session_narrative:
        sections += ["", "=== SESSION NARRATIVE ===", f"  {session_narrative}"]

    if focus_concept:
        sections += ["", f"=== RECOMMENDED FOCUS CONCEPT: {focus_concept} ==="]

    sections += [
        "",
        "=== YOUR TUTORING GUIDELINES ===",
        f"- Be warm, encouraging, and specific — always reference the exact standard code and concept.",
        f"- Use age-appropriate language for {grade_label} students.",
        "- When explaining a gap, say WHY the concept matters and give a concrete real-world example.",
        "- When explaining a misconception, gently clarify what the student likely misunderstood.",
        "- When giving next steps, be concrete: suggest 1-2 specific practice activities.",
        "- Keep responses focused and concise — avoid unnecessary filler.",
        "- You may use **bold**, bullet points, and numbered lists for clarity.",
        "- If asked a math or ELA question directly, work through it step-by-step.",
        "- Stay grounded in this student's actual results — no generic advice.",
        "- If the student seems discouraged, acknowledge their effort and reframe mistakes as learning.",
        "- After a student completes practice exercises, proactively tell them if they seem ready for "
        "  a formal re-assessment (hint: suggest it when their answers show consistent improvement).",
        f"- The system's recommended focus concept is '{focus_concept}' — steer the conversation here when natural." if focus_concept else "",
    ]

    return "\n".join(sections)


# ── Context loader ───────────────────────────────────────────────────────────

@router.get("/context/{student_id}", summary="Load a student's live mastery profile from Neo4j")
async def get_student_context(
    student_id: str,
    grade: str = "all",
    subject: str = "all",
) -> dict:
    """
    Query Neo4j SKILL_STATE edges for a student and return a structured
    mastery profile: top gaps, strengths, overall stats, and recently updated nodes.
    Used by the standalone AI Tutor to ground its responses in real mastery data.
    """
    import asyncio
    from neo4j import GraphDatabase
    from backend.app.core.settings import settings

    def _fetch():
        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        try:
            with driver.session() as session:
                grade_filter = "" if grade == "all" else "AND n.gradeLevelList IS NOT NULL AND ANY(g IN n.gradeLevelList WHERE g = $grade_num)"
                subj_filter  = "" if subject == "all" else "AND toLower(n.academicSubject) CONTAINS toLower($subj)"
                grade_num    = grade.replace("K", "") if grade != "all" else ""

                result = session.run(
                    f"""
                    MATCH (s:Student {{id: $sid}})-[r:SKILL_STATE]->(n:StandardsFrameworkItem)
                    WHERE r.p_mastery IS NOT NULL
                    {grade_filter}
                    {subj_filter}
                    RETURN n.identifier        AS identifier,
                           n.statementCode     AS code,
                           n.description       AS description,
                           n.gradeLevelList     AS grades,
                           n.academicSubject    AS subject,
                           r.p_mastery         AS mastery,
                           r.attempts          AS attempts,
                           r.correct           AS correct,
                           r.last_updated      AS last_updated
                    ORDER BY r.last_updated DESC
                    LIMIT 200
                    """,
                    sid=student_id,
                    grade_num=grade_num,
                    subj=subject,
                )
                rows = [r.data() for r in result]

                # Count total standards in graph for coverage
                total_r = session.run(
                    "MATCH (n:StandardsFrameworkItem) RETURN count(n) AS total"
                ).single()
                total_in_kg = total_r["total"] if total_r else 0

                return rows, total_in_kg
        finally:
            driver.close()

    loop = asyncio.get_event_loop()
    try:
        rows, total_in_kg = await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        logger.warning(f"Context load failed: {exc}")
        rows, total_in_kg = [], 0

    if not rows:
        return {
            "student_id":    student_id,
            "has_history":   False,
            "total_assessed": 0,
            "total_in_kg":   total_in_kg,
            "mean_mastery":  None,
            "gaps":          [],
            "strengths":     [],
            "recent":        [],
            "grade_breakdown": {},
        }

    masteries = [float(r["mastery"] or 0) for r in rows]
    mean_m    = round(sum(masteries) / len(masteries), 3)

    gaps = sorted(
        [r for r in rows if (r["mastery"] or 0) < 0.55],
        key=lambda x: x["mastery"],
    )[:10]

    strengths = sorted(
        [r for r in rows if (r["mastery"] or 0) >= 0.70],
        key=lambda x: -x["mastery"],
    )[:8]

    recent = rows[:8]

    grade_breakdown: dict[str, dict] = {}
    for r in rows:
        gl = (r.get("grades") or ["?"])[0]
        if gl not in grade_breakdown:
            grade_breakdown[gl] = {"count": 0, "total_mastery": 0.0}
        grade_breakdown[gl]["count"] += 1
        grade_breakdown[gl]["total_mastery"] += float(r["mastery"] or 0)

    grade_summary = {
        g: {
            "count": v["count"],
            "mean_mastery": round(v["total_mastery"] / v["count"], 3),
        }
        for g, v in grade_breakdown.items()
    }

    def _fmt(rows_subset):
        return [
            {
                "identifier": r["identifier"],
                "code":        r["code"] or r["identifier"],
                "description": r["description"] or "",
                "grade":       (r.get("grades") or ["?"])[0],
                "subject":     r["subject"] or "",
                "mastery":     round(float(r["mastery"] or 0), 3),
                "attempts":    r["attempts"] or 0,
            }
            for r in rows_subset
        ]

    return {
        "student_id":     student_id,
        "has_history":    True,
        "total_assessed": len(rows),
        "total_in_kg":    total_in_kg,
        "mean_mastery":   mean_m,
        "gaps":           _fmt(gaps),
        "strengths":      _fmt(strengths),
        "recent":         _fmt(recent),
        "grade_breakdown": grade_summary,
    }


def _build_system_from_mastery(
    student_id: str,
    grade: str,
    subject: str,
    ctx: dict,
) -> str:
    """Build a system prompt from raw Neo4j mastery context (standalone tutor)."""
    grade_label  = f"Grade {grade.replace('K', '')}" if grade != "all" else "K1–K8"
    subject_name = (
        "Mathematics"            if subject.lower() == "math"    else
        "English Language Arts"  if subject.lower() == "english" else
        "all subjects"
    )

    mean_m   = ctx.get("mean_mastery")
    assessed = ctx.get("total_assessed", 0)
    total_kg = ctx.get("total_in_kg", 0)
    coverage = f"{assessed}/{total_kg}" if total_kg else str(assessed)

    gaps      = ctx.get("gaps", [])
    strengths = ctx.get("strengths", [])
    recent    = ctx.get("recent", [])
    grade_bk  = ctx.get("grade_breakdown", {})

    gap_lines = [
        f"  • {g['code']}: {g['description']} | mastery {round(g['mastery']*100)}%"
        for g in gaps[:8]
    ]
    strength_lines = [
        f"  • {s['code']}: {s['description']} | mastery {round(s['mastery']*100)}%"
        for s in strengths[:6]
    ]
    grade_lines = [
        f"  • Grade {g}: {v['count']} standards assessed, avg mastery {round(v['mean_mastery']*100)}%"
        for g, v in sorted(grade_bk.items())
    ]

    sections = [
        f"You are an expert, encouraging AI tutor for {student_id}, a {grade_label} {subject_name} student.",
        "You have direct access to this student's live mastery data from the knowledge graph.",
        f"Standards assessed: {coverage} | Mean mastery: {round((mean_m or 0)*100)}%",
        "",
    ]

    if gap_lines:
        sections += [f"=== KNOWLEDGE GAPS ({len(gaps)} standards below 55% mastery) ==="] + gap_lines + [""]
    else:
        sections += ["=== No gaps detected — all assessed standards above mastery threshold. ===", ""]

    if strength_lines:
        sections += ["=== STRENGTHS (above 70% mastery) ==="] + strength_lines + [""]

    if grade_lines:
        sections += ["=== MASTERY BY GRADE ==="] + grade_lines + [""]

    if recent:
        recent_lines = [f"  • {r['code']}: {r['description']} ({round(r['mastery']*100)}%)" for r in recent[:5]]
        sections += ["=== RECENTLY ASSESSED STANDARDS ==="] + recent_lines + [""]

    sections += [
        "=== TUTORING GUIDELINES ===",
        f"- Be warm, specific, and encouraging. Always reference exact standard codes when relevant.",
        f"- Use age-appropriate language for {grade_label} students.",
        "- When explaining a concept, connect it to real-world examples the student can relate to.",
        "- When a student asks about a gap, explain the concept clearly, then give 1-2 concrete practice suggestions.",
        "- When answering a math/ELA question, work through it step by step, showing your reasoning.",
        "- Keep responses focused: no unnecessary filler, but be thorough when explaining concepts.",
        "- You may use **bold**, bullet points, and numbered lists for clarity.",
        "- If the student has no history yet, encourage them to take an assessment to unlock personalised guidance.",
        "- If asked about a standard not in the student's history, still explain it clearly and say it hasn't been assessed yet.",
    ]

    return "\n".join(sections)


# ── Endpoint ─────────────────────────────────────────────────────────────────

@router.post("/tutor", summary="Chat with the AI tutor about your assessment results")
async def chat_with_tutor(body: TutorRequest) -> dict[str, Any]:
    """
    Multi-turn AI tutor grounded in the student's full assessment results.
    Uses Gemini 2.5 Pro for stronger reasoning and pedagogical quality,
    falling back to the configured gemini_model if 2.5-pro is unavailable.
    """
    import asyncio
    from backend.app.agents.vertex_llm import get_llm

    system_prompt = _build_system_prompt(
        student_id=body.student_id,
        grade=body.grade,
        subject=body.subject,
        ctx=body.context,
    )

    history = [{"role": m.role, "content": m.content} for m in body.history]

    llm = get_llm()
    loop = asyncio.get_event_loop()

    try:
        response: str = await loop.run_in_executor(
            None,
            lambda: llm.chat(
                system=system_prompt,
                history=history,
                message=body.message,
                model=CHAT_MODEL,
            ),
        )

        if not response or not response.strip():
            raise ValueError("Empty response from model")

        return {
            "role": "assistant",
            "content": response.strip(),
            "model": CHAT_MODEL,
        }

    except Exception as exc:
        logger.error(f"AI tutor chat failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


class StandaloneTutorRequest(BaseModel):
    student_id: str = "default"
    grade: str = "all"
    subject: str = "all"
    message: str
    history: list[ChatMessage] = Field(default_factory=list)
    mastery_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Output of GET /chat/context/{student_id}",
    )


@router.post("/standalone", summary="Standalone AI tutor grounded in live Neo4j mastery")
async def standalone_tutor(body: StandaloneTutorRequest) -> dict[str, Any]:
    """
    Free-form AI tutor backed by the student's real-time mastery profile from Neo4j.
    Uses Gemini 2.5 Pro. Does not require a completed assessment — works any time.
    """
    import asyncio
    from backend.app.agents.vertex_llm import get_llm

    system_prompt = _build_system_from_mastery(
        student_id=body.student_id,
        grade=body.grade,
        subject=body.subject,
        ctx=body.mastery_context,
    )

    history = [{"role": m.role, "content": m.content} for m in body.history]
    llm  = get_llm()
    loop = asyncio.get_event_loop()

    try:
        response: str = await loop.run_in_executor(
            None,
            lambda: llm.chat(
                system=system_prompt,
                history=history,
                message=body.message,
                model=CHAT_MODEL,
            ),
        )
        if not response or not response.strip():
            raise ValueError("Empty response from model")
        return {
            "role":    "assistant",
            "content": response.strip(),
            "model":   CHAT_MODEL,
        }
    except Exception as exc:
        logger.error(f"Standalone tutor failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
