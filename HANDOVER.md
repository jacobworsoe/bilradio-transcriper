# Bilradio Transcriber — Handover Document

_Last updated: 2026-03-30. Covers sessions [1](08dd503a-6a2a-4b6d-a2d7-a266f1a90b26) and [2](08dd503a-6a2a-4b6d-a2d7-a266f1a90b26)._

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

## Current state (end of session 2)

| Status | Count | Notes |
|---|---|---|
| `downloaded` | 22 | Episodes 317-338, all MP3s on disk, ready to transcribe |
| `extracted` | 1 | 24-second test clip (scaffold bullets in DB) |
| **Total** | **23** | — |

**All 22 full episodes are downloaded and waiting for transcription.** Nothing is currently running.

---

## The unsolved bug — Whisper output not flowing through pipe

### What was diagnosed

Transcription has been attempted multiple times but episodes always end in error:  
`"Whisper did not produce expected transcript at …"`

Through systematic debugging, the following was established:

| Test | Result |
|---|---|
| `whisper.exe` binary with `stdout=PIPE` | Crashes silently, exits 0, no file |
| `python -m whisper` with `stdout=PIPE`, relative path | ffmpeg: "No such file or directory", skipped |
| `python -m whisper` with `stdout=PIPE`, **absolute path** | Works on 30s clip ✓ — output lines captured, .txt produced |
| `python -u -m whisper` + `PYTHONUNBUFFERED=1` with `stdout=PIPE`, long file (64 min) | Still no output lines after 3+ minutes |

**The current suspicion:** with a long audio file, Whisper or ffmpeg takes several minutes to load/decode the audio into RAM before producing any transcript lines. At 3 minutes the test was cut off — it may simply need more time. Alternatively, something in Whisper's internal buffering for large files is different from small files.

### What has been fixed

1. **`whisper.exe` binary → `python.exe -m whisper`**: The `.exe` wrapper crashes when stdout is piped. Now auto-detected via `bilradio/config.py:_find_whisper_python()`.
2. **Relative → absolute audio path**: `_build_cmd()` now calls `audio_path.resolve()`.
3. **`-u` + `PYTHONUNBUFFERED=1`**: Added to the Whisper subprocess to prevent block-buffering.
4. **Skipping detection**: Reader thread now detects `"Skipping … due to"` in stdout and raises `RuntimeError` rather than silently returning.
5. **Silent exit 0 detection**: If `_run_whisper_once` completes but `.txt` is missing, `FileNotFoundError` is raised (was already there, now more explicit).
6. **ASCII-only filenames**: `safe_filename_part()` now uses `c.isascii()` instead of `c.isalnum()` — Danish chars (`ø`, `å`, `æ`) are replaced with `_` to avoid ffmpeg path failures on Windows.

### What to try next session

**Step 1 — Confirm the 64-minute file actually works (wait longer)**

Start transcription of episode 317 via the web UI or terminal, but let it run for at least 15-20 minutes before checking output. The model load + full-file decode for a 64-minute episode could easily take 5-10 minutes before the first output line appears.

```powershell
# Start web server (if not running)
.\.venv\Scripts\python.exe -m bilradio.cli serve

# OR start directly from the queue page at http://127.0.0.1:8765/queue
# → Click "▶ Start full queue" (all 22 episodes already downloaded)
```

Watch the terminal or web UI heartbeat for transcript lines starting to appear (format: `[00:00.000 --> 00:30.000] text`).

**Step 2 — If it still hangs, try `--device cpu` first**

CUDA can cause unexpected hangs with long files if VRAM is tight. Test with CPU to isolate:

```powershell
$env:BILRADIO_WHISPER_DEVICE = "cpu"
.\.venv\Scripts\python.exe -m bilradio.cli transcribe --guid 48788b21-243c-4c9c-b0f7-b38e00c52f22
```

CPU will be slow (~4× real-time) but will confirm whether the issue is GPU-specific.

**Step 3 — If CPU also hangs, try chunked/streaming approach**

Whisper loads the ENTIRE audio file into RAM before processing. For a 64-minute file that's ~245 MB of float32 PCM + mel spectrogram on GPU. Consider:
- Splitting the audio with ffmpeg into 10-minute chunks before transcribing
- Using `--model small` instead of `medium` as a test
- Using the Python Whisper API directly (call `whisper.transcribe()`) instead of CLI subprocess

**Step 4 — Worst case: use `faster-whisper` instead**

`faster-whisper` is a drop-in replacement that is more memory-efficient and handles long files better. Install with `pip install faster-whisper` in the **global Python** (`C:\Python311`), then update `whisper_run.py` to use it.

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
