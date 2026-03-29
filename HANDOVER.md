# Bilradio Transcriber — Handover Document

_Last updated: 2026-03-30. Covers all work done in the [first session](08dd503a-6a2a-4b6d-a2d7-a266f1a90b26)._

---

## What was built

A local pipeline that:
1. Fetches the Bilradio podcast RSS feed (episodes from **2025-11-07** onwards, all durations).
2. Downloads MP3 audio files.
3. Transcribes them locally with **OpenAI Whisper** (CUDA, medium model).
4. Writes transcript + structured prompt to `data/cursor_inbox/` for analysis inside Cursor.
5. Imports the structured JSON bullets into SQLite.
6. Serves a **FastAPI web UI** with two pages:
   - `/` — Topic bullets browser with car/theme exclusion filters.
   - `/queue` — Transcription queue manager with start/stop controls.

---

## Repo layout

```
bilradio/
  config.py          # All constants and env-var overrides
  db.py              # SQLite schema + helpers
  rss_feed.py        # RSS fetch + filtering
  download.py        # MP3 download
  audio_meta.py      # Duration extraction (mutagen)
  whisper_run.py     # Whisper subprocess wrapper with stall detection
  pipeline.py        # Orchestration (sync, download, transcribe, queue runner)
  extract.py         # Cursor-inbox writer + bullet JSON parser
  cli.py             # Typer CLI entry point
  web/
    app.py           # FastAPI routes (topics + queue API)
    templates/
      index.html     # Topics page (English UI)
      queue.html     # Queue page (English UI)
    static/
      style.css
.cursor/rules/
  agent-run-commands.mdc   # Agent runs all commands itself
  ui-language.mdc          # UI = English, podcast content = Danish
data/                      # gitignored — all runtime data lives here
  bilradio.sqlite
  audio/
  transcripts/
  cursor_inbox/
  whisper_pid.txt          # PID of active Whisper subprocess
  whisper_heartbeat.txt    # Latest Whisper stdout line + timestamp
  whisper_current_guid.txt # GUID of episode currently being transcribed
  queue_runner_pid.txt     # PID of bilradio run-queue / pipeline subprocess
```

---

## Setup (first time on a new machine)

```powershell
# 1. Create and activate venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install the package (editable)
pip install -e .

# 3. Initialise database
bilradio init
```

**Python interpreter:** `.venv\Scripts\python.exe` — always use this when invoking via
`python.exe -m bilradio.cli ...` (the agent rule already ensures this).

---

## Key environment variables

All optional; set in `.env` (gitignored) or in the shell.

| Variable | Default | Purpose |
|---|---|---|
| `BILRADIO_MIN_DURATION_SEC` | `0` | Episodes shorter than this are skipped (0 = include all) |
| `BILRADIO_WHISPER_MODEL` | `medium` | Whisper model size |
| `BILRADIO_WHISPER_DEVICE` | `cuda` | `cuda` or `cpu` |
| `BILRADIO_WHISPER_CMD` | `whisper` | Path to Whisper binary if not on PATH |
| `BILRADIO_WHISPER_STALL_SEC` | `120` | Kill Whisper if silent for this many seconds |
| `BILRADIO_WHISPER_BOOT_SILENCE_SEC` | `300` | Grace period before stall detection starts |
| `BILRADIO_WHISPER_HEARTBEAT_SEC` | `120` | How often to write heartbeat file |
| `BILRADIO_WHISPER_MAX_RESTARTS` | `10` | Max automatic restarts on stall |
| `BILRADIO_DATA_DIR` | `data` | Root for all runtime data |
| `BILRADIO_RSS_URL` | Omny URL | Override RSS feed URL |

---

## CLI commands

```powershell
# Activate venv first (or prefix with .\.venv\Scripts\python.exe -m bilradio.cli)

bilradio init              # Create dirs + DB schema
bilradio sync              # Fetch RSS and upsert episodes
bilradio download          # Download all pending episodes (or --guid X)
bilradio transcribe        # Whisper all downloaded episodes (or --guid X)
bilradio prepare-extract   # Write cursor_inbox files (or --guid X)
bilradio import-bullets --guid X [--file path.json]  # Load Cursor output into DB
bilradio scaffold-bullets --guid X                   # Auto-bullets for UI preview
bilradio process-first     # Sync + download + transcribe oldest pending episode
bilradio pipeline [--guid X]   # sync + download + transcribe + prepare-extract
bilradio run-queue         # Full unattended queue: sync, download all, transcribe all
bilradio serve [--port 8765]   # Start web UI
```

---

## Web UI

Start with:
```powershell
.\.venv\Scripts\python.exe -m bilradio.cli serve
```

Opens at **http://127.0.0.1:8765**

