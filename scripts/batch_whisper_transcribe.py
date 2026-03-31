"""Batch transcribe all downloaded audio with Whisper CLI.

Usage (from repo root):
  .\.venv\Scripts\python.exe .\scripts\batch_whisper_transcribe.py
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from bilradio.config import DB_PATH
from bilradio.db import init_db
from bilradio.pipeline import step_download, sync_episodes_from_rss


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Queue all data/audio/*.mp3 through Whisper (one-by-one)."
    )
    p.add_argument("--model", default="medium", help="Whisper model (default: medium)")
    p.add_argument("--device", default="cuda", help="Whisper device (default: cuda)")
    p.add_argument(
        "--language",
        default="da",
        help="Whisper language code (default: da)",
    )
    p.add_argument(
        "--output-format",
        default=None,
        choices=["txt", "json", "all", "srt", "tsv", "vtt"],
        help=(
            "Whisper output format. "
            "If omitted, --output_format is not passed and the whisper CLI defaults to all formats."
        ),
    )
    p.add_argument(
        "--whisper-cmd",
        default="whisper",
        help="Whisper executable or command prefix first token (default: whisper)",
    )
    p.add_argument(
        "--retry-failed",
        action="store_true",
        help="Also run files that already have .txt output",
    )
    p.add_argument(
        "--skip-sync-download",
        action="store_true",
        help="Skip RSS sync + download pass before transcription queue",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    repo = Path(__file__).resolve().parents[1]
    audio_dir = repo / "data" / "audio"
    transcripts_dir = repo / "data" / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_sync_download:
        print("Syncing RSS and downloading any new pending episodes...")
        init_db(DB_PATH)
        touched = sync_episodes_from_rss()
        print(f"  RSS upserted rows: {touched}")
        step_download()

    files = sorted(audio_dir.glob("*.mp3"))
    if not files:
        print(f"No .mp3 files found in {audio_dir}")
        return 1

    done = 0
    skipped = 0
    failed = 0

    for i, mp3 in enumerate(files, start=1):
        txt = transcripts_dir / f"{mp3.stem}.txt"
        if txt.exists() and not args.retry_failed:
            skipped += 1
            print(f"[{i}/{len(files)}] skip (exists): {txt.name}")
            continue

        cmd = [
            args.whisper_cmd,
            str(mp3),
            "--model",
            args.model,
            "--language",
            args.language,
            "--device",
            args.device,
            "--temperature",
            "0",
            "--condition_on_previous_text",
            "True",
            "--output_dir",
            str(transcripts_dir),
        ]
        if args.output_format is not None:
            cmd.extend(["--output_format", args.output_format])

        print(f"[{i}/{len(files)}] transcribing: {mp3.name}")
        rc = subprocess.run(cmd, cwd=str(repo)).returncode
        if rc == 0:
            done += 1
        else:
            failed += 1
            print(f"  failed rc={rc}: {mp3.name}")

    print(
        f"Finished. success={done} skipped={skipped} failed={failed} total={len(files)}"
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
