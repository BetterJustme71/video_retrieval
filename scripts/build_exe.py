from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "视频片段检索工具.spec"
OUTPUT_EXE = ROOT / "dist" / "视频片段检索工具" / "视频片段检索工具.exe"


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        str(SPEC_PATH),
    ]
    print("运行：", " ".join(cmd))
    print("说明：FFmpeg/ffprobe 不会被打包，目标机器仍需确保它们在 PATH 中可用。")
    code = subprocess.call(cmd, cwd=str(ROOT))
    if code == 0:
        print(f"打包完成：{OUTPUT_EXE}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
