import logging
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

from popularpages.logger_config import setup_logging  # noqa: E402

# setup_logging(name=".", level="INFO")
setup_logging(name="popularpages", level="INFO")
