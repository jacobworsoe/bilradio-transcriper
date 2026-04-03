# Bilradio Transcriber — Handover Document

_Last updated: 2026-04-04. Operator + maintainer notes._

---

## What was built

A local pipeline that:

1. Fetches the Bilradio podcast RSS feed (episodes from **2025-11-07** onwards). By default, items **under 60 seconds** are excluded (`BILRADIO_MIN_DURATION_SEC`, default **60**; set **`0`** to allow promos/clips).
2. Downloads MP3 audio files under `data/audio/`.
3. Transcribes with **OpenAI Whisper** (typically CUDA + **medium**) — **recommended:** batch CLI script; integrated `bilradio transcribe` / `run-queue` remain optional.
4. Keeps **Whisper output on disk** under `data/transcripts/` (`.json` preferred; `.txt` also supported).
5. **Improved** structured JSON (shape from **`CURSOR_INSTRUCTIONS`** in `bilradio/extract.py`) under `data/transcripts_improved/<stem>.json` — **author with Cursor Auto Agent** (see `.cursor/rules/transcript-storage.mdc`). **Authoring is two steps:** Whisper on disk, then one improved JSON (summaries + structure); **`import-bullets`** loads that file into SQLite — no separate third “bullet authoring” step. Optional **`start_sec` / `end_sec` on each section** only (aligned with Whisper **`segments`**); per-bullet times are not part of the contract. **`CURSOR_INSTRUCTIONS`** also require **omitting sponsor/ad read copy** (e.g. GAVMIL spots) from bullets, and **section/bullet counts follow episode content** (not a fixed template).
6. Imports sectioned bullets into **SQLite** (`topic_sections` + `topic_bullets`, including optional time columns on sections/bullets for legacy rows) via `import-bullets`.
7. Serves a **FastAPI web UI:** **`/`** Topics — wide layout (**`body` max-width 85rem**), car/theme **exclude** chips (excluded chips use **red** strikethrough text), facets, **per-section** summary line (**time range** + **section title** + tags on **own line**), **disc list markers** for bullets, **`/api/bullets`** returns **`bullet_time_range` only when** the DB has per-bullet times (otherwise `null` — UI hides the span). **First block of each episode:** one **`h2.episode-heading`** with **`YYYY-MM-DD - [episode title]`** (larger type, **5.5rem** top margin **except** the **first** episode on the page, which keeps **0.5rem** top). **No extra `h2` per section** — section names live only in the summary row. **`/episodes`** Episodes status (disk + DB flags, RSS sync, ingest, clear error). **`/queue`** redirects to **`/episodes`**. **`bilradio serve`** uses **auto-reload by default** (watch `bilradio` `*.py` / `*.html`); **`--no-reload`** for a stable process; startup prints **`Web UI from …`** for the resolved package path.

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

**Step 2 (improved JSON)** is **not** part of this script. Whisper → disk is step 1 only. **Cursor Auto Agent** runs inside Cursor on `data/cursor_inbox/*_improve_auto_agent.md`; there is no headless “run Agent from Python” hook in-repo. To **batch-fill missing** improved files without the Agent, use **`scripts/improved_json_from_segments.py`** per episode (stem + `--guid` from the prompt or DB), then **`import-bullets`** per guid — output is **interim** (`_bilradio_meta.replace_with_cursor_agent`, generic section titles, `themes: ["bootstrap"]`, `uncertain: true`) until replaced by a real Agent pass.

**Web UI (`bilradio serve`):** open **`/episodes`** for **Sync RSS**, **Ingest transcripts**, and columns: Downloaded / Whisper (JSON+TXT) / Improved / Status / Bullets. **Display status** is normalized for the UI: SQLite **`extracted`** shows as **Summarized** (bullets loaded); if Whisper **JSON exists on disk** but the DB still has **`error`**, the row is shown as **transcribed** with an optional stale-error note instead of a blocking error badge. **`serve`** auto-reloads on code/template changes unless **`--no-reload`**; confirm the printed **`Web UI from …`** path if the site looks stale.

