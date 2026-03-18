"""Scheduler — OCR endpoints for handwriting extraction.

POST /api/v1/scheduler/ocr/extract   Upload image → extract text via Gemini Vision
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from loguru import logger
from pydantic import BaseModel

router = APIRouter(prefix="/scheduler/ocr", tags=["Scheduler — OCR"])

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".heic"}


class OCRResponse(BaseModel):
    text: str
    confidence: float
    method: str
    filename: str


@router.post("/extract", summary="Extract text from uploaded image via OCR")
async def extract_text(file: UploadFile = File(...)) -> OCRResponse:
    """Upload an image of handwritten work and extract text.

    Accepts: PNG, JPG, JPEG, WEBP, HEIC, TIFF, PDF (up to 10 MB)
    """
    filename = file.filename or "upload.png"
    ext = Path(filename).suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    file_bytes = await file.read()

    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({len(file_bytes) / 1024 / 1024:.1f} MB). Maximum: 10 MB.",
        )

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file uploaded.")

    try:
        from backend.app.services.ocr_service import process_file, save_ocr_document

        result = process_file(file_bytes, filename)

        if not result.text:
            raise HTTPException(
                status_code=422,
                detail="Could not extract text from the uploaded file. "
                       "Please ensure the image is clear and contains readable text.",
            )

        # Persist to Supabase (best-effort)
        try:
            save_ocr_document(
                filename=filename,
                extracted_text=result.text,
                confidence=result.confidence,
                method=result.method,
            )
        except Exception as e:
            logger.warning("Failed to save OCR document to Supabase: %s", e)

        return OCRResponse(
            text=result.text,
            confidence=result.confidence,
            method=result.method,
            filename=filename,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("OCR extraction failed: %s", e)
        raise HTTPException(status_code=500, detail=f"OCR processing error: {e}")
