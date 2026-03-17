"""
AI Tutor Chat endpoint.

POST /chat/tutor
  - Accepts the student's message + conversation history + assessment context
  - Uses a stronger Gemini reasoning model (gemini-2.5-pro by default)
  - Builds a rich system prompt from the student's actual assessment results
  - Returns a grounded, personalised tutoring response
"""

from __future__ import annotations

import re
from typing import Any, Literal

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
    latency_seconds: float = Field(
        default=0.0,
        description="Seconds since the student's previous message. >120 triggers silence detection.",
    )


class PendingAction(BaseModel):
    """
    A BKT update that the tutor wants to record, pending student confirmation.
    The frontend shows a confirmation dialog before calling /chat/confirm_action.
    """
    action_type: Literal["record_answer"] = "record_answer"
    student_id: str
    standard_code: str
    student_answer: str
    is_correct: bool
    confidence: float           # system's confidence that this was intentional
    explanation: str = ""       # tutor's explanation of why it's correct/incorrect


class ConfirmActionRequest(BaseModel):
    action: PendingAction
    confirmed: bool             # True = record it, False = student said it was accidental


# ── Intent detection ─────────────────────────────────────────────────────────

def _detect_intent(message: str, history: list[dict], latency_seconds: float = 0.0) -> dict:
    """
    Classify the student's message before deciding whether to trigger a
    BKT update.  Uses fast heuristics first; falls back to a lightweight
    LLM call only for ambiguous cases.

    Returns:
        {
          "intent":               "answer_attempt" | "question" | "conversation" | "accidental",
          "confidence":           0.0–1.0,
          "metacognitive_signal": "positive" | "negative" | "silence" | None,
        }

    Metacognitive signals (checked independently of intent):
      - positive: "I get it", "I understand", "that makes sense", "got it", "I see", "makes sense"
      - negative: "I don't get it", "I'm lost", "I'm confused", "I don't understand", "no idea"
      - silence:  latency_seconds > 120 (student took > 2 minutes to respond)

    Intent rules (applied in order, first match wins):
      1. Very short / gibberish  → accidental   (confidence 1.0)
      2. Pure MCQ letter (A/B/C/D alone) after tutor asked a question
                                 → answer_attempt (0.90)
      3. Ends with "?"           → question       (0.85)
      4. Contains explicit answer phrasing
                                 → answer_attempt (0.80)
      5. Ambiguous               → LLM classifier (returned confidence)
    """
    stripped = message.strip()

    # ── Metacognitive signal detection (independent of intent) ────────────────
    metacognitive_signal: str | None = None

    if latency_seconds > 120:
        metacognitive_signal = "silence"
    else:
        _positive_patterns = [
            r"\bi (get|understand|see) (it|now|this)\b",
            r"\bthat makes sense\b",
            r"\bgot it\b",
            r"\bi see\b",
            r"\bmakes sense\b",
            r"\bnow i (get|understand|see)\b",
            r"\boh (i see|ok|okay|that makes sense)\b",
        ]
        _negative_patterns = [
            r"\bi (don'?t|do not) (get|understand)\b",
            r"\bi'?m (lost|confused|stuck)\b",
            r"\bno idea\b",
            r"\bdon'?t (get|understand) (it|this)\b",
            r"\bwhat (does|do) (this|that|it) mean\b",
            r"\bi'?m not sure (how|why|what)\b",
            r"\bstill (confused|lost|don'?t understand)\b",
        ]
        if any(re.search(p, stripped, re.IGNORECASE) for p in _positive_patterns):
            metacognitive_signal = "positive"
        elif any(re.search(p, stripped, re.IGNORECASE) for p in _negative_patterns):
            metacognitive_signal = "negative"

    # ── Intent classification ─────────────────────────────────────────────────

    # Rule 1: too short or clearly accidental
    if len(stripped) < 3:
        return {"intent": "accidental", "confidence": 1.0, "metacognitive_signal": metacognitive_signal}
    # All non-alphanumeric / random keyboard mashing
    if len(stripped) <= 8 and not re.search(r"[a-zA-Z]", stripped):
        return {"intent": "accidental", "confidence": 0.95, "metacognitive_signal": metacognitive_signal}

    # Rule 2: single MCQ letter, and the last tutor message contained a question
    if re.fullmatch(r"[A-Da-d]\.?", stripped):
        last_tutor = next(
            (m["content"] for m in reversed(history) if m.get("role") == "assistant"),
            "",
        )
        if "?" in last_tutor or any(
            marker in last_tutor for marker in ["A.", "B.", "C.", "D.", "(A)", "(B)"]
        ):
            return {"intent": "answer_attempt", "confidence": 0.90, "metacognitive_signal": metacognitive_signal}

    # Rule 3: ends with a question mark → asking for help, not answering
    if stripped.endswith("?"):
        return {"intent": "question", "confidence": 0.85, "metacognitive_signal": metacognitive_signal}

    # Rule 4: explicit answer phrasing
    answer_phrases = [
        r"\bmy answer is\b", r"\bi think (?:it'?s?|the answer)\b",
        r"\bthe answer is\b", r"^(?:it'?s?|that'?s?) ",
        r"\b(?:equals?|=)\s*\d",
    ]
    if any(re.search(p, stripped, re.IGNORECASE) for p in answer_phrases):
        return {"intent": "answer_attempt", "confidence": 0.80, "metacognitive_signal": metacognitive_signal}

    # Rule 5: ambiguous — LLM classifier using flash (fast + cheap)
    try:
        from backend.app.agents.vertex_llm import get_llm
        llm = get_llm()
        recent = "\n".join(
            f"{m['role'].upper()}: {m['content'][:200]}"
            for m in history[-4:]
        )
        clf_prompt = f"""Classify this student message in a tutoring chat.

Recent conversation:
{recent}

Student message: "{stripped}"

Respond with ONLY a JSON object:
{{"intent": "answer_attempt"|"question"|"conversation", "confidence": 0.0-1.0}}

- answer_attempt: student is answering a practice question the tutor asked
- question: student is asking for help or explanation
- conversation: casual chat, not answering or asking a specific question"""

        raw = llm.generate_json(clf_prompt)
        if isinstance(raw, dict) and "intent" in raw:
            intent = raw["intent"]
            conf   = float(raw.get("confidence", 0.5))
            if intent in ("answer_attempt", "question", "conversation"):
                return {"intent": intent, "confidence": conf, "metacognitive_signal": metacognitive_signal}
    except Exception:
        pass  # Fall through to default

    # Default: treat as conversation — safe, no state change
    return {"intent": "conversation", "confidence": 0.6, "metacognitive_signal": metacognitive_signal}


