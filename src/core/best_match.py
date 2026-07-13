from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from src.core.models import QuerySegment, SearchMatch

MIN_SCORE_THRESHOLD = 0.0


def pick_best_matches(matches: list[SearchMatch], queries: list[QuerySegment] | None = None) -> list[tuple[SearchMatch, str]]:
    by_index: dict[int, list[SearchMatch]] = defaultdict(list)
    for match in matches:
        by_index[match.query_index].append(match)

    query_text_by_index: dict[int, str] = {}
    if queries:
        for q in queries:
            query_text_by_index[q.index] = q.text
    for match in matches:
        if match.query_index not in query_text_by_index:
            query_text_by_index[match.query_index] = match.query_text

    sorted_indices = sorted(by_index.keys())
    result: list[tuple[SearchMatch, str]] = []
    for idx in sorted_indices:
        candidates = by_index[idx]
        best = max(candidates, key=lambda m: m.final_score)
        if best.final_score <= MIN_SCORE_THRESHOLD:
            continue
        text = query_text_by_index.get(idx, best.query_text)
        result.append((best, text))
    return result
