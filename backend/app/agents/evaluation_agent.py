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


# Default BKT params — used when a node has no fitted values yet.
# After BKTFitter runs, per-skill values on the node override these.
from backend.app.student.bkt_fitter import (
    DEFAULT_P_INIT,
    DEFAULT_P_TRANSIT,
    DEFAULT_P_SLIP,
    DEFAULT_P_GUESS,
)


def _bkt_update(
    p_mastery: float,
    correct: bool,
    p_slip: float,
    p_guess: float,
    p_transit: float,
    phi: float = 1.0,
) -> float:
    """
    Neuro-Symbolic BKT update: traditional Bayes posterior + φ-modulated transition.

    Standard step (Bayes posterior):
        P(L | correct) = P(L)(1-slip) / [P(L)(1-slip) + (1-P(L))guess]
        P(L | wrong)   = P(L)slip     / [P(L)slip     + (1-P(L))(1-guess)]

    φ-modulated transition (the "Signal Bridge"):
        P(L_{t+1}) = P(L|obs) + (1 - P(L|obs)) * (p_transit * φ)

    φ = 1.0  → full learning gain (fluent, genuine understanding)
    φ = 0.5  → half gain (brittle mastery, possible guess)
    φ = 0.0  → no transition (mastery frozen at posterior)
    φ = -1.0 → un-learning: engine stays or moves backward (deep block)

    This lets LLM-detected confusion actively pull P(L) down, forcing the
    system to remediate rather than silently over-credit a correct answer.
    """
    if correct:
        denom = p_mastery * (1.0 - p_slip) + (1.0 - p_mastery) * p_guess
        posterior = (p_mastery * (1.0 - p_slip)) / (denom + 1e-9)
    else:
        denom = p_mastery * p_slip + (1.0 - p_mastery) * (1.0 - p_guess)
        posterior = (p_mastery * p_slip) / (denom + 1e-9)
    # φ-modulated transition (φ can be negative → un-learning)
    updated = posterior + (1.0 - posterior) * (p_transit * phi)
    return max(0.01, min(0.999, updated))


def _neo4j():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


