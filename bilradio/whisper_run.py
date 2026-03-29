from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from bilradio.config import (
    DATA_DIR,
    TRANSCRIPTS_DIR,
    WHISPER_CMD,
    WHISPER_BOOT_SILENCE_SEC,
    WHISPER_DEVICE,
    WHISPER_HEARTBEAT_SEC,
    WHISPER_MAX_RESTARTS,
    WHISPER_MODEL,
    WHISPER_STALL_SEC,
    ensure_data_dirs,
)

# Updated on each heartbeat so tools/agents can read status without the TTY.
WHISPER_HEARTBEAT_FILE = DATA_DIR / "whisper_heartbeat.txt"
# Contains the PID of the current Whisper subprocess; cleared when it exits.
WHISPER_PID_FILE = DATA_DIR / "whisper_pid.txt"


class WhisperStalledError(RuntimeError):
    """Whisper produced no new output within the stall window."""


def transcript_path_for_audio(audio_path: Path) -> Path:
    """Whisper writes `<basename>.txt` into output dir for input `<basename>.mp3`."""
    ensure_data_dirs()
    return TRANSCRIPTS_DIR / f"{audio_path.stem}.txt"


def _build_cmd(audio_path: Path) -> list[str]:
    return [
        *WHISPER_CMD,        # e.g. ['C:\Python311\python.exe', '-m', 'whisper']
        str(audio_path.resolve()),  # Always absolute — some ffmpeg builds reject relative paths
        "--model",
        WHISPER_MODEL,
        "--language",
        "da",
        "--device",
        WHISPER_DEVICE,
        "--temperature",
        "0",
        "--condition_on_previous_text",
        "True",
        "--output_format",
        "txt",
        "--output_dir",
        str(TRANSCRIPTS_DIR),
    ]


def _run_whisper_once(audio_path: Path, out_txt: Path) -> None:
    """
    Pipe Whisper stdout: stream lines to the terminal, print a heartbeat with the
    latest line every WHISPER_HEARTBEAT_SEC (default 120s), and kill the process
    if no new non-empty line arrives for WHISPER_STALL_SEC once past the boot
    grace WHISPER_BOOT_SILENCE_SEC (stall = hang detection + auto-restart in run_whisper).
    """
    cmd = _build_cmd(audio_path)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    try:
        WHISPER_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass

    latest_line = ""
    last_output_mono = time.monotonic()
    lock = threading.Lock()
    reader_done = threading.Event()
    skipped_file: list[str] = []  # set by reader if Whisper prints "Skipping … due to"

    def reader() -> None:
        nonlocal latest_line, last_output_mono
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                stripped = line.rstrip("\n\r")
                with lock:
                    if stripped:
                        latest_line = stripped
                        last_output_mono = time.monotonic()
                    # Detect Whisper's silent-skip pattern (exit code 0, no output file)
                    if "Skipping" in stripped and "due to" in stripped:
                        skipped_file.append(stripped)
                try:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                except UnicodeEncodeError:
                    sys.stdout.buffer.write(line.encode("utf-8", errors="replace"))
                    sys.stdout.buffer.flush()
        finally:
            reader_done.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    start_mono = time.monotonic()
    last_heartbeat_mono = start_mono

    while True:
        if reader_done.is_set() and proc.poll() is not None:
            break
        time.sleep(2.0)
        now = time.monotonic()

        with lock:
            line_snapshot = latest_line
            last_out = last_output_mono

        # Heartbeat: latest line every WHISPER_HEARTBEAT_SEC
        if now - last_heartbeat_mono >= WHISPER_HEARTBEAT_SEC:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
            safe = line_snapshot if line_snapshot else "(no line yet)"
            print(
                f"[bilradio-whisper] heartbeat {ts} latest: {safe}",
                flush=True,
            )
            try:
                ensure_data_dirs()
                WHISPER_HEARTBEAT_FILE.write_text(
                    f"utc={ts}\nlatest_line={safe}\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
            last_heartbeat_mono = now

        # Stall detection (only while process still running)
        if proc.poll() is not None:
            break
        silent_for = now - last_out
        # After boot grace, require at least one line every WHISPER_STALL_SEC
        if (
            now - start_mono >= WHISPER_BOOT_SILENCE_SEC
            and silent_for >= WHISPER_STALL_SEC
        ):
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            t.join(timeout=5)
            try:
                WHISPER_PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            raise WhisperStalledError(
                f"No new Whisper output for {WHISPER_STALL_SEC}s "
                f"(boot grace was {WHISPER_BOOT_SILENCE_SEC}s)."
            )

    t.join(timeout=30)
    rc = proc.wait() if proc.poll() is None else proc.returncode
    try:
        if WHISPER_PID_FILE.exists():
            WHISPER_PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    if rc != 0:
        raise subprocess.CalledProcessError(rc, cmd)
    # Whisper silently skips unreadable files and exits 0 with no output.
    if skipped_file:
        raise RuntimeError(
            f"Whisper skipped the audio file (ffmpeg could not read it): {skipped_file[0][:200]}"
        )


def run_whisper(audio_path: Path) -> Path:
    """
    Invoke OpenAI Whisper CLI with stall watchdog and automatic restarts.
    """
    ensure_data_dirs()
    out_txt = transcript_path_for_audio(audio_path)
    last_err: Exception | None = None
    for attempt in range(1, WHISPER_MAX_RESTARTS + 1):
        try:
            if attempt > 1:
                print(
                    f"[bilradio-whisper] starting attempt {attempt}/{WHISPER_MAX_RESTARTS}",
                    flush=True,
                )
            _run_whisper_once(audio_path, out_txt)
            if not out_txt.is_file():
                raise FileNotFoundError(
                    f"Whisper did not produce expected transcript at {out_txt}. "
                    "Check that the `whisper` CLI is from the openai-whisper package."
                )
            return out_txt
        except WhisperStalledError as e:
            last_err = e
            print(f"[bilradio-whisper] {e}", flush=True)
            if attempt >= WHISPER_MAX_RESTARTS:
                raise
        except subprocess.CalledProcessError as e:
            raise
    assert last_err is not None
    raise last_err
