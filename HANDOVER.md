# Bilradio Transcriber — Handover Document

_Last updated: 2026-03-30 (evening). Covers sessions [1](08dd503a-6a2a-4b6d-a2d7-a266f1a90b26) and [2](08dd503a-6a2a-4b6d-a2d7-a266f1a90b26)._

---

## What was built

A local pipeline that:
1. Fetches the Bilradio podcast RSS feed (episodes from **2025-11-07** onwards, all durations).
2. Downloads MP3 audio files.
3. Transcribes them locally with **OpenAI Whisper** (CUDA, medium model).
4. Writes transcript + structured prompt to `data/cursor_inbox/` for analysis inside Cursor.
5. Imports structured JSON bullets into SQLite.
6. Serves a **FastAPI web UI** with two pages:
   - `/` — Topic bullets browser with car/theme exclusion filters.
   - `/queue` — Transcription queue manager with start/stop controls.

---

## Current state (end of session 2, evening)

| Status | Count | Notes |
|---|---|---|
| `downloaded` | 22 | Episodes 317-338, all MP3s on disk, ready to transcribe |
| `extracted` | 1 | 24-second test clip (scaffold bullets in DB) |
| **Total** | **23** | — |

**All 22 full episodes are downloaded and waiting for transcription. Nothing is currently running. All processes have been killed and PID files cleared.**

---

## Whisper status — confirmed working

### What was confirmed this session

**Whisper IS working.** After starting the queue, Task Manager showed:
- 3 Python processes each using ~5 GB RAM
- GPU at **90–92% load** (CUDA transcription active)
- Processes were using the GPU for an extended period

This confirms all the fixes from the previous session solved the core problem. The pipeline was genuinely transcribing but was killed manually before any episode finished.

### History of fixes (for reference)

1. **`whisper.exe` binary → `python.exe -m whisper`**: The `.exe` wrapper crashes when stdout is piped. Now auto-detected via `bilradio/config.py:_find_whisper_python()`.
2. **Relative → absolute audio path**: `_build_cmd()` now calls `audio_path.resolve()`.
3. **`-u` + `PYTHONUNBUFFERED=1`**: Added to the Whisper subprocess to prevent block-buffering.
4. **Skipping detection**: Reader thread now detects `"Skipping … due to"` in stdout and raises `RuntimeError` rather than silently returning.
5. **Silent exit 0 detection**: If `_run_whisper_once` completes but `.txt` is missing, `FileNotFoundError` is raised.
6. **ASCII-only filenames**: `safe_filename_part()` uses `c.isascii()` — Danish chars (`ø`, `å`, `æ`) become `_` to avoid ffmpeg path failures on Windows.

### What to do next session

**Just let it run.** Start the queue and leave it overnight — each ~1-hour episode takes roughly 1 hour to transcribe with the medium CUDA model. All 22 episodes will take ~22 hours.

```powershell
# Start web server
.\.venv\Scripts\python.exe -m bilradio.cli serve

# Then open http://127.0.0.1:8765/queue → click "▶ Start full queue"
```

The first transcript lines appear only after Whisper has loaded the model and decoded the full audio (~5–10 minutes for a 64-min episode). The web UI heartbeat and `/queue` page will show progress. **Do not kill the process — it is working even when silent.**

### If something goes wrong

If an episode errors (not just slow), try:

```powershell
# Test with CPU to rule out GPU-specific issues
$env:BILRADIO_WHISPER_DEVICE = "cpu"
.\.venv\Scripts\python.exe -m bilradio.cli transcribe --guid <guid>
```

Or switch to `faster-whisper` (more memory-efficient, handles long files better):
```powershell
C:\Python311\pip.exe install faster-whisper
# Then update whisper_run.py to use faster-whisper API instead of CLI subprocess
```

---

## Repo layout

