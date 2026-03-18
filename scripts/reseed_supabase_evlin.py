#!/usr/bin/env python3
"""Re-seed Supabase with data that matches the Evlin frontend mock data.

Replaces Calendar Scheduler dummy data (Ava Patel, 30 courses) with
the student / courses / schedule shown in Evlin's React components.

Usage:
    cd adaptive-engine
    poetry run python scripts/reseed_supabase_evlin.py
"""
from __future__ import annotations

import os
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

# ── Load env ───────────────────────────────────────────────
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


# ============================================================
# 1. CLEAR ALL EXISTING DATA  (FK-safe order)
# ============================================================
def clear_all():
    print("🗑  Clearing existing data …")
    for table in [
        "checkin_log",
        "session_instances",
        "schedule_slots",
        "schedules",
        "availability",
        "agent_conversations",
        "generated_pdfs",
        "ocr_documents",
        "students",
        "courses",
    ]:
        try:
            # delete everything — neq id to an impossible value is a "match all" trick
            sb.table(table).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
            print(f"   ✓ {table}")
        except Exception as e:
            print(f"   ⚠ {table}: {e}")
    print()


# ============================================================
# 2. INSERT STUDENT
# ============================================================
STUDENT = {
    "first_name": "Emma",
    "last_name": "Johnson",
    "grade_level": 8,
    "date_of_birth": "2012-06-15",
    "parent_name": "Sarah Johnson",
    "parent_email": "sarah.johnson@example.com",
    "notes": "8th grade homeschool student. Balanced curriculum with core academics and electives.",
}


def insert_student() -> str:
    print("👩‍🎓 Inserting student …")
    result = sb.table("students").insert(STUDENT).execute()
    sid = result.data[0]["id"]
    print(f"   ✓ Emma Johnson  id={sid}\n")
    return sid


# ============================================================
# 3. INSERT COURSES  (matching Evlin Schedule / Courses pages)
# ============================================================
COURSES = [
    {
        "code": "MATH-8A",
        "title": "Algebra Fundamentals",
        "subject": "Math",
        "grade_level_min": 7,
        "grade_level_max": 9,
        "description": "Algebra including linear equations, inequalities, and introduction to geometry.",
        "duration_weeks": 12,
        "hours_per_week": 5.0,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "SCI-8A",
        "title": "Introduction to Biology",
        "subject": "Science",
        "grade_level_min": 7,
        "grade_level_max": 9,
        "description": "Biology fundamentals including cell structure, physics laws, chemistry, and earth science.",
        "duration_weeks": 12,
        "hours_per_week": 4.5,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "ELA-8A",
        "title": "American Literature",
        "subject": "Reading",
        "grade_level_min": 7,
        "grade_level_max": 9,
        "description": "Literature analysis, poetry workshop, and comprehension practice.",
        "duration_weeks": 12,
        "hours_per_week": 3.25,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "HIST-8A",
        "title": "American History",
        "subject": "History",
        "grade_level_min": 7,
        "grade_level_max": 9,
        "description": "American Revolution and historical research projects.",
        "duration_weeks": 12,
        "hours_per_week": 2.0,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "WRIT-8A",
        "title": "Essay Writing",
        "subject": "Writing",
        "grade_level_min": 7,
        "grade_level_max": 9,
        "description": "Essay structure, academic writing, and composition skills.",
        "duration_weeks": 12,
        "hours_per_week": 1.0,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["core"],
        "is_active": True,
    },
    {
        "code": "ART-8A",
        "title": "Watercolor Techniques",
        "subject": "Art",
        "grade_level_min": 6,
        "grade_level_max": 10,
        "description": "Watercolor painting techniques and creative expression.",
        "duration_weeks": 12,
        "hours_per_week": 0.75,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["elective"],
        "is_active": True,
    },
    {
        "code": "MUS-8A",
        "title": "Piano Practice",
        "subject": "Music",
        "grade_level_min": 6,
        "grade_level_max": 10,
        "description": "Piano practice and music theory foundations.",
        "duration_weeks": 12,
        "hours_per_week": 0.75,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["elective"],
        "is_active": True,
    },
    {
        "code": "SPAN-8A",
        "title": "Spanish for Beginners",
        "subject": "Spanish",
        "grade_level_min": 6,
        "grade_level_max": 10,
        "description": "Spanish vocabulary, verbs, and past tense conjugation.",
        "duration_weeks": 12,
        "hours_per_week": 0.5,
        "difficulty": "standard",
        "prerequisites": [],
        "tags": ["elective"],
        "is_active": True,
    },
]


