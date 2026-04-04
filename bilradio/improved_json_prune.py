"""Detect placeholder / extractive improved JSON and optionally remove + clear SQLite topics."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bilradio.config import DB_PATH, TRANSCRIPTS_IMPROVED_DIR, ensure_data_dirs
from bilradio.db import connect
from bilradio.episode_paths import improved_transcript_json_path

# Real summaries use short bullets; segment/bootstrap caps near 320–420; transcript dumps go higher.
_MAX_BULLET_CHARS = 520
_TOO_MANY_BULLETS = 80


def classify_improved_json(data: dict[str, Any]) -> tuple[bool, str]:
    """
    Returns (should_remove, reason). True means not a real Cursor summary — bootstrap,
    segment-chunk placeholder, or likely raw transcript as bullets.
    """
    meta = data.get("_bilradio_meta")
    if isinstance(meta, dict) and meta.get("replace_with_cursor_agent") is True:
        return True, "replace_with_cursor_agent (_bilradio_meta)"

    sections = data.get("sections")
    if not isinstance(sections, list):
        return True, "missing_or_invalid_sections"

    texts: list[str] = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        bullets = sec.get("bullets")
        if not isinstance(bullets, list):
            continue
        for b in bullets:
            if isinstance(b, dict):
                t = str(b.get("text", "")).strip()
                if t:
                    texts.append(t)

    if not texts:
        return True, "no_bullets"

    n = len(texts)
    longest = max(len(t) for t in texts)
    if longest > _MAX_BULLET_CHARS:
        return True, f"bullet_too_long (max {longest} chars > {_MAX_BULLET_CHARS})"

    if n >= _TOO_MANY_BULLETS:
        return True, f"too_many_bullets ({n} >= {_TOO_MANY_BULLETS})"

    return False, "ok"


def _guid_for_improved_path(conn: Any, path: Path) -> str | None:
    path = path.resolve()
    rows = conn.execute("SELECT guid, title, audio_path FROM episodes").fetchall()
    for row in rows:
        p = improved_transcript_json_path(row["guid"], row["title"], row["audio_path"])
        try:
            if p.resolve() == path:
                return row["guid"]
        except OSError:
            continue
    return None


def _guid_from_file(path: Path, data: dict[str, Any], conn: Any) -> str | None:
    meta = data.get("_bilradio_meta")
    if isinstance(meta, dict):
        g = meta.get("episode_guid")
        if isinstance(g, str) and g.strip():
            return g.strip()
    return _guid_for_improved_path(conn, path)


def _clear_episode_topics(conn: Any, guid: str) -> None:
    conn.execute("DELETE FROM topic_bullets WHERE episode_guid = ?", (guid,))
    conn.execute("DELETE FROM topic_sections WHERE episode_guid = ?", (guid,))
    conn.execute(
        """
        UPDATE episodes
        SET status = 'transcribed', extract_model = NULL, extract_at = NULL
        WHERE guid = ?
        """,
        (guid,),
    )


def prune_placeholder_improved_json(
    *,
    dry_run: bool,
    update_db: bool,
    single_path: Path | None,
) -> tuple[list[tuple[Path, str]], list[str]]:
    """
    Returns (removed_or_would_remove as (path, reason), sqlite_guids_cleared).
    """
    ensure_data_dirs()
    root = TRANSCRIPTS_IMPROVED_DIR
    if single_path is not None:
        paths = [single_path.resolve()]
    else:
        paths = sorted(root.glob("*.json"))

    removed: list[tuple[Path, str]] = []
    guids_cleared: list[str] = []

    with connect(DB_PATH) as conn:
        for path in paths:
            if not path.is_file():
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
                data = json.loads(raw)
            except (OSError, json.JSONDecodeError):
                removed.append((path, "invalid_json"))
                if not dry_run:
                    guid = _guid_for_improved_path(conn, path)
                    path.unlink(missing_ok=True)
                    if update_db and guid:
                        _clear_episode_topics(conn, guid)
                        guids_cleared.append(guid)
                        conn.commit()
                continue

            if not isinstance(data, dict):
                removed.append((path, "not_a_json_object"))
                if not dry_run:
                    guid = _guid_for_improved_path(conn, path)
                    path.unlink(missing_ok=True)
                    if update_db and guid:
                        _clear_episode_topics(conn, guid)
                        guids_cleared.append(guid)
                        conn.commit()
                continue

            bad, reason = classify_improved_json(data)
            if not bad:
                continue

            removed.append((path, reason))
            if dry_run:
                continue

            guid = _guid_from_file(path, data, conn)
            path.unlink(missing_ok=True)
            if update_db and guid:
                _clear_episode_topics(conn, guid)
                guids_cleared.append(guid)
                conn.commit()

    return removed, guids_cleared