```
bilradio/
  config.py          # Constants, env-var overrides, WHISPER_CMD auto-detect
  db.py              # SQLite schema + helpers
  rss_feed.py        # RSS fetch + filtering
  download.py        # MP3 download (ASCII-only filenames)
  audio_meta.py      # Duration extraction (mutagen)
  whisper_run.py     # Whisper subprocess with stall detection + skip detection
  pipeline.py        # Orchestration (sync, download, transcribe, queue runner)
  extract.py         # Cursor-inbox writer + bullet JSON parser
  cli.py             # Typer CLI entry point
  web/
    app.py           # FastAPI routes (topics + queue API with start/stop)
    templates/
      index.html     # Topics page (English UI)
      queue.html     # Queue page (English UI, start/stop buttons)
    static/
      style.css
.cursor/rules/
  agent-run-commands.mdc   # Agent runs all commands itself
  ui-language.mdc          # UI = English, podcast content = Danish
data/                      # gitignored
  bilradio.sqlite          # All episode state
  audio/                   # 22 MP3 files (episodes 317-338, all ~50MB)
  transcripts/             # Only the test clip transcript so far
  cursor_inbox/            # Cursor analysis files (empty until transcription works)
  whisper_pid.txt          # (cleared) PID of active Whisper subprocess
  queue_runner_pid.txt     # (cleared) PID of queue runner
```

---

## Key Whisper configuration

```
WHISPER_CMD   = ['C:\Python311\python.exe', '-u', '-m', 'whisper']
WHISPER_MODEL = medium
WHISPER_DEVICE = cuda
BILRADIO_WHISPER_STALL_SEC = 120  (kill if no stdout for 2 min, after boot grace)
BILRADIO_WHISPER_BOOT_SILENCE_SEC = 300  (5 min grace before stall detection)
```

Auto-detected at startup via `_find_whisper_python()` in `config.py`. Override with env var `BILRADIO_WHISPER_PYTHON=C:\Python311\python.exe`.

---

## Setup (start of next session)

```powershell
cd C:\Git\bilradio-transcriper

# Start the web server
.\.venv\Scripts\python.exe -m bilradio.cli serve

# Open in browser
# http://127.0.0.1:8765/queue  → click "▶ Start full queue"
```

All 22 episodes are already downloaded. The queue will transcribe them one by one (~1h each, ~22h total).

---

## CLI quick reference

```powershell
# All commands use the venv Python:
.\.venv\Scripts\python.exe -m bilradio.cli <command>

init              # Create dirs + DB schema
sync              # Fetch RSS and upsert episodes
download          # Download all pending episodes (or --guid X)
transcribe        # Whisper all downloaded episodes (or --guid X)
prepare-extract   # Write cursor_inbox files for Cursor analysis
import-bullets    # Load Cursor JSON output into DB
scaffold-bullets  # Auto-bullets for quick UI preview
run-queue         # Full queue: sync → download all → transcribe all
serve             # Start web UI (default port 8765)
```

---

## After transcription: extracting bullets

Once an episode is `transcribed`, use the Cursor workflow:

1. `bilradio prepare-extract --guid <guid>` — writes `data/cursor_inbox/<guid>_CURSOR_PROMPT.md`
2. Open that `.md` file in Cursor, follow the instructions — Cursor produces JSON
3. Save the JSON as `data/cursor_inbox/<guid>.bullets.json`
4. `bilradio import-bullets --guid <guid>` — loads bullets into DB, sets status `extracted`
5. Refresh the Topics page (`/`) to see bullets with car/theme tags

---

## Git

Repo: `https://github.com/jacobworsoe/bilradio-transcriper`  
Branch: `main`  
Latest commit: `c88dd42` — unbuffered Whisper subprocess fix

All code is committed and pushed. The `data/` directory is gitignored — audio files, transcripts, and SQLite DB are local only.

---

## Session log

| Session | Key outcome |
|---|---|
| Session 1 | Built full pipeline: RSS sync, download, Whisper, Cursor inbox, web UI with queue controls |
| Session 2 (morning) | Fixed Whisper subprocess bugs (exe crash, Unicode paths, buffering, skip detection). Confirmed working on 30s clip. |
| Session 2 (evening) | Confirmed Whisper IS working on full episodes (GPU at 90%, 5 GB RAM). Processes killed manually before completion. DB reset: 22 downloaded, ready to go. |
