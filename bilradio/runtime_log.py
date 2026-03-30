from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler

from bilradio.config import LOGS_DIR, ensure_data_dirs

_logger_configured = False


def setup_runtime_logging() -> logging.Logger:
    """Attach a rotating file handler under data/logs/bilradio.log (UTC timestamps)."""
    global _logger_configured
    ensure_data_dirs()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("bilradio")
    if _logger_configured:
        return root
    _logger_configured = True
    root.setLevel(logging.DEBUG)
    handler = RotatingFileHandler(
        LOGS_DIR / "bilradio.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    root.addHandler(handler)
    return root


def get_logger(name: str = "bilradio") -> logging.Logger:
    """Child logger under bilradio; ensures file logging is configured once."""
    setup_runtime_logging()
    return logging.getLogger(name)
