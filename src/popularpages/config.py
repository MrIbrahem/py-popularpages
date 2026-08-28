""" """

from __future__ import annotations
import yaml

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

VIEWS_DIR = BASE_DIR / "views"

MESSAGES_DIR = BASE_DIR / "messages"

LOG_DIR = BASE_DIR / "logs"

CONFIG_PATH = BASE_DIR / "config.ini"

FALLBACK_LANG = "en"

# See T164178 in the original PHP codebase: this is a safety cap against
# unbounded memory use for extremely large WikiProjects, not a stylistic
# choice -- keep it as-is.
MAX_PROJECT_SIZE = 1_000_000

# Number of target pages to accumulate before flushing a pageviews batch.
# The 60 is arbitrary (see original PHP comment): staying near this number
# keeps each PageviewsRepository.get_pageviews() call in the 60-200 page
# range once redirects are included, which keeps us close to the Pageviews
# API's 100 req/sec limit without needing to hit it exactly -- the retry
# handler in PageviewsRepository absorbs the rest.
BATCH_SIZE_THRESHOLD = 60

ASSESSMENT_CONFIG_URL = "https://xtools.wmflabs.org/api/project/assessments"

def load_wikis_config():
    """
    Load the wikis configuration from the config/wikis.yaml file.
    """
    return yaml.safe_load((BASE_DIR / "config" / "wikis.yaml").read_text(encoding="utf-8"))


__all__ = [
    "LOG_DIR",
    "MESSAGES_DIR",
    "FALLBACK_LANG",
    "BASE_DIR",
    "CONFIG_PATH",
    "MAX_PROJECT_SIZE",
    "BATCH_SIZE_THRESHOLD",
    "ASSESSMENT_CONFIG_URL",
    "load_wikis_config",
    "VIEWS_DIR",
]
