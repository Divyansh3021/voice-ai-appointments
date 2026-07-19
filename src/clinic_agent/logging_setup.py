"""Persistent log file, separate from the webhook alerting in alerting.py.

Alerts tell you *that* something broke; this is what lets you actually
reconstruct *what happened* on a call afterwards - the [call=...] traces,
tool execution details, Cliniko request/response info, and everything
livekit.agents itself logs (STT/TTS timing, turn detection, tool dispatch)
that only ever existed in whatever terminal happened to be open otherwise.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024  # 10MB per file
BACKUP_COUNT = 5  # ~50MB retained per process before oldest rolls off


def install_file_handler(source: str, log_dir: str = "logs", level: int = logging.INFO) -> None:
    """Attach a rotating file handler to the ROOT logger (not just
    `clinic_agent`) so it captures livekit.agents' own logging too, not
    just our modules - a call's full story spans both.

    `source` names the file (e.g. "agent-worker.log" / "api.log") - keep
    per-process files rather than one shared file, since RotatingFileHandler
    isn't safe for two processes rotating the same file concurrently.

    Only ever *lowers* the root logger's level threshold, never raises it -
    won't clobber a more verbose level LiveKit's CLI already set (e.g.
    DEBUG in `dev` mode).
    """
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        directory / f"{source}.log",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > level:
        root.setLevel(level)
