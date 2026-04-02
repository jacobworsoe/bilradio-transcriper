"""Extractive placeholders for data/transcripts_improved/*.json (replace with Cursor Auto Agent output)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from bilradio.config import DB_PATH, ensure_data_dirs
from bilradio.db import connect
from bilradio.episode_paths import (
    episode_stem,
    has_non_empty_json,
    improved_transcript_json_path,
    whisper_transcript_json_path,
    whisper_transcript_txt_path,
)
from bilradio.transcript_text import transcript_plain_text_from_file, whisper_segments_from_json


def _paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n+", text.strip())
    out: list[str] = []
    for p in parts:
        line = " ".join(p.split())
        if len(line) >= 60:
            out.append(line)
    return out


def _chunk_paragraphs(ps: list[str], per_section: int = 4, max_sections: int = 10) -> list[list[str]]:
    chunks: list[list[str]] = []
    i = 0
    while i < len(ps) and len(chunks) < max_sections:
        chunks.append(ps[i : i + per_section])
        i += per_section
    return chunks


def write_bootstrap_improved(
    *,
    guid: str | None,
    force: bool,
    limit: int | None,
) -> tuple[int, int, int]:
    """Returns (written, skipped_no_transcript, skipped_exists)."""
    ensure_data_dirs()
    w = sk_nt = sk_ex = 0

    with connect(DB_PATH) as conn:
        q = "SELECT guid, title, audio_path FROM episodes ORDER BY pub_date ASC"
        args: tuple = ()
        if guid:
            q = "SELECT guid, title, audio_path FROM episodes WHERE guid = ?"
            args = (guid,)
        rows = conn.execute(q, args).fetchall()

    for idx, row in enumerate(rows):
        if limit is not None and idx >= limit:
            break
        g = row["guid"]
        title = row["title"]
        ap = row["audio_path"]
        stem = episode_stem(g, title, ap)
        out_path = improved_transcript_json_path(g, title, ap)
        if out_path.exists() and not force:
            sk_ex += 1
            continue
        jp = whisper_transcript_json_path(g, title, ap)
        tp = whisper_transcript_txt_path(g, title, ap)
        src: Path | None = None
        if has_non_empty_json(jp):
            src = jp
        elif tp.is_file() and tp.stat().st_size > 0:
            src = tp
        if src is None:
            sk_nt += 1
            continue

        plain = transcript_plain_text_from_file(src)
        if not plain.strip():
            sk_nt += 1
            continue

        paras = _paragraphs(plain)
        if len(paras) < 3:
            paras = [plain[:2000] + ("…" if len(plain) > 2000 else "")]

        section_chunks = _chunk_paragraphs(paras, per_section=5, max_sections=8)
        segments = whisper_segments_from_json(jp) if has_non_empty_json(jp) else []
        n_seg = len(segments)

        def bullet_times(idx_in_section: int, n_in_section: int) -> tuple[float | None, float | None]:
            if n_seg == 0 or n_in_section <= 0:
                return None, None
            a = idx_in_section * n_seg // n_in_section
            b = (idx_in_section + 1) * n_seg // n_in_section
            b = max(b, a + 1)
            chunk = segments[a : min(b, n_seg)]
            if not chunk:
                return None, None
            return float(chunk[0]["start"]), float(chunk[-1]["end"])

        sections: list[dict] = []
        for si, chunk in enumerate(section_chunks):
            bullets: list[dict] = []
            chunk_bullets = chunk[:5]
            n_b = len(chunk_bullets)
            for bi, para in enumerate(chunk_bullets):
                txt = para[:320] + ("…" if len(para) > 320 else "")
                bs, be = bullet_times(bi, n_b)
                bullets.append(
                    {
                        "text": txt,
                        "cars": [],
                        "themes": ["bootstrap"],
                        "uncertain": True,
                        **({"start_sec": bs, "end_sec": be} if bs is not None and be is not None else {}),
                    }
                )
            if bullets:
                sec_payload: dict = {
                    "title": f"Del {si + 1}",
                    "bullets": bullets,
                }
                b_starts = [b["start_sec"] for b in bullets if b.get("start_sec") is not None]
                b_ends = [b["end_sec"] for b in bullets if b.get("end_sec") is not None]
                if b_starts and b_ends:
                    sec_payload["start_sec"] = min(b_starts)
                    sec_payload["end_sec"] = max(b_ends)
                sections.append(sec_payload)

        if not sections:
            sk_nt += 1
            continue

        payload = {
            "sections": sections,
            "_bilradio_meta": {
                "generator": "bootstrap_improved",
                "replace_with_cursor_agent": True,
                "episode_guid": g,
                "stem": stem,
            },
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        w += 1

    return w, sk_nt, sk_ex
