"""
Dual-mapper: extract concepts from text chunks via Gemini and map each one
to a Learning Commons StandardsFrameworkItem via SATISFIES or FAR_EXCEEDS.

This runs during the PDF ingestion pipeline to enrich the knowledge graph
with concept nodes tied to state standards.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

_EXTRACTION_PROMPT = """\
You are a curriculum alignment specialist working with K1–K8 math and ELA standards.

Given the text excerpt below, extract the key educational concepts and map each one
to the most relevant standard from the provided standards list.

For each concept decide:
  - SATISFIES   → concept aligns with the standard at grade level
  - FAR_EXCEEDS → concept goes significantly beyond the standard (advanced enrichment)

TEXT:
{text_chunk}

STANDARDS (identifier | code | description):
{standards_context}

Respond ONLY with valid JSON:
{{
  "concepts": [
    {{"name": "...", "description": "...", "difficulty_level": "grade_N or advanced", "tags": ["..."]}}
  ],
  "mappings": [
    {{"concept_name": "...", "standard_id": "...", "alignment": "SATISFIES|FAR_EXCEEDS", "confidence": 0.0}}
  ]
}}
"""


class DualMapper:
    """
    Maps PDF-extracted text chunks into Concept nodes linked to standards.

    Requires a Gemini client (from backend.app.llm.gemini_service.GeminiService)
    and a KnowledgeGraphAdapter for reading standards and writing results.
    """

    def __init__(self, kg_adapter, gemini_client=None):
        self.kg = kg_adapter
        self.gemini = gemini_client

    def process_chunk(
        self,
        text: str,
        source_file: str,
        page_refs: list[int] | None = None,
        subject: str | None = None,
        grade_band: str | None = None,
    ) -> dict[str, int]:
        """
        Run dual-mapping on a single text chunk.

        Returns:
            {"concepts": N, "satisfies": N, "far_exceeds": N}
        """
        standards = self._fetch_standards(subject=subject, grade_band=grade_band)
        if not standards:
            logger.warning("No standards found for context — skipping dual-mapping")
            return {"concepts": 0, "satisfies": 0, "far_exceeds": 0}

        standards_text = "\n".join(
            f"{s['identifier']} | {s.get('code', '')} | {s.get('description', '')}"
            for s in standards[:60]
        )

        prompt = _EXTRACTION_PROMPT.format(
            text_chunk=text[:5000],
            standards_context=standards_text[:3500],
        )

        if self.gemini is None:
            logger.warning("No Gemini client — skipping dual-mapping")
            return {"concepts": 0, "satisfies": 0, "far_exceeds": 0}

        try:
            raw = self.gemini.generate_text(prompt, temperature=0.1, max_tokens=3000)
            result = self._parse_json(raw)
        except Exception as exc:
            logger.error(f"Gemini dual-mapping failed: {exc}")
            return {"concepts": 0, "satisfies": 0, "far_exceeds": 0}

        return self._write_to_graph(result, source_file, page_refs or [])

    # ── Private helpers ───────────────────────────────────────────────────────

    def _fetch_standards(
        self, subject: str | None, grade_band: str | None, limit: int = 80
    ) -> list[dict[str, Any]]:
        cypher = """
        MATCH (n:StandardsFrameworkItem)
        WHERE n.normalizedStatementType IN ['Standard', 'Learning Target']
        {subject_filter}
        RETURN n.identifier AS identifier,
               n.statementCode AS code,
               n.description AS description
        LIMIT $limit
        """
        where_extra = ""
        params: dict[str, Any] = {"limit": limit}
        if subject:
            where_extra = "  AND n.academicSubject = $subject"
            params["subject"] = subject

        final_cypher = cypher.format(subject_filter=where_extra)
        try:
            with self.kg._session() as s:
                result = s.run(final_cypher, **params)
                return [dict(r) for r in result]
        except Exception as exc:
            logger.error(f"Could not fetch standards: {exc}")
            return []

    def _parse_json(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        if "```" in text:
            lines = [l for l in text.splitlines() if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Gemini response was not valid JSON")
            return {"concepts": [], "mappings": []}

    def _write_to_graph(
        self, result: dict, source_file: str, page_refs: list[int]
    ) -> dict[str, int]:
        from backend.app.kg.schema import ConceptRecord

        stats = {"concepts": 0, "satisfies": 0, "far_exceeds": 0}

        concepts: list[dict] = result.get("concepts", [])
        mappings: list[dict] = result.get("mappings", [])

        for c in concepts:
            name = c.get("name", "").strip()
            if not name:
                continue
            record = ConceptRecord(
                name=name,
                description=c.get("description"),
                source_file=source_file,
                difficulty_level=c.get("difficulty_level"),
                tags=c.get("tags", []),
            )
            self.kg.upsert_concept(record)
            stats["concepts"] += 1

        for m in mappings:
            concept_name = m.get("concept_name", "").strip()
            standard_id = m.get("standard_id", "").strip()
            alignment = m.get("alignment", "SATISFIES").upper()
            confidence = float(m.get("confidence", 0.7))

            if not concept_name or not standard_id:
                continue

            rel_type = "SATISFIES" if alignment == "SATISFIES" else "FAR_EXCEEDS"
            try:
                self.kg.link_concept_to_standard(concept_name, standard_id, rel_type, confidence)
                stats[alignment.lower() if alignment == "FAR_EXCEEDS" else "satisfies"] += 1
            except Exception as exc:
                logger.debug(f"Could not write mapping {concept_name} -> {standard_id}: {exc}")

        logger.info(f"Dual-mapper wrote: {stats} from {source_file}")
        return stats
