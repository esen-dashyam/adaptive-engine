"""OCR service — extract text from images/PDFs using Gemini Vision + PyMuPDF.

Primary: Gemini Vision (excellent at handwriting recognition)
Fallback: PyMuPDF for native-text PDFs (instant, no API call)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.app.core.settings import settings

log = logging.getLogger(__name__)


@dataclass
class OCRResult:
    text: str
    confidence: float
    method: str  # "gemini_vision", "pymupdf", or "none"


# ── Gemini Vision extraction ─────────────────────────────

HANDWRITING_PROMPT = """You are an OCR system specialized in reading handwritten student work.

Examine this image of a student's handwritten homework or quiz answers.

Instructions:
1. Extract ALL handwritten text exactly as written by the student.
2. Preserve the structure: if answers are numbered, keep the numbering.
3. If there are math expressions, represent them in a readable text format (e.g., "3/4 + 1/2 = 5/4").
4. If a word or number is unclear, provide your best guess with [?] after it.
5. Separate distinct answers or sections with blank lines.
6. Do NOT add any commentary, grading, or corrections — just transcribe what is written.

Return ONLY the transcribed text, nothing else."""


def extract_with_gemini_vision(image_bytes: bytes, mime_type: str) -> OCRResult:
    """Use Gemini Vision to extract handwritten text from an image."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)

    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    resp = client.models.generate_content(
        model=settings.gemini_model,
        contents=[image_part, HANDWRITING_PROMPT],
        config=types.GenerateContentConfig(temperature=0.1),
    )

    text = resp.text.strip()
    return OCRResult(text=text, confidence=92.0, method="gemini_vision")


# ── PyMuPDF for native-text PDFs ──────────────────────────


def extract_with_pymupdf(pdf_bytes: bytes) -> OCRResult | None:
    """Extract text from a native-text PDF. Returns None if no text found."""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text_parts = []
    for page in doc:
        page_text = page.get_text().strip()
        if page_text:
            text_parts.append(page_text)
    doc.close()

    full_text = "\n\n".join(text_parts)
    if len(full_text.strip()) > 50:
        return OCRResult(text=full_text, confidence=99.0, method="pymupdf")
    return None


# ── Main dispatch ─────────────────────────────────────────

MIME_MAP = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".webp": "image/webp",
    ".heic": "image/heic",
}


def process_file(file_bytes: bytes, filename: str) -> OCRResult:
    """Process an uploaded file and extract text.

    Strategy:
    1. For PDFs: try PyMuPDF first (native text), then Gemini Vision per page
    2. For images: use Gemini Vision directly
    """
    ext = Path(filename).suffix.lower()
    mime = MIME_MAP.get(ext)
    if not mime:
        raise ValueError(f"Unsupported file format: {ext}")

    # PDF path
    if ext == ".pdf":
        # Try native text extraction first
        pymupdf_result = extract_with_pymupdf(file_bytes)
        if pymupdf_result:
            return pymupdf_result

        # PDF is image-based — convert pages to images and use Gemini Vision
        if settings.gemini_api_key:
            import fitz

            doc = fitz.open(stream=file_bytes, filetype="pdf")
            all_text = []
            for page in doc:
                pix = page.get_pixmap(dpi=200)
                png_bytes = pix.tobytes("png")
                try:
                    result = extract_with_gemini_vision(png_bytes, "image/png")
                    all_text.append(result.text)
                except Exception as e:
                    log.warning("Gemini Vision failed on PDF page: %s", e)
            doc.close()
            if all_text:
                return OCRResult(
                    text="\n\n".join(all_text),
                    confidence=90.0,
                    method="gemini_vision",
                )

        return OCRResult(text="", confidence=0.0, method="none")

    # Image path
    if settings.gemini_api_key:
        return extract_with_gemini_vision(file_bytes, mime)

    return OCRResult(text="", confidence=0.0, method="none")


# ── Supabase persistence ──────────────────────────────────


def save_ocr_document(
    filename: str,
    extracted_text: str,
    confidence: float,
    method: str,
) -> dict:
    """Insert an OCR document record into Supabase (best-effort)."""
    from backend.app.services.supabase_client import get_supabase

    sb = get_supabase()
    data = {
        "original_filename": filename,
        "minio_key": f"ocr/{filename}",
        "extracted_text": extracted_text,
        "confidence": confidence,
        "status": "completed",
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    return sb.table("ocr_documents").insert(data).execute().data[0]
