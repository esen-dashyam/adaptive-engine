"""
GraphRAG debug and inspection endpoints.

Lets you inspect exactly what context the RAG pipeline retrieves from
the Neo4j knowledge graph for any set of standards — useful for
understanding why a question was generated the way it was.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(prefix="/rag", tags=["GraphRAG"])


class RagContextRequest(BaseModel):
    identifiers: list[str] = Field(
        ..., description="List of StandardsFrameworkItem identifiers"
    )
    max_prereqs: int = Field(default=4, ge=1, le=10)


@router.post("/context", summary="Inspect GraphRAG context for a list of standards")
async def get_rag_context(body: RagContextRequest) -> dict[str, Any]:
    """
    Retrieve and display the full GraphRAG context that would be injected
    into the Gemini prompt for the given standards.

    Useful for debugging: shows prerequisites, domain context, existing
    questions, and full-text related standards for each node.
    """
    try:
        from neo4j import GraphDatabase
        from backend.app.core.settings import settings
        from backend.app.rag.graph_rag import GraphRAG

        # Build minimal node dicts from identifiers
        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        with driver.session(database=settings.neo4j_database) as session:
            res = session.run("""
                MATCH (n:StandardsFrameworkItem)
                WHERE n.identifier IN $ids
                RETURN n.identifier AS identifier, n.statementCode AS code,
                       n.description AS description, n.gradeLevel AS gradeLevel,
                       n.academicSubject AS subject
            """, ids=body.identifiers)
            nodes = [dict(r) for r in res]
        driver.close()

        if not nodes:
            raise HTTPException(status_code=404, detail="No matching standards found")

        rag = GraphRAG()
        ctx_map = rag.retrieve_context(nodes, max_prereqs=body.max_prereqs)
        prompt_block = rag.build_prompt_context(nodes, ctx_map)
        rag.close()

        return {
            "nodes_found": len(nodes),
            "rag_enabled": settings.rag_enabled,
            "prompt_block": prompt_block,
            "context_per_node": {
                nid: {
                    "code":               ctx.code,
                    "description":        ctx.description[:200],
                    "grade":              ctx.grade,
                    "domain":             ctx.domain,
                    "prerequisites":      ctx.prerequisites,
                    "builds_toward":      ctx.builds_toward,
                    "siblings_count":     len(ctx.siblings),
                    "existing_questions": ctx.existing_questions,
                    "fulltext_related":   ctx.full_text_related,
                }
                for nid, ctx in ctx_map.items()
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"RAG context retrieval failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/search", summary="Full-text search across StandardsFrameworkItem descriptions")
async def fulltext_search(
    q:       str = Query(..., description="Search query"),
    grade:   str = Query(None, description="Filter by grade number e.g. 3"),
    subject: str = Query(None, description="Filter by subject: math or english"),
    limit:   int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    """
    Full-text search across all StandardsFrameworkItem descriptions in Neo4j.

    Falls back to CONTAINS if the full-text index hasn't been created yet.
    """
    try:
        from neo4j import GraphDatabase
        from backend.app.core.settings import settings

        subject_name = None
        if subject:
            subject_name = "Mathematics" if subject.lower() == "math" else "English Language Arts"

        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        results = []
        with driver.session(database=settings.neo4j_database) as session:
            # Try FTS index first
            try:
                grade_filter   = "AND n.gradeLevel CONTAINS $grade"    if grade        else ""
                subject_filter = "AND n.academicSubject = $subject"     if subject_name else ""

                res = session.run(f"""
                    CALL db.index.fulltext.queryNodes(
                        '{settings.rag_fulltext_index}', $query
                    ) YIELD node AS n, score
                    WHERE n.normalizedStatementType = 'Standard'
                      AND score > 0.5
                      {grade_filter}
                      {subject_filter}
                    RETURN n.identifier AS identifier, n.statementCode AS code,
                           n.description AS description, n.gradeLevel AS gradeLevel,
                           n.academicSubject AS subject, score
                    ORDER BY score DESC
                    LIMIT $limit
                """, query=q, grade=grade or "", subject=subject_name or "", limit=limit)
                results = [dict(r) for r in res]
            except Exception:
                # FTS index not created — fallback to CONTAINS
                grade_filter   = "AND n.gradeLevel CONTAINS $grade"    if grade        else ""
                subject_filter = "AND n.academicSubject = $subject"     if subject_name else ""

                res = session.run(f"""
                    MATCH (n:StandardsFrameworkItem)
                    WHERE toLower(n.description) CONTAINS toLower($query)
                      AND n.normalizedStatementType = 'Standard'
                      {grade_filter}
                      {subject_filter}
                    RETURN n.identifier AS identifier, n.statementCode AS code,
                           n.description AS description, n.gradeLevel AS gradeLevel,
                           n.academicSubject AS subject, 1.0 AS score
                    LIMIT $limit
                """, query=q, grade=grade or "", subject=subject_name or "", limit=limit)
                results = [dict(r) for r in res]

        driver.close()
        return {"query": q, "results": results, "count": len(results)}
    except Exception as exc:
        logger.error(f"Full-text search failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/create-fulltext-index",
    summary="Create Neo4j full-text index on StandardsFrameworkItem.description",
)
async def create_fulltext_index() -> dict[str, Any]:
    """
    One-time setup: create the Neo4j full-text index used by GraphRAG.

    Run this once after Neo4j starts. The index enables fast keyword
    retrieval of related standards during RAG context assembly.

    Index name: `standardsDescription`
    Indexed property: `StandardsFrameworkItem.description`
    """
    try:
        from neo4j import GraphDatabase
        from backend.app.core.settings import settings

        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        with driver.session(database=settings.neo4j_database) as session:
            session.run("""
                CREATE FULLTEXT INDEX standardsDescription IF NOT EXISTS
                FOR (n:StandardsFrameworkItem)
                ON EACH [n.description]
            """)
        driver.close()
        return {"status": "ok", "index": "standardsDescription", "message": "Full-text index created (or already exists)"}
    except Exception as exc:
        logger.error(f"Index creation failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
