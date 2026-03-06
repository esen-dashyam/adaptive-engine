"""
Knowledge graph schema for the Adaptive Learning Engine.

Node types and relationship types are defined here, modelling the
Learning Commons KG structure alongside adaptive assessment concepts.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class NodeLabel(str, Enum):
    STANDARDS_ITEM = "StandardsFrameworkItem"
    CONCEPT = "Concept"
    CHUNK = "Chunk"
    STUDENT = "Student"
    SKILL_STATE = "SkillState"


class RelLabel(str, Enum):
    HAS_CHILD = "hasChild"
    IS_RELATED_TO = "isRelatedTo"
    PRECEDES = "precedes"
    DEFINES_UNDERSTANDING = "DEFINES_UNDERSTANDING"
    RELATED_TOPICS = "RELATED_TOPICS"
    BUILDS_TOWARDS = "BUILDS_TOWARDS"
    HAS_SKILL = "HAS_SKILL"
    NEXT = "NEXT"
    MENTIONS = "MENTIONS"


class StandardsItem(BaseModel):
    identifier: str
    description: str
    statement_code: str | None = None
    statement_type: str | None = None
    normalized_type: str | None = None
    grade_level: list[str] = Field(default_factory=list)
    academic_subject: str | None = None
    jurisdiction: str | None = None
    domain: str | None = None
    cluster: str | None = None


class ChunkRecord(BaseModel):
    chunk_id: str
    text: str
    chunk_index: int = 0
    source_file: str | None = None
    page_refs: list[int] = Field(default_factory=list)
    subject: str | None = None
    grade_band: str | None = None
    previous_chunk_id: str | None = None


class ConceptRecord(BaseModel):
    name: str
    description: str | None = None
    source_file: str | None = None
    difficulty_level: str | None = None
    tags: list[str] = Field(default_factory=list)
    frequency: int = 1
    importance_score: float = 0.0


class KGImportStats(BaseModel):
    nodes_written: int = 0
    relationships_written: int = 0
    nodes_skipped: int = 0
    relationships_skipped: int = 0
    elapsed_seconds: float = 0.0
