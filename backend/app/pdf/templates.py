"""PDF templates — schedule report, semester calendar, course overview, practice problems.

Merged from Calendar Scheduler pdf/templates/*.py with import paths adjusted.
"""
from __future__ import annotations

import calendar as cal_mod
from io import BytesIO
from datetime import date, timedelta

from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame,
    Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak,
)
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor

from backend.app.pdf.styles import (
    get_evlin_styles,
    EVLIN_NAVY, EVLIN_BLUE, EVLIN_LIGHT_BLUE,
    EVLIN_GRAY, EVLIN_DARK_GRAY, EVLIN_ACCENT, EVLIN_TEXT,
    EVLIN_GREEN, EVLIN_RED,
    HEADING_FONT, BODY_FONT, ITALIC_FONT, PAGE_MARGIN,
)
from backend.app.pdf.textbook_base import EvlinTextbookDoc

# ═══════════════════════════════════════════════════════════
#  SCHEDULE REPORT
# ═══════════════════════════════════════════════════════════

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

SCHEDULE_SUBJECT_COLORS = {
    "Math": HexColor("#4A90D9"),
    "Science": HexColor("#27AE60"),
    "English": HexColor("#E67E22"),
    "History": HexColor("#8E44AD"),
    "Art": HexColor("#E74C3C"),
    "PE": HexColor("#16A085"),
}


