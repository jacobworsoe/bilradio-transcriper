"""Build improved JSON from Whisper segment groups (extractive placeholders).

Use when transcript_plain_text has no paragraph breaks (single block). Each bullet
joins ``seg_per_bullet`` consecutive segments; ``bullets_per_section`` bullets per section.

Usage:
  .venv\\Scripts\\python.exe scripts\\improved_json_from_segments.py \\
    data\\transcripts\\<stem>.json data\\transcripts_improved\\<stem>.json \\
    --guid <uuid>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bilradio.transcript_text import whisper_segments_from_json  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Improved JSON from Whisper segments")
    p.add_argument("whisper_json", type=Path, help="Whisper full-result .json")
    p.add_argument("out_improved", type=Path, help="Output improved JSON path")
    p.add_argument("--guid", required=True, help="Episode guid for _bilradio_meta")
    p.add_argument("--stem", default="", help="Optional stem for meta (default: whisper stem)")
    p.add_argument("--seg-per-bullet", type=int, default=12, help="Segments per bullet")
    p.add_argument("--per-section", type=int, default=5, help="Bullets per section")
    p.add_argument("--max-text", type=int, default=420, help="Max chars per bullet text")
    args = p.parse_args()

    wpath = args.whisper_json.resolve()
    segs = whisper_segments_from_json(wpath)
    if not segs:
        print("No segments found.", file=sys.stderr)
        return 1

    stem = args.stem.strip() or wpath.stem
    n = len(segs)
    spb = max(1, args.seg_per_bullet)
    bps = max(1, args.per_section)
    max_t = max(80, args.max_text)

    bullets_flat: list[dict] = []
    for i in range(0, n, spb):
        chunk = segs[i : i + spb]
        raw = " ".join(
            str(s.get("text") or "").strip() for s in chunk if isinstance(s, dict)
        )
        raw = " ".join(raw.split())
        if len(raw) < 35:
            continue
        if len(raw) > max_t:
            raw = raw[: max_t - 1] + "…"
        bullets_flat.append(
            {
                "text": raw,
                "cars": [],
                "themes": ["bootstrap"],
                "uncertain": True,
                "start_sec": round(float(chunk[0]["start"]), 2),
                "end_sec": round(float(chunk[-1]["end"]), 2),
            }
        )

    if not bullets_flat:
        print("No bullets produced.", file=sys.stderr)
        return 1

    sections: list[dict] = []
    for si in range(0, len(bullets_flat), bps):
        chunk = bullets_flat[si : si + bps]
        sections.append(
            {
                "title": f"Del {len(sections) + 1}",
                "start_sec": round(min(b["start_sec"] for b in chunk), 2),
                "end_sec": round(max(b["end_sec"] for b in chunk), 2),
                "bullets": chunk,
            }
        )

    out = {
        "sections": sections,
        "_bilradio_meta": {
            "generator": "improved_json_from_segments",
            "replace_with_cursor_agent": True,
            "episode_guid": args.guid,
            "stem": stem,
            "seg_per_bullet": spb,
            "bullets_per_section": bps,
        },
    }
    out_path = args.out_improved.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {out_path} ({len(sections)} sections, {len(bullets_flat)} bullets, {n} segments)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
