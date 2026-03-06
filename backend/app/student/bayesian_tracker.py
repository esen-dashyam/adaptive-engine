"""
Bayesian Knowledge Tracing (BKT) skill tracker.

Stores per-student, per-standard mastery in Neo4j as:
  (:Student)-[:SKILL_STATE {p_mastery, p_transit, p_slip, p_guess, attempts, correct, nano_weight}]->(:StandardsFrameworkItem)

BKT update equations:
  Correct:   P(L|correct)   = P(L)*(1-P(S)) / [P(L)*(1-P(S)) + (1-P(L))*P(G)]
  Incorrect: P(L|incorrect) = P(L)*P(S)     / [P(L)*P(S)     + (1-P(L))*(1-P(G))]
  Transition: P(L_next) = P(L|obs) + (1 - P(L|obs)) * P(T)

nano_weight = P(mastery) * 100  — a 0-100 human-readable skill score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from backend.app.core.settings import settings


@dataclass
class BKTParams:
    p_mastery: float = 0.1
    p_transit: float = 0.1
    p_slip: float = 0.05
    p_guess: float = 0.25
    nano_weight: float = 10.0
    attempts: int = 0
    correct: int = 0


class BayesianSkillTracker:
    """
    Graph-native Bayesian Knowledge Tracing.

    All skill states live in Neo4j — no local state is kept between requests.
    """

    MASTERY_THRESHOLD = 0.85
    NANO_SCALE = 100.0
    PROPAGATION_FACTOR = 0.05

    def __init__(self):
        from neo4j import GraphDatabase
        self._driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        self._db = settings.neo4j_database

    def close(self):
        self._driver.close()

    # ── Public API ──────────────────────────────────────────────────────────

    def update_skill(
        self,
        student_id: str,
        node_identifier: str,
        is_correct: bool,
    ) -> dict[str, Any]:
        """Update BKT posterior for one student-skill observation."""
        cur = self._get_or_create_skill_state(student_id, node_identifier)

        p_ln = cur["p_mastery"]
        p_s  = cur["p_slip"]
        p_g  = cur["p_guess"]
        p_t  = cur["p_transit"]

        if is_correct:
            num = p_ln * (1.0 - p_s)
            den = num + (1.0 - p_ln) * p_g
        else:
            num = p_ln * p_s
            den = num + (1.0 - p_ln) * (1.0 - p_g)

        p_given_obs = num / den if den > 0 else p_ln
        p_next = p_given_obs + (1.0 - p_given_obs) * p_t
        p_next = max(0.001, min(0.999, p_next))

        nano_weight = round(p_next * self.NANO_SCALE, 1)
        attempts    = cur["attempts"] + 1
        correct     = cur["correct"] + (1 if is_correct else 0)

        self._write_skill_state(student_id, node_identifier, p_next, nano_weight, attempts, correct)

        return {
            "student_id":      student_id,
            "node_identifier": node_identifier,
            "p_mastery":       round(p_next, 4),
            "nano_weight":     nano_weight,
            "attempts":        attempts,
            "correct":         correct,
            "is_mastered":     p_next >= self.MASTERY_THRESHOLD,
            "observation":     "correct" if is_correct else "incorrect",
        }

    def batch_update(
        self,
        student_id: str,
        observations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Update BKT for multiple observations.
        Each observation: {"node_identifier": str, "is_correct": bool}
        """
        results = []
        for obs in observations:
            r = self.update_skill(
                student_id=student_id,
                node_identifier=obs["node_identifier"],
                is_correct=obs["is_correct"],
            )
            results.append(r)
        self._propagate_nano_weights(student_id, observations)
        return results

    def get_skill_profile(self, student_id: str) -> dict[str, Any]:
        """Full BKT skill profile for a student."""
        with self._driver.session(database=self._db) as session:
            result = session.run("""
                MATCH (s:Student {student_id: $sid})-[sk:SKILL_STATE]->(n:StandardsFrameworkItem)
                RETURN n.identifier     AS node_id,
                       n.statementCode  AS code,
                       n.description    AS description,
                       n.academicSubject AS subject,
                       n.gradeLevel     AS grade,
                       sk.p_mastery     AS p_mastery,
                       sk.nano_weight   AS nano_weight,
                       sk.attempts      AS attempts,
                       sk.correct       AS correct
                ORDER BY sk.nano_weight ASC
            """, sid=student_id)
            skills = [dict(r) for r in result]

        by_subject: dict[str, list] = {}
        for sk in skills:
            by_subject.setdefault(sk.get("subject", "Unknown"), []).append(sk)

        mastered = [s for s in skills if (s.get("p_mastery") or 0) >= self.MASTERY_THRESHOLD]
        weak     = [s for s in skills if (s.get("p_mastery") or 0) < 0.5 and (s.get("attempts") or 0) > 0]

        return {
            "student_id":           student_id,
            "total_skills_tracked": len(skills),
            "mastered_count":       len(mastered),
            "weak_count":           len(weak),
            "skills":               skills,
            "by_subject":           by_subject,
            "weak_areas":           weak[:10],
            "mastered_areas":       mastered[:10],
        }

    def get_nano_weights_for_grade(
        self, student_id: str, grade: str, subject: str
    ) -> list[dict[str, Any]]:
        """Nano weights for all standards in a grade/subject."""
        grade_num    = grade.upper().replace("K", "")
        subject_name = "Mathematics" if subject.lower() == "math" else "English Language Arts"

        with self._driver.session(database=self._db) as session:
            result = session.run("""
                MATCH (n:StandardsFrameworkItem)
                WHERE n.gradeLevel CONTAINS $grade
                  AND n.academicSubject = $subject
                  AND n.normalizedStatementType = 'Standard'
                OPTIONAL MATCH (s:Student {student_id: $sid})-[sk:SKILL_STATE]->(n)
                RETURN n.identifier  AS node_id,
                       n.statementCode AS code,
                       n.description   AS description,
                       COALESCE(sk.p_mastery,   0.0) AS p_mastery,
                       COALESCE(sk.nano_weight,  0.0) AS nano_weight,
                       COALESCE(sk.attempts,     0)   AS attempts
                ORDER BY nano_weight ASC
            """, sid=student_id, grade=grade_num, subject=subject_name)
            return [dict(r) for r in result]

    def find_blocking_gaps(
        self, student_id: str, subject: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Find standards where low mastery blocks multiple downstream concepts.
        Uses BUILDS_TOWARDS edges in the graph.
        """
        subject_filter = "AND n.academicSubject = $subject" if subject else ""

        with self._driver.session(database=self._db) as session:
            result = session.run(f"""
                MATCH (s:Student {{student_id: $sid}})-[sk:SKILL_STATE]->(n:StandardsFrameworkItem)
                WHERE sk.p_mastery < 0.5 AND sk.attempts > 0
                {subject_filter}
                OPTIONAL MATCH (n)-[:BUILDS_TOWARDS]->(target:StandardsFrameworkItem)
                OPTIONAL MATCH (s)-[tsk:SKILL_STATE]->(target)
                WITH n, sk,
                     collect(DISTINCT {{
                         target_id:      target.identifier,
                         target_code:    target.statementCode,
                         target_mastery: COALESCE(tsk.p_mastery, 0.0)
                     }}) AS downstream
                WITH n, sk, downstream,
                     size([d IN downstream WHERE d.target_mastery < 0.5]) AS blocked_count
                WHERE blocked_count > 0
                RETURN n.identifier      AS node_id,
                       n.statementCode   AS code,
                       n.description     AS description,
                       n.academicSubject AS subject,
                       sk.p_mastery      AS p_mastery,
                       sk.nano_weight    AS nano_weight,
                       blocked_count,
                       downstream
                ORDER BY blocked_count DESC, sk.p_mastery ASC
                LIMIT 15
            """, sid=student_id, subject=subject)
            return [dict(r) for r in result]

    # ── Private helpers ──────────────────────────────────────────────────────

    def _get_or_create_skill_state(
        self, student_id: str, node_identifier: str
    ) -> dict[str, Any]:
        with self._driver.session(database=self._db) as session:
            session.run("""
                MERGE (s:Student {student_id: $sid})
                ON CREATE SET s.created_at = datetime()
            """, sid=student_id)

            result = session.run("""
                MATCH (s:Student {student_id: $sid})
                MATCH (n:StandardsFrameworkItem {identifier: $nid})
                MERGE (s)-[sk:SKILL_STATE]->(n)
                ON CREATE SET
                    sk.p_mastery  = 0.1,
                    sk.p_transit  = 0.1,
                    sk.p_slip     = 0.05,
                    sk.p_guess    = 0.25,
                    sk.nano_weight = 10.0,
                    sk.attempts   = 0,
                    sk.correct    = 0,
                    sk.created_at = datetime()
                RETURN sk.p_mastery   AS p_mastery,
                       sk.p_transit   AS p_transit,
                       sk.p_slip      AS p_slip,
                       sk.p_guess     AS p_guess,
                       sk.nano_weight AS nano_weight,
                       sk.attempts    AS attempts,
                       sk.correct     AS correct
            """, sid=student_id, nid=node_identifier)

            record = result.single()
            return dict(record) if record else BKTParams().__dict__

    def _write_skill_state(
        self,
        student_id: str,
        node_identifier: str,
        p_mastery: float,
        nano_weight: float,
        attempts: int,
        correct: int,
    ):
        with self._driver.session(database=self._db) as session:
            session.run("""
                MATCH (s:Student {student_id: $sid})-[sk:SKILL_STATE]->
                      (n:StandardsFrameworkItem {identifier: $nid})
                SET sk.p_mastery   = $pm,
                    sk.nano_weight = $nw,
                    sk.attempts    = $att,
                    sk.correct     = $cor,
                    sk.updated_at  = datetime()
            """, sid=student_id, nid=node_identifier,
                pm=p_mastery, nw=nano_weight, att=attempts, cor=correct)

    def _propagate_nano_weights(
        self, student_id: str, observations: list[dict[str, Any]]
    ):
        """Propagate weight changes along BUILDS_TOWARDS edges (5% influence)."""
        node_ids = [o["node_identifier"] for o in observations]
        with self._driver.session(database=self._db) as session:
            session.run("""
                UNWIND $node_ids AS nid
                MATCH (n:StandardsFrameworkItem {identifier: nid})
                MATCH (n)-[:BUILDS_TOWARDS]->(target:StandardsFrameworkItem)
                MATCH (s:Student {student_id: $sid})-[sk_src:SKILL_STATE]->(n)
                MERGE (s)-[sk_tgt:SKILL_STATE]->(target)
                ON CREATE SET
                    sk_tgt.p_mastery   = 0.1, sk_tgt.p_transit  = 0.1,
                    sk_tgt.p_slip      = 0.05, sk_tgt.p_guess   = 0.25,
                    sk_tgt.nano_weight = 10.0, sk_tgt.attempts   = 0,
                    sk_tgt.correct     = 0,    sk_tgt.created_at = datetime()
                WITH sk_src, sk_tgt
                WHERE sk_tgt.attempts > 0
                SET sk_tgt.nano_weight = sk_tgt.nano_weight +
                    (sk_src.nano_weight - sk_tgt.nano_weight) * $factor,
                    sk_tgt.updated_at = datetime()
            """, node_ids=node_ids, sid=student_id, factor=self.PROPAGATION_FACTOR)
