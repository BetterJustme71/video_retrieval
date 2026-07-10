from __future__ import annotations

from pathlib import Path

from src.core.exporter import export_matches_csv, export_matches_json
from src.core.models import EDIT_LIST_STATUSES, SearchMatch


def filter_edit_list_matches(matches: list[SearchMatch]) -> list[SearchMatch]:
    return [match for match in matches if match.status in EDIT_LIST_STATUSES]


def export_editing_checklist(matches: list[SearchMatch], output_dir: Path, basename: str = "剪辑清单") -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = filter_edit_list_matches(matches)
    csv_path = output_dir / f"{basename}.csv"
    json_path = output_dir / f"{basename}.json"
    export_matches_csv(selected, csv_path)
    export_matches_json(selected, json_path)
    return csv_path, json_path
