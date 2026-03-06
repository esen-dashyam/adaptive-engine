"""
Neo4j adapter for the Adaptive Learning Engine.

Handles all graph database operations: writing standards nodes, chunk nodes,
concept nodes, relationships, and performing vector/fulltext searches.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from neo4j import Driver, GraphDatabase, Session

from backend.app.core.settings import settings
from backend.app.kg.schema import ChunkRecord, ConceptRecord


class KnowledgeGraphAdapter:
    """Manages all Neo4j operations for the adaptive learning knowledge graph."""

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ):
        self.uri = uri or settings.neo4j_uri
        self.user = user or settings.neo4j_user
        self.password = password or settings.neo4j_password
        self.database = database or settings.neo4j_database
        self.driver: Driver | None = None

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        with self._session() as s:
            s.run("RETURN 1")
        logger.info(f"Connected to Neo4j at {self.uri} (db={self.database})")

    def close(self) -> None:
        if self.driver:
            self.driver.close()
            self.driver = None

    def _session(self) -> Session:
        assert self.driver, "Call connect() first."
        return self.driver.session(database=self.database)

    # ── Standards (Learning Commons) ─────────────────────────────────────────

    def upsert_standards_item(self, props: dict[str, Any]) -> None:
        cypher = """
        MERGE (n:StandardsFrameworkItem {identifier: $identifier})
        SET n += $props
        """
        with self._session() as s:
            s.run(cypher, identifier=props["identifier"], props=props)

    def upsert_standards_batch(self, batch: list[dict[str, Any]]) -> int:
        # Route LearningComponent nodes to their own label
        standards = [r for r in batch if r.get("node_type") != "LearningComponent"]
        lcs       = [r for r in batch if r.get("node_type") == "LearningComponent"]
        with self._session() as s:
            if standards:
                s.run("""
                    UNWIND $rows AS row
                    MERGE (n:StandardsFrameworkItem {identifier: row.identifier})
                    SET n += row
                """, rows=standards)
            if lcs:
                s.run("""
                    UNWIND $rows AS row
                    MERGE (n:LearningComponent {identifier: row.identifier})
                    SET n += row
                """, rows=lcs)
        return len(batch)

    def upsert_relationship_batch(self, batch: list[dict[str, Any]]) -> int:
        """
        Each item: {src_id, tgt_id, label, props}
        Creates the appropriate relationship between two StandardsFrameworkItem nodes.
        """
        cypher = """
        UNWIND $rows AS row
        MATCH (src:StandardsFrameworkItem {identifier: row.src_id})
        MATCH (tgt:StandardsFrameworkItem {identifier: row.tgt_id})
        CALL apoc.merge.relationship(src, row.label, {identifier: row.rel_id}, row.props, tgt)
        YIELD rel
        RETURN count(rel)
        """
        cypher_no_apoc = """
        UNWIND $rows AS row
        MATCH (src:StandardsFrameworkItem {identifier: row.src_id})
        MATCH (tgt:StandardsFrameworkItem {identifier: row.tgt_id})
        MERGE (src)-[r:RELATED_TO {identifier: row.rel_id}]->(tgt)
        SET r += row.props
        SET r.label = row.label
        """
        with self._session() as s:
            try:
                s.run(cypher, rows=batch)
            except Exception:
                s.run(cypher_no_apoc, rows=batch)
        return len(batch)

    def upsert_typed_relationship_batch(self, label: str, batch: list[dict]) -> int:
        """Write relationships of a single known type using dynamic Cypher."""
        safe_label = label.replace("`", "")
        cypher = f"""
        UNWIND $rows AS row
        MATCH (src:StandardsFrameworkItem {{identifier: row.src_id}})
        MATCH (tgt:StandardsFrameworkItem {{identifier: row.tgt_id}})
        MERGE (src)-[r:`{safe_label}` {{identifier: row.rel_id}}]->(tgt)
        SET r += row.props
        """
        with self._session() as s:
            s.run(cypher, rows=batch)
        return len(batch)

    # ── Concepts ──────────────────────────────────────────────────────────────

    def upsert_concept(self, concept: ConceptRecord) -> None:
        cypher = """
        MERGE (c:Concept {name: $name})
        SET c.description = $description,
            c.source_file = $source_file,
            c.difficulty_level = $difficulty_level,
            c.tags = $tags,
            c.frequency = coalesce(c.frequency, 0) + $frequency,
            c.importance_score = $importance_score
        """
        with self._session() as s:
            s.run(
                cypher,
                name=concept.name,
                description=concept.description,
                source_file=concept.source_file,
                difficulty_level=concept.difficulty_level,
                tags=concept.tags,
                frequency=concept.frequency,
                importance_score=concept.importance_score,
            )

    def link_concept_to_standard(
        self, concept_name: str, standard_id: str, rel_type: str, confidence: float = 0.8
    ) -> None:
        cypher = f"""
        MATCH (c:Concept {{name: $name}})
        MATCH (s:StandardsFrameworkItem {{identifier: $std_id}})
        MERGE (c)-[r:{rel_type}]->(s)
        SET r.confidence = $confidence
        """
        with self._session() as s:
            s.run(cypher, name=concept_name, std_id=standard_id, confidence=confidence)

    # ── Chunks ────────────────────────────────────────────────────────────────

    def upsert_chunk(self, chunk: ChunkRecord) -> None:
        cypher = """
        MERGE (c:Chunk {chunk_id: $chunk_id})
        SET c.text = $text,
            c.chunk_index = $chunk_index,
            c.source_file = $source_file,
            c.page_refs = $page_refs,
            c.subject = $subject,
            c.grade_band = $grade_band
        """
        with self._session() as s:
            s.run(
                cypher,
                chunk_id=chunk.chunk_id,
                text=chunk.text,
                chunk_index=chunk.chunk_index,
                source_file=chunk.source_file,
                page_refs=chunk.page_refs,
                subject=chunk.subject,
                grade_band=chunk.grade_band,
            )
        if chunk.previous_chunk_id:
            link = """
            MATCH (prev:Chunk {chunk_id: $prev_id})
            MATCH (curr:Chunk {chunk_id: $curr_id})
            MERGE (prev)-[:NEXT]->(curr)
            """
            with self._session() as s:
                s.run(link, prev_id=chunk.previous_chunk_id, curr_id=chunk.chunk_id)

    def link_chunk_to_standard(self, chunk_id: str, standard_id: str) -> None:
        cypher = """
        MATCH (c:Chunk {chunk_id: $chunk_id})
        MATCH (s:StandardsFrameworkItem {identifier: $std_id})
        MERGE (c)-[:MENTIONS]->(s)
        """
        with self._session() as s:
            s.run(cypher, chunk_id=chunk_id, std_id=standard_id)

    # ── Indexes ───────────────────────────────────────────────────────────────

    def create_standards_fulltext_index(self) -> None:
        cypher = """
        CREATE FULLTEXT INDEX standards_description IF NOT EXISTS
        FOR (n:StandardsFrameworkItem) ON EACH [n.description, n.statementCode]
        """
        with self._session() as s:
            s.run(cypher)
        logger.info("Fulltext index 'standards_description' ready")

    def create_concept_fulltext_index(self) -> None:
        cypher = """
        CREATE FULLTEXT INDEX concept_names IF NOT EXISTS
        FOR (n:Concept) ON EACH [n.name, n.description]
        """
        with self._session() as s:
            s.run(cypher)
        logger.info("Fulltext index 'concept_names' ready")

    def create_chunk_id_index(self) -> None:
        cypher = """
        CREATE INDEX chunk_id_idx IF NOT EXISTS
        FOR (c:Chunk) ON (c.chunk_id)
        """
        with self._session() as s:
            s.run(cypher)
        logger.info("Index 'chunk_id_idx' ready")

    def create_standards_identifier_index(self) -> None:
        cypher = """
        CREATE INDEX standards_identifier_idx IF NOT EXISTS
        FOR (n:StandardsFrameworkItem) ON (n.identifier)
        """
        with self._session() as s:
            s.run(cypher)
        logger.info("Index 'standards_identifier_idx' ready")

    def create_all_indexes(self) -> None:
        self.create_standards_identifier_index()
        self.create_standards_fulltext_index()
        self.create_concept_fulltext_index()
        self.create_chunk_id_index()

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_graph_stats(self) -> dict[str, Any]:
        queries = {
            "standards_items": "MATCH (n:StandardsFrameworkItem) RETURN count(n) AS c",
            "concepts": "MATCH (n:Concept) RETURN count(n) AS c",
            "chunks": "MATCH (n:Chunk) RETURN count(n) AS c",
            "relationships": "MATCH ()-[r]->() RETURN count(r) AS c",
        }
        stats: dict[str, Any] = {}
        with self._session() as s:
            for key, q in queries.items():
                result = s.run(q)
                record = result.single()
                stats[key] = record["c"] if record else 0
        return stats

    def fulltext_search_standards(self, query: str, limit: int = 10) -> list[dict]:
        cypher = """
        CALL db.index.fulltext.queryNodes('standards_description', $query)
        YIELD node, score
        RETURN node.identifier AS identifier,
               node.description AS description,
               node.statementCode AS code,
               node.gradeLevel AS grade_level,
               node.academicSubject AS subject,
               score
        LIMIT $limit
        """
        with self._session() as s:
            result = s.run(cypher, query=query, limit=limit)
            return [dict(r) for r in result]

    def get_standards_for_grade(
        self, grade: str, subject: str | None = None, limit: int = 200
    ) -> list[dict]:
        where_parts = ["n.normalizedStatementType IN ['Standard', 'Learning Target']"]
        params: dict[str, Any] = {"grade": grade, "limit": limit}
        if subject:
            where_parts.append("n.academicSubject = $subject")
            params["subject"] = subject
        where = " AND ".join(where_parts)
        cypher = f"""
        MATCH (n:StandardsFrameworkItem)
        WHERE {where}
          AND any(g IN n.gradeLevelList WHERE g = $grade)
        RETURN n.identifier AS identifier,
               n.description AS description,
               n.statementCode AS code,
               n.gradeLevel AS grade_level,
               n.academicSubject AS subject,
               n.jurisdiction AS jurisdiction
        LIMIT $limit
        """
        with self._session() as s:
            result = s.run(cypher, **params)
            return [dict(r) for r in result]


# ── Singleton ─────────────────────────────────────────────────────────────────

_adapter: KnowledgeGraphAdapter | None = None


def get_kg_adapter() -> KnowledgeGraphAdapter:
    global _adapter
    if _adapter is None:
        _adapter = KnowledgeGraphAdapter()
        _adapter.connect()
    return _adapter
