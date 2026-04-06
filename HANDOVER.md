# Bilradio Transcriber — Handover Document

_Last updated: 2026-04-06 (session: ingest vs improvement pipelines; rules + handover). Operator + maintainer notes._

---

## What was built

Work is split into two **named pipelines** (full CLI order and **`Prompt to create improved JSON.txt`** are in **`.cursor/rules/pipelines.mdc`**; disk/SQLite policy stays in **`.cursor/rules/transcript-storage.mdc`**).

### Ingest pipeline (steps 1–4)

1. Fetches the Bilradio podcast RSS feed (episodes from **2025-11-07** onwards). By default, items **under 60 seconds** are excluded (`BILRADIO_MIN_DURATION_SEC`, default **60**; set **`0`** to allow promos/clips).
2. Downloads MP3 audio files under `data/audio/`.
3. Transcribes with **OpenAI Whisper** (typically CUDA + **medium**) — **recommended:** **`scripts/batch_whisper_transcribe.py`**; integrated **`bilradio transcribe`** / **`run-queue`** remain optional (`bilradio/whisper_run.py`).
4. Keeps **Whisper output on disk** under `data/transcripts/` (`.json` preferred; `.txt` also supported). **`batch_whisper_transcribe.py`** then runs **`ingest-transcripts`** so SQLite marks episodes **transcribed** when matching files exist (or run **`bilradio ingest-transcripts`** yourself if you use another transcribe path or **`--skip-ingest-transcripts`**).

### Improvement pipeline (everything after raw transcripts on disk)

Includes **`bilradio prepare-improved-agent`** (writes **`data/cursor_inbox/<guid>_improve_auto_agent.md`**), the operator task in **`Prompt to create improved JSON.txt`** (*Create the improved JSON for the oldest episode that is not yet improved and delete the inbox file for that episode when you are done.*), **Cursor Auto Agent** authoring of **`data/transcripts_improved/<stem>.json`** per **`CURSOR_INSTRUCTIONS`** in **`bilradio/extract.py`**, **`bilradio import-bullets`** into **`topic_sections`** / **`topic_bullets`**, **`bilradio serve`** for the local UI, and optionally **`bilradio export-github-pages`** → **`docs/`** for GitHub Pages.

**Authoring is still two disk steps:** Whisper (ingest end state), then one improved JSON (summaries + structure); **`import-bullets`** loads that file into SQLite — no separate third “bullet authoring” step. Optional **`start_sec` / `end_sec` on each section** only (aligned with Whisper **`segments`**); per-bullet times are not part of the contract. **`CURSOR_INSTRUCTIONS`** also require **omitting sponsor/ad read copy** (e.g. GAVMIL spots) from bullets, and **section/bullet counts follow episode content** (not a fixed template).

### Web app and public site

- **FastAPI web UI:** **`/`** = **Episodes** table (pipeline status, **RSS sync**, **Ingest transcripts**, **Clear error**). **`/topics`** = **Topics** — wide layout (**`body` max-width 85rem**), **per-section** summary line (**time range** + **section title** + car/theme tags on **own line**), **disc list markers** for bullets, **`/api/bullets`** returns **`bullet_time_range` only when** the DB has per-bullet times (otherwise `null` — UI hides the span). **First block of each episode:** one **`h2.episode-heading`** in **`episode-heading-row`** with **`YYYY-MM-DD - [episode title]`**; **5.5rem** between episodes except the first (**0.5rem** top). Section titles appear only in the summary row, not as extra **`h2`s**. **Client-only read state:** **`localStorage`** **`bilradio_read_episodes`** and **`bilradio_read_sections`** (composite **`sectionGroupKey`** = `episode_guid` + `section_order` + `section_title`, same string as in **`topics.html`**). **Mark episode read** / **Mark section read** hide content; **Clear all read** clears both. When **every section** of an episode is marked read, the episode is **auto-added** to **`bilradio_read_episodes`**. **`/api/bullets`** is ordered **`pub_date` ASC** (oldest episodes first on Topics). Single-episode **`/topics?guid=…`**: after nothing is left to show, the client **redirects to `/`**. **Episodes row UX:** status column = one **badge** (`display_status` + server-built **`status_label`**; disk/ingest states folded in — see **`episode_badge_for_row`** in **`web/app.py`**); **Sections** / **Bullets** columns show **unread** counts (from bullets JSON + read sets); **`–`** when no topics or when that unread count is **0**. **Read** pill (full read) is **blue**; **Partly read** uses the former gray read styling; **Mark read** adds the episode to **`bilradio_read_episodes`**. Episodes page loads **`/api/bullets`** once for read progress. **`/queue`** and **`/episodes`** redirect to **`/`**. **`bilradio serve`:** auto-reload by default; **`--no-reload`** if needed; startup prints **`Web UI from …`**.
- **Public static mirror on GitHub Pages:** **`bilradio export-github-pages`** writes **`docs/`** — **`index.html`** (Episodes), **`topics/index.html`** (Topics), **`docs/api/bullets.json`**, **`docs/api/episodes.json`** (no **`facets.json`**), **`static/style.css`**, **`CNAME`**, **`.nojekyll`**. **`TestClient`** calls the FastAPI **`/api/bullets`** and **`/api/episodes`** routes against the local DB. **`.github/workflows/pages.yml`** deploys on push to **`main`**. Live: **https://bilradio.jacobworsoe.dk** ( **`docs/CNAME`** ) and **https://jacobworsoe.github.io/bilradio-transcriper/**. Pages is **read-only** for RSS/ingest/clear-error. See **`.cursor/rules/github-pages-deploy.mdc`**. **Static Topics:** **`IS_STATIC`** branch in **`topics.html`** loads **`.json`** APIs and mirrors read/auto-episode-read/redirect behavior. Avoid duplicate **`const`/`let`** bindings for the same identifier in one **`loadBullets()`** scope (causes **SyntaxError** and endless **Loading…**).

