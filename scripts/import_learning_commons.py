"""Import Learning Commons Knowledge Graph data into Neo4j.

Loads ``nodes.jsonl`` and ``relationships.jsonl`` from the Learning Commons KG
export (~242k nodes, ~400k relationships) into Neo4j in streaming batches.

Usage:
    poetry run python scripts/import_learning_commons.py
    poetry run python scripts/import_learning_commons.py --grades 1 2 3 4 5 6 7 8
    poetry run python scripts/import_learning_commons.py --subject Mathematics --batch-size 500
    poetry run python scripts/import_learning_commons.py --dry-run

Default data location (standalone repo):
    data/learning-commons-kg/exports/nodes.jsonl
    data/learning-commons-kg/exports/relationships.jsonl

If you still have the legacy MysterionRise subtree checked out, the script
will fall back to:
    MysterionRise/data/learning-commons-kg/exports/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.kg.neo4j_adapter import KnowledgeGraphAdapter

# Default path relative to repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_EXPORTS = _REPO_ROOT / "data" / "learning-commons-kg" / "exports"

# Backward-compatible fallback to the legacy nested MysterionRise repo, so the
# script keeps working even if the LC exports still live there.
_LEGACY_EXPORTS = _REPO_ROOT / "MysterionRise" / "data" / "learning-commons-kg" / "exports"
if not _DEFAULT_EXPORTS.exists() and _LEGACY_EXPORTS.exists():
    _DEFAULT_EXPORTS = _LEGACY_EXPORTS

K1_K8_GRADES = {"1", "2", "3", "4", "5", "6", "7", "8"}

# ── Pruning: only these node labels are kept ──────────────────────────────────
KEEP_LABELS = {"StandardsFrameworkItem", "LearningComponent"}
KEEP_STATEMENT_TYPES = {"Standard"}  # only for StandardsFrameworkItem; LearningComponents bypass this

# ── Relationship label normalisation + conceptual weight ──────────────────────
# camelCase (JSONL) → UPPER_SNAKE (Neo4j) + Rasch conceptual weight
#   0.9 = Strict Prerequisite   (A must come before B)
#   0.5 = Co-requisite/Related  (meaningful overlap, not strict)
#   0.2 = Contextual link       (weak / structural)
REL_MAP: dict[str, tuple[str, float]] = {
    "buildsTowards":          ("BUILDS_TOWARDS",           0.9),
    "hasDependency":          ("HAS_DEPENDENCY",            0.9),
    "supports":               ("SUPPORTS",                  0.5),
    "hasPart":                ("HAS_PART",                  0.5),
    "hasStandardAlignment":   ("HAS_STANDARD_ALIGNMENT",    0.5),
    "hasEducationalAlignment":("HAS_EDUCATIONAL_ALIGNMENT", 0.2),
    "hasChild":               ("HAS_CHILD",                 0.2),
    "relatesTo":              ("RELATES_TO",                0.2),
    "hasReference":           ("HAS_REFERENCE",             0.2),
    "mutuallyExclusiveWith":  ("MUTUALLY_EXCLUSIVE_WITH",   0.2),
}
# skip relationship types that carry no learning signal
SKIP_LABELS = set()


def _grade_list(raw: str | list) -> list[str]:
    """Parse gradeLevel which may be a JSON string like '["2","3"]' or already a list."""
    if isinstance(raw, list):
        return [str(g) for g in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(g) for g in parsed]
        except (json.JSONDecodeError, ValueError):
            pass
        return [raw.strip()]
    return []


def _compute_difficulty(grades: list[str]) -> float | None:
    """
    Derive Rasch item difficulty β from the standard's grade level.

    β = grade number (1.0 – 8.0).  Multi-grade standards use the highest grade.
    Returns None for non-K8 standards so they can be excluded.
    """
    numeric = [int(g) for g in grades if g.isdigit() and 1 <= int(g) <= 8]
    return float(max(numeric)) if numeric else None


def stream_nodes(
    nodes_path: Path,
    grade_filter: set[str] | None = None,
    subject_filter: str | None = None,
    prune_noise: bool = True,
) -> tuple[list[dict], set[str]]:
    """
    Stream nodes.jsonl and return:
      - list of property dicts ready for Neo4j MERGE
      - set of identifiers that passed the filter (for relationship filtering)

    With prune_noise=True (default) keeps ONLY StandardsFrameworkItem nodes
    whose normalizedStatementType is 'Standard' — discarding Activity, Lesson,
    LessonGrouping, Assessment, Course, and Standard Grouping nodes.
    """
    included: list[dict] = []
    included_ids: set[str] = set()
    total = pruned_label = pruned_type = 0

    with nodes_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                node = json.loads(line)
            except json.JSONDecodeError:
                continue

            # ── Pruning: label filter ─────────────────────────────────────────
            labels: list[str] = node.get("labels", [])
            if prune_noise and not any(lb in KEEP_LABELS for lb in labels):
                pruned_label += 1
                continue

            props: dict[str, Any] = node.get("properties", {})
            identifier = node.get("identifier") or props.get("identifier")
            if not identifier:
                continue

            is_lc = "LearningComponent" in labels

            # ── Pruning: statement type filter (Standards only, skip for LCs) ─
            stmt_type = props.get("normalizedStatementType", "")
            if prune_noise and not is_lc and stmt_type not in KEEP_STATEMENT_TYPES:
                pruned_type += 1
                continue

            # ── Grade filter (skip for LCs — they have no gradeLevel) ────────
            grades = _grade_list(props.get("gradeLevel", "[]"))
            if not is_lc and grade_filter and not any(g in grade_filter for g in grades):
                continue

            # ── Subject filter ───────────────────────────────────────────────
            if subject_filter:
                subj = props.get("academicSubject", "")
                if subj and subject_filter.lower() not in subj.lower():
                    continue

            # ── Flatten props for Neo4j ──────────────────────────────────────
            flat: dict[str, Any] = {"identifier": identifier}
            # tag node type so GraphRAG can distinguish
            flat["node_type"] = "LearningComponent" if is_lc else "Standard"
            for k, v in props.items():
                if k == "gradeLevel":
                    flat["gradeLevelList"] = grades
                    flat["gradeLevel"] = v
                elif isinstance(v, (str, int, float, bool)):
                    flat[k] = v
                elif isinstance(v, list):
                    flat[k] = v

            flat.setdefault("normalizedStatementType", stmt_type)
            flat.setdefault("statementCode", props.get("statementCode", ""))
            flat.setdefault("academicSubject", props.get("academicSubject", ""))
            flat.setdefault("jurisdiction", props.get("jurisdiction", ""))

            # ── Rasch difficulty β (Standards only) ──────────────────────────
            if not is_lc:
                beta = _compute_difficulty(grades)
                if beta is not None:
                    flat["difficulty"] = beta

            included.append(flat)
            included_ids.add(identifier)

    logger.info(
        f"Nodes: {len(included)}/{total} kept | "
        f"pruned_label={pruned_label} pruned_type={pruned_type}"
    )
    return included, included_ids


def stream_relationships(
    rels_path: Path,
    allowed_ids: set[str] | None = None,
) -> dict[str, list[dict]]:
    """
    Stream relationships.jsonl and group by relationship label.

    Returns:
        dict mapping label -> list of {src_id, tgt_id, rel_id, props}
    """
    grouped: dict[str, list[dict]] = {}
    total = skipped = 0

    with rels_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                rel = json.loads(line)
            except json.JSONDecodeError:
                continue

            src_id = rel.get("source_identifier")
            tgt_id = rel.get("target_identifier")
            rel_id = rel.get("identifier", f"{src_id}_{tgt_id}")
            label = rel.get("label", "RELATED_TO")

            if not src_id or not tgt_id:
                skipped += 1
                continue

            if allowed_ids and (src_id not in allowed_ids or tgt_id not in allowed_ids):
                skipped += 1
                continue

            if label in SKIP_LABELS:
                skipped += 1
                continue

            # Normalise label (camelCase → UPPER_SNAKE) and assign conceptual weight
            neo4j_label, weight = REL_MAP.get(label, (label.upper(), 0.2))

            rel_props: dict[str, Any] = {"conceptual_weight": weight}
            for k, v in rel.get("properties", {}).items():
                if isinstance(v, (str, int, float, bool)):
                    rel_props[k] = v

            grouped.setdefault(neo4j_label, []).append(
                {"src_id": src_id, "tgt_id": tgt_id, "rel_id": rel_id, "props": rel_props}
            )

    logger.info(
        f"Relationships: {total - skipped}/{total} passed filter, "
        f"{len(grouped)} distinct labels"
    )
    return grouped


def write_nodes_batched(
    adapter: KnowledgeGraphAdapter,
    nodes: list[dict],
    batch_size: int = 500,
    dry_run: bool = False,
) -> int:
    written = 0
    for i in range(0, len(nodes), batch_size):
        batch = nodes[i : i + batch_size]
        if not dry_run:
            adapter.upsert_standards_batch(batch)
        written += len(batch)
        if i % (batch_size * 10) == 0:
            logger.info(f"  nodes written: {written}/{len(nodes)}")
    return written


def write_relationships_batched(
    adapter: KnowledgeGraphAdapter,
    grouped: dict[str, list[dict]],
    batch_size: int = 500,
    dry_run: bool = False,
) -> int:
    written = 0
    for label, rels in grouped.items():
        for i in range(0, len(rels), batch_size):
            batch = rels[i : i + batch_size]
            if not dry_run:
                try:
                    adapter.upsert_typed_relationship_batch(label, batch)
                except Exception as exc:
                    logger.warning(f"  Skipping batch for label '{label}': {exc}")
            written += len(batch)
        logger.info(f"  [{label}] {len(rels)} relationships written")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import Learning Commons KG into Neo4j for adaptive learning."
    )
    parser.add_argument(
        "--exports-dir",
        type=Path,
        default=_DEFAULT_EXPORTS,
        help="Path to the directory containing nodes.jsonl and relationships.jsonl",
    )
    parser.add_argument(
        "--grades",
        nargs="*",
        default=list(K1_K8_GRADES),
        help="Grade levels to import (default: 1-8). Pass empty to import all grades.",
    )
    parser.add_argument(
        "--subject",
        type=str,
        default=None,
        help="Filter by academic subject, e.g. 'Mathematics' or 'ELA'.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Neo4j write batch size (default: 500).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and count without writing to Neo4j.",
    )
    parser.add_argument(
        "--skip-relationships",
        action="store_true",
        help="Only import nodes, skip relationships.",
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="Import ALL node types (Activity, Lesson, etc.). Default: prune noise, keep only Standards.",
    )
    args = parser.parse_args()

    nodes_path = args.exports_dir / "nodes.jsonl"
    rels_path = args.exports_dir / "relationships.jsonl"

    for p in [nodes_path, rels_path]:
        if not p.exists():
            logger.error(f"File not found: {p}")
            logger.error("Make sure the learning-commons-kg exports are in place.")
            sys.exit(1)

    grade_filter: set[str] | None = set(args.grades) if args.grades else None

    logger.info("=" * 60)
    logger.info("Learning Commons KG Importer")
    prune_noise = not args.no_prune
    logger.info(f"  Grades filter : {sorted(grade_filter) if grade_filter else 'ALL'}")
    logger.info(f"  Subject filter: {args.subject or 'ALL'}")
    logger.info(f"  Prune noise   : {prune_noise} (keep only Standard nodes, add β + conceptual_weight)")
    logger.info(f"  Batch size    : {args.batch_size}")
    logger.info(f"  Dry run       : {args.dry_run}")
    logger.info("=" * 60)

    t0 = time.time()

    # ── Step 1: Parse nodes ───────────────────────────────────────────────────
    logger.info("Parsing nodes.jsonl ...")
    nodes, allowed_ids = stream_nodes(
        nodes_path,
        grade_filter=grade_filter,
        subject_filter=args.subject,
        prune_noise=prune_noise,
    )

    # ── Step 2: Parse relationships ───────────────────────────────────────────
    grouped_rels: dict[str, list[dict]] = {}
    if not args.skip_relationships:
        logger.info("Parsing relationships.jsonl ...")
        grouped_rels = stream_relationships(rels_path, allowed_ids=allowed_ids)

    if args.dry_run:
        logger.info(f"DRY RUN — would write {len(nodes)} nodes and "
                    f"{sum(len(v) for v in grouped_rels.values())} relationships")
        return

    # ── Step 3: Connect and write ─────────────────────────────────────────────
    adapter = KnowledgeGraphAdapter()
    adapter.connect()

    try:
        logger.info("Creating indexes ...")
        adapter.create_all_indexes()

        logger.info(f"Writing {len(nodes)} nodes ...")
        nodes_written = write_nodes_batched(adapter, nodes, batch_size=args.batch_size)

        rels_written = 0
        if grouped_rels:
            logger.info("Writing relationships ...")
            rels_written = write_relationships_batched(adapter, grouped_rels, batch_size=args.batch_size)

        elapsed = time.time() - t0
        logger.success(
            f"Import complete — {nodes_written} nodes, {rels_written} relationships "
            f"in {elapsed:.1f}s"
        )
        logger.info("Neo4j stats:")
        for k, v in adapter.get_graph_stats().items():
            logger.info(f"  {k}: {v}")

    finally:
        adapter.close()


if __name__ == "__main__":
    main()
