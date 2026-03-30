from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bilradio.config import (
    DATA_DIR,
    DB_PATH,
    QUEUE_RUNNER_PID_FILE,
    WHISPER_CURRENT_GUID_FILE,
)
from bilradio.db import connect, parse_json_list
from bilradio.pipeline import step_clear_episode_error
from bilradio.whisper_run import WHISPER_HEARTBEAT_FILE, WHISPER_PID_FILE

_BASE = Path(__file__).resolve().parent
# Repository root (parent of the `bilradio` package); stable cwd for background workers.
_REPO_ROOT = Path(__file__).resolve().parents[2]

app = FastAPI(title="Bilradio topics")
app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(_BASE / "templates"))


def _norm(s: str) -> str:
    return s.strip().lower()


# ---------------------------------------------------------------------------
# Process-state helpers
# ---------------------------------------------------------------------------

def _read_pid_file(path: Path) -> int | None:
    """Read a PID file and return the int PID if the process is alive, else None."""
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _read_heartbeat() -> dict[str, str]:
    try:
        data: dict[str, str] = {}
        for line in WHISPER_HEARTBEAT_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                data[k.strip()] = v.strip()
        return data
    except OSError:
        return {}


def _kill_pid(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], check=False)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _start_subprocess(args: list[str]) -> int:
    """Launch a detached background subprocess; return its PID."""
    if sys.platform == "win32":
        proc = subprocess.Popen(
            args,
            cwd=str(_REPO_ROOT),
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    else:
        proc = subprocess.Popen(
            args,
            cwd=str(_REPO_ROOT),
            close_fds=True,
            start_new_session=True,
        )
    return proc.pid


def _venv_python() -> str:
    return sys.executable


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
                   e.title, e.pub_date, e.guid
            FROM topic_bullets b
            JOIN episodes e ON e.guid = b.episode_guid
            WHERE e.status = 'extracted'
            ORDER BY e.pub_date DESC, b.id ASC
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
            }
        )
    return {"bullets": out, "count": len(out)}


# ---------------------------------------------------------------------------
# Queue view
# ---------------------------------------------------------------------------

@app.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "queue.html", {})


@app.get("/api/queue")
def api_queue() -> dict:
    with connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT guid, title, pub_date, duration_sec, status, error,
                   extract_at,
                   (SELECT count(*) FROM topic_bullets WHERE episode_guid = e.guid) AS bullet_count
            FROM episodes e
            ORDER BY pub_date ASC
            """
        ).fetchall()

    episodes = [
        {
            "guid": r["guid"],
            "title": r["title"],
            "pub_date": r["pub_date"],
            "duration_sec": r["duration_sec"],
            "status": r["status"],
            "error": r["error"],
            "extract_at": r["extract_at"],
            "bullet_count": r["bullet_count"],
        }
        for r in rows
    ]

    counts: dict[str, int] = {}
    for ep in episodes:
        counts[ep["status"]] = counts.get(ep["status"], 0) + 1

    whisper_pid = _read_pid_file(WHISPER_PID_FILE)
    queue_runner_pid = _read_pid_file(QUEUE_RUNNER_PID_FILE)
    current_guid: str | None = None
    try:
        current_guid = WHISPER_CURRENT_GUID_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        pass

    return {
        "episodes": episodes,
        "counts": counts,
        "whisper_pid": whisper_pid,
        "queue_runner_pid": queue_runner_pid,
        "current_transcribing_guid": current_guid,
        "heartbeat": _read_heartbeat(),
    }


@app.post("/api/queue/start")
def api_queue_start() -> JSONResponse:
    """Start bilradio run-queue as a background process (sync + download all + transcribe all)."""
    existing = _read_pid_file(QUEUE_RUNNER_PID_FILE)
    if existing:
        raise HTTPException(status_code=409, detail=f"Queue runner already running (PID {existing}).")
    pid = _start_subprocess([_venv_python(), "-m", "bilradio.cli", "run-queue"])
    try:
        QUEUE_RUNNER_PID_FILE.write_text(str(pid), encoding="utf-8")
    except OSError:
        pass
    return JSONResponse({"started": pid, "mode": "run-queue"})


@app.post("/api/queue/stop")
def api_queue_stop() -> JSONResponse:
    """Stop the queue runner (and any Whisper child) immediately."""
    runner_pid = _read_pid_file(QUEUE_RUNNER_PID_FILE)
    whisper_pid = _read_pid_file(WHISPER_PID_FILE)
    killed: list[int] = []
    if runner_pid:
        _kill_pid(runner_pid)
        killed.append(runner_pid)
        try:
            QUEUE_RUNNER_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    if whisper_pid and whisper_pid not in killed:
        _kill_pid(whisper_pid)
        killed.append(whisper_pid)
        try:
            WHISPER_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        WHISPER_CURRENT_GUID_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    if not killed:
        raise HTTPException(status_code=404, detail="No active queue runner or Whisper process found.")
    return JSONResponse({"stopped": killed})


@app.post("/api/queue/clear-error/{guid}")
def api_queue_clear_error(guid: str) -> JSONResponse:
    """Clear stored error text and set episode back to downloaded (retry transcription)."""
    if _read_pid_file(QUEUE_RUNNER_PID_FILE):
        raise HTTPException(
            status_code=409,
            detail="Stop the queue runner before clearing errors.",
        )
    ok, msg = step_clear_episode_error(guid)
    if not ok:
        code = 404 if msg == "Episode not found." else 400
        raise HTTPException(status_code=code, detail=msg)
    return JSONResponse({"cleared": guid})


@app.post("/api/queue/process/{guid}")
def api_queue_process_episode(guid: str) -> JSONResponse:
    """Download (if needed) and transcribe a single episode in the background."""
    existing = _read_pid_file(QUEUE_RUNNER_PID_FILE)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Queue runner already running (PID {existing}). Stop it first or let it finish.",
        )
    with connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT guid, status FROM episodes WHERE guid = ?", (guid,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Episode {guid!r} not found.")
    # Use 'pipeline' CLI which handles download + transcribe + prepare-extract
    pid = _start_subprocess([_venv_python(), "-m", "bilradio.cli", "pipeline", "--guid", guid])
    try:
        QUEUE_RUNNER_PID_FILE.write_text(str(pid), encoding="utf-8")
    except OSError:
        pass
    return JSONResponse({"started": pid, "guid": guid, "mode": "pipeline"})


@app.post("/api/queue/kill-whisper")
def api_kill_whisper() -> JSONResponse:
    """Kill only the Whisper subprocess; the queue runner continues to the next episode."""
    pid = _read_pid_file(WHISPER_PID_FILE)
    if pid is None:
        raise HTTPException(status_code=404, detail="No active Whisper process found.")
    _kill_pid(pid)
    try:
        WHISPER_PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    return JSONResponse({"killed": pid})


@app.post("/api/queue/sync")
def api_queue_sync() -> JSONResponse:
    """Sync RSS feed and discover new episodes (fast, non-blocking)."""
    from bilradio.pipeline import sync_episodes_from_rss
    n = sync_episodes_from_rss()
    return JSONResponse({"upserted": n})
