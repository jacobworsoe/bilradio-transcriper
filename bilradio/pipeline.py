from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from bilradio.audio_meta import audio_duration_seconds
from bilradio.config import (
    CURSOR_INBOX_DIR,
    DATA_DIR,
    DB_PATH,
    MIN_DURATION_SEC,
    ensure_data_dirs,
)
from bilradio.db import connect
from bilradio.download import audio_path_for_guid, download_audio
from bilradio.extract import load_bullets_from_json_path, write_cursor_inbox
from bilradio.rss_feed import load_filtered_episodes
from bilradio.whisper_run import run_whisper

EpisodeStatus = Literal[
    "pending",
    "skipped_short",
    "downloaded",
    "transcribed",
    "extracted",
    "error",
]


def sync_episodes_from_rss() -> int:
    """Upsert episodes from RSS. Returns count of rows touched (insert or update)."""
    ensure_data_dirs()
    episodes = load_filtered_episodes()
    n = 0
    with connect(DB_PATH) as conn:
        for ep in episodes:
            cur = conn.execute("SELECT status FROM episodes WHERE guid = ?", (ep.guid,))
            row = cur.fetchone()
            pub_iso = ep.pub_date.isoformat()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO episodes (
                        guid, title, pub_date, enclosure_url, duration_sec,
                        audio_path, transcript_path, extract_model, extract_at,
                        status, error
                    ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 'pending', NULL)
                    """,
                    (ep.guid, ep.title, pub_iso, ep.enclosure_url, ep.duration_sec),
                )
                n += 1
            else:
                conn.execute(
                    """
                    UPDATE episodes SET
                        title = ?,
                        pub_date = ?,
                        enclosure_url = ?,
                        duration_sec = COALESCE(?, duration_sec)
                    WHERE guid = ?
                    """,
                    (ep.title, pub_iso, ep.enclosure_url, ep.duration_sec, ep.guid),
                )
                n += 1
        conn.commit()
    return n


def _too_short_duration(sec: int | None) -> bool:
    if sec is None:
        return False
    return sec < MIN_DURATION_SEC


def step_download(guid: str | None = None) -> None:
    ensure_data_dirs()
    with connect(DB_PATH) as conn:
        q = "SELECT * FROM episodes WHERE status = 'pending'"
        args: tuple = ()
        if guid:
            q += " AND guid = ?"
            args = (guid,)
        rows = conn.execute(q, args).fetchall()
        for row in rows:
            g = row["guid"]
            title = row["title"]
            url = row["enclosure_url"]
            path = audio_path_for_guid(g, title)
            try:
                download_audio(url, path)
                dur = audio_duration_seconds(path)
                if row["duration_sec"] is None and dur is not None:
                    conn.execute(
                        "UPDATE episodes SET duration_sec = ? WHERE guid = ?",
                        (dur, g),
                    )
                effective = dur if dur is not None else row["duration_sec"]
                if _too_short_duration(effective):
                    conn.execute(
                        """
                        UPDATE episodes SET status = 'skipped_short', audio_path = ?, error = NULL
                        WHERE guid = ?
                        """,
                        (str(path), g),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE episodes SET status = 'downloaded', audio_path = ?, error = NULL
                        WHERE guid = ?
                        """,
                        (str(path), g),
                    )
                conn.commit()
            except Exception as e:
                conn.execute(
                    "UPDATE episodes SET status = 'error', error = ? WHERE guid = ?",
                    (str(e), g),
                )
                conn.commit()


def step_transcribe(guid: str | None = None) -> None:
    ensure_data_dirs()
    with connect(DB_PATH) as conn:
        q = "SELECT * FROM episodes WHERE status = 'downloaded'"
        args: tuple = ()
        if guid:
            q += " AND guid = ?"
            args = (guid,)
        rows = conn.execute(q, args).fetchall()
        for row in rows:
            g = row["guid"]
            ap = row["audio_path"]
            if not ap:
                continue
            audio_path = Path(ap)
            try:
                DATA_DIR.joinpath("whisper_current_guid.txt").write_text(g, encoding="utf-8")
            except OSError:
                pass
            try:
                txt_path = run_whisper(audio_path)
                conn.execute(
                    """
                    UPDATE episodes SET status = 'transcribed', transcript_path = ?, error = NULL
                    WHERE guid = ?
                    """,
                    (str(txt_path), g),
                )
                conn.commit()
            except Exception as e:
                conn.execute(
                    "UPDATE episodes SET status = 'error', error = ? WHERE guid = ?",
                    (str(e), g),
                )
                conn.commit()
            finally:
                try:
                    DATA_DIR.joinpath("whisper_current_guid.txt").unlink(missing_ok=True)
                except OSError:
                    pass


def _save_bullets_for_episode(
    conn: sqlite3.Connection,
    guid: str,
    bullets: list[dict],
    source_label: str,
) -> None:
    conn.execute("DELETE FROM topic_bullets WHERE episode_guid = ?", (guid,))
    for b in bullets:
        conn.execute(
            """
            INSERT INTO topic_bullets (episode_guid, text, cars, themes, uncertain)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                guid,
                b["text"],
                json.dumps(b["cars"], ensure_ascii=False),
                json.dumps(b["themes"], ensure_ascii=False),
                1 if b["uncertain"] else 0,
            ),
        )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE episodes SET status = 'extracted', extract_model = ?, extract_at = ?, error = NULL
        WHERE guid = ?
        """,
        (source_label, now, guid),
    )
    conn.commit()


