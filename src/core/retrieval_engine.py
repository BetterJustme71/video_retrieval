from __future__ import annotations

from collections import defaultdict
import hashlib
import json

from src.app.config import AppConfig
from src.core.database import Database
from src.core.embedding_index import EmbeddingIndex, fallback_score
from src.core.models import QuerySegment, SearchMatch
from src.core.text_utils import extract_entities, tokenized_text
from src.core.timecode import clamp_range


class RetrievalEngine:
    def __init__(self, db: Database, config: AppConfig):
        self.db = db
        self.config = config
        self.embedding_index = EmbeddingIndex(model_name=config.embedding_model)
        self._init_embedding_index()

    def _init_embedding_index(self) -> None:
        if not self.embedding_index.available():
            return
        chunks = self.db.list_chunks()
        if not chunks:
            return
        cache_key = self._embedding_cache_key(chunks)
        chunk_ids = [int(chunk["id"]) for chunk in chunks]
        cache_path = self.config.cache_dir / "index" / f"chunks_{cache_key}"
        if self.embedding_index.load(cache_path, cache_key=cache_key, chunk_ids=chunk_ids):
            return
        self.embedding_index.build(chunks)
        self.embedding_index.save(cache_path, cache_key=cache_key)

    def _embedding_cache_key(self, chunks) -> str:
        rows = self.db.chunk_fingerprint_rows()
        payload = {
            "version": 1,
            "model_name": self.config.embedding_model,
            "chunk_count": len(chunks),
            "chunk_ids": [int(chunk["id"]) for chunk in chunks],
            "videos": [
                {
                    "id": int(row["id"]),
                    "fingerprint": row["fingerprint"],
                    "transcript_fingerprint": row["transcript_fingerprint"],
                    "chunks_fingerprint": row["chunks_fingerprint"],
                }
                for row in rows
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def search_queries(self, queries: list[QuerySegment], top_k: int = 5) -> list[SearchMatch]:
        all_matches: list[SearchMatch] = []
        for query in queries:
            all_matches.extend(self.search_one(query, top_k=top_k))
        return all_matches

    def search_one(self, query: QuerySegment, top_k: int = 5) -> list[SearchMatch]:
        query_tokens = tokenized_text(query.text)
        fts_rows = self.db.search_fts(query_tokens, limit=max(100, top_k * 10))
        chunks = {int(row["id"]): row for row in fts_rows}

        # If FTS returns too little, scan all chunks with token overlap fallback.
        if len(chunks) < top_k:
            for row in self.db.list_chunks():
                chunks.setdefault(int(row["id"]), row)

        matches: list[SearchMatch] = []
        fts_rank_by_id = {int(row["id"]): float(row["rank"]) for row in fts_rows if "rank" in row.keys()}
        semantic_scores = self.embedding_index.score_query(query.text) if self.embedding_index.available() else {}
        for row in chunks.values():
            text_score = self._text_score(int(row["id"]), fts_rank_by_id, query.text, str(row["text_raw"]))
            semantic_score = semantic_scores.get(int(row["id"]), fallback_score(query.text, str(row["text_raw"])))
            entity_score = self._entity_score(query.text, str(row["text_raw"]))
            episode_score = self._episode_boost(query.text, row["episode_no"])
            final_score = 0.45 * text_score + 0.30 * semantic_score + 0.20 * entity_score + 0.05 * episode_score
            if final_score <= 0:
                continue
            preview_start, preview_end = clamp_range(
                int(row["start_ms"]) - self.config.preview_padding_ms,
                int(row["end_ms"]) + self.config.preview_padding_ms,
                int(row["duration_ms"]) if row["duration_ms"] is not None else None,
            )
            matches.append(
                SearchMatch(
                    query_index=query.index,
                    query_text=query.text,
                    query_type=query.query_type,
                    section=query.section,
                    video_id=int(row["video_id"]),
                    video_path=str(row["video_path"]),
                    video_filename=str(row["video_filename"]),
                    episode_no=int(row["episode_no"]) if row["episode_no"] is not None else None,
                    start_ms=int(row["start_ms"]),
                    end_ms=int(row["end_ms"]),
                    preview_start_ms=preview_start,
                    preview_end_ms=preview_end,
                    evidence_text=str(row["text_raw"]),
                    text_score=text_score,
                    semantic_score=semantic_score,
                    entity_score=entity_score,
                    episode_score=episode_score,
                    final_score=final_score,
                )
            )
        matches.sort(key=lambda m: m.final_score, reverse=True)
        return self._merge_adjacent(matches[: max(top_k * 2, top_k)], top_k=top_k)

    def _text_score(self, chunk_id: int, fts_rank_by_id: dict[int, float], query: str, text: str) -> float:
        overlap = fallback_score(query, text)
        if chunk_id in fts_rank_by_id:
            rank = abs(fts_rank_by_id[chunk_id])
            # Combine FTS rank with actual token overlap so common one-character hits do not dominate.
            rank_score = min(1.0, rank / (rank + 5.0)) if rank > 0 else 0.0
            return max(overlap, 0.65 * overlap + 0.35 * rank_score)
        return overlap

    def _entity_score(self, query: str, text: str) -> float:
        entities = extract_entities(query, self.config.entity_terms)
        if not entities:
            return 0.0
        hits = sum(1 for entity in entities if entity in text)
        return hits / len(entities)

    def _episode_boost(self, query: str, episode_no: int | None) -> float:
        if episode_no is None:
            return 0.0
        if episode_no == 1 and any(marker in query for marker in ["01", "第一集", "第1集", "开篇", "开头"]):
            return 1.0
        return 0.0

    def _merge_adjacent(self, matches: list[SearchMatch], top_k: int) -> list[SearchMatch]:
        # Keep one result per close time window to avoid duplicate overlapping chunks.
        accepted: list[SearchMatch] = []
        by_video: dict[int, list[SearchMatch]] = defaultdict(list)
        for match in matches:
            nearby = False
            for existing in by_video[match.video_id]:
                if abs(existing.start_ms - match.start_ms) < 20_000 or not (match.end_ms < existing.start_ms or match.start_ms > existing.end_ms):
                    nearby = True
                    break
            if nearby:
                continue
            accepted.append(match)
            by_video[match.video_id].append(match)
            if len(accepted) >= top_k:
                break
        return accepted