# ── Session working memory helpers ────────────────────────────────────────────

async def _load_chat_session(student_id: str) -> dict:
    """
    Load the ChatSession row for a student from Postgres.
    Returns a plain dict; creates a default record if none exists.
    """
    from sqlalchemy import select, insert
    from backend.app.db.engine import _init, _session_factory
    from backend.app.db.models.chat import ChatSession

    _init()
    async with _session_factory() as db:
        row = (await db.execute(
            select(ChatSession).where(ChatSession.student_id == student_id)
        )).scalar_one_or_none()

        if row is None:
            return {
                "student_id":            student_id,
                "current_node_id":       None,
                "current_node_code":     None,
                "pedagogical_strategy":  "socratic",
                "consecutive_struggles": 0,
                "last_message_at":       None,
            }
        return {
            "student_id":            row.student_id,
            "current_node_id":       row.current_node_id,
            "current_node_code":     row.current_node_code,
            "pedagogical_strategy":  row.pedagogical_strategy,
            "consecutive_struggles": row.consecutive_struggles,
            "last_message_at":       row.last_message_at,
        }


async def _save_chat_session(
    student_id:            str,
    current_node_id:       str | None,
    current_node_code:     str | None,
    consecutive_struggles: int,
    pedagogical_strategy:  str,
) -> None:
    """Upsert the ChatSession row for a student."""
    from datetime import datetime, timezone
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from backend.app.db.engine import _init, _session_factory
    from backend.app.db.models.chat import ChatSession

    _init()
    now = datetime.now(timezone.utc)
    async with _session_factory() as db:
        existing = (await db.execute(
            select(ChatSession).where(ChatSession.student_id == student_id)
        )).scalar_one_or_none()

        if existing is None:
            import uuid
            db.add(ChatSession(
                id=uuid.uuid4(),
                student_id=student_id,
                current_node_id=current_node_id,
                current_node_code=current_node_code,
                pedagogical_strategy=pedagogical_strategy,
                consecutive_struggles=consecutive_struggles,
                last_message_at=now,
            ))
        else:
            existing.current_node_id       = current_node_id
            existing.current_node_code     = current_node_code
            existing.pedagogical_strategy  = pedagogical_strategy
            existing.consecutive_struggles = consecutive_struggles
            existing.last_message_at       = now
        await db.commit()


