from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check_import(name: str) -> tuple[bool, str]:
    spec = importlib.util.find_spec(name)
    if spec is None:
        return False, f"缺少 Python 包：{name}"
    return True, f"Python 包可用：{name}"


def check_sqlite_fts() -> tuple[bool, str]:
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.close()
        return True, "SQLite FTS5 可用"
    except Exception as exc:
        return False, f"SQLite FTS5 不可用：{exc}"


def main() -> int:
    checks: list[tuple[bool, str]] = []
    checks.append((sys.version_info >= (3, 10), f"Python 版本：{sys.version.split()[0]}"))
    for binary in ["ffmpeg", "ffprobe"]:
        path = shutil.which(binary)
        checks.append((path is not None, f"{binary}: {path or '未找到'}"))
    checks.append(check_sqlite_fts())
    for package in ["PySide6", "faster_whisper", "jieba", "pydantic"]:
        checks.append(check_import(package))
    # Optional heavy packages.
    for package in ["sentence_transformers", "hnswlib"]:
        ok, msg = check_import(package)
        checks.append((True, ("可选：" + msg) if ok else ("可选：" + msg + "（MVP 可先用文本相似度 fallback）")))

    failed = False
    for ok, msg in checks:
        marker = "[OK]" if ok else "[FAIL]"
        print(f"{marker} {msg}")
        if not ok:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