def insert_courses() -> dict[str, str]:
    """Return {course_code: uuid}."""
    print("📚 Inserting courses …")
    result = sb.table("courses").insert(COURSES).execute()
    course_map = {c["code"]: c["id"] for c in result.data}
    for c in result.data:
        print(f"   ✓ {c['code']}  {c['title']}")
    print()
    return course_map


# ============================================================
# 4. INSERT SCHEDULES + SLOTS
#    Matches Evlin Schedule.tsx exactly (Mon-Fri)
# ============================================================
# day_of_week: 0=Mon … 4=Fri  (matching JS getDay() conversion in Home.tsx)

SCHEDULE_BLUEPRINT = [
    # ── Math: every day 09:00-10:00 ──
    {
        "course_code": "MATH-8A",
        "slots": [
            {"day_of_week": 0, "start_time": "09:00", "end_time": "10:00"},
            {"day_of_week": 1, "start_time": "09:00", "end_time": "10:00"},
            {"day_of_week": 2, "start_time": "09:00", "end_time": "10:00"},
            {"day_of_week": 3, "start_time": "09:00", "end_time": "10:00"},
            {"day_of_week": 4, "start_time": "09:00", "end_time": "10:00"},
        ],
    },
    # ── Science: every day 10:30, end varies ──
    {
        "course_code": "SCI-8A",
        "slots": [
            {"day_of_week": 0, "start_time": "10:30", "end_time": "11:15"},  # Mon 45 min
            {"day_of_week": 1, "start_time": "10:30", "end_time": "11:30"},  # Tue 60 min
            {"day_of_week": 2, "start_time": "10:30", "end_time": "11:30"},  # Wed 60 min
            {"day_of_week": 3, "start_time": "10:30", "end_time": "11:15"},  # Thu 45 min
            {"day_of_week": 4, "start_time": "10:30", "end_time": "11:30"},  # Fri 60 min
        ],
    },
    # ── Reading (American Literature): Mon, Tue, Wed afternoon ──
    {
        "course_code": "ELA-8A",
        "slots": [
            {"day_of_week": 0, "start_time": "13:00", "end_time": "14:00"},  # Mon 60 min
            {"day_of_week": 1, "start_time": "13:00", "end_time": "14:15"},  # Tue 75 min
            {"day_of_week": 2, "start_time": "13:00", "end_time": "14:00"},  # Wed 60 min
        ],
    },
    # ── History: Mon 3 PM (30 min) + Fri 1 PM project (90 min) ──
    {
        "course_code": "HIST-8A",
        "slots": [
            {"day_of_week": 0, "start_time": "15:00", "end_time": "15:30"},  # Mon 30 min
            {"day_of_week": 4, "start_time": "13:00", "end_time": "14:30"},  # Fri 90 min (Project)
        ],
    },
    # ── Writing: Thu 1 PM (60 min) ──
    {
        "course_code": "WRIT-8A",
        "slots": [
            {"day_of_week": 3, "start_time": "13:00", "end_time": "14:00"},
        ],
    },
    # ── Art: Tue 3 PM (45 min) ──
    {
        "course_code": "ART-8A",
        "slots": [
            {"day_of_week": 1, "start_time": "15:00", "end_time": "15:45"},
        ],
    },
    # ── Music: Wed 2:30 PM (45 min) ──
    {
        "course_code": "MUS-8A",
        "slots": [
            {"day_of_week": 2, "start_time": "14:30", "end_time": "15:15"},
        ],
    },
    # ── Spanish: Thu 3 PM (30 min) ──
    {
        "course_code": "SPAN-8A",
        "slots": [
            {"day_of_week": 3, "start_time": "15:00", "end_time": "15:30"},
        ],
    },
]


