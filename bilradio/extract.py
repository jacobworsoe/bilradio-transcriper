from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bilradio.config import CURSOR_INBOX_DIR, TRANSCRIPT_CHUNK_CHARS, ensure_data_dirs

CURSOR_INSTRUCTIONS = """Process this file.

You are an expert transcription editor for the Danish podcast Bilradio (biler, mobilitet, Danmark).

Your task is to clean, structure, and summarize Whisper-generated transcripts.
Do not invent content. Preserve factual accuracy.
Flag uncertain corrections instead of guessing silently.
Correct obvious transcription errors.
Use context to infer the most likely wording.

Organize the episode into **clear thematic sections** (e.g. intro, main topics, tests, closing).
For **each section**, produce **3–5 topic bullets** for the web app (not a full transcript for reading).

Respond with **JSON only** (no markdown fences), in this exact shape:
{
  "sections": [
    {
      "title": "Kort dansk sektionstitel",
      "bullets": [
        {
          "text": "Kort bullet på dansk (én sætning).",
          "cars": ["mærke model eller tom liste"],
          "themes": ["fx elbil", "leasing"],
          "uncertain": false
        }
      ]
    }
  ]
}

Rules:
- **sections**: 1–12 sections per episode; each section must have **3–5 bullets** (merge or split if needed).
- **title**: short Danish section heading.
- **text**: one concise Danish sentence per bullet.
- **cars**: concrete makes/models for that bullet, or [] if none.
- **themes**: 0–3 short labels for filtering (e.g. elbil, leasing, brændstofpriser).
- **uncertain**: true if the point is weakly supported in the audio/text.
- Merge overlapping bullets; avoid duplicate facts across sections.
"""


def _normalize_bullet_dict(b: dict[str, Any]) -> dict[str, Any] | None:
    text = str(b.get("text", "")).strip()
    if not text:
        return None
    cars = b.get("cars") or []
    themes = b.get("themes") or []
    if not isinstance(cars, list):
        cars = []
    if not isinstance(themes, list):
        themes = []
    cars = [str(c).strip() for c in cars if str(c).strip()]
    themes = [str(t).strip() for t in themes if str(t).strip()]
    uncertain = bool(b.get("uncertain", False))
    return {
        "text": text,
        "cars": cars,
        "themes": themes,
        "uncertain": uncertain,
    }


@dataclass(frozen=True)
class BulletSection:
    title: str
    bullets: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class BulletDocument:
    sections: tuple[BulletSection, ...]

    @property
    def bullet_count(self) -> int:
        return sum(len(s.bullets) for s in self.sections)


def _parse_bullet_json_raw(data: dict[str, Any]) -> BulletDocument:
    if "sections" in data and isinstance(data["sections"], list):
        out_sections: list[BulletSection] = []
        for sec in data["sections"]:
            if not isinstance(sec, dict):
                continue
            title = str(sec.get("title", "")).strip() or "Episode"
            raw_bullets = sec.get("bullets")
            if not isinstance(raw_bullets, list):
                continue
            items: list[dict[str, Any]] = []
            for b in raw_bullets:
                if isinstance(b, dict):
                    nb = _normalize_bullet_dict(b)
                    if nb:
                        items.append(nb)
            if items:
                out_sections.append(BulletSection(title=title, bullets=tuple(items)))
        if out_sections:
            return BulletDocument(sections=tuple(out_sections))

    # Legacy: flat bullets list
    bullets = data.get("bullets")
    if not isinstance(bullets, list):
        return BulletDocument(sections=())
    items: list[dict[str, Any]] = []
    for b in bullets:
        if isinstance(b, dict):
            nb = _normalize_bullet_dict(b)
            if nb:
                items.append(nb)
    if not items:
        return BulletDocument(sections=())
    return BulletDocument(
        sections=(BulletSection(title="Episode", bullets=tuple(items)),)
    )


def parse_bullet_document_from_string(raw: str) -> BulletDocument:
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s)
    data = json.loads(s)
    if not isinstance(data, dict):
        return BulletDocument(sections=())
    return _parse_bullet_json_raw(data)


def load_bullet_document_from_json_path(path: Path) -> BulletDocument:
    return parse_bullet_document_from_string(
        path.read_text(encoding="utf-8", errors="replace")
    )


def load_bullets_from_json_path(path: Path) -> list[dict[str, Any]]:
    """Flatten all bullets (legacy API for callers that expect a list)."""
    doc = load_bullet_document_from_json_path(path)
    out: list[dict[str, Any]] = []
    for sec in doc.sections:
        out.extend(sec.bullets)
    return out


def chunk_transcript(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            nl = text.rfind("\n", start + max_chars // 2, end)
            if nl != -1:
                end = nl + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks


def write_cursor_inbox(
    guid: str,
    title: str,
    transcript: str,
    *,
    inbox_dir: Path | None = None,
) -> Path:
    """
    Write transcript + a Markdown brief for Cursor (Composer/Chat) with instructions.
    User works in Cursor on these files, then saves JSON to <guid>.bullets.json
    """
    ensure_data_dirs()
    base = inbox_dir or CURSOR_INBOX_DIR
    base.mkdir(parents=True, exist_ok=True)
    transcript_path = base / f"{guid}_transcript.txt"
    prompt_path = base / f"{guid}_CURSOR_PROMPT.md"
    transcript_path.write_text(transcript, encoding="utf-8")
    chunks = chunk_transcript(transcript, TRANSCRIPT_CHUNK_CHARS)
    chunk_note = ""
    if len(chunks) > 1:
        chunk_note = (
            f"\n\n**Note:** Transskriptionen er opdelt i {len(chunks)} dele i denne mappe "
            f"(`{guid}_chunk_*.txt`). Behandl alle dele, og **samle ét samlet JSON** med alle bullets.\n"
        )
        for i, ch in enumerate(chunks):
            (base / f"{guid}_chunk_{i + 1:02d}.txt").write_text(ch, encoding="utf-8")

    prompt_body = f"""# Bilradio — Cursor-udtræk

- **GUID (bruges ved import):** `{guid}`
- **Episodetitel:** {title}

Transskription (Whisper): se **`{transcript_path.name}`** i samme mappe (`{base}`).
{chunk_note}
---

{CURSOR_INSTRUCTIONS}

Når du er færdig, gem resultatet som **`{guid}.bullets.json`** i mappen `{base}` (samme mappe som denne fil).
Kør derefter: `bilradio import-bullets --guid {guid}`
"""
    prompt_path.write_text(prompt_body, encoding="utf-8")
    return prompt_path
