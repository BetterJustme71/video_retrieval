from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from src.core.models import SearchMatch
from src.core.timecode import ms_to_timecode


def match_to_export_row(match: SearchMatch) -> dict:
    row = asdict(match)
    row.update(
        {
            "start_time": ms_to_timecode(match.start_ms),
            "end_time": ms_to_timecode(match.end_ms),
            "preview_start_time": ms_to_timecode(match.preview_start_ms),
            "preview_end_time": ms_to_timecode(match.preview_end_ms),
        }
    )
    return row


def export_matches_csv(matches: list[SearchMatch], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [match_to_export_row(m) for m in matches]
    fieldnames = list(rows[0].keys()) if rows else [
        "query_index", "query_text", "query_type", "section", "episode_no", "video_filename",
        "start_time", "end_time", "preview_start_time", "preview_end_time", "final_score", "evidence_text",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def export_matches_json(matches: list[SearchMatch], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [match_to_export_row(m) for m in matches]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
