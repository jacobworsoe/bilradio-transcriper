"""Fill start_sec/end_sec on improved JSON from Whisper segment indices (proportional by bullet count).

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

    flat: list[tuple[int, int]] = []
    for si, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue
        bullets = sec.get("bullets") or []
        if not isinstance(bullets, list):
            continue
        for bi, _b in enumerate(bullets):
            flat.append((si, bi))

    b_count = len(flat)
    if b_count == 0:
        print("No bullets to annotate.", file=sys.stderr)
        return 1

    for idx, (si, bi) in enumerate(flat):
        seg_a = idx * n // b_count
        seg_b = (idx + 1) * n // b_count
        if seg_b <= seg_a:
            seg_b = min(seg_a + 1, n)
        seg_b = min(seg_b, n)
        bullet = sections[si]["bullets"][bi]
        if not isinstance(bullet, dict):
            continue
        st = round(float(segs[seg_a]["start"]), 2)
        en = round(float(segs[seg_b - 1]["end"]), 2)
        bullet["start_sec"] = st
        bullet["end_sec"] = en

    for sec in sections:
        if not isinstance(sec, dict):
            continue
        bullets = sec.get("bullets") or []
        if not isinstance(bullets, list):
            continue
        starts: list[float] = []
        ends: list[float] = []
        for b in bullets:
            if isinstance(b, dict) and "start_sec" in b and "end_sec" in b:
                starts.append(float(b["start_sec"]))
                ends.append(float(b["end_sec"]))
        if starts and ends:
            sec["start_sec"] = round(min(starts), 2)
            sec["end_sec"] = round(max(ends), 2)

    meta = doc.get("_bilradio_meta")
    if isinstance(meta, dict):
        meta["timecodes_applied"] = "whisper_segments_proportional_by_bullet_index"
    else:
        doc["_bilradio_meta"] = {
            "timecodes_applied": "whisper_segments_proportional_by_bullet_index",
        }

    improved_path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {improved_path} ({b_count} bullets, {n} segments)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