def _get_student_mastery_and_params(
    session, student_id: str, node_id: str
) -> tuple[float, float, float, float]:
    """
    Returns (p_mastery, p_slip, p_guess, p_transit) for this student-skill pair.

    p_mastery comes from the SKILL_STATE edge (defaults to p_init if new).
    p_slip/p_guess/p_transit come from the node's fitted BKT params if present,
    otherwise fall back to system defaults.
    """
    r = session.run(
        """
        MATCH (n:StandardsFrameworkItem {identifier: $nid})
        OPTIONAL MATCH (s:Student {id: $sid})-[sk:SKILL_STATE]->(n)
        RETURN coalesce(sk.p_mastery, $def_init)      AS p_mastery,
               coalesce(n.bkt_p_slip,    $def_slip)    AS p_slip,
               coalesce(n.bkt_p_guess,   $def_guess)   AS p_guess,
               coalesce(n.bkt_p_transit, $def_transit) AS p_transit
        """,
        sid=student_id, nid=node_id,
        def_init=DEFAULT_P_INIT,
        def_slip=DEFAULT_P_SLIP,
        def_guess=DEFAULT_P_GUESS,
        def_transit=DEFAULT_P_TRANSIT,
    ).single()
    if not r:
        return DEFAULT_P_INIT, DEFAULT_P_SLIP, DEFAULT_P_GUESS, DEFAULT_P_TRANSIT
    return (
        float(r["p_mastery"]),
        float(r["p_slip"]),
        float(r["p_guess"]),
        float(r["p_transit"]),
    )


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
    logger.info("━" * 60)
    logger.info("  PHASE B — STEP 1/7 │ score_answers")
    logger.info(f"  {len(state.submitted_answers)} answers submitted for {len(state.questions)} questions")
    logger.info("━" * 60)
    q_map = {q["id"]: q for q in state.questions}
    results = []
    correct_count = 0

    time_per_question: dict[str, float] = {}

    for submission in state.submitted_answers:
        qid = submission.get("question_id", "")
        selected = submission.get("selected_answer", "").upper().strip()
        q = q_map.get(qid, {})
        correct_ans = (q.get("answer") or "").upper().strip()
        is_correct = selected == correct_ans

        if is_correct:
            correct_count += 1

        # Capture time-to-respond for Fidelity Multiplier (ms → stored per question)
        time_ms = float(submission.get("time_ms", 0.0))
        if qid:
            time_per_question[qid] = time_ms

        # Fluency flag: very fast correct answers (< 4 s) may indicate a guess
        # This is used later by apply_fidelity_correction to halve the BKT gain.
        is_likely_guess = is_correct and 0 < time_ms < 4000

        # Chat message typed by the student alongside this answer (may be empty)
        chat_message = submission.get("chat_message", "") or ""

        results.append({
            "question_id": qid,
            "question":    q.get("question", ""),
            "options":     q.get("options", []),
            "correct_answer": correct_ans,
            "student_answer": selected,
            "is_correct":   is_correct,
            "is_likely_guess": is_likely_guess,
            "time_ms":      time_ms,
            "chat_message": chat_message,
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
    return {"results": results, "score": score, "time_per_question": time_per_question}


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 — update_rasch
# ─────────────────────────────────────────────────────────────────────────────

def update_rasch(state: AssessmentState) -> dict:
    """
    Apply Rasch 1PL IRT updates after all answers are scored.
    Processes responses in the order they were submitted to simulate
    live adaptive updating.
    """
    logger.info("━" * 60)
    logger.info(f"  PHASE B — STEP 2/7 │ update_rasch  (initial θ={state.theta:+.3f})")
    logger.info("━" * 60)
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

    total_answered = state.total_answered + len(updated_results)
    return {
        "results":        updated_results,
        "theta":          rasch.theta,
        "theta_history":  [h["theta_after"] for h in rasch.history],
        "se":             rasch.se,
        "total_answered": total_answered,
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
    wrong = [r for r in state.results if not r["is_correct"]]
    logger.info("━" * 60)
    logger.info(f"  PHASE B — STEP 3/7 │ detect_misconceptions  ({len(wrong)} wrong answers → Gemini)")
    logger.info("━" * 60)
    wrong_answers = [r for r in state.results if not r["is_correct"]]
    if not wrong_answers:
        return {"misconceptions": [], "misconception_weights": {}}

    grade_label = f"Grade {state.grade.replace('K','')}"

    # Build a compact summary for the LLM
    cases = []
    for r in wrong_answers[:6]:  # cap at 6 to keep prompt short
        cases.append(
            f"- Standard: {r.get('standard_code','')} | "
            f"Question: {(r.get('question') or '')[:120]} | "
            f"Student answered: {r.get('student_answer','')} | "
            f"Correct: {r.get('correct_answer','')}"
        )

    prompt = f"""You are a learning diagnostician for {grade_label} {state.subject}.

A student got the following questions wrong:
{chr(10).join(cases)}

For EACH wrong answer, diagnose which specific PREREQUISITE skill broke down.
Think like a teacher reading the student's work: the surface error (e.g. wrong fraction)
usually points to a gap in a foundational skill (e.g. multiplication facts, place value).

Return a JSON array — one entry per wrong answer:
[
  {{
    "question_id": "<id from the question>",
    "standard_code": "<the standard being tested, e.g. 4.NF.A.1>",
    "misconception": "<one sentence: what the student misunderstood>",
    "root_prerequisite_code": "<the SINGLE prerequisite standard code most responsible for the failure, e.g. 3.OA.A.1>",
    "affected_standards": ["<code1>", "<code2>"],
    "mastery_penalty": 0.2
  }}
]

Rules:
- root_prerequisite_code must be a PREREQUISITE of the tested standard, not the standard itself
- If no prerequisite is clearly responsible, set root_prerequisite_code to null
- mastery_penalty: 0.1 (minor gap) to 0.4 (fundamental breakdown)"""

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
                    existing = misconception_weights.get(std_code, 0.0)
                    misconception_weights[std_code] = min(0.5, existing + penalty)
    except Exception as exc:
        logger.warning(f"Misconception detection failed (non-fatal): {exc}")

    # Back-propagate failure to root prerequisite nodes in Neo4j
    # This is the key step Gemini described: if the LLM says "Basic Division (3.OA.A.1)
    # is the root cause of this Fractions failure", we write a BKT incorrect observation
    # directly to the Division node — not just a temporary penalty on the Fractions node.
    if misconceptions:
        _backpropagate_to_prerequisites(state.student_id, misconceptions)

    logger.info(
        f"Misconception detection: {len(misconceptions)} misconceptions found, "
        f"{len(misconception_weights)} nodes penalised, "
        f"{sum(1 for m in misconceptions if m.get('root_prerequisite_code'))} prereqs back-propagated"
    )
    return {"misconceptions": misconceptions, "misconception_weights": misconception_weights}


def _backpropagate_to_prerequisites(
    student_id: str,
    misconceptions: list[dict],
) -> None:
    """
    For each misconception where the LLM identified a root prerequisite,
    write a BKT incorrect observation to that prerequisite's SKILL_STATE in Neo4j.

    This is distinct from the misconception_weights penalty (which is a temporary
    surface adjustment): this is a permanent BKT signal on the actual prerequisite node,
    meaning future assessments will surface that gap.
    """
    # Collect unique root prerequisite codes with their penalties
    prereq_penalties: dict[str, float] = {}
    for item in misconceptions:
        code = item.get("root_prerequisite_code")
        if not code:
            continue
        penalty = float(item.get("mastery_penalty", 0.15))
        # Take the largest penalty if a prereq appears multiple times
        prereq_penalties[code] = max(prereq_penalties.get(code, 0.0), penalty)

    if not prereq_penalties:
        return

    driver = _neo4j()
    try:
        with driver.session() as session:
            for std_code, penalty in prereq_penalties.items():
                # Look up the node identifier for this standard code
                node_row = session.run(
                    """
                    MATCH (n:StandardsFrameworkItem {statementCode: $code})
                    WHERE n.normalizedStatementType = 'Standard'
                    RETURN n.identifier AS nid
                    LIMIT 1
                    """,
                    code=std_code,
                ).single()

                if not node_row or not node_row["nid"]:
                    logger.debug(f"Back-propagation: no node found for code {std_code}")
                    continue

                nid = node_row["nid"]

                # Read current mastery + per-skill BKT params for this prereq node
                p_before, p_slip, p_guess, p_transit = _get_student_mastery_and_params(
                    session, student_id, nid
                )

                # Apply the penalty first (LLM's confidence signal),
                # then run a BKT incorrect update (marks an observed failure)
                p_penalised = max(0.05, p_before - penalty)
                p_after = _bkt_update(p_penalised, correct=False,
                                      p_slip=p_slip, p_guess=p_guess, p_transit=p_transit)

                # Read current attempt counts
                count_row = session.run(
                    """
                    MATCH (s:Student {id: $sid})-[r:SKILL_STATE]->
                          (n:StandardsFrameworkItem {identifier: $nid})
                    RETURN coalesce(r.attempts, 0) AS att,
                           coalesce(r.correct, 0)  AS cor
                    """,
                    sid=student_id, nid=nid,
                ).single()
                attempts = (count_row["att"] if count_row else 0) + 1
                correct  = (count_row["cor"] if count_row else 0)  # incorrect observation

                _upsert_mastery(session, student_id, nid, p_after, attempts, correct)

                logger.info(
                    f"Back-propagated to {std_code} ({nid}): "
                    f"p_mastery {p_before:.3f} → {p_after:.3f} (penalty={penalty})"
                )
    except Exception as exc:
        logger.warning(f"Prerequisite back-propagation failed (non-fatal): {exc}")
    finally:
        driver.close()


# ─────────────────────────────────────────────────────────────────────────────
# Node 4 — update_bkt
# ─────────────────────────────────────────────────────────────────────────────

def update_bkt(state: AssessmentState) -> dict:
    """
    Update BKT mastery probabilities in Neo4j for each tested standard.
    Also fills mastery_before / mastery_after into results.
    """
    logger.info("━" * 60)
    logger.info(f"  PHASE B — STEP 4/7 │ update_bkt  ({len(state.results)} nodes → Neo4j SKILL_STATE)")
    logger.info("━" * 60)
    driver = _neo4j()
    mastery_updates: dict[str, float] = {}
    updated_results = list(state.results)

    try:
        with driver.session() as neo_sess:
            for i, r in enumerate(updated_results):
                nid = r.get("node_ref", "")
                if not nid:
                    continue

                # Read current mastery + per-skill fitted BKT params from Neo4j
                p_before, p_slip, p_guess, p_transit = _get_student_mastery_and_params(
                    neo_sess, state.student_id, nid
                )

                # Apply misconception penalty (LLM layer signal)
                std_code = r.get("standard_code", "")
                penalty = state.misconception_weights.get(std_code, 0.0)
                p_before_adj = max(0.05, p_before - penalty)

                # Pull φ from the Signal Bridge output (defaults by correctness if absent)
                qid = r.get("question_id", "")
                phi_entry = state.phi_signals.get(qid, {})
                if phi_entry:
                    phi = float(phi_entry.get("phi", 1.0 if r["is_correct"] else 0.0))
                else:
                    # No chat signal available — fall back to time-based fidelity
                    phi = 0.5 if r.get("is_likely_guess") else (1.0 if r["is_correct"] else 0.0)

                # φ-modulated BKT update
                p_after = _bkt_update(p_before_adj, r["is_correct"], p_slip, p_guess, p_transit, phi)

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
                    "phi":            round(phi, 3),
                }
    except Exception as exc:
        logger.error(f"BKT update failed: {exc}")
    finally:
        driver.close()

    logger.info(f"BKT: updated mastery for {len(mastery_updates)} nodes")
    return {"results": updated_results, "mastery_updates": mastery_updates}
