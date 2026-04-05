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
from bilradio.time_format import format_time_range_bracket

_BASE = Path(__file__).resolve().parent

app = FastAPI(title="Bilradio topics")
app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(_BASE / "templates"))


def _norm(s: str) -> str:
    return s.strip().lower()


def _resolved_section_bounds(group: list[dict]) -> tuple[float | None, float | None]:
    ss = group[0].get("section_start_sec")
    es = group[0].get("section_end_sec")
    bs_list = [g["start_sec"] for g in group if g.get("start_sec") is not None]
    be_list = [g["end_sec"] for g in group if g.get("end_sec") is not None]
    if ss is None and bs_list:
        ss = min(bs_list)
    if es is None and be_list:
        es = max(be_list)
    return ss, es


def _apply_section_time_ranges(rows: list[dict]) -> None:
    i = 0
    while i < len(rows):
        j = i + 1
        key = (
            rows[i]["episode_guid"],
            rows[i]["section_order"],
            rows[i]["section_title"],
        )
        while (
            j < len(rows)
            and (
                rows[j]["episode_guid"],
                rows[j]["section_order"],
                rows[j]["section_title"],
            )
            == key
        ):
            j += 1
        group = rows[i:j]
        ss, es = _resolved_section_bounds(group)
        for g in group:
            g["section_start_sec"] = ss
            g["section_end_sec"] = es
            g["section_time_range"] = format_time_range_bracket(ss, es)
            bs, be = g.get("start_sec"), g.get("end_sec")
            if bs is not None or be is not None:
                g["bullet_time_range"] = format_time_range_bracket(bs, be)
            else:
                g["bullet_time_range"] = None
        i = j


# ---------------------------------------------------------------------------
# Topics view
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def home_episodes(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "episodes.html", {"static_site": False}
    )


@app.get("/topics", response_class=HTMLResponse)
@app.get("/topics/", response_class=HTMLResponse)
def topics_page(
    request: Request,
    guid: str | None = Query(default=None, alias="guid"),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "topics.html",
        {"filter_episode_guid": guid.strip() if guid else None},
    )


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
    episode_guid: str | None = Query(default=None),
) -> dict:
    ex_c = {_norm(x) for x in exclude_car if x and x.strip()}
    ex_t = {_norm(x) for x in exclude_theme if x and x.strip()}
    eg = episode_guid.strip() if episode_guid and episode_guid.strip() else None
    with connect(DB_PATH) as conn:
        sql = """
            SELECT b.id, b.text, b.cars, b.themes, b.uncertain,
                   b.start_sec AS start_sec,
                   b.end_sec AS end_sec,
                   e.title, e.pub_date, e.guid,
                   COALESCE(s.title, '') AS section_title,
                   COALESCE(s.sort_order, 0) AS section_order,
                   s.start_sec AS section_start_sec,
                   s.end_sec AS section_end_sec
            FROM topic_bullets b
            JOIN episodes e ON e.guid = b.episode_guid
            LEFT JOIN topic_sections s ON s.id = b.section_id
            WHERE e.status = 'extracted'
            """
        params: list[str] = []
        if eg:
            sql += " AND e.guid = ?"
            params.append(eg)
        sql += """
            ORDER BY e.pub_date DESC,
                     COALESCE(s.sort_order, 0),
                     b.id ASC
            """
        rows = conn.execute(sql, params if params else ()).fetchall()
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
                "start_sec": r["start_sec"],
                "end_sec": r["end_sec"],
                "section_start_sec": r["section_start_sec"],
                "section_end_sec": r["section_end_sec"],
            }
        )
    _apply_section_time_ranges(out)
    return {"bullets": out, "count": len(out)}


# ---------------------------------------------------------------------------
# Episodes status (read-only + RSS sync + clear error)
# ---------------------------------------------------------------------------


@app.get("/queue")
def queue_redirect() -> RedirectResponse:
    return RedirectResponse(url="/", status_code=301)


