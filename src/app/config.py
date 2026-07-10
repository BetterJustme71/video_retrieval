from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path


def _detect_root() -> Path:
    """Return the directory that holds data/ cache/ exports/ and logs/.

    - In development mode: the git repo root (parents[2] from src/app/config.py).
    - In PyInstaller onedir mode: sys.executable's parent, NOT the _internal dir.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # We are inside the _internal/ bundle.  Use the EXE directory as root.
        return Path(sys.executable).resolve().parent
    try:
        return Path(__file__).resolve().parents[2]
    except Exception:
        return Path.cwd()


PROJECT_ROOT = _detect_root()

DEFAULT_VIDEO_DIR = Path(r"E:\影视剧集")
DEFAULT_SCRIPT_PATH = Path(r"D:\ClaudeCode_AI\闯关东\《闯关东》深度解析01：离乡不是选择，而是穷人最后的生路.md")


@dataclass(slots=True)
class AppConfig:
    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    cache_dir: Path = PROJECT_ROOT / "cache"
    exports_dir: Path = PROJECT_ROOT / "exports"
    logs_dir: Path = PROJECT_ROOT / "logs"
    db_path: Path = PROJECT_ROOT / "data" / "app.db"
    default_video_dir: Path = DEFAULT_VIDEO_DIR
    default_script_path: Path = DEFAULT_SCRIPT_PATH
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    chunk_min_ms: int = 10_000
    chunk_max_ms: int = 25_000
    chunk_overlap_segments: int = 2
    preview_padding_ms: int = 8_000
    entity_terms: list[str] = field(default_factory=lambda: [
        "朱开山", "文他娘", "鲜儿", "传文", "传武", "传杰", "朱家",
        "闯关东", "山东", "东北", "山海关", "离乡", "活路", "饥荒", "逃荒",
    ])

    def ensure_dirs(self) -> None:
        for path in [self.data_dir, self.cache_dir, self.exports_dir, self.logs_dir]:
            path.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "transcripts").mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "embeddings").mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "index").mkdir(parents=True, exist_ok=True)

        # Copy an existing database from the source project directory
        # so the packaged EXE finds the already-indexed data on first run.
        _try_copy_legacy_db(self.db_path)


def _try_copy_legacy_db(target: Path) -> None:
    if target.exists() and target.stat().st_size > 4096:
        return  # already has real data

    # Known development project root on this machine
    candidates = [
        Path(r"E:\0_AI\Claude_影视检索") / "data" / "app.db",
    ]
    # If running in PyInstaller bundle, also try parents of sys.executable.
    if getattr(sys, "frozen", False):
        exe_parent = Path(sys.executable).resolve().parent.parent
        candidates.append(exe_parent / "data" / "app.db")
        candidates.append(exe_parent / ".." / "data" / "app.db")

    import shutil
    for src in candidates:
        src = src.resolve()
        if src.exists() and src.stat().st_size > 4096:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(target))
            return


CONFIG = AppConfig()