---

## Security / secrets

- **No secrets belong in git.** The repo uses a public RSS URL only.
- Put optional paths and flags in **`.env`** at repo root; **`.env` is gitignored** — verify with `git check-ignore -v .env`.
- **`data/`** is gitignored (SQLite, audio, transcripts, improved JSON, cursor_inbox): do not force-add it if it ever contains anything sensitive.

---

## Primary ops path: ingest pipeline (batch Whisper + ingest-transcripts)

For reliable long GPU runs, use the **batch script** (not the web app for transcription). This covers the **ingest pipeline** through Whisper on disk plus DB **transcribed** status:

```powershell
cd C:\Git\bilradio-transcriper

# Full ingest pipeline: RSS, download, Whisper per data\audio\*.mp3, then ingest-transcripts → SQLite
.\.venv\Scripts\python.exe .\scripts\batch_whisper_transcribe.py

# Only if you skipped DB ingest or need to re-sync disk → DB later:
# .\.venv\Scripts\python.exe -m bilradio.cli ingest-transcripts
```

Batch script options (`python scripts\batch_whisper_transcribe.py --help`): `--skip-sync-download`, `--skip-ingest-transcripts`, `--retry-failed`, `--device cpu`, `--output-format` (omit for Whisper default `all`).

The **improvement pipeline** (improved JSON, **`cursor_inbox`**, **`import-bullets`**) is **not** part of this script. **Cursor Auto Agent** runs inside Cursor on `data/cursor_inbox/*_improve_auto_agent.md`; there is no headless “run Agent from Python” hook in-repo. Follow **`Prompt to create improved JSON.txt`** for backlog order and inbox cleanup. To **batch-fill missing** improved files without the Agent, use **`scripts/improved_json_from_segments.py`** per episode (stem + `--guid` from the prompt or DB), then **`import-bullets`** per guid — output is **interim** (`_bilradio_meta.replace_with_cursor_agent`, generic section titles, `themes: ["bootstrap"]`, `uncertain: true`) until replaced by a real Agent pass.

**Web UI (`bilradio serve`):** open **`/`** for **Sync RSS**, **Ingest transcripts**, **Clear error**, and the Episodes table (**status badge**, **Sections** / **Bullets** = unread counts, read/partly-read pills). **`/topics`** for Topics. **`serve`** auto-reloads on code/template changes unless **`--no-reload`**; confirm **`Web UI from …`** if the UI looks stale.

---

## Improvement pipeline: improved JSON → bullets (Cursor Auto Agent)

