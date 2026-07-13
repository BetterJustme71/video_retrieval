from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from src.core.models import SearchMatch
from src.core.subtitle import generate_srt, save_srt
from src.core.timecode import ms_to_timecode

ProgressCallback = Callable[[str], None]


def ffmpeg_time(ms: int) -> str:
    return ms_to_timecode(ms)


def safe_filename(text: str, max_len: int = 80) -> str:
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "clip"


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


def assemble_clips(
    segments: list[tuple[SearchMatch, str]],
    output_dir: Path,
    name: str,
    progress: ProgressCallback | None = None,
) -> dict:
    progress = progress or (lambda _message: None)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，请先安装 FFmpeg 并加入 PATH。")
    output_dir.mkdir(parents=True, exist_ok=True)
    clips_dir = output_dir / f"{name}_clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clip_paths: list[Path] = []
    total_duration_ms = 0
    for idx, (match, text) in enumerate(segments):
        seg_duration = max(1000, (match.preview_end_ms or match.end_ms) - (match.preview_start_ms or match.start_ms))
        clip_name = f"Q{idx:03d}.mp4"
        clip_path = clips_dir / clip_name
        start_ms = match.preview_start_ms if match.preview_start_ms else match.start_ms
        end_ms = match.preview_end_ms if match.preview_end_ms else match.end_ms
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            ffmpeg_time(start_ms),
            "-i",
            str(Path(match.video_path)),
            "-t",
            ffmpeg_time(max(1000, end_ms - start_ms)),
            "-map",
            "0",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(clip_path),
        ]
        progress(f"截取片段 {idx+1}/{len(segments)}：{clip_name}")
        completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
        if completed.returncode != 0:
            progress(f"重编码截取：{clip_name}")
            cmd = [
                ffmpeg,
                "-y",
                "-ss",
                ffmpeg_time(start_ms),
                "-i",
                str(Path(match.video_path)),
                "-t",
                ffmpeg_time(max(1000, end_ms - start_ms)),
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
                str(clip_path),
            ]
            completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
            if completed.returncode != 0:
                raise RuntimeError(f"截取失败：{clip_name} {completed.stderr.strip()}")
        clip_paths.append(clip_path)
        total_duration_ms += seg_duration
    concat_txt = output_dir / f"{name}_concat.txt"
    concat_txt.write_text("\n".join(f"file '{p.resolve()}'" for p in clip_paths), encoding="utf-8")
    video_path = output_dir / f"{name}.mp4"
    progress("拼接片段…")
    concat_cmd = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_txt),
        "-c",
        "copy",
        str(video_path),
    ]
    completed = subprocess.run(concat_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
    if completed.returncode != 0:
        progress(f"流复制拼接失败，改用重编码拼接：{completed.stderr.strip()}")
        concat_cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_txt),
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
            str(video_path),
        ]
        completed = subprocess.run(concat_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"拼接失败：{completed.stderr.strip()}")
    srt_text = generate_srt(segments)
    srt_path = output_dir / f"{name}.srt"
    save_srt(srt_text, srt_path)
    progress(f"拼接完成：{video_path}")
    progress(f"字幕文件：{srt_path}")
    return {
        "video_path": str(video_path),
        "srt_path": str(srt_path),
        "clip_count": len(clip_paths),
        "duration_ms": total_duration_ms,
    }


def burn_subtitle(video_path: Path, srt_path: Path, output_path: Path, progress: ProgressCallback | None = None) -> Path:
    progress = progress or (lambda _message: None)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，请先安装 FFmpeg 并加入 PATH。")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"subtitles={str(srt_path.resolve())}",
        "-c:a",
        "copy",
        str(output_path),
    ]
    progress(f"烧录字幕：{output_path.name}")
    completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"烧录字幕失败：{completed.stderr.strip()}")
    progress(f"烧录完成：{output_path}")
    return output_path
