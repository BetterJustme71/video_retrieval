from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from src.core.models import VideoInfo
from src.core.timecode import seconds_to_ms
from src.core.transcript_providers import find_sidecar_subtitles

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".ts", ".m4v", ".webm", ".mpg", ".mpeg"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt"}
EPISODE_RE = re.compile(r"第\s*(\d+)\s*集")


class FFprobeNotFoundError(RuntimeError):
    pass


def normalize_filename(filename: str) -> str:
    stem = Path(filename).stem.strip()
    suffix = Path(filename).suffix.lower().strip()
    return f"{stem}{suffix}"


def extract_episode_no(filename: str) -> int | None:
    match = EPISODE_RE.search(filename)
    if match:
        return int(match.group(1))
    return None


def video_fingerprint(path: Path, duration_ms: int | None) -> str:
    stat = path.stat()
    raw = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime}|{duration_ms or ''}"
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def run_ffprobe(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise FFprobeNotFoundError("未找到 ffprobe，请先安装 FFmpeg 并加入 PATH。")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,channels,language:stream_tags=language,title",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe 读取失败：{path}\n{completed.stderr.strip()}")
    return json.loads(completed.stdout or "{}")


class MediaScanner:
    def __init__(self, video_dir: Path):
        self.video_dir = Path(video_dir)

    def scan(self, probe: bool = True) -> list[VideoInfo]:
        if not self.video_dir.exists():
            raise FileNotFoundError(f"视频目录不存在：{self.video_dir}")
        if not self.video_dir.is_dir():
            raise NotADirectoryError(f"不是视频目录：{self.video_dir}")
        paths = [p for p in self.video_dir.iterdir() if p.is_file() and p.suffix.lower().strip() in VIDEO_EXTENSIONS]
        infos = [self._build_info(path, probe=probe) for path in paths]
        infos.sort(key=lambda info: (info.episode_no is None, info.episode_no or 10**9, info.normalized_filename))
        return infos

    def _build_info(self, path: Path, probe: bool) -> VideoInfo:
        stat = path.stat()
        normalized = normalize_filename(path.name)
        episode = extract_episode_no(path.name)
        duration_ms: int | None = None
        video_streams: list[dict[str, Any]] = []
        audio_streams: list[dict[str, Any]] = []
        subtitle_streams: list[dict[str, Any]] = []
        if probe:
            data = run_ffprobe(path)
            try:
                duration = float(data.get("format", {}).get("duration") or 0)
                duration_ms = seconds_to_ms(duration)
            except (TypeError, ValueError):
                duration_ms = None
            for stream in data.get("streams", []) or []:
                codec_type = stream.get("codec_type")
                if codec_type == "video":
                    video_streams.append(stream)
                elif codec_type == "audio":
                    audio_streams.append(stream)
                elif codec_type == "subtitle":
                    subtitle_streams.append(stream)
        return VideoInfo(
            path=path.resolve(),
            filename=path.name,
            normalized_filename=normalized,
            episode_no=episode,
            duration_ms=duration_ms,
            size_bytes=stat.st_size,
            mtime=stat.st_mtime,
            fingerprint=video_fingerprint(path, duration_ms),
            has_audio=bool(audio_streams) if probe else True,
            has_subtitle=bool(subtitle_streams) or bool(find_sidecar_subtitles(path)),
            video_streams=video_streams,
            audio_streams=audio_streams,
            subtitle_streams=subtitle_streams,
        )
