from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable

from src.core.clipper import ffmpeg_time, safe_filename
from src.core.models import SearchMatch

ProgressCallback = Callable[[str], None]


def thumbnail_time_ms(match: SearchMatch) -> int:
    start = match.preview_start_ms if match.preview_start_ms is not None else match.start_ms
    end = match.preview_end_ms if match.preview_end_ms is not None else match.end_ms
    if end <= start:
        return max(0, start)
    return start + (end - start) // 2


def export_thumbnail(
    match: SearchMatch,
    output_dir: Path,
    width: int = 480,
    progress: ProgressCallback | None = None,
) -> Path:
    progress = progress or (lambda _message: None)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，请先安装 FFmpeg 并加入 PATH。")

    video_path = Path(match.video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"视频文件不存在：{video_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    shot_ms = thumbnail_time_ms(match)
    filename = (
        f"Q{match.query_index:03d}_第{match.episode_no or '未知'}集_"
        f"{ffmpeg_time(shot_ms).replace(':', '-')}_{safe_filename(match.query_text, 36)}.jpg"
    )
    output_path = output_dir / filename
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        ffmpeg_time(shot_ms),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-2",
        "-q:v",
        "3",
        str(output_path),
    ]
    progress(f"生成缩略图：{output_path.name}")
    completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"缩略图生成失败：{completed.stderr.strip()}")
    match.thumbnail_path = str(output_path)
    progress(f"缩略图完成：{output_path}")
    return output_path


def export_thumbnails(
    matches: Iterable[SearchMatch],
    output_dir: Path,
    width: int = 480,
    limit: int | None = None,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    selected = list(matches)
    if limit is not None:
        selected = selected[:limit]
    outputs: list[Path] = []
    for match in selected:
        outputs.append(export_thumbnail(match, output_dir, width=width, progress=progress))
    return outputs
