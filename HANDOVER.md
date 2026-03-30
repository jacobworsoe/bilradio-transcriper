# Bilradio Transcriber — Handover Document

_Last updated: 2026-03-31. Operator + maintainer notes._

---

## What was built

A local pipeline that:

1. Fetches the Bilradio podcast RSS feed (episodes from **2025-11-07** onwards; optional min duration via `BILRADIO_MIN_DURATION_SEC`).
2. Downloads MP3 audio files under `data/audio/`.
3. Transcribes with **OpenAI Whisper** (typically CUDA + **medium**).
4. Writes transcript + structured prompt to `data/cursor_inbox/` for analysis inside Cursor.
5. Imports structured JSON bullets into SQLite.
6. Serves a **FastAPI web UI** (`/` topics, `/queue` queue + status).

---

## Security / secrets

- **No secrets belong in git.** The repo uses a public RSS URL only.
- Put optional paths and flags in **`.env`** at repo root; **`.env` is gitignored** — verify with `git check-ignore -v .env`.
- **`data/`** is gitignored (SQLite, audio, transcripts): do not force-add it if it ever contains anything sensitive.

---

## Primary ops path: batch Whisper + ingest

For reliable long GPU runs, **do not rely on the web “Start full queue”** alone. Use:

```powershell
cd C:\Git\bilradio-transcriper

# Sync RSS, download pending episodes, run whisper on each data\audio\*.mp3 (medium/cuda by default)
.\.venv\Scripts\python.exe .\scripts\batch_whisper_transcribe.py

# Update SQLite: downloaded|error → transcribed when data\transcripts\<stem>.txt exists and is non-empty
.\.venv\Scripts\python.exe -m bilradio.cli ingest-transcripts
```

Options (see `python scripts\batch_whisper_transcribe.py --help`):

- `--skip-sync-download` — transcription only (no RSS/download).
- `--retry-failed` — re-run Whisper even if `.txt` exists.
- `--device cpu` — CPU inference.

Then: `bilradio prepare-extract` → Cursor → `bilradio import-bullets`.

---

## Integrated Whisper (optional)

- **`bilradio transcribe`**, **`run-queue`**, or web-launched **`pipeline`**: use `bilradio/whisper_run.py` (piped stdout in `full` mode, stall watchdog).
- **`BILRADIO_WHISPER_SUBPROCESS=simple`** in `.env`: inherit console; no pipe/stall parsing — closest to manual `whisper` inside the same app.
- **`bilradio ingest-transcripts`**: sync external/manual transcript files into DB.
- **`bilradio clear-error --guid …`**: reset an `error` row to `downloaded` and clear `error` text (queue UI).

---

## Repo layout

```
bilradio/
  config.py          # Env overrides, WHISPER_CMD / SUBPROCESS mode
  db.py              # SQLite schema + helpers
  rss_feed.py        # RSS fetch + filtering
  download.py        # MP3 download (ASCII-safe filenames)
  audio_meta.py      # Duration (mutagen)
  whisper_run.py     # Whisper subprocess (full vs simple mode)
  pipeline.py        # sync, download, transcribe, ingest, clear-error, queue
  extract.py         # Cursor inbox + bullet JSON
  cli.py             # Typer entry point
  web/               # FastAPI + templates
scripts/
  batch_whisper_transcribe.py   # RSS + download + sequential whisper CLI (recommended batch path)
  reset_episode_error.py        # Legacy: clear error by guid prefix
data/                             # gitignored — local only
```

---

## Key Whisper-related env vars

```
BILRADIO_WHISPER_PYTHON       # venv python.exe (recommended on Windows)
BILRADIO_WHISPER_MODEL        # default medium
BILRADIO_WHISPER_DEVICE       # cuda | cpu
BILRADIO_WHISPER_SUBPROCESS   # unset or full | simple
BILRADIO_WHISPER_VERBOSE       # true | false
BILRADIO_WHISPER_STALL_SEC     # full mode only
BILRADIO_WHISPER_BOOT_SILENCE_SEC
```

Run **`bilradio doctor`** for resolved paths and import check.

---

## CLI quick reference

```powershell
.\.venv\Scripts\python.exe -m bilradio.cli <command>

init              # Dirs + DB schema
sync              # RSS upsert
download          # Pending → MP3 on disk
transcribe        # Integrated Whisper (full or simple subprocess)
ingest-transcripts # .txt on disk → DB transcribed
clear-error       # error → downloaded (clear message)
prepare-extract   # cursor_inbox prompts
import-bullets    # Cursor JSON → DB
scaffold-bullets  # Rough preview bullets
run-queue         # sync + download all + integrated transcribe all
pipeline          # sync + download + transcribe + prepare-extract (per guid or all)
serve             # Web UI (8765)
doctor            # Paths + whisper import
self-test-transcribe  # Tiny CPU smoke test
```

Logs: `data/logs/bilradio.log`, integrated runs may also write `data/logs/whisper_*.log` (full mode).

---

## After transcription: extracting bullets

1. `bilradio prepare-extract --guid <guid>`
2. Open `data/cursor_inbox/<guid>_CURSOR_PROMPT.md` in Cursor
3. Save JSON as `data/cursor_inbox/<guid>.bullets.json`
4. `bilradio import-bullets --guid <guid>`

---

## Git

Remote: `https://github.com/jacobworsoe/bilradio-transcriper`  
Branch: `main`  

See `git log -1` for latest commit on this clone.

---

## Session log (high level)

| Period | Focus |
|--------|--------|
| Early | RSS, download, Whisper subprocess hardening, web queue, stall/skip detection |
| Later | Segment heartbeat UI, `ingest-transcripts`, `WHISPER_SUBPROCESS=simple`, `clear-error`, batch script |
