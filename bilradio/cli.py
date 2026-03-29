from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from bilradio.config import CURSOR_INBOX_DIR, DB_PATH, ensure_data_dirs
from bilradio.db import init_db
from bilradio.pipeline import (
    first_pending_guid,
    step_download,
    step_import_bullets,
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
        help='JSON with {"bullets": [...]}; default: data/cursor_inbox/<guid>.bullets.json',
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
) -> None:
    """Start the web UI (topic bullets + exclusions)."""
    import uvicorn

    init_db(DB_PATH)
    uvicorn.run(
        "bilradio.web.app:app",
        host=host,
        port=port,
        reload=False,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
