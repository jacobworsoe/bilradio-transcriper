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