---

## Improved JSON → bullets (Cursor Auto Agent)

- **Policy:** No third-party LLM APIs for improved JSON unless explicitly decided; use **Cursor Auto Agent** (see `.cursor/rules/transcript-storage.mdc`).
- **`bilradio prepare-improved-agent`** — writes `data/cursor_inbox/<guid>_improve_auto_agent.md` with output path and full `CURSOR_INSTRUCTIONS`.
- **`bilradio bootstrap-improved-json`** — optional extractive placeholders (`_bilradio_meta.replace_with_cursor_agent: true`); replace with Agent output when ready. **Limitation:** if Whisper **`.json`** has almost no paragraph breaks (one big block of segment lines), bootstrap may produce **one** tiny section — use **`scripts/improved_json_from_segments.py`** instead (interim segment-chunk placeholders; **section-level** time spans only, no per-bullet times).
- **`bilradio import-bullets --guid <guid> --file data\transcripts_improved\<stem>.json`** — loads sections/bullets into SQLite and sets episode **`extracted`**.
- **Timecodes:** JSON may include **`start_sec` / `end_sec` on sections** (floats). After editing, **re-run `import-bullets`**. For a **mechanical backfill**, **`scripts/apply_whisper_timecodes.py`** slices the Whisper **segment** timeline **proportionally by section** (weighted by bullet count per section), writes **section** bounds, and **strips any per-bullet** `start_sec`/`end_sec` so output matches **`CURSOR_INSTRUCTIONS`**:

```powershell
.\.venv\Scripts\python.exe scripts\apply_whisper_timecodes.py `
  data\transcripts\<stem>.json `
  data\transcripts_improved\<stem>.json
```

**Segment-chunk improved JSON** (single-block Whisper JSON, extractive placeholders):

```powershell
.\.venv\Scripts\python.exe scripts\improved_json_from_segments.py `
  data\transcripts\<stem>.json `
  data\transcripts_improved\<stem>.json `
  --guid <episode-guid>
```

Optional: `--seg-per-bullet` (default 12), `--per-section` (default 5), `--max-text`, `--stem`. Then **`import-bullets`** as usual.

**Working through a full `cursor_inbox` backlog:** run **`prepare-improved-agent`** once (optional `--limit`) so every episode that lacks improved JSON gets an `*_improve_auto_agent.md` file. Episodes that **already** have `data/transcripts_improved/<stem>.json` are skipped unless **`--force`**. For each prompt whose JSON path is still missing, run **`improved_json_from_segments.py`** (or open the `.md` in Cursor for a proper Agent run), then **`import-bullets --guid … --file …`**. Section times from the segment script follow Whisper **segment** groupings; **`apply_whisper_timecodes.py`** is optional (proportional slice of the whole timeline) and is usually **redundant** right after `improved_json_from_segments.py`.

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
  short_episode_purge.py    # Remove sub-min-duration episodes + files
  bootstrap_improved.py     # Extractive improved JSON (optional)
  prepare_improved_agent.py # Auto Agent Markdown prompts
  transcript_text.py        # Plain text + whisper_segments_from_json()
  time_format.py            # Time range strings for /api/bullets
  extract.py                # CURSOR_INSTRUCTIONS, bullet parse/import shape
  pipeline.py               # sync, download, transcribe, ingest, import, scaffold, …
  whisper_run.py
  web/app.py                # /api/bullets, /api/episodes, …
  web/templates/index.html  # Topics client render (episode h2, sections, bullets)
  web/static/style.css      # Topics + episodes layout
scripts/
  batch_whisper_transcribe.py
  apply_whisper_timecodes.py  # Section-level proportional Whisper times; strips bullet times
  improved_json_from_segments.py  # Interim multi-section JSON from segments (section times only)
  episode_cleanup.py        # CLI wrapper for coverage report
.cursor/rules/
  transcript-storage.mdc    # Two-step authoring + SQLite import (not three manual layers)
  web-restart-after-changes.mdc  # Web/API edits + serve reload behavior
  agent-run-commands.mdc    # Agents run commands; long servers in background
data/                       # gitignored
```

