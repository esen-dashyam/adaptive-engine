"""
LangGraph node functions for the Adaptive Assessment Agent.

Each function receives the full AssessmentState and returns a dict
with only the keys it modifies. LangGraph merges those diffs in.

Flow:
  select_standards
    ↓
  fetch_rag_context
    ↓
  generate_questions          ← [API pauses here, returns questions to UI]
    ↓
  [student submits answers]
    ↓
  evaluate_answers
    ↓
  update_mastery              ← writes BKT to Neo4j + Postgres
    ↓
  analyze_gaps                ← strong Cypher query, writes to Postgres
    ↓
  generate_remediation  (if gaps exist)
    ↓
  write_report
    ↓
  END
"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger

from backend.app.agent.state import AssessmentState
from backend.app.core.settings import settings


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _neo4j_driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )


def _gemini():
    from backend.app.llm.gemini_service import GeminiService
    return GeminiService()


# ─────────────────────────────────────────────────────────────────────────────
# Node 1 — select_standards
# ─────────────────────────────────────────────────────────────────────────────

def select_standards(state: AssessmentState) -> dict:
    """
    Query Neo4j to select standards for the assessment.

    Strategy:
      1. For each grade in {grade-1, grade} pick top standards from LC KG.
      2. Grade-1 standards → "prerequisite" category (DOK 1 gap checks).
      3. Grade standards   → "target" category (DOK 2-3 grade-level work).
      4. Respect existing BKT mastery — de-prioritise already-mastered standards.
    """
    grade_num = state.grade.replace("K", "").strip()
    try:
        grade_int = int(grade_num)
    except ValueError:
        grade_int = 1

    prereq_grade = str(max(grade_int - 1, 1))
    target_grade = grade_num

    driver = _neo4j_driver()
    target_nodes: list[dict] = []
    prereq_nodes: list[dict] = []

    cypher_standards = """
    MATCH (n:StandardsFrameworkItem)
    WHERE n.normalizedStatementType IN ['Standard', 'Learning Target']
      AND any(g IN n.gradeLevelList WHERE g = $grade)
      AND ($subject IS NULL OR n.academicSubject CONTAINS $subject)
    WITH n
    ORDER BY rand()
    LIMIT $limit
    RETURN n.identifier     AS identifier,
           n.statementCode  AS code,
           n.description    AS description,
           n.academicSubject AS subject,
           n.gradeLevel     AS grade_level
    """

    params_subject = state.subject if state.subject else None

    try:
        with driver.session(database=settings.neo4j_database) as s:
            # target grade standards
            res = s.run(
                cypher_standards,
                grade=target_grade,
                subject=params_subject,
                limit=settings.agent_max_questions,
            )
            for r in res:
                node = dict(r)
                node["question_category"] = "target"
                node["understanding_strength"] = 1.0
                target_nodes.append(node)

            # prerequisite grade standards
            res = s.run(
                cypher_standards,
                grade=prereq_grade,
                subject=params_subject,
                limit=max(2, settings.agent_max_questions // 3),
            )
            for r in res:
                node = dict(r)
                node["question_category"] = "prerequisite"
                node["understanding_strength"] = 0.85
                prereq_nodes.append(node)

    except Exception as exc:
        logger.error(f"[select_standards] Neo4j error: {exc}")
        return {"error": str(exc), "phase": "error"}
    finally:
        driver.close()

    all_nodes = prereq_nodes + target_nodes
    logger.info(
        f"[select_standards] grade={state.grade} "
        f"prereqs={len(prereq_nodes)} targets={len(target_nodes)}"
    )
    return {
        "target_standards": target_nodes,
        "prerequisite_standards": prereq_nodes,
        "all_nodes": all_nodes,
        "phase": "rag",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 — fetch_rag_context
# ─────────────────────────────────────────────────────────────────────────────

def fetch_rag_context(state: AssessmentState) -> dict:
    """
    GraphRAG: for every selected node, pull from Neo4j:
      - prerequisite chain (buildsTowards / hasChild)
      - forward progression
      - domain siblings
      - existing question stems (for diversity)
      - full-text related standards
    """
    if not state.all_nodes:
        return {"rag_context_map": {}, "rag_prompt_block": "", "phase": "generate"}

    try:
        from backend.app.rag.graph_rag import retrieve_rag_context
        rag_map, rag_block = retrieve_rag_context(
            state.all_nodes,
            max_prereqs=settings.rag_graph_hop_depth,
        )
    except Exception as exc:
        logger.warning(f"[fetch_rag_context] Non-fatal RAG error: {exc}")
        rag_map, rag_block = {}, ""

    logger.info(f"[fetch_rag_context] enriched {len(rag_map)} nodes")
    return {
        "rag_context_map": rag_map,
        "rag_prompt_block": rag_block,
        "phase": "generate",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node 3 — generate_questions
# ─────────────────────────────────────────────────────────────────────────────

def generate_questions(state: AssessmentState) -> dict:
    """
    Gemini generates one multiple-choice question per selected standard,
    using the GraphRAG context block for curriculum alignment.
    """
    nodes = state.all_nodes
    if not nodes:
        return {"questions": [], "phase": "await_answers", "error": "no standards selected"}

    grade_label = f"Grade {state.grade}"
    subject_name = "Mathematics" if "math" in state.subject.lower() else state.subject

    node_lines = []
    for i, node in enumerate(nodes):
        code = node.get("code") or node.get("identifier", "")
        desc = node.get("description", "")
        cat = node.get("question_category", "target")
        strength = node.get("understanding_strength", 1.0)
        node_lines.append(
            f"  {i+1}. [{cat.upper()}] [strength={strength:.2f}] "
            f"{code}: {desc[:180]}"
        )

    prompt = (
        f"You are an Educational Assessment Architect generating a "
        f"{state.framework}-aligned K1-K8 assessment.\n\n"
        f"Student: {grade_label} | Subject: {subject_name} | "
        f"Framework: {state.framework} | Jurisdiction: {state.state_jurisdiction}\n\n"
        + (f"{state.rag_prompt_block}\n\n" if state.rag_prompt_block else "")
        + f"Standards to assess:\n" + "\n".join(node_lines) + "\n\n"
        f"Generate exactly {len(nodes)} multiple-choice questions.\n\n"
        "Rules:\n"
        "1. PREREQUISITE questions: DOK 1 — direct recall, age-appropriate\n"
        "2. TARGET questions: DOK 2-3 — problem-solving and application\n"
        "3. Each question tests ONE standard — write a real curriculum problem\n"
        "4. Exactly 4 options (A, B, C, D) with ONE correct answer\n"
        "5. Do NOT repeat any question stems listed under 'AVOID repeating' above\n\n"
        "Return ONLY a valid JSON array. Each element:\n"
        '{"id":"<uuid>","type":"multiple_choice","question":"text",'
        '"options":["A. ...","B. ...","C. ...","D. ..."],'
        '"answer":"A","dok_level":1,"category":"prerequisite|target",'
        '"standard_code":"code","node_index":0}'
    )

    questions: list[dict] = []
    try:
        gemini = _gemini()
        raw = gemini.generate_content(prompt)
        if not raw:
            raise ValueError("Gemini returned empty response")
        import json, re
        raw = raw.strip()
        if "```" in raw:
            raw = re.sub(r"```[a-z]*\n?", "", raw).replace("```", "")
        si, ei = raw.find("["), raw.rfind("]") + 1
        questions = json.loads(raw[si:ei]) if si >= 0 and ei > si else []
        if not isinstance(questions, list):
            questions = []
    except Exception as exc:
        logger.error(f"[generate_questions] Gemini error: {exc}")
        return {"questions": [], "phase": "await_answers", "error": str(exc)}

    # Ensure every question has a UUID
    for q in questions:
        if not q.get("id"):
            q["id"] = str(uuid.uuid4())

    logger.info(f"[generate_questions] generated {len(questions)} questions")
    return {"questions": questions, "phase": "await_answers"}


# ─────────────────────────────────────────────────────────────────────────────
# Node 4 — evaluate_answers
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_answers(state: AssessmentState) -> dict:
    """
    Compare submitted answers against correct answers.
    Pulls mastery_before from Neo4j SkillState for each standard.
    """
    question_map = {q["id"]: q for q in state.questions}
    results: list[dict] = []
    correct_count = 0

    driver = _neo4j_driver()

    def get_prior(code: str) -> float:
        try:
            with driver.session(database=settings.neo4j_database) as s:
                res = s.run(
                    """
                    MATCH (:Student {student_id: $sid})-[sk:SKILL_STATE]->(n:StandardsFrameworkItem)
                    WHERE n.identifier = $code OR n.statementCode = $code
                    RETURN sk.p_mastery AS p
                    """,
                    sid=state.student_id,
                    code=code,
                )
                rec = res.single()
                return float(rec["p"]) if rec else settings.student_initial_mastery
        except Exception:
            return settings.student_initial_mastery

    try:
        for ans in state.submitted_answers:
            qid = ans.get("question_id", "")
            selected = (ans.get("selected_answer") or "").strip().upper()
            q = question_map.get(qid)
            if not q:
                continue

            correct = (q.get("answer") or "").strip().upper()
            is_correct = selected == correct
            if is_correct:
                correct_count += 1

            code = q.get("standard_code", "")
            mastery_before = get_prior(code)

            results.append({
                **q,
                "selected_answer": selected,
                "is_correct": is_correct,
                "mastery_before": mastery_before,
                "mastery_after": mastery_before,   # updated in next node
            })
    finally:
        driver.close()

    score = correct_count / len(results) if results else 0.0
    logger.info(
        f"[evaluate_answers] score={score:.2f} "
        f"({correct_count}/{len(results)} correct)"
    )
    return {"results": results, "score": score, "phase": "update_mastery"}


# ─────────────────────────────────────────────────────────────────────────────
# Node 5 — update_mastery
# ─────────────────────────────────────────────────────────────────────────────

def update_mastery(state: AssessmentState) -> dict:
    """
    Run BKT update for each evaluated standard and persist to:
      1. Neo4j (primary) — MERGE (:SkillState) with updated mastery_prob
      2. Postgres (secondary) — upsert MasteryRecord via MasteryRepository
    """
    from backend.app.student.bayesian_tracker import BayesianSkillTracker

    tracker = BayesianSkillTracker()
    mastery_updates: dict[str, float] = {}
    updated_results = []

    for r in state.results:
        code = r.get("standard_code", "")
        if not code:
            updated_results.append(r)
            continue

        try:
            bkt_result = tracker.update_skill(
                student_id=state.student_id,
                node_identifier=code,
                is_correct=r["is_correct"],
            )
            new_prob = bkt_result["p_mastery"]
        except Exception as exc:
            logger.warning(f"[update_mastery] BKT update failed for {code}: {exc}")
            new_prob = r.get("mastery_before", settings.student_initial_mastery)

        mastery_updates[code] = new_prob
        updated_results.append({**r, "mastery_after": new_prob})

    try:
        tracker.close()
    except Exception:
        pass

    logger.info(f"[update_mastery] updated {len(mastery_updates)} skill states")
    return {
        "mastery_updates": mastery_updates,
        "results": updated_results,
        "phase": "analyze",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node 6 — analyze_gaps
# ─────────────────────────────────────────────────────────────────────────────

def analyze_gaps(state: AssessmentState) -> dict:
    """
    Strong Neo4j Cypher query to identify blocking knowledge gaps.

    Query logic:
      1. For each standard tested below mastery_threshold,
         count how many downstream standards (via buildsTowards / hasChild)
         it blocks — "blocked_downstream".
      2. Sort by blocked_downstream DESC, mastery_prob ASC.
      3. Return top N gaps.

    This is the post-assessment analysis the agent uses to decide what to remediate.
    """
    if not state.mastery_updates:
        return {"gaps": [], "phase": "write_report"}

    threshold = settings.agent_mastery_threshold
    limit = settings.agent_gap_limit

    # Build list of (code, mastery) pairs below threshold
    weak = [
        (code, prob)
        for code, prob in state.mastery_updates.items()
        if prob < threshold
    ]

    if not weak:
        logger.info("[analyze_gaps] No gaps found — student mastered all tested standards")
        return {"gaps": [], "phase": "write_report"}

    weak_codes = [c for c, _ in weak]
    mastery_map = dict(weak)

    # ── Strong Cypher query ───────────────────────────────────────────────────
    gap_cypher = """
    UNWIND $codes AS code
    MATCH (src:StandardsFrameworkItem)
    WHERE src.identifier = code OR src.statementCode = code
    OPTIONAL MATCH (src)-[:BUILDS_TOWARDS|HAS_CHILD*1..3]->(downstream:StandardsFrameworkItem)
    WITH src,
         code,
         count(DISTINCT downstream) AS blocked_downstream
    RETURN
        src.identifier   AS identifier,
        src.statementCode AS standard_code,
        src.description   AS description,
        src.academicSubject AS subject,
        src.gradeLevel    AS grade_level,
        blocked_downstream
    ORDER BY blocked_downstream DESC
    LIMIT $limit
    """

    driver = _neo4j_driver()
    gaps: list[dict] = []
    try:
        with driver.session(database=settings.neo4j_database) as s:
            res = s.run(gap_cypher, codes=weak_codes, limit=limit)
            for r in res:
                code = r["standard_code"] or r["identifier"]
                gaps.append({
                    "identifier": r["identifier"],
                    "standard_code": code,
                    "description": r["description"],
                    "subject": r["subject"],
                    "grade_level": r["grade_level"],
                    "mastery_prob": mastery_map.get(r["identifier"],
                                   mastery_map.get(code, 0.0)),
                    "blocked_downstream": r["blocked_downstream"],
                })
    except Exception as exc:
        logger.error(f"[analyze_gaps] Neo4j error: {exc}")
        # Fall back to simple list of weak codes
        for code, prob in weak:
            gaps.append({
                "identifier": code,
                "standard_code": code,
                "description": "",
                "mastery_prob": prob,
                "blocked_downstream": 0,
            })
    finally:
        driver.close()

    logger.info(f"[analyze_gaps] found {len(gaps)} blocking gaps")
    return {"gaps": gaps, "phase": "remediate" if gaps else "write_report"}


# ─────────────────────────────────────────────────────────────────────────────
# Node 7 — generate_remediation
# ─────────────────────────────────────────────────────────────────────────────

def generate_remediation(state: AssessmentState) -> dict:
    """
    Gemini generates a focused remediation plan for each identified gap.

    Per gap:
      - A short plain-English explanation of the concept
      - 2-3 DOK-1 / DOK-2 practice exercises with answers
      - A learning tip

    The remediation plan is structured so the frontend can render it
    step-by-step as a guided practice session.
    """
    if not state.gaps:
        return {"remediation_plan": [], "phase": "write_report"}

    import json, re

    gap_lines = "\n".join(
        f"  {i+1}. [{g['standard_code']}] mastery={g['mastery_prob']:.2f} "
        f"blocks {g['blocked_downstream']} downstream: {g['description'][:150]}"
        for i, g in enumerate(state.gaps)
    )

    prompt = (
        f"You are an adaptive tutor for {state.grade} grade {state.subject} students.\n\n"
        f"The student struggled with these standards (below {settings.agent_mastery_threshold:.0%} mastery):\n"
        f"{gap_lines}\n\n"
        "For EACH standard, generate a remediation block containing:\n"
        "  1. A clear, friendly 2-3 sentence explanation aimed at the grade level\n"
        "  2. Exactly 2 practice exercises (short questions with the correct answer)\n"
        "  3. One concrete learning tip\n\n"
        "Return ONLY valid JSON — an array where each element is:\n"
        '{"standard_code":"...","explanation":"...","exercises":'
        '[{"question":"...","answer":"..."}],"tip":"..."}'
    )

    remediation_plan: list[dict] = []
    try:
        gemini = _gemini()
        raw = gemini.generate_content(prompt)
        if not raw:
            raise ValueError("Gemini returned empty response")
        raw = raw.strip()
        if "```" in raw:
            raw = re.sub(r"```[a-z]*\n?", "", raw).replace("```", "")
        si, ei = raw.find("["), raw.rfind("]") + 1
        remediation_plan = json.loads(raw[si:ei]) if si >= 0 and ei > si else []
        if not isinstance(remediation_plan, list):
            remediation_plan = []
    except Exception as exc:
        logger.error(f"[generate_remediation] Gemini error: {exc}")

    logger.info(f"[generate_remediation] built {len(remediation_plan)} remediation blocks")
    return {"remediation_plan": remediation_plan, "phase": "write_report"}


# ─────────────────────────────────────────────────────────────────────────────
# Node 8 — write_report  (persists everything to Postgres)
# ─────────────────────────────────────────────────────────────────────────────

async def write_report(state: AssessmentState) -> dict:
    """
    Persist the complete assessment run to Postgres:
      - Finalise AssessmentSession (score, gap_analysis, remediation_plan, phase=done)
      - Save each AssessmentAnswer row
      - Dual-write MasteryRecord updates

    This node is ASYNC because it uses SQLAlchemy async sessions.
    """
    if not state.pg_session_id or not state.pg_student_uuid:
        logger.warning("[write_report] No Postgres session ID — skipping persistence")
        return {"phase": "done"}

    from backend.app.db.engine import get_async_session
    from backend.app.db.repositories.assessment_repo import AssessmentRepository
    from backend.app.db.repositories.mastery_repo import MasteryRepository

    session_uuid = uuid.UUID(state.pg_session_id)
    student_uuid = uuid.UUID(state.pg_student_uuid)

    gap_analysis_payload = {
        "gaps": state.gaps,
        "score": state.score,
        "mastery_updates": state.mastery_updates,
        "total_questions": len(state.results),
        "correct_count": sum(1 for r in state.results if r.get("is_correct")),
    }

    remediation_payload = {
        "plan": state.remediation_plan,
        "gap_count": len(state.gaps),
    }

    try:
        async for db in get_async_session():
            assess_repo = AssessmentRepository(db)
            mastery_repo = MasteryRepository(db)

            # Save individual answers
            for r in state.results:
                await assess_repo.save_answer(
                    session_id=session_uuid,
                    question_id=r.get("id", ""),
                    question_text=r.get("question", ""),
                    standard_code=r.get("standard_code", ""),
                    category=r.get("category", "target"),
                    dok_level=r.get("dok_level", 1),
                    student_answer=r.get("selected_answer", ""),
                    correct_answer=r.get("answer", ""),
                    is_correct=bool(r.get("is_correct")),
                    mastery_before=float(r.get("mastery_before", 0.1)),
                    mastery_after=float(r.get("mastery_after", 0.1)),
                )

            # Dual-write mastery updates
            for code, new_prob in state.mastery_updates.items():
                result_for_code = next(
                    (r for r in state.results if r.get("standard_code") == code), None
                )
                is_correct = bool(result_for_code.get("is_correct")) if result_for_code else False
                await mastery_repo.upsert(
                    student_id=student_uuid,
                    standard_code=code,
                    is_correct=is_correct,
                    mastery_prob=new_prob,
                    subject=state.subject,
                    grade=state.grade,
                )

            # Finalise session
            await assess_repo.finalize_session(
                session_id=session_uuid,
                score=state.score,
                gap_analysis=gap_analysis_payload,
                remediation_plan=remediation_payload,
                phase="done",
            )

            logger.info(
                f"[write_report] Persisted session {state.pg_session_id} "
                f"score={state.score:.2f} gaps={len(state.gaps)}"
            )
            break  # only need one iteration of the generator

    except Exception as exc:
        logger.error(f"[write_report] Postgres persistence failed: {exc}")

    return {"phase": "done"}


# ─────────────────────────────────────────────────────────────────────────────
# Routing function
# ─────────────────────────────────────────────────────────────────────────────

def route_after_gaps(state: AssessmentState) -> str:
    """Branch: remediate if gaps exist, else write_report."""
    if state.gaps:
        return "remediate"
    return "write_report"