def step_prepare_cursor_inbox(guid: str | None = None) -> list[Path]:
    """Write transcript + Cursor prompt under data/cursor_inbox/. Returns paths created."""
    ensure_data_dirs()
    written: list[Path] = []
    with connect(DB_PATH) as conn:
        q = (
            "SELECT * FROM episodes WHERE transcript_path IS NOT NULL "
            "AND status IN ('transcribed', 'extracted')"
        )
        args: tuple = ()
        if guid:
            q += " AND guid = ?"
            args = (guid,)
        rows = conn.execute(q, args).fetchall()
        for row in rows:
            g = row["guid"]
            tp = row["transcript_path"]
            if not tp:
                continue
            tpath = Path(tp)
            transcript = tpath.read_text(encoding="utf-8", errors="replace")
            p = write_cursor_inbox(g, row["title"], transcript, inbox_dir=CURSOR_INBOX_DIR)
            written.append(p)
            # Chunk sidecar files may also exist
            for f in sorted(CURSOR_INBOX_DIR.glob(f"{g}_chunk_*.txt")):
                if f not in written:
                    written.append(f)
            if CURSOR_INBOX_DIR / f"{g}_transcript.txt" not in written:
                written.append(CURSOR_INBOX_DIR / f"{g}_transcript.txt")
    return written


def step_import_bullets(guid: str, json_path: Path, source_label: str = "cursor-json") -> None:
    bullets = load_bullets_from_json_path(json_path)
    if not bullets:
        raise ValueError("No bullets parsed from JSON (empty or invalid).")
    with connect(DB_PATH) as conn:
        row = conn.execute("SELECT guid FROM episodes WHERE guid = ?", (guid,)).fetchone()
        if not row:
            raise ValueError(f"Unknown episode guid: {guid}")
        _save_bullets_for_episode(conn, guid, bullets, source_label)


def step_scaffold_bullets(guid: str, max_bullets: int = 28) -> None:
    """
    Quick UI preview: one bullet per transcript paragraph (Whisper output).
    Replace later with `prepare-extract` + Cursor + `import-bullets`.
    """
    import re

    ensure_data_dirs()
    with connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT * FROM episodes WHERE guid = ?
            AND status IN ('transcribed', 'extracted')
            AND transcript_path IS NOT NULL
            """,
            (guid,),
        ).fetchone()
        if not row:
            raise ValueError(f"No episode with transcript for guid {guid}")
        tp = row["transcript_path"]
        if not tp:
            raise ValueError("Episode has no transcript_path")
        text = Path(tp).read_text(encoding="utf-8", errors="replace")
        parts = re.split(r"\n\s*\n+", text.strip())
        bullets: list[dict] = []
        for p in parts:
            p = " ".join(p.split())
            if len(p) < 50:
                continue
            bullets.append(
                {
                    "text": p[:500] + ("…" if len(p) > 500 else ""),
                    "cars": [],
                    "themes": ["forhåndsvisning"],
                    "uncertain": True,
                }
            )
            if len(bullets) >= max_bullets:
                break
        if not bullets:
            bullets.append(
                {
                    "text": text[:400] + ("…" if len(text) > 400 else ""),
                    "cars": [],
                    "themes": ["forhåndsvisning"],
                    "uncertain": True,
                }
            )
        _save_bullets_for_episode(conn, guid, bullets, "scaffold-preview")


def first_pending_guid() -> str | None:
    with connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT guid FROM episodes
            WHERE status = 'pending'
            ORDER BY pub_date ASC
            LIMIT 1
            """
        ).fetchone()
        return str(row["guid"]) if row else None


def _downloaded_guids_ordered() -> list[tuple[str, str]]:
    """Return [(guid, title)] for all 'downloaded' episodes, oldest first."""
    with connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT guid, title FROM episodes
            WHERE status = 'downloaded'
            ORDER BY pub_date ASC
            """
        ).fetchall()
        return [(r["guid"], r["title"]) for r in rows]


def step_run_queue(
    on_progress: "Callable[[int, int, str], None] | None" = None,
) -> None:
    """
    Full sequential queue: sync → download all pending → transcribe all downloaded.

    `on_progress(current, total, title)` is called before each transcription.
    Errors on individual episodes are logged and skipped; the queue continues.
    Raises KeyboardInterrupt cleanly if the caller catches it.
    """
    from bilradio.whisper_run import WHISPER_PID_FILE

    ensure_data_dirs()
    sync_episodes_from_rss()
    step_download()

    episodes = _downloaded_guids_ordered()
    total = len(episodes)
    if not episodes:
        print("[run-queue] No downloaded episodes to transcribe.", flush=True)
        return

    for i, (guid, title) in enumerate(episodes, start=1):
        if on_progress:
            on_progress(i, total, title)
        try:
            step_transcribe(guid)
        except KeyboardInterrupt:
            # Clean up PID file and re-raise so the CLI can handle it
            try:
                WHISPER_PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        except Exception as e:
            print(f"[run-queue] Episode {guid!r} failed: {e}", flush=True)
            print(f"[run-queue] Continuing to next episode.", flush=True)


