"""Validate on-disk files per episode (audio, Whisper JSON, improved JSON). Safe to re-run."""
from __future__ import annotations

from bilradio.config import DB_PATH, ensure_data_dirs
from bilradio.db import connect
from bilradio.episode_paths import (
    expected_audio_path,
    has_non_empty_json,
    improved_transcript_json_path,
    whisper_transcript_json_path,
)


def run_episode_cleanup_report() -> None:
    ensure_data_dirs()
    n_audio = n_whisper = n_improved = n_eps = 0
    with connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT guid, title, audio_path FROM episodes ORDER BY pub_date ASC"
        ).fetchall()
    for row in rows:
        n_eps += 1
        guid = row["guid"]
        title = row["title"]
        ap = row["audio_path"]
        audio = expected_audio_path(guid, title, ap)
        wj = whisper_transcript_json_path(guid, title, ap)
        ij = improved_transcript_json_path(guid, title, ap)
        if audio.is_file():
            n_audio += 1
        if has_non_empty_json(wj):
            n_whisper += 1
        if has_non_empty_json(ij):
            n_improved += 1

    print(f"Episodes in database: {n_eps}")
    print(f"With audio file on disk: {n_audio}")
    print(f"With Whisper JSON (data/transcripts/<stem>.json): {n_whisper}")
    print(f"With improved JSON (data/transcripts_improved/<stem>.json): {n_improved}")
