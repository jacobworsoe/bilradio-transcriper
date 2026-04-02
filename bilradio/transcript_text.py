"""Read plain transcript text from Whisper .txt or full-result .json."""
from __future__ import annotations

import json
from pathlib import Path


def transcript_plain_text_from_file(path: Path) -> str:
    if path.suffix.lower() == ".json":
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return ""
        if not isinstance(data, dict):
            return ""
        t = data.get("text")
        if isinstance(t, str) and t.strip():
            return t.strip()
        segments = data.get("segments")
        if isinstance(segments, list):
            parts: list[str] = []
            for seg in segments:
                if isinstance(seg, dict):
                    tx = seg.get("text")
                    if isinstance(tx, str) and tx.strip():
                        parts.append(tx.strip())
            return "\n".join(parts)
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def whisper_segments_from_json(path: Path) -> list[dict[str, float | str]]:
    """Return Whisper ``segments`` with ``start``, ``end`` (floats) and ``text``; empty if not JSON."""
    if path.suffix.lower() != ".json":
        return []
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    segments = data.get("segments")
    if not isinstance(segments, list):
        return []
    out: list[dict[str, float | str]] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        try:
            st = float(seg["start"])
            en = float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        tx = seg.get("text")
        out.append(
            {
                "start": st,
                "end": en,
                "text": tx.strip() if isinstance(tx, str) else "",
            }
        )
    return out
