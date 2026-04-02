from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from bilradio.config import (
    CURSOR_INBOX_DIR,
    DATA_DIR,
    DB_PATH,
    LOGS_DIR,
    MIN_DURATION_SEC,
    TRANSCRIPTS_DIR,
    TRANSCRIPTS_IMPROVED_DIR,
    WHISPER_CMD,
    WHISPER_DEVICE,
    WHISPER_MODEL,
    WHISPER_SUBPROCESS_MODE,
    WHISPER_VERBOSE,
    ensure_data_dirs,
)
from bilradio.db import init_db
from bilradio.episode_cleanup import run_episode_cleanup_report
from bilradio.pipeline import (
    first_pending_guid,
    step_clear_episode_error,
    step_download,
    step_import_bullets,
    step_ingest_transcripts,
    step_prepare_cursor_inbox,
    step_run_queue,
    step_scaffold_bullets,
    step_transcribe,
    sync_episodes_from_rss,
)

app = typer.Typer(
    no_args_is_help=True,
    help="Bilradio RSS, local Whisper, Cursor-based bullets, web UI",
)


@app.command()
def doctor() -> None:
    """Print data paths, Whisper command, and verify openai-whisper imports."""
    from bilradio.whisper_run import check_whisper_import

    ensure_data_dirs()
    typer.echo(f"DATA_DIR: {DATA_DIR}")
    typer.echo(f"MIN_DURATION_SEC: {MIN_DURATION_SEC}")
    typer.echo(f"LOGS_DIR: {LOGS_DIR}")
    typer.echo(f"TRANSCRIPTS_DIR: {TRANSCRIPTS_DIR}")
    typer.echo(f"TRANSCRIPTS_IMPROVED_DIR: {TRANSCRIPTS_IMPROVED_DIR}")
    typer.echo(f"WHISPER_MODEL: {WHISPER_MODEL}")
    typer.echo(f"WHISPER_DEVICE: {WHISPER_DEVICE}")
    typer.echo(f"WHISPER_VERBOSE: {WHISPER_VERBOSE}")
    typer.echo(f"WHISPER_SUBPROCESS: {WHISPER_SUBPROCESS_MODE}")
    typer.echo(f"WHISPER_CMD: {WHISPER_CMD}")
    ok, msg = check_whisper_import()
    if ok:
        typer.echo(f"openai-whisper: OK ({msg})")
    else:
        typer.echo(f"openai-whisper: FAILED — {msg}", err=True)
        raise typer.Exit(1)


@app.command("self-test-transcribe")
def self_test_transcribe() -> None:
    """Run Whisper tiny on CPU against a short synthetic WAV (smoke test)."""
    import math
    import struct
    import tempfile
    import wave

    from bilradio.whisper_run import check_whisper_import, run_whisper

    init_db(DB_PATH)
    ok, msg = check_whisper_import()
    if not ok:
        typer.echo(
            f"openai-whisper not importable with WHISPER_CMD interpreter: {msg}",
            err=True,
        )
        typer.echo("Run `bilradio doctor` and fix BILRADIO_WHISPER_PYTHON if needed.", err=True)
        raise typer.Exit(1)

    with tempfile.TemporaryDirectory(prefix="bilradio_whisper_test_") as td:
        wav = Path(td) / "selftest.wav"
        fr = 16000
        sec = 4
        n = fr * sec
        samples = [
            max(-32767, min(32767, int(8000 * math.sin(2 * math.pi * 440 * i / fr))))
            for i in range(n)
        ]
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(fr)
            w.writeframes(struct.pack(f"{len(samples)}h", *samples))

        typer.echo(f"Wrote {sec}s test WAV; running Whisper (model=tiny, device=cpu)…")
        try:
            out = run_whisper(wav, model="tiny", device="cpu")
        except Exception as e:
            typer.echo(f"Transcription failed: {e}", err=True)
            raise typer.Exit(2)
        text = out.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            typer.echo(f"Transcript file is empty: {out}", err=True)
            raise typer.Exit(1)
        typer.echo(f"OK — transcript at {out} ({len(text)} non-whitespace chars).")


