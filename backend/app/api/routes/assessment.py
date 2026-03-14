"""
K1-K8 Assessment API routes.

Endpoints:
  POST /assessment/generate  — generate adaptive assessment (IRT-ranked via new agents)
  POST /assessment/evaluate  — score answers, Rasch update, KST gap analysis, remediation
  GET  /assessment/nodes     — preview which standards would be selected
  GET  /assessment/grades    — list grades, subjects, states
  GET  /assessment/student/{id}/performance  — BKT performance report
  GET  /assessment/student/{id}/trajectory   — K1-K8 grade trajectory
"""

from __future__ import annotations

import asyncio
import datetime
from functools import partial
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(prefix="/assessment", tags=["Assessment"])


# ── Request / Response models ────────────────────────────────────────────────

class AssessmentRequest(BaseModel):
    grade: str = Field(..., description="Grade level: K1-K8 or 1-8")
    subject: str = Field(..., description="Subject: math or english")
    student_id: str = Field(default="default")
    num_questions: int = Field(default=15, ge=5, le=30)
    state: str = Field(
        default="Multi-State",
        description="US state abbreviation (TX, CA, NY …) or 'Multi-State' for Common Core",
    )
    # Retry mode: if provided, only ask questions on these specific standard codes
    pinned_standard_codes: list[str] = Field(default_factory=list)


class ParentFeedbackRequest(BaseModel):
    student_id: str
    assessment_id: str = ""
    accurate: str = Field(..., description="'yes' | 'somewhat' | 'no'")
    parent_notes: str = ""


class AnswerSubmission(BaseModel):
    assessment_id: str
    student_id: str = "default"
    grade: str = Field(default="K5")
    subject: str = Field(default="math")
    state: str = Field(default="Multi-State")
    # Elastic Stopping: cumulative count from prior batches (0 on first call)
    total_answered_prior: int = Field(default=0)
    # Confusion signal: student typed "I don't get this" without finishing
    confusion_signal: bool = Field(default=False)
    confusion_chat: str = Field(default="")
    answers: list[dict[str, Any]] = Field(
        ...,
        description=(
            "List of {question_id, student_answer, node_ref, category, "
            "standard_code, standard_description, is_correct}"
        ),
    )


class ExerciseResultRequest(BaseModel):
    """Single exercise completion — updates BKT and KG edge weights in real-time."""
    student_id: str
    standard_code: str
    node_identifier: str        # Neo4j identifier for the StandardsFrameworkItem
    exercise_id: str            # the question id from the remediation plan
    question_text: str = ""
    correct: bool
    selected_answer: str = ""
    correct_answer: str = ""
    dok_level: int = 2
    question_type: str = "practice"
    difficulty_beta: float = 0.0
    session_id: str | None = None


class ExerciseChatRequest(BaseModel):
    """
    Live chat message sent by the student DURING a remediation exercise.
    The backend computes φ, triggers recursive pivot if φ < -0.3,
    and returns a bridge instruction + optional pivot exercise.
    """
    student_id: str
    node_identifier: str        # Neo4j identifier of the current exercise's target standard
    standard_code: str = ""
    concept: str = ""           # human-readable concept name, e.g. "regrouping"
    exercise_text: str = ""     # full text of the exercise shown to the student
    nanopoint_tag: str = ""     # [NanoPoint_ID: ... | Standard: ... | ...] from remediation plan
    chat_message: str           # what the student typed
    answer: str = ""            # student's current answer (may be empty if mid-exercise)
    correct: bool | None = None # None if not yet graded
    time_ms: float = 0.0
    beta: float = 0.0           # question difficulty logit


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _load_student_theta(student_id: str) -> float:
    """
    Load a student's current Rasch θ from Neo4j SKILL_STATE relationships.
    Returns 0.0 (average ability) for new students with no history.
    θ is estimated from the mean of existing BKT mastery probabilities,
    then mapped to a logit scale: logit(p) = log(p / (1-p)).
    """
    import math
    try:
        from neo4j import GraphDatabase
        from backend.app.core.settings import settings

        def _fetch(tx):
            result = tx.run(
                """
                MATCH (s:Student {id: $sid})-[r:SKILL_STATE]->()
                WHERE r.p_mastery IS NOT NULL
                RETURN collect(r.p_mastery) AS masteries
                """,
                sid=student_id,
            )
            row = result.single()
            return row["masteries"] if row else []

        loop = asyncio.get_event_loop()
        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        masteries = await loop.run_in_executor(
            None,
            lambda: driver.execute_query(
                "MATCH (s:Student {id:$sid})-[r:SKILL_STATE]->() WHERE r.p_mastery IS NOT NULL "
                "RETURN collect(r.p_mastery) AS masteries",
                sid=student_id,
            ).records[0]["masteries"] if True else []
        )
        driver.close()

        if not masteries:
            return 0.0
        mean_p = sum(masteries) / len(masteries)
        mean_p = max(0.01, min(0.99, mean_p))
        theta  = math.log(mean_p / (1.0 - mean_p))
        return round(max(-4.0, min(4.0, theta)), 3)
    except Exception:
        return 0.0


def _normalize_grade_token(grade: str) -> str:
    token = str(grade or "").strip().upper()
    if not token:
        return "1"
    if token == "K":
        return "k"
    if token.startswith("K") and token[1:].isdigit():
        return token[1:]
    if token.isdigit():
        return token
    return token.replace("K", "") or "1"


async def _load_beta_anchors(answer_rows: list[dict[str, Any]], fallback_grade: str) -> dict[str, float]:
    from neo4j import GraphDatabase

    from backend.app.agents.rasch import grade_to_difficulty
    from backend.app.core.settings import settings

    fallback = _normalize_grade_token(fallback_grade)
    node_ids = list({a.get("node_ref", "") for a in answer_rows if a.get("node_ref")})
    grade_map: dict[str, str] = {}

    if node_ids:
        loop = asyncio.get_event_loop()

        def _fetch() -> dict[str, str]:
            driver = GraphDatabase.driver(
                settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
            )
            try:
                with driver.session() as neo:
                    rows = neo.run(
                        """
                        UNWIND $ids AS nid
                        MATCH (n:StandardsFrameworkItem {identifier: nid})
                        RETURN n.identifier AS nid, n.gradeLevelList AS grade_levels
                        """,
                        ids=node_ids,
                    )
                    return {
                        r["nid"]: _normalize_grade_token((r["grade_levels"] or [fallback])[0])
                        for r in rows
                        if r["nid"]
                    }
            finally:
                driver.close()

        grade_map = await loop.run_in_executor(None, _fetch)

    anchors: dict[str, float] = {}
    for a in answer_rows:
        qid = a.get("question_id", "")
        if not qid:
            continue
        node_grade = grade_map.get(a.get("node_ref", ""), fallback)
        anchors[qid] = grade_to_difficulty(
            node_grade,
            int(a.get("dok_level", 2) or 2),
            a.get("category", "target"),
        )
    return anchors


