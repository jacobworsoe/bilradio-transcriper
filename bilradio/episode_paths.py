"""Resolve per-episode filesystem paths (audio stem, Whisper JSON, improved JSON)."""
from __future__ import annotations

from pathlib import Path

from bilradio.config import TRANSCRIPTS_DIR, TRANSCRIPTS_IMPROVED_DIR
from bilradio.download import audio_path_for_guid


def expected_audio_path(guid: str, title: str, audio_path_db: str | None) -> Path:
    """Prefer on-disk path from DB when valid; else canonical path from guid + title."""
    if audio_path_db and str(audio_path_db).strip():
        p = Path(str(audio_path_db))
        if p.is_file():
            return p
    return audio_path_for_guid(guid, title)


def episode_stem(guid: str, title: str, audio_path_db: str | None) -> str:
    return expected_audio_path(guid, title, audio_path_db).stem


def whisper_transcript_json_path(guid: str, title: str, audio_path_db: str | None) -> Path:
    return TRANSCRIPTS_DIR / f"{episode_stem(guid, title, audio_path_db)}.json"


def whisper_transcript_txt_path(guid: str, title: str, audio_path_db: str | None) -> Path:
    return TRANSCRIPTS_DIR / f"{episode_stem(guid, title, audio_path_db)}.txt"


def improved_transcript_json_path(guid: str, title: str, audio_path_db: str | None) -> Path:
    return TRANSCRIPTS_IMPROVED_DIR / f"{episode_stem(guid, title, audio_path_db)}.json"


def has_non_empty_json(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def has_non_empty_txt(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False
