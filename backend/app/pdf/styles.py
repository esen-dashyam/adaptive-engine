"""Evlin brand styles for PDF generation."""
from __future__ import annotations
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch, cm
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY, TA_RIGHT

# ── Color Palette ─────────────────────────────────────────
EVLIN_NAVY = HexColor("#1B2A4A")
EVLIN_BLUE = HexColor("#2E5090")
EVLIN_LIGHT_BLUE = HexColor("#D6E4F0")
EVLIN_ACCENT = HexColor("#E8913A")
EVLIN_GRAY = HexColor("#F5F5F5")
EVLIN_DARK_GRAY = HexColor("#666666")
EVLIN_TEXT = HexColor("#333333")
EVLIN_GREEN = HexColor("#27AE60")
EVLIN_RED = HexColor("#C0392B")

# ── Page Dimensions ───────────────────────────────────────
PAGE_MARGIN = 0.75 * inch
HEADER_HEIGHT = 0.6 * inch
FOOTER_HEIGHT = 0.4 * inch

# ── Fonts ─────────────────────────────────────────────────
HEADING_FONT = "Helvetica-Bold"
BODY_FONT = "Helvetica"
MONO_FONT = "Courier"
ITALIC_FONT = "Helvetica-Oblique"


def get_evlin_styles():
    """Get the full set of Evlin-branded paragraph styles."""
    styles = getSampleStyleSheet()

    # Title - main document title
    styles.add(ParagraphStyle(
        name="EvlinTitle",
        fontName=HEADING_FONT,
        fontSize=24,
        leading=28,
        textColor=EVLIN_NAVY,
        spaceAfter=6,
        alignment=TA_LEFT,
    ))

    # Subtitle
    styles.add(ParagraphStyle(
        name="EvlinSubtitle",
        fontName=ITALIC_FONT,
        fontSize=14,
        leading=18,
        textColor=EVLIN_BLUE,
        spaceAfter=16,
        alignment=TA_LEFT,
    ))

    # Section heading
    styles.add(ParagraphStyle(
        name="EvlinH2",
        fontName=HEADING_FONT,
        fontSize=16,
        leading=20,
        textColor=EVLIN_BLUE,
        spaceBefore=18,
        spaceAfter=8,
    ))

    # Subsection heading
    styles.add(ParagraphStyle(
        name="EvlinH3",
        fontName=HEADING_FONT,
        fontSize=13,
        leading=16,
        textColor=EVLIN_NAVY,
        spaceBefore=12,
        spaceAfter=6,
    ))

    # Body text
    styles.add(ParagraphStyle(
        name="EvlinBody",
        fontName=BODY_FONT,
        fontSize=11,
        leading=15,
        textColor=EVLIN_TEXT,
        alignment=TA_JUSTIFY,
        spaceAfter=6,
    ))

    # Problem number label
    styles.add(ParagraphStyle(
        name="ProblemNumber",
        fontName=HEADING_FONT,
        fontSize=11,
        leading=15,
        textColor=EVLIN_BLUE,
        spaceBefore=12,
    ))

    # Problem instruction text
    styles.add(ParagraphStyle(
        name="ProblemInstruction",
        fontName=BODY_FONT,
        fontSize=11,
        leading=15,
        textColor=EVLIN_TEXT,
        leftIndent=24,
        spaceAfter=4,
    ))

    # Problem content (the actual question)
    styles.add(ParagraphStyle(
        name="ProblemContent",
        fontName=MONO_FONT,
        fontSize=12,
        leading=16,
        textColor=EVLIN_NAVY,
        leftIndent=24,
        spaceBefore=4,
        spaceAfter=8,
        alignment=TA_CENTER,
    ))

    # Answer key header
    styles.add(ParagraphStyle(
        name="AnswerHeader",
        fontName=HEADING_FONT,
        fontSize=14,
        leading=18,
        textColor=EVLIN_GREEN,
        spaceBefore=12,
        spaceAfter=8,
    ))

    # Answer text
    styles.add(ParagraphStyle(
        name="AnswerText",
        fontName=BODY_FONT,
        fontSize=10,
        leading=13,
        textColor=EVLIN_DARK_GRAY,
        leftIndent=24,
        spaceAfter=4,
    ))

    # Explanation text
    styles.add(ParagraphStyle(
        name="ExplanationText",
        fontName=ITALIC_FONT,
        fontSize=9,
        leading=12,
        textColor=EVLIN_DARK_GRAY,
        leftIndent=24,
        spaceAfter=8,
    ))

    # Points label
    styles.add(ParagraphStyle(
        name="PointsLabel",
        fontName=BODY_FONT,
        fontSize=9,
        leading=12,
        textColor=EVLIN_ACCENT,
        leftIndent=24,
    ))

    # Footer text
    styles.add(ParagraphStyle(
        name="EvlinFooter",
        fontName=BODY_FONT,
        fontSize=8,
        leading=10,
        textColor=EVLIN_DARK_GRAY,
        alignment=TA_CENTER,
    ))

    # Header text
    styles.add(ParagraphStyle(
        name="EvlinHeader",
        fontName=BODY_FONT,
        fontSize=9,
        leading=11,
        textColor=EVLIN_NAVY,
    ))

    # Table cell
    styles.add(ParagraphStyle(
        name="TableCell",
        fontName=BODY_FONT,
        fontSize=10,
        leading=13,
        textColor=EVLIN_TEXT,
    ))

    # Table header
    styles.add(ParagraphStyle(
        name="TableHeader",
        fontName=HEADING_FONT,
        fontSize=10,
        leading=13,
        textColor=HexColor("#FFFFFF"),
    ))

    return styles
