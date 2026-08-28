import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))

from logger_config import setup_logging

setup_logging(name="popularpages", level="INFO")

logger = logging.getLogger(__name__)

# load .env for local development
dotenv_path = Path(__file__).parent.parent / ".env"

if not dotenv_path.exists():
    dotenv_path = dotenv_path.parent / ".env"

try:
    load_dotenv(
        dotenv_path,
        override=False,
    )
except Exception as e:
    logger.info("Failed to load .env: %s", e)

