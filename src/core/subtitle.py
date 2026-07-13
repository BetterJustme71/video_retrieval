from __future__ import annotations

from pathlib import Path
from src.core.models import SearchMatch

SRT_TEMPLATE = "{idx}\n{start} --> {end}\n{text}\n\n"


def ms_to_ts(ms: int) -> str:
    total_s = ms // 1000
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    millis = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


def generate_srt(segments: list[tuple[SearchMatch, str]]) -> str:
    offset = 0
    parts: list[str] = []
    for idx, (match, text) in enumerate(segments, start=1):
        start_ms = match.start_ms if match.start_ms is not None else 0
        end_ms = match.end_ms if match.end_ms is not None else start_ms
        duration = max(500, end_ms - start_ms)
        seg_start = offset
        seg_end = offset + duration
        parts.append(
            SRT_TEMPLATE.format(
                idx=idx,
                start=ms_to_ts(seg_start),
                end=ms_to_ts(seg_end),
                text=text.strip(),
            )
        )
        offset += duration
    return "".join(parts)


def save_srt(srt_text: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(srt_text, encoding="utf-8-sig")
    return path
