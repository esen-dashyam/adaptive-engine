#!/usr/bin/env python3
"""Add two more students (Liam & Sophia) to Supabase alongside Emma.

Does NOT clear existing data — only inserts new records.
Reuses the existing course catalog (MATH-8A … SPAN-8A).

Usage:
    cd adaptive-engine
    poetry run python scripts/add_liam_sophia.py
"""
from __future__ import annotations

import os
import random
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

# ── Load env ───────────────────────────────────────────────
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

random.seed(42)  # Reproducible completion patterns


# ============================================================
# 1. STUDENT DEFINITIONS
# ============================================================
STUDENTS = [
    {
        "first_name": "Liam",
        "last_name": "Johnson",
        "grade_level": 5,
        "date_of_birth": "2015-03-22",
        "parent_name": "Sarah Johnson",
        "parent_email": "sarah.johnson@example.com",
        "notes": "5th grade homeschool student. Strong in math, developing reading skills. Loves science experiments.",
    },
    {
        "first_name": "Sophia",
        "last_name": "Johnson",
        "grade_level": 3,
        "date_of_birth": "2017-09-10",
        "parent_name": "Sarah Johnson",
        "parent_email": "sarah.johnson@example.com",
        "notes": "3rd grade homeschool student. Creative and artistic. Enjoys reading and storytelling.",
    },
]


# ============================================================
# 2. GRADE-APPROPRIATE COURSES
#    Reuse catalog entries if they exist; create new ones for
#    grade-specific courses that don't exist yet.
# ============================================================
LIAM_COURSES = [
    {
        "code": "MATH-5A",
        "title": "Pre-Algebra Basics",
        "subject": "Math",
        "grade_level_min": 4,
        "grade_level_max": 6,
        "description": "Fractions, decimals, early algebra concepts, and basic geometry.",
        "duration_weeks": 12,
        "hours_per_week": 4.0,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "SCI-5A",
        "title": "Earth & Space Science",
        "subject": "Science",
        "grade_level_min": 4,
        "grade_level_max": 6,
        "description": "Solar system, weather patterns, rocks & minerals, and simple machines.",
        "duration_weeks": 12,
        "hours_per_week": 3.5,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "ELA-5A",
        "title": "Reading Adventures",
        "subject": "Reading",
        "grade_level_min": 4,
        "grade_level_max": 6,
        "description": "Chapter book discussions, vocabulary building, and reading comprehension strategies.",
        "duration_weeks": 12,
        "hours_per_week": 3.0,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "HIST-5A",
        "title": "World Cultures",
        "subject": "History",
        "grade_level_min": 4,
        "grade_level_max": 6,
        "description": "Introduction to world geography, ancient civilizations, and cultural traditions.",
        "duration_weeks": 12,
        "hours_per_week": 2.0,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "WRIT-5A",
        "title": "Creative Writing",
        "subject": "Writing",
        "grade_level_min": 4,
        "grade_level_max": 6,
        "description": "Paragraph structure, short stories, and journal writing.",
        "duration_weeks": 12,
        "hours_per_week": 1.0,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "ART-5A",
        "title": "Drawing & Sketching",
        "subject": "Art",
        "grade_level_min": 3,
        "grade_level_max": 7,
        "description": "Pencil sketching, color theory, and craft projects.",
        "duration_weeks": 12,
        "hours_per_week": 0.75,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["elective"],
        "is_active": True,
    },
    {
        "code": "MUS-5A",
        "title": "Ukulele Basics",
        "subject": "Music",
        "grade_level_min": 3,
        "grade_level_max": 7,
        "description": "Basic chords, strumming patterns, and simple songs.",
        "duration_weeks": 12,
        "hours_per_week": 0.75,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["elective"],
        "is_active": True,
    },
]