def build_schedule_report_pdf(student: dict, schedules: list[dict], slots: list[dict]) -> bytes:
    """Generate a student schedule report PDF."""
    buffer = BytesIO()
    styles = get_evlin_styles()

    student_name = f"{student['first_name']} {student['last_name']}"

    doc = EvlinTextbookDoc(
        buffer,
        title=f"Schedule Report - {student_name}",
        subtitle=f"Grade {student['grade_level']} | {date.today().strftime('%B %Y')}",
    )

    story: list = []

    # Title section
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("Student Schedule Report", styles["EvlinTitle"]))
    story.append(Paragraph(
        f"{student_name} — Grade {student['grade_level']}",
        styles["EvlinSubtitle"],
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=EVLIN_BLUE))

    # Student info
    story.append(Spacer(1, 0.2 * inch))
    info_items = [
        ["Student", student_name],
        ["Grade", str(student["grade_level"])],
        ["Report Date", date.today().strftime("%B %d, %Y")],
    ]
    if student.get("parent_name"):
        info_items.append(["Parent/Guardian", student["parent_name"]])

    info_table = Table(info_items, colWidths=[1.5 * inch, 4 * inch])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), EVLIN_LIGHT_BLUE),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.5, EVLIN_LIGHT_BLUE),
    ]))
    story.append(info_table)

    # Course summary
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("Enrolled Courses", styles["EvlinH2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=EVLIN_LIGHT_BLUE))
    story.append(Spacer(1, 0.1 * inch))

    if schedules:
        course_data = [["Code", "Course Title", "Subject", "Hrs/Wk", "Status"]]
        for sch in schedules:
            course = sch.get("courses", {})
            course_data.append([
                course.get("code", ""),
                course.get("title", ""),
                course.get("subject", ""),
                str(course.get("hours_per_week", "")),
                sch.get("status", "").title(),
            ])

        course_table = Table(
            course_data,
            colWidths=[1 * inch, 2.5 * inch, 1 * inch, 0.8 * inch, 0.8 * inch],
        )
        course_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), EVLIN_NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#FFFFFF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("PADDING", (0, 0), (-1, -1), 6),
            ("GRID", (0, 0), (-1, -1), 0.5, EVLIN_LIGHT_BLUE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#FFFFFF"), EVLIN_GRAY]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(course_table)

        total_hours = sum(
            s.get("courses", {}).get("hours_per_week", 0) for s in schedules
        )
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph(
            f"<b>Total Weekly Hours: {total_hours:.1f}</b>",
            styles["EvlinBody"],
        ))
    else:
        story.append(Paragraph("No active courses enrolled.", styles["EvlinBody"]))

    # Weekly schedule grid
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("Weekly Schedule", styles["EvlinH2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=EVLIN_LIGHT_BLUE))
    story.append(Spacer(1, 0.1 * inch))

    if slots:
        slots_by_day: dict[int, list] = {}
        for s in slots:
            d = s.get("day_of_week", 0)
            if d not in slots_by_day:
                slots_by_day[d] = []
            slots_by_day[d].append(s)

        sched_data = [["Day", "Time", "Course", "Location"]]
        for day_idx in sorted(slots_by_day.keys()):
            day_slots = sorted(slots_by_day[day_idx], key=lambda x: str(x.get("start_time", "")))
            for s in day_slots:
                sched_data.append([
                    DAY_NAMES[day_idx],
                    f"{str(s.get('start_time', ''))[:5]} - {str(s.get('end_time', ''))[:5]}",
                    f"{s.get('course_code', '')} {s.get('course_title', '')}",
                    s.get("location", "Home"),
                ])

        sched_table = Table(
            sched_data,
            colWidths=[1.2 * inch, 1.2 * inch, 2.8 * inch, 1 * inch],
        )
        sched_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), EVLIN_NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#FFFFFF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("PADDING", (0, 0), (-1, -1), 6),
            ("GRID", (0, 0), (-1, -1), 0.5, EVLIN_LIGHT_BLUE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#FFFFFF"), EVLIN_GRAY]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(sched_table)
    else:
        story.append(Paragraph("No scheduled time slots.", styles["EvlinBody"]))

    doc.build(story)
    return buffer.getvalue()


# ═══════════════════════════════════════════════════════════
#  SEMESTER CALENDAR
# ═══════════════════════════════════════════════════════════

CAL_PAGE_WIDTH, CAL_PAGE_HEIGHT = landscape(letter)
DAY_HEADERS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

CALENDAR_SUBJECT_COLORS: dict[str, tuple[str, str]] = {
    "Math":    ("#4A90D9", "#EAF1FA"),
    "Science": ("#27AE60", "#E8F8EF"),
    "English": ("#E67E22", "#FDF2E9"),
    "History": ("#8E44AD", "#F4ECF7"),
    "Art":     ("#E74C3C", "#FDEDEC"),
    "PE":      ("#16A085", "#E8F6F3"),
}


class _CalendarDoc(BaseDocTemplate):
    """Landscape document for calendar pages."""

    def __init__(self, filename, title="", subtitle="", **kwargs):
        self.doc_title = title
        self.doc_subtitle = subtitle
        margin = 0.5 * inch

        super().__init__(
            filename,
            pagesize=landscape(letter),
            leftMargin=margin,
            rightMargin=margin,
            topMargin=margin + 0.4 * inch,
            bottomMargin=margin + 0.25 * inch,
            **kwargs,
        )

        content_width = CAL_PAGE_WIDTH - 2 * margin
        content_height = CAL_PAGE_HEIGHT - 2 * margin - 0.4 * inch - 0.25 * inch

        frame = Frame(margin, margin + 0.25 * inch, content_width, content_height, id="cal")
        template = PageTemplate(id="calendar", frames=[frame], onPage=self._draw_page)
        self.addPageTemplates([template])

    def _draw_page(self, canvas, doc):
        canvas.saveState()
        margin = 0.5 * inch

        # Header bar
        header_y = CAL_PAGE_HEIGHT - margin
        canvas.setFillColor(EVLIN_NAVY)
        canvas.rect(margin, header_y - 2, CAL_PAGE_WIDTH - 2 * margin, 22, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#FFFFFF"))
        canvas.setFont(HEADING_FONT, 9)
        canvas.drawString(margin + 6, header_y + 2, "EVLIN EDUCATION")
        canvas.setFont(BODY_FONT, 8)
        canvas.drawRightString(CAL_PAGE_WIDTH - margin - 6, header_y + 2, self.doc_title[:80])

        # Accent line
        canvas.setStrokeColor(EVLIN_BLUE)
        canvas.setLineWidth(1.5)
        canvas.line(margin, header_y - 4, CAL_PAGE_WIDTH - margin, header_y - 4)

        # Footer
        footer_y = margin - 0.08 * inch
        canvas.setStrokeColor(EVLIN_LIGHT_BLUE)
        canvas.setLineWidth(0.5)
        canvas.line(margin, footer_y + 10, CAL_PAGE_WIDTH - margin, footer_y + 10)
        canvas.setFillColor(EVLIN_DARK_GRAY)
        canvas.setFont(BODY_FONT, 7)
        canvas.drawString(margin, footer_y, "Evlin Homeschool Education Platform")
        canvas.drawRightString(CAL_PAGE_WIDTH - margin, footer_y, f"Page {canvas.getPageNumber()}")
        if self.doc_subtitle:
            canvas.drawCentredString(CAL_PAGE_WIDTH / 2, footer_y, self.doc_subtitle)

        canvas.restoreState()


def _build_month_page(
    year: int,
    month: int,
    slots: list[dict],
    schedules: list[dict],
    styles,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list:
    """Build flowables for one month calendar page."""
    month_name = cal_mod.month_name[month]
    story: list = []

    # Month title
    story.append(Paragraph(
        f'<font size="20" color="#{EVLIN_NAVY.hexval()[2:]}">{month_name} {year}</font>',
        styles["EvlinTitle"],
    ))
    story.append(Spacer(1, 0.1 * inch))

    # Build lookup: day_of_week -> list of course entries
    slot_by_dow: dict[int, list] = {}
    for s in slots:
        dow = s.get("day_of_week", 0)
        if dow not in slot_by_dow:
            slot_by_dow[dow] = []
        slot_by_dow[dow].append(s)

    # Course subject lookup
    course_subjects: dict[str, str] = {}
    for sch in schedules:
        c = sch.get("courses", {})
        code = c.get("code", "")
        course_subjects[code] = c.get("subject", "")

    # Calendar matrix (weeks as rows, 0=Monday)
    cal = cal_mod.Calendar(firstweekday=0)
    month_weeks = cal.monthdayscalendar(year, month)

    # Ensure exactly 6 rows for consistent layout
    while len(month_weeks) < 6:
        month_weeks.append([0] * 7)

    col_w = (CAL_PAGE_WIDTH - 1.0 * inch) / 7.0

    # Header row
    header_row = []
    for dh in DAY_HEADERS:
        header_row.append(Paragraph(
            f'<font name="{HEADING_FONT}" size="9" color="#FFFFFF">{dh}</font>',
            styles["Normal"],
        ))

    data_rows = [header_row]
    cell_styles: list = []

    for week_idx, week in enumerate(month_weeks):
        row = []
        for day_idx, day_num in enumerate(week):
            if day_num == 0:
                row.append("")
                continue

            this_date = date(year, month, day_num)
            is_outside = False
            if start_date and this_date < start_date:
                is_outside = True
            if end_date and this_date > end_date:
                is_outside = True

            day_color = EVLIN_DARK_GRAY.hexval()[2:] if is_outside else EVLIN_NAVY.hexval()[2:]
            cell_content = f'<font name="{HEADING_FONT}" size="9" color="#{day_color}">{day_num}</font>'

            if this_date == date.today():
                cell_content = (
                    f'<font name="{HEADING_FONT}" size="9" color="#{EVLIN_ACCENT.hexval()[2:]}">'
                    f'<u>{day_num}</u></font>'
                )

            if not is_outside and day_idx in slot_by_dow:
                for s in slot_by_dow[day_idx]:
                    code = s.get("course_code", "")
                    subj = course_subjects.get(code, s.get("subject", ""))
                    fg, bg = CALENDAR_SUBJECT_COLORS.get(subj, ("#333333", "#F0F0F0"))
                    time_str = f"{str(s.get('start_time', ''))[:5]}"
                    cell_content += (
                        f'<br/><font name="{BODY_FONT}" size="6" color="{fg}">'
                        f'{code} {time_str}</font>'
                    )

            row_in_table = week_idx + 1
            if day_idx >= 5:
                cell_styles.append(
                    ("BACKGROUND", (day_idx, row_in_table), (day_idx, row_in_table),
                     HexColor("#F8F8F8"))
                )

            if is_outside:
                cell_styles.append(
                    ("BACKGROUND", (day_idx, row_in_table), (day_idx, row_in_table),
                     HexColor("#F0F0F0"))
                )

            row.append(Paragraph(cell_content, styles["Normal"]))
        data_rows.append(row)

    row_h = 0.82 * inch
    header_h = 0.3 * inch

    tbl = Table(
        data_rows,
        colWidths=[col_w] * 7,
        rowHeights=[header_h] + [row_h] * 6,
    )

    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), EVLIN_NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#FFFFFF")),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("VALIGN", (0, 1), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, EVLIN_LIGHT_BLUE),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
    ]
    style_commands.extend(cell_styles)

    tbl.setStyle(TableStyle(style_commands))
    story.append(tbl)

    return story


def build_semester_calendar_pdf(
    student: dict,
    schedules: list[dict],
    slots: list[dict],
    num_months: int = 3,
    start_date: date | None = None,
) -> bytes:
    """Generate a multi-month semester calendar PDF (landscape, one month per page)."""
    buffer = BytesIO()
    styles = get_evlin_styles()

    student_name = f"{student['first_name']} {student['last_name']}"

    if start_date is None:
        today = date.today()
        start_date = today.replace(day=1)

    end_date = start_date
    for _ in range(num_months):
        if end_date.month == 12:
            end_date = end_date.replace(year=end_date.year + 1, month=1, day=1)
        else:
            end_date = end_date.replace(month=end_date.month + 1, day=1)
    end_date = end_date - timedelta(days=1)

    start_str = start_date.strftime("%b %Y")
    end_str = end_date.strftime("%b %Y")

    doc = _CalendarDoc(
        buffer,
        title=f"Course Calendar — {student_name}",
        subtitle=f"Grade {student['grade_level']} | {start_str} – {end_str}",
    )

    story: list = []

    # Cover / Summary Page
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("Semester Course Calendar", styles["EvlinTitle"]))
    story.append(Paragraph(
        f"{student_name} — Grade {student['grade_level']}",
        styles["EvlinSubtitle"],
    ))
    story.append(Paragraph(
        f"{start_str} – {end_str}  ({num_months} months)",
        styles["EvlinBody"],
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=EVLIN_BLUE))
    story.append(Spacer(1, 0.2 * inch))

    # Course legend
    if schedules:
        story.append(Paragraph("Enrolled Courses", styles["EvlinH2"]))

        legend_data = [["Code", "Course", "Subject", "Hrs/Wk", "Schedule"]]
        for sch in schedules:
            c = sch.get("courses", {})
            code = c.get("code", "")

            sch_slots = [s for s in slots if s.get("course_code") == code]
            day_names_short = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            sched_parts = []
            for s in sorted(sch_slots, key=lambda x: x.get("day_of_week", 0)):
                dn = day_names_short[s["day_of_week"]]
                t = str(s.get("start_time", ""))[:5]
                sched_parts.append(f"{dn} {t}")
            sched_str = ", ".join(sched_parts) if sched_parts else "TBD"

            legend_data.append([
                code,
                c.get("title", ""),
                c.get("subject", ""),
                str(c.get("hours_per_week", "")),
                sched_str,
            ])

        legend_tbl = Table(
            legend_data,
            colWidths=[0.9 * inch, 2.8 * inch, 0.9 * inch, 0.7 * inch, 2.5 * inch],
        )
        legend_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), EVLIN_NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#FFFFFF")),
            ("FONTNAME", (0, 0), (-1, 0), HEADING_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("PADDING", (0, 0), (-1, -1), 5),
            ("GRID", (0, 0), (-1, -1), 0.5, EVLIN_LIGHT_BLUE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#FFFFFF"), EVLIN_GRAY]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(legend_tbl)

        # Color legend
        story.append(Spacer(1, 0.15 * inch))
        subjects_in_use = set()
        for sch in schedules:
            subjects_in_use.add(sch.get("courses", {}).get("subject", ""))

        color_parts = []
        for subj in sorted(subjects_in_use):
            fg, _ = CALENDAR_SUBJECT_COLORS.get(subj, ("#333", "#FFF"))
            color_parts.append(f'<font color="{fg}"><b>■</b></font> {subj}')
        if color_parts:
            story.append(Paragraph(
                "Color key:  " + "    ".join(color_parts),
                styles["EvlinBody"],
            ))

        total_hours = sum(s.get("courses", {}).get("hours_per_week", 0) for s in schedules)
        story.append(Paragraph(
            f"<b>Total Weekly Hours: {total_hours:.1f}</b>",
            styles["EvlinBody"],
        ))
    else:
        story.append(Paragraph("No active courses enrolled.", styles["EvlinBody"]))

    # Monthly Calendar Pages
    cur = start_date
    for i in range(num_months):
        story.append(PageBreak())
        month_story = _build_month_page(
            year=cur.year,
            month=cur.month,
            slots=slots,
            schedules=schedules,
            styles=styles,
            start_date=start_date,
            end_date=end_date,
        )
        story.extend(month_story)

        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)

    doc.build(story)
    return buffer.getvalue()


# ═══════════════════════════════════════════════════════════
#  COURSE OVERVIEW
# ═══════════════════════════════════════════════════════════

def build_course_overview_pdf(course: dict) -> bytes:
    """Generate a course overview PDF."""
    buffer = BytesIO()
    styles = get_evlin_styles()

    doc = EvlinTextbookDoc(
        buffer,
        title=f"{course['code']} - {course['title']}",
        subtitle=course.get("subject", ""),
    )

    story: list = []

    # Title
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(f"{course['code']}", styles["EvlinSubtitle"]))
    story.append(Paragraph(course["title"], styles["EvlinTitle"]))
    story.append(HRFlowable(width="100%", thickness=2, color=EVLIN_BLUE))
    story.append(Spacer(1, 0.2 * inch))

    # Course details table
    details = [
        ["Subject", course.get("subject", "")],
        ["Grade Level", f"{course.get('grade_level_min', '')}-{course.get('grade_level_max', '')}"],
        ["Difficulty", course.get("difficulty", "standard").title()],
        ["Duration", f"{course.get('duration_weeks', 12)} weeks"],
        ["Hours per Week", str(course.get("hours_per_week", 3.0))],
    ]

    if course.get("prerequisites"):
        details.append(["Prerequisites", ", ".join(course["prerequisites"])])
    if course.get("tags"):
        details.append(["Tags", ", ".join(course["tags"])])

    detail_table = Table(details, colWidths=[1.8 * inch, 4 * inch])
    detail_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), EVLIN_LIGHT_BLUE),
        ("TEXTCOLOR", (0, 0), (0, -1), EVLIN_NAVY),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("PADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, EVLIN_LIGHT_BLUE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (1, 0), (1, -1), [HexColor("#FFFFFF"), EVLIN_GRAY]),
    ]))
    story.append(detail_table)

    # Description
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("Course Description", styles["EvlinH2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=EVLIN_LIGHT_BLUE))
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph(
        course.get("description", "No description available."),
        styles["EvlinBody"],
    ))

    # Learning objectives (placeholder)
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("Learning Objectives", styles["EvlinH2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=EVLIN_LIGHT_BLUE))
    story.append(Spacer(1, 0.1 * inch))

    objectives = [
        "Develop foundational understanding of core concepts",
        "Apply knowledge through hands-on activities and exercises",
        "Build critical thinking and problem-solving skills",
        "Prepare for the next level of study in this subject",
    ]
    for i, obj in enumerate(objectives, 1):
        story.append(Paragraph(f"{i}. {obj}", styles["EvlinBody"]))

    doc.build(story)
    return buffer.getvalue()


# ═══════════════════════════════════════════════════════════
#  PRACTICE PROBLEMS
# ═══════════════════════════════════════════════════════════

def build_practice_problems_pdf(
    title: str,
    subject: str,
    grade: int,
    problems: list[dict],
    include_answers: bool = True,
) -> bytes:
    """Generate a textbook-quality practice problems PDF."""
    buffer = BytesIO()
    styles = get_evlin_styles()

    doc = EvlinTextbookDoc(
        buffer,
        title=title,
        subtitle=f"{subject} | Grade {grade}",
    )

    story: list = []

    # Cover Section
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(title, styles["EvlinTitle"]))
    story.append(Paragraph(f"{subject} — Grade {grade}", styles["EvlinSubtitle"]))

    # Info box
    total_points = sum(p.get("points", 0) for p in problems)
    info_data = [
        ["Total Questions", str(len(problems))],
        ["Total Points", str(total_points)],
        ["Subject", subject],
        ["Grade Level", str(grade)],
    ]
    info_table = Table(info_data, colWidths=[1.5 * inch, 2 * inch])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), EVLIN_LIGHT_BLUE),
        ("TEXTCOLOR", (0, 0), (0, -1), EVLIN_NAVY),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.5, EVLIN_LIGHT_BLUE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(Spacer(1, 0.2 * inch))
    story.append(info_table)

    # Name/Date line
    story.append(Spacer(1, 0.3 * inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=EVLIN_DARK_GRAY))
    story.append(Spacer(1, 0.05 * inch))
    story.append(Paragraph(
        "Name: ________________________________    Date: ________________",
        styles["EvlinBody"],
    ))
    story.append(Spacer(1, 0.2 * inch))
    story.append(HRFlowable(width="100%", thickness=1, color=EVLIN_BLUE))

    # Problems Section
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("Problems", styles["EvlinH2"]))

    for p in problems:
        num = p.get("number", "?")
        instruction = p.get("instruction", "")
        content = p.get("content", "")
        points = p.get("points", 0)
        prob_type = p.get("type", "short_answer")

        story.append(Paragraph(
            f"<b>Problem {num}</b>",
            styles["ProblemNumber"],
        ))
        story.append(Paragraph(
            f"[{points} point{'s' if points != 1 else ''}]",
            styles["PointsLabel"],
        ))

        if instruction:
            story.append(Paragraph(instruction, styles["ProblemInstruction"]))

        if content:
            story.append(Paragraph(
                f"<b>{content}</b>",
                styles["ProblemContent"],
            ))

        if prob_type == "essay":
            for _ in range(6):
                story.append(Spacer(1, 0.05 * inch))
                story.append(HRFlowable(
                    width="90%", thickness=0.3, color=EVLIN_LIGHT_BLUE,
                    spaceAfter=8,
                ))
        elif prob_type == "true_false":
            story.append(Spacer(1, 0.05 * inch))
            story.append(Paragraph(
                "&nbsp;&nbsp;&nbsp;&nbsp;☐ True&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;☐ False",
                styles["ProblemInstruction"],
            ))
        else:
            story.append(Spacer(1, 0.1 * inch))
            story.append(Paragraph(
                "Answer: _____________________________________________",
                styles["ProblemInstruction"],
            ))

        story.append(Spacer(1, 0.1 * inch))

    # Answer Key Section
    if include_answers:
        story.append(PageBreak())
        story.append(Paragraph("Answer Key", styles["AnswerHeader"]))
        story.append(HRFlowable(width="100%", thickness=1, color=EVLIN_GREEN))
        story.append(Spacer(1, 0.15 * inch))

        for p in problems:
            num = p.get("number", "?")
            answer = p.get("answer", "")
            explanation = p.get("explanation", "")
            points = p.get("points", 0)

            story.append(Paragraph(
                f"<b>Problem {num}</b> [{points} pts]",
                styles["ProblemNumber"],
            ))
            story.append(Paragraph(
                f"<b>Answer:</b> {answer}",
                styles["AnswerText"],
            ))
            if explanation:
                story.append(Paragraph(
                    f"<i>Explanation:</i> {explanation}",
                    styles["ExplanationText"],
                ))

    doc.build(story)
    return buffer.getvalue()