- **What to read:** **`CURSOR_INSTRUCTIONS`** in `bilradio/extract.py` require **section** `start_sec` / `end_sec` aligned with Whisper **`segments`** — use **`data/transcripts/<stem>.json`** for timing (and full text structure), not only a flat **`.txt`**. The repo-root file **`Prompt to create improved JSON.txt`** is the **operator / Agent task** for backlog runs (verbatim in **`.cursor/rules/pipelines.mdc`**); it is **not** the transcript. Inbox files from **`prepare-improved-agent`** / **`write_cursor_inbox`** include **`{guid}_transcript.txt`** (plain text extracted from Whisper); treat that as convenience text alongside the **`.json`** segments, not a replacement when setting timecodes.
- **Policy:** No third-party LLM APIs for improved JSON unless explicitly decided; use **Cursor Auto Agent** (see `.cursor/rules/transcript-storage.mdc`).
- **`bilradio prepare-improved-agent`** — writes `data/cursor_inbox/<guid>_improve_auto_agent.md` with output path and full `CURSOR_INSTRUCTIONS`.
- **`bilradio bootstrap-improved-json`** — optional extractive placeholders (`_bilradio_meta.replace_with_cursor_agent: true`); replace with Agent output when ready. **Limitation:** if Whisper **`.json`** has almost no paragraph breaks (one big block of segment lines), bootstrap may produce **one** tiny section — use **`scripts/improved_json_from_segments.py`** instead (interim segment-chunk placeholders; **section-level** time spans only, no per-bullet times).
- **`bilradio import-bullets --guid <guid> --file data\transcripts_improved\<stem>.json`** — loads sections/bullets into SQLite and sets episode **`extracted`**.
- **Typical Agent completion loop (one episode):** write improved JSON to the path named in `data/cursor_inbox/<guid>_improve_auto_agent.md` → **`import-bullets --guid <guid> --file …`** → **delete** that inbox **`.md`** → **`export-github-pages`** → **commit + push** tracked **`docs/api/*.json`** (and any template changes). **`data/`** stays gitignored. To pick the **oldest** episode still missing improved JSON among inbox prompts, order by **`episodes.pub_date`** in SQLite and skip GUIDs that already have a non-empty `data/transcripts_improved/*_<guid>.json`.
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
  web/app.py                # /api/bullets, /api/episodes (no /api/facets)
  web/templates/episodes.html
  web/templates/topics.html
  web/static/style.css      # Topics + episodes layout
  export_pages.py           # Static tree → docs/ for GitHub Pages
docs/                       # Published static site (tracked); from export-github-pages
.github/workflows/pages.yml # Deploy docs/ via GitHub Actions
scripts/
  batch_whisper_transcribe.py
  apply_whisper_timecodes.py  # Section-level proportional Whisper times; strips bullet times
  improved_json_from_segments.py  # Interim multi-section JSON from segments (section times only)
  episode_cleanup.py        # CLI wrapper for coverage report
