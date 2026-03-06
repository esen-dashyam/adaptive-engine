"""
Adaptive Assessment Engine — K1-K8 with GraphRAG.

Full pipeline:
  1. Select target + prerequisite standards for grade / state / subject (Neo4j)
  2. Load the student's BKT mastery across all candidate nodes (Neo4j SKILL_STATE)
  3. ZPD-tier candidates and select a 15-question, ~25-minute assessment
  4. GraphRAG retrieval — enrich each node with prerequisite chains, grade
     progression, domain siblings, and existing question bank from Neo4j
  5. Generate every question via Gemini (RAG-augmented batch prompt → per-standard)
  6. Persist questions as (:GeneratedQuestion) nodes in Neo4j
  7. After evaluation: update BKT SKILL_STATE, detect gaps, generate Gemini
     remediation exercises with RAG context
"""

from __future__ import annotations

import json
import random
import uuid
from typing import Any

from loguru import logger

from backend.app.core.settings import settings

# ── Constants ────────────────────────────────────────────────────────────────

STATE_FRAMEWORK_NAMES: dict[str, str] = {
    "Texas":          "TEKS",
    "Florida":        "B.E.S.T.",
    "New York":       "Next Generation Learning Standards",
    "California":     "CA CCSS",
    "Virginia":       "SOL",
    "Georgia":        "GSE",
    "North Carolina": "NC Standard Course of Study",
    "Ohio":           "Ohio Learning Standards",
    "Pennsylvania":   "PA Core Standards",
    "Illinois":       "Illinois Learning Standards",
    "Multi-State":    "Common Core State Standards (CCSS)",
}

STATE_ABBREV: dict[str, str] = {
    "AL": "Alabama",     "AK": "Alaska",       "AZ": "Arizona",      "AR": "Arkansas",
    "CA": "California",  "CO": "Colorado",      "CT": "Connecticut",  "DE": "Delaware",
    "FL": "Florida",     "GA": "Georgia",       "HI": "Hawaii",       "ID": "Idaho",
    "IL": "Illinois",    "IN": "Indiana",       "IA": "Iowa",         "KS": "Kansas",
    "KY": "Kentucky",    "LA": "Louisiana",     "ME": "Maine",        "MD": "Maryland",
    "MA": "Massachusetts","MI": "Michigan",     "MN": "Minnesota",    "MS": "Mississippi",
    "MO": "Missouri",    "MT": "Montana",       "NE": "Nebraska",     "NV": "Nevada",
    "NH": "New Hampshire","NJ": "New Jersey",   "NM": "New Mexico",   "NY": "New York",
    "NC": "North Carolina","ND": "North Dakota","OH": "Ohio",         "OK": "Oklahoma",
    "OR": "Oregon",      "PA": "Pennsylvania",  "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota","TN": "Tennessee",     "TX": "Texas",        "UT": "Utah",
    "VT": "Vermont",     "VA": "Virginia",      "WA": "Washington",   "WV": "West Virginia",
    "WI": "Wisconsin",   "WY": "Wyoming",       "DC": "Washington, D.C.",
}

DOK_LEVELS = {
    1: "Recall & Reproduction",
    2: "Skills & Concepts",
    3: "Strategic Thinking",
}

GRADE_DESCRIPTORS = {
    "1": "1st grade (ages 6-7)",   "2": "2nd grade (ages 7-8)",
    "3": "3rd grade (ages 8-9)",   "4": "4th grade (ages 9-10)",
    "5": "5th grade (ages 10-11)", "6": "6th grade (ages 11-12)",
    "7": "7th grade (ages 12-13)", "8": "8th grade (ages 13-14)",
}


# ── Engine ───────────────────────────────────────────────────────────────────

