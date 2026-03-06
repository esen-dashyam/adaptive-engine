"""
Async SQLAlchemy engine and session factory for PostgreSQL.

Neo4j is the PRIMARY store for the knowledge graph and skill states.
Postgres is the SECONDARY store for audit logs, session history, and analytics.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.settings import settings

_engine = None
_session_factory = None


def _init() -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.debug,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
        _session_factory = async_sessionmaker(
            _engine, class_=AsyncSession, expire_on_commit=False
        )


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async Postgres session."""
    _init()
    assert _session_factory is not None
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all_tables() -> None:
    """Create all ORM tables on startup (idempotent)."""
    from backend.app.db.base import Base
    import backend.app.db.models  # noqa: F401 — ensures models are registered

    _init()
    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
