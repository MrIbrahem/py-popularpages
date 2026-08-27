"""
Simple file-based logger, ported from src/Logger.php.

Writes one timestamped line per call to logs/log-{wiki}.txt, matching the
original PHP output format exactly so existing tooling/habits around reading
these log files on Toolforge keep working.
"""

from __future__ import annotations

from datetime import datetime

# Project root is three levels up from this file: src/popularpages/logger.py
from popularpages.wiki_repository import BASE_DIR

LOG_DIR = BASE_DIR / "logs"


def log_to_file(message: str, wiki: str) -> None:
    """
    Append a timestamped message to logs/log-{wiki}.txt.

    :param message: Message to record in the file.
    :param wiki: Wiki key (e.g. 'en.wikipedia'), used to select the log file.
    Mirrors the behaviour of the PHP ``wfLogToFile()`` helper used on Toolforge.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"log-{wiki}.txt"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output = f"{timestamp}  {message}"

    with log_path.open("a", encoding="utf-8") as f:
        f.write(output + "\n")
