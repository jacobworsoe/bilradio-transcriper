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
    TRANSCRIPTS_DIR,
    ensure_data_dirs,
)
from bilradio.db import connect
from bilradio.download import audio_path_for_guid, download_audio
from bilradio.episode_paths import episode_stem, has_non_empty_json, whisper_transcript_json_path
from bilradio.extract import (
    BulletDocument,
    BulletSection,
    load_bullet_document_from_json_path,
    write_cursor_inbox,
)
from bilradio.transcript_text import transcript_plain_text_from_file
from bilradio.rss_feed import load_filtered_episodes
from bilradio.runtime_log import get_logger
from bilradio.whisper_run import run_whisper, transcript_path_for_audio

_queue_log = get_logger("bilradio.queue")

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


def step_ingest_transcripts(guid: str | None = None) -> tuple[int, int]:
    """
    Promote episodes to ``transcribed`` when a non-empty Whisper transcript exists under
    ``data/transcripts``: prefers ``{stem}.json``, else ``{stem}.txt`` (same stem as audio basename).

    Considers rows with ``status`` ``downloaded`` or ``error`` so external/offline Whisper runs
    can be synced into SQLite after ``bilradio download`` (or after fixing GPU errors).
    """
    ensure_data_dirs()
    ingested = 0
    skipped = 0
    with connect(DB_PATH) as conn:
        q = """
            SELECT guid, title, audio_path, status FROM episodes
            WHERE audio_path IS NOT NULL AND TRIM(audio_path) != ''
            AND status IN ('downloaded', 'error')
        """
        args: tuple = ()
        if guid:
            q += " AND guid = ?"
            args = (guid,)
        q += " ORDER BY pub_date ASC"
        rows = conn.execute(q, args).fetchall()
        for row in rows:
            ap_raw = row["audio_path"]
            audio_path = Path(str(ap_raw))
            if not audio_path.is_file():
                skipped += 1
                continue
            g = row["guid"]
            title = row["title"]
            json_path = whisper_transcript_json_path(g, title, ap_raw)
            stem = episode_stem(g, title, ap_raw)
            txt_path = TRANSCRIPTS_DIR / f"{stem}.txt"

            chosen: Path | None = None
            if has_non_empty_json(json_path):
                chosen = json_path
            elif txt_path.is_file():
                try:
                    if txt_path.stat().st_size < 1:
                        chosen = None
                except OSError:
                    chosen = None
                else:
                    try:
                        head = txt_path.read_text(encoding="utf-8", errors="replace")[:4096]
                    except OSError:
                        chosen = None
                    else:
                        chosen = txt_path if head.strip() else None

            if chosen is None:
                skipped += 1
                continue

            conn.execute(
                """
                UPDATE episodes SET status = 'transcribed', transcript_path = ?, error = NULL
                WHERE guid = ?
                """,
                (str(chosen.resolve()), g),
            )
            conn.commit()
            ingested += 1
    return ingested, skipped


def step_clear_episode_error(guid: str) -> tuple[bool, str]:
    """
    Clear a persisted failure: set ``status`` to ``downloaded`` and ``error`` to NULL so the
    episode can be transcribed again. Only applies when the row is currently ``error`` and the
    audio file still exists on disk.
    """
    ensure_data_dirs()
    with connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT guid, status, audio_path FROM episodes WHERE guid = ?", (guid,)
        ).fetchone()
        if not row:
            return False, "Episode not found."
        if row["status"] != "error":
            return False, "Episode is not in error state."
        ap_raw = row["audio_path"]
        if not ap_raw or not str(ap_raw).strip():
            return False, "Episode has no audio path; sync or download again."
        audio_path = Path(str(ap_raw))
        if not audio_path.is_file():
            return False, f"Audio file missing on disk: {audio_path}"
        conn.execute(
            "UPDATE episodes SET status = 'downloaded', error = NULL WHERE guid = ?",
            (guid,),
        )
        conn.commit()
    return True, "Error cleared; episode is Downloaded again."


def _save_bullet_document(
    conn: sqlite3.Connection,
    guid: str,
    doc: BulletDocument,
    source_label: str,
) -> None:
    conn.execute("DELETE FROM topic_bullets WHERE episode_guid = ?", (guid,))
    conn.execute("DELETE FROM topic_sections WHERE episode_guid = ?", (guid,))
    for order, sec in enumerate(doc.sections):
        cur = conn.execute(
            """
            INSERT INTO topic_sections (episode_guid, title, sort_order, start_sec, end_sec)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guid, sec.title, order, sec.start_sec, sec.end_sec),
        )
        sid = cur.lastrowid
        for b in sec.bullets:
            conn.execute(
                """
                INSERT INTO topic_bullets (
                    episode_guid, section_id, text, cars, themes, uncertain, start_sec, end_sec
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guid,
                    sid,
                    b["text"],
                    json.dumps(b["cars"], ensure_ascii=False),
                    json.dumps(b["themes"], ensure_ascii=False),
                    1 if b["uncertain"] else 0,
                    b.get("start_sec"),
                    b.get("end_sec"),
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
            transcript = transcript_plain_text_from_file(tpath)
            if not transcript.strip():
                continue
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
    doc = load_bullet_document_from_json_path(json_path)
    if doc.bullet_count == 0:
        raise ValueError("No bullets parsed from JSON (empty or invalid).")
    with connect(DB_PATH) as conn:
        row = conn.execute("SELECT guid FROM episodes WHERE guid = ?", (guid,)).fetchone()
        if not row:
            raise ValueError(f"Unknown episode guid: {guid}")
        _save_bullet_document(conn, guid, doc, source_label)


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
        text = transcript_plain_text_from_file(Path(tp))
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
        doc = BulletDocument(
            sections=(BulletSection("Episode", tuple(bullets)),),
        )
        _save_bullet_document(conn, guid, doc, "scaffold-preview")


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
            _queue_log.warning("Episode %s failed: %s", guid, e, exc_info=True)


