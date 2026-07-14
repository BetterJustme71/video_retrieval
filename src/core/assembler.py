from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from src.core.audio import AssemblyAudioOptions, finalize_audio, probe_duration_ms, synthesize_narration
from src.core.models import SearchMatch
from src.core.subtitle import generate_srt, save_srt, segment_durations
from src.core.timecode import ms_to_timecode

ProgressCallback = Callable[[str], None]


def ffmpeg_time(ms: int) -> str:
    return ms_to_timecode(ms)


def safe_filename(text: str, max_len: int = 80) -> str:
    text = re.sub(r"[\\\/:*?\"<>|\r\n\t]+", "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    return (text or "clip")[:max_len]


def _concat_manifest_line(path: Path) -> str:
    normalized = path.resolve().as_posix().replace("'", "\\'")
    return f"file '{normalized}'"


def _replace_file(source: Path, target: Path) -> None:
    backup: Path | None = None
    if target.exists():
        backup = target.with_suffix(target.suffix + ".bak")
        os.replace(target, backup)
    try:
        os.replace(source, target)
    except Exception:
        if backup is not None:
            os.replace(backup, target)
        raise
    if backup is not None:
        backup.unlink(missing_ok=True)


def _export_timed_visual_clip(
    match: SearchMatch,
    clip_path: Path,
    duration_ms: int,
    progress: ProgressCallback,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，请先安装 FFmpeg 并加入 PATH。")
    video_path = Path(match.video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"视频文件不存在：{video_path}")
    start_ms = match.preview_start_ms if match.preview_start_ms else match.start_ms
    duration_s = max(0.001, duration_ms / 1000)
    filter_complex = (
        f"[0:v]setpts=PTS-STARTPTS,"
        f"tpad=stop_mode=clone:stop_duration={duration_s:.3f},"
        f"trim=duration={duration_s:.3f},"
        "setpts=PTS-STARTPTS[v]"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        ffmpeg_time(start_ms),
        "-i",
        str(video_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-t",
        f"{duration_s:.3f}",
        str(clip_path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"按配音时长截取失败：{clip_path.name} {completed.stderr.strip()}")
    try:
        actual_ms = probe_duration_ms(clip_path)
    except Exception as exc:
        raise RuntimeError(f"无法验证片段时长：{clip_path.name} {exc}") from exc
    if abs(actual_ms - duration_ms) > 250:
        progress(f"警告：{clip_path.name} 时长与配音相差 {abs(actual_ms - duration_ms)}ms")


def _export_legacy_assembly_clip(
    match: SearchMatch,
    clip_path: Path,
    duration_ms: int,
    progress: ProgressCallback,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，请先安装 FFmpeg 并加入 PATH。")
    start_ms = match.preview_start_ms if match.preview_start_ms else match.start_ms
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        ffmpeg_time(start_ms),
        "-i",
        str(Path(match.video_path)),
        "-t",
        ffmpeg_time(duration_ms),
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        str(clip_path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
    if completed.returncode != 0:
        progress(f"重编码截取：{clip_path.name}")
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            ffmpeg_time(start_ms),
            "-i",
            str(Path(match.video_path)),
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
            str(clip_path),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"截取失败：{clip_path.name} {completed.stderr.strip()}")


def _concat_clips(clip_paths: list[Path], concat_txt: Path, video_path: Path, progress: ProgressCallback) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，请先安装 FFmpeg 并加入 PATH。")
    concat_txt.write_text("\n".join(_concat_manifest_line(p) for p in clip_paths), encoding="utf-8")
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
    audio_options: AssemblyAudioOptions | None = None,
) -> dict:
    progress = progress or (lambda _message: None)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，请先安装 FFmpeg 并加入 PATH。")
    if not segments:
        raise ValueError("没有可组装的片段。")

    audio_options = audio_options or AssemblyAudioOptions()
    audio_options.validate()
    output_dir.mkdir(parents=True, exist_ok=True)
    clips_dir = output_dir / f"{name}_clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = output_dir / f"{name}_audio"
    narration = None

    if audio_options.tts_enabled:
        narration = synthesize_narration([text for _match, text in segments], audio_dir, audio_options, progress=progress)
        durations = narration.segment_durations_ms
    else:
        durations = segment_durations(segments)

    total_duration_ms = sum(durations)
    clip_paths: list[Path] = []
    for idx, (match, _text) in enumerate(segments):
        seg_duration = durations[idx]
        clip_name = f"Q{idx:03d}.mp4"
        clip_path = clips_dir / clip_name
        progress(f"截取片段 {idx+1}/{len(segments)}：{clip_name}")
        if audio_options.tts_enabled:
            _export_timed_visual_clip(match, clip_path, seg_duration, progress)
        else:
            _export_legacy_assembly_clip(match, clip_path, seg_duration, progress)
        clip_paths.append(clip_path)

    concat_txt = output_dir / f"{name}_concat.txt"
    final_video_path = output_dir / f"{name}.mp4"
    base_video_path = output_dir / f"{name}_base.mp4" if audio_options.enabled else final_video_path
    _concat_clips(clip_paths, concat_txt, base_video_path, progress)

    if audio_options.enabled:
        mixed_path = output_dir / f"{name}_mixed.mp4"
        if audio_options.tts_enabled:
            final_duration_ms = total_duration_ms
        else:
            actual_ms = probe_duration_ms(base_video_path)
            final_duration_ms = max(total_duration_ms, actual_ms)
        finalize_audio(
            base_video_path,
            mixed_path,
            final_duration_ms,
            narration.narration_path if narration is not None else None,
            audio_options,
            progress=progress,
        )
        _replace_file(mixed_path, final_video_path)

    srt_text = generate_srt(segments, durations)
    srt_path = output_dir / f"{name}.srt"
    save_srt(srt_text, srt_path)
    progress(f"拼接完成：{final_video_path}")
    progress(f"字幕文件：{srt_path}")
    return {
        "video_path": str(final_video_path),
        "srt_path": str(srt_path),
        "clip_count": len(clip_paths),
        "duration_ms": total_duration_ms,
        "tts_enabled": audio_options.tts_enabled,
        "narration_path": str(narration.narration_path) if narration is not None else None,
        "bgm_path": str(audio_options.bgm_path) if audio_options.bgm_path is not None else None,
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