@app.command()
def init() -> None:
    """Create data directories and SQLite schema."""
    ensure_data_dirs()
    init_db(DB_PATH)
    typer.echo(f"Database ready at {DB_PATH}")


@app.command()
def sync() -> None:
    """Fetch RSS and upsert episodes (pubDate + duration filters)."""
    init_db(DB_PATH)
    n = sync_episodes_from_rss()
    typer.echo(f"RSS sync finished; upserted {n} episode row(s).")


@app.command()
def download(
    guid: Optional[str] = typer.Option(None, "--guid", help="Only this episode guid"),
) -> None:
    """Download pending episodes."""
    init_db(DB_PATH)
    step_download(guid)
    typer.echo("Download pass complete.")


@app.command()
def transcribe(
    guid: Optional[str] = typer.Option(None, "--guid", help="Only this episode guid"),
) -> None:
    """Run local Whisper on downloaded episodes."""
    init_db(DB_PATH)
    step_transcribe(guid)
    typer.echo("Transcribe pass complete.")


@app.command("clear-error")
def clear_error(
    guid: str = typer.Option(..., "--guid", help="Episode guid to reset from error → downloaded"),
) -> None:
    """Clear stored error and set status to downloaded (audio file must still exist)."""
    init_db(DB_PATH)
    ok, msg = step_clear_episode_error(guid)
    if not ok:
        typer.echo(msg, err=True)
        raise typer.Exit(1 if msg != "Episode not found." else 2)
    typer.echo(msg)


@app.command("prepare-improved-agent")
def prepare_improved_agent(
    guid: Optional[str] = typer.Option(
        None,
        "--guid",
        help="Only this episode (full guid)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Write prompt even if data/transcripts_improved/<stem>.json already exists",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        help="Max episodes to consider (pub_date ASC)",
    ),
) -> None:
    """Write Cursor Auto Agent Markdown prompts under data/cursor_inbox/ (improved JSON path inside)."""
    from bilradio.prepare_improved_agent import write_improved_agent_prompts

    init_db(DB_PATH)
    paths = write_improved_agent_prompts(guid=guid, force=force, limit=limit)
    if not paths:
        typer.echo("No prompts written (missing Whisper transcript on disk, or all improved JSON exist).")
        raise typer.Exit(1)
    typer.echo(f"Wrote {len(paths)} prompt(s). Open each in Cursor and run Auto Agent on it.")
    for p in paths[:10]:
        typer.echo(f"  {p}")
    if len(paths) > 10:
        typer.echo("  ...")


@app.command("bootstrap-improved-json")
def bootstrap_improved_json(
    guid: Optional[str] = typer.Option(
        None,
        "--guid",
        help="Only this episode (full guid)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing data/transcripts_improved/<stem>.json",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        help="Max episodes to consider (pub_date ASC)",
    ),
) -> None:
    """Extractive placeholders in transcripts_improved/; replace with Cursor Auto Agent when ready."""
    from bilradio.bootstrap_improved import write_bootstrap_improved

    init_db(DB_PATH)
    w, sk_nt, sk_ex = write_bootstrap_improved(guid=guid, force=force, limit=limit)
    typer.echo(
        f"Bootstrap done. written={w} skipped_no_transcript={sk_nt} skipped_existing={sk_ex}"
    )


@app.command("episode-cleanup")
def episode_cleanup() -> None:
    """Print counts of episodes with audio, Whisper JSON, and improved JSON on disk."""
    init_db(DB_PATH)
    run_episode_cleanup_report()


