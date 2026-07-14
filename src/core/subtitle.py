from __future__ import annotations

from pathlib import Path
from collections.abc import Sequence
from src.core.models import SearchMatch

SRT_TEMPLATE = "{idx}\n{start} --> {end}\n{text}\n\n"


def ms_to_ts(ms: int) -> str:
    total_s = ms // 1000
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    millis = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


def text_duration_ms(text: str) -> int:
    """估算文案阅读时长：每字约 280ms，最短 2 秒，最长 15 秒。"""
    raw = text.strip()
    char_count = len(raw)
    calculated = int(char_count * 280)
    return max(2000, min(15000, calculated))


def generate_srt(segments: list[tuple[SearchMatch, str]], durations_ms: Sequence[int] | None = None) -> str:
    if durations_ms is not None:
        if len(durations_ms) != len(segments):
            raise ValueError("字幕时长数量与片段数量不一致。")
        durations = [int(duration) for duration in durations_ms]
        if any(duration <= 0 for duration in durations):
            raise ValueError("字幕时长必须大于 0。")
    else:
        durations = [text_duration_ms(text) for _match, text in segments]

    offset = 0
    parts: list[str] = []
    for idx, ((_match, text), duration) in enumerate(zip(segments, durations), start=1):
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


def segment_durations(segments: list[tuple[SearchMatch, str]]) -> list[int]:
    """返回每段文案对应的显示时长（毫秒），供截取视频时对齐使用。"""
    return [text_duration_ms(text) for _match, text in segments]


def save_srt(srt_text: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(srt_text, encoding="utf-8-sig")
    return path
