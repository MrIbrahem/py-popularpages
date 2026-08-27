"""Simple file-based logger, ported from src/Logger.php.

Writes one timestamped line per call to logs/log-{wiki}.txt, matching the
original PHP output format exactly so existing tooling/habits around reading
these log files on Toolforge keep working.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


def log_to_file(message: str, wiki: str) -> None:
    """Append a timestamped message to ``logs/log-{wiki}.txt``.

    Mirrors the behaviour of the PHP ``wfLogToFile()`` helper used on Toolforge.
    """
    log_path = LOG_DIR / f"log-{wiki}.txt"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"{timestamp}  {message}\n")