async def _bubble_mastery_forward(student_id: str, node_identifier: str, p_mastery: float) -> dict[str, Any]:
    if p_mastery < 0.65 or not node_identifier:
        return {"updated": 0, "nodes": []}

    from datetime import datetime as _dt
    from neo4j import GraphDatabase

    from backend.app.core.settings import settings

    loop = asyncio.get_event_loop()

    def _run() -> dict[str, Any]:
        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        try:
            with driver.session() as neo:
                rows = neo.run(
                    """
                    MATCH p=(src:StandardsFrameworkItem {identifier: $nid})
                          -[rels:BUILDS_TOWARDS|PRECEDES*1..2]->
                          (dst:StandardsFrameworkItem)
                    WHERE dst.identifier <> $nid
                    OPTIONAL MATCH (s:Student {id: $sid})-[sk:SKILL_STATE]->(dst)
                    RETURN dst.identifier AS nid,
                           [rel IN relationships(p) | coalesce(rel.conceptual_weight, rel.understanding_strength, 0.7)] AS weights,
                           length(p) AS hops,
                           coalesce(sk.p_mastery, 0.3) AS current_mastery
                    """,
                    sid=student_id,
                    nid=node_identifier,
                )

                updates: list[dict[str, Any]] = []
                for row in rows:
                    path_weight = 1.0
                    for weight in row["weights"] or []:
                        path_weight *= float(weight or 0.7)
                    hops = max(int(row["hops"] or 1), 1)
                    candidate = min(0.95, float(p_mastery) * path_weight * (0.9 ** (hops - 1)))
                    current_mastery = float(row["current_mastery"] or 0.3)
                    if candidate <= current_mastery + 0.02:
                        continue
                    updates.append({
                        "nid": row["nid"],
                        "mastery": round(candidate, 4),
                        "source": node_identifier,
                    })

                if not updates:
                    return {"updated": 0, "nodes": []}

                neo.run(
                    """
                    UNWIND $updates AS item
                    MERGE (s:Student {id: $sid})
                    MATCH (n:StandardsFrameworkItem {identifier: item.nid})
                    MERGE (s)-[r:SKILL_STATE]->(n)
                    SET r.p_mastery = CASE
                            WHEN coalesce(r.p_mastery, 0.0) < item.mastery THEN item.mastery
                            ELSE r.p_mastery
                        END,
                        r.inferred = CASE
                            WHEN coalesce(r.attempts, 0) = 0 THEN true
                            ELSE coalesce(r.inferred, false)
                        END,
                        r.source = CASE
                            WHEN coalesce(r.attempts, 0) = 0 THEN 'exercise_bubble'
                            ELSE coalesce(r.source, 'exercise_bubble')
                        END,
                        r.last_propagated_from = item.source,
                        r.last_updated = $now
                    """,
                    sid=student_id,
                    updates=updates,
                    now=_dt.utcnow().isoformat(),
                )
                return {"updated": len(updates), "nodes": [u["nid"] for u in updates]}
        finally:
            driver.close()

    return await loop.run_in_executor(None, _run)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/generate", summary="Generate an adaptive K1-K8 assessment")
async def generate_assessment(body: AssessmentRequest) -> dict[str, Any]:
    """
    Generate a K1-K8 adaptive assessment using the new IRT-aware agents:
    - Rasch 1PL IRT to rank standards by Fisher Information at student's θ
    - GraphRAG context (prerequisite chains, sibling standards, existing stems)
    - Vertex AI (ADC) / Gemini Flash generates real curriculum questions
    """
    import uuid
    from backend.app.agents.orchestrator import get_phase_a
    from backend.app.agent.state import AssessmentState

    try:
        logger.info("═" * 60)
        logger.info(f"  ▶ PHASE A START │ student={body.student_id}  grade={body.grade}  subject={body.subject}  state={body.state}")
        logger.info("═" * 60)
        # Load student's current θ from Neo4j (or default 0.0 for new students)
        theta = await _load_student_theta(body.student_id)

        initial_state = AssessmentState(
            student_id=body.student_id,
            grade=body.grade,
            subject=body.subject,
            state_jurisdiction=body.state,
            theta=theta,
            phase="start",
            pinned_standard_codes=body.pinned_standard_codes,
        )

        loop = asyncio.get_event_loop()
        phase_a = get_phase_a()
        final_state: AssessmentState = await loop.run_in_executor(
            None, lambda: phase_a.invoke(initial_state)
        )

        if isinstance(final_state, dict):
            final_state = AssessmentState(**final_state)

        questions = final_state.questions
        if not questions:
            err = final_state.error or "No questions generated"
            if "GEMINI_API_KEY" in err or "No LLM backend" in err:
                raise HTTPException(
                    status_code=503,
                    detail={"gemini_required": True, "message": err,
                            "setup_url": "https://aistudio.google.com/app/apikey"},
                )
            raise HTTPException(status_code=500, detail=err)

        assessment_id = str(uuid.uuid4())
        prereq_count  = sum(1 for q in questions if q.get("category") == "prerequisite")

        return {
            "assessment_id":     assessment_id,
            "student_id":        body.student_id,
            "grade":             body.grade,
            "subject":           body.subject,
            "state":             body.state,
            "framework":         final_state.framework,
            "estimated_minutes": 25,
            "num_questions":     len(questions),
            "prerequisite_count": prereq_count,
            "target_count":      len(questions) - prereq_count,
            "theta":             round(final_state.theta, 3),
            "question_difficulties": final_state.question_difficulties,
            "core_standards": [
                {"identifier": n.get("identifier",""), "code": n.get("code",""),
                 "description": n.get("description","")}
                for n in final_state.all_nodes
            ],
            "questions": questions,
        }
    except HTTPException:
        raise
    except RuntimeError as exc:
        msg = str(exc)
        if "GEMINI_API_KEY" in msg or "No LLM backend" in msg:
            raise HTTPException(
                status_code=503,
                detail={"gemini_required": True, "message": msg,
                        "setup_url": "https://aistudio.google.com/app/apikey"},
            )
        raise HTTPException(status_code=500, detail=msg)
    except Exception as exc:
        logger.error(f"Assessment generation failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/evaluate", summary="Score answers, Rasch update, KST gaps, remediation")