---

## Key env vars

**Whisper:** `BILRADIO_WHISPER_PYTHON`, `BILRADIO_WHISPER_MODEL`, `BILRADIO_WHISPER_DEVICE`, `BILRADIO_WHISPER_SUBPROCESS`, `BILRADIO_WHISPER_VERBOSE`, stall/boot settings (see `config.py`).

**Paths:** `BILRADIO_DATA_DIR`, `BILRADIO_TRANSCRIPTS_IMPROVED_DIR` (optional).

**Short episodes:** default **60s** minimum; **`purge-short-episodes`** drops matching rows and files (`--dry-run` to preview).

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
purge-short-episodes    # Delete rows & files under min duration (--dry-run, --seconds)
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
2. Generate **`data/transcripts_improved/<stem>.json`** with **Cursor Auto Agent** (`prepare-improved-agent` prompts), **`bootstrap-improved-json`**, or **`improved_json_from_segments.py`** when bootstrap collapses to one block (or to bulk-fill many missing files before Agent polish). Optionally run **`apply_whisper_timecodes.py`** for **section**-level times when improved JSON was authored without segment-aligned bounds (often **skipped** immediately after `improved_json_from_segments.py`).
3. **`bilradio import-bullets --guid <guid> --file data\transcripts_improved\<stem>.json`**
4. Open **`/`** in the web app: **facets**, **section** time range on the summary line (no per-bullet range in the UI unless legacy DB rows still have bullet times), **episode heading** format **`date - title`**, and list **disc** markers for bullets.

---

## Git

Remote: `https://github.com/jacobworsoe/bilradio-transcriper`  
Branch: `main`  

For current HEAD after pull: `git log -1 --oneline`

---

## Session log (high level)

| Period | Focus |
|--------|-------|
| Early | RSS, download, Whisper subprocess, web queue, stall/skip |
| Mid | `ingest-transcripts`, batch script, `WHISPER_SUBPROCESS=simple`, `clear-error` |
| Later | Episodes status page (disk truth for JSON), sectioned bullets + DB schema, transcript-storage rule, Cursor Auto Agent workflow (no OpenAI improver), `prepare-improved-agent` / `bootstrap-improved-json` |
| 2026-04 | Episodes **display_status** (Summarized vs extracted, stale-error override when JSON on disk), **`serve`** auto-reload + package path echo, Cursor agent rule: start **`bilradio serve` in background** so the user’s terminal stays free |
| 2026-04 | Default **`MIN_DURATION_SEC=60`**, **`purge-short-episodes`** CLI + orphan short MP3 cleanup |
| 2026-04 | Topics condensed outline; optional **section** times in improved JSON → SQLite → `/api/bullets` |
| 2026-04 | **`apply_whisper_timecodes.py`** → **section-only** times; **`improved_json_from_segments.py`** → section times only; Topics UI: **no per-section `h2`**, **episode `h2`** as **`date - title`**, summary row shows **every** section title, **disc** bullets, **wider** layout, **red** excluded chips, **`bullet_time_range`** omitted in API when bullets have no times |
| 2026-04 | **`CURSOR_INSTRUCTIONS`**: content-driven section counts, **section-level** timecodes, **omit sponsor reads**; transcript-storage rule = **two authoring steps** + SQLite import; hand-curated / repaired improved JSON for sample episodes (**317**, **318**) |
| 2026-04 | Handover: **`batch_whisper_transcribe.py`** = step 1 only; step 2 batch options (**`prepare-improved-agent`** backlog + **`improved_json_from_segments.py`** + **`import-bullets`**); backlog fill for missing prompts vs segment interim JSON |