async def _record_failure_chain(
    student_id:          str,
    failed_node_id:      str,
    failed_node_code:    str | None,
    root_prereq_node_id: str | None,
    root_prereq_code:    str | None,
    signal_source:       str,
    hops_to_lca:         int | None,
) -> None:
    """Append one row to the failure_chains audit table."""
    import uuid
    from backend.app.db.engine import _init, _session_factory
    from backend.app.db.models.chat import FailureChain

    _init()
    async with _session_factory() as db:
        db.add(FailureChain(
            id=uuid.uuid4(),
            student_id=student_id,
            failed_node_id=failed_node_id,
            failed_node_code=failed_node_code,
            root_prereq_node_id=root_prereq_node_id,
            root_prereq_code=root_prereq_code,
            signal_source=signal_source,
            hops_to_lca=hops_to_lca,
        ))
        await db.commit()


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

    # ── Filter out orphaned nodes ──────────────────────────────────────────────
    # SKILL_STATE edges sometimes point to nodes with no statementCode/description
    # (test stubs, UUID-only exercise nodes, "node123" dummy entries). Keeping them
    # injects unreadable UUIDs into Gemini's system prompt and corrupts the gap list.
    rows = [
        r for r in rows
        if r.get("code") and r.get("description")
        and not str(r.get("code", "")).startswith("node")
    ]

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

    # Recent = directly observed only (inferred KST nodes have attempts=0)
    recent = [r for r in rows if (r.get("attempts") or 0) > 0][:8]

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
        f"  • {g['code']} [node:{g.get('identifier','')}]: {g['description']} | mastery {round(g['mastery']*100)}%"
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

    student_name = ctx.get("student_name") or student_id
    sections = [
        f"You are an expert, encouraging AI tutor for {student_name}, a {grade_label} {subject_name} student.",
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
        "",
        "=== EXERCISE REFERRAL (IMPORTANT) ===",
        "When the student asks to practice, wants exercises, or when you determine hands-on practice would help,",
        "append this EXACT marker on a NEW LINE at the very end of your response (after all other text):",
        '[[PRACTICE_ACTION: {"node_identifier": "<node_id_from_gap>", "standard_code": "<code>", "concept": "<short concept name>"}]]',
        "Rules for the marker:",
        "- Only emit it when practice exercises are genuinely appropriate (student asks for practice, or is stuck).",
        "- Use the node_identifier from the [node:...] values in the KNOWLEDGE GAPS section above.",
        "- If the student mentions a specific gap, use that gap's node_identifier and code.",
        "- If multiple gaps are relevant, pick the single most important one.",
        "- If no node_identifier is available (standard not in gaps list), omit the marker entirely.",
        "- Do NOT emit the marker for general questions or explanations — only for practice referrals.",
    ]

    return "\n".join(sections)


# ── Endpoint ─────────────────────────────────────────────────────────────────

_STRATEGY_PROMPTS: dict[str, str] = {
    "socratic": (
        "\n=== PEDAGOGICAL STRATEGY: SOCRATIC ===\n"
        "Guide the student to the answer through questions. "
        "Do NOT give the answer directly. Ask one focused leading question at a time."
    ),
    "visual": (
        "\n=== PEDAGOGICAL STRATEGY: VISUAL / CONCRETE EXAMPLES ===\n"
        "The student has struggled with this concept. Shift away from abstract explanation.\n"
        "Use vivid concrete examples, real-world analogies, step-by-step worked examples, "
        "or describe a visual representation (number line, diagram, table). "
        "Show, don't just tell."
    ),
    "cra": (
        "\n=== PEDAGOGICAL STRATEGY: CONCRETE → REPRESENTATIONAL → ABSTRACT (CRA) ===\n"
        "The student is significantly struggling. Use the CRA approach:\n"
        "  1. CONCRETE: Describe the concept using physical objects (counters, blocks, fingers).\n"
        "  2. REPRESENTATIONAL: Draw or describe a picture/diagram of those objects.\n"
        "  3. ABSTRACT: Only then introduce the symbolic/mathematical notation.\n"
        "Start at step 1 and only move forward when the student confirms understanding."
    ),
}


