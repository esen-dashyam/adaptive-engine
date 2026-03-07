"""
Recommendation Agent — Learning Path Builder.

Flow:
  identify_frontier → build_learning_path → explain_rationale → END

After KST maps the full knowledge state, this agent:
  1. Identifies the ZPD frontier (ready-to-learn nodes)
  2. Builds a sequenced learning path of 3-5 next concepts
  3. Uses Vertex AI to explain WHY each concept matters and HOW to approach it
  4. Tags each recommendation with difficulty, estimated time, and prerequisite chain
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from backend.app.agent.state import AssessmentState
from backend.app.agents.kst import identify_frontier
from backend.app.agents.rasch import grade_to_difficulty, p_correct
from backend.app.agents.vertex_llm import get_llm
from backend.app.core.settings import settings

MAX_RECOMMENDATIONS = 5


def _neo4j():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Node — generate_recommendations
# ─────────────────────────────────────────────────────────────────────────────

def generate_recommendations(state: AssessmentState) -> dict:
    """
    Build a personalised learning path from the KST frontier + Rasch θ.
    Each recommendation is enriched with Vertex AI rationale.
    """
    logger.info("━" * 60)
    logger.info(f"  PHASE B — STEP 7/7 │ generate_recommendations  (ZPD frontier, θ={state.theta:+.3f})")
    logger.info("━" * 60)
    if not state.knowledge_state:
        return {"recommendations": []}

    # 1. Fetch KG edges needed for frontier identification
    all_node_ids = list(state.knowledge_state.keys())
    driver = _neo4j()
    edges = []
    node_details: dict[str, dict] = {}

    try:
        with driver.session() as session:
            if all_node_ids:
                edge_result = session.run(
                    """
                    UNWIND $ids AS nid
                    MATCH (a:StandardsFrameworkItem {identifier: nid})
                    OPTIONAL MATCH (a)-[r:PRECEDES|BUILDS_TOWARDS]->(b:StandardsFrameworkItem)
                    RETURN a.identifier AS source, b.identifier AS target,
                           coalesce(r.weight, 0.7) AS weight, type(r) AS rel_type
                    """,
                    ids=all_node_ids[:50],
                )
                edges = [r.data() for r in edge_result if r["target"]]

                detail_result = session.run(
                    """
                    UNWIND $ids AS nid
                    MATCH (n:StandardsFrameworkItem {identifier: nid})
                    RETURN n.identifier  AS identifier,
                           n.statementCode AS code,
                           n.description   AS description,
                           n.gradeLevelList AS grade_list,
                           n.academicSubject AS subject
                    """,
                    ids=all_node_ids[:50],
                )
                for r in detail_result:
                    row = r.data()
                    grade_list = row.get("grade_list") or []
                    row["grade"] = grade_list[0] if grade_list else ""
                    node_details[row["identifier"]] = row
    finally:
        driver.close()

    # 2. Identify ZPD frontier
    frontier_ids = identify_frontier(
        state.knowledge_state, edges, mastery_threshold=0.60
    )

    if not frontier_ids:
        # Fall back to lowest-mastery unblocked nodes
        frontier_ids = sorted(
            [nid for nid in state.knowledge_state if nid not in state.hard_blocked_nodes],
            key=lambda nid: state.knowledge_state[nid],
        )[:MAX_RECOMMENDATIONS]

    # 3. Score frontier nodes by IRT: prefer nodes where θ ≈ β (50% success prob)
    import math

    scored_frontier = []
    for nid in frontier_ids[:20]:
        detail  = node_details.get(nid, {})
        grade   = detail.get("grade", state.grade)
        beta    = grade_to_difficulty(grade, 2, "target")
        p_succ  = p_correct(state.theta, beta)
        # ideal is p_succ ≈ 0.5 — Fisher Information maximised there
        info_score = p_succ * (1.0 - p_succ)
        scored_frontier.append((info_score, nid, detail, beta))

    scored_frontier.sort(key=lambda x: x[0], reverse=True)
    top_frontier = scored_frontier[:MAX_RECOMMENDATIONS]

    if not top_frontier:
        return {"recommendations": []}

    # 4. Vertex AI: explain the learning path
    grade_label  = f"Grade {state.grade.replace('K', '')}"
    subject_name = "Mathematics" if state.subject.lower() == "math" else "English Language Arts"

    concept_list = []
    for rank, (info, nid, detail, beta) in enumerate(top_frontier, 1):
        concept_list.append(
            f"{rank}. {detail.get('code', nid)}: {detail.get('description', 'Unknown concept')} "
            f"(Grade {detail.get('grade', '?')}, difficulty β={beta:+.1f})"
        )

    prompt = f"""You are a personalised learning coach for a {grade_label} {subject_name} student.

Student profile:
  - Rasch ability θ = {state.theta:+.2f} ({_theta_label(state.theta)})
  - Just completed an adaptive assessment
  - {len(state.gaps)} learning gaps identified

The following concepts are next on their learning path (ZPD frontier):
{chr(10).join(concept_list)}

For EACH concept (in order), provide:
1. Why it matters for this student right now (1 sentence, specific to their level)
2. How to start learning it (1-2 concrete first steps)
3. Estimated study time to reach 80% mastery

Return ONLY a valid JSON array:
[
  {{
    "rank": 1,
    "standard_code": "<code>",
    "why_now": "<motivation sentence>",
    "how_to_start": "<concrete first steps>",
    "estimated_minutes": <number>,
    "difficulty": "accessible|challenging|stretch"
  }}
]"""

    llm = get_llm()
    rec_enrichments: dict[str, dict] = {}
    try:
        raw = llm.generate_json(prompt)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    rec_enrichments[item.get("standard_code", "")] = item
    except Exception as exc:
        logger.warning(f"Recommendation rationale generation failed (non-fatal): {exc}")

    # 5. Assemble final recommendations
    recommendations = []
    for rank, (info, nid, detail, beta) in enumerate(top_frontier, 1):
        code = detail.get("code", nid)
        enrich = rec_enrichments.get(code, {})
        p_succ = p_correct(state.theta, beta)

        recommendations.append({
            "rank":             rank,
            "node_identifier":  nid,
            "standard_code":    code,
            "description":      detail.get("description", ""),
            "grade":            detail.get("grade", state.grade),
            "difficulty_beta":  round(beta, 2),
            "success_prob":     round(p_succ, 2),
            "current_mastery":  round(state.knowledge_state.get(nid, 0.3), 3),
            "why_now":          enrich.get("why_now", ""),
            "how_to_start":     enrich.get("how_to_start", ""),
            "estimated_minutes": enrich.get("estimated_minutes", 30),
            "difficulty":       enrich.get("difficulty", "challenging"),
            "information_score": round(info, 4),
        })

    logger.info(
        f"Recommendation Agent: {len(recommendations)} recommendations "
        f"for θ={state.theta:+.2f}"
    )
    return {"recommendations": recommendations}


def _theta_label(theta: float) -> str:
    if theta >= 1.5:   return "advanced"
    if theta >= 0.5:   return "above average"
    if theta >= -0.5:  return "on grade level"
    if theta >= -1.5:  return "slightly below grade level"
    return "significantly below grade level"
