from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from src.core.database import Database
from src.core.models import SearchChunk


@dataclass(slots=True)
class ChunkBuildResult:
    chunk_count: int
    rebuilt: bool
    fingerprint: str
    reason: str


def build_chunks_for_video(
    db: Database,
    video_id: int,
    min_ms: int = 10_000,
    max_ms: int = 25_000,
    overlap_segments: int = 2,
) -> list[SearchChunk]:
    segments = db.load_segments(video_id)
    if not segments:
        return []

    chunks: list[SearchChunk] = []
    start_index = 0
    while start_index < len(segments):
        current = []
        end_index = start_index
        start_ms = int(segments[start_index]["start_ms"])
        end_ms = start_ms
        while end_index < len(segments):
            seg = segments[end_index]
            current.append(seg)
            end_ms = int(seg["end_ms"])
            duration = end_ms - start_ms
            end_index += 1
            if duration >= min_ms and (duration >= max_ms or end_index >= len(segments)):
                break
            if duration >= max_ms:
                break
        if not current:
            break
        text = " ".join(str(seg["text_raw"]).strip() for seg in current if str(seg["text_raw"]).strip())
        segment_ids = [int(seg["id"]) for seg in current]
        chunks.append(SearchChunk(id=None, video_id=video_id, start_ms=start_ms, end_ms=end_ms, text=text, segment_ids=segment_ids))
        if end_index >= len(segments):
            break
        start_index = max(start_index + 1, end_index - max(0, overlap_segments))
    return chunks


def chunk_fingerprint_for_video(
    db: Database,
    video_id: int,
    min_ms: int = 10_000,
    max_ms: int = 25_000,
    overlap_segments: int = 2,
) -> str:
    segments = db.load_segments(video_id)
    payload = {
        "version": 1,
        "video_id": video_id,
        "params": {
            "min_ms": min_ms,
            "max_ms": max_ms,
            "overlap_segments": overlap_segments,
        },
        "segments": [
            {
                "start_ms": int(seg["start_ms"]),
                "end_ms": int(seg["end_ms"]),
                "text_raw": str(seg["text_raw"]),
            }
            for seg in segments
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def rebuild_chunks_for_video(
    db: Database,
    video_id: int,
    min_ms: int = 10_000,
    max_ms: int = 25_000,
    overlap_segments: int = 2,
    force: bool = False,
) -> ChunkBuildResult:
    row = db.get_video(video_id)
    if row is None:
        raise RuntimeError(f"视频记录不存在：{video_id}")

    target_fingerprint = chunk_fingerprint_for_video(
        db,
        video_id,
        min_ms=min_ms,
        max_ms=max_ms,
        overlap_segments=overlap_segments,
    )
    existing_count = db.count_chunks(video_id)
    if not force and existing_count > 0 and row["chunks_fingerprint"] == target_fingerprint:
        return ChunkBuildResult(
            chunk_count=existing_count,
            rebuilt=False,
            fingerprint=target_fingerprint,
            reason="fingerprint_match",
        )

    chunks = build_chunks_for_video(db, video_id, min_ms=min_ms, max_ms=max_ms, overlap_segments=overlap_segments)
    db.replace_chunks(video_id, chunks)
    db.update_chunks_fingerprint(video_id, target_fingerprint)
    reason = "force" if force else "fingerprint_changed"
    if existing_count == 0:
        reason = "missing_chunks"
    elif row["chunks_fingerprint"] is None:
        reason = "missing_fingerprint"
    return ChunkBuildResult(
        chunk_count=len(chunks),
        rebuilt=True,
        fingerprint=target_fingerprint,
        reason=reason,
    )
