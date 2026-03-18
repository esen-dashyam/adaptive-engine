"""Supabase client singleton — lazy-initialized from settings."""
from __future__ import annotations

from supabase import create_client, Client
from loguru import logger

from backend.app.core.settings import settings

_client: Client | None = None


def get_supabase() -> Client:
    """Return (and lazily create) the global Supabase client."""
    global _client
    if _client is None:
        if not settings.supabase_url or not settings.supabase_key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_KEY must be set in .env"
            )
        _client = create_client(settings.supabase_url, settings.supabase_key)
        logger.info("Supabase client initialized")
    return _client
