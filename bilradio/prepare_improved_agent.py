"""Write Markdown prompts for Cursor Auto Agent to produce data/transcripts_improved/<stem>.json."""
from __future__ import annotations

from pathlib import Path

from bilradio.config import CURSOR_INBOX_DIR, DB_PATH, ensure_data_dirs
from bilradio.db import connect
from bilradio.episode_paths import (
    episode_stem,
    has_non_empty_json,
    improved_transcript_json_path,
    whisper_transcript_json_path,
    whisper_transcript_txt_path,
)
from bilradio.extract import CURSOR_INSTRUCTIONS


def write_improved_agent_prompts(
    *,
    guid: str | None,
    force: bool,
    limit: int | None,
) -> tuple[list[Path], list[Path]]:
    """Returns (written prompt paths, removed stale prompt paths)."""
    ensure_data_dirs()
    inbox = CURSOR_INBOX_DIR
    inbox.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    removed: list[Path] = []

    with connect(DB_PATH) as conn:
        q = "SELECT guid, title, audio_path FROM episodes ORDER BY pub_date ASC"
        args: tuple = ()
        if guid:
            q = "SELECT guid, title, audio_path FROM episodes WHERE guid = ?"
            args = (guid,)
        rows = conn.execute(q, args).fetchall()

    for idx, row in enumerate(rows):
        if limit is not None and idx >= limit:
            break
        g = row["guid"]
        title = row["title"]
        ap = row["audio_path"]
        stem = episode_stem(g, title, ap)
        out_json = improved_transcript_json_path(g, title, ap)
        if out_json.exists() and not force:
            stale = inbox / f"{g}_improve_auto_agent.md"
            if stale.is_file():
                try:
                    stale.unlink()
                    removed.append(stale)
                except OSError:
                    pass
            continue
        wj = whisper_transcript_json_path(g, title, ap)
        wt = whisper_transcript_txt_path(g, title, ap)
        if not (has_non_empty_json(wj) or (wt.is_file() and wt.stat().st_size > 0)):
            continue

        whisper_ref = wj if has_non_empty_json(wj) else wt
        path_out = inbox / f"{g}_improve_auto_agent.md"
        body = f"""# Bilradio — Improved JSON via Cursor Auto Agent

Use **Cursor Auto Agent** with your license. Do **not** call external paid LLM APIs for this repo unless explicitly requested.

## Episode

- **GUID:** `{g}`
- **Title:** {title}
- **Audio/transcript stem:** `{stem}`

## Output (required)

Save **JSON only** to this exact path (create parent dirs if needed):

`{out_json.resolve()}`

Same schema as in the instructions below (sections → bullets with `text`, `cars`, `themes`, `uncertain`), **plus optional `start_sec` / `end_sec`** (float seconds from audio start) on each bullet and section — copy from the Whisper JSON **`segments[].start`** and **`segments[].end`** that support each bullet.

## Transcript input

Read plain text from Whisper:

- **Preferred:** `{whisper_ref.resolve()}`  
  (if JSON: use the top-level `text` field, or join `segments[].text`; **use `segments[].start` / `end` for timestamps** in the output JSON)

Optional helper copy (if you ran `bilradio prepare-extract`):

- `{(inbox / f"{g}_transcript.txt").resolve()}`

---

{CURSOR_INSTRUCTIONS}
"""
        path_out.write_text(body, encoding="utf-8")
        written.append(path_out)

    return written, removed
