"""
GraphRAG retriever for assessment context enrichment.

Uses Neo4j itself as the retrieval layer — no external vector store needed.
For each StandardsFrameworkItem node being assessed, retrieves:

  1. Full node metadata (description, domain/cluster, grade level)
  2. Prerequisite chain via BUILDS_TOWARDS / DEFINES_UNDERSTANDING edges
  3. Forward progression — what this standard leads to (next grade concepts)
  4. Parent grouping — the domain/cluster this standard belongs to
  5. Sibling standards — other standards in the same domain at the same grade
  6. Full-text related standards — similar description keywords via Neo4j FTS
  7. Existing GeneratedQuestion bank for this standard (for prompt diversity)

The retrieved context is formatted as a structured string and injected into
the Gemini prompt so every generated question is grounded in the actual
curriculum structure — not just the one-line standard description.

Flow:
  StandardsFrameworkItem nodes
        ↓
  GraphRAG.retrieve_context(nodes)   ← Cypher queries to Neo4j
        ↓
  RagContext (per-node rich text)
        ↓
  Gemini prompt (augmented with KG context)
        ↓
  Generated questions + answer explanations
        ↓
  BKT SKILL_STATE update (map results back to KG)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from backend.app.core.settings import settings


@dataclass
class NodeContext:
    """Rich context retrieved from the KG for a single StandardsFrameworkItem."""
    identifier: str
    code: str
    description: str
    grade: str
    subject: str
    domain: str = ""
    prerequisites: list[dict[str, Any]] = field(default_factory=list)
    builds_toward: list[dict[str, Any]] = field(default_factory=list)
    siblings: list[dict[str, Any]] = field(default_factory=list)
    existing_questions: list[str] = field(default_factory=list)
    full_text_related: list[dict[str, Any]] = field(default_factory=list)
    learning_components: list[str] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        """Format this context as a structured block for a Gemini prompt."""
        lines = [
            f"[STANDARD] {self.code} (Grade {self.grade})",
            f"  Description: {self.description}",
        ]
        if self.domain:
            lines.append(f"  Domain/Cluster: {self.domain}")

        if self.prerequisites:
            lines.append("  Prerequisites (student must know first):")
            for p in self.prerequisites[:4]:
                lines.append(f"    • [{p.get('code','')}] {p.get('description','')[:120]}")

        if self.builds_toward:
            lines.append("  Leads to (higher-grade targets):")
            for t in self.builds_toward[:3]:
                lines.append(f"    → [{t.get('code','')}] {t.get('description','')[:100]}")

        if self.siblings:
            lines.append(f"  Same-domain siblings ({len(self.siblings)} total, showing 3):")
            for s in self.siblings[:3]:
                lines.append(f"    ~ [{s.get('code','')}] {s.get('description','')[:100]}")

        if self.learning_components:
            lines.append(f"  Sub-skills students must demonstrate (Learning Components):")
            for lc in self.learning_components[:6]:
                lines.append(f"    ▸ {lc[:140]}")

        if self.existing_questions:
            lines.append(f"  AVOID repeating these existing question stems:")
            for q in self.existing_questions[:3]:
                lines.append(f"    ✗ {q[:120]}")

        if self.full_text_related:
            lines.append("  Related standards (same keywords):")
            for r in self.full_text_related[:2]:
                lines.append(f"    ≈ [{r.get('code','')}] {r.get('description','')[:100]}")

        return "\n".join(lines)


class GraphRAG:
    """
    GraphRAG retriever — queries Neo4j for rich curriculum context.

    Designed to augment Gemini's question generation with actual KG structure
    so that:
    - Prerequisite knowledge is surfaced in question context
    - Questions reference the right domain vocabulary
    - Generated questions don't repeat existing banked questions
    - Grade progression is respected (DOK levels align with KG depth)
    """

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

    def close(self):
        if self._driver:
            self._driver.close()

    # ── Main Retrieval API ────────────────────────────────────────────────────

    def retrieve_context(
        self,
        nodes: list[dict[str, Any]],
        max_prereqs: int = 4,
        max_siblings: int = 5,
        max_existing_questions: int = 5,
    ) -> dict[str, NodeContext]:
        """
        Retrieve rich KG context for a list of StandardsFrameworkItem nodes.

        Returns a mapping of {identifier: NodeContext} for each node.
        Each NodeContext contains enough information to meaningfully augment
        the Gemini question-generation prompt.
        """
        if not nodes:
            return {}

        identifiers = [n["identifier"] for n in nodes if n.get("identifier")]
        if not identifiers:
            return {}

        logger.info(f"GraphRAG: retrieving context for {len(identifiers)} nodes")

        context_map: dict[str, NodeContext] = {}

        # ── Batch 1: Prerequisites (BUILDS_TOWARDS / DEFINES_UNDERSTANDING) ──
        prereqs_map = self._batch_prerequisites(identifiers, max_prereqs)

        # ── Batch 2: Forward progression (what these standards lead to) ──
        forward_map = self._batch_forward_progression(identifiers)

        # ── Batch 3: Domain/cluster parent + sibling standards ──
        domain_map, siblings_map = self._batch_domain_and_siblings(identifiers, max_siblings)

        # ── Batch 4: Existing generated questions (avoid repetition) ──
        questions_map = self._batch_existing_questions(identifiers, max_existing_questions)

        # ── Batch 5: Full-text related standards ──
        related_map = self._batch_fulltext_related(nodes)

        # ── Batch 6: Learning Components (sub-skills) ──
        lc_map = self._batch_learning_components(identifiers)

        # Assemble NodeContext for each node
        for node in nodes:
            nid = node.get("identifier", "")
            if not nid:
                continue
            raw_gl = str(node.get("gradeLevel", "")).split(",")[0].strip()
            ctx = NodeContext(
                identifier  = nid,
                code        = node.get("code", ""),
                description = node.get("description", ""),
                grade       = raw_gl,
                subject     = node.get("academicSubject", node.get("subject", "")),
                domain      = domain_map.get(nid, ""),
                prerequisites      = prereqs_map.get(nid, []),
                builds_toward      = forward_map.get(nid, []),
                siblings           = siblings_map.get(nid, []),
                existing_questions = questions_map.get(nid, []),
                full_text_related  = related_map.get(nid, []),
                learning_components = lc_map.get(nid, []),
            )
            context_map[nid] = ctx

        logger.info(
            f"GraphRAG: context assembled for {len(context_map)} nodes | "
            f"prereqs={sum(len(v.prerequisites) for v in context_map.values())} "
            f"existing_q={sum(len(v.existing_questions) for v in context_map.values())}"
        )
        return context_map

    def build_prompt_context(
        self,
        nodes: list[dict[str, Any]],
        context_map: dict[str, NodeContext],
    ) -> str:
        """
        Build the RAG context section for the Gemini prompt.

        Returns a formatted multi-line string ready to be injected into the
        question-generation prompt.
        """
        if not context_map:
            return ""

        blocks = ["=== KNOWLEDGE GRAPH CONTEXT (use to write accurate, curriculum-aligned questions) ===\n"]
        for node in nodes:
            nid = node.get("identifier", "")
            ctx = context_map.get(nid)
            if ctx:
                blocks.append(ctx.to_prompt_block())
                blocks.append("")  # blank line between nodes

        return "\n".join(blocks)

    # ── Private Cypher Batches ────────────────────────────────────────────────

    def _batch_prerequisites(
        self, identifiers: list[str], max_prereqs: int
    ) -> dict[str, list[dict[str, Any]]]:
        """
        For each node, retrieve standards that are prerequisites.

        Uses DEFINES_UNDERSTANDING (weighted) first, falls back to
        BUILDS_TOWARDS / HAS_DEPENDENCY if enrichment hasn't been run.
        """
        result_map: dict[str, list] = {nid: [] for nid in identifiers}
        try:
            with self._get_driver().session(database=settings.neo4j_database) as session:
                # DEFINES_UNDERSTANDING (post-enrichment)
                res = session.run("""
                    UNWIND $ids AS nid
                    MATCH (target:StandardsFrameworkItem {identifier: nid})
                    MATCH (prereq:StandardsFrameworkItem)-[du:DEFINES_UNDERSTANDING]->(target)
                    WHERE prereq.normalizedStatementType = 'Standard'
                      AND size(prereq.description) > 20
                    RETURN nid,
                           prereq.identifier    AS prereq_id,
                           prereq.statementCode AS code,
                           prereq.description   AS description,
                           prereq.gradeLevel    AS gradeLevel,
                           du.understanding_strength AS strength
                    ORDER BY du.understanding_strength DESC
                """, ids=identifiers)

                enriched_hits: dict[str, list] = {nid: [] for nid in identifiers}
                for r in res:
                    nid = r["nid"]
                    if len(enriched_hits[nid]) < max_prereqs:
                        enriched_hits[nid].append({
                            "prereq_id":   r["prereq_id"],
                            "code":        r["code"],
                            "description": r["description"],
                            "gradeLevel":  r["gradeLevel"],
                            "strength":    r["strength"],
                        })

                # Merge in enriched hits
                for nid, hits in enriched_hits.items():
                    result_map[nid].extend(hits)

                # Fallback: BUILDS_TOWARDS for nodes with no enriched prereqs
                fallback_ids = [nid for nid, v in result_map.items() if not v]
                if fallback_ids:
                    res = session.run("""
                        UNWIND $ids AS nid
                        MATCH (target:StandardsFrameworkItem {identifier: nid})
                        OPTIONAL MATCH (p:StandardsFrameworkItem)-[:BUILDS_TOWARDS]->(target)
                        OPTIONAL MATCH (d:StandardsFrameworkItem)<-[:HAS_DEPENDENCY]-(target)
                        WITH nid, collect(DISTINCT p) + collect(DISTINCT d) AS all_p
                        UNWIND all_p AS prereq
                        WITH nid, prereq WHERE prereq IS NOT NULL
                          AND prereq.normalizedStatementType = 'Standard'
                          AND size(prereq.description) > 20
                        RETURN nid,
                               prereq.identifier    AS prereq_id,
                               prereq.statementCode AS code,
                               prereq.description   AS description,
                               prereq.gradeLevel    AS gradeLevel,
                               0.9                  AS strength
                        LIMIT $limit
                    """, ids=fallback_ids, limit=max_prereqs * len(fallback_ids))

                    for r in res:
                        nid = r["nid"]
                        if len(result_map[nid]) < max_prereqs:
                            result_map[nid].append({
                                "prereq_id":   r["prereq_id"],
                                "code":        r["code"],
                                "description": r["description"],
                                "gradeLevel":  r["gradeLevel"],
                                "strength":    r["strength"],
                            })

        except Exception as exc:
            logger.warning(f"GraphRAG prerequisite retrieval failed: {exc}")
        return result_map

    def _batch_forward_progression(
        self, identifiers: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Retrieve what each standard builds toward (forward graph edges)."""
        result_map: dict[str, list] = {nid: [] for nid in identifiers}
        try:
            with self._get_driver().session(database=settings.neo4j_database) as session:
                res = session.run("""
                    UNWIND $ids AS nid
                    MATCH (src:StandardsFrameworkItem {identifier: nid})
                    MATCH (src)-[:BUILDS_TOWARDS]->(target:StandardsFrameworkItem)
                    WHERE target.normalizedStatementType = 'Standard'
                      AND size(target.description) > 20
                    RETURN nid,
                           target.statementCode AS code,
                           target.description   AS description,
                           target.gradeLevel    AS gradeLevel
                    LIMIT $limit
                """, ids=identifiers, limit=3 * len(identifiers))

                for r in res:
                    nid = r["nid"]
                    if len(result_map[nid]) < 3:
                        result_map[nid].append({
                            "code":        r["code"],
                            "description": r["description"],
                            "gradeLevel":  r["gradeLevel"],
                        })
        except Exception as exc:
            logger.warning(f"GraphRAG forward progression retrieval failed: {exc}")
        return result_map

    def _batch_domain_and_siblings(
        self, identifiers: list[str], max_siblings: int
    ) -> tuple[dict[str, str], dict[str, list]]:
        """
        Retrieve the domain/cluster parent and same-domain sibling standards.

        Domain = HAS_CHILD parent with normalizedStatementType in
        (Domain, Cluster, Category, Standard Category).
        """
        domain_map: dict[str, str] = {}
        siblings_map: dict[str, list] = {nid: [] for nid in identifiers}
        try:
            with self._get_driver().session(database=settings.neo4j_database) as session:
                # Domain/cluster parents
                res = session.run("""
                    UNWIND $ids AS nid
                    MATCH (n:StandardsFrameworkItem {identifier: nid})
                    OPTIONAL MATCH (parent:StandardsFrameworkItem)-[:HAS_CHILD]->(n)
                    WHERE parent.normalizedStatementType IN
                          ['Domain', 'Cluster', 'Category', 'Standard Category', 'Reporting Category']
                    RETURN nid, parent.description AS domain
                    LIMIT $limit
                """, ids=identifiers, limit=len(identifiers))
                for r in res:
                    if r["domain"]:
                        domain_map[r["nid"]] = r["domain"][:120]

                # Siblings (same parent, same grade, same subject)
                res = session.run("""
                    UNWIND $ids AS nid
                    MATCH (n:StandardsFrameworkItem {identifier: nid})
                    MATCH (parent:StandardsFrameworkItem)-[:HAS_CHILD]->(n)
                    MATCH (parent)-[:HAS_CHILD]->(sibling:StandardsFrameworkItem)
                    WHERE sibling.identifier <> nid
                      AND sibling.normalizedStatementType = 'Standard'
                      AND sibling.gradeLevel = n.gradeLevel
                      AND size(sibling.description) > 20
                    RETURN nid,
                           sibling.statementCode AS code,
                           sibling.description   AS description
                    LIMIT $limit
                """, ids=identifiers, limit=max_siblings * len(identifiers))
                for r in res:
                    nid = r["nid"]
                    if len(siblings_map[nid]) < max_siblings:
                        siblings_map[nid].append({
                            "code":        r["code"],
                            "description": r["description"],
                        })
        except Exception as exc:
            logger.warning(f"GraphRAG domain/siblings retrieval failed: {exc}")
        return domain_map, siblings_map

    def _batch_existing_questions(
        self, identifiers: list[str], max_q: int
    ) -> dict[str, list[str]]:
        """
        Retrieve previously generated question stems for each standard.
        Used to inject diversity constraints into the Gemini prompt.
        """
        result_map: dict[str, list[str]] = {nid: [] for nid in identifiers}
        try:
            with self._get_driver().session(database=settings.neo4j_database) as session:
                res = session.run("""
                    UNWIND $ids AS nid
                    MATCH (s:StandardsFrameworkItem {identifier: nid})
                    MATCH (q:GeneratedQuestion)-[:TESTS_STANDARD]->(s)
                    RETURN nid, q.text AS question_text
                    ORDER BY q.created_at DESC
                    LIMIT $limit
                """, ids=identifiers, limit=max_q * len(identifiers))
                for r in res:
                    nid = r["nid"]
                    if len(result_map[nid]) < max_q and r.get("question_text"):
                        result_map[nid].append(r["question_text"])
        except Exception as exc:
            logger.warning(f"GraphRAG existing questions retrieval failed: {exc}")
        return result_map

    def _batch_fulltext_related(
        self, nodes: list[dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Find semantically related standards using Neo4j full-text search
        on the description keywords of each node.

        Falls back gracefully if the full-text index doesn't exist.
        """
        result_map: dict[str, list] = {n["identifier"]: [] for n in nodes if n.get("identifier")}
        try:
            with self._get_driver().session(database=settings.neo4j_database) as session:
                for node in nodes:
                    nid = node.get("identifier", "")
                    if not nid:
                        continue
                    desc = node.get("description", "")
                    if not desc or len(desc) < 10:
                        continue

                    # Extract 3-5 meaningful keywords from description
                    keywords = self._extract_keywords(desc)
                    if not keywords:
                        continue

                    query_str = " OR ".join(f'"{kw}"' for kw in keywords[:3])

                    try:
                        res = session.run("""
                            CALL db.index.fulltext.queryNodes(
                                'standardsDescription', $query
                            )
                            YIELD node, score
                            WHERE node.identifier <> $nid
                              AND node.normalizedStatementType = 'Standard'
                              AND score > 1.0
                            RETURN node.statementCode AS code,
                                   node.description   AS description,
                                   score
                            ORDER BY score DESC
                            LIMIT 3
                        """, query=query_str, nid=nid)
                        related = [
                            {"code": r["code"], "description": r["description"], "score": r["score"]}
                            for r in res if r.get("code")
                        ]
                        result_map[nid] = related
                    except Exception:
                        pass  # FTS index may not exist — silently skip
        except Exception as exc:
            logger.warning(f"GraphRAG full-text retrieval failed: {exc}")
        return result_map

    def _batch_learning_components(
        self, identifiers: list[str]
    ) -> dict[str, list[str]]:
        """
        Retrieve LearningComponent sub-skills linked to each standard via SUPPORTS.

        LearningComponents describe the granular skills a student must demonstrate
        to show mastery of a standard — injecting them gives Gemini the vocabulary
        to write diagnostic questions that target specific sub-skills rather than
        generic topic coverage.
        """
        result_map: dict[str, list[str]] = {nid: [] for nid in identifiers}
        try:
            with self._get_driver().session(database=settings.neo4j_database) as session:
                res = session.run("""
                    UNWIND $ids AS nid
                    MATCH (std:StandardsFrameworkItem {identifier: nid})
                    MATCH (lc:LearningComponent)-[:SUPPORTS]->(std)
                    WHERE size(lc.description) > 10
                    RETURN nid, lc.description AS desc
                    LIMIT $limit
                """, ids=identifiers, limit=8 * len(identifiers))
                for r in res:
                    nid = r["nid"]
                    if len(result_map[nid]) < 8 and r.get("desc"):
                        result_map[nid].append(r["desc"])
        except Exception as exc:
            logger.warning(f"GraphRAG learning components retrieval failed: {exc}")
        return result_map

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_keywords(description: str, max_words: int = 5) -> list[str]:
        """
        Extract meaningful keywords from a standard description.

        Simple approach: filter out stop words, take the longest meaningful words.
        No NLP dependencies required.
        """
        STOP = {
            "a", "an", "the", "and", "or", "of", "to", "in", "for", "with",
            "that", "this", "is", "are", "be", "by", "as", "at", "on", "it",
            "can", "will", "use", "using", "used", "from", "how", "what",
            "when", "which", "within", "between", "including", "such",
            "students", "student", "understand", "knowledge", "apply",
            "identify", "describe", "explain", "demonstrate", "compare",
            "write", "read", "solve", "find", "determine", "recognize",
        }
        words = description.replace(",", " ").replace(".", " ").replace(";", " ").split()
        meaningful = [
            w.strip("()[]") for w in words
            if len(w) > 4 and w.lower().strip("()[]") not in STOP
        ]
        # De-dup and take up to max_words by length (prefer longer = more specific)
        seen: set[str] = set()
        out: list[str] = []
        for w in sorted(meaningful, key=len, reverse=True):
            wl = w.lower()
            if wl not in seen:
                seen.add(wl)
                out.append(w)
            if len(out) >= max_words:
                break
        return out


# ── Module-level retrieval function ──────────────────────────────────────────

def retrieve_rag_context(
    nodes: list[dict[str, Any]],
    max_prereqs: int | None = None,
) -> tuple[dict[str, NodeContext], str]:
    """
    Convenience function: retrieve GraphRAG context and build the prompt block.

    Returns:
      context_map  — {identifier: NodeContext} for downstream use
      prompt_block — formatted string ready for Gemini prompt injection
    """
    rag = GraphRAG()
    context_map = rag.retrieve_context(
        nodes,
        max_prereqs=max_prereqs or settings.rag_graph_hop_depth,
    )
    prompt_block = rag.build_prompt_context(nodes, context_map)
    rag.close()
    return context_map, prompt_block
