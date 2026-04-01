from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bilradio.config import DB_PATH
from bilradio.db import connect, parse_json_list
from bilradio.episode_paths import (
    expected_audio_path,
    has_non_empty_json,
    has_non_empty_txt,
    improved_transcript_json_path,
    whisper_transcript_json_path,
    whisper_transcript_txt_path,
)
from bilradio.pipeline import step_clear_episode_error, step_ingest_transcripts

_BASE = Path(__file__).resolve().parent

app = FastAPI(title="Bilradio topics")
app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(_BASE / "templates"))


def _norm(s: str) -> str:
    return s.strip().lower()


# ---------------------------------------------------------------------------
# Topics view
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/api/facets")
def api_facets() -> dict:
    with connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT b.cars, b.themes FROM topic_bullets b
            JOIN episodes e ON e.guid = b.episode_guid
            WHERE e.status = 'extracted'
            """
        ).fetchall()
    cars: set[str] = set()
    themes: set[str] = set()
    for r in rows:
        for c in parse_json_list(r["cars"]):
            if c.strip():
                cars.add(c.strip())
        for t in parse_json_list(r["themes"]):
            if t.strip():
                themes.add(t.strip())
    return {
        "cars": sorted(cars, key=lambda x: x.lower()),
        "themes": sorted(themes, key=lambda x: x.lower()),
    }


@app.get("/api/bullets")
def api_bullets(
    exclude_car: list[str] = Query(default=[]),
    exclude_theme: list[str] = Query(default=[]),
) -> dict:
    ex_c = {_norm(x) for x in exclude_car if x and x.strip()}
    ex_t = {_norm(x) for x in exclude_theme if x and x.strip()}
    with connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT b.id, b.text, b.cars, b.themes, b.uncertain,
                   e.title, e.pub_date, e.guid,
                   COALESCE(s.title, '') AS section_title,
                   COALESCE(s.sort_order, 0) AS section_order
            FROM topic_bullets b
            JOIN episodes e ON e.guid = b.episode_guid
            LEFT JOIN topic_sections s ON s.id = b.section_id
            WHERE e.status = 'extracted'
            ORDER BY e.pub_date DESC,
                     COALESCE(s.sort_order, 0),
                     b.id ASC
            """
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        cars = parse_json_list(r["cars"])
        themes = parse_json_list(r["themes"])
        if ex_c and any(_norm(c) in ex_c for c in cars):
            continue
        if ex_t and any(_norm(t) in ex_t for t in themes):
            continue
        out.append(
            {
                "id": r["id"],
                "text": r["text"],
                "cars": cars,
                "themes": themes,
                "uncertain": bool(r["uncertain"]),
                "episode_title": r["title"],
                "episode_guid": r["guid"],
                "pub_date": r["pub_date"],
                "section_title": r["section_title"] or "",
                "section_order": int(r["section_order"] or 0),
            }
        )
    return {"bullets": out, "count": len(out)}


# ---------------------------------------------------------------------------
# Episodes status (read-only + RSS sync + clear error)
# ---------------------------------------------------------------------------


@app.get("/queue")
def queue_redirect() -> RedirectResponse:
    return RedirectResponse(url="/episodes", status_code=301)


@app.get("/episodes", response_class=HTMLResponse)
def episodes_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "episodes.html", {})


def _effective_display_status(
    db_status: str,
    *,
    downloaded: bool,
    transcript_on_disk: bool,
) -> tuple[str, str | None]:
    """
    Badge status for the Episodes page. Maps DB ``extracted`` → ``summarized`` (bullets in SQLite).
    When a Whisper file exists on disk but DB still says ``error`` (or ``downloaded``), show
    ``transcribed`` and a short note — do not require audio on disk for the error override.
    """
    if db_status == "extracted":
        return "summarized", None
    if db_status == "transcribed":
        return db_status, None
    if transcript_on_disk and db_status in ("downloaded", "error"):
        note = None
        if db_status == "error":
            note = (
                "A transcript file exists on disk; the database error is stale. "
                "Click “Ingest transcripts” to update the database, or clear the error if you prefer."
            )
        elif db_status == "downloaded" and not downloaded:
            note = (
                "Transcript on disk but audio path missing or file not found; check download / DB audio_path."
            )
        return "transcribed", note
    return db_status, None


@app.get("/api/episodes")
def api_episodes() -> dict:
    with connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT guid, title, pub_date, duration_sec, status, error, audio_path, extract_at,
                   (SELECT count(*) FROM topic_bullets WHERE episode_guid = e.guid) AS bullet_count
            FROM episodes e
            ORDER BY pub_date ASC
            """
        ).fetchall()

    episodes: list[dict] = []
    counts: dict[str, int] = {}
    for r in rows:
        guid = r["guid"]
        title = r["title"]
        ap = r["audio_path"]
        audio_path = expected_audio_path(guid, title, ap)
        wjson = whisper_transcript_json_path(guid, title, ap)
        wtxt = whisper_transcript_txt_path(guid, title, ap)
        ij = improved_transcript_json_path(guid, title, ap)
        downloaded = audio_path.is_file()
        has_whisper_json = has_non_empty_json(wjson)
        has_whisper_txt = has_non_empty_txt(wtxt)
        transcript_on_disk = has_whisper_json or has_whisper_txt
        has_improved = has_non_empty_json(ij)

        st = r["status"]
        disp, note = _effective_display_status(
            st, downloaded=downloaded, transcript_on_disk=transcript_on_disk
        )
        counts[disp] = counts.get(disp, 0) + 1

        episodes.append(
            {
                "guid": guid,
                "title": title,
                "pub_date": r["pub_date"],
                "duration_sec": r["duration_sec"],
                "status": st,
                "display_status": disp,
                "status_note": note,
                "error": r["error"],
                "extract_at": r["extract_at"],
                "bullet_count": r["bullet_count"],
                "downloaded": downloaded,
                "whisper_json": has_whisper_json,
                "whisper_txt": has_whisper_txt,
                "improved_transcript": has_improved,
            }
        )

    return {"episodes": episodes, "counts": counts}


@app.post("/api/episodes/sync")
def api_episodes_sync() -> JSONResponse:
    from bilradio.pipeline import sync_episodes_from_rss

    n = sync_episodes_from_rss()
    return JSONResponse({"upserted": n})


@app.post("/api/episodes/ingest-transcripts")
def api_episodes_ingest_transcripts() -> JSONResponse:
    """Promote downloaded/error rows to transcribed when JSON or .txt exists (matches CLI ingest)."""
    n, skipped = step_ingest_transcripts(None)
    return JSONResponse({"ingested": n, "skipped": skipped})


@app.post("/api/episodes/clear-error/{guid}")
def api_episodes_clear_error(guid: str) -> JSONResponse:
    ok, msg = step_clear_episode_error(guid)
    if not ok:
        code = 404 if msg == "Episode not found." else 400
        raise HTTPException(status_code=code, detail=msg)
    return JSONResponse({"cleared": guid})
