"""Generate structured improved summaries under data/transcripts_improved/ via OpenAI API.

Uses :data:`bilradio.extract.CURSOR_INSTRUCTIONS` (sectioned bullets JSON), not a full verbatim transcript.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from bilradio.config import DB_PATH, ensure_data_dirs
from bilradio.db import connect
from bilradio.episode_paths import (
    episode_stem,
    has_non_empty_json,
    has_non_empty_txt,
    improved_transcript_json_path,
    whisper_transcript_json_path,
    whisper_transcript_txt_path,
)
from bilradio.extract import CURSOR_INSTRUCTIONS, parse_bullet_document_from_string
from bilradio.transcript_text import transcript_plain_text_from_file

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


def _resolve_transcript_path(guid: str, title: str, audio_path_db: str | None) -> Path | None:
    jp = whisper_transcript_json_path(guid, title, audio_path_db)
    tp = whisper_transcript_txt_path(guid, title, audio_path_db)
    if has_non_empty_json(jp):
        return jp
    if has_non_empty_txt(tp):
        return tp
    return None


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return (
        text[:max_chars]
        + "\n\n[Transcript truncated for API input limit; improve from this portion only.]",
        True,
    )


def _call_openai_bullets_json(transcript_plain: str) -> dict[str, Any]:
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in the environment.")

    model = (os.environ.get("BILRADIO_IMPROVE_MODEL") or "gpt-4o-mini").strip()
    max_in = int(os.environ.get("BILRADIO_IMPROVE_MAX_INPUT_CHARS", "140000"))

    body, truncated = _truncate(transcript_plain.strip(), max_in)
    system = CURSOR_INSTRUCTIONS.strip()
    user = (
        "Below is the Whisper-generated transcript for one Bilradio podcast episode. "
        "Follow the instructions exactly and respond with **one JSON object only** "
        '(the "sections" array shape), no markdown fences.\n\n---\n\n'
        + body
    )

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    with httpx.Client(timeout=httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)) as client:
        r = client.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = r.text[:800]
            except Exception:
                pass
            raise RuntimeError(f"OpenAI HTTP {r.status_code}: {detail}") from e

        data = r.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected OpenAI response shape: {data!r:.500}") from e

    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Empty model content.")

    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise RuntimeError("Model did not return a JSON object.")

    # Validate shape (sections + bullets)
    doc = parse_bullet_document_from_string(json.dumps(parsed))
    if doc.bullet_count < 1:
        raise RuntimeError("Parsed JSON has no bullets.")

    out = dict(parsed)
    raw_meta = out.pop("_bilradio_meta", None)
    meta: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    meta["model"] = model
    meta["input_truncated"] = truncated
    out["_bilradio_meta"] = meta
    return out


def improve_one_episode(
    guid: str,
    title: str,
    audio_path_db: str | None,
    *,
    force: bool,
) -> tuple[str, str]:
    """
    Returns (status, detail) where status is ok | skip | error.
    """
    ensure_data_dirs()
    out_path = improved_transcript_json_path(guid, title, audio_path_db)
    if out_path.exists() and not force:
        return "skip", f"exists: {out_path.name}"

    src = _resolve_transcript_path(guid, title, audio_path_db)
    if src is None:
        return "skip", "no whisper .json/.txt on disk"

    plain = transcript_plain_text_from_file(src)
    if not plain.strip():
        return "skip", "empty transcript text"

    try:
        payload = _call_openai_bullets_json(plain)
    except Exception as e:
        return "error", str(e)

    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return "ok", str(out_path)


def run_improve_batch(
    *,
    guid: str | None,
    force: bool,
    limit: int | None,
    pause_sec: float,
) -> tuple[int, int, int, list[str]]:
    """Returns (ok_count, skip_count, error_count, error_messages)."""
    ensure_data_dirs()
    errors: list[str] = []
    ok_c = skip_c = err_c = 0

    with connect(DB_PATH) as conn:
        q = "SELECT guid, title, audio_path FROM episodes ORDER BY pub_date ASC"
        args: tuple[Any, ...] = ()
        if guid:
            q = "SELECT guid, title, audio_path FROM episodes WHERE guid = ?"
            args = (guid,)
        rows = conn.execute(q, args).fetchall()

    n_rows = len(rows)
    for idx, row in enumerate(rows):
        if limit is not None and idx >= limit:
            break
        g = row["guid"]
        title = row["title"]
        ap = row["audio_path"]
        stem = episode_stem(g, title, ap)
        status, detail = improve_one_episode(g, title, ap, force=force)
        if status == "ok":
            ok_c += 1
            print(f"[ok] {stem}: {detail}", flush=True)
        elif status == "skip":
            skip_c += 1
            print(f"[skip] {stem}: {detail}", flush=True)
        else:
            err_c += 1
            msg = f"{stem}: {detail}"
            errors.append(msg)
            print(f"[error] {msg}", flush=True)

        if limit is not None:
            is_last = idx >= min(limit, n_rows) - 1
        else:
            is_last = idx >= n_rows - 1
        if pause_sec > 0 and not is_last:
            time.sleep(pause_sec)

    return ok_c, skip_c, err_c, errors
