from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.models import TranscriptSegment

PROVIDER_VERSION = "subtitle-provider-v1"
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt"}
TEXT_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}
IMAGE_SUBTITLE_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "xsub"}
CHINESE_HINTS = ("zh", "chi", "zho", "chs", "cht", "sc", "tc", "简", "繁", "中文", "中字")
EXTENSION_PRIORITY = {".srt": 0, ".ass": 1, ".ssa": 1, ".vtt": 2}
TIME_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}|\d{1,2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}|\d{1,2}:\d{2}[,.]\d{1,3})"
)
TAG_RE = re.compile(r"<[^>]+>")
ASS_OVERRIDE_RE = re.compile(r"\{[^}]*\}")


@dataclass(slots=True)
class TranscriptCandidate:
    kind: str
    priority: int
    source_ref: str
    source_fingerprint: str
    display_name: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TranscriptLoadResult:
    segments: list[TranscriptSegment]
    source: str
    source_ref: str
    source_fingerprint: str
    transcript_fingerprint: str


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def file_content_hash(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def source_fingerprint(parts: list[str]) -> str:
    return sha1_text("|".join([PROVIDER_VERSION, *parts]))


def transcript_segments_fingerprint(segments: list[TranscriptSegment]) -> str:
    payload = [
        {"start_ms": seg.start_ms, "end_ms": seg.end_ms, "text": seg.text.strip()}
        for seg in segments
        if seg.text.strip()
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha1_text(raw)


def find_sidecar_subtitles(video_path: Path) -> list[Path]:
    directory = video_path.parent
    stem = video_path.stem
    if not directory.exists():
        return []
    candidates: list[Path] = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() not in SUBTITLE_EXTENSIONS:
            continue
        item_stem = path.stem
        if item_stem == stem or item_stem.startswith(stem + "."):
            candidates.append(path)
    return sorted(candidates, key=lambda path: _sidecar_rank(video_path, path))


def choose_sidecar_candidate(video_path: Path, video_fingerprint: str) -> TranscriptCandidate | None:
    paths = find_sidecar_subtitles(video_path)
    if not paths:
        return None
    path = paths[0]
    content_hash = file_content_hash(path)
    fingerprint = source_fingerprint([
        "sidecar",
        video_fingerprint,
        str(path.resolve()),
        content_hash,
    ])
    return TranscriptCandidate(
        kind="sidecar",
        priority=10,
        source_ref=str(path.resolve()),
        source_fingerprint=fingerprint,
        display_name=f"外挂字幕：{path.name}",
        payload={"path": str(path.resolve()), "suffix": path.suffix.lower()},
    )


def choose_embedded_candidate(video_row: Any) -> TranscriptCandidate | None:
    streams = _subtitle_streams(video_row)
    text_streams = [stream for stream in streams if _stream_codec(stream) in TEXT_SUBTITLE_CODECS]
    if not text_streams:
        return None
    text_streams.sort(key=_embedded_stream_rank)
    stream = text_streams[0]
    stream_index = str(stream.get("index", ""))
    codec = _stream_codec(stream)
    language = _stream_language(stream)
    title = _stream_title(stream)
    video_fingerprint = str(video_row["fingerprint"] or "")
    fingerprint = source_fingerprint([
        "embedded",
        video_fingerprint,
        stream_index,
        codec,
        language,
        title,
    ])
    source_ref = f"stream:{stream_index}/{codec}/{language or 'unknown'}"
    return TranscriptCandidate(
        kind="embedded",
        priority=20,
        source_ref=source_ref,
        source_fingerprint=fingerprint,
        display_name=f"内嵌字幕：{source_ref}",
        payload={"stream_index": stream_index, "codec": codec, "language": language, "title": title},
    )


def asr_candidate(video_row: Any, model_size: str, device: str, compute_type: str) -> TranscriptCandidate:
    video_fingerprint = str(video_row["fingerprint"] or "")
    source_ref = f"whisper:{model_size}/{device}/{compute_type}"
    fingerprint = source_fingerprint(["asr", video_fingerprint, model_size, device, compute_type])
    return TranscriptCandidate(
        kind="asr",
        priority=100,
        source_ref=source_ref,
        source_fingerprint=fingerprint,
        display_name=f"ASR：{source_ref}",
        payload={"model_size": model_size, "device": device, "compute_type": compute_type},
    )


def load_subtitle_candidate(video_id: int, video_path: Path, candidate: TranscriptCandidate) -> TranscriptLoadResult:
    if candidate.kind == "sidecar":
        subtitle_path = Path(str(candidate.payload["path"]))
        text = subtitle_to_srt_text(subtitle_path)
    elif candidate.kind == "embedded":
        text = embedded_subtitle_to_srt_text(video_path, str(candidate.payload["stream_index"]))
    else:
        raise ValueError(f"不支持的字幕来源：{candidate.kind}")
    segments = parse_srt_like_text(text, video_id)
    if not segments:
        raise RuntimeError(f"字幕为空或无法解析：{candidate.display_name}")
    return TranscriptLoadResult(
        segments=segments,
        source=candidate.kind,
        source_ref=candidate.source_ref,
        source_fingerprint=candidate.source_fingerprint,
        transcript_fingerprint=transcript_segments_fingerprint(segments),
    )


def subtitle_to_srt_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".srt", ".vtt"}:
        return read_text_guess(path)
    return ffmpeg_subtitle_to_srt(["-i", str(path)])


def embedded_subtitle_to_srt_text(video_path: Path, stream_index: str) -> str:
    return ffmpeg_subtitle_to_srt(["-i", str(video_path), "-map", f"0:{stream_index}"])


def ffmpeg_subtitle_to_srt(input_args: list[str]) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，请先安装 FFmpeg 并加入 PATH。")
    cmd = [ffmpeg, "-v", "error", *input_args, "-f", "srt", "pipe:1"]
    completed = subprocess.run(cmd, capture_output=True, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"字幕转换失败：{stderr}")
    return completed.stdout.decode("utf-8", errors="ignore")


def read_text_guess(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def parse_srt_like_text(text: str, video_id: int) -> list[TranscriptSegment]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    segments: list[TranscriptSegment] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip().lstrip("﻿")
        match = TIME_RE.search(line)
        if not match:
            index += 1
            continue
        start_ms = parse_subtitle_time(match.group("start"))
        end_ms = parse_subtitle_time(match.group("end"))
        index += 1
        text_lines: list[str] = []
        while index < len(lines):
            body = lines[index].strip()
            if not body:
                break
            if body.upper().startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
                index += 1
                continue
            text_lines.append(clean_subtitle_text(body))
            index += 1
        merged = " ".join(item for item in text_lines if item).strip()
        if merged and end_ms > start_ms:
            segments.append(TranscriptSegment(video_id=video_id, start_ms=start_ms, end_ms=end_ms, text=merged))
        index += 1
    return segments


def parse_subtitle_time(value: str) -> int:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
    elif len(parts) == 2:
        hours = 0
        minutes = int(parts[0])
        seconds = float(parts[1])
    else:
        raise ValueError(f"无效字幕时间码：{value}")
    return int(round((hours * 3600 + minutes * 60 + seconds) * 1000))


def clean_subtitle_text(text: str) -> str:
    text = ASS_OVERRIDE_RE.sub("", text)
    text = TAG_RE.sub("", text)
    text = text.replace("\\N", " ").replace("\\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def _sidecar_rank(video_path: Path, subtitle_path: Path) -> tuple[int, int, int, str]:
    exact = 0 if subtitle_path.stem == video_path.stem else 1
    chinese = 0 if _contains_chinese_hint(subtitle_path.stem) else 1
    ext_rank = EXTENSION_PRIORITY.get(subtitle_path.suffix.lower(), 99)
    return (exact, chinese, ext_rank, subtitle_path.name.lower())


def _subtitle_streams(video_row: Any) -> list[dict[str, Any]]:
    raw = video_row["subtitle_streams_json"] if "subtitle_streams_json" in video_row.keys() else "[]"
    try:
        streams = json.loads(raw or "[]")
    except Exception:
        return []
    return [stream for stream in streams if isinstance(stream, dict)]


def _stream_codec(stream: dict[str, Any]) -> str:
    return str(stream.get("codec_name") or "").lower()


def _stream_language(stream: dict[str, Any]) -> str:
    tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
    return str(stream.get("language") or tags.get("language") or "").lower()


def _stream_title(stream: dict[str, Any]) -> str:
    tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
    return str(tags.get("title") or stream.get("title") or "")


def _embedded_stream_rank(stream: dict[str, Any]) -> tuple[int, int]:
    text = " ".join([_stream_language(stream), _stream_title(stream), _stream_codec(stream)]).lower()
    chinese = 0 if _contains_chinese_hint(text) else 1
    try:
        index = int(stream.get("index") or 0)
    except (TypeError, ValueError):
        index = 10**9
    return (chinese, index)


def _contains_chinese_hint(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in CHINESE_HINTS)
