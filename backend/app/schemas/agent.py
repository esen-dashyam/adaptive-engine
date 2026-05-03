"""Agent response envelope. Used internally by AgentLoop; the chat
endpoint adapter copies these fields into ChatResponse."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Proposal(BaseModel):
    tool: str = ""
    args: dict = Field(default_factory=dict)
    label: str = ""
    danger: str = "low"  # "low" | "medium" | "high"
    token: str = ""      # for /parent/agent/exec


class Receipt(BaseModel):
    tool: str = ""
    args: dict = Field(default_factory=dict)
    summary: str = ""
    undo_token: str | None = None


class AgentResponse(BaseModel):
    message: str = ""
    reasoning: str | None = None
    proposals: list[Proposal] = Field(default_factory=list)
    receipts: list[Receipt] = Field(default_factory=list)
    cancelled_proposals: list[str] = Field(default_factory=list)
