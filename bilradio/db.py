from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    guid TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    pub_date TEXT NOT NULL,
    enclosure_url TEXT NOT NULL,
    duration_sec INTEGER,
    audio_path TEXT,
    transcript_path TEXT,
    extract_model TEXT,
    extract_at TEXT,
    status TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS topic_bullets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_guid TEXT NOT NULL,
    text TEXT NOT NULL,
    cars TEXT NOT NULL DEFAULT '[]',
    themes TEXT NOT NULL DEFAULT '[]',
    uncertain INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (episode_guid) REFERENCES episodes(guid)
);

CREATE INDEX IF NOT EXISTS idx_bullets_episode ON topic_bullets(episode_guid);

CREATE TABLE IF NOT EXISTS topic_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_guid TEXT NOT NULL,
    title TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (episode_guid) REFERENCES episodes(guid)
);

CREATE INDEX IF NOT EXISTS idx_sections_episode ON topic_sections(episode_guid);
"""


def _migrate_schema(conn: sqlite3.Connection) -> None:
    bcols = {row[1] for row in conn.execute("PRAGMA table_info(topic_bullets)").fetchall()}
    if "section_id" not in bcols:
        conn.execute("ALTER TABLE topic_bullets ADD COLUMN section_id INTEGER")
    if "start_sec" not in bcols:
        conn.execute("ALTER TABLE topic_bullets ADD COLUMN start_sec REAL")
    if "end_sec" not in bcols:
        conn.execute("ALTER TABLE topic_bullets ADD COLUMN end_sec REAL")

    scols = {row[1] for row in conn.execute("PRAGMA table_info(topic_sections)").fetchall()}
    if "start_sec" not in scols:
        conn.execute("ALTER TABLE topic_sections ADD COLUMN start_sec REAL")
    if "end_sec" not in scols:
        conn.execute("ALTER TABLE topic_sections ADD COLUMN end_sec REAL")


@contextmanager
def connect(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        _migrate_schema(conn)
        conn.commit()
        yield conn
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        pass


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def parse_json_list(s: str | None) -> list[str]:
    if not s:
        return []
    try:
        v = json.loads(s)
        return list(v) if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []
