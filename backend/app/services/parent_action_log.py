"""ParentActionLog — central record of every parent-side mutation, with
inverse-action handles for global Undo. See spec §6.

v1 is in-memory; wiped on Railway redeploy alongside BigKidStore.
Phase 13 adds SQLite persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4


Source = Literal["agent", "profile_ui", "shield_dispatcher", "debug_panel"]


@dataclass
class ActionLogEntry:
    action_id: str
    action_type: str           # tool name or 'profile_approve_task' etc.
    args: dict
    inverse_action: str | None
    inverse_args: dict
    source: Source
    created_at: datetime
    expires_at: datetime
    reverted: bool = False


class ParentActionLog:
    """Process-local log. Use `get_log()` for the singleton."""

    def __init__(self, ttl_seconds: int = 60) -> None:
        self._entries: dict[str, ActionLogEntry] = {}
        self._ttl_seconds = ttl_seconds

    def record(
        self, *, action_type: str, args: dict,
        inverse_action: str | None, inverse_args: dict, source: Source,
    ) -> str:
        action_id = uuid4().hex
        now = datetime.now(timezone.utc)
        self._entries[action_id] = ActionLogEntry(
            action_id=action_id,
            action_type=action_type,
            args=args,
            inverse_action=inverse_action,
            inverse_args=inverse_args,
            source=source,
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl_seconds),
        )
        return action_id

    def get(self, action_id: str) -> ActionLogEntry | None:
        entry = self._entries.get(action_id)
        if entry is None:
            return None
        if entry.expires_at < datetime.now(timezone.utc):
            return None
        return entry

    def mark_reverted(self, action_id: str) -> None:
        entry = self._entries.get(action_id)
        if entry is not None:
            entry.reverted = True

    def gc(self) -> int:
        """Drop expired entries; returns count purged. Call periodically
        if memory pressure ever matters (v1: not bothering)."""
        now = datetime.now(timezone.utc)
        stale = [aid for aid, e in self._entries.items() if e.expires_at < now]
        for aid in stale:
            del self._entries[aid]
        return len(stale)


_singleton: ParentActionLog | None = None


def get_log() -> ParentActionLog:
    global _singleton
    if _singleton is None:
        _singleton = ParentActionLog()
    return _singleton