SOPHIA_COURSES = [
    {
        "code": "MATH-3A",
        "title": "Multiplication & Division",
        "subject": "Math",
        "grade_level_min": 2,
        "grade_level_max": 4,
        "description": "Times tables, division facts, place value, and word problems.",
        "duration_weeks": 12,
        "hours_per_week": 3.5,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "SCI-3A",
        "title": "Animals & Habitats",
        "subject": "Science",
        "grade_level_min": 2,
        "grade_level_max": 4,
        "description": "Animal classification, habitats, life cycles, and simple ecology.",
        "duration_weeks": 12,
        "hours_per_week": 2.5,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "ELA-3A",
        "title": "Storytime Reading",
        "subject": "Reading",
        "grade_level_min": 2,
        "grade_level_max": 4,
        "description": "Guided reading, phonics practice, and early chapter books.",
        "duration_weeks": 12,
        "hours_per_week": 3.0,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "SS-3A",
        "title": "Community & Maps",
        "subject": "Social Studies",
        "grade_level_min": 2,
        "grade_level_max": 4,
        "description": "Neighborhoods, maps, community helpers, and basic US geography.",
        "duration_weeks": 12,
        "hours_per_week": 1.5,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "WRIT-3A",
        "title": "Handwriting & Sentences",
        "subject": "Writing",
        "grade_level_min": 2,
        "grade_level_max": 4,
        "description": "Cursive practice, sentence building, and short paragraph writing.",
        "duration_weeks": 12,
        "hours_per_week": 1.0,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "ART-3A",
        "title": "Arts & Crafts",
        "subject": "Art",
        "grade_level_min": 1,
        "grade_level_max": 5,
        "description": "Painting, collage, clay modeling, and seasonal craft projects.",
        "duration_weeks": 12,
        "hours_per_week": 1.0,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["elective"],
        "is_active": True,
    },
]


# ============================================================
# 3. SCHEDULE BLUEPRINTS (weekly time slot templates)
#    Slightly different from Emma's to look realistic.
# ============================================================

LIAM_SCHEDULE = [
    # ── Math: Mon–Fri 09:00–09:50 ──
    {
        "course_code": "MATH-5A",
        "slots": [
            {"day_of_week": 0, "start_time": "09:00", "end_time": "09:50"},
            {"day_of_week": 1, "start_time": "09:00", "end_time": "09:50"},
            {"day_of_week": 2, "start_time": "09:00", "end_time": "09:50"},
            {"day_of_week": 3, "start_time": "09:00", "end_time": "09:50"},
            {"day_of_week": 4, "start_time": "09:00", "end_time": "09:50"},
        ],
    },
    # ── Science: Mon, Wed, Fri 10:00–11:00 + Tue, Thu 10:00–10:45 ──
    {
        "course_code": "SCI-5A",
        "slots": [
            {"day_of_week": 0, "start_time": "10:00", "end_time": "11:00"},
            {"day_of_week": 1, "start_time": "10:00", "end_time": "10:45"},
            {"day_of_week": 2, "start_time": "10:00", "end_time": "11:00"},
            {"day_of_week": 3, "start_time": "10:00", "end_time": "10:45"},
            {"day_of_week": 4, "start_time": "10:00", "end_time": "11:00"},
        ],
    },
    # ── Reading: Mon, Tue, Wed 13:00–14:00 ──
    {
        "course_code": "ELA-5A",
        "slots": [
            {"day_of_week": 0, "start_time": "13:00", "end_time": "14:00"},
            {"day_of_week": 1, "start_time": "13:00", "end_time": "14:00"},
            {"day_of_week": 2, "start_time": "13:00", "end_time": "14:00"},
        ],
    },
    # ── History: Thu 13:00–14:00 + Fri 13:00–14:00 ──
    {
        "course_code": "HIST-5A",
        "slots": [
            {"day_of_week": 3, "start_time": "13:00", "end_time": "14:00"},
            {"day_of_week": 4, "start_time": "13:00", "end_time": "14:00"},
        ],
    },
    # ── Writing: Wed 14:15–15:00 ──
    {
        "course_code": "WRIT-5A",
        "slots": [
            {"day_of_week": 2, "start_time": "14:15", "end_time": "15:00"},
        ],
    },
    # ── Art: Mon 14:15–15:00 ──
    {
        "course_code": "ART-5A",
        "slots": [
            {"day_of_week": 0, "start_time": "14:15", "end_time": "15:00"},
        ],
    },
    # ── Music: Fri 14:15–15:00 ──
    {
        "course_code": "MUS-5A",
        "slots": [
            {"day_of_week": 4, "start_time": "14:15", "end_time": "15:00"},
        ],
    },
]

