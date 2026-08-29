"""
Simple file-based logger, ported from src/Logger.php.

Writes one timestamped line per call to logs/log-{wiki}.txt, matching the
original PHP output format exactly so existing tooling/habits around reading
these log files on Toolforge keep working.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .config import config

logger = logging.getLogger(__name__)


def log_to_file(message: str, wiki: str) -> None:
    """
    Append a timestamped message to logs/log-{wiki}.txt.

    :param message: Message to record in the file.
    :param wiki: Wiki key (e.g. 'en.wikipedia'), used to select the log file.
    Mirrors the behaviour of the PHP ``wfLogToFile()`` helper used on Toolforge.
    """
    config.data_paths.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = config.data_paths.log_dir / f"log-{wiki}.txt"

    # match php time: date( 'Y-m-d H:i:s' )
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output = f"{timestamp}  {message}"

    with log_path.open("a", encoding="utf-8") as f:
        f.write(output + "\n")


__all__ = [
    "log_to_file",
]
