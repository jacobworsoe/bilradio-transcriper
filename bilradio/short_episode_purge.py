"""Remove episodes shorter than a threshold from SQLite and related disk files."""
from __future__ import annotations

from pathlib import Path

from bilradio.audio_meta import audio_duration_seconds
from bilradio.config import (
    AUDIO_DIR,
    CURSOR_INBOX_DIR,
    DB_PATH,
    MIN_DURATION_SEC,
    TRANSCRIPTS_DIR,
    ensure_data_dirs,
)
from bilradio.db import connect
from bilradio.episode_paths import (
    episode_stem,
    expected_audio_path,
    improved_transcript_json_path,
)


def _cursor_inbox_paths_for_guid(guid: str) -> list[Path]:
    inbox = CURSOR_INBOX_DIR
    names = [
        f"{guid}_transcript.txt",
        f"{guid}_CURSOR_PROMPT.md",
        f"{guid}_improve_auto_agent.md",
        f"{guid}.bullets.json",
    ]
    out = [inbox / n for n in names if (inbox / n).is_file()]
    out.extend(sorted(inbox.glob(f"{guid}_chunk_*.txt")))
    return out


def _transcript_sidecars(stem: str) -> list[Path]:
    paths: list[Path] = []
    for suf in (".json", ".txt", ".vtt", ".tsv", ".srt"):
        p = TRANSCRIPTS_DIR / f"{stem}{suf}"
        if p.is_file():
            paths.append(p)
    return paths


def collect_paths_for_episode_row(guid: str, title: str, audio_path_db: str | None) -> list[Path]:
    paths: list[Path] = []
    audio = expected_audio_path(guid, title, audio_path_db)
    if audio.is_file():
        paths.append(audio)
    stem = episode_stem(guid, title, audio_path_db)
    paths.extend(_transcript_sidecars(stem))
    imp = improved_transcript_json_path(guid, title, audio_path_db)
    if imp.is_file():
        paths.append(imp)
    paths.extend(_cursor_inbox_paths_for_guid(guid))
    return paths


def _row_is_short(
    row: dict,
    threshold_sec: int,
    *,
    probe_audio_when_duration_unknown: bool,
) -> tuple[bool, str]:
    if row["status"] == "skipped_short":
        return True, "status=skipped_short"
    ds = row["duration_sec"]
    if ds is not None and ds < threshold_sec:
        return True, f"duration_sec={ds}"
    if probe_audio_when_duration_unknown and ds is None:
        ap_raw = row["audio_path"]
        if ap_raw and str(ap_raw).strip():
            ap = Path(str(ap_raw))
            if ap.is_file():
                d = audio_duration_seconds(ap)
                if d is not None and d < threshold_sec:
                    return True, f"audio_probe_sec={d}"
    return False, ""


def _referenced_audio_paths(conn) -> set[Path]:
    refs: set[Path] = set()
    for row in conn.execute("SELECT guid, title, audio_path FROM episodes").fetchall():
        refs.add(expected_audio_path(row["guid"], row["title"], row["audio_path"]).resolve())
    return refs


def purge_short_episodes(
    threshold_sec: int | None = None,
    *,
    dry_run: bool = False,
    probe_audio_when_duration_unknown: bool = True,
    remove_orphan_short_mp3: bool = True,
) -> tuple[int, list[str]]:
    """
    Delete episodes under ``threshold_sec`` from SQLite (and topic bullets/sections),
    remove related files, then optionally delete short MP3s under ``data/audio`` that
    no longer match any episode row.

    Returns (number of episodes removed, log lines).
    """
    ensure_data_dirs()
    eff = threshold_sec if threshold_sec is not None else (MIN_DURATION_SEC if MIN_DURATION_SEC > 0 else 60)
    if eff <= 0:
        raise ValueError("threshold_sec must be positive")

    log: list[str] = []
    removed_guids: list[str] = []

    with connect(DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM episodes").fetchall()
        to_delete: list[tuple[str, str, str | None, str]] = []
        for row in rows:
            short, why = _row_is_short(
                dict(row),
                eff,
                probe_audio_when_duration_unknown=probe_audio_when_duration_unknown,
            )
            if short:
                to_delete.append((row["guid"], row["title"], row["audio_path"], why))

        for guid, title, ap_db, why in to_delete:
            paths = collect_paths_for_episode_row(guid, title, ap_db)
            log.append(f"episode {guid[:8]}... {why} - {len(paths)} file(s)")
            if not dry_run:
                for p in paths:
                    try:
                        p.unlink()
                    except OSError as e:
                        log.append(f"  unlink failed {p}: {e}")
                conn.execute("DELETE FROM topic_bullets WHERE episode_guid = ?", (guid,))
                conn.execute("DELETE FROM topic_sections WHERE episode_guid = ?", (guid,))
                conn.execute("DELETE FROM episodes WHERE guid = ?", (guid,))
            removed_guids.append(guid)

        if not dry_run and to_delete:
            conn.commit()

    n_orphan_mp3 = 0
    if remove_orphan_short_mp3 and AUDIO_DIR.is_dir():
        with connect(DB_PATH) as conn:
            refs = _referenced_audio_paths(conn)
        for mp3 in sorted(AUDIO_DIR.glob("*.mp3")):
            try:
                rp = mp3.resolve()
            except OSError:
                continue
            if rp in refs:
                continue
            dur = audio_duration_seconds(mp3)
            if dur is None or dur >= eff:
                continue
            stem = mp3.stem
            extras = _transcript_sidecars(stem)
            log.append(f"orphan short audio {mp3.name} ({dur}s) + {len(extras)} transcript sidecar(s)")
            if not dry_run:
                try:
                    mp3.unlink()
                except OSError as e:
                    log.append(f"  unlink failed {mp3}: {e}")
                for p in extras:
                    try:
                        p.unlink()
                    except OSError as e:
                        log.append(f"  unlink failed {p}: {e}")
            n_orphan_mp3 += 1

    log.insert(
        0,
        f"threshold={eff}s dry_run={dry_run} episodes_removed={len(removed_guids)} "
        f"orphan_short_mp3={n_orphan_mp3}",
    )
    return len(removed_guids), log
