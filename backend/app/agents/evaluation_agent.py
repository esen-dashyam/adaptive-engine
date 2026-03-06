"""
Evaluation Agent — Phase B LangGraph.

Flow:
  score_answers → update_rasch → detect_misconceptions → update_bkt → END

Key algorithms:
  1. Score answers (correct/incorrect)
  2. Rasch 1PL IRT update: recalculate θ after every answer
  3. LLM Reasoning Layer: feed wrong answers to Vertex AI to diagnose misconceptions
  4. BKT mastery update: update Neo4j SKILL_STATE relationships
  5. Misconception weights: temporarily lower mastery of related nodes
"""

from __future__ import annotations

import math
from typing import Any

from loguru import logger

from backend.app.agent.state import AssessmentState
from backend.app.agents.rasch import RaschSession, grade_to_difficulty, p_correct
from backend.app.agents.vertex_llm import get_llm
from backend.app.core.settings import settings


# ── BKT constants ─────────────────────────────────────────────────────────────
P_INIT   = 0.1
P_LEARN  = 0.2
P_SLIP   = 0.1
P_GUESS  = 0.2


def _bkt_update(p_mastery: float, correct: bool) -> float:
    p_slip, p_guess = P_SLIP, P_GUESS
    if correct:
        denom = p_mastery * (1 - p_slip) + (1 - p_mastery) * p_guess
        posterior = (p_mastery * (1 - p_slip)) / (denom + 1e-9)
    else:
        denom = p_mastery * p_slip + (1 - p_mastery) * (1 - p_guess)
        posterior = (p_mastery * p_slip) / (denom + 1e-9)
    return min(1.0, posterior + (1 - posterior) * P_LEARN)


def _neo4j():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


def _get_student_mastery(session, student_id: str, node_id: str) -> float:
    r = session.run(
        """
        MATCH (s:Student {id: $sid})-[r:SKILL_STATE]->(n:StandardsFrameworkItem {identifier: $nid})
        RETURN r.p_mastery AS p
        """,
        sid=student_id, nid=node_id,
    ).single()
    return float(r["p"]) if r and r["p"] is not None else P_INIT


def _upsert_mastery(session, student_id: str, node_id: str, p_mastery: float,
                     attempts: int, correct: int) -> None:
    session.run(
        """
        MERGE (s:Student {id: $sid})
        MERGE (n:StandardsFrameworkItem {identifier: $nid})
        MERGE (s)-[r:SKILL_STATE]->(n)
        SET r.p_mastery = $pm,
            r.attempts  = $att,
            r.correct   = $cor,
            r.last_updated = datetime()
        """,
        sid=student_id, nid=node_id, pm=p_mastery, att=attempts, cor=correct,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Node 1 — score_answers
# ─────────────────────────────────────────────────────────────────────────────

def score_answers(state: AssessmentState) -> dict:
    """Compare submitted answers against correct answers; build results list."""
    q_map = {q["id"]: q for q in state.questions}
    results = []
    correct_count = 0

    for submission in state.submitted_answers:
        qid = submission.get("question_id", "")
        selected = submission.get("selected_answer", "").upper().strip()
        q = q_map.get(qid, {})
        correct_ans = (q.get("answer") or "").upper().strip()
        is_correct = selected == correct_ans

        if is_correct:
            correct_count += 1

        results.append({
            "question_id": qid,
            "question":    q.get("question", ""),
            "options":     q.get("options", []),
            "correct_answer": correct_ans,
            "student_answer": selected,
            "is_correct":   is_correct,
            "category":     q.get("category", "target"),
            "dok_level":    q.get("dok_level", 2),
            "standard_code": q.get("standard_code", ""),
            "node_ref":     q.get("node_ref", ""),
            "beta":         q.get("beta", 0.0),
            "mastery_before": 0.0,  # filled in update_rasch
            "mastery_after":  0.0,
        })

    score = correct_count / max(len(results), 1)
    logger.info(f"Evaluation Agent: score={score:.2%}  ({correct_count}/{len(results)})")
    return {"results": results, "score": score}


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 — update_rasch
# ─────────────────────────────────────────────────────────────────────────────

def update_rasch(state: AssessmentState) -> dict:
    """
    Apply Rasch 1PL IRT updates after all answers are scored.
    Processes responses in the order they were submitted to simulate
    live adaptive updating.
    """
    rasch = RaschSession(initial_theta=state.theta)
    updated_results = list(state.results)

    for i, r in enumerate(updated_results):
        beta = float(r.get("beta", 0.0))
        is_correct = r["is_correct"]
        theta_before = rasch.theta
        new_theta = rasch.record(r["question_id"], beta, is_correct)
        updated_results[i] = {**r, "theta_before": theta_before, "theta_after": new_theta}

    rasch_summary = rasch.to_dict()
    logger.info(
        f"Rasch update: θ {state.theta:+.3f} → {rasch.theta:+.3f} "
        f"(SE={rasch.se:.3f}, equiv={rasch_summary['grade_equivalent']})"
    )

    return {
        "results": updated_results,
        "theta": rasch.theta,
        "theta_history": [h["theta_after"] for h in rasch.history],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node 3 — detect_misconceptions
# ─────────────────────────────────────────────────────────────────────────────

def detect_misconceptions(state: AssessmentState) -> dict:
    """
    Feed wrong answers to Vertex AI and ask: 'Which misconception led to this error?'
    The LLM response identifies the root misconception and affected concept nodes,
    which the KST layer will use to lower mastery weights on related nodes.
    """
    wrong_answers = [r for r in state.results if not r["is_correct"]]
    if not wrong_answers:
        return {"misconceptions": [], "misconception_weights": {}}

    grade_label = f"Grade {state.grade.replace('K','')}"

    # Build a compact summary for the LLM
    cases = []
    for r in wrong_answers[:6]:  # cap at 6 to keep prompt short
        cases.append(
            f"- Standard: {r.get('standard_code','')} | "
            f"Question: {r.get('question','')[:120]} | "
            f"Student answered: {r.get('student_answer','')} | "
            f"Correct: {r.get('correct_answer','')}"
        )

    prompt = f"""You are a learning diagnostician for {grade_label} {state.subject}.

A student got the following questions wrong:
{chr(10).join(cases)}

For EACH wrong answer:
1. Identify the most likely underlying misconception (1 short sentence).
2. List 1-3 concept node IDs (from the standard codes) that are likely affected by this misconception.
3. Assign a mastery penalty (0.1 to 0.4) — how much to lower mastery of the affected nodes.

Return a JSON array:
[
  {{
    "question_id": "<id from the question>",
    "standard_code": "<code>",
    "misconception": "<short description>",
    "affected_standards": ["<code1>", "<code2>"],
    "mastery_penalty": 0.2
  }}
]"""

    llm = get_llm()
    misconceptions = []
    misconception_weights: dict[str, float] = {}

    try:
        raw = llm.generate_json(prompt)
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                misconceptions.append(item)
                penalty = float(item.get("mastery_penalty", 0.15))
                for std_code in item.get("affected_standards", []):
                    # accumulate penalties (max 0.5 total per node)
                    existing = misconception_weights.get(std_code, 0.0)
                    misconception_weights[std_code] = min(0.5, existing + penalty)
    except Exception as exc:
        logger.warning(f"Misconception detection failed (non-fatal): {exc}")

    logger.info(
        f"Misconception detection: {len(misconceptions)} misconceptions found, "
        f"{len(misconception_weights)} nodes penalised"
    )
    return {"misconceptions": misconceptions, "misconception_weights": misconception_weights}


# ─────────────────────────────────────────────────────────────────────────────
# Node 4 — update_bkt
# ─────────────────────────────────────────────────────────────────────────────

def update_bkt(state: AssessmentState) -> dict:
    """
    Update BKT mastery probabilities in Neo4j for each tested standard.
    Also fills mastery_before / mastery_after into results.
    """
    driver = _neo4j()
    mastery_updates: dict[str, float] = {}
    updated_results = list(state.results)

    try:
        with driver.session() as neo_sess:
            for i, r in enumerate(updated_results):
                nid = r.get("node_ref", "")
                if not nid:
                    continue

                # Read current BKT mastery
                p_before = _get_student_mastery(neo_sess, state.student_id, nid)

                # Apply misconception penalty before BKT
                std_code = r.get("standard_code", "")
                penalty = state.misconception_weights.get(std_code, 0.0)
                p_before_adj = max(0.05, p_before - penalty)

                # BKT update
                p_after = _bkt_update(p_before_adj, r["is_correct"])

                # Count attempts/correct in Neo4j
                count_r = neo_sess.run(
                    """
                    MATCH (s:Student {id:$sid})-[r:SKILL_STATE]->(n:StandardsFrameworkItem {identifier:$nid})
                    RETURN coalesce(r.attempts,0) AS att, coalesce(r.correct,0) AS cor
                    """,
                    sid=state.student_id, nid=nid,
                ).single()
                attempts = (count_r["att"] if count_r else 0) + 1
                correct  = (count_r["cor"] if count_r else 0) + (1 if r["is_correct"] else 0)

                _upsert_mastery(neo_sess, state.student_id, nid, p_after, attempts, correct)
                mastery_updates[nid] = p_after

                updated_results[i] = {
                    **updated_results[i],
                    "mastery_before": round(p_before, 3),
                    "mastery_after":  round(p_after, 3),
                    "rasch_mastery":  round(p_after, 3),
                }
    except Exception as exc:
        logger.error(f"BKT update failed: {exc}")
    finally:
        driver.close()

    logger.info(f"BKT: updated mastery for {len(mastery_updates)} nodes")
    return {"results": updated_results, "mastery_updates": mastery_updates}