@app.get("/episodes")
def episodes_legacy_redirect() -> RedirectResponse:
    return RedirectResponse(url="/", status_code=301)


def _whisper_disk_phrase(has_json: bool, has_txt: bool) -> str:
    if has_json and has_txt:
        return "Whisper (JSON+TXT)"
    if has_json:
        return "Whisper (JSON)"
    if has_txt:
        return "Whisper (TXT)"
    return "Whisper"


def _disk_vs_db_note(db_status: str, downloaded: bool) -> str | None:
    """Yellow status note when on-disk transcripts disagree with SQLite row."""
    if db_status == "error":
        return (
            "A transcript file exists on disk; the database error is stale. "
            'Click "Ingest transcripts" to update the database, or clear the error if you prefer.'
        )
    if db_status == "downloaded" and not downloaded:
        return (
            "Transcript on disk but audio path missing or file not found; check download / DB audio_path."
        )
    return None


def episode_badge_for_row(
    db_status: str,
    *,
    downloaded: bool,
    has_whisper_json: bool,
    has_whisper_txt: bool,
    has_improved: bool,
    bullet_count: int,
) -> tuple[str, str, str | None]:
    """
    Episodes table: one badge text + optional note. Returns
    (display_status_key, badge_label, status_note).

    ``display_status`` selects CSS class; ``badge_label`` is the full English badge text.
    """
    transcript_on_disk = has_whisper_json or has_whisper_txt
    wl = _whisper_disk_phrase(has_whisper_json, has_whisper_txt)

    if db_status == "skipped_short":
        return "skipped_short", "Skipped (short)", None

    if db_status == "extracted" or bullet_count > 0:
        if downloaded and transcript_on_disk and has_improved:
            return "summarized", "Summarized", None
        problems: list[str] = []
        if not downloaded:
            problems.append("no audio on disk")
        if not transcript_on_disk:
            problems.append("Whisper transcript missing on disk")
        if not has_improved:
            problems.append("improved JSON missing on disk")
        suffix = f" — {'; '.join(problems)}" if problems else ""
        return "summarized", "Summarized" + suffix, None

    if db_status == "transcribed" and not transcript_on_disk:
        return (
            "transcribed",
            "Transcribed in DB — Whisper transcript missing on disk",
            None,
        )

    if db_status == "error" and not transcript_on_disk:
        return "error", "Error — no Whisper transcript on disk", None

    if transcript_on_disk:
        note = None
        if db_status in ("downloaded", "error"):
            note = _disk_vs_db_note(db_status, downloaded)
        if has_improved and bullet_count == 0:
            return (
                "import_pending",
                f"Import pending — {wl} + improved JSON on disk",
                note,
            )
        return (
            "transcribed",
            f"Transcribed — {wl}; no improved JSON on disk yet",
            note,
        )

    if downloaded:
        return (
            "downloaded",
            "Downloaded — audio on disk, no Whisper transcript yet",
            None,
        )

    if db_status == "pending":
        return "pending", "Pending — no audio on disk", None

    return "pending", "Pending", None


@app.get("/api/episodes")
def api_episodes() -> dict:
    with connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT guid, title, pub_date, duration_sec, status, error, audio_path, extract_at,
                   (SELECT count(*) FROM topic_bullets WHERE episode_guid = e.guid) AS bullet_count,
                   (SELECT count(*) FROM topic_sections WHERE episode_guid = e.guid) AS section_count
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
        bcount = int(r["bullet_count"] or 0)
        disp, label, note = episode_badge_for_row(
            st,
            downloaded=downloaded,
            has_whisper_json=has_whisper_json,
            has_whisper_txt=has_whisper_txt,
            has_improved=has_improved,
            bullet_count=bcount,
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
                "status_label": label,
                "status_note": note,
                "error": r["error"],
                "extract_at": r["extract_at"],
                "bullet_count": bcount,
                "section_count": int(r["section_count"] or 0),
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
