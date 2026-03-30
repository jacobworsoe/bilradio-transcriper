"""Smoke tests: Whisper CUDA via bilradio.run_whisper (same path as web queue)."""
from __future__ import annotations

import math
import os
import struct
import tempfile
import wave
from pathlib import Path

# Force Whisper subprocess to use this repo's venv (CUDA torch lives here).
_REPO = Path(__file__).resolve().parents[1]
_VENV_PY = _REPO / ".venv" / "Scripts" / "python.exe"
os.environ.setdefault("BILRADIO_WHISPER_PYTHON", str(_VENV_PY))


def _synth_wav(path: Path, sec: float = 4.0) -> None:
    fr = 16000
    n = int(fr * sec)
    samples = [
        max(-32767, min(32767, int(8000 * math.sin(2 * math.pi * 440 * i / fr))))
        for i in range(n)
    ]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fr)
        w.writeframes(struct.pack(f"{len(samples)}h", *samples))


def main() -> None:
    from bilradio.whisper_run import run_whisper

    with tempfile.TemporaryDirectory(prefix="bilradio_cuda_") as td:
        wav = Path(td) / "sine.wav"
        _synth_wav(wav, 4.0)
        print("Test 1: synthetic 4s WAV, model=tiny, device=cuda …")
        out1 = run_whisper(wav, model="tiny", device="cuda")
        assert out1.is_file(), "no transcript file (synthetic)"
        t1 = out1.read_text(encoding="utf-8", errors="replace").strip()
        print(f"  OK -> {out1} ({len(t1)} chars of text)" + (" (tone may be empty)" if not t1 else ""))

    audio_dir = _REPO / "data" / "audio"
    shorts = sorted(audio_dir.glob("*cfedf6fc*.mp3"))
    if not shorts:
        print("Test 2: SKIP (no *cfedf6fc*.mp3 short episode in data/audio)")
        print("All CUDA smoke tests passed (synthetic only).")
        return
    mp3 = shorts[0]
    print(f"Test 2: real short MP3 {mp3.name}, model=medium, device=cuda …")
    out2 = run_whisper(mp3, model="medium", device="cuda")
    t2 = out2.read_text(encoding="utf-8", errors="replace").strip()
    assert t2, "empty transcript (mp3)"
    print(f"  OK -> {out2} ({len(t2)} chars)")


if __name__ == "__main__":
    main()
