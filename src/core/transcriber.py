from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.core.database import Database
from src.core.models import TranscriptSegment
from src.core.transcript_providers import (
    TranscriptCandidate,
    TranscriptLoadResult,
    asr_candidate,
    choose_embedded_candidate,
    choose_sidecar_candidate,
    load_subtitle_candidate,
    transcript_segments_fingerprint,
)

ProgressCallback = Callable[[str], None]


@dataclass(slots=True)
class TranscriptResult:
    segment_count: int
    reused: bool
    fingerprint: str
    reason: str
    source: str
    source_ref: str
    source_fingerprint: str
    transcript_fingerprint: str


class Transcriber:
    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        progress: ProgressCallback | None = None,
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.progress = progress or (lambda _message: None)
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel  # type: ignore
            except Exception as exc:  # pragma: no cover - depends on optional package
                raise RuntimeError(
                    "未安装 faster-whisper。请先运行：pip install -r requirements.txt"
                ) from exc
            self.progress(f"加载 Whisper 模型：{self.model_size} ({self.device}/{self.compute_type})")
            self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        return self._model

    def transcribe_video(self, video_id: int, video_path: Path) -> list[TranscriptSegment]:
        model = self._load_model()
        self.progress(f"开始转写：{video_path.name}")
        segments_iter, info = model.transcribe(str(video_path), language="zh", beam_size=5, vad_filter=True)
        self.progress(f"识别语言：{getattr(info, 'language', 'unknown')}，时长：{getattr(info, 'duration', 0):.1f}s")
        segments: list[TranscriptSegment] = []
        for i, seg in enumerate(segments_iter, start=1):
            text = (seg.text or "").strip()
            if not text:
                continue
            item = TranscriptSegment(
                video_id=video_id,
                start_ms=int(float(seg.start) * 1000),
                end_ms=int(float(seg.end) * 1000),
                text=text,
            )
            segments.append(item)
            if i % 20 == 0:
                self.progress(f"已转写 {i} 段，当前 {seg.end:.1f}s")
        self.progress(f"转写完成：{len(segments)} 段")
        return segments


def ensure_transcript(
    db: Database,
    video_id: int,
    video_path: Path,
    model_size: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> TranscriptResult:
    progress = progress or (lambda _message: None)
    row = db.get_video(video_id)
    if row is None:
        raise RuntimeError(f"视频记录不存在：{video_id}")

    candidates = _build_candidates(row, video_path, model_size, device, compute_type)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return _ensure_candidate_transcript(
                db,
                video_id,
                video_path,
                candidate,
                model_size,
                device,
                compute_type,
                force=force,
                progress=progress,
            )
        except Exception as exc:
            last_error = exc
            if candidate.kind == "asr":
                raise
            progress(f"{candidate.display_name} 不可用，继续尝试下一来源：{exc}")
    if last_error:
        raise RuntimeError(f"无法生成字幕/转写：{last_error}") from last_error
    raise RuntimeError("无法生成字幕/转写：没有可用来源")


def _build_candidates(row, video_path: Path, model_size: str, device: str, compute_type: str) -> list[TranscriptCandidate]:
    video_fingerprint = str(row["fingerprint"] or "")
    candidates: list[TranscriptCandidate] = []
    sidecar = choose_sidecar_candidate(video_path, video_fingerprint)
    if sidecar is not None:
        candidates.append(sidecar)
    embedded = choose_embedded_candidate(row)
    if embedded is not None:
        candidates.append(embedded)
    candidates.append(asr_candidate(row, model_size, device, compute_type))
    candidates.sort(key=lambda item: item.priority)
    return candidates


def _ensure_candidate_transcript(
    db: Database,
    video_id: int,
    video_path: Path,
    candidate: TranscriptCandidate,
    model_size: str,
    device: str,
    compute_type: str,
    force: bool,
    progress: ProgressCallback,
) -> TranscriptResult:
    row = db.get_video(video_id)
    if row is None:
        raise RuntimeError(f"视频记录不存在：{video_id}")
    has_transcript = db.transcript_exists(video_id)
    current_transcript_fingerprint = row["transcript_fingerprint"]
    source = row["transcript_source"]
    source_fingerprint = row["transcript_source_fingerprint"]

    if (
        not force
        and has_transcript
        and source == candidate.kind
        and source_fingerprint == candidate.source_fingerprint
        and current_transcript_fingerprint
    ):
        segment_count = len(db.load_segments(video_id))
        progress(f"复用已有字幕/转写：{candidate.display_name} / {segment_count} 段")
        return TranscriptResult(
            segment_count=segment_count,
            reused=True,
            fingerprint=str(current_transcript_fingerprint),
            reason="source_fingerprint_match",
            source=candidate.kind,
            source_ref=candidate.source_ref,
            source_fingerprint=candidate.source_fingerprint,
            transcript_fingerprint=str(current_transcript_fingerprint),
        )

    if force:
        reason = "force"
    elif not has_transcript:
        reason = "missing_transcript"
    elif source is None or source_fingerprint is None:
        reason = "missing_source_metadata"
    elif source != candidate.kind:
        reason = "source_changed"
    else:
        reason = "source_fingerprint_changed"
    progress(f"生成字幕/转写：{candidate.display_name} / {reason}")

    load_result = _load_candidate(video_id, video_path, candidate, model_size, device, compute_type, progress)
    db.replace_transcript(video_id, load_result.segments)
    db.update_transcript_metadata(
        video_id,
        load_result.source,
        load_result.source_ref,
        load_result.source_fingerprint,
        load_result.transcript_fingerprint,
    )
    return TranscriptResult(
        segment_count=len(load_result.segments),
        reused=False,
        fingerprint=load_result.transcript_fingerprint,
        reason=reason,
        source=load_result.source,
        source_ref=load_result.source_ref,
        source_fingerprint=load_result.source_fingerprint,
        transcript_fingerprint=load_result.transcript_fingerprint,
    )


def _load_candidate(
    video_id: int,
    video_path: Path,
    candidate: TranscriptCandidate,
    model_size: str,
    device: str,
    compute_type: str,
    progress: ProgressCallback,
) -> TranscriptLoadResult:
    if candidate.kind in {"sidecar", "embedded"}:
        return load_subtitle_candidate(video_id, video_path, candidate)
    if candidate.kind == "asr":
        transcriber = Transcriber(model_size=model_size, device=device, compute_type=compute_type, progress=progress)
        segments = transcriber.transcribe_video(video_id, video_path)
        if not segments:
            raise RuntimeError(f"ASR 未生成有效文本：{video_path.name}")
        return TranscriptLoadResult(
            segments=segments,
            source=candidate.kind,
            source_ref=candidate.source_ref,
            source_fingerprint=candidate.source_fingerprint,
            transcript_fingerprint=transcript_segments_fingerprint(segments),
        )
    raise RuntimeError(f"未知 transcript 来源：{candidate.kind}")
