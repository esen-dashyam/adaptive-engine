"""
Adaptive Learning Engine — FastAPI entry point.

Focused stack:
  - Neo4j knowledge graph (StandardsFrameworkItem + SKILL_STATE)
  - Bayesian Knowledge Tracing (BKT) for student mastery
  - Gemini for adaptive question generation
  - K1-K8 only
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from backend.app.core.settings import settings
from backend.app.api.routes.assessment import router as assessment_router
from backend.app.api.routes.students import router as students_router
from backend.app.api.routes.rag import router as rag_router
from backend.app.api.routes.agent import router as agent_router
from backend.app.api.routes.rasch import router as rasch_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    gemini_status = "configured" if (settings.gemini_api_key or settings.gcp_project_id) else "NOT configured (add GEMINI_API_KEY to .env)"
    logger.info(f"Gemini:   {gemini_status}")
    logger.info(f"Neo4j:    {settings.neo4j_uri}")
    logger.info(f"GraphRAG: {'enabled' if settings.rag_enabled else 'disabled'} | hop_depth={settings.rag_graph_hop_depth}")

    # Create Postgres tables (idempotent)
    try:
        from backend.app.db.engine import create_all_tables
        await create_all_tables()
        logger.info("Postgres: tables ready")
    except Exception as exc:
        logger.warning(f"Postgres: table creation failed (check DATABASE_URL) — {exc}")

    yield
    logger.info("Shutting down")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "**Adaptive Learning Engine** — K1-K8 adaptive assessments powered by "
        "Neo4j Knowledge Graph + Bayesian Knowledge Tracing + Gemini AI.\n\n"
        "## How it works\n"
        "1. Standards are stored as `StandardsFrameworkItem` nodes in Neo4j\n"
        "2. Each assessment is BKT-aware — questions are selected from the student's Zone of Proximal Development\n"
        "3. Every question is generated on-the-fly by Gemini (no static banks)\n"
        "4. After evaluation, BKT `SKILL_STATE` edges are updated and remediation exercises are generated\n\n"
        "## Quick start\n"
        "```\nPOST /api/v1/assessment/generate\n"
        '{"grade":"K3","subject":"math","student_id":"student_001","state":"TX"}\n```'
    ),
    lifespan=lifespan,
)

cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(assessment_router, prefix=settings.api_prefix)
app.include_router(students_router,   prefix=settings.api_prefix)
app.include_router(rag_router,        prefix=settings.api_prefix)
app.include_router(agent_router,      prefix=settings.api_prefix)
app.include_router(rasch_router,      prefix=settings.api_prefix)


@app.get("/", tags=["Health"])
async def root():
    return {"name": settings.app_name, "version": settings.app_version, "status": "running"}


@app.get("/health", tags=["Health"])
async def health():
    """Check Neo4j connectivity."""
    import time
    try:
        from neo4j import GraphDatabase
        start  = time.perf_counter()
        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        with driver.session(database=settings.neo4j_database) as session:
            result = session.run("MATCH (n:StandardsFrameworkItem) RETURN count(n) AS total")
            total  = result.single()["total"]
        driver.close()
        latency = round((time.perf_counter() - start) * 1000, 1)
        return {
            "status": "healthy",
            "neo4j":  "connected",
            "standards_in_graph": total,
            "latency_ms": latency,
            "gemini": "configured" if (settings.gemini_api_key or settings.gcp_project_id) else "not configured",
        }
    except Exception as exc:
        return {"status": "unhealthy", "neo4j": str(exc)}
