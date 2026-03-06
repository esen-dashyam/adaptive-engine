"""
PDF text extraction for the Adaptive Learning Engine.

Supports PyMuPDF (local) for page-level text extraction
and produces chunks suitable for Neo4j ingestion and RAG.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger


def extract_pages(pdf_path: str | Path) -> list[dict[str, Any]]:
    """
    Extract text page-by-page from a PDF.

    Returns:
        List of {"page": int, "text": str, "metadata": dict}
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        import fitz
    except ImportError:
        raise ImportError("Install pymupdf: poetry add pymupdf")

    pages: list[dict[str, Any]] = []
    doc = fitz.open(str(pdf_path))

    for i in range(len(doc)):
        page = doc[i]
        text = page.get_text("text").strip()
        if text:
            pages.append(
                {
                    "page": i + 1,
                    "text": text,
                    "metadata": {
                        "filename": pdf_path.name,
                        "total_pages": len(doc),
                    },
                }
            )

    doc.close()
    logger.info(f"Extracted {len(pages)} pages from {pdf_path.name}")
    return pages


def chunk_pages(
    pages: list[dict[str, Any]],
    max_chars: int = 3000,
    overlap_chars: int = 200,
) -> list[dict[str, Any]]:
    """
    Merge and split pages into fixed-size chunks with optional overlap.

    Returns:
        List of {"text": str, "pages": [int], "metadata": dict}
    """
    full_text = "\n\n".join(p["text"] for p in pages)
    all_page_refs = [p["page"] for p in pages]
    meta = pages[0]["metadata"] if pages else {}

    chunks: list[dict[str, Any]] = []
    start = 0
    chunk_index = 0

    while start < len(full_text):
        end = min(start + max_chars, len(full_text))
        chunk_text = full_text[start:end].strip()
        if chunk_text:
            chunks.append(
                {
                    "text": chunk_text,
                    "pages": all_page_refs,
                    "chunk_index": chunk_index,
                    "metadata": meta,
                }
            )
            chunk_index += 1
        start = end - overlap_chars if end < len(full_text) else end

    logger.info(f"Created {len(chunks)} chunks from {len(pages)} pages")
    return chunks


def parse_pdf(
    pdf_path: str | Path,
    max_chars: int = 3000,
    overlap_chars: int = 200,
) -> list[dict[str, Any]]:
    """
    Full pipeline: extract pages → chunk.

    Returns the list of chunk dicts ready for Neo4j ingestion.
    """
    pages = extract_pages(pdf_path)
    return chunk_pages(pages, max_chars=max_chars, overlap_chars=overlap_chars)
