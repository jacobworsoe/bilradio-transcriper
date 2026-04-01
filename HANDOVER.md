# Bilradio Transcriber — Handover Document

_Last updated: 2026-04-03. Operator + maintainer notes._

---

## What was built

A local pipeline that:

1. Fetches the Bilradio podcast RSS feed (episodes from **2025-11-07** onwards; optional min duration via `BILRADIO_MIN_DURATION_SEC`).
2. Downloads MP3 audio files under `data/audio/`.
3. Transcribes with **OpenAI Whisper** (typically CUDA + **medium**) — **recommended:** batch CLI script; integrated `bilradio transcribe` / `run-queue` remain optional.
4. Keeps **Whisper output on disk** under `data/transcripts/` (`.json` preferred; `.txt` also supported).
5. **Improved** structured JSON (same shape as `CURSOR_INSTRUCTIONS` in `bilradio/extract.py`) under `data/transcripts_improved/<stem>.json` — **author with Cursor Auto Agent** (see `.cursor/rules/transcript-storage.mdc`).
6. Imports sectioned bullets into **SQLite** (`topic_sections` + `topic_bullets`) via `import-bullets`.
7. Serves a **FastAPI web UI:** **`/`** Topics (facets + sectioned bullets), **`/episodes`** Episodes status (disk + DB flags, RSS sync, ingest, clear error). **`/queue`** redirects to **`/episodes`**. **`bilradio serve`** uses **auto-reload by default** (watch `bilradio` `*.py` / `*.html`); **`--no-reload`** for a stable process; startup prints **`Web UI from …`** for the resolved package path.

---

## Security / secrets

- **No secrets belong in git.** The repo uses a public RSS URL only.
- Put optional paths and flags in **`.env`** at repo root; **`.env` is gitignored** — verify with `git check-ignore -v .env`.
- **`data/`** is gitignored (SQLite, audio, transcripts, improved JSON, cursor_inbox): do not force-add it if it ever contains anything sensitive.

---

## Primary ops path: batch Whisper + ingest

For reliable long GPU runs, use the **batch script** (not the web app for transcription):

```powershell
cd C:\Git\bilradio-transcriper

# Sync RSS, download pending episodes, run whisper on each data\audio\*.mp3 (all output formats by default)
.\.venv\Scripts\python.exe .\scripts\batch_whisper_transcribe.py

# Update SQLite: downloaded|error → transcribed when data\transcripts\<stem>.json (preferred) or .txt exists
.\.venv\Scripts\python.exe -m bilradio.cli ingest-transcripts
```

Batch script options (`python scripts\batch_whisper_transcribe.py --help`): `--skip-sync-download`, `--retry-failed`, `--device cpu`, `--output-format` (omit for Whisper default `all`).

**Web UI (`bilradio serve`):** open **`/episodes`** for **Sync RSS**, **Ingest transcripts**, and columns: Downloaded / Whisper (JSON+TXT) / Improved / Status / Bullets. **Display status** is normalized for the UI: SQLite **`extracted`** shows as **Summarized** (bullets loaded); if Whisper **JSON exists on disk** but the DB still has **`error`**, the row is shown as **transcribed** with an optional stale-error note instead of a blocking error badge. **`serve`** auto-reloads on code/template changes unless **`--no-reload`**; confirm the printed **`Web UI from …`** path if the site looks stale.

---

## Improved JSON → bullets (Cursor Auto Agent)

- **Policy:** No third-party LLM APIs for improved JSON unless explicitly decided; use **Cursor Auto Agent** (see `.cursor/rules/transcript-storage.mdc`).
- **`bilradio prepare-improved-agent`** — writes `data/cursor_inbox/<guid>_improve_auto_agent.md` with output path and full `CURSOR_INSTRUCTIONS`.
- **`bilradio bootstrap-improved-json`** — optional extractive placeholders (`_bilradio_meta.replace_with_cursor_agent: true`); replace with Agent output when ready.
- **`bilradio import-bullets --guid <guid> --file data\transcripts_improved\<stem>.json`** — loads sections/bullets into SQLite and sets episode **`extracted`**.

Legacy path still works: `prepare-extract` → `*_CURSOR_PROMPT.md` → save `<guid>.bullets.json` in `cursor_inbox` → `import-bullets`.

---

## Integrated Whisper (optional)

- **`bilradio transcribe`**, **`run-queue`**, **`pipeline`:** `bilradio/whisper_run.py` (full vs `BILRADIO_WHISPER_SUBPROCESS=simple`).
- **`bilradio ingest-transcripts`:** syncs disk transcripts into DB (`transcribed`).
- **`bilradio clear-error --guid …`** or **Episodes** page **Clear error**.

