"""Report episode file coverage (audio, Whisper JSON, improved JSON).

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe .\\scripts\\episode_cleanup.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bilradio.db import init_db  # noqa: E402
from bilradio.config import DB_PATH  # noqa: E402
from bilradio.episode_cleanup import run_episode_cleanup_report  # noqa: E402


def main() -> int:
    init_db(DB_PATH)
    run_episode_cleanup_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
