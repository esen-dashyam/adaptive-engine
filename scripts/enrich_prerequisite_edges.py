"""
Prerequisite Edge Enrichment — builds a dense BUILDS_TOWARDS graph.

The Learning Commons data only has 418 BUILDS_TOWARDS edges for 17,312 nodes
(98.7% isolated). This script derives prerequisites from 3 sources:

  Source 1 — Grade-strand progression (algorithmic)
      CCSS/state codes follow Grade.Domain.Cluster.Standard patterns.
      2.OA.A.1 → 3.OA.A.1 is a prerequisite by definition (same strand, +1 grade).
      Generates ~8,000–12,000 edges.

  Source 2 — Same-strand lower-grade sweep (algorithmic)
      Any standard in the same domain at a lower grade is a soft prerequisite.
      Generates DEFINES_UNDERSTANDING edges at w=0.5.

  Source 3 — LLM-inferred (Gemini, optional)
      For the CCSS backbone (364 standards), ask Gemini which standards
      are strict prerequisites for each, then write those as BUILDS_TOWARDS w=0.9.

Run:
    poetry run python scripts/enrich_prerequisite_edges.py
    poetry run python scripts/enrich_prerequisite_edges.py --llm   # enable Gemini inference
    poetry run python scripts/enrich_prerequisite_edges.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.core.settings import settings

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )


def _parse_grade_from_code(code: str) -> int | None:
    """
    Extract grade number from statement codes like:
      3.OA.A.1  → 3
      K.CC.A.1  → 0
      6.EE.B.5  → 6
      7.SP.C.9  → 7
    """
    if not code:
        return None
    m = re.match(r'^(\d+)\.', code)
    if m:
        return int(m.group(1))
    if code.upper().startswith("K."):
        return 0
    return None


def _code_strand(code: str) -> str | None:
    """
    Extract the domain.cluster strand (everything after the grade prefix).
      3.OA.A.1  → OA.A.1
      6.EE.B.5  → EE.B.5
    Returns None if code doesn't follow the pattern.
    """
    m = re.match(r'^(?:\d+|K)\.(.+)$', code, re.IGNORECASE)
    return m.group(1) if m else None


def _code_domain(code: str) -> str | None:
    """
    Extract just the domain (first segment after grade).
      3.OA.A.1  → OA
      6.EE.B.5  → EE
    """
    strand = _code_strand(code)
    if not strand:
        return None
    return strand.split(".")[0]


# ── Source 1: Grade-strand progression ────────────────────────────────────────

def derive_strand_edges(driver, dry_run: bool = False) -> int:
    """
    For each standard, find standards in the SAME strand at grade-1
    and write BUILDS_TOWARDS (w=0.9) if none exists.

    Example: all Grade 3 OA.A standards → Grade 4 OA.A standards
    """
    logger.info("Source 1: Deriving grade-strand prerequisite edges...")

    with driver.session(database=settings.neo4j_database) as s:
        # Load all math standards with parseable codes
        rows = s.run("""
            MATCH (n:StandardsFrameworkItem)
            WHERE n.academicSubject = 'Mathematics'
              AND n.normalizedStatementType = 'Standard'
              AND n.statementCode IS NOT NULL
            RETURN n.identifier  AS id,
                   n.statementCode AS code,
                   n.jurisdiction  AS jurisdiction,
                   n.difficulty    AS beta
        """)
        nodes = [dict(r) for r in rows]

    logger.info(f"  Loaded {len(nodes)} nodes")

    # Group by (jurisdiction, strand) → list of (grade, id, code)
    strand_groups: dict[tuple[str, str], list[tuple[int, str, str]]] = {}
    for n in nodes:
        code  = n["code"] or ""
        grade = _parse_grade_from_code(code)
        strand = _code_strand(code)
        if grade is None or not strand:
            continue
        key = (n["jurisdiction"] or "Multi-State", strand)
        strand_groups.setdefault(key, []).append((grade, n["id"], code))

    # For each strand, sort by grade and wire consecutive grades
    edges_to_write: list[tuple[str, str, float]] = []  # (src_id, tgt_id, weight)
    for (_jur, strand), entries in strand_groups.items():
        entries.sort(key=lambda x: x[0])
        for i in range(len(entries) - 1):
            g0, id0, _c0 = entries[i]
            g1, id1, _c1 = entries[i + 1]
            if g1 == g0 + 1:  # consecutive grades only
                edges_to_write.append((id0, id1, 0.9))

    logger.info(f"  Derived {len(edges_to_write)} grade-strand edges")

    if dry_run:
        logger.info("  DRY RUN — not writing")
        return len(edges_to_write)

    # Write in batches, skip if BUILDS_TOWARDS already exists
    written = 0
    batch_size = 500
    with driver.session(database=settings.neo4j_database) as s:
        for i in range(0, len(edges_to_write), batch_size):
            batch = [
                {"src": src, "tgt": tgt, "w": w}
                for src, tgt, w in edges_to_write[i:i + batch_size]
            ]
            result = s.run("""
                UNWIND $rows AS row
                MATCH (src:StandardsFrameworkItem {identifier: row.src})
                MATCH (tgt:StandardsFrameworkItem {identifier: row.tgt})
                MERGE (src)-[r:BUILDS_TOWARDS {source: 'strand_inference'}]->(tgt)
                ON CREATE SET r.conceptual_weight = row.w,
                              r.inferred = true,
                              r.created_at = datetime()
                RETURN count(r) AS cnt
            """, rows=batch)
            written += result.single()["cnt"]

    logger.info(f"  Wrote {written} BUILDS_TOWARDS edges (Source 1)")
    return written


# ── Source 2: Same-domain soft prerequisites ──────────────────────────────────

def derive_domain_edges(driver, dry_run: bool = False) -> int:
    """
    Write DEFINES_UNDERSTANDING (w=0.5) edges between same-domain standards
    across grades that don't already have a BUILDS_TOWARDS.

    Example: All Grade 2 NBT standards → All Grade 3 NBT standards
    (domain-level relationship, not strand-exact)
    """
    logger.info("Source 2: Deriving same-domain cross-grade edges...")

    with driver.session(database=settings.neo4j_database) as s:
        rows = s.run("""
            MATCH (n:StandardsFrameworkItem)
            WHERE n.academicSubject = 'Mathematics'
              AND n.normalizedStatementType = 'Standard'
              AND n.statementCode IS NOT NULL
              AND n.difficulty IS NOT NULL
            RETURN n.identifier  AS id,
                   n.statementCode AS code,
                   n.jurisdiction  AS jurisdiction,
                   n.difficulty    AS beta
        """)
        nodes = [dict(r) for r in rows]

    # Group by (jurisdiction, domain) → list of (grade_float, id)
    domain_groups: dict[tuple[str, str], list[tuple[float, str]]] = {}
    for n in nodes:
        code   = n["code"] or ""
        grade  = _parse_grade_from_code(code)
        domain = _code_domain(code)
        if grade is None or not domain:
            continue
        key = (n["jurisdiction"] or "", domain)
        domain_groups.setdefault(key, []).append((float(grade), n["id"]))

    edges: list[tuple[str, str]] = []
    for (_jur, _dom), entries in domain_groups.items():
        # Sort by grade; connect every lower-grade standard to every higher-grade one
        entries.sort(key=lambda x: x[0])
        # Only link consecutive grades to avoid combinatorial explosion
        by_grade: dict[float, list[str]] = {}
        for g, nid in entries:
            by_grade.setdefault(g, []).append(nid)

        grade_levels = sorted(by_grade.keys())
        for gi in range(len(grade_levels) - 1):
            g_lo = grade_levels[gi]
            g_hi = grade_levels[gi + 1]
            if g_hi != g_lo + 1:
                continue
            for src in by_grade[g_lo]:
                for tgt in by_grade[g_hi]:
                    edges.append((src, tgt))

    logger.info(f"  Derived {len(edges)} domain cross-grade edges")

    if dry_run:
        logger.info("  DRY RUN — not writing")
        return len(edges)

    written = 0
    batch_size = 500
    with driver.session(database=settings.neo4j_database) as s:
        for i in range(0, len(edges), batch_size):
            batch = [{"src": src, "tgt": tgt} for src, tgt in edges[i:i + batch_size]]
            result = s.run("""
                UNWIND $rows AS row
                MATCH (src:StandardsFrameworkItem {identifier: row.src})
                MATCH (tgt:StandardsFrameworkItem {identifier: row.tgt})
                MERGE (src)-[r:DEFINES_UNDERSTANDING {source: 'domain_inference'}]->(tgt)
                ON CREATE SET r.conceptual_weight = 0.5,
                              r.understanding_strength = 0.5,
                              r.inferred = true,
                              r.created_at = datetime()
                RETURN count(r) AS cnt
            """, rows=batch)
            written += result.single()["cnt"]

    logger.info(f"  Wrote {written} DEFINES_UNDERSTANDING edges (Source 2)")
    return written


# ── Source 3: LLM-inferred prerequisites (CCSS backbone) ─────────────────────

def derive_llm_edges(driver, dry_run: bool = False) -> int:
    """
    For each CCSS (Multi-State) standard, ask Gemini which other CCSS standards
    are strict prerequisites. Writes high-confidence BUILDS_TOWARDS w=0.9 edges.

    Only runs on the 364 CCSS standards — keeps LLM calls manageable.
    """
    logger.info("Source 3: LLM-inferred prerequisite edges (CCSS backbone)...")

    with driver.session(database=settings.neo4j_database) as s:
        rows = s.run("""
            MATCH (n:StandardsFrameworkItem)
            WHERE n.jurisdiction = 'Multi-State'
              AND n.academicSubject = 'Mathematics'
              AND n.normalizedStatementType = 'Standard'
            RETURN n.identifier  AS id,
                   n.statementCode AS code,
                   n.description   AS description,
                   n.difficulty    AS beta
            ORDER BY n.difficulty ASC
        """)
        ccss_nodes = [dict(r) for r in rows]

    # Build lookup: code → id
    code_to_id = {n["code"]: n["id"] for n in ccss_nodes if n["code"]}
    all_codes   = sorted(code_to_id.keys())

    logger.info(f"  {len(ccss_nodes)} CCSS standards to process")

    if dry_run:
        logger.info("  DRY RUN — not calling Gemini")
        return 0

    from backend.app.llm.gemini_service import GeminiService
    svc = GeminiService()
    if not svc._get_model():
        logger.warning("  Gemini not configured — skipping LLM inference")
        return 0

    written = 0
    with driver.session(database=settings.neo4j_database) as s:
        # Process in batches of 10 standards per prompt to reduce API calls
        for i in range(0, len(ccss_nodes), 10):
            batch = ccss_nodes[i:i + 10]
            standards_block = "\n".join(
                f"  {n['code']}: {n['description'][:120]}"
                for n in batch
            )
            all_codes_list = ", ".join(all_codes[:80])  # keep prompt size bounded

            prompt = (
                "You are a K-8 math curriculum expert.\n\n"
                "For each standard below, list its STRICT prerequisites — "
                "standards a student MUST master first. Only list prerequisites "
                "that exist in the provided code list.\n\n"
                f"Standards to analyze:\n{standards_block}\n\n"
                f"Available prerequisite codes (CCSS only): {all_codes_list}\n\n"
                "Return ONLY valid JSON — a list of objects:\n"
                '[{"standard":"3.OA.A.1","prerequisites":["2.OA.A.1","2.NBT.A.1"]}]\n'
                "If a standard has no prerequisites in the list, return an empty array.\n"
                "Be conservative — only list genuine strict prerequisites, not related standards."
            )

            text = svc.generate_content(prompt)
            if not text:
                continue

            try:
                parsed = svc.parse_json_response(text, array=True)
                if not isinstance(parsed, list):
                    continue

                for item in parsed:
                    tgt_code = item.get("standard", "")
                    prereqs  = item.get("prerequisites", [])
                    tgt_id   = code_to_id.get(tgt_code)
                    if not tgt_id:
                        continue
                    for prereq_code in prereqs:
                        src_id = code_to_id.get(prereq_code)
                        if not src_id or src_id == tgt_id:
                            continue
                        r = s.run("""
                            MATCH (src:StandardsFrameworkItem {identifier: $src})
                            MATCH (tgt:StandardsFrameworkItem {identifier: $tgt})
                            MERGE (src)-[r:BUILDS_TOWARDS {source: 'llm_inference'}]->(tgt)
                            ON CREATE SET r.conceptual_weight = 0.9,
                                          r.inferred = true,
                                          r.llm_confidence = 0.85,
                                          r.created_at = datetime()
                            RETURN count(r) AS cnt
                        """, src=src_id, tgt=tgt_id)
                        written += r.single()["cnt"]

            except Exception as exc:
                logger.warning(f"  LLM parse error for batch {i}: {exc}")

            time.sleep(0.5)  # rate limit

    logger.info(f"  Wrote {written} BUILDS_TOWARDS edges (Source 3 — LLM)")
    return written


# ── Stats ─────────────────────────────────────────────────────────────────────

def print_stats(driver):
    with driver.session(database=settings.neo4j_database) as s:
        total    = s.run("MATCH (n:StandardsFrameworkItem) WHERE n.academicSubject='Mathematics' AND n.normalizedStatementType='Standard' RETURN count(n) AS c").single()["c"]
        bt       = s.run("MATCH ()-[r:BUILDS_TOWARDS]->() RETURN count(r) AS c").single()["c"]
        du       = s.run("MATCH ()-[r:DEFINES_UNDERSTANDING]->() RETURN count(r) AS c").single()["c"]
        isolated = s.run("""
            MATCH (n:StandardsFrameworkItem)
            WHERE n.academicSubject='Mathematics'
              AND n.normalizedStatementType='Standard'
              AND NOT (n)-[:BUILDS_TOWARDS]-()
              AND NOT ()-[:BUILDS_TOWARDS]->(n)
              AND NOT (n)-[:DEFINES_UNDERSTANDING]-()
              AND NOT ()-[:DEFINES_UNDERSTANDING]->(n)
            RETURN count(n) AS c
        """).single()["c"]

    logger.info("=" * 55)
    logger.info(f"  Total math Standards        : {total:>7,}")
    logger.info(f"  BUILDS_TOWARDS edges        : {bt:>7,}  (w=0.9)")
    logger.info(f"  DEFINES_UNDERSTANDING edges : {du:>7,}  (w=0.5)")
    logger.info(f"  Still isolated              : {isolated:>7,}  ({isolated/total*100:.1f}%)")
    logger.info("=" * 55)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Enrich the knowledge graph with inferred prerequisite edges."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--llm", action="store_true",
                        help="Enable Gemini LLM inference for CCSS backbone (Source 3)")
    parser.add_argument("--source", type=int, choices=[1, 2, 3],
                        help="Run only one source (1=strand, 2=domain, 3=llm)")
    args = parser.parse_args()

    driver = _get_driver()

    logger.info("=" * 55)
    logger.info("Knowledge Graph — Prerequisite Edge Enrichment")
    logger.info(f"  dry_run={args.dry_run}  llm={args.llm}")
    logger.info("=" * 55)
    logger.info("Before:")
    print_stats(driver)

    total_written = 0

    if not args.source or args.source == 1:
        total_written += derive_strand_edges(driver, dry_run=args.dry_run)

    if not args.source or args.source == 2:
        total_written += derive_domain_edges(driver, dry_run=args.dry_run)

    if (not args.source or args.source == 3) and args.llm:
        total_written += derive_llm_edges(driver, dry_run=args.dry_run)

    if not args.dry_run:
        logger.info("\nAfter:")
        print_stats(driver)

    logger.success(f"Total edges written: {total_written:,}")
    driver.close()


if __name__ == "__main__":
    main()
