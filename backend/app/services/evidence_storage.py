"""Supabase Storage uploader for BigKid task evidence photos.

Replaces the in-memory blob storage that was wiped every time Railway
redeployed. Uploads to a public bucket so the iOS apps can load via
plain `AsyncImage(url:)` with no auth headers.

Bucket layout: `evidence-photos/<child_id>/<task_id>.jpg`. Per-child path
prefix means we can restrict access by RLS later if needed (Phase 13).
"""
from __future__ import annotations

from uuid import UUID

from loguru import logger

from backend.app.services.supabase_client import get_supabase


_BUCKET = "evidence-photos"
_BUCKET_ENSURED = False  # one-shot per process


def _ensure_bucket() -> None:
    """Create the bucket on first call. Public bucket so the URLs work
    with `AsyncImage` (no signed-URL refresh dance). Idempotent — if the
    bucket already exists Supabase returns an error we silently swallow.
    """
    global _BUCKET_ENSURED
    if _BUCKET_ENSURED:
        return
    sb = get_supabase()
    try:
        # supabase-py 2.x accepts options dict for public/file_size_limit
        sb.storage.create_bucket(
            _BUCKET,
            options={"public": True, "file_size_limit": 5 * 1024 * 1024},
        )
        logger.info("Created Supabase bucket {}", _BUCKET)
    except Exception as exc:
        # "Bucket already exists" — totally fine. Anything else, log and
        # let the upload attempt produce the real error.
        logger.debug("create_bucket('{}') ignored: {}", _BUCKET, exc)
    _BUCKET_ENSURED = True


def upload_evidence(child_id: UUID, task_id: UUID, *, content: bytes) -> str:
    """Upload `content` as JPEG to Supabase Storage and return the public URL.
    Overwrites any previous photo for the same task (kid resubmits)."""
    _ensure_bucket()
    sb = get_supabase()
    path = f"{child_id}/{task_id}.jpg"
    bucket = sb.storage.from_(_BUCKET)

    # `upsert` lets the kid resubmit; without it the second upload errors
    # because the path already exists.
    bucket.upload(
        path=path,
        file=content,
        file_options={"content-type": "image/jpeg", "upsert": "true"},
    )

    # `get_public_url` returns a stable CDN URL because the bucket is public.
    return bucket.get_public_url(path)
