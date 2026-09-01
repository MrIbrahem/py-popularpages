import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# load .env for local development
dotenv_path = Path().resolve().parent / ".env"

if not dotenv_path.exists():
    dotenv_path = dotenv_path.parent / ".env"

try:
    load_dotenv(
        dotenv_path,
        override=False,
    )
except Exception as e:
    logging.info("Failed to load .env: %s", e)

from .logger_config import setup_logging  # noqa: E402

# setup_logging(name=".", level="INFO")

# Logging is configured before argparse runs in main(), so detect the
# debug/verbose flag from sys.argv at import time. --verbose/-v is the
# canonical flag (used by main()); --debug is also honored for convenience.
_DEBUG_FLAGS = {"-v", "--verbose", "--debug"}
level = "DEBUG" if _DEBUG_FLAGS.intersection(sys.argv[1:]) else "INFO"
setup_logging(name="popularpages", level=level)
