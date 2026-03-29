from __future__ import annotations

from pathlib import Path

import httpx

from bilradio.config import AUDIO_DIR, ensure_data_dirs


def safe_filename_part(s: str, max_len: int = 80) -> str:
    # ASCII-only: non-ASCII chars (ø, å, æ …) become underscores so that
    # downstream tools (ffmpeg) can open the path on every platform.
    out = "".join(c if c.isascii() and (c.isalnum() or c in " -_.") else "_" for c in s)
    out = out.strip().replace(" ", "_")[:max_len]
    return out or "episode"


def audio_path_for_guid(guid: str, title: str) -> Path:
    ensure_data_dirs()
    stem = f"{safe_filename_part(title)}_{guid[:8]}"
    return AUDIO_DIR / f"{stem}.mp3"


def download_audio(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=600.0, follow_redirects=True) as client:
        with client.stream("GET", url) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1024 * 256):
                    f.write(chunk)