def insert_schedules(student_id: str, course_map: dict[str, str]) -> dict[str, str]:
    """Create schedule (enrollment) + slots. Return {course_code: schedule_id}."""
    print("📅 Inserting schedules & slots …")

    today = date.today()
    semester_start = today - timedelta(days=today.weekday())  # Start of current week
    semester_start -= timedelta(weeks=10)  # Pretend started 10 weeks ago
    semester_end = semester_start + timedelta(weeks=12)

    schedule_map: dict[str, str] = {}

    for blueprint in SCHEDULE_BLUEPRINT:
        code = blueprint["course_code"]
        cid = course_map.get(code)
        if not cid:
            print(f"   ⚠ Course {code} not found")
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

        for slot in blueprint["slots"]:
            sb.table("schedule_slots").insert({
                "schedule_id": schedule_id,
                "day_of_week": slot["day_of_week"],
                "start_time": slot["start_time"],
                "end_time": slot["end_time"],
                "location": "Home",
            }).execute()

        print(f"   ✓ {code}  ({len(blueprint['slots'])} slots)")

    print()
    return schedule_map


# ============================================================
# 5. INSERT AVAILABILITY  (Mon-Fri 8:30 AM – 4 PM)
# ============================================================
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
    print("   ✓ Mon–Fri 8:30 AM – 4:00 PM\n")


# ============================================================
# 6. GENERATE SESSION INSTANCES  (past + future weeks)
#    Creates concrete dated sessions from weekly slot templates.
#    Marks past sessions ~85% completed, ~15% missed for
#    realistic compliance stats.
# ============================================================
def generate_sessions(schedule_map: dict[str, str]):
    print("📋 Generating session instances …")

    # Fetch all slots for all schedules
    all_schedule_ids = list(schedule_map.values())
    all_slots = []
    for sid in all_schedule_ids:
        slots = sb.table("schedule_slots").select("*").eq("schedule_id", sid).execute().data
        all_slots.extend(slots)

    # Fetch schedule date ranges
    schedules = sb.table("schedules").select("*").in_("id", all_schedule_ids).execute().data
    sch_dates = {s["id"]: (date.fromisoformat(s["start_date"]), date.fromisoformat(s["end_date"])) for s in schedules}

    today = date.today()
    total_created = 0
    completed = 0
    missed = 0

    for slot in all_slots:
        sched_id = slot["schedule_id"]
        start_d, end_d = sch_dates[sched_id]
        dow = slot["day_of_week"]

        # Find the first occurrence of this day_of_week on or after start_d
        d = start_d
        while d.weekday() != dow:
            d += timedelta(days=1)

        while d <= end_d:
            status = "pending"
            checked_in_at = None

            if d < today:
                # Past sessions: 85% completed, 15% missed
                if random.random() < 0.85:
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

    print(f"   ✓ {total_created} session instances")
    print(f"     {completed} completed  |  {missed} missed  |  {total_created - completed - missed} pending")
    print()


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 60)
    print("  Evlin MVP — Reseed Supabase")
    print("=" * 60)
    print()

    clear_all()
    student_id = insert_student()
    course_map = insert_courses()
    schedule_map = insert_schedules(student_id, course_map)
    insert_availability(student_id)
    generate_sessions(schedule_map)

    print("=" * 60)
    print("  ✅ Done! Supabase now matches Evlin frontend.")
    print("=" * 60)

    # Quick summary
    slots_resp = sb.table("schedule_slots").select("id", count="exact").execute()
    sess_resp = sb.table("session_instances").select("id", count="exact").execute()
    print(f"\n  Student:    Emma Johnson (8th Grade)")
    print(f"  Courses:    {len(course_map)}")
    print(f"  Slots:      {slots_resp.count}")
    print(f"  Sessions:   {sess_resp.count}")
    print()


if __name__ == "__main__":
    main()
