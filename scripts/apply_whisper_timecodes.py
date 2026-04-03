"""Fill start_sec/end_sec on each **section** of improved JSON from Whisper segments.

Slices the segment timeline proportionally by section, weighted by bullet count in each
section. Removes any per-bullet start_sec/end_sec so output matches CURSOR_INSTRUCTIONS
(section-level timecodes only).

Usage (repo root):
  .venv\\Scripts\\python.exe scripts\\apply_whisper_timecodes.py data\\transcripts\\<stem>.json data\\transcripts_improved\\<stem>.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: apply_whisper_timecodes.py <whisper.json> <improved.json>", file=sys.stderr)
        return 2
    whisper_path = Path(sys.argv[1]).resolve()
    improved_path = Path(sys.argv[2]).resolve()
    wj = json.loads(whisper_path.read_text(encoding="utf-8"))
    segs = [
        s
        for s in (wj.get("segments") or [])
        if isinstance(s, dict) and "start" in s and "end" in s
    ]
    n = len(segs)
    if n == 0:
        print("No segments in Whisper JSON.", file=sys.stderr)
        return 1

    doc = json.loads(improved_path.read_text(encoding="utf-8"))
    sections = doc.get("sections")
    if not isinstance(sections, list):
        print("Improved JSON has no sections list.", file=sys.stderr)
        return 1

    counts: list[int] = []
    for sec in sections:
        if not isinstance(sec, dict):
            counts.append(0)
            continue
        bullets = sec.get("bullets") or []
        counts.append(len(bullets) if isinstance(bullets, list) else 0)

    total_b = sum(counts)
    if total_b == 0:
        print("No bullets to anchor sections.", file=sys.stderr)
        return 1

    cum = 0
    for si, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue
        k = counts[si] if si < len(counts) else 0
        if k == 0:
            continue
        seg_i0 = cum * n // total_b
        cum += k
        seg_i1 = cum * n // total_b
        if seg_i1 <= seg_i0:
            seg_i1 = min(seg_i0 + 1, n)
        seg_i1 = min(seg_i1, n)
        st = round(float(segs[seg_i0]["start"]), 2)
        en = round(float(segs[seg_i1 - 1]["end"]), 2)
        sec["start_sec"] = st
        sec["end_sec"] = en
        bullets = sec.get("bullets")
        if isinstance(bullets, list):
            for b in bullets:
                if isinstance(b, dict):
                    b.pop("start_sec", None)
                    b.pop("end_sec", None)

    meta = doc.get("_bilradio_meta")
    if isinstance(meta, dict):
        meta["timecodes_applied"] = "whisper_segments_proportional_by_section"
    else:
        doc["_bilradio_meta"] = {
            "timecodes_applied": "whisper_segments_proportional_by_section",
        }

    improved_path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {improved_path} ({len(sections)} sections, {total_b} bullets, {n} segments; section times only)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
