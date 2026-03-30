from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bilradio.config import (
    DATA_DIR,
    LOGS_DIR,
    TRANSCRIPTS_DIR,
    WHISPER_CMD,
    WHISPER_BOOT_SILENCE_SEC,
    WHISPER_DEVICE,
    WHISPER_HEARTBEAT_SEC,
    WHISPER_MAX_RESTARTS,
    WHISPER_MODEL,
    WHISPER_SEGMENT_STATUS_SEC,
    WHISPER_STALL_SEC,
    WHISPER_SUBPROCESS_MODE,
    WHISPER_VERBOSE,
    ensure_data_dirs,
)
from bilradio.runtime_log import get_logger as _get_runtime_logger

# Large enough that a Whisper traceback cannot push the "Skipping ... due to" line out.
_SKIP_CARRY_MAX = 512 * 1024

# Updated on each heartbeat so tools/agents can read status without the TTY.
WHISPER_HEARTBEAT_FILE = DATA_DIR / "whisper_heartbeat.txt"
# Contains the PID of the current Whisper subprocess; cleared when it exits.
WHISPER_PID_FILE = DATA_DIR / "whisper_pid.txt"

# tqdm transcription bar: " 18%|#8 | 1620/9000 [00:12<01:03, 250frames/s]"
_TRANSCRIBE_TQDM_RE = re.compile(
    r"(\d+)%\|[^|\r\n]*\|\s*(\d+)/(\d+)\s+\[[^\]\r\n]*frames/s[^\]\r\n]*\]"
)
# Model download bar uses byte units in the rate (not frames/s).
_MODEL_PULL_TQDM_RE = re.compile(
    r"(\d+)%\|[^|\r\n]*\|\s*[^\r\n]+?\s+\[[^\]\r\n]*?(?:[MGK]iB|iB)/s[^\]\r\n]*\]"
)
# Whisper verbose line: [00:12.960 --> 00:14.320]  Lidt irriterende.
_WHISPER_SEGMENT_SUFFIX = re.compile(
    r"^\s*\[[^\]]+\s*-->\s*[^\]]+\]\s*\S.*"
)
_WHISPER_SEGMENT_RANGE_RE = re.compile(r"\[([\d.:]+)\s*-->\s*([\d.:]+)\]")


def _parse_whisper_ts(ts: str) -> float | None:
    """Parse Whisper timestamp (e.g. 00:24.360, 1:04:05.120) to seconds."""
    ts = ts.strip()
    parts = ts.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (ValueError, TypeError):
        return None
    return None


def _last_segment_end_seconds_from_log(text: str) -> float | None:
    """Largest end timestamp from [... --> end] markers in log text."""
    best: float | None = None
    for m in _WHISPER_SEGMENT_RANGE_RE.finditer(text):
        sec = _parse_whisper_ts(m.group(2))
        if sec is not None and (best is None or sec > best):
            best = sec
    return best


def _format_hms_short(seconds: float) -> str:
    """Human-friendly M:SS or H:MM:SS for diagnostic lines."""
    if seconds < 0:
        return "0:00"
    s = int(round(seconds))
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    if h:
        return f"{h:d}:{m:02d}:{sec:02d}"
    return f"{m:d}:{sec:02d}"


class WhisperStalledError(RuntimeError):
    """Whisper produced no new output within the stall window."""


def _last_whisper_segment_line(text: str) -> str | None:
    """Return the last complete verbose segment line from a stdout tail, if any."""
    best: str | None = None
    for line in text.splitlines():
        s = line.strip()
        if not s or len(s) > 6000:
            continue
        if _WHISPER_SEGMENT_SUFFIX.match(s):
            best = s
    return best


def transcript_path_for_audio(audio_path: Path) -> Path:
    """Whisper writes `<basename>.txt` into output dir for input `<basename>.mp3`."""
    ensure_data_dirs()
    return TRANSCRIPTS_DIR / f"{audio_path.stem}.txt"


def _build_cmd(
    audio_path: Path,
    *,
    model: str | None = None,
    device: str | None = None,
) -> list[str]:
    m = WHISPER_MODEL if model is None else model
    d = WHISPER_DEVICE if device is None else device
    cmd: list[str] = [
        *WHISPER_CMD,
        str(audio_path.resolve()),
        "--model",
        m,
        "--language",
        "da",
        "--device",
        d,
        "--temperature",
        "0",
        "--condition_on_previous_text",
        "True",
    ]
    # Default matches interactive `whisper file.mp3` (no flag) — verbose=True, segment lines.
    # Set BILRADIO_WHISPER_VERBOSE=false for tqdm-only output (queue page progress bars).
    if not WHISPER_VERBOSE:
        cmd.extend(["--verbose", "False"])
    cmd.extend(
        [
            "--output_format",
            "txt",
            "--output_dir",
            str(TRANSCRIPTS_DIR),
        ]
    )
    return cmd