async def evaluate_assessment(body: AnswerSubmission) -> dict[str, Any]:
    """
    Full Phase B evaluation pipeline:
    - Score answers + Rasch 1PL IRT θ update
    - LLM misconception detection (Vertex AI)
    - BKT mastery update in Neo4j
    - KST knowledge state propagation across full KG
    - Gap identification + ranking by downstream impact
    - Vertex AI targeted remediation exercises per gap
    - Personalised learning path recommendations (ZPD frontier)
    """
    try:
        from backend.app.agents.orchestrator import get_phase_b
        from backend.app.agent.state import AssessmentState

        logger.info("═" * 60)
        logger.info(f"  ▶ PHASE B START │ student={body.student_id}  grade={body.grade}  answers={len(body.answers)}")
        logger.info("═" * 60)
        theta = await _load_student_theta(body.student_id)
        beta_anchors = await _load_beta_anchors(body.answers, body.grade)

        # Reconstruct question list from submitted answers for Phase B.
        # Supports open-ended (rubric/answer_key) and legacy MC (options/answer).
        questions = [
            {
                "id":            a.get("question_id", ""),
                "question":      a.get("question", ""),
                "rubric":        a.get("rubric", ""),
                "answer_key":    a.get("answer_key", a.get("correct_answer", a.get("answer", ""))),
                "type":          a.get("question_type", a.get("type", "open_ended")),
                "options":       a.get("options", {}),
                "answer":        a.get("correct_answer", a.get("answer", "")),
                "category":      a.get("category", "target"),
                "dok_level":     a.get("dok_level", 2),
                "standard_code": a.get("standard_code", ""),
                "node_ref":      a.get("node_ref", ""),
                "beta":          beta_anchors.get(a.get("question_id", ""), a.get("beta", 0.0)),
            }
            for a in body.answers
        ]
        submitted = [
            {
                "question_id":      a.get("question_id", ""),
                # open-ended: student_response; MC: selected_answer
                "student_response": a.get("student_response") or a.get("student_answer") or "",
                "selected_answer":  a.get("selected_answer") or a.get("student_answer") or a.get("student_response") or "",
                "time_ms":          float(a.get("time_ms", 0.0) or 0.0),
                "chat_message":     a.get("chat_message", "") or "",
            }
            for a in body.answers
        ]

        initial_state = AssessmentState(
            student_id=body.student_id,
            grade=body.grade,
            subject=body.subject,
            state_jurisdiction=body.state,
            theta=theta,
            questions=questions,
            submitted_answers=submitted,
            phase="evaluate",
            total_answered=body.total_answered_prior,
            confusion_signal=body.confusion_signal,
            confusion_chat=body.confusion_chat,
        )

        loop    = asyncio.get_event_loop()
        phase_b = get_phase_b()
        final: AssessmentState = await loop.run_in_executor(
            None, lambda: phase_b.invoke(initial_state)
        )

        if isinstance(final, dict):
            final = AssessmentState(**final)

        prereq_results = [r for r in final.results if r.get("category") == "prerequisite"]
        target_results  = [r for r in final.results if r.get("category") == "target"]
        prereq_score = sum(1 for r in prereq_results if r["is_correct"]) / max(len(prereq_results), 1)
        target_score  = sum(1 for r in target_results  if r["is_correct"]) / max(len(target_results), 1)

        # ── Store last assessment snapshot on Student node for parent review ──
        try:
            import json as _json
            from neo4j import GraphDatabase as _GDB
            from backend.app.core.settings import settings as _cfg
            _failed_codes = [
                r.get("standard_code", "") for r in final.results
                if not r.get("is_correct") and r.get("standard_code")
            ]
            _snapshot = {
                "assessment_id":  body.assessment_id,
                "score":          round(final.score, 3),
                "total":          len(final.results),
                "correct":        sum(1 for r in final.results if r["is_correct"]),
                "grade":          body.grade,
                "subject":        body.subject,
                "failed_standard_codes": _failed_codes,
                "failed_standards": [
                    {"code": r.get("standard_code",""), "question": r.get("question","")[:120]}
                    for r in final.results if not r.get("is_correct")
                ],
                "timestamp":      datetime.datetime.utcnow().isoformat(),
            }
            _drv = _GDB.driver(_cfg.neo4j_uri, auth=(_cfg.neo4j_user, _cfg.neo4j_password))
            _drv.execute_query(
                "MERGE (s:Student {id:$sid}) SET s.last_assessment = $snap, s.last_assessment_at = $ts",
                sid=body.student_id,
                snap=_json.dumps(_snapshot),
                ts=_snapshot["timestamp"],
            )
            _drv.close()
        except Exception as _snap_err:
            logger.warning(f"Could not store assessment snapshot: {_snap_err}")

        if   final.score >= 0.85: grade_status = "above"
        elif final.score >= 0.70: grade_status = "at"
        elif final.score >= 0.50: grade_status = "approaching"
        else:                     grade_status = "below"

        return {
            "assessment_id":     body.assessment_id,
            "student_id":        body.student_id,
            "score":             round(final.score, 3),
            "correct":           sum(1 for r in final.results if r["is_correct"]),
            "total":             len(final.results),
            "grade_status":      grade_status,
            "prerequisite_score": round(prereq_score, 3),
            "target_score":      round(target_score, 3),
            # Rasch IRT
            "theta":             round(final.theta, 3),
            "theta_history":     final.theta_history,
            # Gaps & remediation
            "gap_count":         len(final.gaps),
            "gaps":              final.gaps,
            "hard_blocked_count": len(final.hard_blocked_nodes),
            "gap_exercises":     final.remediation_plan,
            # Misconceptions
            "misconceptions":    final.misconceptions,
            # Recommendations (ZPD frontier)
            "recommendations":   getattr(final, "recommendations", []),
            # BKT updates
            "bkt_updates":       [
                {"node": k, "mastery": v} for k, v in final.mastery_updates.items()
            ],
            # LLM metacognitive outputs
            "mastery_verdicts":  getattr(final, "mastery_verdicts", {}),
            "session_narrative": getattr(final, "llm_decisions", {}).get("session_narrative", ""),
            "focus_concept":     getattr(final, "llm_decisions", {}).get("focus_concept", ""),
            # Full results
            "results":           final.results,
            # Elastic Stopping — if True, frontend shows additional_questions next
            "needs_more_questions": final.needs_more_questions,
            "additional_questions": final.additional_questions,
            "total_answered":       final.total_answered,
            "se":                   round(final.se, 3),
            # Confusion / LCA outputs
            "lca_safety_nets":      final.lca_safety_nets,
            # Cognitive Load Pruning
            "newly_blocked_nodes":  final.newly_blocked_nodes,
        }
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Assessment evaluation failed: {type(exc).__name__}: {exc}\n{tb}")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}" or "Evaluation pipeline error")


