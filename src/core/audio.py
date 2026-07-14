from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ProgressCallback = Callable[[str], None]

_RATE_PATTERN = re.compile(r"^[+-]\d+%$")


@dataclass(slots=True)
class AssemblyAudioOptions:
    tts_enabled: bool = False
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_rate: str = "+0%"
    bgm_path: Path | None = None
    bgm_volume: float = 0.15

    @property
    def bgm_enabled(self) -> bool:
        return self.bgm_path is not None

    @property
    def enabled(self) -> bool:
        return self.tts_enabled or self.bgm_enabled

    def validate(self) -> None:
        if self.tts_enabled:
            if not self.tts_voice.strip():
                raise ValueError("启用 AI 旁白时，配音音色不能为空。")
            if not _RATE_PATTERN.match(self.tts_rate.strip()):
                raise ValueError("配音语速格式无效，请使用 +0%、-10% 这类格式。")
        if not 0 <= float(self.bgm_volume) <= 1:
            raise ValueError("BGM 音量必须在 0.0 到 1.0 之间。")
        if self.bgm_path is not None:
            bgm_path = Path(self.bgm_path)
            if not bgm_path.exists() or not bgm_path.is_file():
                raise FileNotFoundError(f"BGM 文件不存在：{bgm_path}")


@dataclass(slots=True)
class NarrationResult:
    segment_paths: list[Path]
    segment_durations_ms: list[int]
    narration_path: Path
    total_duration_ms: int


def _noop(_message: str) -> None:
    return None


def _require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"未找到 {name}，请先安装 FFmpeg 并加入 PATH。")
    return path


def _run_command(cmd: list[str], error_prefix: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"{error_prefix}：{detail}")
    return completed


def _seconds_text(ms: int) -> str:
    return f"{max(1, int(ms)) / 1000:.3f}"


def _concat_manifest_line(path: Path) -> str:
    normalized = path.resolve().as_posix().replace("'", "\\'")
    return f"file '{normalized}'"


def probe_duration_ms(path: Path) -> int:
    ffprobe = _require_binary("ffprobe")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    completed = _run_command(cmd, f"ffprobe 读取时长失败：{path}")
    try:
        duration = float((completed.stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe 返回了无效时长：{path}") from exc
    duration_ms = int(round(duration * 1000))
    if duration_ms <= 0:
        raise RuntimeError(f"媒体时长无效：{path}")
    return duration_ms


def media_has_audio(path: Path) -> bool:
    ffprobe = _require_binary("ffprobe")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "json",
        str(path),
    ]
    completed = _run_command(cmd, f"ffprobe 读取音频流失败：{path}")
    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe 返回了无效音频流信息：{path}") from exc
    return bool(data.get("streams"))


def synthesize_narration(
    texts: list[str],
    output_dir: Path,
    options: AssemblyAudioOptions,
    progress: ProgressCallback | None = None,
) -> NarrationResult:
    progress = progress or _noop
    options.validate()
    if not options.tts_enabled:
        raise ValueError("未启用 AI 旁白，不能生成配音。")
    if not texts:
        raise ValueError("没有可配音的文案段落。")

    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("缺少 edge-tts，请先运行：pip install edge-tts") from exc

    ffmpeg = _require_binary("ffmpeg")
    _require_binary("ffprobe")
    output_dir.mkdir(parents=True, exist_ok=True)
    segment_paths: list[Path] = []
    segment_durations_ms: list[int] = []

    for idx, text in enumerate(texts):
        raw = text.strip()
        if not raw:
            raise ValueError(f"第 {idx + 1} 段文案为空，无法生成 AI 旁白。")
        audio_path = output_dir / f"Q{idx:03d}.mp3"
        progress(f"生成配音 {idx + 1}/{len(texts)}：{audio_path.name}")
        try:
            communicate = edge_tts.Communicate(raw, options.tts_voice.strip(), rate=options.tts_rate.strip())
            communicate.save_sync(str(audio_path))
        except Exception as exc:
            raise RuntimeError(f"AI 配音失败（第 {idx + 1} 段）：{exc}") from exc
        if not audio_path.exists() or audio_path.stat().st_size <= 0:
            raise RuntimeError(f"AI 配音未生成有效音频（第 {idx + 1} 段）：{audio_path}")
        duration_ms = probe_duration_ms(audio_path)
        segment_paths.append(audio_path)
        segment_durations_ms.append(duration_ms)

    manifest_path = output_dir / "narration_concat.txt"
    manifest_path.write_text("\n".join(_concat_manifest_line(path) for path in segment_paths), encoding="utf-8")
    narration_path = output_dir / "narration.m4a"
    progress("拼接旁白音频…")
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(manifest_path),
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(narration_path),
    ]
    _run_command(cmd, "拼接旁白失败")
    return NarrationResult(
        segment_paths=segment_paths,
        segment_durations_ms=segment_durations_ms,
        narration_path=narration_path,
        total_duration_ms=sum(segment_durations_ms),
    )


