# Bilradio transcriber

Download [Bilradio](https://jyllands-posten.dk/podcast/bilradio/) from the Omny RSS feed, transcribe with **local OpenAI Whisper** (GPU), turn transcripts into **tagged topic bullets** using **Cursor** (no cloud LLM API key), and browse them in a small web UI.

## Requirements

- Python 3.11+
- [OpenAI Whisper](https://github.com/openai/whisper) CLI on your `PATH` (`pip install openai-whisper` in the project venv), with CUDA if you use `--device cuda`.
- `ffmpeg` (used by Whisper).

## Security and secrets

- **Do not commit** API keys, passwords, tokens, or private URLs. This repo is intended to run **fully local** (Whisper on your machine; Cursor is optional and uses your editor, not a committed key).
- Optional settings go in **`.env`** at the repo root. **`.env` is listed in `.gitignore`** and must never be added to git.
- The default RSS URL in code is the **public** Bilradio Omny feed — not a secret.
- If you fork this repo, rotate any credentials you use elsewhere; do not paste them into issues or commits.

## Setup

```powershell
cd bilradio-transcriper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
pip install openai-whisper
```

On Windows, point Bilradio at the venv interpreter if needed:

```powershell
# .env (local only, not committed)
BILRADIO_WHISPER_PYTHON=C:\path\to\bilradio-transcriper\.venv\Scripts\python.exe
```

## Recommended: batch Whisper (full ingest pipeline)

Use this when the integrated pipeline or web queue is fragile on long GPU runs. The script runs the **ingest pipeline** end-to-end: **syncs RSS**, **downloads new pending episodes**, runs the **`whisper` executable** on each `data/audio/*.mp3` (default **medium** + **cuda** + Danish), writes outputs into **`data/transcripts`**, then runs **`ingest-transcripts`** so SQLite rows become **`transcribed`** when matching files exist.

From the repo root:

```powershell
.\.venv\Scripts\python.exe .\scripts\batch_whisper_transcribe.py
```

Useful options:

```powershell
# Skip RSS/download if you only want transcription
.\.venv\Scripts\python.exe .\scripts\batch_whisper_transcribe.py --skip-sync-download

# Skip SQLite update (only write transcript files on disk)
.\.venv\Scripts\python.exe .\scripts\batch_whisper_transcribe.py --skip-ingest-transcripts

# Re-run Whisper even when .txt already exists
.\.venv\Scripts\python.exe .\scripts\batch_whisper_transcribe.py --retry-failed

# CPU instead of GPU
.\.venv\Scripts\python.exe .\scripts\batch_whisper_transcribe.py --device cpu
```

If you used **`--skip-ingest-transcripts`** or need to re-sync disk → DB later: **`bilradio ingest-transcripts`**.

After ingest, continue with **`bilradio prepare-extract`** and the Cursor JSON flow (see below).

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `BILRADIO_DATA_DIR` | `data` | SQLite, audio, transcripts, `cursor_inbox`. |
| `BILRADIO_CURSOR_INBOX_DIR` | `<data>/cursor_inbox` | Transcript + prompt files for Cursor. |
| `BILRADIO_WHISPER_PYTHON` | _(auto)_ | Python that runs `python -m whisper` (set to venv `python.exe` on Windows if needed). |
| `BILRADIO_WHISPER_CMD` | `whisper` | Fallback whisper executable name for detection. |
| `BILRADIO_WHISPER_MODEL` | `medium` | Default for integrated `bilradio transcribe` (batch script defaults to `medium` in code). |
| `BILRADIO_WHISPER_DEVICE` | `cuda` | Whisper `--device` (use `cpu` if no GPU). |
| `BILRADIO_WHISPER_SUBPROCESS` | _(unset = full)_ | Set to `simple` for terminal-like integrated Whisper (inherit stdio; no stall watchdog). |
| `BILRADIO_WHISPER_VERBOSE` | `true` | Set `false` for tqdm-only subprocess output in integrated mode. |
| `BILRADIO_WHISPER_HEARTBEAT_SEC` | `60` | Console heartbeat interval in integrated `full` mode. |
| `BILRADIO_WHISPER_STALL_SEC` | `120` | Kill Whisper if no new stdout in integrated `full` mode (after boot grace). |
| `BILRADIO_WHISPER_BOOT_SILENCE_SEC` | `300` | Grace before stall detection in integrated `full` mode. |
| `BILRADIO_WHISPER_MAX_RESTARTS` | `10` | Stall restarts in integrated mode. |
| `BILRADIO_TRANSCRIPT_CHUNK_CHARS` | `45000` | Splits long transcripts into extra `*_chunk_*.txt` files. |
| `BILRADIO_MIN_DURATION_SEC` | `60` | Episodes shorter than this (seconds) are **not** inserted from RSS; after download, they become **`skipped_short`**. Set to **`0`** to allow sub-minute clips everywhere. |

Episodes are included when **`pubDate` ≥ 2025-11-07 12:02:43 UTC** (see `config.MIN_PUBDATE_UTC`).

**One-time cleanup:** `bilradio purge-short-episodes` removes existing SQLite rows under the threshold (plus related files), with optional **`--dry-run`**. **`--no-probe`** skips probing MP3 length when `duration_sec` is unknown.

## Cursor workflow (bullets + tags)

1. **`bilradio prepare-extract`** (or run **`bilradio process-first`** below) writes:
   - `data/cursor_inbox/<guid>_transcript.txt`
   - `data/cursor_inbox/<guid>_CURSOR_PROMPT.md` — open this in Cursor and follow the instructions (clean transcript + emit JSON bullets with `cars` / `themes`).
2. Save the model output as **`data/cursor_inbox/<guid>.bullets.json`** (JSON only, shape `{"bullets":[...]}`).
3. **`bilradio import-bullets --guid <guid>`** (optional **`--file`** if the JSON lives elsewhere).

**Quick web preview:** `bilradio process-first` runs **`scaffold-bullets`** by default: rough paragraph bullets with theme `forhåndsvisning` so you can open the site before Cursor. Replace with **`import-bullets`** when your JSON is ready.

## Commands (Typer CLI)

```powershell
.\.venv\Scripts\python.exe -m bilradio.cli init
.\.venv\Scripts\python.exe -m bilradio.cli sync
.\.venv\Scripts\python.exe -m bilradio.cli download [--guid ...]
.\.venv\Scripts\python.exe -m bilradio.cli transcribe [--guid ...]
.\.venv\Scripts\python.exe -m bilradio.cli ingest-transcripts [--guid ...]
.\.venv\Scripts\python.exe -m bilradio.cli clear-error --guid <guid>
.\.venv\Scripts\python.exe -m bilradio.cli prepare-extract [--guid ...]
.\.venv\Scripts\python.exe -m bilradio.cli import-bullets --guid <guid> [--file path.json]
.\.venv\Scripts\python.exe -m bilradio.cli scaffold-bullets --guid <guid>
.\.venv\Scripts\python.exe -m bilradio.cli process-first
.\.venv\Scripts\python.exe -m bilradio.cli pipeline [--guid ...]
.\.venv\Scripts\python.exe -m bilradio.cli run-queue
.\.venv\Scripts\python.exe -m bilradio.cli serve [--no-reload]
.\.venv\Scripts\python.exe -m bilradio.cli purge-short-episodes [--dry-run] [--seconds N]
.\.venv\Scripts\python.exe -m bilradio.cli doctor
```

Integrated `bilradio transcribe` / `run-queue` use a subprocess wrapper (progress + optional stall restart). For behavior closest to typing `whisper` in a terminal, set **`BILRADIO_WHISPER_SUBPROCESS=simple`** in `.env` or use **`scripts/batch_whisper_transcribe.py`** above.

Whisper is invoked equivalently to:

```text
whisper "<audio.mp3>" --model medium --language da --device cuda --temperature 0 --condition_on_previous_text True --output_format txt --output_dir <data>/transcripts
```

## Web UI

Open **`http://127.0.0.1:8765`** after **`bilradio serve`**.

- **`/`** — Topic bullets with car and theme tags; click tags to **exclude** them (stored in `localStorage`).
- **`/episodes`** — Pipeline status: RSS sync, ingest transcripts, disk vs DB columns, **Clear error**, auto-refresh. Status badges reflect **effective** state (for example **Summarized** when bullets are in SQLite, and a stale DB **error** is not shown as error if Whisper JSON already exists on disk).
- **Topics time ranges:** Improved / import JSON may include **`start_sec` / `end_sec`** (from Whisper segment times). They are stored in SQLite and shown on the Topics page; **re-run `import-bullets`** after adding timestamps to existing JSON.

**`bilradio serve`** turns on **uvicorn auto-reload by default** (watches `bilradio/**/*.py` and `*.html`). Use **`bilradio serve --no-reload`** for a single long-lived process. On startup the CLI prints **`Web UI from …`** — if the UI looks wrong, confirm that path is this repo’s `bilradio` package (not another clone or stale editable install).

The legacy **Queue** URL redirects to **`/episodes`**; there is no in-browser queue for Whisper. For long GPU runs, use the **batch script + ingest** workflow above.

Full transcripts are not shown in the UI.
