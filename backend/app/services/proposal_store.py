"""ProposalStore — in-memory staging of tool calls awaiting parent
confirmation. 10-min TTL so stale proposals get garbage-collected.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4


class ProposalStore:
    def __init__(self, ttl_seconds: int = 600) -> None:
        # token -> [tool_name, args_dict, expires_at]
        self._entries: dict[str, list] = {}
        self._ttl = ttl_seconds

    def stage(self, *, tool: str, args: dict) -> str:
        token = uuid4().hex
        self._entries[token] = [
            tool,
            args,
            datetime.now(timezone.utc) + timedelta(seconds=self._ttl),
        ]
        return token

    def pop(self, token: str) -> tuple[str, dict] | None:
        entry = self._entries.pop(token, None)
        if entry is None:
            return None
        tool, args, expires_at = entry
        if expires_at < datetime.now(timezone.utc):
            return None
        return (tool, args)


_singleton: ProposalStore | None = None


def get_proposal_store() -> ProposalStore:
    global _singleton
    if _singleton is None:
        _singleton = ProposalStore()
    return _singleton