@router.post("/exercise_complete", summary="Submit a single exercise result — updates BKT and KG edge weights")
async def submit_exercise_result(body: ExerciseResultRequest) -> dict[str, Any]:
    """
    Called each time a student submits an answer to a remediation exercise.

    Runs two things immediately:
    1. BKT mastery update for this student-standard pair (stored on SKILL_STATE edge)
    2. EMA edge weight update for BUILDS_TOWARDS edges connected to this standard
       (the knowledge graph learns which prerequisite relationships actually predict
       whether students can master the target concept)

    This makes the system learn from every exercise answer, not just from
    formal assessments.
    """
    import uuid as _uuid
    from datetime import datetime
    from neo4j import GraphDatabase
    from backend.app.core.settings import settings
    from backend.app.student.bkt_fitter import (
        DEFAULT_P_INIT     as P_INIT,
        DEFAULT_P_SLIP     as P_SLIP,
        DEFAULT_P_GUESS    as P_GUESS,
        EXERCISE_P_TRANSIT as P_LEARN,   # higher learning rate for targeted practice
    )

    EMA_LR  = 0.05

    def _bkt_update(p: float, correct: bool) -> float:
        if correct:
            denom = p * (1 - P_SLIP) + (1 - p) * P_GUESS
            posterior = (p * (1 - P_SLIP)) / (denom + 1e-9)
        else:
            denom = p * P_SLIP + (1 - p) * (1 - P_GUESS)
            posterior = (p * P_SLIP) / (denom + 1e-9)
        # Safety floor: cannot drop more than 0.15 in one cycle (mirrors evaluation_agent)
        updated = posterior + (1 - posterior) * P_LEARN
        return min(1.0, max(p - 0.15, updated))

    session_id = body.session_id or str(_uuid.uuid4())
    now_str    = datetime.utcnow().isoformat()

    try:
        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )

        loop = asyncio.get_event_loop()

        def _run_update():
            with driver.session() as neo:
                # ── 1. Read current BKT state ─────────────────────────────────
                row = neo.run(
                    """
                    MATCH (s:Student {id: $sid})-[r:SKILL_STATE]->(n:StandardsFrameworkItem {identifier: $nid})
                    RETURN coalesce(r.p_mastery, $init) AS p_mastery,
                           coalesce(r.attempts, 0)      AS attempts,
                           coalesce(r.correct, 0)       AS correct
                    """,
                    sid=body.student_id, nid=body.node_identifier, init=P_INIT,
                ).single()

                p_before = float(row["p_mastery"]) if row else P_INIT
                attempts  = int(row["attempts"]) + 1 if row else 1
                correct_count = int(row["correct"]) + (1 if body.correct else 0) if row else (1 if body.correct else 0)

                # ── 2. BKT update ────────────────────────────────────────────
                p_after = _bkt_update(p_before, body.correct)

                # ── 3. Write updated SKILL_STATE ─────────────────────────────
                neo.run(
                    """
                    MERGE (s:Student {id: $sid})
                    MERGE (n:StandardsFrameworkItem {identifier: $nid})
                    MERGE (s)-[r:SKILL_STATE]->(n)
                    SET r.p_mastery   = $pm,
                        r.attempts    = $att,
                        r.correct     = $cor,
                        r.last_updated = $now
                    """,
                    sid=body.student_id, nid=body.node_identifier,
                    pm=p_after, att=attempts, cor=correct_count, now=now_str,
                )

                # ── 4. Persist exercise attempt ──────────────────────────────
                neo.run(
                    """
                    MERGE (q:GeneratedQuestion {id: $eid})
                    SET   q.question_text   = $qtext,
                          q.standard_code   = $code,
                          q.dok_level       = $dok,
                          q.question_type   = $qtype,
                          q.difficulty_beta = $beta,
                          q.created_at      = coalesce(q.created_at, $now)
                    WITH q
                    MATCH (n:StandardsFrameworkItem {identifier: $nid})
                    MERGE (q)-[:TESTS]->(n)
                    WITH q
                    MERGE (s:Student {id: $sid})
                    MERGE (s)-[a:ATTEMPTED {session_id: $sess, question_id: $eid}]->(q)
                    SET   a.correct         = $correct,
                          a.selected_answer = $selected,
                          a.correct_answer  = $correct_ans,
                          a.timestamp       = $now
                    """,
                    eid=body.exercise_id,
                    qtext=body.question_text,
                    code=body.standard_code,
                    dok=body.dok_level,
                    qtype=body.question_type,
                    beta=body.difficulty_beta,
                    now=now_str,
                    nid=body.node_identifier,
                    sid=body.student_id,
                    sess=session_id,
                    correct=body.correct,
                    selected=body.selected_answer,
                    correct_ans=body.correct_answer,
                )

                # ── 5. EMA edge weight update ────────────────────────────────
                # For each BUILDS_TOWARDS edge pointing TO this standard:
                # if prereq is highly mastered but student failed → increase weight
                # if prereq mastered and student passed → weight confirmed
                edge_result = neo.run(
                    """
                    MATCH (pre:StandardsFrameworkItem)-[r:BUILDS_TOWARDS]->(n:StandardsFrameworkItem {identifier: $nid})
                    OPTIONAL MATCH (s:Student {id: $sid})-[sk:SKILL_STATE]->(pre)
                    RETURN pre.identifier AS pre_id,
                           coalesce(r.conceptual_weight, 0.7) AS weight,
                           coalesce(sk.p_mastery, 0.1)        AS pre_mastery
                    LIMIT 10
                    """,
                    nid=body.node_identifier, sid=body.student_id,
                )
                prereq_edges = [rec.data() for rec in edge_result]

                updates = 0
                for edge in prereq_edges:
                    pre_mastery = float(edge["pre_mastery"])
                    old_weight  = float(edge["weight"])
                    pre_id      = edge["pre_id"]

                    prereq_mastered = pre_mastery >= 0.65
                    if prereq_mastered and body.correct:
                        signal = 1.0   # edge was predictive: knowing prereq → can do target
                    elif prereq_mastered and not body.correct:
                        signal = 0.5   # knew prereq but still failed: edge is important (raise weight)
                    elif not prereq_mastered and not body.correct:
                        signal = 0.7   # both weak: neutral
                    else:
                        signal = 0.3   # didn't know prereq but got target right: edge less critical

                    new_weight = round(
                        max(0.3, min(1.0, old_weight * (1 - EMA_LR) + signal * EMA_LR)), 4
                    )
                    if abs(new_weight - old_weight) >= 0.001:
                        neo.run(
                            """
                            MATCH (a:StandardsFrameworkItem {identifier: $pre_id})
                                  -[r:BUILDS_TOWARDS]->(b:StandardsFrameworkItem {identifier: $nid})
                            SET r.conceptual_weight    = $w,
                                r.observation_count    = coalesce(r.observation_count, 0) + 1,
                                r.last_weight_update   = $now
                            """,
                            pre_id=pre_id, nid=body.node_identifier,
                            w=new_weight, now=now_str,
                        )
                        updates += 1

                return {
                    "p_mastery_before": round(p_before, 3),
                    "p_mastery_after":  round(p_after, 3),
                    "attempts":         attempts,
                    "correct_total":    correct_count,
                    "edge_updates":     updates,
                }

        result = await loop.run_in_executor(None, _run_update)

        # ── Unblock downstream nodes when blocker is mastered ──────────────
        # If this exercise brought the blocking node above the mastery threshold,
        # clear TEMPORARY_BLOCK relationships for all nodes it was blocking.
        MASTERY_UNBLOCK_THRESHOLD = 0.65
        bubble_result = {"updated": 0, "nodes": []}
        if result["p_mastery_after"] >= MASTERY_UNBLOCK_THRESHOLD:
            def _clear_blocks():
                with driver.session() as neo:
                    cleared = neo.run(
                        """
                        MATCH (s:Student {id: $sid})-[b:TEMPORARY_BLOCK]->(n)
                        WHERE b.blocked_by = $nid
                        DELETE b
                        RETURN count(b) AS cleared
                        """,
                        sid=body.student_id, nid=body.node_identifier,
                    ).single()
                    # Reset failure_streak so the three-strike counter starts fresh
                    neo.run(
                        """
                        MATCH (s:Student {id: $sid})-[sk:SKILL_STATE]->
                              (n:StandardsFrameworkItem {identifier: $nid})
                        SET sk.failure_streak = 0
                        """,
                        sid=body.student_id, nid=body.node_identifier,
                    )
                    return int(cleared["cleared"]) if cleared else 0

            n_cleared = await loop.run_in_executor(None, _clear_blocks)
            if n_cleared:
                logger.info(
                    f"Unblocked {n_cleared} downstream nodes — "
                    f"student {body.student_id} mastered [{body.node_identifier}]"
                )
            bubble_result = await _bubble_mastery_forward(
                body.student_id,
                body.node_identifier,
                float(result["p_mastery_after"]),
            )

        driver.close()

        logger.info(
            f"exercise_complete: student={body.student_id} "
            f"std={body.standard_code} correct={body.correct} "
            f"bkt {result['p_mastery_before']:.3f}→{result['p_mastery_after']:.3f} "
            f"edge_updates={result['edge_updates']}"
        )
        return {
            "student_id":       body.student_id,
            "standard_code":    body.standard_code,
            "correct":          body.correct,
            "propagated_forward": bubble_result["updated"],
            "propagated_nodes": bubble_result["nodes"],
            **result,
        }

    except Exception as exc:
        logger.error(f"exercise_complete failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Live Exercise Chat — Neuro-Symbolic Signal Bridge
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/exercise_chat", summary="Process live student chat → φ signal + recursive pivot")
async def exercise_chat(body: ExerciseChatRequest) -> dict[str, Any]:
    """
    The 'Back and Forth' engine.

    Called when a student types anything into the chatbox during a remediation
    exercise.  The backend:
      1. Calls the Dynamic Weight Auditor (Gemini) to compute φ from the chat.
      2. Applies φ-modified BKT update to Neo4j.
      3. If φ < -0.3 (Hard Block): runs LCA to find the safety-net node,
         generates a bridge instruction, and returns a simpler pivot exercise.
      4. Writes a FailureChain audit record to PostgreSQL.
      5. Returns: {phi, reason, pivot_needed, pivot_node, bridge_instruction}.

    The frontend uses this to:
      - The "Back": show the bridge instruction + pivot exercise.
      - The "Forth": once the pivot is solved (φ → 1.0), resume original exercise
        with the bridge framing.
    """
    from backend.app.agents.signal_bridge import (
        PHI_BACKPROP_THRESHOLD,
        _heuristic_phi,
        compute_pivot,
        write_failure_chain,
    )
    from backend.app.agents.vertex_llm import get_llm
    from backend.app.agents.evaluation_agent import _bkt_update, _neo4j, _get_student_mastery_and_params, _upsert_mastery
    from backend.app.student.bkt_fitter import DEFAULT_P_TRANSIT

    try:
        # ── 1. Compute φ via Gemini ────────────────────────────────────────────
        llm = get_llm()
        phi_prompt = f"""\
Role: You are the Dynamic Weight Auditor for a K-8 adaptive math tutor.

Exercise context:
  {body.nanopoint_tag or f"[Standard: {body.standard_code} | Difficulty: {body.beta:.1f}]"}
  Exercise: {body.exercise_text[:300]}
  Concept: {body.concept}

Student signal:
  chat_message: "{body.chat_message}"
  answer_so_far: "{body.answer}"
  correct: {body.correct}
  time_ms: {body.time_ms:.0f}

Determine the Fidelity Signal φ ∈ [-1.0, 1.0]:
  1.0  Fluent      — student shows genuine conceptual understanding
  0.5  Partial     — student is trying but has a specific hurdle
  0.2  Brittle     — likely guessing or pattern-matching
 -0.5  Struggling  — specific prerequisite hurdle identified
 -1.0  Hard Block  — "I don't get this" — fundamental prerequisite missing

Return JSON: {{"phi": <float>, "reason": "<1 sentence>", "gap_tag": "<sub-skill or null>"}}"""

        phi = None
        reason = ""
        gap_tag = None
        try:
            raw = llm.generate_json(phi_prompt)
            if isinstance(raw, dict):
                phi     = max(-1.0, min(1.0, float(raw.get("phi", 0.5))))
                reason  = raw.get("reason", "")
                gap_tag = raw.get("gap_tag")
        except Exception as exc:
            logger.warning(f"exercise_chat φ computation failed: {exc}")

        if phi is None:
            mock_r = {
                "is_correct": body.correct or False,
                "is_likely_guess": body.time_ms > 0 and body.time_ms < 3000,
                "time_ms": body.time_ms,
                "dok_level": 2,
            }
            phi = _heuristic_phi(mock_r)["phi"]
            reason = "Heuristic φ (LLM unavailable)"

        logger.info(
            f"exercise_chat: student={body.student_id} std={body.standard_code} "
            f"φ={phi:+.2f}  reason='{reason[:60]}'"
        )

        # ── 2. φ-modified BKT update ──────────────────────────────────────────
        loop   = asyncio.get_event_loop()
        driver = _neo4j()

        def _do_bkt():
            with driver.session() as neo:
                p_before, p_slip, p_guess, p_transit = _get_student_mastery_and_params(
                    neo, body.student_id, body.node_identifier
                )
                count_row = neo.run(
                    """
                    MATCH (s:Student {id: $sid})-[r:SKILL_STATE]->
                          (n:StandardsFrameworkItem {identifier: $nid})
                    RETURN coalesce(r.attempts, 0) AS att, coalesce(r.correct, 0) AS cor
                    """,
                    sid=body.student_id, nid=body.node_identifier,
                ).single()
                attempts = (count_row["att"] if count_row else 0) + 1
                correct_count = (count_row["cor"] if count_row else 0) + (1 if body.correct else 0)

                p_after = _bkt_update(
                    p_before,
                    body.correct or False,
                    p_slip, p_guess, p_transit,
                    phi=phi,
                )
                _upsert_mastery(neo, body.student_id, body.node_identifier, p_after, attempts, correct_count)

                # Persist gap_tag if the LLM identified a specific sub-skill gap.
                # Stored as a list on SKILL_STATE (capped at 5 most recent, deduplicated).
                if gap_tag:
                    neo.run(
                        """
                        MATCH (s:Student {id: $sid})-[r:SKILL_STATE]->
                              (n:StandardsFrameworkItem {identifier: $nid})
                        SET r.gap_tags = CASE
                            WHEN r.gap_tags IS NULL            THEN [$tag]
                            WHEN $tag IN r.gap_tags            THEN r.gap_tags
                            WHEN size(r.gap_tags) >= 5         THEN tail(r.gap_tags) + [$tag]
                            ELSE r.gap_tags + [$tag]
                        END
                        """,
                        sid=body.student_id, nid=body.node_identifier, tag=gap_tag,
                    )

                return p_before, p_after

        p_before, p_after = await loop.run_in_executor(None, _do_bkt)
        driver.close()

        # ── 3. Recursive Pivot if φ < threshold ───────────────────────────────
        pivot_result = await loop.run_in_executor(
            None,
            lambda: compute_pivot(
                body.student_id, body.node_identifier, body.standard_code,
                body.concept, phi, gap_tag,
            ),
        )

        # ── 4. Write FailureChain audit record ────────────────────────────────
        if phi < PHI_BACKPROP_THRESHOLD:
            await loop.run_in_executor(
                None,
                lambda: write_failure_chain(
                    body.student_id, body.node_identifier, body.standard_code,
                    pivot_result.get("pivot_node"),
                    signal_source="phi_negative",
                ),
            )

        return {
            "phi":                phi,
            "reason":             reason,
            "gap_tag":            gap_tag,
            "p_mastery_before":   round(p_before, 3),
            "p_mastery_after":    round(p_after, 3),
            **pivot_result,
        }

    except Exception as exc:
        import traceback
        logger.error(f"exercise_chat failed: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/readiness/{student_id}",
    summary="LLM-powered assessment readiness check",
)
async def assessment_readiness(
    student_id: str,
    subject: str = Query(..., description="math or english"),
    grade: str   = Query(..., description="Grade level: 1-8"),
    concepts: str = Query(
        None,
        description="Comma-separated standard codes to check (e.g. 3.NBT.A.1,3.NBT.B.2). "
                    "If omitted, checks all tracked concepts for this student.",
    ),
) -> dict[str, Any]:
    """
    The LLM reviews this student's exercise history and BKT mastery trends
    to decide whether they are ready for a formal re-assessment.

    The verdict includes:
    - ready: bool — should we offer an assessment now?
    - trigger_now: bool — proactive suggestion (higher confidence)
    - exercises_until_ready: int — if not ready, how many more exercises are needed
    - concepts_to_assess: list — which standards to include in the next assessment
    - session_focus: str — one-sentence guidance for the next practice session
    - reasoning: str — the LLM's explanation

    The AI Tutor calls this endpoint during conversation to decide whether
    to proactively suggest re-assessment to the student.
    """
    from backend.app.agents.readiness_agent import check_assessment_readiness

    concept_list = [c.strip() for c in concepts.split(",")] if concepts else None

    try:
        loop = asyncio.get_event_loop()
        verdict = await loop.run_in_executor(
            None,
            lambda: check_assessment_readiness(student_id, subject, grade, concept_list),
        )
        return {
            "student_id": student_id,
            "subject":    subject,
            "grade":      grade,
            **verdict,
        }
    except Exception as exc:
        logger.error(f"Readiness check failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/nodes", summary="Preview standards selected for an assessment")
async def preview_nodes(
    grade: str   = Query(..., description="Grade: K1-K8"),
    subject: str = Query(..., description="Subject: math or english"),
    state: str   = Query("Multi-State"),
) -> dict[str, Any]:
    """Preview which graph nodes would be selected without generating questions."""
    try:
        from backend.app.student.assessment_engine import AssessmentEngine

        engine = AssessmentEngine()
        nodes  = engine.select_nodes(grade, state, subject)
        return {
            "grade":                    grade,
            "subject":                  subject,
            "state":                    state,
            "core_nodes_count":         len(nodes["core_nodes"]),
            "prerequisite_nodes_count": len(nodes["prerequisite_nodes"]),
            "target_nodes_count":       len(nodes["all_target_nodes"]),
            "core_nodes":               nodes["core_nodes"],
            "prerequisite_nodes":       nodes["prerequisite_nodes"][:10],
            "sample_target_nodes":      nodes["all_target_nodes"][:10],
        }
    except Exception as exc:
        logger.error(f"Node preview failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/grades", summary="List available grades, subjects, and states")
async def list_grades() -> dict[str, Any]:
    """List all supported grade levels (K1-K8), subjects, and US states."""
    from backend.app.student.assessment_engine import STATE_ABBREV, STATE_FRAMEWORK_NAMES

    return {
        "grades": [
            {"id": f"K{i}", "name": f"Grade {i}", "ages": f"{i + 5}-{i + 6}"}
            for i in range(1, 9)
        ],
        "subjects": [
            {"id": "math",    "name": "Mathematics",           "icon": "calculator"},
            {"id": "english", "name": "English Language Arts", "icon": "book-open"},
        ],
        "states": [
            {
                "abbrev":    abbrev,
                "name":      full,
                "framework": STATE_FRAMEWORK_NAMES.get(full, f"{full} Standards"),
            }
            for abbrev, full in sorted(STATE_ABBREV.items(), key=lambda x: x[1])
        ],
    }


@router.get(
    "/student/{student_id}/performance",
    summary="BKT performance report for a student",
)
async def student_performance(
    student_id: str,
    grade:   str = Query(..., description="Grade: K1-K8"),
    subject: str = Query(..., description="Subject: math or english"),
    state:   str = Query("Multi-State"),
) -> dict[str, Any]:
    """
    Full BKT performance report for a student on a specific grade + subject.

    Returns coverage %, mastery %, grade readiness score, blocking gaps,
    and per-standard nano weights.
    """
    try:
        from backend.app.student.bayesian_tracker import BayesianSkillTracker

        tracker = BayesianSkillTracker()
        profile = tracker.get_skill_profile(student_id)
        nano    = tracker.get_nano_weights_for_grade(student_id, grade, subject)
        gaps    = tracker.find_blocking_gaps(student_id, subject)
        tracker.close()

        attempted = [n for n in nano if n["attempts"] > 0]
        mastered  = [n for n in nano if (n.get("p_mastery") or 0) >= 0.85]
        coverage  = round(len(attempted) / len(nano) * 100, 1) if nano else 0
        mastery   = round(len(mastered) / len(attempted) * 100, 1) if attempted else 0

        return {
            "student_id":       student_id,
            "grade":            grade,
            "subject":          subject,
            "state":            state,
            "standards_total":  len(nano),
            "standards_attempted": len(attempted),
            "standards_mastered":  len(mastered),
            "coverage_pct":     coverage,
            "mastery_pct":      mastery,
            "blocking_gaps":    gaps[:10],
            "nano_weights":     nano,
            "skill_profile":    profile,
        }
    except Exception as exc:
        logger.error(f"Performance report failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/recommendations/{student_id}",
    summary="Agent-powered learning recommendations from BKT + Knowledge Graph",
)
async def get_recommendations(
    student_id: str,
    subject: str = Query(..., description="math or english"),
    grade: str   = Query(None, description="Focus grade (K1-K8). If omitted, auto-detects from BKT."),
    limit: int   = Query(5, ge=1, le=20),
) -> dict[str, Any]:
    """
    Graph-aware recommendation engine.

    After each assessment, this endpoint synthesizes:
    1. The student's BKT skill states (Neo4j SKILL_STATE edges)
    2. KG prerequisite chains (BUILDS_TOWARDS edges)
    3. Downstream blocking analysis — which gaps block the most future learning
    4. ZPD targeting — surfaces standards just above current mastery

    Returns a prioritized action plan:
      - immediate_actions: Fix these NOW (high-impact blocking gaps)
      - next_standards:    Ready to learn (prerequisites satisfied)
      - strengths:         Mastered standards to celebrate
      - learning_path:     Ordered sequence of recommended next standards
    """
    try:
        from neo4j import GraphDatabase
        from backend.app.core.settings import settings
        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        subject_name = "Mathematics" if subject.lower() == "math" else "English Language Arts"

        with driver.session(database=settings.neo4j_database) as session:
            # ── 1. Load all BKT states for this student ───────────────────
            bkt_res = session.run("""
                MATCH (stu:Student {student_id: $sid})-[sk:SKILL_STATE]->(n:StandardsFrameworkItem)
                WHERE n.academicSubject = $subject
                  AND n.normalizedStatementType = 'Standard'
                RETURN n.identifier      AS nid,
                       n.statementCode   AS code,
                       n.description     AS description,
                       n.gradeLevel      AS grade_level,
                       n.gradeLevelList  AS grade_list,
                       sk.p_mastery      AS p_mastery,
                       sk.nano_weight    AS nano_weight,
                       sk.attempts       AS attempts,
                       sk.correct        AS correct
                ORDER BY sk.p_mastery ASC
            """, sid=student_id, subject=subject_name)
            all_skills = [dict(r) for r in bkt_res]

        if not all_skills:
            driver.close()
            return {
                "student_id": student_id,
                "subject": subject,
                "message": "No assessment data yet. Complete an assessment first.",
                "immediate_actions": [],
                "next_standards": [],
                "strengths": [],
                "learning_path": [],
                "summary": {
                    "total_tracked": 0,
                    "mastered": 0,
                    "in_progress": 0,
                    "not_started": 0,
                },
            }

        # Categorise skills by mastery tier
        mastered    = [s for s in all_skills if (s.get("p_mastery") or 0) >= 0.85]
        proficient  = [s for s in all_skills if 0.65 <= (s.get("p_mastery") or 0) < 0.85]
        developing  = [s for s in all_skills if 0.35 <= (s.get("p_mastery") or 0) < 0.65]
        struggling  = [s for s in all_skills if (s.get("p_mastery") or 0) < 0.35 and (s.get("attempts") or 0) > 0]

        weak_ids = [s["nid"] for s in (struggling + developing)]

        # ── 2. Downstream blocking analysis ──────────────────────────────
        immediate_actions: list[dict] = []
        if weak_ids:
            with driver.session(database=settings.neo4j_database) as session:
                gap_res = session.run("""
                    UNWIND $ids AS nid
                    MATCH (src:StandardsFrameworkItem {identifier: nid})
                    OPTIONAL MATCH (src)-[:BUILDS_TOWARDS|HAS_CHILD*1..2]->(downstream:StandardsFrameworkItem)
                    WHERE downstream.normalizedStatementType = 'Standard'
                    WITH src, nid, count(DISTINCT downstream) AS blocked_count
                    OPTIONAL MATCH (stu:Student {student_id: $sid})-[sk:SKILL_STATE]->(src)
                    RETURN nid,
                           src.statementCode  AS code,
                           src.description    AS description,
                           src.gradeLevel     AS grade_level,
                           COALESCE(sk.p_mastery, 0.1)   AS p_mastery,
                           COALESCE(sk.nano_weight, 10.0) AS nano_weight,
                           COALESCE(sk.attempts, 0)       AS attempts,
                           blocked_count
                    ORDER BY blocked_count DESC, p_mastery ASC
                    LIMIT $limit
                """, ids=weak_ids, sid=student_id, limit=limit)

                for r in gap_res:
                    p = float(r["p_mastery"] or 0.1)
                    priority = (
                        "critical" if p < 0.35 and r["blocked_count"] > 3 else
                        "high"     if p < 0.35 else
                        "medium"   if p < 0.65 else
                        "low"
                    )
                    immediate_actions.append({
                        "node_id":         r["nid"],
                        "code":            r["code"],
                        "description":     r["description"],
                        "grade_level":     r["grade_level"],
                        "p_mastery":       round(p, 3),
                        "nano_weight":     round(float(r["nano_weight"] or 10), 1),
                        "attempts":        int(r["attempts"] or 0),
                        "blocked_count":   r["blocked_count"],
                        "priority":        priority,
                        "action":          (
                            f"Review and practice {r['code']} — "
                            f"mastery at {p*100:.0f}% is blocking {r['blocked_count']} future standards"
                        ),
                    })

        # ── 3. Next standards: prerequisites satisfied, not yet mastered ──
        mastered_ids = [s["nid"] for s in mastered]
        next_standards: list[dict] = []
        if mastered_ids:
            with driver.session(database=settings.neo4j_database) as session:
                next_res = session.run("""
                    UNWIND $mastered_ids AS mid
                    MATCH (m:StandardsFrameworkItem {identifier: mid})
                    MATCH (m)-[:BUILDS_TOWARDS]->(candidate:StandardsFrameworkItem)
                    WHERE candidate.normalizedStatementType = 'Standard'
                      AND NOT candidate.identifier IN $mastered_ids
                      AND candidate.academicSubject = $subject
                    OPTIONAL MATCH (stu:Student {student_id: $sid})-[sk:SKILL_STATE]->(candidate)
                    WITH candidate,
                         COALESCE(sk.p_mastery, 0.1) AS p_mastery,
                         COALESCE(sk.attempts, 0)    AS attempts,
                         collect(m.statementCode)    AS unlocked_by
                    WHERE p_mastery < 0.85
                    RETURN candidate.identifier   AS nid,
                           candidate.statementCode AS code,
                           candidate.description   AS description,
                           candidate.gradeLevel    AS grade_level,
                           p_mastery, attempts, unlocked_by
                    ORDER BY p_mastery DESC
                    LIMIT $limit
                """, mastered_ids=mastered_ids, sid=student_id,
                     subject=subject_name, limit=limit)

                for r in next_res:
                    next_standards.append({
                        "node_id":     r["nid"],
                        "code":        r["code"],
                        "description": r["description"],
                        "grade_level": r["grade_level"],
                        "p_mastery":   round(float(r["p_mastery"] or 0.1), 3),
                        "attempts":    int(r["attempts"] or 0),
                        "unlocked_by": r["unlocked_by"],
                        "readiness":   "ready" if float(r["p_mastery"] or 0.1) >= 0.35 else "approaching",
                        "action":      f"Start working on {r['code']} — prerequisites mastered",
                    })

        # ── 4. Build ordered learning path ────────────────────────────────
        # Priority: critical gaps first → medium gaps → next standards
        learning_path: list[dict] = []
        seen_path: set[str] = set()
        for item in immediate_actions:
            if item["node_id"] not in seen_path:
                learning_path.append({
                    "step": len(learning_path) + 1,
                    "type": "gap_remediation",
                    "code": item["code"],
                    "description": item["description"][:120],
                    "priority": item["priority"],
                    "reason": f"Blocking {item['blocked_count']} future standards",
                })
                seen_path.add(item["node_id"])
        for item in next_standards:
            if item["node_id"] not in seen_path and len(learning_path) < limit:
                learning_path.append({
                    "step": len(learning_path) + 1,
                    "type": "next_learning",
                    "code": item["code"],
                    "description": item["description"][:120],
                    "priority": "medium",
                    "reason": f"Prerequisites satisfied by: {', '.join(item['unlocked_by'][:2])}",
                })
                seen_path.add(item["node_id"])

        driver.close()

        return {
            "student_id":   student_id,
            "subject":      subject,
            "grade_focus":  grade,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "summary": {
                "total_tracked": len(all_skills),
                "mastered":      len(mastered),
                "proficient":    len(proficient),
                "developing":    len(developing),
                "struggling":    len(struggling),
                "avg_mastery":   round(
                    sum(float(s.get("p_mastery") or 0) for s in all_skills) / len(all_skills), 3
                ) if all_skills else 0,
            },
            "immediate_actions": immediate_actions[:limit],
            "next_standards":    next_standards[:limit],
            "strengths": [
                {
                    "code":        s["code"],
                    "description": s["description"][:100],
                    "p_mastery":   round(float(s.get("p_mastery") or 0), 3),
                    "nano_weight": round(float(s.get("nano_weight") or 0), 1),
                }
                for s in mastered[:5]
            ],
            "learning_path": learning_path,
        }

    except Exception as exc:
        logger.error(f"Recommendations failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/calibrate_bkt",
    summary="Run Baum-Welch EM to fit per-skill BKT parameters from response history",
)
async def calibrate_bkt(
    min_observations: int = Query(
        default=30,
        ge=10,
        description="Minimum ATTEMPTED records required to fit a skill",
    ),
) -> dict[str, Any]:
    """
    Fits (p_init, p_transit, p_slip, p_guess) per StandardsFrameworkItem
    using Baum-Welch EM on accumulated student response sequences stored in Neo4j.

    Writes fitted params back to each node as:
      n.bkt_p_init, n.bkt_p_transit, n.bkt_p_slip, n.bkt_p_guess,
      n.bkt_fitted_at, n.bkt_n_sequences

    Both BayesianSkillTracker and the evaluation pipeline read these values
    on every subsequent assessment.  Skills with insufficient data keep system
    defaults until more responses accumulate.

    Call this endpoint periodically (e.g. nightly cron) as more student data
    builds up.
    """
    from neo4j import GraphDatabase
    from backend.app.core.settings import settings
    from backend.app.student.bkt_fitter import calibrate_all_skills

    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    try:
        stats = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: calibrate_all_skills(driver, settings.neo4j_database, min_observations),
        )
    finally:
        driver.close()

    return {
        "status": "ok",
        "min_observations": min_observations,
        **stats,
    }