def _parse_tqdm_buffer(buf: str) -> dict[str, int | str | None]:
    """
    Return the latest Whisper tqdm state from a stdout tail. Keys may include:
    phase (model_pull | transcribing), model_pull_pct, transcribe_pct, transcribe_cur, transcribe_total.
    """
    t_matches = list(_TRANSCRIBE_TQDM_RE.finditer(buf))
    m_matches = list(_MODEL_PULL_TQDM_RE.finditer(buf))
    last_t = t_matches[-1] if t_matches else None
    last_m = m_matches[-1] if m_matches else None

    out: dict[str, int | str | None] = {
        "phase": None,
        "model_pull_pct": None,
        "transcribe_pct": None,
        "transcribe_cur": None,
        "transcribe_total": None,
    }

    if last_t is None and last_m is None:
        return out

    if last_m is None or (last_t is not None and last_t.end() > last_m.end()):
        assert last_t is not None
        cur = int(last_t.group(2))
        total = int(last_t.group(3))
        pct = min(100, round(100.0 * cur / total)) if total > 0 else int(last_t.group(1))
        out["phase"] = "transcribing"
        out["transcribe_pct"] = pct
        out["transcribe_cur"] = cur
        out["transcribe_total"] = total
        return out

    assert last_m is not None
    out["phase"] = "model_pull"
    out["model_pull_pct"] = int(last_m.group(1))
    return out


def _progress_summary_line(state: dict[str, int | str | None]) -> str | None:
    if state.get("phase") == "transcribing":
        cur, total, pct = (
            state.get("transcribe_cur"),
            state.get("transcribe_total"),
            state.get("transcribe_pct"),
        )
        if isinstance(cur, int) and isinstance(total, int) and isinstance(pct, int):
            return f"Transcribing {pct}% ({cur}/{total} frames)"
        return None
    if state.get("phase") == "model_pull":
        mp = state.get("model_pull_pct")
        if isinstance(mp, int):
            return f"Loading Whisper model {mp}%"
    return None


def _write_whisper_status_file(
    latest_line: str,
    tqdm_state: dict[str, int | str | None],
    utc_ts: str | None = None,
) -> None:
    """Persist fields consumed by /api/queue (see queue.html)."""
    try:
        ensure_data_dirs()
    except Exception:
        return
    ts = utc_ts or datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    phase = tqdm_state.get("phase") or ""
    mp = tqdm_state.get("model_pull_pct")
    tcp = tqdm_state.get("transcribe_pct")
    tc = tqdm_state.get("transcribe_cur")
    tt = tqdm_state.get("transcribe_total")

    def _s(v: object | None) -> str:
        return "" if v is None else str(v)

    lines = [
        f"utc={ts}",
        f"latest_line={latest_line}",
        f"phase={phase}",
        f"model_pull_pct={_s(mp)}",
        f"transcribe_pct={_s(tcp)}",
        f"transcribe_cur={_s(tc)}",
        f"transcribe_total={_s(tt)}",
    ]
    try:
        WHISPER_HEARTBEAT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def _find_whisper_skipping_line(text: str) -> str | None:
    for line in text.splitlines():
        if "Skipping" in line and "due to" in line:
            return line.strip()[:2000]
    return None


