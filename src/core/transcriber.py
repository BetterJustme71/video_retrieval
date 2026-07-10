from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.core.database import Database
from src.core.models import TranscriptSegment

ProgressCallback = Callable[[str], None]


@dataclass(slots=True)
class TranscriptResult:
    segment_count: int
    reused: bool
    fingerprint: str
    reason: str


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

    current_fingerprint = str(row["fingerprint"] or "")
    transcript_fingerprint = row["transcript_fingerprint"]
    has_transcript = db.transcript_exists(video_id)

    if not force and has_transcript and transcript_fingerprint == current_fingerprint:
        segment_count = len(db.load_segments(video_id))
        progress(f"复用已有转写：{video_path.name} / {segment_count} 段")
        return TranscriptResult(
            segment_count=segment_count,
            reused=True,
            fingerprint=current_fingerprint,
            reason="fingerprint_match",
        )

    if force:
        progress(f"强制重新转写：{video_path.name}")
        reason = "force"
    elif not has_transcript:
        progress(f"未找到已有转写，开始转写：{video_path.name}")
        reason = "missing_transcript"
    elif transcript_fingerprint is None:
        progress(f"旧库转写缺少指纹，重新转写：{video_path.name}")
        reason = "missing_fingerprint"
    else:
        progress(f"视频指纹变化，重新转写：{video_path.name}")
        reason = "fingerprint_changed"

    transcriber = Transcriber(model_size=model_size, device=device, compute_type=compute_type, progress=progress)
    segments = transcriber.transcribe_video(video_id, video_path)
    db.replace_transcript(video_id, segments)
    db.update_transcript_fingerprint(video_id, current_fingerprint)
    return TranscriptResult(
        segment_count=len(segments),
        reused=False,
        fingerprint=current_fingerprint,
        reason=reason,
    )
