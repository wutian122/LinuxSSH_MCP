from __future__ import annotations

import re
import sys
from pathlib import Path
from tempfile import gettempdir
from typing import Any

from loguru import logger

from linux_ssh_mcp.settings import SSHMCPSettings

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(password\s*[:=]\s*)([^\s]+)"), r"\1***"),
    (re.compile(r"(?i)(passwd\s*[:=]\s*)([^\s]+)"), r"\1***"),
    (re.compile(r"(?i)(token\s*[:=]\s*)([^\s]+)"), r"\1***"),
]


def _redact(text: str) -> str:
    redacted = text
    for pattern, repl in _REDACTIONS:
        redacted = pattern.sub(repl, redacted)
    return redacted


def setup_logger(settings: SSHMCPSettings) -> None:
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        log_dir = Path(settings.log_dir)
    except Exception:
        log_dir = Path(gettempdir()) / "linux-ssh-mcp-logs"
        log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "app.log"
    err_file = log_dir / "error.log"

    logger.remove()

    def patcher(record: Any) -> None:
        record["message"] = _redact(record.get("message", ""))

    patched = logger.patch(patcher)

    if getattr(sys.stderr, "isatty", lambda: False)():
        patched.add(
            sys.stderr,
            level=settings.log_level,
            colorize=True,
            backtrace=False,
            diagnose=False,
            enqueue=True,
        )

    patched.add(
        str(log_file),
        level=settings.log_level,
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        enqueue=True,
        backtrace=False,
        diagnose=False,
        encoding="utf-8",
    )

    patched.add(
        str(err_file),
        level="ERROR",
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        enqueue=True,
        backtrace=False,
        diagnose=False,
        encoding="utf-8",
    )
