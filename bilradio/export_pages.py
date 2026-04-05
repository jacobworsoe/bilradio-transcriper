"""Build a static tree under ``docs/`` for GitHub Pages from the current SQLite database."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.testclient import TestClient

from bilradio.config import DB_PATH, ensure_data_dirs
from bilradio.db import init_db
from bilradio.web.app import app

_WEB = Path(__file__).resolve().parent / "web"
_TEMPLATES = _WEB / "templates"
_STATIC = _WEB / "static"


def export_github_pages(
    dest: Path,
    *,
    cname: str = "bilradio.jacobworsoe.dk",
) -> None:
    """
    Write static HTML, ``/api/*.json``, and assets to ``dest`` for GitHub Pages.

    Creates an empty database with ``init_db`` if ``DB_PATH`` is missing so CI
    or a fresh clone can still emit a valid empty site.
    """
    ensure_data_dirs()
    if not DB_PATH.is_file():
        init_db(DB_PATH)

    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    api_dir = dest / "api"
    api_dir.mkdir(exist_ok=True)
    static_out = dest / "static"
    static_out.mkdir(exist_ok=True)

    with TestClient(app) as client:
        facets = client.get("/api/facets").json()
        bullets = client.get("/api/bullets").json()
        episodes = client.get("/api/episodes").json()

    (api_dir / "facets.json").write_text(
        json.dumps(facets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (api_dir / "bullets.json").write_text(
        json.dumps(bullets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (api_dir / "episodes.json").write_text(
        json.dumps(episodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    ctx = {"static_site": True}
    (dest / "index.html").write_text(
        env.get_template("episodes.html").render(**ctx), encoding="utf-8"
    )
    topics_dir = dest / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    (topics_dir / "index.html").write_text(
        env.get_template("topics.html").render(**ctx, filter_episode_guid=None),
        encoding="utf-8",
    )
    (dest / "episodes.html").write_text(
        """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta http-equiv="refresh" content="0;url=/" />
  <link rel="canonical" href="/" />
  <title>Moved — Bilradio</title>
</head>
<body>
  <p>This page moved to the <a href="/">home page</a>.</p>
</body>
</html>
""",
        encoding="utf-8",
    )

    for name in ("style.css",):
        src = _STATIC / name
        if src.is_file():
            shutil.copy2(src, static_out / name)

    (dest / "CNAME").write_text(cname.strip() + "\n", encoding="utf-8")
    (dest / ".nojekyll").write_text("", encoding="utf-8")
