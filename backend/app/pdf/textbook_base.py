"""Base textbook document class with consistent header/footer."""
from __future__ import annotations
from reportlab.platypus import BaseDocTemplate, PageTemplate, Frame
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor

from backend.app.pdf.styles import (
    EVLIN_NAVY, EVLIN_BLUE, EVLIN_LIGHT_BLUE, EVLIN_DARK_GRAY,
    PAGE_MARGIN, HEADING_FONT, BODY_FONT,
)

PAGE_WIDTH, PAGE_HEIGHT = letter


class EvlinTextbookDoc(BaseDocTemplate):
    """Base document with Evlin-branded header and footer."""

    def __init__(self, filename, title="", subtitle="", **kwargs):
        self.doc_title = title
        self.doc_subtitle = subtitle

        super().__init__(
            filename,
            pagesize=letter,
            leftMargin=PAGE_MARGIN,
            rightMargin=PAGE_MARGIN,
            topMargin=PAGE_MARGIN + 0.5 * inch,  # Room for header
            bottomMargin=PAGE_MARGIN + 0.3 * inch,  # Room for footer
            **kwargs,
        )

        content_width = PAGE_WIDTH - 2 * PAGE_MARGIN
        content_height = (
            PAGE_HEIGHT
            - PAGE_MARGIN * 2
            - 0.5 * inch   # header
            - 0.3 * inch   # footer
        )

        frame = Frame(
            PAGE_MARGIN,
            PAGE_MARGIN + 0.3 * inch,
            content_width,
            content_height,
            id="main",
        )

        template = PageTemplate(
            id="textbook",
            frames=[frame],
            onPage=self._draw_page,
        )
        self.addPageTemplates([template])

    def _draw_page(self, canvas, doc):
        """Draw header and footer on each page."""
        canvas.saveState()
        width = PAGE_WIDTH
        margin = PAGE_MARGIN

        # ── Header ────────────────────────────────────────
        header_y = PAGE_HEIGHT - PAGE_MARGIN

        # Header background bar
        canvas.setFillColor(EVLIN_NAVY)
        canvas.rect(margin, header_y - 2, width - 2 * margin, 24, fill=1, stroke=0)

        # Title in header
        canvas.setFillColor(HexColor("#FFFFFF"))
        canvas.setFont(HEADING_FONT, 10)
        canvas.drawString(margin + 8, header_y + 4, f"EVLIN EDUCATION")

        # Document title on right
        canvas.setFont(BODY_FONT, 9)
        title_text = self.doc_title[:60]
        canvas.drawRightString(width - margin - 8, header_y + 4, title_text)

        # Thin accent line below header
        canvas.setStrokeColor(EVLIN_BLUE)
        canvas.setLineWidth(2)
        canvas.line(margin, header_y - 4, width - margin, header_y - 4)

        # ── Footer ────────────────────────────────────────
        footer_y = PAGE_MARGIN - 0.1 * inch

        # Thin line above footer
        canvas.setStrokeColor(EVLIN_LIGHT_BLUE)
        canvas.setLineWidth(0.5)
        canvas.line(margin, footer_y + 12, width - margin, footer_y + 12)

        # Footer text
        canvas.setFillColor(EVLIN_DARK_GRAY)
        canvas.setFont(BODY_FONT, 8)
        canvas.drawString(margin, footer_y, "Evlin Homeschool Education Platform")

        # Page number
        page_num = canvas.getPageNumber()
        canvas.drawRightString(width - margin, footer_y, f"Page {page_num}")

        # Center: subtitle or date
        if self.doc_subtitle:
            canvas.drawCentredString(width / 2, footer_y, self.doc_subtitle)

        canvas.restoreState()
