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


class AnswerSubmission(BaseModel):
    assessment_id: str
    student_id: str = "default"
    grade: str = Field(default="K5")
    subject: str = Field(default="math")
    state: str = Field(default="Multi-State")
    answers: list[dict[str, Any]] = Field(
        ...,
        description=(
            "List of {question_id, student_answer, node_ref, category, "
            "standard_code, standard_description, is_correct}"
        ),
    )


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
        # Load student's current θ from Neo4j (or default 0.0 for new students)
        theta = await _load_student_theta(body.student_id)

        initial_state = AssessmentState(
            student_id=body.student_id,
            grade=body.grade,
            subject=body.subject,
            state_jurisdiction=body.state,
            theta=theta,
            phase="start",
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
    from backend.app.agents.orchestrator import get_phase_b
    from backend.app.agent.state import AssessmentState

    try:
        theta = await _load_student_theta(body.student_id)

        # Reconstruct question list from submitted answers for Phase B
        questions = [
            {
                "id":           a.get("question_id", ""),
                "question":     a.get("question", ""),
                "options":      a.get("options", []),
                "answer":       a.get("correct_answer", a.get("answer", "")),
                "category":     a.get("category", "target"),
                "dok_level":    a.get("dok_level", 2),
                "standard_code": a.get("standard_code", ""),
                "node_ref":     a.get("node_ref", ""),
                "beta":         a.get("beta", 0.0),
            }
            for a in body.answers
        ]
        submitted = [
            {"question_id": a.get("question_id", ""), "selected_answer": a.get("student_answer", "")}
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
            # Full results
            "results":           final.results,
        }
    except Exception as exc:
        logger.error(f"Assessment evaluation failed: {exc}")
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
