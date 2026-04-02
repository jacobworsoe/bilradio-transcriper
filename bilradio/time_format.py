"""Format second offsets for Topics UI (English numerals)."""
from __future__ import annotations


def format_timecode(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(max(0.0, float(seconds)))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def format_time_range_bracket(start: float | None, end: float | None) -> str:
    return f"[{format_timecode(start)} – {format_timecode(end)}]"
