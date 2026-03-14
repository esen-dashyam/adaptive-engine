"""
Memory Agent — Exercise Memory + Knowledge Graph Self-Learning.

Three responsibilities:

1. consolidate_memory (runs after BKT update):
   - Persists this session's answered questions as ExerciseAttempt edges in Neo4j
     (:Student)-[:ATTEMPTED {correct, timestamp, session_id, beta}]->(:GeneratedQuestion)
     (:GeneratedQuestion)-[:TESTS]->(:StandardsFrameworkItem)
   - EMA-updates BUILDS_TOWARDS / PRECEDES conceptual_weight from observed
     mastery transitions across all student pairs seen in this session.
     New weight = old * 0.95 + signal * 0.05  (learning rate 0.05)
     Signal = 1.0 when prereq mastery predicts target mastery correctly,
              0.3 when it doesn't.

2. load_exercise_memory (runs before generate_remediation):
   - Fetches the student's full exercise history from Neo4j for each
     assessed standard. Returned as state.exercise_memory so the
     remediation and recommendation agents can avoid repetition and
     learn which exercise types actually worked.

Data model in Neo4j:
  (:GeneratedQuestion {
    id, question_text, standard_code, dok_level,
    question_type, difficulty_beta, created_at
  })
  (:Student)-[:ATTEMPTED {
    correct: bool, timestamp, session_id, selected_answer, correct_answer
  }]->(:GeneratedQuestion)
  (:GeneratedQuestion)-[:TESTS]->(:StandardsFrameworkItem)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from loguru import logger

from backend.app.agent.state import AssessmentState
from backend.app.core.settings import settings


def _neo4j():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Node 1 — consolidate_memory
# ─────────────────────────────────────────────────────────────────────────────

def consolidate_memory(state: AssessmentState) -> dict:
    """
    After BKT update:
      A) Persist this session's answered questions to Neo4j memory.
      B) Update BUILDS_TOWARDS conceptual_weight from observed transitions.
    """
    logger.info("━" * 60)
    logger.info("  PHASE B — STEP 5/9 │ consolidate_memory  (persist + edge weight update)")
    logger.info("━" * 60)

    session_id = state.pg_session_id or str(uuid.uuid4())
    driver = _neo4j()
    misconception_by_qid = {
        m.get("question_id", ""): m
        for m in state.misconceptions
        if m.get("question_id")
    }

    try:
        with driver.session() as neo:
            # ── A. Persist exercise attempts ──────────────────────────────────
            persisted = 0
            for r in state.results:
                nid  = r.get("node_ref", "")
                qid  = r.get("question_id", "")
                if not nid or not qid:
                    continue
                misconception = misconception_by_qid.get(qid, {})

                neo.run(
                    """
                    MERGE (q:GeneratedQuestion {id: $qid})
                    SET   q.question_text  = $question,
                          q.standard_code  = $code,
                          q.dok_level      = $dok,
                          q.question_type  = $qtype,
                          q.difficulty_beta = $beta,
                          q.created_at     = coalesce(q.created_at, $now)
                    WITH q
                    MATCH (n:StandardsFrameworkItem {identifier: $nid})
                    MERGE (q)-[:TESTS]->(n)
                    WITH q
                    MERGE (s:Student {id: $sid})
                    MERGE (s)-[a:ATTEMPTED {session_id: $sess_id, question_id: $qid}]->(q)
                    SET   a.correct          = $correct,
                          a.selected_answer  = $selected,
                          a.correct_answer   = $correct_ans,
                          a.phi              = $phi,
                          a.misconception    = $misconception,
                          a.root_prerequisite_code = $root_prereq,
                          a.timestamp        = $now
                    """,
                    qid=qid,
                    question=r.get("question", ""),
                    code=r.get("standard_code", ""),
                    dok=r.get("dok_level", 2),
                    qtype=r.get("category", "target"),
                    beta=float(r.get("beta", 0.0)),
                    now=datetime.utcnow().isoformat(),
                    nid=nid,
                    sid=state.student_id,
                    sess_id=session_id,
                    correct=r.get("is_correct", False),
                    selected=r.get("student_answer", ""),
                    correct_ans=r.get("correct_answer", ""),
                    phi=r.get("phi"),
                    misconception=misconception.get("misconception", ""),
                    root_prereq=misconception.get("root_prerequisite_code"),
                )
                persisted += 1

            logger.info(f"Memory: persisted {persisted} exercise attempts for student {state.student_id}")

            # ── B. EMA edge weight update from mastery transitions ────────────
            # Build lookup: node_identifier → mastery_after
            mastery_map: dict[str, float] = {}
            for r in state.results:
                nid = r.get("node_ref", "")
                if nid:
                    mastery_map[nid] = float(r.get("mastery_after", r.get("mastery_before", 0.3)))

            if len(mastery_map) < 2:
                # Not enough data to update edges
                return {}

            # Fetch edges between assessed nodes
            node_ids = list(mastery_map.keys())
            edge_result = neo.run(
                """
                UNWIND $ids AS nid
                MATCH (a:StandardsFrameworkItem {identifier: nid})
                      -[r:BUILDS_TOWARDS|PRECEDES]->(b:StandardsFrameworkItem)
                WHERE b.identifier IN $ids
                RETURN a.identifier AS src, b.identifier AS tgt,
                       coalesce(r.conceptual_weight, r.understanding_strength, 0.7) AS weight,
                       coalesce(r.observation_count, 0) AS obs_count,
                       type(r) AS rel_type, id(r) AS rel_id
                """,
                ids=node_ids,
            )
            edges = [rec.data() for rec in edge_result]

            updates = 0
            for edge in edges:
                src_mastery = mastery_map.get(edge["src"], 0.3)
                tgt_mastery = mastery_map.get(edge["tgt"], 0.3)
                old_weight  = float(edge["weight"])

                # Signal: prereq mastery should predict target mastery.
                # Both high or both low → edge weight confirmed.
                # Divergence → signal is weaker (edge less predictive).
                src_mastered = src_mastery >= 0.65
                tgt_mastered = tgt_mastery >= 0.65
                if src_mastered == tgt_mastered:
                    signal = 1.0   # edge prediction correct
                elif src_mastered and not tgt_mastered:
                    signal = 0.4   # prereq known but target failed → increase weight (gap matters)
                else:
                    signal = 0.2   # target mastered without prereq → edge less critical

                new_weight = old_weight * 0.95 + signal * 0.05
                new_weight = round(max(0.3, min(1.0, new_weight)), 4)

                if abs(new_weight - old_weight) < 0.001:
                    continue  # skip negligible updates

                rel_type = edge["rel_type"]
                weight_prop = "conceptual_weight" if rel_type == "BUILDS_TOWARDS" else "understanding_strength"
                neo.run(
                    f"""
                    MATCH (a:StandardsFrameworkItem {{identifier: $src}})
                          -[r:{rel_type}]->(b:StandardsFrameworkItem {{identifier: $tgt}})
                    SET r.{weight_prop}   = $new_weight,
                        r.observation_count = coalesce(r.observation_count, 0) + 1,
                        r.last_weight_update = $now
                    """,
                    src=edge["src"], tgt=edge["tgt"],
                    new_weight=new_weight,
                    now=datetime.utcnow().isoformat(),
                )
                updates += 1

            logger.info(f"Memory: updated conceptual_weight on {updates} KG edges from observed transitions")

    except Exception as exc:
        logger.warning(f"consolidate_memory failed (non-fatal): {exc}")
    finally:
        driver.close()

    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 — load_exercise_memory
# ─────────────────────────────────────────────────────────────────────────────

def load_exercise_memory(state: AssessmentState) -> dict:
    """
    Fetch this student's exercise history for all assessed standards.
    Returns exercise_memory: {standard_code → list of past exercise records}.

    Used by generate_remediation to avoid repeating questions the student
    has already seen, and by judge_mastery to assess longitudinal trends.
    """
    logger.info("━" * 60)
    logger.info("  PHASE B — STEP 6/9 │ load_exercise_memory  (fetch prior exercise history)")
    logger.info("━" * 60)

    # Collect all standard codes from current results + gaps
    standard_codes = set()
    for r in state.results:
        code = r.get("standard_code", "")
        if code:
            standard_codes.add(code)
    for g in state.gaps:
        code = g.get("code", "")
        if code:
            standard_codes.add(code)

    if not standard_codes:
        return {"exercise_memory": {}}

    driver = _neo4j()
    exercise_memory: dict[str, list[dict]] = {}

    try:
        with driver.session() as neo:
            result = neo.run(
                """
                MATCH (s:Student {id: $sid})-[a:ATTEMPTED]->(q:GeneratedQuestion)-[:TESTS]->
                      (n:StandardsFrameworkItem)
                WHERE q.standard_code IN $codes
                RETURN q.standard_code   AS standard_code,
                       q.question_text   AS question_text,
                       q.dok_level       AS dok_level,
                       q.question_type   AS question_type,
                       q.difficulty_beta AS beta,
                       a.correct         AS correct,
                       a.phi             AS phi,
                       a.misconception   AS misconception,
                       a.root_prerequisite_code AS root_prerequisite_code,
                       a.timestamp       AS timestamp,
                       a.session_id      AS session_id
                ORDER BY a.timestamp DESC
                """,
                sid=state.student_id,
                codes=list(standard_codes),
            )
            for rec in result:
                row = rec.data()
                code = row.get("standard_code", "")
                if code:
                    exercise_memory.setdefault(code, []).append({
                        "question_text":  row.get("question_text", ""),
                        "dok_level":      row.get("dok_level", 2),
                        "question_type":  row.get("question_type", "target"),
                        "beta":           row.get("beta", 0.0),
                        "correct":        row.get("correct", False),
                        "phi":            row.get("phi"),
                        "misconception":  row.get("misconception", ""),
                        "root_prerequisite_code": row.get("root_prerequisite_code"),
                        "timestamp":      row.get("timestamp", ""),
                        "session_id":     row.get("session_id", ""),
                    })

    except Exception as exc:
        logger.warning(f"load_exercise_memory failed (non-fatal): {exc}")
    finally:
        driver.close()

    total = sum(len(v) for v in exercise_memory.values())
    logger.info(
        f"Memory: loaded {total} prior exercise records "
        f"across {len(exercise_memory)} standards for student {state.student_id}"
    )
    return {"exercise_memory": exercise_memory}