@app.command("purge-short-episodes")
def purge_short_episodes_cmd(
    seconds: Optional[int] = typer.Option(
        None,
        "--seconds",
        help="Minimum length to keep (default: BILRADIO_MIN_DURATION_SEC, or 60 if that is 0)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="List targets only; do not delete rows or files",
    ),
    no_probe: bool = typer.Option(
        False,
        "--no-probe",
        help="Do not measure MP3 duration when duration_sec is NULL",
    ),
    no_orphan_audio: bool = typer.Option(
        False,
        "--no-orphan-audio",
        help="Do not remove short MP3s under data/audio with no matching episode row",
    ),
) -> None:
    """Remove sub-threshold episodes from SQLite and delete their audio/transcripts/inbox files."""
    from bilradio.short_episode_purge import purge_short_episodes as run_purge

    init_db(DB_PATH)
    n, lines = run_purge(
        seconds,
        dry_run=dry_run,
        probe_audio_when_duration_unknown=not no_probe,
        remove_orphan_short_mp3=not no_orphan_audio,
    )
    for line in lines:
        typer.echo(line)
    if dry_run:
        typer.echo(f"Dry run: would remove {n} episode row(s).")
    else:
        typer.echo(f"Removed {n} episode row(s).")


@app.command("ingest-transcripts")
def ingest_transcripts(
    guid: Optional[str] = typer.Option(
        None,
        "--guid",
        help="Only this episode guid (prefers data/transcripts/<stem>.json, else .txt)",
    ),
) -> None:
    """Promote downloaded/error rows to transcribed when Whisper output exists on disk (JSON or .txt)."""
    init_db(DB_PATH)
    n, skipped = step_ingest_transcripts(guid)
    typer.echo(
        f"Ingest complete: {n} episode(s) updated; "
        f"{skipped} row(s) skipped (no audio file, missing transcript, or empty file)."
    )


@app.command("prepare-extract")
def prepare_extract(
    guid: Optional[str] = typer.Option(
        None,
        "--guid",
        help="Only this episode; default = all with a transcript",
    ),
) -> None:
    """Write transcript + instructions to data/cursor_inbox for analysis in Cursor."""
    init_db(DB_PATH)
    paths = step_prepare_cursor_inbox(guid)
    if not paths:
        typer.echo("No episodes with transcripts to export.")
        raise typer.Exit(1)
    typer.echo(f"Wrote {len(paths)} file(s) under {CURSOR_INBOX_DIR}")
    for p in paths[:8]:
        typer.echo(f"  {p}")
    if len(paths) > 8:
        typer.echo("  ...")


@app.command("import-bullets")
def import_bullets(
    guid: str = typer.Option(..., "--guid", help="Episode guid (see CURSOR_PROMPT.md)"),
    bullets_file: Optional[Path] = typer.Option(
        None,
        "--file",
        exists=False,
        dir_okay=False,
        help='JSON with {"sections":[...]} or legacy {"bullets":[...]}; default: cursor_inbox/<guid>.bullets.json',
    ),
) -> None:
    """Load bullets from JSON (after you save output from Cursor)."""
    init_db(DB_PATH)
    path = bullets_file or (CURSOR_INBOX_DIR / f"{guid}.bullets.json")
    if not path.is_file():
        typer.echo(f"File not found: {path}", err=True)
        raise typer.Exit(1)
    step_import_bullets(guid, path)
    typer.echo(f"Imported bullets for {guid}.")


@app.command("scaffold-bullets")
def scaffold_bullets(
    guid: str = typer.Option(..., "--guid", help="Episode guid"),
    max_bullets: int = typer.Option(28, "--max", help="Max bullets from transcript"),
) -> None:
    """Rough bullets from paragraph breaks (preview UI before Cursor JSON import)."""
    init_db(DB_PATH)
    step_scaffold_bullets(guid, max_bullets=max_bullets)
    typer.echo(f"Scaffold bullets saved for {guid} (replace with import-bullets later).")