@router.get(
    "/student/{student_id}/trajectory",
    summary="K1-K8 grade trajectory for a student",
)
async def student_trajectory(
    student_id: str,
    subject: str = Query(..., description="Subject: math or english"),
    state:   str = Query("Multi-State"),
) -> dict[str, Any]:
    """
    Grade K1-K8 summary for a student in one subject.

    Returns one row per grade with: standards_total, attempted, mastered,
    coverage_pct, mastery_pct, and grade_status.
    """
    try:
        from backend.app.student.bayesian_tracker import BayesianSkillTracker

        tracker = BayesianSkillTracker()
        trajectory = []
        active_grade = None

        for i in range(1, 9):
            grade_id = f"K{i}"
            nano     = tracker.get_nano_weights_for_grade(student_id, grade_id, subject)
            attempted = [n for n in nano if n["attempts"] > 0]
            mastered  = [n for n in nano if (n.get("p_mastery") or 0) >= 0.85]

            coverage = round(len(attempted) / len(nano) * 100, 1) if nano else 0
            mastery  = round(len(mastered) / len(attempted) * 100, 1) if attempted else 0

            grade_status = (
                "above"       if mastery >= 90 else
                "at"          if mastery >= 75 else
                "approaching" if mastery >= 60 else
                "below"       if attempted else
                "not_started"
            )

            if attempted:
                active_grade = grade_id

            trajectory.append({
                "grade":              grade_id,
                "grade_name":         f"Grade {i}",
                "standards_total":    len(nano),
                "standards_attempted": len(attempted),
                "standards_mastered": len(mastered),
                "coverage_pct":       coverage,
                "mastery_pct":        mastery,
                "grade_status":       grade_status,
            })

        tracker.close()
        return {
            "student_id":   student_id,
            "subject":      subject,
            "state":        state,
            "active_grade": active_grade,
            "trajectory":   trajectory,
        }
    except Exception as exc:
        logger.error(f"Trajectory report failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/student/{student_id}/last_result", summary="Get the last assessment result for parent review")