class AssessmentEngine:
    """
    Graph-aware, BKT-adaptive assessment engine for K1-K8.

    All question generation is handled by Gemini — no static banks.
    """

    QUESTIONS_PER_ASSESSMENT = 15
    NUM_CORE_NODES = 3

    def __init__(self):
        self._driver = None

    def _get_driver(self):
        if self._driver is None:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
        return self._driver

    # ── 1. Node Selection ────────────────────────────────────────────────────

    def select_nodes(
        self, grade: str, state: str, subject: str
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Query Neo4j for target-grade standards + prerequisite standards.

        Target nodes:  StandardsFrameworkItem for grade + state + subject
        Core nodes:    3 randomly sampled target standards
        Prerequisite:  DEFINES_UNDERSTANDING / BUILDS_TOWARDS / HAS_DEPENDENCY → core nodes
        """
        state_full   = STATE_ABBREV.get(state, state)
        grade_num    = grade.upper().replace("K", "")
        subject_name = "Mathematics" if subject.lower() == "math" else "English Language Arts"
        driver       = self._get_driver()

        with driver.session(database=settings.neo4j_database) as session:
            # Target standards — use gradeLevelList (parsed list) not raw gradeLevel JSON string
            res = session.run("""
                MATCH (n:StandardsFrameworkItem)
                WHERE n.jurisdiction = $state
                  AND n.academicSubject = $subject
                  AND ANY(g IN n.gradeLevelList WHERE g = $grade)
                  AND n.normalizedStatementType = 'Standard'
                  AND NOT (n.statementCode STARTS WITH 'MP')
                  AND size(n.description) > 20
                RETURN n.identifier AS identifier, n.statementCode AS code,
                       n.description AS description, n.gradeLevel AS gradeLevel,
                       n.normalizedStatementType AS type
            """, state=state_full, subject=subject_name, grade=grade_num)
            target_nodes = [dict(r) for r in res]

            # Fallback to Multi-State / Common Core if state-specific not found
            if len(target_nodes) < 3 and state_full != "Multi-State":
                res = session.run("""
                    MATCH (n:StandardsFrameworkItem)
                    WHERE n.jurisdiction = 'Multi-State'
                      AND n.academicSubject = $subject
                      AND ANY(g IN n.gradeLevelList WHERE g = $grade)
                      AND n.normalizedStatementType = 'Standard'
                      AND NOT (n.statementCode STARTS WITH 'MP')
                      AND size(n.description) > 20
                    RETURN n.identifier AS identifier, n.statementCode AS code,
                           n.description AS description, n.gradeLevel AS gradeLevel,
                           n.normalizedStatementType AS type
                """, subject=subject_name, grade=grade_num)
                target_nodes.extend([dict(r) for r in res])

            if not target_nodes:
                logger.warning(f"No standards found for {state_full}/{grade_num}/{subject_name}")
                return {"core_nodes": [], "prerequisite_nodes": [], "all_target_nodes": []}

            core_nodes = random.sample(target_nodes, min(self.NUM_CORE_NODES, len(target_nodes)))
            core_ids   = [n["identifier"] for n in core_nodes]

            # Prerequisites via DEFINES_UNDERSTANDING (post-enrichment)
            res = session.run("""
                UNWIND $core_ids AS cid
                MATCH (core:StandardsFrameworkItem {identifier: cid})
                MATCH (prereq:StandardsFrameworkItem)-[du:DEFINES_UNDERSTANDING]->(core)
                WHERE prereq.normalizedStatementType = 'Standard'
                  AND size(prereq.description) > 20
                RETURN DISTINCT prereq.identifier AS identifier, prereq.statementCode AS code,
                       prereq.description AS description, prereq.gradeLevel AS gradeLevel,
                       prereq.normalizedStatementType AS type,
                       du.understanding_strength AS understanding_strength,
                       cid AS core_id, core.statementCode AS core_code
                ORDER BY du.understanding_strength DESC
            """, core_ids=core_ids)
            prereq_nodes = [dict(r) for r in res]

            # Fallback to raw graph edges
            if not prereq_nodes:
                res = session.run("""
                    UNWIND $core_ids AS cid
                    MATCH (core:StandardsFrameworkItem {identifier: cid})
                    OPTIONAL MATCH (p:StandardsFrameworkItem)-[:BUILDS_TOWARDS]->(core)
                    OPTIONAL MATCH (d:StandardsFrameworkItem)<-[:HAS_DEPENDENCY]-(core)
                    WITH core, cid, collect(DISTINCT p) + collect(DISTINCT d) AS all_p
                    UNWIND all_p AS p WITH p, cid, core WHERE p IS NOT NULL
                      AND p.normalizedStatementType = 'Standard'
                      AND size(p.description) > 20
                    RETURN DISTINCT p.identifier AS identifier, p.statementCode AS code,
                           p.description AS description, p.gradeLevel AS gradeLevel,
                           p.normalizedStatementType AS type, 0.90 AS understanding_strength,
                           cid AS core_id, core.statementCode AS core_code
                """, core_ids=core_ids)
                prereq_nodes = [dict(r) for r in res]

            # Last resort: previous grade standards
            if not prereq_nodes:
                prev_grade = str(max(1, int(grade_num) - 1)) if grade_num.isdigit() else "1"
                res = session.run("""
                    MATCH (n:StandardsFrameworkItem)
                    WHERE (n.jurisdiction = $state OR n.jurisdiction = 'Multi-State')
                      AND n.academicSubject = $subject
                      AND ANY(g IN split(n.gradeLevel, ',') WHERE trim(g) = $prev)
                      AND n.normalizedStatementType = 'Standard'
                      AND size(n.description) > 20
                    RETURN n.identifier AS identifier, n.statementCode AS code,
                           n.description AS description, n.gradeLevel AS gradeLevel,
                           n.normalizedStatementType AS type, 0.90 AS understanding_strength,
                           '' AS core_id, '' AS core_code
                    LIMIT 15
                """, state=state_full, subject=subject_name, prev=prev_grade)
                prereq_nodes = [dict(r) for r in res]

        logger.info(
            f"Node selection: {len(target_nodes)} target, {len(core_nodes)} core, "
            f"{len(prereq_nodes)} prereqs — {state}/{grade}/{subject}"
        )
        return {"core_nodes": core_nodes, "prerequisite_nodes": prereq_nodes,
                "all_target_nodes": target_nodes}

    # ── 2. Adaptive Assessment Generation ───────────────────────────────────

    def generate_assessment(
        self,
        grade: str,
        subject: str,
        student_id: str = "default",
        num_questions: int | None = None,
        state: str = "Multi-State",
    ) -> dict[str, Any]:
        """
        True adaptive BKT + ZPD assessment.

        Flow:
          1. Fetch candidate standards from the KG
          2. Load student's BKT mastery from Neo4j
          3. ZPD-tier nodes → dynamic prereq/target allocation
          4. Generate questions via Gemini (batch → per-standard fallback)
          5. Persist questions as GeneratedQuestion nodes
        """
        num_questions = num_questions or self.QUESTIONS_PER_ASSESSMENT
        grade         = grade.upper() if grade.upper().startswith("K") else f"K{grade}"
        state_full    = STATE_ABBREV.get(state, state)
        framework     = STATE_FRAMEWORK_NAMES.get(state_full, f"{state_full} State Standards")

        nodes        = self.select_nodes(grade, state, subject)
        target_nodes = nodes["all_target_nodes"]
        prereq_nodes = nodes["prerequisite_nodes"]
        core_nodes   = nodes["core_nodes"]

        all_ids     = [n["identifier"] for n in (target_nodes + prereq_nodes) if n.get("identifier")]
        mastery_map = self._load_student_mastery(student_id, all_ids)
        logger.info(
            f"Adaptive | student={student_id} grade={grade} "
            f"candidates={len(all_ids)} tracked={len(mastery_map)}"
        )

        selected = self._zpd_adaptive_select(target_nodes, prereq_nodes, mastery_map, num_questions)
        questions = self._generate_questions(selected, grade, subject, state_full, framework)
        questions.sort(key=lambda q: q.get("dok_level", 1))

        self._save_questions_to_graph(questions, grade)

        prereq_count = sum(1 for q in questions if q.get("category") == "prerequisite")
        return {
            "assessment_id":      str(uuid.uuid4()),
            "student_id":         student_id,
            "grade":              grade,
            "subject":            subject,
            "state":              state_full,
            "framework":          framework,
            "estimated_minutes":  25,
            "num_questions":      len(questions),
            "prerequisite_count": prereq_count,
            "target_count":       len(questions) - prereq_count,
            "core_standards": [
                {"identifier": n["identifier"], "code": n.get("code", ""),
                 "description": n.get("description", "")}
                for n in core_nodes
            ],
            "questions": questions,
        }

    # ── 3. Evaluation & Gap Analysis ─────────────────────────────────────────

    def evaluate_assessment(
        self,
        assessment_id: str,
        student_id: str,
        answers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Score answers, identify gaps, generate remediation exercises via Gemini.

        Each answer dict: {question_id, student_answer, node_ref, category,
                           standard_code, standard_description, is_correct}
        """
        if not answers:
            return {"error": "No answers provided"}

        total          = len(answers)
        correct_count  = sum(1 for a in answers if a.get("is_correct"))
        score_pct      = round(correct_count / total * 100, 1) if total else 0

        prereq_answers = [a for a in answers if a.get("category") == "prerequisite"]
        target_answers = [a for a in answers if a.get("category") != "prerequisite"]

        prereq_score = (
            round(sum(1 for a in prereq_answers if a.get("is_correct")) / len(prereq_answers) * 100, 1)
            if prereq_answers else None
        )
        target_score = (
            round(sum(1 for a in target_answers if a.get("is_correct")) / len(target_answers) * 100, 1)
            if target_answers else None
        )

        # Group by node_ref to detect weak standards
        by_node: dict[str, dict[str, Any]] = {}
        for a in answers:
            nref = a.get("node_ref", "")
            if nref not in by_node:
                by_node[nref] = {
                    "node_ref":             nref,
                    "standard_code":        a.get("standard_code", ""),
                    "standard_description": a.get("standard_description", ""),
                    "category":             a.get("category", "target"),
                    "total": 0, "correct": 0,
                }
            by_node[nref]["total"]   += 1
            by_node[nref]["correct"] += 1 if a.get("is_correct") else 0

        gap_metadata: list[dict[str, Any]] = []
        for nref, data in by_node.items():
            mastery = data["correct"] / data["total"] if data["total"] else 0
            gap_metadata.append({**data, "mastery": round(mastery, 2),
                                  "is_gap": mastery < 0.6})

        weak_areas   = [g for g in gap_metadata if g["is_gap"]]
        strong_areas = [g for g in gap_metadata if not g["is_gap"]]
        prereq_gaps  = [g for g in weak_areas if g["category"] == "prerequisite"]

        grade_status = (
            "above"       if score_pct >= 90 else
            "at"          if score_pct >= 75 else
            "approaching" if score_pct >= 60 else
            "below"
        )

        # Extract grade/subject/state from the answers context (stored on questions)
        # We'll detect them from core_standard codes or fall back
        grade   = answers[0].get("grade", "K5") if answers else "K5"
        subject = answers[0].get("subject", "math") if answers else "math"
        state   = answers[0].get("state", "Multi-State") if answers else "Multi-State"
        framework = STATE_FRAMEWORK_NAMES.get(STATE_ABBREV.get(state, state), "CCSS")

        gap_exercises = self._generate_gap_exercises(
            weak_areas, grade, subject, state, framework
        )

        recommendations = self._build_recommendations(weak_areas, strong_areas, prereq_gaps)

        return {
            "assessment_id":    assessment_id,
            "student_id":       student_id,
            "score":            score_pct,
            "correct":          correct_count,
            "total":            total,
            "prerequisite_score": prereq_score,
            "target_score":     target_score,
            "grade_status":     grade_status,
            "has_prereq_gaps":  len(prereq_gaps) > 0,
            "gap_count":        len(weak_areas),
            "weak_areas":       weak_areas,
            "strong_areas":     strong_areas,
            "gap_exercises":    gap_exercises,
            "recommendations":  recommendations,
        }

    # ── 4. Mastery Loading ───────────────────────────────────────────────────

    def _load_student_mastery(
        self, student_id: str, node_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Query BKT SKILL_STATE from Neo4j."""
        if not node_ids:
            return {}
        try:
            with self._get_driver().session(database=settings.neo4j_database) as session:
                result = session.run("""
                    MATCH (s:Student {student_id: $sid})-[sk:SKILL_STATE]->(n:StandardsFrameworkItem)
                    WHERE n.identifier IN $ids
                    RETURN n.identifier   AS identifier,
                           sk.p_mastery   AS p_mastery,
                           sk.attempts    AS attempts,
                           sk.nano_weight AS nano_weight,
                           sk.correct     AS correct
                """, sid=student_id, ids=node_ids)
                return {
                    r["identifier"]: {
                        "p_mastery":   float(r["p_mastery"]   or 0.1),
                        "attempts":    int(r["attempts"]      or 0),
                        "nano_weight": float(r["nano_weight"] or 10.0),
                        "correct":     int(r["correct"]       or 0),
                    }
                    for r in result
                }
        except Exception as exc:
            logger.warning(f"Could not load mastery ({exc}); treating all nodes as UNKNOWN")
            return {}

    # ── 5. ZPD Adaptive Selection ────────────────────────────────────────────

    @staticmethod
    def _zpd_adaptive_select(
        target_nodes: list[dict[str, Any]],
        prereq_nodes: list[dict[str, Any]],
        mastery_map: dict[str, dict[str, Any]],
        num_questions: int,
    ) -> list[dict[str, Any]]:
        """
        Zone of Proximal Development node selection.

        Tiers: MASTERED(≥0.85) | PROFICIENT(≥0.65) | DEVELOPING(≥0.35)
               | STRUGGLING(p<0.35, attempts>0) | UNKNOWN(attempts==0)

        Priority: STRUGGLING > DEVELOPING > UNKNOWN > PROFICIENT > MASTERED
        """

        def _tier(node: dict) -> str:
            nid   = node.get("identifier", "")
            state = mastery_map.get(nid, {})
            p     = state.get("p_mastery", 0.1)
            a     = state.get("attempts", 0)
            if p >= 0.85: return "MASTERED"
            if p >= 0.65: return "PROFICIENT"
            if p >= 0.35: return "DEVELOPING"
            if a > 0:     return "STRUGGLING"
            return "UNKNOWN"

        TIERS = ["MASTERED", "PROFICIENT", "DEVELOPING", "STRUGGLING", "UNKNOWN"]
        prereq_buckets: dict[str, list] = {t: [] for t in TIERS}
        target_buckets: dict[str, list] = {t: [] for t in TIERS}

        for n in prereq_nodes:
            prereq_buckets[_tier(n)].append({**n, "question_category": "prerequisite"})
        for n in target_nodes:
            target_buckets[_tier(n)].append({**n, "question_category": "target"})

        seen_ids: set[str] = set()

        def _pick(pool: list, n: int) -> list[dict]:
            out: list[dict] = []
            for item in random.sample(pool, len(pool)):
                if len(out) >= n:
                    break
                nid = item.get("identifier") or item.get("code", "")
                if nid not in seen_ids:
                    seen_ids.add(nid)
                    out.append(item)
            return out

        n_struggling = len(prereq_buckets["STRUGGLING"])
        n_unknown    = len(prereq_buckets["UNKNOWN"])
        prereq_budget = min(6, max(2, n_struggling + max(1, n_unknown // 2)))
        target_budget = num_questions - prereq_budget

        selected: list[dict] = []
        selected += _pick(prereq_buckets["STRUGGLING"], prereq_budget)
        selected += _pick(prereq_buckets["UNKNOWN"],    max(1, prereq_budget - len(selected)))
        selected += _pick(prereq_buckets["DEVELOPING"], max(0, prereq_budget - len(selected)))
        selected += _pick(prereq_buckets["PROFICIENT"], max(0, prereq_budget - len(selected)))

        t_before = len(selected)
        selected += _pick(target_buckets["STRUGGLING"], max(2, target_budget // 4))
        selected += _pick(target_buckets["UNKNOWN"],    max(3, target_budget // 2))
        selected += _pick(target_buckets["DEVELOPING"], max(2, target_budget // 3))
        selected += _pick(target_buckets["PROFICIENT"], max(1, target_budget - (len(selected) - t_before)))

        if len(selected) < num_questions:
            selected += _pick(
                prereq_buckets["MASTERED"] + target_buckets["MASTERED"],
                num_questions - len(selected),
            )
        if len(selected) < num_questions:
            for n in random.sample(target_nodes + prereq_nodes, len(target_nodes + prereq_nodes)):
                if len(selected) >= num_questions:
                    break
                nid = n.get("identifier") or n.get("code", "")
                if nid not in seen_ids:
                    seen_ids.add(nid)
                    cat = "target" if n in target_nodes else "prerequisite"
                    selected.append({**n, "question_category": cat})

        return selected[:num_questions]

    # ── 6. Question Generation (GraphRAG-augmented) ────────────────────────────

    def _generate_questions(
        self,
        nodes: list[dict[str, Any]],
        grade: str,
        subject: str,
        state: str,
        framework: str,
    ) -> list[dict[str, Any]]:
        """
        Build a RAG-augmented Gemini prompt and generate questions.

        Step 1 — GraphRAG retrieval:
          For every selected node, query Neo4j for:
            • prerequisite chain (BUILDS_TOWARDS / DEFINES_UNDERSTANDING)
            • forward progression (what this leads to)
            • domain/cluster parent + sibling standards
            • existing question bank (for prompt diversity)
            • full-text related standards

        Step 2 — Gemini batch prompt with RAG context injected.
        Step 3 — Per-standard fallback (also RAG-augmented) if batch fails.
        """
        grade_num    = grade.replace("K", "")
        grade_label  = GRADE_DESCRIPTORS.get(grade_num, f"Grade {grade_num}")
        subject_name = "Mathematics" if subject.lower() == "math" else "English Language Arts"

        # ── Step 1: GraphRAG retrieval ────────────────────────────────────────
        rag_context_map: dict = {}
        rag_prompt_block: str = ""
        if settings.rag_enabled:
            try:
                from backend.app.rag.graph_rag import retrieve_rag_context
                rag_context_map, rag_prompt_block = retrieve_rag_context(
                    nodes,
                    max_prereqs=settings.rag_graph_hop_depth,
                )
                logger.info(f"GraphRAG context retrieved for {len(rag_context_map)} nodes")
            except Exception as exc:
                logger.warning(f"GraphRAG retrieval failed (non-fatal): {exc}")

        # ── Step 2: Build standard node summary lines ─────────────────────────
        node_lines = []
        prereq_to_target: dict[str, str] = {}
        for i, node in enumerate(nodes):
            code     = node.get("code", "")
            desc     = node.get("description", "")
            cat      = node.get("question_category", "target")
            strength = node.get("understanding_strength", "")
            core_c   = node.get("core_code", "")
            grade_lv = str(node.get("gradeLevel", "")).split(",")[0].strip()
            s_str    = f" [strength={strength:.2f}]" if isinstance(strength, float) else ""
            c_str    = f" → builds toward {core_c}" if core_c else ""
            node_lines.append(
                f"  {i+1}. [{cat.upper()}]{s_str} {code} (grade {grade_lv}): {desc[:180]}{c_str}"
            )
            if cat == "prerequisite" and core_c:
                prereq_to_target[code] = core_c

        rel_section = ""
        if prereq_to_target:
            lines = [f"  • {p} is prerequisite for {t}" for p, t in prereq_to_target.items()]
            rel_section = "Knowledge Graph Relationships:\n" + "\n".join(lines) + "\n\n"

        # ── Step 3: Assemble RAG-augmented prompt ─────────────────────────────
        prompt = (
            f"You are an Educational Assessment Architect generating a {framework}-aligned assessment.\n\n"
            f"Student: {grade_label} in {state} | Subject: {subject_name} | Framework: {framework}\n\n"
            + (f"{rag_prompt_block}\n" if rag_prompt_block else "")
            + f"{rel_section}"
            + f"Standards to test (PREREQUISITE = foundational gap-check, TARGET = grade-level):\n"
            + "\n".join(node_lines)
            + f"\n\nGenerate exactly {len(nodes)} multiple-choice questions.\n\n"
            "Rules:\n"
            f"1. Use {state} {framework} terminology, age-appropriate for {grade_label}\n"
            "2. PREREQUISITE questions: DOK 1 — direct recall of foundational knowledge\n"
            "3. TARGET questions: DOK 2-3 — problem-solving and application\n"
            "4. Each question tests ONE standard — write a REAL curriculum problem, NOT a meta-question about standards\n"
            "5. Exactly 4 options (A, B, C, D) with ONE correct answer\n"
            "6. Use prerequisite and domain context above to write questions that reflect actual curriculum progression\n"
            "7. Do NOT repeat any question stems listed under 'AVOID repeating' in the KG context above\n\n"
            "Return ONLY a valid JSON array. Each element:\n"
            '{"id":"uuid","type":"multiple_choice","question":"text","options":["A. ...","B. ...","C. ...","D. ..."],'
            '"answer":"A","dok_level":1,"category":"prerequisite|target","standard_code":"code","node_index":0}'
        )

        if not (settings.gemini_api_key or settings.gcp_project_id):
            raise RuntimeError(
                "GEMINI_API_KEY not configured. "
                "Add it to your .env file to generate assessment questions. "
                "Get a free key at https://aistudio.google.com/app/apikey"
            )

        questions = self._try_gemini_batch(prompt, nodes)
        if questions:
            return questions

        logger.warning("Gemini batch failed — falling back to per-standard RAG calls")
        questions = self._fallback_per_standard(nodes, grade, subject, state, framework, rag_context_map)
        if questions:
            return questions

        raise RuntimeError("Gemini failed to generate questions for all standards. Check your API key and quota.")

    def _try_gemini_batch(
        self, prompt: str, nodes: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        """Send batch prompt to Gemini and parse response."""
        if not (settings.gemini_api_key or settings.gcp_project_id):
            return None
        try:
            from backend.app.llm.gemini_service import GeminiService
            svc  = GeminiService()
            text = svc.generate_content(prompt)
            if text:
                return self._parse_questions(text, nodes)
        except Exception as exc:
            logger.warning(f"Gemini batch question generation failed: {exc}")
        return None

    def _fallback_per_standard(
        self,
        nodes: list[dict[str, Any]],
        grade: str,
        subject: str,
        state: str,
        framework: str,
        rag_context_map: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Generate one question per standard via individual RAG-augmented Gemini calls."""
        from backend.app.llm.gemini_service import GeminiService
        svc = GeminiService()

        grade_num = grade.replace("K", "")
        questions: list[dict[str, Any]] = []
        n_prereqs = sum(1 for n in nodes if n.get("question_category") == "prerequisite")
        rag_context_map = rag_context_map or {}

        for i, node in enumerate(nodes):
            code = node.get("code", f"STD-{i}")
            desc = node.get("description", "")
            cat  = node.get("question_category", "target")

            strength = node.get("understanding_strength", 0.75)
            dok = (1 if strength >= 0.85 else 2) if cat == "prerequisite" else min(3, (max(0, i - n_prereqs) // 4) + 2)

            raw_gl     = node.get("gradeLevel", grade_num)
            ng         = str(raw_gl).split(",")[0].strip() if raw_gl else grade_num
            ng         = ng if ng.isdigit() else grade_num
            node_label = "Kindergarten" if ng == "0" else f"Grade {ng}"

            dok_desc = {
                1: "Recall (DOK 1) — direct recall of a fact or basic procedure",
                2: "Application (DOK 2) — apply the concept to solve a new problem",
                3: "Reasoning (DOK 3) — analyse, justify, or evaluate",
            }.get(dok, "Application (DOK 2)")

            # Build per-node RAG context block
            node_rag_block = ""
            nid = node.get("identifier", "")
            if nid and nid in rag_context_map:
                ctx = rag_context_map[nid]
                node_rag_block = ctx.to_prompt_block() + "\n\n"

            prompt = (
                f"You are an expert {framework} assessment writer.\n"
                f"Generate exactly ONE multiple-choice question for the standard below.\n\n"
                + (f"{node_rag_block}" if node_rag_block else "")
                + f"Standard: {code}\nDescription: {desc}\n"
                f"Grade: {node_label} ({state})\nDepth of Knowledge: {dok_desc}\n\n"
                "Requirements:\n"
                "- Write a REAL curriculum question grounded in the KG context above\n"
                f"- Language appropriate for {node_label}\n"
                "- Exactly 4 options (A, B, C, D) with ONE correct answer\n"
                "- Plausible distractors targeting common misconceptions\n"
                "- Do NOT repeat any question stems listed as existing questions above\n\n"
                "Return ONLY valid JSON — no markdown fences:\n"
                f'{{"question":"...","options":["A. ...","B. ...","C. ...","D. ..."],"answer":"A","dok_level":{dok}}}'
            )

            text = svc.generate_content(prompt)
            if text:
                q_data = svc.parse_json_response(text, array=False)
                if (q_data and q_data.get("question") and
                        isinstance(q_data.get("options"), list) and
                        len(q_data["options"]) == 4 and q_data.get("answer")):
                    questions.append({
                        "id":                   str(uuid.uuid4()),
                        "type":                 "multiple_choice",
                        "question":             q_data["question"],
                        "options":              q_data["options"],
                        "answer":               q_data["answer"],
                        "node_ref":             node.get("identifier", ""),
                        "standard_code":        code,
                        "standard_description": desc[:200],
                        "category":             cat,
                        "dok_level":            dok,
                        "dok_label":            DOK_LEVELS.get(dok, ""),
                    })
                    continue
            logger.warning(f"Gemini unavailable — skipping question for {code}")

        return questions

    def _parse_questions(
        self, text: str, nodes: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        """Parse a batch LLM response into structured question dicts."""
        text = text.strip()
        if "```" in text:
            text = "\n".join(l for l in text.split("\n") if not l.strip().startswith("```")).strip()
        si, ei = text.find("["), text.rfind("]") + 1
        if si < 0 or ei <= si:
            return None
        try:
            raw = json.loads(text[si:ei])
            if not isinstance(raw, list) or not raw:
                return None
            questions = []
            for i, q in enumerate(raw):
                node = nodes[i] if i < len(nodes) else nodes[-1]
                q["id"]                   = str(uuid.uuid4())
                q["node_ref"]             = node.get("identifier", "")
                q["standard_code"]        = node.get("code", "")
                q["standard_description"] = node.get("description", "")[:200]
                q["category"]             = node.get("question_category", "target")
                q["dok_level"]            = q.get("dok_level", min(3, (i // 5) + 1))
                q["dok_label"]            = DOK_LEVELS.get(q["dok_level"], "")
                q["type"]                 = "multiple_choice"
                questions.append(q)
            return questions
        except json.JSONDecodeError as exc:
            logger.warning(f"JSON parse error in batch questions: {exc}")
            return None

    # ── 7. Persist Questions ─────────────────────────────────────────────────

    def _save_questions_to_graph(
        self, questions: list[dict[str, Any]], grade: str
    ) -> None:
        """Persist (:GeneratedQuestion)-[:TESTS_STANDARD]->(:StandardsFrameworkItem)."""
        if not questions:
            return
        try:
            with self._get_driver().session(database=settings.neo4j_database) as session:
                for q in questions:
                    session.run("""
                        MERGE (aq:GeneratedQuestion {question_id: $qid})
                        SET aq.text          = $text,
                            aq.options       = $options,
                            aq.answer        = $answer,
                            aq.dok_level     = $dok,
                            aq.standard_code = $code,
                            aq.grade         = $grade,
                            aq.created_at    = timestamp()
                        WITH aq
                        OPTIONAL MATCH (s:StandardsFrameworkItem {identifier: $node_ref})
                        FOREACH (_ IN CASE WHEN s IS NOT NULL THEN [1] ELSE [] END |
                            MERGE (aq)-[:TESTS_STANDARD]->(s)
                        )
                    """,
                        qid=q["id"], text=q["question"], options=q["options"],
                        answer=q["answer"], dok=q.get("dok_level", 1),
                        code=q.get("standard_code", ""), grade=grade,
                        node_ref=q.get("node_ref", ""),
                    )
            logger.info(f"Persisted {len(questions)} GeneratedQuestion nodes to Neo4j")
        except Exception as exc:
            logger.warning(f"Failed to persist questions to graph: {exc}")

    # ── 8. Gap Exercise Generation ───────────────────────────────────────────

    def _generate_gap_exercises(
        self,
        weak_areas: list[dict[str, Any]],
        grade: str,
        subject: str,
        state: str,
        framework: str,
    ) -> list[dict[str, Any]]:
        """
        Generate 2 remediation exercises per detected gap via Gemini.
        Persists as (:GapExercise)-[:REMEDIATES]->(:StandardsFrameworkItem).
        Only processes the 5 worst gaps to keep latency reasonable.
        """
        if not weak_areas:
            return []

        grade_num    = grade.replace("K", "")
        grade_n      = int(grade_num) if grade_num.isdigit() else 0
        grade_label  = "Kindergarten" if grade_n == 0 else f"Grade {grade_n}"
        subject_name = "Mathematics" if subject.lower() == "math" else "English Language Arts"

        from backend.app.llm.gemini_service import GeminiService
        svc = GeminiService()
        if not svc._get_model():
            logger.warning("Gemini not configured — skipping gap exercise generation")
            return []

        exercises: list[dict[str, Any]] = []

        for area in weak_areas[:5]:
            code    = area.get("standard_code", "")
            desc    = area.get("standard_description", "")
            mastery = area.get("mastery", 0.0)
            cat     = area.get("category", "target")

            prompt = (
                f"You are an adaptive {framework} tutor generating remediation exercises.\n\n"
                f"A student scored {mastery*100:.0f}% on this {cat} standard:\n"
                f"Standard: {code}\nDescription: {desc}\nGrade: {grade_label} ({state})\n\n"
                "Generate exactly 2 targeted practice exercises:\n"
                "Exercise 1: DOK 1 — reinforce the foundational concept with direct recall\n"
                "Exercise 2: DOK 2 — apply the concept in a simple word problem\n\n"
                "Requirements:\n"
                f"- Real {subject_name} problems, NOT meta-questions about standards\n"
                f"- Age-appropriate for {grade_label}\n"
                "- Each has exactly 4 MC options (A-D) with one correct answer\n"
                "- Include a one-sentence hint explaining the key idea\n\n"
                "Return ONLY a valid JSON array:\n"
                f'[{{"question":"...","options":["A. ...","B. ...","C. ...","D. ..."],'
                f'"answer":"A","dok_level":1,"hint":"...","standard_code":"{code}"}}]'
            )

            text = svc.generate_content(prompt)
            raw: list = []
            if text:
                parsed = svc.parse_json_response(text, array=True)
                if isinstance(parsed, list):
                    raw = parsed

            for ex in raw[:2]:
                ex_id          = str(uuid.uuid4())
                ex["id"]       = ex_id
                ex["type"]     = "gap_exercise"
                ex["node_ref"] = area.get("node_ref", "")
                ex["gap_mastery"] = mastery
                exercises.append(ex)

                try:
                    with self._get_driver().session(database=settings.neo4j_database) as session:
                        session.run("""
                            MERGE (ge:GapExercise {exercise_id: $eid})
                            SET ge.question      = $question,
                                ge.options       = $options,
                                ge.answer        = $answer,
                                ge.dok_level     = $dok,
                                ge.hint          = $hint,
                                ge.standard_code = $code,
                                ge.grade         = $grade,
                                ge.created_at    = timestamp()
                            WITH ge
                            OPTIONAL MATCH (s:StandardsFrameworkItem {identifier: $node_ref})
                            FOREACH (_ IN CASE WHEN s IS NOT NULL THEN [1] ELSE [] END |
                                MERGE (ge)-[:REMEDIATES]->(s)
                            )
                        """,
                            eid=ex_id, question=ex.get("question", ""),
                            options=ex.get("options", []), answer=ex.get("answer", ""),
                            dok=ex.get("dok_level", 1), hint=ex.get("hint", ""),
                            code=code, grade=grade, node_ref=area.get("node_ref", ""),
                        )
                except Exception as exc:
                    logger.warning(f"Failed to persist gap exercise for {code}: {exc}")

        logger.info(f"Generated {len(exercises)} gap exercises for {len(weak_areas[:5])} gaps")
        return exercises

    # ── 9. Recommendations ───────────────────────────────────────────────────

    @staticmethod
    def _build_recommendations(
        weak_areas: list[dict], strong_areas: list[dict], prereq_gaps: list[dict]
    ) -> list[str]:
        recs: list[str] = []
        if prereq_gaps:
            codes = ", ".join(g["standard_code"] for g in prereq_gaps[:3])
            recs.append(
                f"[PREREQUISITE] Address foundational gaps first: {codes}. "
                "These are blocking progress on grade-level material."
            )
        for area in weak_areas[:5]:
            tag = "[PREREQUISITE]" if area["category"] == "prerequisite" else "[GRADE-LEVEL]"
            recs.append(
                f"{tag} Practice {area['standard_code']}: "
                f"{area['standard_description'][:100]} "
                f"(current mastery: {area['mastery']*100:.0f}%)"
            )
        if strong_areas:
            codes = ", ".join(a["standard_code"] for a in strong_areas[:3])
            recs.append(f"[STRENGTH] Strong performance on: {codes}. Continue to build on these.")
        return recs
