"""
Assessment Agent — Phase A LangGraph.

Flow:
  select_standards_irt → fetch_rag_context → generate_questions → END

Key enhancements over the legacy agent:
  - IRT-aware node selection: ranks candidates by Fisher Information at θ
  - Assigns β (difficulty logit) to every question for Rasch tracking
  - Prerequisite map built from PRECEDES relationships for IRT Selector
  - Vertex AI (via VertexLLM) generates curriculum-aligned questions
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from backend.app.agent.state import AssessmentState
from backend.app.agents.irt_selector import assign_difficulties, rank_nodes_by_information, build_prerequisite_map
from backend.app.agents.vertex_llm import get_llm
from backend.app.core.settings import settings


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _neo4j():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Node 1 — select_standards_irt
# ─────────────────────────────────────────────────────────────────────────────

# State abbreviation → full jurisdiction name (matches Neo4j n.jurisdiction)
_STATE_ABBREV: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}


def _parse_grade_subject(grade: str, subject: str, state_jurisdiction: str) -> tuple[str, str, str, str, str]:
    """
    Convert frontend values to the property values stored in Neo4j.

    Returns: (grade_num, prereq_grade, subject_name, jurisdiction, framework)
      grade_num   : "1".."8"  (strips the leading K)
      prereq_grade: one grade below, min "1"
      subject_name: "Mathematics" | "English Language Arts"
      jurisdiction: full state name or "Multi-State"
    """
    grade_num = grade.upper().replace("K", "").strip()
    try:
        grade_int = int(grade_num)
    except ValueError:
        grade_int = 1
        grade_num = "1"
    prereq_grade = str(max(grade_int - 1, 1))
    subject_name = "Mathematics" if subject.lower() == "math" else "English Language Arts"
    jurisdiction = _STATE_ABBREV.get(state_jurisdiction, state_jurisdiction)  # "Multi-State" passes through
    return grade_num, prereq_grade, subject_name, jurisdiction


def select_standards_irt(state: AssessmentState) -> dict:  # noqa: C901
    logger.info("━" * 60)
    logger.info("  PHASE A — STEP 1/3 │ select_standards_irt")
    logger.info(f"  student={state.student_id}  grade={state.grade}  subject={state.subject}  θ={state.theta:+.2f}")
    logger.info("━" * 60)
    """
    Query Neo4j for candidate standards using the real schema, then rank by
    Fisher Information at the student's current θ.

    Real property names on StandardsFrameworkItem:
      n.gradeLevelList  — list<string> e.g. ["1", "2"]
      n.academicSubject — "Mathematics" | "English Language Arts"
      n.jurisdiction    — "Multi-State", "Texas", "California" …
      n.statementCode   — e.g. "1.NBT.B.3"
      n.normalizedStatementType — must be 'Standard'
    """
    grade_num, prereq_grade, subject_name, jurisdiction = _parse_grade_subject(
        state.grade, state.subject, state.state_jurisdiction
    )

    driver = _neo4j()
    try:
        with driver.session() as session:

            def _query_nodes(grade: str, jur: str, category: str, dok: int, limit: int):
                res = session.run(
                    """
                    MATCH (n:StandardsFrameworkItem)
                    WHERE n.jurisdiction = $jur
                      AND n.academicSubject = $subject
                      AND ANY(g IN n.gradeLevelList WHERE g = $grade)
                      AND n.normalizedStatementType = 'Standard'
                      AND NOT (n.statementCode STARTS WITH 'MP')
                      AND size(n.description) > 20
                    RETURN n.identifier AS identifier,
                           n.statementCode AS code,
                           n.description AS description,
                           n.gradeLevelList AS gradeLevelList
                    ORDER BY rand() LIMIT $lim
                    """,
                    jur=jur, subject=subject_name, grade=grade, lim=limit,
                )
                return [
                    {**r.data(), "grade": grade, "category": category, "dok_level": dok,
                     "subject": subject_name}
                    for r in res
                ]

            target_nodes = _query_nodes(grade_num, jurisdiction, "target", 2, 20)

            # Fallback to Multi-State if state-specific found nothing
            if len(target_nodes) < 3 and jurisdiction != "Multi-State":
                target_nodes += _query_nodes(grade_num, "Multi-State", "target", 2, 20)

            prereq_nodes = _query_nodes(prereq_grade, jurisdiction, "prerequisite", 1, 12)
            if len(prereq_nodes) < 2 and jurisdiction != "Multi-State":
                prereq_nodes += _query_nodes(prereq_grade, "Multi-State", "prerequisite", 1, 12)

            # Prerequisite graph edges for IRT constraint
            edge_result = session.run(
                """
                MATCH (a:StandardsFrameworkItem)-[r:BUILDS_TOWARDS|HAS_DEPENDENCY|DEFINES_UNDERSTANDING]->(b:StandardsFrameworkItem)
                WHERE ANY(g IN a.gradeLevelList WHERE g IN [$pg, $tg])
                  AND ANY(g IN b.gradeLevelList WHERE g IN [$pg, $tg])
                  AND a.academicSubject = $subject
                RETURN a.identifier AS source, b.identifier AS target,
                       coalesce(r.conceptual_weight, r.understanding_strength, 0.7) AS weight, type(r) AS rel_type
                LIMIT 200
                """,
                pg=prereq_grade, tg=grade_num, subject=subject_name,
            )
            edges = [r.data() for r in edge_result]

    finally:
        driver.close()

    all_candidates = prereq_nodes + target_nodes
    if not all_candidates:
        logger.warning(
            f"No standards found — grade={grade_num} subject={subject_name} "
            f"jurisdiction={jurisdiction}"
        )
        return {
            "target_standards": [], "prerequisite_standards": [], "all_nodes": [],
            "error": f"No standards found in KG for Grade {grade_num} {subject_name}",
        }

    # Deduplicate by identifier
    seen: set[str] = set()
    deduped = []
    for n in all_candidates:
        if n["identifier"] not in seen:
            seen.add(n["identifier"])
            deduped.append(n)
    all_candidates = deduped

    # IRT ranking
    ranked = rank_nodes_by_information(state.theta, all_candidates)

    n_prereq = max(3, min(5, len([n for _, n in ranked if n["category"] == "prerequisite"])))
    n_target = max(7, min(12, len([n for _, n in ranked if n["category"] == "target"])))

    selected_prereq = [n for _, n in ranked if n["category"] == "prerequisite"][:n_prereq]
    selected_target = [n for _, n in ranked if n["category"] == "target"][:n_target]

    # Cap total number of standards/questions using settings.agent_max_questions
    max_q = getattr(settings, "agent_max_questions", None) or (len(selected_prereq) + len(selected_target))
    if len(selected_prereq) + len(selected_target) > max_q:
        total = len(selected_prereq) + len(selected_target)
        prereq_ratio = len(selected_prereq) / total if total else 0.0
        # Aim to preserve prereq/target mix while respecting max_q
        desired_prereq = min(len(selected_prereq), max(1, int(round(max_q * prereq_ratio))))
        desired_target = max_q - desired_prereq
        selected_prereq = selected_prereq[:desired_prereq]
        selected_target = selected_target[:desired_target]

    all_nodes = selected_prereq + selected_target

    difficulties = assign_difficulties(all_nodes)
    _ = build_prerequisite_map(all_nodes)  # builds internal map (used by selector downstream)

    logger.info(
        f"IRT selector: θ={state.theta:+.2f} → {len(selected_target)} target "
        f"+ {len(selected_prereq)} prereq (grade={grade_num}, {subject_name}, {jurisdiction})"
    )

    return {
        "target_standards": selected_target,
        "prerequisite_standards": selected_prereq,
        "all_nodes": all_nodes,
        "question_difficulties": difficulties,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 — fetch_rag_context
# ─────────────────────────────────────────────────────────────────────────────

def fetch_rag_context(state: AssessmentState) -> dict:
    """
    Fetch GraphRAG context for selected nodes: prerequisites, siblings,
    domain labels, existing question stems (to avoid repetition).
    """
    logger.info("━" * 60)
    logger.info(f"  PHASE A — STEP 2/3 │ fetch_rag_context  ({len(state.all_nodes)} nodes)")
    logger.info("━" * 60)
    nodes = state.all_nodes
    if not nodes:
        return {"rag_context_map": {}, "rag_prompt_block": ""}

    identifiers = [n["identifier"] for n in nodes if "identifier" in n]
    driver = _neo4j()
    context_map: dict[str, Any] = {}

    try:
        with driver.session() as session:
            for nid in identifiers:
                ctx: dict[str, Any] = {"prereqs": [], "siblings": [], "existing_questions": []}

                # Prerequisites via BUILDS_TOWARDS / HAS_DEPENDENCY / DEFINES_UNDERSTANDING
                prereq_r = session.run(
                    """
                    MATCH (n:StandardsFrameworkItem {identifier: $id})
                    OPTIONAL MATCH (pre:StandardsFrameworkItem)-[:BUILDS_TOWARDS|HAS_DEPENDENCY|DEFINES_UNDERSTANDING]->(n)
                    RETURN collect(DISTINCT {code: pre.statementCode, description: pre.description})[..4] AS prereqs
                    """,
                    id=nid,
                )
                row = prereq_r.single()
                if row:
                    ctx["prereqs"] = [p for p in (row["prereqs"] or []) if p.get("code")]

                # Domain siblings (same grade + subject)
                sibling_r = session.run(
                    """
                    MATCH (n:StandardsFrameworkItem {identifier: $id})
                    OPTIONAL MATCH (sib:StandardsFrameworkItem)
                    WHERE sib.identifier <> $id
                      AND sib.academicSubject = n.academicSubject
                      AND sib.gradeLevelList = n.gradeLevelList
                      AND sib.normalizedStatementType = 'Standard'
                    RETURN collect(DISTINCT sib.description)[..3] AS siblings
                    """,
                    id=nid,
                )
                row = sibling_r.single()
                if row:
                    ctx["siblings"] = [s for s in (row["siblings"] or []) if s]

                # Existing question stems (for diversity)
                q_r = session.run(
                    """
                    MATCH (q:GeneratedQuestion)-[:TESTS]->(n:StandardsFrameworkItem {identifier: $id})
                    RETURN collect(q.question_text)[..3] AS stems
                    """,
                    id=nid,
                )
                row = q_r.single()
                if row:
                    ctx["existing_questions"] = row["stems"] or []

                context_map[nid] = ctx

    finally:
        driver.close()

    # Build a text block for the LLM prompt
    lines = []
    for node in nodes:
        nid = node["identifier"]
        ctx = context_map.get(nid, {})
        if ctx.get("prereqs"):
            lines.append(f"  {node.get('code','')}: prereqs → {[p['code'] for p in ctx['prereqs']]}")
        if ctx.get("existing_questions"):
            lines.append(f"  AVOID repeating stems: {ctx['existing_questions']}")

    rag_block = "KG Context:\n" + "\n".join(lines) if lines else ""

    return {"rag_context_map": context_map, "rag_prompt_block": rag_block}


# ─────────────────────────────────────────────────────────────────────────────
# Node 3 — generate_questions
# ─────────────────────────────────────────────────────────────────────────────

def generate_questions(state: AssessmentState) -> dict:
    """
    Generate curriculum-aligned questions via Vertex AI / Gemini Flash.
    Uses the IRT-selected node set and RAG context.
    """
    logger.info("━" * 60)
    logger.info(f"  PHASE A — STEP 3/3 │ generate_questions  (asking Gemini for {len(state.all_nodes)} questions)")
    logger.info("━" * 60)
    nodes = state.all_nodes
    if not nodes:
        return {"questions": [], "error": "No nodes to generate questions for"}

    grade_label = f"Grade {state.grade.replace('K','')}"
    subject_name = "Mathematics" if state.subject.lower() == "math" else "English Language Arts"

    node_lines = []
    for n in nodes:
        cat = n.get("category", "target")
        beta = state.question_difficulties.get(n.get("identifier", ""), 0.0)
        code = n.get("code") or n.get("statementCode", "")
        node_lines.append(
            f"  [{cat.upper()} | β={beta:+.1f}] {code}: {n.get('description','')}"
        )

    prompt = f"""You are an Educational Assessment Architect generating a {state.framework}-aligned assessment.