---

## Repo layout

```
bilradio/
  config.py                 # DATA_DIR, TRANSCRIPTS_DIR, TRANSCRIPTS_IMPROVED_DIR, WHISPER_*
  db.py                     # episodes, topic_sections, topic_bullets + migrations
  episode_paths.py          # stem / whisper / improved paths
  episode_cleanup.py        # Disk coverage report
  bootstrap_improved.py     # Extractive improved JSON (optional)
  prepare_improved_agent.py # Auto Agent Markdown prompts
  transcript_text.py        # Plain text from .txt or Whisper .json
  extract.py                # CURSOR_INSTRUCTIONS, bullet parse/import shape
  pipeline.py               # sync, download, transcribe, ingest, import, scaffold, …
  whisper_run.py
  web/app.py                # /api/bullets, /api/episodes, …
scripts/
  batch_whisper_transcribe.py
  episode_cleanup.py        # CLI wrapper for coverage report
.cursor/rules/
  transcript-storage.mdc    # Disk vs SQLite for the three layers
  web-restart-after-changes.mdc  # Web/API edits + serve reload behavior
  agent-run-commands.mdc    # Agents run commands; long servers in background
data/                       # gitignored
```

---

## Key env vars

**Whisper:** `BILRADIO_WHISPER_PYTHON`, `BILRADIO_WHISPER_MODEL`, `BILRADIO_WHISPER_DEVICE`, `BILRADIO_WHISPER_SUBPROCESS`, `BILRADIO_WHISPER_VERBOSE`, stall/boot settings (see `config.py`).

**Paths:** `BILRADIO_DATA_DIR`, `BILRADIO_TRANSCRIPTS_IMPROVED_DIR` (optional).

Run **`bilradio doctor`** for resolved paths and Whisper import check.

---

## CLI quick reference

```powershell
.\.venv\Scripts\python.exe -m bilradio.cli <command>

init                    # Dirs + DB schema
sync                    # RSS upsert
download                # Pending → MP3
transcribe              # Integrated Whisper
ingest-transcripts      # Whisper .json or .txt on disk → DB transcribed
clear-error             # error → downloaded
prepare-extract         # cursor_inbox transcript + CURSOR prompt (legacy flow)
prepare-improved-agent  # cursor_inbox *improve_auto_agent.md for Auto Agent
bootstrap-improved-json # Placeholder improved JSON (replace with Agent)
import-bullets          # JSON (sections/bullets) → SQLite + extracted
scaffold-bullets        # Rough preview bullets
episode-cleanup         # Count audio / whisper / improved on disk
run-queue               # sync + download all + integrated transcribe all
pipeline                # sync + download + transcribe + prepare-extract (per guid or all)
serve                   # Web UI (127.0.0.1:8765); auto-reload on by default; --no-reload off
doctor
self-test-transcribe
```

Logs: `data/logs/bilradio.log`, integrated runs may also write `data/logs/whisper_*.log` (full mode).

---

## After transcription: improved JSON and Topics

1. Ensure **`data/transcripts/<stem>.json`** (or `.txt`) exists; run **`ingest-transcripts`** if DB should show **transcribed**.
2. Generate **`data/transcripts_improved/<stem>.json`** with **Cursor Auto Agent** (`prepare-improved-agent` prompts) or bootstrap temporarily.
3. **`bilradio import-bullets --guid <guid> --file data\transcripts_improved\<stem>.json`**
4. Open **`/`** in the web app to see sectioned bullets and facets.

---

## Git

Remote: `https://github.com/jacobworsoe/bilradio-transcriper`  
Branch: `main`  

For current HEAD after pull: `git log -1 --oneline`

---

## Session log (high level)

| Period | Focus |
|--------|--------|
| Early | RSS, download, Whisper subprocess, web queue, stall/skip |
| Mid | `ingest-transcripts`, batch script, `WHISPER_SUBPROCESS=simple`, `clear-error` |
| Later | Episodes status page (disk truth for JSON), sectioned bullets + DB schema, transcript-storage rule, Cursor Auto Agent workflow (no OpenAI improver), `prepare-improved-agent` / `bootstrap-improved-json` |
| 2026-04 | Episodes **display_status** (Summarized vs extracted, stale-error override when JSON on disk), **`serve`** auto-reload + package path echo, Cursor agent rule: start **`bilradio serve` in background** so the user’s terminal stays free |