.cursor/rules/
  pipelines.mdc           # Ingest vs improvement pipeline; prepare-improved-agent; Prompt to create improved JSON.txt
  transcript-storage.mdc    # Two authoring artifacts + SQLite import (not three manual layers)
  web-restart-after-changes.mdc  # Web/API edits + serve reload behavior
  github-pages-deploy.mdc   # When/how to export docs/ and deploy to Pages
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
export-github-pages     # Write docs/ static snapshot for GitHub Pages (--output, --cname)
doctor
self-test-transcribe
```

Logs: `data/logs/bilradio.log`, integrated runs may also write `data/logs/whisper_*.log` (full mode).

---

## After transcription: improvement pipeline → Topics

1. **Ingest pipeline** done: **`data/transcripts/<stem>.json`** (or `.txt`) exists and SQLite shows **transcribed** ( **`batch_whisper_transcribe.py`** runs **`ingest-transcripts`** for you; otherwise run **`bilradio ingest-transcripts`**).
2. **`prepare-improved-agent`** (and follow **`Prompt to create improved JSON.txt`** when working the backlog). Generate **`data/transcripts_improved/<stem>.json`** with **Cursor Auto Agent** (inbox prompts), **`bootstrap-improved-json`**, or **`improved_json_from_segments.py`** when bootstrap collapses to one block (or to bulk-fill many missing files before Agent polish). Optionally run **`apply_whisper_timecodes.py`** for **section**-level times when improved JSON was authored without segment-aligned bounds (often **skipped** immediately after `improved_json_from_segments.py`).
3. **`bilradio import-bullets --guid <guid> --file data\transcripts_improved\<stem>.json`**
4. Open **`/topics`** in the web app: **section** time range on the summary line (no per-bullet range unless legacy DB rows still have bullet times), **episode heading** **`date - title`**, **disc** markers for bullets.

---

## GitHub Pages

- **Workflow:** `.github/workflows/pages.yml` — **build** (checkout, `configure-pages@v5`, `upload-pages-artifact@v3` from **`docs/`**) then **deploy** (`deploy-pages@v4`, environment **`github-pages`**). Triggers: **push to `main`**, **Run workflow** in Actions.
- **Refresh published content:** with a populated local DB under **`data/`**, run **`bilradio export-github-pages`** (writes **`docs/`**), then **commit + push** `docs/` (tracked; **`data/`** is not). Optional **`--cname`**, **`--output`**. After **template or static CSS** changes, export updates **`docs/index.html`** and **`docs/static/style.css`** even if API JSON is unchanged.
- **Repo:** must be **public** (or Enterprise) for free Pages; **Settings → Pages → Source: GitHub Actions**; set **custom domain** in Settings to match **`docs/CNAME`** when using **bilradio.jacobworsoe.dk**.
- **Implementation:** `bilradio/export_pages.py`, Jinja templates in **`bilradio/web/templates/`** with **`static_site: true`** only for **`export-github-pages`** output; **`bilradio serve`** renders **`episodes.html`** and **`topics.html`** with **`static_site: false`**. Do not upgrade to **`upload-pages-artifact@v4.0.0`** without handling **`.nojekyll`** (v4.0.0 excludes dotfiles from the tarball).
- **Debugging “Loading…” with no JSON in Network:** usually a **JavaScript parse/runtime error** in inline script in **`topics.html`** or **`episodes.html`**. Check the browser console; re-export **`docs/`** after fixing templates.

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
| 2026-04 | **GitHub Pages:** static export (**`export-github-pages`** → **`docs/`**), Actions workflow, custom domain **bilradio.jacobworsoe.dk**; **`.cursor/rules/github-pages-deploy.mdc`** for deploy discipline |
| 2026-04-04 | **Cursor Auto Agent improved JSON** for inbox backlog (oldest first by **`pub_date`**): episodes **326**, **327**, **328** (`9ad8e4e0`, `86f78b40`, `f828e145`) — Zeekr/Xpeng/Volvo themes, priskrig 325k, etc.; each run ended with **`import-bullets`**, removed **`*_improve_auto_agent.md`**, **`export-github-pages`**, and **git push** of **`docs/`** API JSON |
| 2026-04 | Repo-root **`Prompt to create improved JSON.txt`** — short operator prompt for Agent backlog runs (not transcript source) |
| 2026-04 | **`import-bullets`** + **`export-github-pages`** + push for continued inbox backlog through **`57aef4d`** (episodes **335–336** and remaining prompts cleared at that time per session) |
| 2026-04-05 | **Topics** read state — **Mark episode read** / **Mark section read** / **Clear all read** (`localStorage`, commit `ba1388c`); **GitHub Pages** stuck **Loading…** — duplicate **`bullets`** in static **`loadBullets()`** (commit `5b6ef11`); handover: Whisper **`.json`** + **segments** vs repo **`.txt`** task stub |
| 2026-04-05 (later) | **Episodes** home **`/`**: consolidated **status** badge + **`status_label`**, **Sections**/**Bullets** DB columns then **unread** client counts, **Read** (blue) / **Partly read** / **Mark read**, **`loadBullets` `epGuid`** fix; **Topics**: **`pub_date` ASC**, auto **episode read** when all sections read, **no car/theme exclude** (removed **`/api/facets`**), single-episode **redirect to `/`**, removed **All topics · Episodes** subnav; **Pages**: **`/topics/`** + **`bullets.json`/`episodes.json`** only; unread **0** shows **`–`** in Episodes columns |
| 2026-04-06 | **Ingest pipeline** (RSS → Whisper on disk) vs **improvement pipeline** (`prepare-improved-agent`, **`Prompt to create improved JSON.txt`**, improved JSON, **`import-bullets`**, serve, **`export-github-pages`**) documented in **`.cursor/rules/pipelines.mdc`**, **HANDOVER.md**, cross-links in **transcript-storage.mdc** |
| 2026-04-06 | **`batch_whisper_transcribe.py`** runs **`ingest-transcripts`** at end of ingest ( **`--skip-ingest-transcripts`** to opt out); **README** / **pipelines** / **HANDOVER** aligned |
