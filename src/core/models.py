from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

MATCH_STATUS_PENDING = "待定"
MATCH_STATUS_USABLE = "可用"
MATCH_STATUS_BAD = "不准"
MATCH_STATUS_EXPORTED = "已导出"
MATCH_STATUSES = [MATCH_STATUS_PENDING, MATCH_STATUS_USABLE, MATCH_STATUS_BAD, MATCH_STATUS_EXPORTED]
EDIT_LIST_STATUSES = {MATCH_STATUS_USABLE, MATCH_STATUS_EXPORTED}


@dataclass(slots=True)
class VideoInfo:
    path: Path
    filename: str
    normalized_filename: str
    episode_no: int | None
    duration_ms: int | None
    size_bytes: int
    mtime: float
    fingerprint: str
    has_audio: bool
    has_subtitle: bool
    video_streams: list[dict[str, Any]]
    audio_streams: list[dict[str, Any]]
    subtitle_streams: list[dict[str, Any]]


@dataclass(slots=True)
class TranscriptSegment:
    video_id: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(slots=True)
class SearchChunk:
    id: int | None
    video_id: int
    start_ms: int
    end_ms: int
    text: str
    segment_ids: list[int]


@dataclass(slots=True)
class QuerySegment:
    index: int
    section: str
    query_type: str
    text: str


@dataclass(slots=True)
class SearchMatch:
    query_index: int
    query_text: str
    query_type: str
    section: str
    video_id: int
    video_path: str
    video_filename: str
    episode_no: int | None
    start_ms: int
    end_ms: int
    preview_start_ms: int
    preview_end_ms: int
    evidence_text: str
    text_score: float
    semantic_score: float
    entity_score: float
    episode_score: float
    final_score: float
    status: str = MATCH_STATUS_PENDING
    export_path: str = ""
    thumbnail_path: str = ""
    match_id: int | None = None
    run_id: int | None = None
    query_segment_id: int | None = None
