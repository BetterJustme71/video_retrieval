from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable

from src.core.models import SearchMatch
from src.core.timecode import ms_to_timecode
from src.core.text_utils import summarize

ProgressCallback = Callable[[str], None]


def ffmpeg_time(ms: int) -> str:
    return ms_to_timecode(ms)


def safe_filename(text: str, max_len: int = 80) -> str:
    text = summarize(text, max_len=max_len)
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "clip"


def open_match_preview(match: SearchMatch, progress: ProgressCallback | None = None) -> None:
    progress = progress or (lambda _message: None)
    video_path = Path(match.video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"视频文件不存在：{video_path}")

    ffplay = shutil.which("ffplay")
    if ffplay:
        cmd = [
            ffplay,
            "-autoexit",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-ss",
            ffmpeg_time(match.preview_start_ms),
            "-i",
            str(video_path),
        ]
        subprocess.Popen(cmd)
        progress(f"已用 ffplay 打开预览：{video_path.name} @ {ffmpeg_time(match.preview_start_ms)}")
        return

    if os.name == "nt":
        os.startfile(str(video_path))  # type: ignore[attr-defined]
        progress("未找到 ffplay，已打开视频文件；请手动跳转到表格中的时间码。")
        return

    raise RuntimeError("未找到 ffplay，当前系统也不支持 os.startfile 打开视频。")


def export_clip(
    match: SearchMatch,
    output_dir: Path,
    use_preview_range: bool = True,
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
    start_ms = match.preview_start_ms if use_preview_range else match.start_ms
    end_ms = match.preview_end_ms if use_preview_range else match.end_ms
    duration_ms = max(1000, end_ms - start_ms)
    filename = (
        f"Q{match.query_index:03d}_第{match.episode_no or '未知'}集_"
        f"{ffmpeg_time(start_ms).replace(':', '-')}_{safe_filename(match.query_text, 36)}.mp4"
    )
    output_path = output_dir / filename

    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        ffmpeg_time(start_ms),
        "-i",
        str(video_path),
        "-t",
        ffmpeg_time(duration_ms),
        "-map",
        "0",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        str(output_path),
    ]
    progress(f"导出片段：{output_path.name}")
    completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
    if completed.returncode != 0:
        # Some MP4 streams cannot be cleanly copied from arbitrary timestamps; re-encode as fallback.
        progress("无损截取失败，改用重编码导出。")
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            ffmpeg_time(start_ms),
            "-i",
            str(video_path),
            "-t",
            ffmpeg_time(duration_ms),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(output_path),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"导出失败：{completed.stderr.strip()}")
    progress(f"导出完成：{output_path}")
    match.export_path = str(output_path)
    return output_path


def export_clips(
    matches: Iterable[SearchMatch],
    output_dir: Path,
    limit: int | None = None,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    selected = list(matches)
    if limit is not None:
        selected = selected[:limit]
    outputs: list[Path] = []
    for match in selected:
        outputs.append(export_clip(match, output_dir, progress=progress))
    return outputs
