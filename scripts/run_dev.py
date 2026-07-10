from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    raise SystemExit(subprocess.call([sys.executable, str(ROOT / "main.py"), "gui"], cwd=str(ROOT)))
