"""Static app catalog — alias → bundle ID + category hint lookup.

Data ships in `app/data/app_catalog.json`. Matching is case-insensitive and
exact (no fuzzy match at this layer — the Chat resolver may add fuzziness later).

Bundle IDs are retained for future explicit hard-block behavior, but they are
not the default UX because they hide app icons. The resolver treats catalog
hits as recognition only; precise shield still requires a Saved List token.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple


class CatalogEntry(NamedTuple):
    names: list[str]
    bundle_id: str
    category_hint: str


@lru_cache(maxsize=1)
def load_catalog() -> list[CatalogEntry]:
    path = Path(__file__).parent.parent / "data" / "app_catalog.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [CatalogEntry(e["names"], e["bundle_id"], e["category_hint"]) for e in data]


def lookup(query: str) -> CatalogEntry | None:
    """Case-insensitive exact-alias match against the catalog."""
    q = query.strip().lower()
    for entry in load_catalog():
        for alias in entry.names:
            if alias.lower() == q:
                return entry
    return None


def primary_name(entry: CatalogEntry) -> str:
    """First entry in names[] is treated as the canonical display name."""
    return entry.names[0]