def finalize_audio(
    base_video_path: Path,
    output_path: Path,
    duration_ms: int,
    narration_path: Path | None,
    options: AssemblyAudioOptions,
    progress: ProgressCallback | None = None,
) -> Path:
    progress = progress or _noop
    options.validate()
    if not options.enabled:
        return base_video_path
    if duration_ms <= 0:
        raise ValueError("最终视频时长无效，无法混音。")
    if not base_video_path.exists():
        raise FileNotFoundError(f"基础视频不存在：{base_video_path}")
    if narration_path is not None and (not narration_path.exists() or not narration_path.is_file()):
        raise FileNotFoundError(f"旁白音频不存在：{narration_path}")
    if base_video_path.resolve() == output_path.resolve():
        raise ValueError("音频合成的输入视频和输出视频不能是同一个路径。")

    ffmpeg = _require_binary("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = _seconds_text(duration_ms)
    bgm_path = Path(options.bgm_path) if options.bgm_path is not None else None
    has_source_audio = media_has_audio(base_video_path)

    cmd: list[str]
    if narration_path is not None and bgm_path is not None:
        progress("混合 AI 旁白和背景音乐…")
        filter_complex = (
            f"[1:a]aresample=async=1:first_pts=0[narr];"
            f"[2:a]volume={options.bgm_volume:.3f},atrim=0:{duration},asetpts=PTS-STARTPTS[bgm];"
            "[narr][bgm]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(base_video_path),
            "-i",
            str(narration_path),
            "-stream_loop",
            "-1",
            "-i",
            str(bgm_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-t",
            duration,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    elif narration_path is not None:
        progress("写入 AI 旁白音轨…")
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(base_video_path),
            "-i",
            str(narration_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-t",
            duration,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    elif bgm_path is not None and has_source_audio:
        progress("混合原视频音频和背景音乐…")
        filter_complex = (
            "[0:a]aresample=async=1:first_pts=0[src];"
            f"[1:a]volume={options.bgm_volume:.3f},atrim=0:{duration},asetpts=PTS-STARTPTS[bgm];"
            "[src][bgm]amix=inputs=2:duration=longest:dropout_transition=0[aout]"
        )
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(base_video_path),
            "-stream_loop",
            "-1",
            "-i",
            str(bgm_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-t",
            duration,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    elif bgm_path is not None:
        progress("写入背景音乐音轨…")
        filter_complex = f"[1:a]volume={options.bgm_volume:.3f},atrim=0:{duration},asetpts=PTS-STARTPTS[aout]"
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(base_video_path),
            "-stream_loop",
            "-1",
            "-i",
            str(bgm_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-t",
            duration,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    else:
        return base_video_path

    _run_command(cmd, "最终音频合成失败")
    progress(f"音频合成完成：{output_path}")
    return output_path
