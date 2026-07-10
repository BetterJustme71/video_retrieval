from __future__ import annotations

from src.core.database import Database
from src.core.models import SearchChunk


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


def rebuild_chunks_for_video(
    db: Database,
    video_id: int,
    min_ms: int = 10_000,
    max_ms: int = 25_000,
    overlap_segments: int = 2,
) -> int:
    chunks = build_chunks_for_video(db, video_id, min_ms=min_ms, max_ms=max_ms, overlap_segments=overlap_segments)
    db.replace_chunks(video_id, chunks)
    return len(chunks)