SOPHIA_SCHEDULE = [
    # ── Math: Mon–Fri 09:00–09:45 ──
    {
        "course_code": "MATH-3A",
        "slots": [
            {"day_of_week": 0, "start_time": "09:00", "end_time": "09:45"},
            {"day_of_week": 1, "start_time": "09:00", "end_time": "09:45"},
            {"day_of_week": 2, "start_time": "09:00", "end_time": "09:45"},
            {"day_of_week": 3, "start_time": "09:00", "end_time": "09:45"},
            {"day_of_week": 4, "start_time": "09:00", "end_time": "09:45"},
        ],
    },
    # ── Science: Mon, Wed, Fri 10:00–10:45 ──
    {
        "course_code": "SCI-3A",
        "slots": [
            {"day_of_week": 0, "start_time": "10:00", "end_time": "10:45"},
            {"day_of_week": 2, "start_time": "10:00", "end_time": "10:45"},
            {"day_of_week": 4, "start_time": "10:00", "end_time": "10:45"},
        ],
    },
    # ── Reading: Mon–Fri 11:00–11:45 ──
    {
        "course_code": "ELA-3A",
        "slots": [
            {"day_of_week": 0, "start_time": "11:00", "end_time": "11:45"},
            {"day_of_week": 1, "start_time": "11:00", "end_time": "11:45"},
            {"day_of_week": 2, "start_time": "11:00", "end_time": "11:45"},
            {"day_of_week": 3, "start_time": "11:00", "end_time": "11:45"},
        ],
    },
    # ── Social Studies: Tue 10:00–10:45 + Thu 10:00–10:45 ──
    {
        "course_code": "SS-3A",
        "slots": [
            {"day_of_week": 1, "start_time": "10:00", "end_time": "10:45"},
            {"day_of_week": 3, "start_time": "10:00", "end_time": "10:45"},
        ],
    },
    # ── Writing: Fri 11:00–11:45 ──
    {
        "course_code": "WRIT-3A",
        "slots": [
            {"day_of_week": 4, "start_time": "11:00", "end_time": "11:45"},
        ],
    },
    # ── Art: Wed 13:00–14:00 + Fri 13:00–14:00 ──
    {
        "course_code": "ART-3A",
        "slots": [
            {"day_of_week": 2, "start_time": "13:00", "end_time": "14:00"},
            {"day_of_week": 4, "start_time": "13:00", "end_time": "14:00"},
        ],
    },
]


# ============================================================
# HELPERS
# ============================================================

def insert_student(student_data: dict) -> str:
    name = f"{student_data['first_name']} {student_data['last_name']}"
    print(f"👩‍🎓 Inserting student {name} …")
    result = sb.table("students").insert(student_data).execute()
    sid = result.data[0]["id"]
    print(f"   ✓ {name}  id={sid}")
    return sid


def insert_courses(course_list: list[dict]) -> dict[str, str]:
    """Insert courses, return {code: uuid}. Skip if code already exists."""
    print("📚 Inserting courses …")

    # Fetch existing courses to avoid duplicates
    existing = sb.table("courses").select("id, code").execute().data
    existing_map = {c["code"]: c["id"] for c in existing}

    course_map: dict[str, str] = {}
    for course in course_list:
        code = course["code"]
        if code in existing_map:
            course_map[code] = existing_map[code]
            print(f"   ↩ {code} already exists")
        else:
            result = sb.table("courses").insert(course).execute()
            cid = result.data[0]["id"]
            course_map[code] = cid
            print(f"   ✓ {code}  {course['title']}")

    return course_map


def insert_schedules(
    student_id: str,
    course_map: dict[str, str],
    blueprint: list[dict],
) -> dict[str, str]:
    """Create schedule (enrollment) + slots. Return {course_code: schedule_id}."""
    print("📅 Inserting schedules & slots …")

    today = date.today()
    semester_start = today - timedelta(days=today.weekday())
    semester_start -= timedelta(weeks=10)
    semester_end = semester_start + timedelta(weeks=12)

    schedule_map: dict[str, str] = {}

    for bp in blueprint:
        code = bp["course_code"]
        cid = course_map.get(code)
        if not cid:
            print(f"   ⚠ Course {code} not found — skipping")
            continue

        sch = sb.table("schedules").insert({
            "student_id": student_id,
            "course_id": cid,
            "status": "active",
            "start_date": str(semester_start),
            "end_date": str(semester_end),
        }).execute()

        schedule_id = sch.data[0]["id"]
        schedule_map[code] = schedule_id

        for slot in bp["slots"]:
            sb.table("schedule_slots").insert({
                "schedule_id": schedule_id,
                "day_of_week": slot["day_of_week"],
                "start_time": slot["start_time"],
                "end_time": slot["end_time"],
                "location": "Home",
            }).execute()

        print(f"   ✓ {code}  ({len(bp['slots'])} slots)")

    return schedule_map


