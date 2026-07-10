from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.core.database import Database
from src.core.models import TranscriptSegment

ProgressCallback = Callable[[str], None]


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
) -> int:
    progress = progress or (lambda _message: None)
    if not force and db.transcript_exists(video_id):
        progress(f"复用已有转写：{video_path.name}")
        return len(db.load_segments(video_id))
    transcriber = Transcriber(model_size=model_size, device=device, compute_type=compute_type, progress=progress)
    segments = transcriber.transcribe_video(video_id, video_path)
    db.replace_transcript(video_id, segments)
    return len(segments)