def _choose_strategy(consecutive_struggles: int) -> str:
    if consecutive_struggles >= 4:
        return "cra"
    if consecutive_struggles >= 2:
        return "visual"
    return "socratic"


@router.post("/tutor", summary="Chat with the AI tutor about your assessment results")
async def chat_with_tutor(body: TutorRequest) -> dict[str, Any]:
    """
    Multi-turn AI tutor grounded in the student's full assessment results.

    Pipeline per request:
      1. Intent detection (answer_attempt / question / conversation / accidental)
         + metacognitive signal (positive / negative / silence)
      2. Load session working memory from Postgres (strategy, struggles, current node)
      3. If negative signal or silence → run LCA search → inject bridge prompt
      4. Inject strategy-specific pedagogical instruction (Socratic / Visual / CRA)
      5. LLM call
      6. Parse EVAL_JSON (if answer_attempt) → build pending_action
      7. Update session state + record failure chain if LCA triggered

    The frontend must call POST /chat/confirm_action with confirmed=true
    before any BKT update is written to Neo4j.
    """
    import asyncio
    import json
    from neo4j import GraphDatabase
    from backend.app.agents.vertex_llm import get_llm
    from backend.app.agents.lca_agent import find_lca
    from backend.app.core.settings import settings

    history_dicts = [{"role": m.role, "content": m.content} for m in body.history]
    loop = asyncio.get_event_loop()

    # ── Step 1: intent + metacognitive detection ──────────────────────────────
    intent = await loop.run_in_executor(
        None,
        lambda: _detect_intent(body.message, history_dicts, body.latency_seconds),
    )

    is_answer_attempt  = intent["intent"] == "answer_attempt" and intent["confidence"] >= 0.70
    meta_signal        = intent.get("metacognitive_signal")  # positive | negative | silence | None

    # ── Step 2: load session working memory ───────────────────────────────────
    try:
        session_state = await _load_chat_session(body.student_id)
    except Exception as exc:
        logger.warning(f"Could not load chat session for {body.student_id}: {exc}")
        session_state = {
            "current_node_id": None, "current_node_code": None,
            "pedagogical_strategy": "socratic", "consecutive_struggles": 0,
        }

    consecutive_struggles = session_state["consecutive_struggles"]
    strategy              = session_state["pedagogical_strategy"]
    current_node_id       = session_state["current_node_id"]
    current_node_code     = session_state["current_node_code"]

    # ── Step 3: LCA + bridge prompt (triggered by negative/silence signal) ────
    lca_result:    dict | None = None
    bridge_block:  str         = ""
    signal_source: str | None  = None

    should_find_lca = (
        meta_signal in ("negative", "silence") or consecutive_struggles >= 4
    ) and current_node_id is not None

    if should_find_lca:
        signal_source = meta_signal or "consecutive_struggles"
        try:
            neo4j_driver = GraphDatabase.driver(
                settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
            )
            lca_result = await loop.run_in_executor(
                None,
                lambda: find_lca(
                    neo4j_driver,
                    student_id=body.student_id,
                    node_id=current_node_id,
                    db=settings.neo4j_database,
                ),
            )
            neo4j_driver.close()
        except Exception as exc:
            logger.warning(f"LCA search failed: {exc}")

        if lca_result:
            lca_code = lca_result.get("code", "")
            lca_desc = lca_result.get("description", "")
            lca_hops = lca_result.get("hops", "?")
            bridge_block = (
                f"\n=== BRIDGE INSTRUCTION ===\n"
                f"The student just signaled confusion (signal: {signal_source}).\n"
                f"LCA analysis found they have mastered '{lca_code}: {lca_desc}' "
                f"({lca_hops} concept{'s' if lca_hops != 1 else ''} earlier in the prerequisite chain).\n"
                f"Bridge strategy:\n"
                f"  1. Ask the student to explain '{lca_code}' back to you in their own words.\n"
                f"  2. Once they demonstrate that foundation, show a single step connecting "
                f"'{lca_code}' to the current concept '{current_node_code or 'this concept'}'.\n"
                f"  3. Do NOT jump to the full concept — build one bridge step at a time."
            )

            # Record failure chain async (fire-and-forget)
            try:
                await _record_failure_chain(
                    student_id=body.student_id,
                    failed_node_id=current_node_id,
                    failed_node_code=current_node_code,
                    root_prereq_node_id=lca_result.get("node_id"),
                    root_prereq_code=lca_code,
                    signal_source=signal_source,
                    hops_to_lca=lca_result.get("hops"),
                )
            except Exception as exc:
                logger.warning(f"Could not record failure chain: {exc}")

    # ── Step 4: choose strategy and build system prompt ───────────────────────
    # If the student sent a positive metacognitive signal → reset struggles
    if meta_signal == "positive":
        consecutive_struggles = 0
        strategy = "socratic"

    strategy = _choose_strategy(consecutive_struggles)

    system_prompt = _build_system_prompt(
        student_id=body.student_id,
        grade=body.grade,
        subject=body.subject,
        ctx=body.context,
    )

    # Inject bridge block (if LCA found) before strategy instruction
    if bridge_block:
        system_prompt += bridge_block

    # Inject strategy-specific pedagogical instruction
    system_prompt += _STRATEGY_PROMPTS.get(strategy, _STRATEGY_PROMPTS["socratic"])

    # If we think the student is answering a practice question, ask the LLM
    # to include a structured evaluation block at the end of its response.
    if is_answer_attempt:
        system_prompt += """

=== ANSWER EVALUATION INSTRUCTION ===
The student appears to be answering a practice question you posed.
After your normal tutoring response, append EXACTLY this JSON block on a new line
(no markdown fences, no extra text around it):

EVAL_JSON:{"standard_code":"<code>","is_correct":true/false,"explanation":"<one sentence>"}

Rules:
- standard_code: the standard code of the question you asked (e.g. "3.NF.A.1")
- is_correct: your honest judgment of whether the student's answer is mathematically/linguistically correct
- explanation: brief explanation of why it is or is not correct
- If you did NOT ask a specific practice question, omit the EVAL_JSON line entirely."""

    # ── Step 5: LLM call ──────────────────────────────────────────────────────
    llm = get_llm()

    try:
        raw_response: str = await loop.run_in_executor(
            None,
            lambda: llm.chat(
                system=system_prompt,
                history=history_dicts,
                message=body.message,
                model=CHAT_MODEL,
            ),
        )

        if not raw_response or not raw_response.strip():
            raise ValueError("Empty response from model")

    except Exception as exc:
        logger.error(f"AI tutor chat failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    # ── Step 6: extract evaluation block (if present) ─────────────────────────
    pending_action: dict | None = None
    clean_response = raw_response.strip()
    eval_std_code: str | None = None
    eval_is_correct: bool | None = None

    if is_answer_attempt and "EVAL_JSON:" in raw_response:
        try:
            eval_line = next(
                line for line in raw_response.splitlines()
                if line.strip().startswith("EVAL_JSON:")
            )
            eval_str  = eval_line.strip()[len("EVAL_JSON:"):].strip()
            eval_data = json.loads(eval_str)

            eval_std_code   = eval_data.get("standard_code", "")
            eval_is_correct = bool(eval_data.get("is_correct", False))
            explanation     = eval_data.get("explanation", "")

            if eval_std_code:
                pending_action = PendingAction(
                    student_id=body.student_id,
                    standard_code=eval_std_code,
                    student_answer=body.message.strip(),
                    is_correct=eval_is_correct,
                    confidence=intent["confidence"],
                    explanation=explanation,
                ).model_dump()

            # Strip the EVAL_JSON line from the response shown to the student
            clean_response = "\n".join(
                line for line in raw_response.splitlines()
                if not line.strip().startswith("EVAL_JSON:")
            ).strip()

        except Exception as exc:
            logger.debug(f"Could not parse EVAL_JSON from tutor response: {exc}")

    # ── Step 7: update session working memory ─────────────────────────────────
    # Update current_node_id if we learned which standard was just practiced
    if eval_std_code:
        current_node_code = eval_std_code
        # Resolve Neo4j node_id from standard code (best-effort, don't fail the request)
        try:
            neo4j_driver = GraphDatabase.driver(
                settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
            )
            def _resolve_node():
                with neo4j_driver.session(database=settings.neo4j_database) as s:
                    row = s.run(
                        "MATCH (n:StandardsFrameworkItem {statementCode: $code}) "
                        "WHERE n.normalizedStatementType = 'Standard' "
                        "RETURN n.identifier AS nid LIMIT 1",
                        code=eval_std_code,
                    ).single()
                    return row["nid"] if row else None
            current_node_id = await loop.run_in_executor(None, _resolve_node)
            neo4j_driver.close()
        except Exception:
            pass  # keep previous current_node_id

    # Update struggle count
    if eval_is_correct is True:
        consecutive_struggles = 0
    elif eval_is_correct is False and is_answer_attempt:
        consecutive_struggles += 1

    new_strategy = _choose_strategy(consecutive_struggles)

    try:
        await _save_chat_session(
            student_id=body.student_id,
            current_node_id=current_node_id,
            current_node_code=current_node_code,
            consecutive_struggles=consecutive_struggles,
            pedagogical_strategy=new_strategy,
        )
    except Exception as exc:
        logger.warning(f"Could not save chat session for {body.student_id}: {exc}")

    return {
        "role":                  "assistant",
        "content":               clean_response,
        "model":                 CHAT_MODEL,
        "intent":                intent["intent"],
        "intent_confidence":     round(intent["confidence"], 2),
        "metacognitive_signal":  meta_signal,
        "pedagogical_strategy":  new_strategy,
        "consecutive_struggles": consecutive_struggles,
        "lca_triggered":         lca_result is not None,
        "pending_action":        pending_action,
    }


@router.post("/confirm_action", summary="Confirm or discard a pending BKT update from chat")
async def confirm_chat_action(body: ConfirmActionRequest) -> dict[str, Any]:
    """
    Called by the frontend after the student responds to the confirmation dialog.

    confirmed=True  → writes BKT update to Neo4j (permanent)
    confirmed=False → does nothing (student said it was accidental or wrong)

    The frontend dialog should say something like:
      "I marked that as correct for 3.NF.A.1. Should I record this? [Yes] [No, I mistyped]"
    """
    import asyncio
    from neo4j import GraphDatabase
    from backend.app.core.settings import settings
    from backend.app.agents.evaluation_agent import (
        _bkt_update, _get_student_mastery_and_params, _upsert_mastery
    )

    action = body.action

    if not body.confirmed:
        logger.info(
            f"Chat action discarded (student declined): "
            f"student={action.student_id} std={action.standard_code}"
        )
        return {"recorded": False, "reason": "student_declined"}

    # Look up node identifier from standard code
    driver = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )

    def _execute():
        with driver.session() as session:
            node_row = session.run(
                """
                MATCH (n:StandardsFrameworkItem {statementCode: $code})
                WHERE n.normalizedStatementType = 'Standard'
                RETURN n.identifier AS nid
                LIMIT 1
                """,
                code=action.standard_code,
            ).single()

            if not node_row or not node_row["nid"]:
                return None

            nid = node_row["nid"]

            p_before, p_slip, p_guess, p_transit = _get_student_mastery_and_params(
                session, action.student_id, nid
            )
            p_after = _bkt_update(
                p_before, action.is_correct, p_slip, p_guess, p_transit
            )

            count_row = session.run(
                """
                MATCH (s:Student {id: $sid})-[r:SKILL_STATE]->
                      (n:StandardsFrameworkItem {identifier: $nid})
                RETURN coalesce(r.attempts, 0) AS att,
                       coalesce(r.correct, 0)  AS cor
                """,
                sid=action.student_id, nid=nid,
            ).single()
            attempts = (count_row["att"] if count_row else 0) + 1
            correct  = (count_row["cor"] if count_row else 0) + (1 if action.is_correct else 0)

            _upsert_mastery(session, action.student_id, nid, p_after, attempts, correct)
            return {"nid": nid, "p_before": p_before, "p_after": p_after}

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _execute)
        driver.close()

        if not result:
            return {
                "recorded": False,
                "reason": f"standard_code '{action.standard_code}' not found in knowledge graph",
            }

        logger.info(
            f"Chat BKT confirmed: student={action.student_id} "
            f"std={action.standard_code} correct={action.is_correct} "
            f"p_mastery {result['p_before']:.3f} → {result['p_after']:.3f}"
        )
        return {
            "recorded":      True,
            "student_id":    action.student_id,
            "standard_code": action.standard_code,
            "is_correct":    action.is_correct,
            "p_mastery_before": round(result["p_before"], 3),
            "p_mastery_after":  round(result["p_after"], 3),
        }

    except Exception as exc:
        driver.close()
        logger.error(f"confirm_action failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


class ParentSummaryRequest(BaseModel):
    student_id: str
    child_name: str = ""
    grade: str = "all"
    subject: str = "math"
    mastery_context: dict[str, Any] = Field(default_factory=dict)


@router.post("/parent_summary", summary="Generate a parent-friendly progress summary")
async def parent_summary(body: ParentSummaryRequest) -> dict[str, Any]:
    """
    Translates technical assessment/mastery data into plain-English language
    that a parent or guardian can understand and act on.

    No jargon, no acronyms, no probability numbers — just clear explanations
    of what the child can do, what they need to work on, and how parents can help.
    Returns a structured JSON object + opens a parent-friendly AI chat session.
    """
    import asyncio
    from backend.app.agents.vertex_llm import get_llm

    ctx = body.mastery_context
    child = body.child_name or f"your child ({body.student_id})"
    subject_name = "Mathematics" if body.subject.lower() == "math" else "English Language Arts"
    grade_label  = f"Grade {body.grade.replace('K','')}" if body.grade != "all" else "K-8"

    gaps      = ctx.get("gaps", []) or []
    strengths = ctx.get("strengths", []) or []
    mean_m    = ctx.get("mean_mastery")
    assessed  = ctx.get("total_assessed", 0)

    if not assessed:
        return {
            "has_data": False,
            "headline": f"No assessments completed yet for {child}.",
            "performance_summary": f"{child} hasn't completed any assessments yet. Have them take an adaptive assessment to see a full report here.",
            "overall_status": "not_started",
            "strengths": [],
            "focus_areas": [],
            "next_milestone": "",
            "encouragement": "Every learning journey starts with a first step!",
        }

    gap_lines = []
    for g in gaps[:6]:
        code = g.get("code", "")
        desc = g.get("description", "")
        m    = round((g.get("mastery", 0) or 0) * 100)
        gap_lines.append(f"  - {desc} (standard {code}, mastery {m}%)")

    strength_lines = []
    for s in strengths[:6]:
        desc = s.get("description", "")
        m    = round((s.get("mastery", 0) or 0) * 100)
        strength_lines.append(f"  - {desc} (mastery {m}%)")

    mean_pct = round((mean_m or 0) * 100)

    prompt = f"""You are writing a progress report for a parent or guardian — NOT a teacher or educational expert.

Child: {child}
Grade: {grade_label}
Subject: {subject_name}
Overall mastery across {assessed} assessed skills: {mean_pct}%

What the child is doing WELL:
{chr(10).join(strength_lines) if strength_lines else "  - Not enough data yet"}

Areas that need more practice:
{chr(10).join(gap_lines) if gap_lines else "  - No major gaps detected"}

Write this report as if you are a warm, experienced teacher writing a note home to parents.
Rules:
- Use simple, clear language. No acronyms. No technical terms.
- Convert "mastery probability" to plain language like "is getting comfortable with" or "needs more practice with"
- Make focus areas sound like opportunities, not failures
- Be specific and actionable — tell parents exactly what they can do
- Keep each section short and easy to read

Return a JSON object with these exact keys:
{{
  "has_data": true,
  "overall_status": "doing_great|on_track|needs_support",
  "headline": "<one upbeat sentence about the child's overall progress>",
  "performance_summary": "<2-3 plain English sentences about how they did overall>",
  "strengths": [
    {{"topic": "<plain English topic name>", "detail": "<1 sentence of what they can do well>"}}
  ],
  "focus_areas": [
    {{
      "topic": "<plain English topic name>",
      "plain_explanation": "<1-2 sentences explaining what they struggle with and why it matters>",
      "severity": "minor|moderate|significant",
      "home_activity": "<1 specific, fun activity parents can do at home to help>"
    }}
  ],
  "next_milestone": "<what the child will be ready to do once they improve these areas>",
  "encouragement": "<1 warm, specific sentence celebrating the child's effort or progress>"
}}"""

    llm = get_llm()
    loop = asyncio.get_event_loop()

    try:
        raw = await loop.run_in_executor(None, lambda: llm.generate_json(prompt))
        if isinstance(raw, dict):
            raw["has_data"] = True
            return raw
    except Exception as exc:
        logger.warning(f"Parent summary LLM failed: {exc}")

    # Fallback structured response
    status = "doing_great" if mean_pct >= 75 else "on_track" if mean_pct >= 55 else "needs_support"
    return {
        "has_data": True,
        "overall_status": status,
        "headline": f"{child} is making progress in {grade_label} {subject_name}.",
        "performance_summary": f"{child} has been assessed on {assessed} skills in {subject_name} with an average mastery of {mean_pct}%.",
        "strengths": [{"topic": s.get("description", "")[:50], "detail": "Showing strong understanding."} for s in strengths[:3]],
        "focus_areas": [{"topic": g.get("description", "")[:50], "plain_explanation": "This area needs more practice.", "severity": "moderate", "home_activity": "Practice this concept together for 10 minutes a day."} for g in gaps[:3]],
        "next_milestone": "Continued practice will unlock more advanced concepts.",
        "encouragement": f"Keep up the great work, {child}!",
    }


@router.post("/parent", summary="Parent-focused AI tutor chat")
async def parent_chat(body: "StandaloneTutorRequest") -> dict[str, Any]:
    """
    Multi-turn AI chat for parents — grounded in the child's mastery data
    but explained in parent-friendly language. No jargon.
    """
    import asyncio
    from backend.app.agents.vertex_llm import get_llm

    ctx = body.mastery_context
    child = body.student_id
    subject_name = "Mathematics" if body.subject.lower() == "math" else "English Language Arts"
    grade_label  = f"Grade {body.grade.replace('K','')}" if body.grade != "all" else "K-8"

    gaps_text = "\n".join(
        f"  - {g.get('description','')} (needs more practice)"
        for g in (ctx.get("gaps") or [])[:5]
    )
    strengths_text = "\n".join(
        f"  - {s.get('description','')} (doing well)"
        for s in (ctx.get("strengths") or [])[:5]
    )

    system = f"""You are a warm, knowledgeable educational advisor speaking with the parent or guardian of {child}, a {grade_label} {subject_name} student.

Your job is to help parents understand their child's learning and give them practical, actionable advice.

Child's current status:
- Strengths: {strengths_text or "Insufficient data — encourage them to complete an assessment"}
- Areas to focus on: {gaps_text or "No major gaps identified"}

Rules:
- Use simple, parent-friendly language. No educational jargon, no acronyms.
- Be warm, encouraging, and solution-focused.
- When parents ask what they can do at home, give specific, fun, practical activities.
- When explaining a learning gap, explain WHY it matters in real-world terms.
- Keep answers focused and conversational — this is a chat, not a lecture.
- If asked about severity, be honest but reassuring."""

    history = [{"role": m.role, "content": m.content} for m in body.history]
    llm  = get_llm()
    loop = asyncio.get_event_loop()

    try:
        response = await loop.run_in_executor(
            None,
            lambda: llm.chat(system=system, history=history, message=body.message, model=CHAT_MODEL),
        )
        return {"role": "assistant", "content": response.strip(), "model": CHAT_MODEL}
    except Exception as exc:
        logger.error(f"Parent chat failed: {exc}")
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

    The response may include an `action` field when the AI decides the student
    should practice a specific concept. The frontend renders a "Start Exercises"
    card that navigates to /exercises with the relevant parameters.
    """
    import asyncio
    import json
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
        raw_response: str = await loop.run_in_executor(
            None,
            lambda: llm.chat(
                system=system_prompt,
                history=history,
                message=body.message,
                model=CHAT_MODEL,
            ),
        )
        if not raw_response or not raw_response.strip():
            raise ValueError("Empty response from model")
    except Exception as exc:
        logger.error(f"Standalone tutor failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    # ── Parse optional [[PRACTICE_ACTION: {...}]] marker ─────────────────────
    action: dict | None = None
    clean_response = raw_response.strip()

    action_match = re.search(
        r'\[\[PRACTICE_ACTION:\s*(\{[^}]+\})\]\]',
        clean_response,
        re.DOTALL,
    )
    if action_match:
        try:
            action_data = json.loads(action_match.group(1))
            if action_data.get("node_identifier") and action_data.get("standard_code"):
                action = {
                    "type":            "start_exercises",
                    "node_identifier": action_data["node_identifier"],
                    "standard_code":   action_data["standard_code"],
                    "concept":         action_data.get("concept", action_data["standard_code"]),
                }
                logger.info(
                    f"Standalone tutor: PRACTICE_ACTION for "
                    f"{action['standard_code']} ({body.student_id})"
                )
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning(f"Failed to parse PRACTICE_ACTION marker: {exc}")
        # Strip the marker from the visible message
        clean_response = clean_response[:action_match.start()].strip()

    return {
        "role":    "assistant",
        "content": clean_response,
        "action":  action,
        "model":   CHAT_MODEL,
    }