def insert_availability(student_id: str):
    print("🕐 Inserting availability …")
    for dow in range(5):
        sb.table("availability").insert({
            "student_id": student_id,
            "day_of_week": dow,
            "start_time": "08:30",
            "end_time": "16:00",
            "preference": "available",
        }).execute()
    print("   ✓ Mon–Fri 8:30 AM – 4:00 PM")


def generate_sessions(schedule_map: dict[str, str], completion_rate: float = 0.85):
    """Create session instances from slots, with realistic past completions."""
    print("📋 Generating session instances …")

    all_schedule_ids = list(schedule_map.values())
    all_slots = []
    for sid in all_schedule_ids:
        slots = sb.table("schedule_slots").select("*").eq("schedule_id", sid).execute().data
        all_slots.extend(slots)

    schedules = sb.table("schedules").select("*").in_("id", all_schedule_ids).execute().data
    sch_dates = {
        s["id"]: (date.fromisoformat(s["start_date"]), date.fromisoformat(s["end_date"]))
        for s in schedules
    }

    today = date.today()
    total_created = 0
    completed = 0
    missed = 0

    for slot in all_slots:
        sched_id = slot["schedule_id"]
        start_d, end_d = sch_dates[sched_id]
        dow = slot["day_of_week"]

        d = start_d
        while d.weekday() != dow:
            d += timedelta(days=1)

        while d <= end_d:
            status = "pending"
            checked_in_at = None

            if d < today:
                if random.random() < completion_rate:
                    status = "completed"
                    checked_in_at = f"{d}T{slot['start_time']}Z"
                    completed += 1
                else:
                    status = "missed"
                    missed += 1

            sb.table("session_instances").insert({
                "schedule_id": sched_id,
                "schedule_slot_id": slot["id"],
                "session_date": str(d),
                "start_time": slot["start_time"],
                "end_time": slot["end_time"],
                "status": status,
                "checked_in_at": checked_in_at,
            }).execute()
            total_created += 1
            d += timedelta(weeks=1)

    print(f"   ✓ {total_created} sessions  ({completed} completed | {missed} missed | {total_created - completed - missed} pending)")


# ============================================================
# MAIN
# ============================================================
def setup_student(student_data: dict, courses: list[dict], schedule_blueprint: list[dict], completion_rate: float):
    name = f"{student_data['first_name']} {student_data['last_name']}"
    print(f"\n{'=' * 50}")
    print(f"  Setting up {name} (Grade {student_data['grade_level']})")
    print(f"{'=' * 50}\n")

    sid = insert_student(student_data)
    course_map = insert_courses(courses)
    schedule_map = insert_schedules(sid, course_map, schedule_blueprint)
    insert_availability(sid)
    generate_sessions(schedule_map, completion_rate)

    print(f"\n  ✅ {name} is ready!\n")
    return sid


def main():
    print("=" * 60)
    print("  Evlin — Add Liam & Sophia Johnson")
    print("=" * 60)

    # Check that Emma already exists
    existing = sb.table("students").select("first_name").execute().data
    names = [s["first_name"] for s in existing]
    print(f"\n  Existing students: {names}")

    if "Liam" in names:
        print("  ⚠ Liam already exists — skipping")
    else:
        setup_student(STUDENTS[0], LIAM_COURSES, LIAM_SCHEDULE, completion_rate=0.80)

    if "Sophia" in names:
        print("  ⚠ Sophia already exists — skipping")
    else:
        setup_student(STUDENTS[1], SOPHIA_COURSES, SOPHIA_SCHEDULE, completion_rate=0.90)

    # Final summary
    print("\n" + "=" * 60)
    print("  ✅ All done!")
    print("=" * 60)
    students = sb.table("students").select("first_name, last_name, grade_level").execute().data
    for s in students:
        print(f"  • {s['first_name']} {s['last_name']} — Grade {s['grade_level']}")
    print()


if __name__ == "__main__":
    main()
