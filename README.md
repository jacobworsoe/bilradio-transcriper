# Bilradio transcriber

Download [Bilradio](https://jyllands-posten.dk/podcast/bilradio/) from the Omny RSS feed, transcribe with **local OpenAI Whisper** (GPU), turn transcripts into **tagged topic bullets** using **Cursor** (no cloud LLM API key), and browse them in a small web UI.

## Requirements

- Python 3.11+
- [OpenAI Whisper](https://github.com/openai/whisper) CLI on your `PATH` (`pip install openai-whisper`), with CUDA if you use `--device cuda`.
- `ffmpeg` (used by Whisper).

## Setup

```powershell
cd bilradio-transcriper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `BILRADIO_DATA_DIR` | `data` | SQLite, audio, transcripts, `cursor_inbox`. |
| `BILRADIO_CURSOR_INBOX_DIR` | `<data>/cursor_inbox` | Transcript + prompt files for Cursor. |
| `BILRADIO_WHISPER_CMD` | `whisper` | Whisper executable. |
| `BILRADIO_WHISPER_MODEL` | `medium` | Whisper model size. |
| `BILRADIO_WHISPER_DEVICE` | `cuda` | Whisper `--device` (use `cpu` if no GPU). |
| `BILRADIO_WHISPER_HEARTBEAT_SEC` | `120` | Log `[bilradio-whisper] heartbeat … latest: …` this often. |
| `BILRADIO_WHISPER_STALL_SEC` | `120` | Kill Whisper if no new stdout line for this long (after boot grace). |
| `BILRADIO_WHISPER_BOOT_SILENCE_SEC` | `300` | No stall kills before this many seconds from process start. |
| `BILRADIO_WHISPER_MAX_RESTARTS` | `10` | After a stall, Whisper is restarted up to this many times. |
| `BILRADIO_TRANSCRIPT_CHUNK_CHARS` | `45000` | Splits long transcripts into extra `*_chunk_*.txt` files. |

Episodes are included when **`pubDate` ≥ 2025-11-07 12:02:43 UTC** and **`itunes:duration` ≥ 60 s** when present in the feed. If duration is missing, the episode is still queued; after download, duration is read from the MP3 and sub‑minute files are marked `skipped_short`.

**Short test clip:** Guids listed in **`BILRADIO_TEST_EPISODE_GUIDS`** (comma-separated) bypass the ≥60s rule. If the variable is **unset**, it defaults to the Omny ~24 s promo (`cfedf6fc-bf33-4cf8-b0dd-b41a00c840d9`) for pipeline smoke tests. Set **`BILRADIO_TEST_EPISODE_GUIDS=`** (empty) to disable.

## Cursor workflow (bullets + tags)

1. **`bilradio prepare-extract`** (or run **`bilradio process-first`** below) writes:
   - `data/cursor_inbox/<guid>_transcript.txt`
   - `data/cursor_inbox/<guid>_CURSOR_PROMPT.md` — open this in Cursor and follow the instructions (clean transcript + emit JSON bullets with `cars` / `themes`).
2. Save the model output as **`data/cursor_inbox/<guid>.bullets.json`** (JSON only, shape `{"bullets":[...]}`).
3. **`bilradio import-bullets --guid <guid>`** (optional **`--file`** if the JSON lives elsewhere).

**Quick web preview:** `bilradio process-first` runs **`scaffold-bullets`** by default: rough paragraph bullets with theme `forhåndsvisning` so you can open the site before Cursor. Replace with **`import-bullets`** when your JSON is ready.

## Commands

```powershell
bilradio init
bilradio sync
bilradio download [--guid ...]
bilradio transcribe [--guid ...]
bilradio prepare-extract [--guid ...]
bilradio import-bullets --guid <guid> [--file path.json]
bilradio scaffold-bullets --guid <guid>
bilradio process-first          # sync + oldest pending: download, transcribe, prepare-extract, scaffold
bilradio pipeline [--guid ...]  # sync, download, transcribe, prepare-extract
bilradio serve                  # http://127.0.0.1:8765
```

While transcribing, a heartbeat line is printed about every 2 minutes (`BILRADIO_WHISPER_HEARTBEAT_SEC`), and the same snapshot is written to **`data/whisper_heartbeat.txt`** so you or the agent can confirm progress without scrolling the terminal. If Whisper prints nothing new for 2 minutes after the first 5 minutes of runtime, the process is killed and restarted (see `BILRADIO_WHISPER_*` vars above).

Whisper is invoked equivalently to:

```text
whisper "<audio.mp3>" --model medium --language da --device cuda --temperature 0 --condition_on_previous_text True --output_format txt --output_dir <data>/transcripts
```

## Web UI

Open the URL printed by `bilradio serve`. You get bullet points with car and theme tags. Click tags to **exclude** them; choices are stored in `localStorage`. Full transcripts are not shown in the UI.