async def get_last_result(student_id: str) -> dict[str, Any]:
    """Return the stored snapshot from the most recent assessment for this student."""
    import json as _json
    try:
        from neo4j import GraphDatabase
        from backend.app.core.settings import settings

        driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
        records, _, _ = driver.execute_query(
            "MATCH (s:Student {id:$sid}) RETURN s.last_assessment AS snap, s.parent_accuracy_feedback AS feedback",
            sid=student_id,
        )
        driver.close()

        if not records or records[0]["snap"] is None:
            return {"has_data": False}

        snap = _json.loads(records[0]["snap"])
        feedback_raw = records[0]["feedback"]
        feedback = _json.loads(feedback_raw) if feedback_raw else None
        return {"has_data": True, "snapshot": snap, "parent_feedback": feedback}
    except Exception as exc:
        logger.error(f"last_result failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/parent_feedback", summary="Parent confirms or disputes assessment accuracy")
async def submit_parent_feedback(body: ParentFeedbackRequest) -> dict[str, Any]:
    """Store parent's accuracy rating (yes/somewhat/no) + optional notes on the Student node."""
    import json as _json
    try:
        from neo4j import GraphDatabase
        from backend.app.core.settings import settings

        payload = {
            "accurate": body.accurate,
            "notes": body.parent_notes,
            "assessment_id": body.assessment_id,
            "submitted_at": datetime.datetime.utcnow().isoformat(),
        }
        driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
        driver.execute_query(
            "MERGE (s:Student {id:$sid}) SET s.parent_accuracy_feedback = $feedback",
            sid=body.student_id,
            feedback=_json.dumps(payload),
        )
        driver.close()
        return {"status": "saved", "student_id": body.student_id, "accurate": body.accurate}
    except Exception as exc:
        logger.error(f"parent_feedback failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
