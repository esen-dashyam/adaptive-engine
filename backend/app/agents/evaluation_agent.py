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


MAX_MASTERY_DROP_PER_CYCLE = 0.15
MAX_LLM_PENALTY_PER_CYCLE = 0.10
SE_REFERENCE = 0.30
MIN_NEGATIVE_PHI_SCALE = 0.25


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
    # Safety floor: a single LLM observation cannot erase more than 0.15 of
    # accumulated mastery. This prevents a mis-interpreted chat message from
    # wiping out weeks of student progress in one turn.
    updated = max(p_mastery - MAX_MASTERY_DROP_PER_CYCLE, updated)
    return max(0.01, min(0.999, updated))


def _cap_llm_penalty(penalty: float) -> float:
    return max(0.0, min(MAX_LLM_PENALTY_PER_CYCLE, float(penalty)))


def _dampen_negative_phi(phi: float, se: float | None) -> float:
    phi = max(-1.0, min(1.0, float(phi)))
    if phi >= 0.0:
        return phi
    se_value = max(SE_REFERENCE, float(se or SE_REFERENCE))
    scale = max(MIN_NEGATIVE_PHI_SCALE, min(1.0, SE_REFERENCE / se_value))
    return max(-1.0, phi * scale)


def _apply_total_drop_floor(p_before: float, p_after: float) -> float:
    return max(float(p_before) - MAX_MASTERY_DROP_PER_CYCLE, float(p_after))


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
# Grader prompt — strict but fair judge for open-ended responses
# ─────────────────────────────────────────────────────────────────────────────

_GRADER_PROMPT = """\
You are a smart, fair grader for a K-8 adaptive math assessment. Your job is to determine whether
the student demonstrates understanding of the core concept — NOT to reward long answers or penalize short ones.

### CORE PRINCIPLE:
**Brevity ≠ wrong. Verbosity ≠ correct.**
A short answer that shows the right mathematical concept is correct.
A long answer that dances around the concept without demonstrating it is wrong.

### INPUTS PER QUESTION:
1. Question text
2. Standard Code & DOK Level
3. Grading Rubric — what a correct answer must demonstrate
4. Answer Key — the ideal answer (for reference, not as a template the student must match)
5. Student Response — the raw text the student typed
6. Time Taken (ms)

### YOUR TASKS:
1. **is_correct** (boolean): Does the student's response demonstrate the core mathematical concept
   tested by this standard?
   - YES if they show the right operation, relationship, or concept — even if brief or not fully worded.
   - NO if they show a wrong concept, wrong operation, or give a numeric result when the question
     asks for meaning/explanation of a concept.
   - Examples:
     • "4 times 6" for an area/tiles question → is_correct=true (shows correct multiplication; phi=0.5 for incompleteness)
     • "24" for "what does 3×8 mean?" → is_correct=false (gave the product, not the meaning of multiplication)
     • "multiply length times width" → is_correct=true, phi=1.0

2. **phi** (float, -1.0 to 1.0) — signals quality/confidence, independent of is_correct:
   - 1.0 = Complete and clear: concept fully demonstrated
   - 0.5 = Correct concept shown but answer is partial, incomplete, or expression-only (no full sentence needed)
   - 0.0 = Wrong, no sign of confusion — just an incorrect attempt
   - -0.5 = Wrong with a traceable misconception shown in their work
   - -1.0 = Blank, "I don't know", or complete breakdown

3. **reasoning** (string): 1-sentence explanation of the grading decision.
4. **misconception_flag** (string or null): If is_correct=false, name the exact cognitive error
   (e.g. "Gave product instead of explaining what multiplication means"). If is_correct=true → null.

### QUESTIONS TO GRADE:
{blocks}

### OUTPUT FORMAT:
Return ONLY a valid JSON object (no markdown, no text outside JSON):
{{
  "<question_id>": {{"is_correct": bool, "phi": float, "reasoning": "...", "misconception_flag": "string or null"}},
  ...
}}

Rules:
- Empty or blank student_response: is_correct=false, phi=-1.0, misconception_flag="Did not attempt"
- Wrong answer always caps phi at 0.0
- Correct answer under 3000 ms on DOK≥2: phi ≤ 0.5
"""


# ─────────────────────────────────────────────────────────────────────────────
# Node 1 — score_answers
# ─────────────────────────────────────────────────────────────────────────────