@app.command("process-first")
def process_first(
    scaffold: bool = typer.Option(
        True,
        "--scaffold/--no-scaffold",
        help="Add rough bullets so the web UI shows content immediately",
    ),
) -> None:
    """Sync RSS, then download + transcribe the earliest pending episode (by pubDate)."""
    init_db(DB_PATH)
    sync_episodes_from_rss()
    g = first_pending_guid()
    if not g:
        typer.echo("No pending episodes (nothing to download).")
        raise typer.Exit(1)
    typer.echo(f"First pending episode (oldest pubDate): {g}")
    step_download(g)
    step_transcribe(g)
    step_prepare_cursor_inbox(g)
    if scaffold:
        try:
            step_scaffold_bullets(g)
            typer.echo("Scaffold bullets added for web UI preview.")
        except ValueError as e:
            typer.echo(f"Scaffold skipped: {e}", err=True)
    typer.echo(f"Cursor inbox: {CURSOR_INBOX_DIR}")
    typer.echo("Open the *_CURSOR_PROMPT.md file in Cursor, then run import-bullets with your JSON.")


@app.command("run-queue")
def run_queue() -> None:
    """Sync RSS, download all pending, transcribe all downloaded (one by one). Ctrl+C to stop cleanly."""
    import os
    import signal

    from bilradio.whisper_run import WHISPER_PID_FILE

    init_db(DB_PATH)

    def on_progress(i: int, total: int, title: str) -> None:
        bar = "=" * 60
        typer.echo(f"\n{bar}")
        typer.echo(f"  [{i}/{total}] Transcribing: {title}")
        typer.echo(f"{bar}\n")

    def _kill_whisper_if_running() -> None:
        try:
            pid_text = WHISPER_PID_FILE.read_text(encoding="utf-8").strip()
            pid = int(pid_text)
            os.kill(pid, signal.SIGTERM)
            typer.echo(f"[run-queue] Sent SIGTERM to Whisper PID {pid}.")
        except (OSError, ValueError):
            pass
        try:
            WHISPER_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        step_run_queue(on_progress=on_progress)
    except KeyboardInterrupt:
        typer.echo("\n[run-queue] Interrupted — stopping Whisper if running…")
        _kill_whisper_if_running()
        raise typer.Exit(130)

    typer.echo("\n[run-queue] All episodes processed.")


@app.command()
def extract(
    guid: Optional[str] = typer.Option(None, "--guid"),
) -> None:
    """Deprecated alias: use prepare-extract + Cursor + import-bullets."""
    _ = guid
    typer.echo(
        "Extraction is done in Cursor (no API key). Run:\n"
        "  bilradio prepare-extract [--guid ...]\n"
        "Then after saving JSON:\n"
        "  bilradio import-bullets --guid <guid> --file path/to/<guid>.bullets.json",
        err=True,
    )
    raise typer.Exit(1)


@app.command()
def pipeline(
    guid: Optional[str] = typer.Option(None, "--guid", help="Only this episode guid"),
) -> None:
    """Sync, download, transcribe, prepare Cursor inbox (no automatic LLM)."""
    init_db(DB_PATH)
    sync_episodes_from_rss()
    step_download(guid)
    step_transcribe(guid)
    step_prepare_cursor_inbox(guid)
    typer.echo("Pipeline done through transcription + cursor_inbox. Use Cursor + import-bullets.")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8765),
    reload: bool = typer.Option(
        True,
        "--reload/--no-reload",
        help="Watch bilradio package (Python + HTML templates) and restart on change.",
    ),
) -> None:
    """Start the web UI (topic bullets + exclusions)."""
    import bilradio
    import uvicorn

    init_db(DB_PATH)
    bilradio_pkg = Path(bilradio.__file__).resolve().parent
    typer.echo(
        f"Web UI from {bilradio_pkg}"
        + (" (auto-reload on)" if reload else " (auto-reload off)")
    )
    run_kw = {"host": host, "port": port, "reload": reload}
    if reload:
        run_kw["reload_dirs"] = [str(bilradio_pkg)]
        run_kw["reload_includes"] = ["*.py", "*.html"]
    uvicorn.run("bilradio.web.app:app", **run_kw)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