Student: {grade_label} | Subject: {subject_name} | Student Ability θ={state.theta:+.2f}

{state.rag_prompt_block}

Standards to test:
{chr(10).join(node_lines)}

Generate exactly {len(nodes)} multiple-choice questions.

Rules:
1. Use {state.framework} terminology, age-appropriate for {grade_label}
2. PREREQUISITE questions (β < 0): DOK 1 — direct recall of foundational knowledge
3. TARGET questions (β ≥ 0): DOK 2-3 — problem-solving and application
4. Each question tests ONE standard — write a REAL curriculum problem, NOT meta-questions about standards
5. Exactly 4 options (A, B, C, D) with ONE correct answer
6. Questions should match the student's ability: θ={state.theta:+.2f} (0=average, positive=strong, negative=struggling)
7. Do NOT repeat any stems listed under AVOID above

Return ONLY a valid JSON array. Each element must be:
{{"id":"<uuid>","type":"multiple_choice","question":"<text>","options":["A. ...","B. ...","C. ...","D. ..."],"answer":"A","dok_level":1,"category":"prerequisite|target","standard_code":"<code>","node_index":<int>}}"""

    llm = get_llm()
    try:
        questions_raw = llm.generate_json(prompt)
        if not isinstance(questions_raw, list) or not questions_raw:
            raise ValueError("LLM returned empty or non-list response")
    except Exception as exc:
        logger.error(f"Question generation failed: {exc}")
        return {"questions": [], "error": str(exc)}

    # Attach node_ref (identifier) to each question
    import uuid as _uuid
    questions = []
    for i, q in enumerate(questions_raw):
        if not isinstance(q, dict):
            continue
        if not q.get("id"):
            q["id"] = str(_uuid.uuid4())
        node_idx = q.get("node_index", i)
        if 0 <= node_idx < len(nodes):
            q["node_ref"] = nodes[node_idx]["identifier"]
            q["standard_description"] = nodes[node_idx].get("description", "")
            q["beta"] = state.question_difficulties.get(nodes[node_idx]["identifier"], 0.0)
            # normalise code field
            if not q.get("standard_code"):
                q["standard_code"] = nodes[node_idx].get("code") or nodes[node_idx].get("statementCode", "")
        questions.append(q)

    logger.info(f"Assessment Agent: generated {len(questions)} questions for θ={state.theta:+.2f}")
    return {"questions": questions}
