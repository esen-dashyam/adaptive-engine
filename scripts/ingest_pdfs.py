"""
Ingest PDF curriculum materials into the Adaptive Learning Engine knowledge graph.

Pipeline:
  1. Parse PDF → text pages
  2. Chunk pages into fixed-size segments
  3. Write Chunk nodes to Neo4j (with NEXT chain)
  4. Optionally run dual-mapping (concept extraction via Gemini → link to standards)

Usage:
    poetry run python scripts/ingest_pdfs.py --pdf path/to/book.pdf --grade 3 --subject Mathematics
    poetry run python scripts/ingest_pdfs.py --pdf-dir data/books/ --subject ELA --grade 5
    poetry run python scripts/ingest_pdfs.py --pdf book.pdf --dual-map   # uses Gemini

The Gemini API key must be set in .env (GEMINI_API_KEY) for --dual-map to work.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.kg.neo4j_adapter import KnowledgeGraphAdapter
from backend.app.kg.schema import ChunkRecord
from backend.app.ingest.pdf_parser import parse_pdf


def ingest_single_pdf(
    pdf_path: Path,
    adapter: KnowledgeGraphAdapter,
    grade: str | None = None,
    subject: str | None = None,
    max_chars: int = 3000,
    dual_map: bool = False,
    gemini_client=None,
) -> dict:
    """
    Full ingestion pipeline for one PDF file.

    Returns stats dict.
    """
    logger.info(f"Ingesting: {pdf_path.name}")
    t0 = time.time()

    chunks_raw = parse_pdf(pdf_path, max_chars=max_chars, overlap_chars=200)
    if not chunks_raw:
        logger.warning(f"No text extracted from {pdf_path.name}")
        return {"chunks": 0, "concepts": 0, "satisfies": 0, "far_exceeds": 0}

    # Build unique chunk IDs and write to Neo4j
    prev_id: str | None = None
    chunk_records: list[ChunkRecord] = []

    for raw in chunks_raw:
        cid = f"{pdf_path.stem}_{uuid.uuid4().hex[:8]}"
        record = ChunkRecord(
            chunk_id=cid,
            text=raw["text"],
            chunk_index=raw["chunk_index"],
            source_file=pdf_path.name,
            page_refs=raw.get("pages", []),
            subject=subject,
            grade_band=grade,
            previous_chunk_id=prev_id,
        )
        chunk_records.append(record)
        prev_id = cid

    for record in chunk_records:
        adapter.upsert_chunk(record)

    stats = {"chunks": len(chunk_records), "concepts": 0, "satisfies": 0, "far_exceeds": 0}

    # Optional: dual-map concepts to standards
    if dual_map:
        from backend.app.ingest.dual_mapper import DualMapper

        mapper = DualMapper(kg_adapter=adapter, gemini_client=gemini_client)
        for chunk in chunk_records:
            result = mapper.process_chunk(
                text=chunk.text,
                source_file=chunk.source_file or pdf_path.name,
                page_refs=chunk.page_refs,
                subject=subject,
                grade_band=grade,
            )
            stats["concepts"] += result.get("concepts", 0)
            stats["satisfies"] += result.get("satisfies", 0)
            stats["far_exceeds"] += result.get("far_exceeds", 0)

    elapsed = time.time() - t0
    logger.success(
        f"{pdf_path.name}: {stats['chunks']} chunks, "
        f"{stats['concepts']} concepts in {elapsed:.1f}s"
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest PDFs into the Adaptive Learning Engine knowledge graph."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdf", type=Path, help="Path to a single PDF file.")
    source.add_argument("--pdf-dir", type=Path, help="Directory containing PDF files to ingest.")

    parser.add_argument("--grade", type=str, default=None, help="Grade level (e.g. '3' or 'K').")
    parser.add_argument(
        "--subject", type=str, default=None, help="Academic subject (e.g. 'Mathematics', 'ELA')."
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=3000,
        help="Max characters per chunk (default: 3000).",
    )
    parser.add_argument(
        "--dual-map",
        action="store_true",
        help="Run Gemini dual-mapping to link concepts to standards.",
    )
    args = parser.parse_args()

    # Collect PDF paths
    pdf_paths: list[Path] = []
    if args.pdf:
        if not args.pdf.exists():
            logger.error(f"File not found: {args.pdf}")
            sys.exit(1)
        pdf_paths = [args.pdf]
    else:
        pdf_paths = sorted(args.pdf_dir.glob("*.pdf"))
        if not pdf_paths:
            logger.error(f"No PDF files found in {args.pdf_dir}")
            sys.exit(1)
        logger.info(f"Found {len(pdf_paths)} PDFs in {args.pdf_dir}")

    # Optionally initialise Gemini client
    gemini_client = None
    if args.dual_map:
        gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        if not gemini_api_key:
            logger.warning("GEMINI_API_KEY not set — dual-mapping disabled")
            args.dual_map = False
        else:
            try:
                from backend.app.llm.gemini_service import GeminiService
                gemini_client = GeminiService(api_key=gemini_api_key)
            except Exception as exc:
                logger.warning(f"Could not initialise Gemini: {exc}")
                args.dual_map = False

    # Connect to Neo4j
    adapter = KnowledgeGraphAdapter()
    adapter.connect()
    adapter.create_all_indexes()

    total_stats: dict[str, int] = {"chunks": 0, "concepts": 0, "satisfies": 0, "far_exceeds": 0}

    try:
        for pdf_path in pdf_paths:
            result = ingest_single_pdf(
                pdf_path=pdf_path,
                adapter=adapter,
                grade=args.grade,
                subject=args.subject,
                max_chars=args.max_chars,
                dual_map=args.dual_map,
                gemini_client=gemini_client,
            )
            for k in total_stats:
                total_stats[k] += result.get(k, 0)

        logger.success(f"Ingestion complete: {total_stats}")
        logger.info("Neo4j stats:")
        for k, v in adapter.get_graph_stats().items():
            logger.info(f"  {k}: {v}")
    finally:
        adapter.close()


if __name__ == "__main__":
    main()
