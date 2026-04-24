#!/usr/bin/env python3
"""Update all Supabase courses with real standard_codes from Neo4j.

Maps each course to grade-appropriate Common Core (Math/ELA) and NGSS (Science) standards.
Also updates course titles/descriptions to better align with real curriculum.

Usage:
    cd adaptive-engine
    poetry run python scripts/update_standard_codes.py
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

# ── Load env ───────────────────────────────────────────────
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


# ============================================================
# Standard code mappings from Neo4j (Multi-State / Common Core / NGSS)
#
# All codes verified to exist in Neo4j as:
#   jurisdiction="Multi-State", normalizedStatementType="Standard"
# ============================================================

COURSE_UPDATES: dict[str, dict] = {
    # ──────────────────────────────────────────────────────
    # EMMA — Grade 8
    # ──────────────────────────────────────────────────────
    "MATH-8A": {
        "title": "Algebra Fundamentals",
        "description": "Expressions & equations, linear functions, systems of equations, and the Pythagorean Theorem.",
        "standard_codes": [
            "8.EE.A.1",   # Properties of integer exponents
            "8.EE.B.5",   # Graph proportional relationships
            "8.EE.C.7",   # Solve linear equations in one variable
            "8.EE.C.8",   # Analyze and solve pairs of simultaneous linear equations
            "8.F.A.1",    # Understand functions
            "8.F.A.3",    # Interpret y = mx + b as linear function
            "8.F.B.4",    # Construct function to model linear relationship
            "8.G.B.6",    # Pythagorean Theorem proof
            "8.G.B.7",    # Apply Pythagorean Theorem
            "8.MP4",      # Model with mathematics
        ],
    },
    "SCI-8A": {
        "title": "Introduction to Biology",
        "description": "Cell structure, body systems, genetics, ecosystems, and photosynthesis — aligned to NGSS Middle School Life Science.",
        "standard_codes": [
            "MS-LS1-1",   # Living things are made of cells
            "MS-LS1-2",   # Cell as a whole and as a system of parts
            "MS-LS1-3",   # Body is a system of interacting subsystems
            "MS-LS1-6",   # Role of photosynthesis
            "MS-LS2-1",   # Effects of resource availability on organisms
            "MS-LS2-3",   # Cycling of matter and flow of energy
            "MS-LS3-1",   # Structural changes to genes (mutations)
            "MS-LS3-2",   # Asexual vs sexual reproduction
            "MS-PS1-3",   # Synthetic materials from natural resources
        ],
    },
    "ELA-8A": {
        "title": "American Literature",
        "description": "Literature analysis, informational texts, and academic writing — Common Core ELA Grade 8.",
        "standard_codes": [
            "RL.8.1",     # Cite textual evidence
            "RL.8.2",     # Determine theme
            "RL.8.3",     # Analyze how dialogue propels action
            "RI.8.1",     # Cite textual evidence (informational)
            "RI.8.2",     # Determine central idea
            "L.8.1",      # Conventions of standard English grammar
            "L.8.4",      # Determine meaning of unknown words
            "L.8.5",      # Figurative language
        ],
    },
    "HIST-8A": {
        "title": "American History",
        "description": "American Revolution, historical research, and argumentative writing using primary sources.",
        "standard_codes": [
            "RH.6-8.1",   # Cite specific textual evidence (history)
            "RH.6-8.2",   # Determine central ideas
            "WHST.6-8.1", # Write arguments
            "WHST.6-8.2", # Write informative texts
            "WHST.6-8.9", # Draw evidence from informational texts
        ],
    },
    "WRIT-8A": {
        "title": "Essay Writing",
        "description": "Essay structure, argumentative and informative writing, revision and editing.",
        "standard_codes": [
            "W.8.1",      # Write arguments
            "W.8.2",      # Write informative/explanatory texts
            "W.8.4",      # Produce clear and coherent writing
            "W.8.5",      # Develop and strengthen writing through planning/revision
            "L.8.2",      # Conventions of capitalization, punctuation, spelling
            "L.8.3",      # Use knowledge of language conventions
        ],
    },

    # ──────────────────────────────────────────────────────
    # LIAM — Grade 5
    # ──────────────────────────────────────────────────────
    "MATH-5A": {
        "title": "Pre-Algebra Basics",
        "description": "Fractions, decimals, volume, coordinate geometry, and early algebraic thinking — Common Core Grade 5.",
        "standard_codes": [
            "5.NF.A.1",   # Add and subtract fractions
            "5.NF.A.2",   # Word problems with fractions
            "5.NF.B.3",   # Interpret fractions as division
            "5.NF.B.4",   # Multiply fractions
            "5.NBT.A.1",  # Place value system
            "5.NBT.B.5",  # Fluently multiply multi-digit whole numbers
            "5.OA.A.1",   # Use parentheses, brackets, braces in expressions
            "5.OA.A.2",   # Write simple expressions
            "5.MD.C.3",   # Recognize volume
            "5.G.A.1",    # Coordinate system
        ],
    },
    "SCI-5A": {
        "title": "Earth & Space Science",
        "description": "Sun and stars, Earth's systems, properties of matter, and gravity — aligned to NGSS Grade 5.",
        "standard_codes": [
            "5-ESS1-1",   # Apparent brightness of sun and stars
            "5-ESS1-2",   # Patterns of daily changes in daylight
            "5-ESS2-1",   # Geosphere, biosphere, hydrosphere, atmosphere
            "5-ESS2-2",   # Salt water and fresh water
            "5-PS1-1",    # Matter is made of particles
            "5-PS1-2",    # Conservation of matter
            "5-PS1-3",    # Identify materials based on properties
            "5-PS2-1",    # Gravitational force
        ],
    },
    "ELA-5A": {
        "title": "Reading Adventures",
        "description": "Chapter book discussions, vocabulary building, and reading comprehension — Common Core ELA Grade 5.",
        "standard_codes": [
            "RL.5.1",     # Quote accurately from text
            "RL.5.2",     # Determine theme from details
            "RL.5.3",     # Compare and contrast characters
            "RI.5.1",     # Quote accurately (informational)
            "RI.5.2",     # Determine main ideas
            "L.5.1",      # Conventions of standard English grammar
            "L.5.4",      # Determine meaning of unknown words
            "L.5.5",      # Figurative language
        ],
    },
    "HIST-5A": {
        "title": "World Cultures",
        "description": "World geography, ancient civilizations, and cultural traditions with informational reading.",
        "standard_codes": [
            "RI.5.3",     # Explain relationships in informational text
            "RI.5.7",     # Draw on information from multiple sources
            "RI.5.9",     # Integrate information from several texts
            "W.5.2",      # Write informative/explanatory texts
            "W.5.7",      # Conduct short research projects
        ],
    },
    "WRIT-5A": {
        "title": "Creative Writing",
        "description": "Paragraph structure, short stories, journal writing, and editing — Common Core Writing Grade 5.",
        "standard_codes": [
            "W.5.1",      # Write opinion pieces
            "W.5.3",      # Write narratives
            "W.5.4",      # Produce clear and coherent writing
            "W.5.5",      # Develop and strengthen writing through planning/revision
            "L.5.2",      # Conventions of capitalization, punctuation, spelling
        ],
    },
    "ART-5A": {
        "standard_codes": [],   # No Common Core standards for Art
    },
    "MUS-5A": {
        "standard_codes": [],   # No Common Core standards for Music
    },

    # ──────────────────────────────────────────────────────
    # SOPHIA — Grade 3
    # ──────────────────────────────────────────────────────
    "MATH-3A": {
        "title": "Multiplication & Division",
        "description": "Times tables, division facts, place value, fractions, and area — Common Core Grade 3.",
        "standard_codes": [
            "3.OA.A.1",   # Interpret products of whole numbers
            "3.OA.A.2",   # Interpret whole-number quotients
            "3.OA.A.3",   # Multiply and divide within 100 (word problems)
            "3.OA.A.4",   # Determine unknown whole number in multiplication
            "3.OA.C.7",   # Fluently multiply and divide within 100
            "3.NBT.A.2",  # Fluently add and subtract within 1000
            "3.NF.A.1",   # Understand fractions as parts of a whole
            "3.MD.C.7",   # Relate area to multiplication and addition
            "3.MD.D.8",   # Solve problems involving perimeters
            "3.MP1",      # Make sense of problems
        ],
    },
    "SCI-3A": {
        "title": "Animals & Habitats",
        "description": "Life cycles, animal groups, traits, fossils, and forces — aligned to NGSS Grade 3.",
        "standard_codes": [
            "3-LS1-1",    # Organisms have unique and diverse life cycles
            "3-LS2-1",    # Animals form groups that help members survive
            "3-LS3-1",    # Plants and animals have traits inherited from parents
            "3-LS3-2",    # Traits can be influenced by the environment
            "3-LS4-1",    # Fossils provide evidence of organisms that lived long ago
            "3-LS4-2",    # Variations in characteristics and survival advantages
            "3-LS4-3",    # Some organisms in a habitat survive better
            "3-LS4-4",    # Changes in habitat affect organisms
            "3-PS2-1",    # Effects of balanced and unbalanced forces
        ],
    },
    "ELA-3A": {
        "title": "Storytime Reading",
        "description": "Guided reading, phonics practice, and early chapter books — Common Core ELA Grade 3.",
        "standard_codes": [
            "RL.3.1",     # Ask and answer questions referring to text
            "RL.3.2",     # Recount stories and determine message
            "RL.3.3",     # Describe characters and their actions
            "RI.3.1",     # Ask and answer questions (informational)
            "RI.3.2",     # Determine main idea
            "L.3.1",      # Conventions of standard English grammar
            "L.3.4",      # Determine meaning of unknown words
        ],
    },
    "SS-3A": {
        "title": "Community & Maps",
        "description": "Neighborhoods, maps, community helpers, and basic US geography.",
        "standard_codes": [
            "RI.3.3",     # Describe relationship in informational text
            "RI.3.7",     # Use information from illustrations and text
            "W.3.2",      # Write informative/explanatory texts
        ],
    },
    "WRIT-3A": {
        "title": "Handwriting & Sentences",
        "description": "Cursive practice, sentence building, and short paragraph writing — Common Core Writing Grade 3.",
        "standard_codes": [
            "W.3.1",      # Write opinion pieces
            "W.3.3",      # Write narratives
            "W.3.5",      # Develop and strengthen writing through planning/revision
            "L.3.1",      # Conventions of standard English grammar
            "L.3.2",      # Conventions of capitalization, punctuation, spelling
        ],
    },
    "ART-3A": {
        "standard_codes": [],   # No Common Core standards for Art
    },

    # ──────────────────────────────────────────────────────
    # Shared electives (keep existing titles, just ensure codes are set)
    # ──────────────────────────────────────────────────────
    "ART-8A": {"standard_codes": []},
    "MUS-8A": {"standard_codes": []},
    "SPAN-8A": {"standard_codes": []},
}


def main():
    print("=" * 60)
    print("  Update Supabase Courses with Real Standard Codes")
    print("=" * 60)
    print()

    # Fetch all courses
    courses = sb.table("courses").select("id, code, title, standard_codes").execute().data
    code_to_id = {c["code"]: c["id"] for c in courses}

    updated = 0
    for code, updates in COURSE_UPDATES.items():
        cid = code_to_id.get(code)
        if not cid:
            print(f"  ⚠ Course {code} not found in DB — skipping")
            continue

        # Build the update payload (only include keys that are present)
        payload = {}
        if "standard_codes" in updates:
            payload["standard_codes"] = updates["standard_codes"]
        if "title" in updates:
            payload["title"] = updates["title"]
        if "description" in updates:
            payload["description"] = updates["description"]

        if not payload:
            continue

        sb.table("courses").update(payload).eq("id", cid).execute()

        n_codes = len(updates.get("standard_codes", []))
        title = updates.get("title", "(unchanged)")
        print(f"  ✓ {code:12s}  {n_codes:2d} standards  → {title}")
        updated += 1

    print(f"\n  ✅ Updated {updated} courses")
    print()


if __name__ == "__main__":
    main()
