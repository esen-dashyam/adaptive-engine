"""
Rasch-Staircase Adaptive Assessment Engine.

Implements Item Response Theory (Rasch 1PL) for K1-K8 math diagnostics.

Assessment flow
───────────────
1. start_session(student_id, grade)
     • θ₀ = grade number (Grade 1 → 1.0, Grade 8 → 8.0)
     • Persists (:RaschSession) node in Neo4j

2. select_next_node(session_id)
     • Queries StandardsFrameworkItem nodes where |β - θ| is minimised
     • Excludes already-answered nodes

3. record_answer(session_id, node_id, is_correct, time_seconds, node_weights?)
     Rasch 1PL update
       P(correct) = sigmoid(θ - β)
       K = 1.2 for Q1-Q5  (aggressive: fast ceiling finding)
       K = 0.6 for Q6-Q15 (stable: precision)
       θ_new = θ_old + K * (outcome - P)
     Time bonus: +0.15 if correct AND time_seconds ≤ 30 AND β > θ
     Multi-standard: proportional ΔMastery per (node_id, weight) pair

4. finalize_session(session_id)
     Heat-map propagation
       • Frontier : β ≤ θ_final → mark SKILL_STATE at 0.85 (potentially mastered)
       • Ancestors: follow BUILDS_TOWARDS backward → mark at 0.98 (confirmed mastered)
       • Future   : follow BUILDS_TOWARDS forward beyond θ → "Next Best Actions"
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from loguru import logger

from backend.app.core.settings import settings

# ── Constants ─────────────────────────────────────────────────────────────────

# θ₀ by grade: Grade 1 = 1.0 … Grade 8 = 8.0
GRADE_THETA: dict[int, float] = {g: float(g) for g in range(1, 9)}

K_AGGRESSIVE = 1.2   # Q1-Q5: fast exploration
K_STABLE     = 0.6   # Q6-Q15: precision

TIME_FAST_SEC    = 30    # seconds — "fast" threshold for hard items
TIME_BONUS_DELTA = 0.15  # extra θ boost when correct + fast + β > θ

TOTAL_QUESTIONS = 15

MASTERY_FRONTIER = 0.85  # β ≤ θ nodes
MASTERY_ANCESTOR = 0.98  # confirmed prerequisite mastery


# ── Engine ────────────────────────────────────────────────────────────────────

class RaschEngine:
    """
    Graph-native Rasch 1PL adaptive assessment engine.

    All session state lives in Neo4j — no local state between requests.
    """

    def __init__(self):
        from neo4j import GraphDatabase
        self._driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        self._db = settings.neo4j_database

    def close(self):
        self._driver.close()

    # ── 1. Session init ───────────────────────────────────────────────────────

    def start_session(self, student_id: str, grade_num: int) -> dict[str, Any]:
        """
        Initialise a Rasch session.

        Returns {session_id, theta, grade, total_questions}.
        """
        session_id = str(uuid.uuid4())
        theta_0 = GRADE_THETA.get(grade_num, float(grade_num))

        with self._driver.session(database=self._db) as s:
            s.run(
                """
                CREATE (rs:RaschSession {
                    session_id: $sid,
                    student_id: $student_id,
                    grade:      $grade,
                    theta:      $theta,
                    q_count:    0,
                    status:     'active',
                    created_at: datetime()
                })
                """,
                sid=session_id, student_id=student_id,
                grade=grade_num, theta=theta_0,
            )

        logger.info(
            f"RaschSession {session_id} | student={student_id} "
            f"grade={grade_num} θ₀={theta_0}"
        )
        return {
            "session_id":      session_id,
            "theta":           theta_0,
            "grade":           grade_num,
            "total_questions": TOTAL_QUESTIONS,
        }

    # ── 2. Item selection ─────────────────────────────────────────────────────

    def select_next_node(self, session_id: str) -> dict[str, Any] | None:
        """
        Select the StandardsFrameworkItem whose β is closest to current θ.

        Returns node dict or None (session complete / no eligible nodes).
        """
        with self._driver.session(database=self._db) as s:
            sess = s.run(
                """
                MATCH (rs:RaschSession {session_id: $sid})
                RETURN rs.theta AS theta, rs.grade AS grade,
                       rs.q_count AS q_count
                """,
                sid=session_id,
            ).single()
            if not sess:
                return None

            theta   = float(sess["theta"])
            q_count = int(sess["q_count"])

            if q_count >= TOTAL_QUESTIONS:
                return None

            # Collect already-answered node IDs
            answered_rows = s.run(
                """
                MATCH (rs:RaschSession {session_id: $sid})-[:ANSWERED]->(n)
                RETURN n.identifier AS identifier
                """,
                sid=session_id,
            )
            answered_ids = [r["identifier"] for r in answered_rows]

            # Select node with minimum |β - θ|, math standards only
            result = s.run(
                """
                MATCH (n:StandardsFrameworkItem)
                WHERE n.academicSubject = 'Mathematics'
                  AND n.normalizedStatementType = 'Standard'
                  AND n.difficulty IS NOT NULL
                  AND size(n.description) > 20
                  AND NOT n.identifier IN $answered_ids
                WITH n, abs(n.difficulty - $theta) AS dist
                ORDER BY dist ASC
                LIMIT 1
                RETURN n.identifier  AS identifier,
                       n.statementCode AS code,
                       n.description   AS description,
                       n.gradeLevel    AS gradeLevel,
                       n.difficulty    AS difficulty,
                       n.jurisdiction  AS jurisdiction
                """,
                theta=theta, answered_ids=answered_ids,
            ).single()

            return dict(result) if result else None

    # ── 3. Record answer ──────────────────────────────────────────────────────

    def record_answer(
        self,
        session_id:   str,
        node_id:      str,
        is_correct:   bool,
        time_seconds: float,
        node_weights: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Record a student answer and update θ via Rasch 1PL.

        node_weights (optional): list of {node_id, weight} for multi-standard
        questions — mastery is distributed proportionally.

        Returns {session_id, q_number, theta, theta_delta, beta, p_correct,
                 is_correct, is_done, mastery_updates}.
        """
        with self._driver.session(database=self._db) as s:
            # Load session
            sess = s.run(
                """
                MATCH (rs:RaschSession {session_id: $sid})
                RETURN rs.theta AS theta, rs.q_count AS q_count,
                       rs.student_id AS student_id
                """,
                sid=session_id,
            ).single()
            if not sess:
                raise ValueError(f"RaschSession {session_id} not found")

            theta      = float(sess["theta"])
            q_count    = int(sess["q_count"])
            student_id = sess["student_id"]

            # Node difficulty β
            node_row = s.run(
                "MATCH (n:StandardsFrameworkItem {identifier: $nid}) "
                "RETURN n.difficulty AS beta",
                nid=node_id,
            ).single()
            beta = float(node_row["beta"]) if (node_row and node_row["beta"] is not None) \
                   else float((q_count // 2) + 1)

            # ── Rasch update ──────────────────────────────────────────────────
            k       = K_AGGRESSIVE if q_count < 5 else K_STABLE
            p       = _rasch_p(theta, beta)
            outcome = 1.0 if is_correct else 0.0
            delta   = k * (outcome - p)

            # Time bonus: fast correct answer on a hard item
            if is_correct and time_seconds <= TIME_FAST_SEC and beta > theta:
                delta += TIME_BONUS_DELTA

            theta_new  = max(0.1, min(9.9, theta + delta))
            q_count_new = q_count + 1

            # Persist ANSWERED relationship
            s.run(
                """
                MATCH (rs:RaschSession {session_id: $sid})
                MATCH (n:StandardsFrameworkItem {identifier: $nid})
                CREATE (rs)-[:ANSWERED {
                    is_correct:   $correct,
                    theta_before: $theta_before,
                    theta_after:  $theta_after,
                    beta:         $beta,
                    p_correct:    $p,
                    time_seconds: $time_s,
                    q_number:     $qn,
                    answered_at:  datetime()
                }]->(n)
                """,
                sid=session_id, nid=node_id, correct=is_correct,
                theta_before=theta, theta_after=theta_new, beta=beta,
                p=p, time_s=time_seconds, qn=q_count_new,
            )

            # Update θ on session node
            s.run(
                """
                MATCH (rs:RaschSession {session_id: $sid})
                SET rs.theta   = $theta,
                    rs.q_count = $qcount,
                    rs.status  = CASE WHEN $qcount >= $total
                                      THEN 'completed' ELSE 'active' END
                """,
                sid=session_id, theta=theta_new,
                qcount=q_count_new, total=TOTAL_QUESTIONS,
            )

            # ── Multi-standard mastery update ─────────────────────────────────
            mastery_updates = self._update_mastery(
                student_id, node_id, is_correct, theta_new, beta, node_weights, s
            )

        is_done = q_count_new >= TOTAL_QUESTIONS
        return {
            "session_id":      session_id,
            "q_number":        q_count_new,
            "is_done":         is_done,
            "theta":           round(theta_new, 3),
            "theta_delta":     round(delta, 3),
            "beta":            round(beta, 2),
            "p_correct":       round(p, 3),
            "is_correct":      is_correct,
            "time_seconds":    time_seconds,
            "mastery_updates": mastery_updates,
        }

    # ── 4. Finalize + heat-map ────────────────────────────────────────────────

    def finalize_session(self, session_id: str) -> dict[str, Any]:
        """
        Post-assessment heat-map propagation.

        Step 1 — Frontier:  all β ≤ θ_final → SKILL_STATE p_mastery = 0.85
        Step 2 — Ancestors: BUILDS_TOWARDS backward from frontier → 0.98
        Step 3 — Future:    BUILDS_TOWARDS forward beyond θ → "Next Best Actions"
        """
        with self._driver.session(database=self._db) as s:
            sess = s.run(
                """
                MATCH (rs:RaschSession {session_id: $sid})
                RETURN rs.theta AS theta, rs.student_id AS student_id,
                       rs.grade AS grade, rs.q_count AS q_count
                """,
                sid=session_id,
            ).single()
            if not sess:
                raise ValueError(f"RaschSession {session_id} not found")

            theta_final = float(sess["theta"])
            student_id  = sess["student_id"]

            # Step 1: Frontier — β ≤ θ_final
            frontier = s.run(
                """
                MATCH (n:StandardsFrameworkItem)
                WHERE n.academicSubject = 'Mathematics'
                  AND n.normalizedStatementType = 'Standard'
                  AND n.difficulty IS NOT NULL
                  AND n.difficulty <= $theta
                RETURN n.identifier   AS id,
                       n.statementCode AS code,
                       n.description   AS description,
                       n.difficulty    AS beta
                ORDER BY n.difficulty DESC
                LIMIT 60
                """,
                theta=theta_final,
            )
            frontier_nodes = [dict(r) for r in frontier]

            # Step 2: Ancestors — BUILDS_TOWARDS backward from frontier
            frontier_ids = [n["id"] for n in frontier_nodes]
            ancestors = s.run(
                """
                UNWIND $ids AS fid
                MATCH (n:StandardsFrameworkItem {identifier: fid})
                MATCH (anc:StandardsFrameworkItem)-[:BUILDS_TOWARDS*1..4]->(n)
                RETURN DISTINCT
                       anc.identifier   AS id,
                       anc.statementCode AS code,
                       anc.description   AS description,
                       anc.difficulty    AS beta
                LIMIT 120
                """,
                ids=frontier_ids,
            )
            ancestor_nodes = [dict(r) for r in ancestors]

            # Step 3: Future — BUILDS_TOWARDS forward beyond θ
            future = s.run(
                """
                UNWIND $ids AS fid
                MATCH (n:StandardsFrameworkItem {identifier: fid})
                MATCH (n)-[:BUILDS_TOWARDS*1..2]->(nxt:StandardsFrameworkItem)
                WHERE nxt.difficulty > $theta
                  AND nxt.difficulty IS NOT NULL
                RETURN DISTINCT
                       nxt.identifier   AS id,
                       nxt.statementCode AS code,
                       nxt.description   AS description,
                       nxt.difficulty    AS beta
                ORDER BY nxt.difficulty ASC
                LIMIT 20
                """,
                ids=frontier_ids, theta=theta_final,
            )
            future_nodes = [dict(r) for r in future]

            # Step 4: LOCKED — nodes far beyond the student's current ability
            # β > θ + 1.5 → too hard to attempt; floor mastery at 0.1 and mark locked
            locked = s.run(
                """
                MATCH (n:StandardsFrameworkItem)
                WHERE n.academicSubject = 'Mathematics'
                  AND n.normalizedStatementType = 'Standard'
                  AND n.difficulty IS NOT NULL
                  AND n.difficulty > $lock_threshold
                RETURN n.identifier   AS id,
                       n.statementCode AS code,
                       n.description   AS description,
                       n.difficulty    AS beta
                LIMIT 200
                """,
                lock_threshold=theta_final + 1.5,
            )
            locked_nodes = [dict(r) for r in locked]
            _write_locked_state(s, student_id, locked_nodes)

            # Write heat-map SKILL_STATEs
            _write_heat_map(s, student_id, frontier_nodes, MASTERY_FRONTIER)
            _write_heat_map(s, student_id, ancestor_nodes, MASTERY_ANCESTOR)

            # Mark session finalised
            s.run(
                """
                MATCH (rs:RaschSession {session_id: $sid})
                SET rs.status = 'completed', rs.finalized_at = datetime()
                """,
                sid=session_id,
            )

        return {
            "session_id":        session_id,
            "theta_final":       round(theta_final, 3),
            "ability_label":     _theta_label(theta_final),
            "frontier_count":    len(frontier_nodes),
            "ancestor_count":    len(ancestor_nodes),
            "locked_count":      len(locked_nodes),
            "frontier":          frontier_nodes[:20],
            "ancestors":         ancestor_nodes[:20],
            "next_best_actions": future_nodes,
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _update_mastery(
        self,
        student_id:   str,
        primary_id:   str,
        is_correct:   bool,
        theta:        float,
        beta:         float,
        node_weights: list[dict] | None,
        session,
    ) -> list[dict[str, Any]]:
        """
        Proportional mastery update for (possibly) multi-standard questions.

        If node_weights is provided each entry {node_id, weight} receives
            ΔMastery = (weight / Σweights) * learning_gain

        The primary node always participates; pass node_weights to split credit.
        """
        if node_weights:
            total_w = sum(w["weight"] for w in node_weights) or 1.0
            targets = [(w["node_id"], w["weight"] / total_w) for w in node_weights]
        else:
            targets = [(primary_id, 1.0)]

        # learning_gain: positive on correct, small negative on incorrect
        learning_gain = (theta - beta) * 0.05 if is_correct else -(beta - theta) * 0.02

        results = []
        for nid, weight in targets:
            delta = weight * learning_gain
            session.run(
                """
                MATCH (st:Student {student_id: $sid})
                MATCH (n:StandardsFrameworkItem {identifier: $nid})
                MERGE (st)-[sk:SKILL_STATE]->(n)
                ON CREATE SET
                    sk.p_mastery   = CASE WHEN 0.1 + $delta < 0.001 THEN 0.001
                                          WHEN 0.1 + $delta > 0.999 THEN 0.999
                                          ELSE 0.1 + $delta END,
                    sk.nano_weight = sk.p_mastery * 100,
                    sk.attempts    = 1,
                    sk.correct     = $correct_int,
                    sk.p_transit   = 0.1,
                    sk.p_slip      = 0.05,
                    sk.p_guess     = 0.25,
                    sk.created_at  = datetime(),
                    sk.source      = 'rasch'
                ON MATCH SET
                    sk.p_mastery   = CASE WHEN sk.p_mastery + $delta < 0.001 THEN 0.001
                                          WHEN sk.p_mastery + $delta > 0.999 THEN 0.999
                                          ELSE sk.p_mastery + $delta END,
                    sk.nano_weight = sk.p_mastery * 100,
                    sk.attempts    = sk.attempts + 1,
                    sk.correct     = sk.correct + $correct_int,
                    sk.updated_at  = datetime()
                """,
                sid=student_id, nid=nid,
                delta=delta, correct_int=1 if is_correct else 0,
            )
            results.append({
                "node_id":       nid,
                "weight":        round(weight, 3),
                "delta_mastery": round(delta, 4),
            })

        return results


# ── Module-level helpers ──────────────────────────────────────────────────────

def _rasch_p(theta: float, beta: float) -> float:
    """P(correct) = sigmoid(θ - β)  —  Rasch 1PL."""
    return 1.0 / (1.0 + math.exp(-(theta - beta)))


def _theta_label(theta: float) -> str:
    if theta >= 7.5: return "Advanced (Grade 8+)"
    if theta >= 6.5: return "Grade 7–8 level"
    if theta >= 5.5: return "Grade 6–7 level"
    if theta >= 4.5: return "Grade 5–6 level"
    if theta >= 3.5: return "Grade 4–5 level"
    if theta >= 2.5: return "Grade 3–4 level"
    if theta >= 1.5: return "Grade 2–3 level"
    return "Grade 1–2 level"


def _write_locked_state(session, student_id: str, nodes: list[dict]) -> None:
    """
    Mark nodes that are far beyond the student's current θ as LOCKED.

    Only creates / updates the SKILL_STATE if it does not already exist OR
    if the existing mastery is already at the floor (≤ 0.1), so that real
    BKT progress is never overwritten by a lock.
    """
    if not nodes:
        return

    session.run(
        "MERGE (st:Student {student_id: $sid}) "
        "ON CREATE SET st.created_at = datetime()",
        sid=student_id,
    )

    for node in nodes:
        nid = node.get("id") or node.get("identifier")
        if not nid:
            continue
        session.run(
            """
            MATCH (n:StandardsFrameworkItem {identifier: $nid})
            MATCH (st:Student {student_id: $sid})
            MERGE (st)-[sk:SKILL_STATE]->(n)
            ON CREATE SET
                sk.p_mastery   = 0.1,
                sk.nano_weight = 10.0,
                sk.attempts    = 0,
                sk.correct     = 0,
                sk.locked      = true,
                sk.created_at  = datetime(),
                sk.source      = 'rasch_locked'
            ON MATCH SET
                sk.locked     = CASE WHEN sk.p_mastery <= 0.1 THEN true ELSE false END,
                sk.updated_at = datetime()
            """,
            sid=student_id, nid=nid,
        )


def _write_heat_map(
    session,
    student_id: str,
    nodes: list[dict],
    mastery_val: float,
) -> None:
    """
    Upsert SKILL_STATE for every node in the heat-map zone.
    Only raises mastery, never lowers it — preserves BKT progress.
    """
    if not nodes:
        return

    session.run(
        "MERGE (st:Student {student_id: $sid}) "
        "ON CREATE SET st.created_at = datetime()",
        sid=student_id,
    )

    for node in nodes:
        nid = node.get("id") or node.get("identifier")
        if not nid:
            continue
        session.run(
            """
            MATCH (n:StandardsFrameworkItem {identifier: $nid})
            MATCH (st:Student {student_id: $sid})
            MERGE (st)-[sk:SKILL_STATE]->(n)
            ON CREATE SET
                sk.p_mastery   = $mastery,
                sk.nano_weight = $mastery * 100,
                sk.attempts    = 1,
                sk.correct     = 1,
                sk.p_transit   = 0.1,
                sk.p_slip      = 0.05,
                sk.p_guess     = 0.25,
                sk.created_at  = datetime(),
                sk.source      = 'rasch_heatmap'
            ON MATCH SET
                sk.p_mastery   = CASE WHEN sk.p_mastery < $mastery
                                      THEN $mastery ELSE sk.p_mastery END,
                sk.nano_weight = CASE WHEN sk.p_mastery < $mastery
                                      THEN $mastery * 100 ELSE sk.nano_weight END,
                sk.updated_at  = datetime()
            """,
            sid=student_id, nid=nid, mastery=mastery_val,
        )