### `/queue` page
- **"▶ Start full queue"** — launches `run-queue` as a background process; syncs RSS, downloads all pending, transcribes all in order.
- **"■ Stop"** — kills the queue runner and any active Whisper child.
- **"⟳ Sync RSS"** — fetches the RSS feed immediately without touching the terminal.
- **"▶ Run"** (per row) — available when queue is idle; launches `pipeline --guid X` for that single episode (download + transcribe + prepare-extract).
- **Whisper heartbeat bar** — shows last Whisper stdout line and timestamp when active.
- Status badges: Pending / Downloaded / Transcribing / Transcribed / Extracted / Error.
- Auto-refreshes every 30 seconds.

### `/` (Topics) page
- Shows bullet-point summaries across all `extracted` episodes.
- Chips to exclude specific cars or themes from the summary.
- Exclusions saved in `localStorage`.

---

## Episode processing pipeline (full detail)

```
RSS feed
  └─ sync_episodes_from_rss()  →  episodes table (status=pending)
       └─ step_download()       →  data/audio/<guid>.mp3, status=downloaded
            └─ step_transcribe() →  data/transcripts/<guid>.txt, status=transcribed
                 └─ step_prepare_cursor_inbox()  →  data/cursor_inbox/<guid>_transcript.txt
                                                     data/cursor_inbox/<guid>_CURSOR_PROMPT.md
                      └─ [manual Cursor step]  →  data/cursor_inbox/<guid>.bullets.json
                           └─ step_import_bullets()  →  topic_bullets table, status=extracted
```

---

## Whisper monitoring

`whisper_run.py` wraps the Whisper CLI with:
- **Heartbeat file** (`data/whisper_heartbeat.txt`) — updated every 2 minutes with last stdout line.
- **PID file** (`data/whisper_pid.txt`) — lets the web UI kill Whisper independently.
- **Stall detection** — if no new stdout after `WHISPER_STALL_SEC` (120s default), kills and retries up to `WHISPER_MAX_RESTARTS` times.
- **Boot grace period** — `WHISPER_BOOT_SILENCE_SEC` (300s) before stall detection activates (allows GPU model load time).

---

## Current database state (as of handover)

| Status | Count |
|---|---|
| `extracted` | 1 (24s test clip — `cfedf6fc-bf33-4cf8-b0dd-b41a00c840d9`) |
| `downloaded` | 1 (episode 317 — ready to transcribe) |
| `pending` | 21 (episodes 318–338) |

**Episode 317** audio is already downloaded at `data/audio/317-*.mp3`.  
The test clip has scaffold bullets in the DB so the Topics page shows some content.

---

## What to do next session

### Priority 1 — Transcribe all episodes
Use the web UI Queue page (`/queue`) or the terminal:

```powershell
# Option A: from the web UI
# → Open http://127.0.0.1:8765/queue  →  click "▶ Start full queue"

# Option B: from the terminal
.\.venv\Scripts\python.exe -m bilradio.cli run-queue
```

This will take roughly **22 hours** total (one ~1h episode at a time, sequentially).  
You can stop and resume at any time — completed episodes keep their status.

### Priority 2 — Extract bullets via Cursor (after transcription)
For each transcribed episode:
1. Run `bilradio prepare-extract --guid <guid>` (or use "▶ Run" in the web UI which does this automatically).
2. Open `data/cursor_inbox/<guid>_CURSOR_PROMPT.md` in Cursor.
3. Follow the instructions in the file — Cursor will analyse the transcript and produce JSON.
4. Save the JSON to `data/cursor_inbox/<guid>.bullets.json`.
5. Run `bilradio import-bullets --guid <guid>` to load it into the DB.

### Priority 3 — Improve the Topics page
Once bullets are imported, the `/` page will show real content. Possible improvements:
- Full-text search across bullets.
- Episode detail page showing all bullets for one episode.
- Cross-episode "most discussed cars" or "most discussed themes" chart.
- Export to CSV / Markdown summary.

### Known rough edges
- **Danish characters** in episode titles render as `?` in the terminal on Windows (cp1252 issue) — cosmetic only, DB stores them correctly.
- **`scaffold-bullets`** produces rough auto-split bullets (not NLP-tagged) — replace with real `import-bullets` output once Cursor analysis is done.
- The `run-queue` process launched from the web UI does **not** send stdout to the browser; monitor via the heartbeat bar and status badges.
- If the machine sleeps/restarts mid-transcription, the PID files may be stale. The web UI handles this gracefully (PID alive-check), but you may need to manually delete `data/whisper_pid.txt` and `data/queue_runner_pid.txt` if the badges show "Running" incorrectly after a reboot.

---

## Git

Repo: `https://github.com/jacobworsoe/bilradio-transcriper`  
Branch: `main` (all work committed and pushed as of this handover)

The `data/` directory is gitignored — audio, transcripts, SQLite DB, and PID files are local only.
