"""
Knowledge graph builder for the Adaptive Learning Engine.

Extracts curriculum concepts from text using YAKE keyword extraction,
mines co-occurrence relationships, and scores nodes via PageRank.
"""

from __future__ import annotations

from collections import Counter

import networkx as nx
from loguru import logger

from backend.app.kg.schema import ConceptRecord


def extract_keywords(text: str, top_n: int = 20, ngram_max: int = 3) -> list[str]:
    """
    Extract top keywords from text using YAKE.

    Returns a deduplicated list of concept strings, longest-first.
    """
    try:
        import yake

        extractor = yake.KeywordExtractor(
            lan="en",
            n=ngram_max,
            dedupLim=0.7,
            top=top_n,
            features=None,
        )
        keywords = extractor.extract_keywords(text)
        return [kw for kw, _ in keywords]
    except ImportError:
        logger.warning("yake not installed — falling back to simple word split")
        words = [w.strip(".,;:") for w in text.split() if len(w) > 4]
        return list(dict.fromkeys(words))[:top_n]


def build_concept_records(
    text_records: list[dict],
    max_concepts: int = 300,
    seed_terms: list[str] | None = None,
) -> list[ConceptRecord]:
    """
    Given a list of text records (each with 'text', 'source_file', 'grade_band', 'subject'),
    extract concept nodes with frequency and importance scores.

    Args:
        text_records: List of dicts with at least 'text' key.
        max_concepts: Hard cap on number of concept nodes to return.
        seed_terms: Known key terms to always include.

    Returns:
        List of ConceptRecord sorted by importance_score descending.
    """
    counter: Counter[str] = Counter()
    concept_sources: dict[str, str] = {}

    seed_set = {t.lower().strip() for t in (seed_terms or [])}

    for record in text_records:
        text = record.get("text", "")
        source = record.get("source_file", "")
        grade = record.get("grade_band", "")
        subject = record.get("subject", "")

        keywords = extract_keywords(text, top_n=25)

        for kw in keywords:
            clean = kw.strip().title()
            if len(clean) < 3 or len(clean.split()) > 5:
                continue
            counter[clean] += 1
            if clean not in concept_sources:
                concept_sources[clean] = source

        for term in seed_terms or []:
            if term.lower() in text.lower():
                clean = term.strip().title()
                counter[clean] += 2
                concept_sources.setdefault(clean, source)

    top = [name for name, _ in counter.most_common(max_concepts)]

    # Build cooccurrence graph for PageRank importance
    G: nx.Graph = nx.Graph()
    for name in top:
        G.add_node(name)

    for record in text_records:
        text_lower = record.get("text", "").lower()
        present = [c for c in top if c.lower() in text_lower]
        for i, c1 in enumerate(present):
            for c2 in present[i + 1 :]:
                pair = tuple(sorted([c1, c2]))
                if G.has_edge(*pair):
                    G[pair[0]][pair[1]]["weight"] += 1
                else:
                    G.add_edge(pair[0], pair[1], weight=1)

    pagerank: dict[str, float] = {}
    if G.nodes:
        try:
            raw_pr = nx.pagerank(G, weight="weight")
            max_pr = max(raw_pr.values()) or 1.0
            pagerank = {k: v / max_pr for k, v in raw_pr.items()}
        except Exception as exc:
            logger.warning(f"PageRank failed: {exc}")

    concepts: list[ConceptRecord] = []
    for name in top:
        concepts.append(
            ConceptRecord(
                name=name,
                source_file=concept_sources.get(name),
                frequency=counter[name],
                importance_score=pagerank.get(name, 0.0),
                tags=[],
            )
        )

    concepts.sort(key=lambda c: c.importance_score, reverse=True)
    logger.info(f"Built {len(concepts)} concept records from {len(text_records)} text records")
    return concepts