def _grade_mc(q: dict, sub: dict) -> tuple[bool, float, str, str | None]:
    """
    Grade a multiple-choice question directly — no LLM needed.
    Returns (is_correct, phi, reasoning, misconception).
    """
    correct_letter = (q.get("answer") or "").strip().upper()
    selected       = (sub.get("selected_answer") or sub.get("student_response") or "").strip().upper()
    time_ms        = float(sub.get("time_ms", 0.0))

    # Normalise: accept "A", "a", "(A)", "A." etc.
    if selected and selected[0] in "ABCD":
        selected = selected[0]

    is_correct = bool(selected) and selected == correct_letter

    if not selected:
        return False, -1.0, "No option selected", "Did not attempt"
    if is_correct:
        # Very fast on DOK≥2 is suspicious but MC DOK 1 is expected to be quick
        phi = 0.7 if time_ms > 0 and time_ms < 3000 else 1.0
        return True, phi, f"Correct option {selected} selected", None
    else:
        wrong_text = (q.get("options") or {}).get(selected, selected)
        return False, 0.0, f"Selected {selected} (incorrect); correct was {correct_letter}", f"Chose distractor: {wrong_text}"


def score_answers(state: AssessmentState) -> dict:
    """
    Grade mixed MC + open-ended answers.

    • Multiple-choice questions are graded instantly (no LLM call).
    • Open-ended questions are sent to Gemini in a single batch call.

    phi_signals are pre-populated so chat_to_signal skips re-analysis.
    """
    logger.info("━" * 60)
    logger.info("  PHASE B — STEP 1/7 │ score_answers  (Mixed MC + Open-Ended Grader)")
    logger.info(f"  {len(state.submitted_answers)} answers submitted")
    logger.info("━" * 60)

    q_map = {q["id"]: q for q in state.questions}

    # ── Separate MC from open-ended ────────────────────────────────────────────
    mc_subs:      list[dict] = []
    openend_subs: list[dict] = []
    for sub in state.submitted_answers:
        qid = sub.get("question_id", "")
        q   = q_map.get(qid, {})
        if q.get("type") == "multiple_choice":
            mc_subs.append(sub)
        else:
            openend_subs.append(sub)

    logger.info(f"  MC: {len(mc_subs)} questions | Open-ended: {len(openend_subs)} questions")

    # ── Grade MC instantly ─────────────────────────────────────────────────────
    mc_grades: dict[str, dict] = {}
    for sub in mc_subs:
        qid = sub.get("question_id", "")
        q   = q_map.get(qid, {})
        is_correct, phi, reasoning, misconception = _grade_mc(q, sub)
        mc_grades[qid] = {
            "is_correct":       is_correct,
            "phi":              phi,
            "reasoning":        reasoning,
            "misconception_flag": misconception,
        }

    # ── LLM batch grades open-ended questions ──────────────────────────────────
    oe_grades: dict[str, dict] = {}
    if openend_subs:
        blocks: list[str] = []
        for sub in openend_subs:
            qid      = sub.get("question_id", "")
            q        = q_map.get(qid, {})
            response = (sub.get("student_response") or sub.get("selected_answer") or "").strip()
            time_ms  = float(sub.get("time_ms", 0.0))
            blocks.append(
                f"question_id: {qid}\n"
                f"  standard: {q.get('standard_code', '')} | DOK: {q.get('dok_level', 2)}\n"
                f"  question: {(q.get('question') or '')[:200]}\n"
                f"  rubric: {q.get('rubric', '')}\n"
                f"  answer_key: {q.get('answer_key', q.get('answer', ''))}\n"
                f"  student_response: \"{response}\"\n"
                f"  time_ms: {time_ms:.0f}\n"
            )

        llm = get_llm()
        try:
            raw = llm.generate_json(_GRADER_PROMPT.format(blocks="\n".join(blocks)))
            if isinstance(raw, dict):
                for qid, entry in raw.items():
                    if isinstance(entry, dict):
                        oe_grades[qid] = entry
            logger.info(f"LLM Grader returned verdicts for {len(oe_grades)}/{len(openend_subs)} open-ended questions")
        except Exception as exc:
            logger.warning(f"LLM batch grader failed — heuristic fallback: {exc}")

    # ── Build results + pre-populate phi_signals ───────────────────────────────
    results: list[dict] = []
    phi_signals: dict[str, dict] = {}
    time_per_question: dict[str, float] = {}
    correct_count = 0

    for sub in state.submitted_answers:
        qid     = sub.get("question_id", "")
        q       = q_map.get(qid, {})
        is_mc   = q.get("type") == "multiple_choice"
        time_ms = float(sub.get("time_ms", 0.0))
        dok     = int(q.get("dok_level", 2) or 2)

        if is_mc:
            response  = (sub.get("selected_answer") or sub.get("student_response") or "").strip().upper()
            if response and response[0] in "ABCD":
                response = response[0]
            verdict = mc_grades.get(qid, {})
        else:
            response = (sub.get("student_response") or sub.get("selected_answer") or "").strip()
            verdict  = oe_grades.get(qid)

        if verdict:
            is_correct    = bool(verdict.get("is_correct", False))
            phi_raw       = float(verdict.get("phi", 0.0))
            reasoning     = verdict.get("reasoning", "")
            misconception = verdict.get("misconception_flag")
        else:
            # Heuristic fallback (open-ended only — MC always has a verdict)
            is_correct    = bool(response)
            phi_raw       = 0.5 if is_correct else (-1.0 if not response else 0.0)
            reasoning     = "Heuristic fallback (grader unavailable)"
            misconception = None if is_correct else "Could not determine (grader unavailable)"

        phi_raw = max(-1.0, min(1.0, phi_raw))

        if is_correct:
            correct_count += 1
        if qid:
            time_per_question[qid] = time_ms

        phi_signals[qid] = {
            "phi":         phi_raw,
            "reason":      reasoning,
            "gap_tag":     misconception,
            "target_node": None,
        }

        correct_answer = q.get("answer_key") or q.get("answer") or ""
        if is_mc:
            # For MC store the letter + text of the correct answer
            correct_letter = (q.get("answer") or "").strip().upper()
            correct_text   = (q.get("options") or {}).get(correct_letter, "")
            correct_answer = f"{correct_letter}: {correct_text}" if correct_text else correct_letter

        results.append({
            "question_id":   qid,
            "question":      q.get("question", ""),
            "correct_answer": correct_answer,
            "student_answer": response,
            "chat_message":  response,
            "is_correct":    is_correct,
            "is_likely_guess": is_correct and 0 < time_ms < 4000 and dok >= 2 and not is_mc,
            "time_ms":       time_ms,
            "question_type": "multiple_choice" if is_mc else "open_ended",
            "grader_reasoning":     reasoning,
            "grader_misconception": misconception,
            "grader_phi":           phi_raw,
            "category":      q.get("category", "target"),
            "dok_level":     dok,
            "standard_code": q.get("standard_code", ""),
            "node_ref":      q.get("node_ref", ""),
            "beta":          q.get("beta", 0.0),
            "mastery_before": 0.0,
            "mastery_after":  0.0,
        })

    score = correct_count / max(len(results), 1)
    logger.info(
        f"Grader: score={score:.2%}  ({correct_count}/{len(results)}) "
        f"[MC: {len(mc_subs)} instant, OE: {len(openend_subs)} LLM]"
    )
    return {
        "results":           results,
        "score":             score,
        "time_per_question": time_per_question,
        "phi_signals":       phi_signals,
    }


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
        penalty = _cap_llm_penalty(float(item.get("mastery_penalty", 0.15)))
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
                                      p_slip=p_slip, p_guess=p_guess, p_transit=p_transit,
                                      phi=0.0)
                p_after = _apply_total_drop_floor(p_before, p_after)

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
                penalty = _cap_llm_penalty(state.misconception_weights.get(std_code, 0.0))
                p_before_adj = max(0.05, p_before - penalty)

                # Pull φ from the Signal Bridge output (defaults by correctness if absent)
                qid = r.get("question_id", "")
                phi_entry = state.phi_signals.get(qid, {})
                if phi_entry:
                    phi_raw = float(phi_entry.get("phi", 1.0 if r["is_correct"] else 0.0))
                else:
                    # No chat signal available — fall back to time-based fidelity
                    phi_raw = 0.5 if r.get("is_likely_guess") else (1.0 if r["is_correct"] else 0.0)
                phi = _dampen_negative_phi(phi_raw, state.se)

                # φ-modulated BKT update
                p_after = _bkt_update(p_before_adj, r["is_correct"], p_slip, p_guess, p_transit, phi)
                p_after = _apply_total_drop_floor(p_before, p_after)

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
                    "phi_raw":        round(phi_raw, 3),
                    "phi":            round(phi, 3),
                    "misconception_penalty": round(penalty, 3),
                }
    except Exception as exc:
        logger.error(f"BKT update failed: {exc}")
    finally:
        driver.close()

    logger.info(f"BKT: updated mastery for {len(mastery_updates)} nodes")
    return {"results": updated_results, "mastery_updates": mastery_updates}
