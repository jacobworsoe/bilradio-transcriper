from __future__ import annotations

from pathlib import Path


def audio_duration_seconds(path: Path) -> int | None:
    """Return duration from MP3 metadata; None if unknown."""
    try:
        from mutagen.mp3 import MP3

        info = MP3(path).info
        if info.length is None:
            return None
        return int(round(info.length))
    except Exception:
        return None
