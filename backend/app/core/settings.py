"""Application settings — loaded from .env."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "Adaptive Learning Engine"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: str = "INFO"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_prefix: str = "/api/v1"

    # CORS
    cors_origins: str = "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003,http://localhost:3004,http://localhost:3005,http://localhost:5173"

    # Neo4j
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"
    neo4j_database: str = "neo4j"

    # Gemini API key (https://aistudio.google.com/app/apikey)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Vertex AI fallback (GCP project)
    gcp_project_id: str = ""
    gcp_region: str = "us-central1"
    vertex_model: str = "gemini-1.5-pro"

    # PostgreSQL (async)
    database_url: str = "postgresql+asyncpg://ale_user:ale_pass@localhost:5433/ale_db"

    # Student BKT
    student_bkt_enabled: bool = True
    student_initial_mastery: float = 0.1

    # Agent
    agent_max_questions: int = 10
    agent_mastery_threshold: float = 0.7   # below this = gap
    agent_gap_limit: int = 5               # max gaps to remediate in one round

    # GraphRAG
    rag_enabled: bool = True
    rag_graph_hop_depth: int = 4        # max prerequisite nodes to retrieve per standard
    rag_max_siblings: int = 5           # sibling standards to include for domain context
    rag_max_existing_questions: int = 5 # existing question stems to inject for diversity
    rag_fulltext_index: str = "standardsDescription"  # Neo4j FTS index name

    # ── Calendar Scheduler (Supabase) ────────────────────
    supabase_url: str = ""
    supabase_key: str = ""

    # ── YouTube Data API v3 ───────────────────────────────
    youtube_api_key: str = ""


settings = Settings()
