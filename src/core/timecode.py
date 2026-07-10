from __future__ import annotations


def ms_to_timecode(ms: int | None) -> str:
    if ms is None:
        return ""
    ms = max(0, int(ms))
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def seconds_to_ms(seconds: float | int | None) -> int | None:
    if seconds is None:
        return None
    return int(float(seconds) * 1000)


def clamp_range(start_ms: int, end_ms: int, duration_ms: int | None = None) -> tuple[int, int]:
    start = max(0, int(start_ms))
    end = max(start, int(end_ms))
    if duration_ms is not None and duration_ms > 0:
        end = min(end, duration_ms)
        start = min(start, end)
    return start, end