def check_whisper_import() -> tuple[bool, str]:
    """Verify openai-whisper imports in the same interpreter as WHISPER_CMD (when applicable)."""
    cmd = WHISPER_CMD
    if len(cmd) >= 2 and cmd[1] == "-m" and "whisper" in cmd:
        py = cmd[0]
    else:
        py = sys.executable
    try:
        r = subprocess.run(
            [py, "-c", "import whisper; print(whisper.__file__)"],
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as e:
        return False, str(e)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()[:800]
        return False, msg or f"exit {r.returncode}"
    return True, r.stdout.strip()


def _list_recent_transcripts(max_files: int = 20) -> str:
    try:
        paths = sorted(
            TRANSCRIPTS_DIR.glob("*.txt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:max_files]
        if not paths:
            return "Recent .txt in transcripts: (none)"
        return "Recent .txt in transcripts:\n  " + "\n  ".join(str(p) for p in paths)
    except OSError as e:
        return f"Recent .txt in transcripts: (error listing: {e})"


def _missing_transcript_message(
    audio_path: Path,
    out_txt: Path,
    diag: dict[str, Any],
) -> str:
    session = diag.get("session_log")
    tail = ""
    spath = Path(session) if session else None
    if spath and spath.is_file():
        raw = spath.read_text(encoding="utf-8", errors="replace")
        tail = raw[-8000:] if len(raw) > 8000 else raw
    ok_imp, imp_msg = check_whisper_import()
    ar = audio_path.resolve()
    lines = [
        f"Whisper did not produce the expected transcript at {out_txt}.",
        f"audio_path exists: {ar.is_file()} ({ar})",
        f"Whisper session log: {session or '(none)'}",
    ]
    if tail.strip():
        lines.append("--- stdout/stderr tail (full log in session file above) ---")
        lines.append(tail.strip())
    lines.append(_list_recent_transcripts())
    lines.append(
        f"openai-whisper import check (WHISPER_CMD interpreter): ok={ok_imp} {imp_msg}"
    )
    return "\n".join(lines)


def _whisper_exited_without_transcript_message(
    out_txt: Path,
    session_path: Path,
    log_tail: str,
    *,
    model: str,
    device: str,
) -> str:
    """
    Whisper CLI sometimes returns 0 without writing a .txt when the process dies
    early (e.g. CUDA OOM / TDR) and never reaches the Skipping handler.
    """
    tail = log_tail.strip()
    end_sec = _last_segment_end_seconds_from_log(tail) if tail else None
    lines = [
        f"Whisper exited with code 0 but did not write the transcript at {out_txt}.",
        "The CLI only writes the .txt after the full decode finishes. If the run stops "
        "part-way, the session log may still show tqdm bars or segment lines, but no file. "
        "On Windows, exit code 0 sometimes appears even when the GPU job ended early.",
    ]
    if end_sec is not None:
        lines.append(
            f"The log shows the last segment ending around {_format_hms_short(end_sec)} wall-clock "
            "time in the audio. For an episode much longer than that, decoding almost certainly "
            "stopped early (often VRAM: `medium` + ~1h on 6–8 GB)."
        )
    else:
        lines.append(
            "The session log often shows tqdm progress (model download or frames/s) then stops — "
            "often GPU ran out of memory or the driver/OS killed the worker."
        )
    lines.extend(
        [
            f"Full session log: {session_path}",
            f"Current run: model={model!r} device={device!r}.",
            "Try: set `BILRADIO_WHISPER_MODEL=small` or `base` in `.env`, or "
            "`BILRADIO_WHISPER_DEVICE=cpu` for reliability (slower). Close other GPU apps.",
        ]
    )
    if tail:
        lines.append("--- session log (tail) ---")
        lines.append(tail)
    return "\n".join(lines)


def _whisper_simple_exited_without_transcript_message(
    out_txt: Path,
    session_path: Path,
    *,
    model: str,
    device: str,
) -> str:
    return "\n".join(
        [
            f"Whisper exited with code 0 (BILRADIO_WHISPER_SUBPROCESS=simple) but did not write "
            f"the transcript at {out_txt}.",
            "Stdout/stderr were not captured — inspect the console output from this process.",
            f"Stub note file: {session_path}",
            f"Current run: model={model!r} device={device!r}.",
            "Try: `BILRADIO_WHISPER_MODEL=small` or `base`, or `BILRADIO_WHISPER_DEVICE=cpu`, "
            "or transcribe manually and run `bilradio ingest-transcripts`.",
        ]
    )


def _run_whisper_once_simple(
    *,
    audio_path: Path,
    out_txt: Path,
    cmd: list[str],
    audio_r: Path,
    out_expected: Path,
    eff_model: str,
    eff_device: str,
    diag: dict[str, Any],
    env: dict[str, str],
    log: Any,
) -> None:
    """Inherit console I/O; no pipe reader or stall watchdog (closest to manual ``whisper``)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_path = LOGS_DIR / f"whisper_{stamp}_simple.txt"
    diag["session_log"] = str(session_path)
    try:
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            "BILRADIO_WHISPER_SUBPROCESS=simple (stdout/stderr not captured by bilradio).\n"
            f"audio={audio_r}\nexpected_txt={out_expected}\n",
            encoding="utf-8",
        )
    except OSError as e:
        log.warning("Could not write simple-mode note %s: %s", session_path, e)

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            env=env,
        )
    except OSError:
        raise

    try:
        WHISPER_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass

    idle_state: dict[str, int | str | None] = {
        "phase": None,
        "model_pull_pct": None,
        "transcribe_pct": None,
        "transcribe_cur": None,
        "transcribe_total": None,
    }
    _write_whisper_status_file(
        "(simple mode — progress in parent console only)", idle_state
    )

    rc = proc.wait()
    try:
        if WHISPER_PID_FILE.exists():
            WHISPER_PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass

    log.info("Whisper subprocess (simple mode) finished rc=%s", rc)

    if rc != 0:
        raise subprocess.CalledProcessError(rc, cmd)

    if not out_txt.is_file():
        log.error("Whisper rc=0 but missing %s (simple mode)", out_txt)
        raise RuntimeError(
            _whisper_simple_exited_without_transcript_message(
                out_txt,
                session_path,
                model=eff_model,
                device=eff_device,
            )
        )


def _run_whisper_once(
    audio_path: Path,
    out_txt: Path,
    *,
    model: str | None = None,
    device: str | None = None,
    diag: dict[str, Any] | None = None,
) -> None:
    """
    Pipe Whisper stdout (binary chunks): stream to the terminal, parse tqdm bars
    for model download vs transcription progress, write WHISPER_HEARTBEAT_FILE for
    the web UI, print a heartbeat every WHISPER_HEARTBEAT_SEC, and kill the process
    if no new stdout byte arrives for WHISPER_STALL_SEC once past the boot grace
    WHISPER_BOOT_SILENCE_SEC.
    """
    log = _get_runtime_logger("bilradio.whisper")
    if diag is None:
        diag = {}
    ensure_data_dirs()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_path = LOGS_DIR / f"whisper_{stamp}.log"
    diag["session_log"] = str(session_path)

    eff_model = WHISPER_MODEL if model is None else model
    eff_device = WHISPER_DEVICE if device is None else device
    cmd = _build_cmd(audio_path, model=model, device=device)
    audio_r = audio_path.resolve()
    out_expected = out_txt.resolve()
    log.info(
        "Whisper session start cmd=%s audio=%s expected_txt=%s transcripts_dir=%s",
        cmd,
        audio_r,
        out_expected,
        TRANSCRIPTS_DIR.resolve(),
    )

    if WHISPER_SUBPROCESS_MODE == "simple":
        _run_whisper_once_simple(
            audio_path=audio_path,
            out_txt=out_txt,
            cmd=cmd,
            audio_r=audio_r,
            out_expected=out_expected,
            eff_model=eff_model,
            eff_device=eff_device,
            diag=diag,
            env={**__import__("os").environ, "PYTHONUNBUFFERED": "1"},
            log=log,
        )
        return

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

    env = {**__import__("os").environ, "PYTHONUNBUFFERED": "1"}
    session_log_fp = None
    try:
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_log_fp = open(session_path, "wb")
    except OSError as e:
        log.warning("Could not open session log %s: %s", session_path, e)
        session_log_fp = None

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=env,
        )
    except OSError:
        if session_log_fp is not None:
            try:
                session_log_fp.close()
            except OSError:
                pass
        raise

    try:
        WHISPER_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass

    idle_state: dict[str, int | str | None] = {
        "phase": None,
        "model_pull_pct": None,
        "transcribe_pct": None,
        "transcribe_cur": None,
        "transcribe_total": None,
    }
    _write_whisper_status_file("(starting…)", idle_state)

    latest_line = ""
    tqdm_state: dict[str, int | str | None] = {
        "phase": None,
        "model_pull_pct": None,
        "transcribe_pct": None,
        "transcribe_cur": None,
        "transcribe_total": None,
    }
    last_output_mono = time.monotonic()
    lock = threading.Lock()
    reader_done = threading.Event()
    skipped_file: list[str] = []
    status_write_fast_mono = 0.0
    status_write_segment_mono = time.monotonic() - WHISPER_SEGMENT_STATUS_SEC

    def reader() -> None:
        nonlocal latest_line, last_output_mono, tqdm_state, status_write_fast_mono, status_write_segment_mono
        assert proc.stdout is not None
        buf = ""
        carry_skip = ""
        try:
            while True:
                chunk_b = proc.stdout.read(8192)
                if not chunk_b:
                    break
                if session_log_fp is not None:
                    try:
                        session_log_fp.write(chunk_b)
                        session_log_fp.flush()
                    except OSError:
                        pass
                last_output_mono = time.monotonic()
                chunk = chunk_b.decode("utf-8", errors="replace")
                try:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                except UnicodeEncodeError:
                    sys.stdout.buffer.write(chunk_b)
                    sys.stdout.buffer.flush()
                buf += chunk
                if len(buf) > 48_000:
                    buf = buf[-48_000:]
                parsed = _parse_tqdm_buffer(buf)
                carry_skip = (carry_skip + chunk)[-_SKIP_CARRY_MAX:]
                if not skipped_file and "Skipping" in carry_skip and "due to" in carry_skip:
                    for line in carry_skip.splitlines():
                        if "Skipping" in line and "due to" in line:
                            skipped_file.append(line.strip()[:2000])
                            break
                seg_line = _last_whisper_segment_line(buf)
                summary = _progress_summary_line(parsed)
                now_m = time.monotonic()
                with lock:
                    tqdm_state = dict(parsed)
                    if seg_line:
                        latest_line = seg_line
                    elif summary:
                        latest_line = summary
                    elif parsed.get("phase") is None and any(c.isalnum() for c in chunk):
                        tail = chunk.rstrip().splitlines()
                        if tail:
                            cand = tail[-1].strip()
                            if cand and len(cand) < 500:
                                latest_line = cand

                is_fast = parsed.get("phase") == "model_pull" or (
                    bool(summary) and seg_line is None
                )
                do_write = False
                if is_fast:
                    if now_m - status_write_fast_mono >= 0.25:
                        status_write_fast_mono = now_m
                        do_write = True
                elif seg_line is not None:
                    if now_m - status_write_segment_mono >= WHISPER_SEGMENT_STATUS_SEC:
                        status_write_segment_mono = now_m
                        do_write = True

                if do_write:
                    with lock:
                        utc = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
                        line_for_file = (
                            latest_line
                            if latest_line
                            else "(no transcription progress yet)"
                        )
                        st = dict(tqdm_state)
                    _write_whisper_status_file(line_for_file, st, utc_ts=utc)
        finally:
            reader_done.set()
            if session_log_fp is not None:
                try:
                    session_log_fp.close()
                except OSError:
                    pass

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
            state_snapshot = dict(tqdm_state)

        if now - last_heartbeat_mono >= WHISPER_HEARTBEAT_SEC:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
            safe = line_snapshot if line_snapshot else "(no line yet)"
            print(
                f"[bilradio-whisper] heartbeat {ts} latest: {safe}",
                flush=True,
            )
            _write_whisper_status_file(safe, state_snapshot, utc_ts=ts)
            last_heartbeat_mono = now

        if proc.poll() is not None:
            break
        silent_for = now - last_out
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

    log.info("Whisper subprocess finished rc=%s session_log=%s", rc, session_path)

    if session_path.is_file() and not skipped_file:
        try:
            full_log = session_path.read_text(encoding="utf-8", errors="replace")
            sl = _find_whisper_skipping_line(full_log)
            if sl:
                skipped_file.append(sl)
        except OSError:
            pass

    if rc != 0:
        raise subprocess.CalledProcessError(rc, cmd)

    if skipped_file:
        log.error("Whisper reported skip: %s", skipped_file[0][:500])
        raise RuntimeError(
            f"Whisper failed for this audio file (full log: {session_path}): "
            f"{skipped_file[0][:2000]}"
        )

    if not out_txt.is_file():
        log_tail = ""
        try:
            if session_path.is_file():
                raw = session_path.read_text(encoding="utf-8", errors="replace")
                log_tail = raw[-6000:] if len(raw) > 6000 else raw
        except OSError:
            pass
        log.error("Whisper rc=0 but missing %s (see %s)", out_txt, session_path)
        raise RuntimeError(
            _whisper_exited_without_transcript_message(
                out_txt,
                session_path,
                log_tail,
                model=eff_model,
                device=eff_device,
            )
        )


def run_whisper(
    audio_path: Path,
    *,
    model: str | None = None,
    device: str | None = None,
) -> Path:
    """
    Invoke OpenAI Whisper CLI with automatic restarts.

    With ``BILRADIO_WHISPER_SUBPROCESS=simple``, the subprocess inherits the console (no
    pipe, no stall watchdog). Default ``full`` mode pipes stdout for UI heartbeats and
    applies ``BILRADIO_WHISPER_STALL_SEC`` after the boot grace.
    """
    ensure_data_dirs()
    out_txt = transcript_path_for_audio(audio_path)
    last_err: Exception | None = None
    for attempt in range(1, WHISPER_MAX_RESTARTS + 1):
        diag: dict[str, Any] = {}
        try:
            if attempt > 1:
                print(
                    f"[bilradio-whisper] starting attempt {attempt}/{WHISPER_MAX_RESTARTS}",
                    flush=True,
                )
            _run_whisper_once(
                audio_path,
                out_txt,
                model=model,
                device=device,
                diag=diag,
            )
            if not out_txt.is_file():
                msg = _missing_transcript_message(audio_path, out_txt, diag)
                raise FileNotFoundError(msg)
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
