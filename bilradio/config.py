from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_RSS_URL = (
    "https://www.omnycontent.com/d/playlist/"
    "87e48115-d0ce-4e9b-83f0-ae5b01244e0a/"
    "4b31e0f0-1b7a-4b4d-8370-b118008ddc47/"
    "5aaff00b-b6fb-47eb-b860-b118008ddc92/podcast.rss"
)

# Inclusive: episodes with pubDate >= this instant (UTC)
MIN_PUBDATE_UTC = datetime(2025, 11, 7, 12, 2, 43, tzinfo=timezone.utc)

# Set to 0 to include all episodes regardless of length.
# Set via env BILRADIO_MIN_DURATION_SEC to filter out short clips.
MIN_DURATION_SEC = int(os.environ.get("BILRADIO_MIN_DURATION_SEC", "0"))

DATA_DIR = Path(os.environ.get("BILRADIO_DATA_DIR", "data")).resolve()
AUDIO_DIR = DATA_DIR / "audio"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
CURSOR_INBOX_DIR = Path(
    os.environ.get("BILRADIO_CURSOR_INBOX_DIR", str(DATA_DIR / "cursor_inbox"))
).resolve()
DB_PATH = DATA_DIR / "bilradio.sqlite"

# Written by the web server when it launches run-queue or a single-episode pipeline.
QUEUE_RUNNER_PID_FILE = DATA_DIR / "queue_runner_pid.txt"
# Written by step_transcribe so the UI knows which episode Whisper is on right now.
WHISPER_CURRENT_GUID_FILE = DATA_DIR / "whisper_current_guid.txt"

RSS_URL = os.environ.get("BILRADIO_RSS_URL", DEFAULT_RSS_URL)

WHISPER_MODEL = os.environ.get("BILRADIO_WHISPER_MODEL", "medium")
WHISPER_DEVICE = os.environ.get("BILRADIO_WHISPER_DEVICE", "cuda")
# The Python interpreter that has openai-whisper installed.
# Auto-detected by finding the 'whisper' script on PATH and using its parent Python.
# Override with BILRADIO_WHISPER_PYTHON=C:\Python311\python.exe if auto-detection fails.
WHISPER_BIN = os.environ.get("BILRADIO_WHISPER_CMD", "whisper")  # kept for compat


def _find_whisper_python() -> list[str]:
    """Return argv prefix for 'python -m whisper' using the Python that owns the whisper script."""
    override = os.environ.get("BILRADIO_WHISPER_PYTHON", "")
    if override:
        return [override, "-m", "whisper"]
    import shutil
    whisper_script = shutil.which(WHISPER_BIN)
    if whisper_script:
        scripts_dir = Path(whisper_script).parent
        # Scripts/ lives inside the Python home; python.exe is one level up.
        python_exe = scripts_dir.parent / "python.exe"
        if python_exe.is_file():
            return [str(python_exe), "-m", "whisper"]
        # On Linux/Mac the binary may be in bin/ next to python3
        for name in ("python3", "python"):
            py = scripts_dir / name
            if py.is_file():
                return [str(py), "-m", "whisper"]
    # Fallback: use the whisper script directly
    return [WHISPER_BIN]


WHISPER_CMD: list[str] = _find_whisper_python()

# Kill Whisper if no new stdout line for this long (after first output line).
WHISPER_STALL_SEC = int(os.environ.get("BILRADIO_WHISPER_STALL_SEC", "120"))
# Allow this long with zero stdout before first line (model load / GPU init).
WHISPER_BOOT_SILENCE_SEC = int(os.environ.get("BILRADIO_WHISPER_BOOT_SILENCE_SEC", "300"))
WHISPER_HEARTBEAT_SEC = int(os.environ.get("BILRADIO_WHISPER_HEARTBEAT_SEC", "120"))
WHISPER_MAX_RESTARTS = int(os.environ.get("BILRADIO_WHISPER_MAX_RESTARTS", "10"))

TRANSCRIPT_CHUNK_CHARS = int(os.environ.get("BILRADIO_TRANSCRIPT_CHUNK_CHARS", "45000"))


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    CURSOR_INBOX_DIR.mkdir(parents=True, exist_ok=True)
