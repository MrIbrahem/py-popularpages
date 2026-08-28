"""
Configuration & credential loading.

Credentials (bot username/password and replica-database access) are read from
a ``.env`` file via python-dotenv, falling back to real environment variables.
``.env.example`` is the committed template:

    cp .env.example .env
    # then edit .env with your bot username/password (from Special:BotPasswords)

"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

try:
    load_dotenv(override=False)
except Exception as e:
    logger.info(f"Failed to load .env: {e}")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# .env lives in the repo root. python-dotenv also honours real environment
# variables (used in the Toolforge deployment), which take precedence.

VIEWS_DIR = BASE_DIR / "views"

# Persisted pageviews cache (see docs/pageviews-persistence-and-dedup-plan.md).
# Layout: DATA_DIR / "views" / <wiki> / <YYYY-MM>.jsonl
DATA_DIR = BASE_DIR / "data"
VIEWS_DATA_DIR = DATA_DIR / "views"

# Number of unique titles fetched from the Pageviews API per request batch.
VIEWS_FETCH_BATCH = 100
# Number of freshly fetched titles buffered in memory before a JSONL write is
# flushed to disk (the "save one time per 100 title" behavior).
VIEWS_FLUSH_TITLES = 100

MESSAGES_DIR = BASE_DIR / "messages"

LOG_DIR = BASE_DIR / "logs"

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

ASSESSMENT_CONFIG_URL = "https://xtools.wmcloud.org/api/project/assessments"

# Used to build the HTTP User-Agent for every outbound request (XTools,
# Pageviews REST API, and the MediaWiki action API via mwclient). Wikimedia's
# User-Agent policy requires an identifying agent with a contact.
PROJECT_NAME = "py-popularpages"
PROJECT_URL = "https://github.com/MrIbrahem/py-popularpages"


def user_agent() -> str:
    """
    Build an HTTP User-Agent string compliant with Wikimedia's UA policy
    (https://meta.wikimedia.org/wiki/User-Agent_policy): identifies the tool
    and provides a contact. Falls back to the tool name when no bot creds are
    configured.
    """
    contact = load_credentials().get("botuser") or "tool"
    return f"{PROJECT_NAME} (contact: {contact}; +{PROJECT_URL})"


def load_credentials() -> dict[str, str]:
    """
    Build the credentials dict consumed by WikiRepository / WikiDatabaseRepository.

    Values come from environment variables (loaded from ``.env`` at import time,
    or set directly in the environment). Missing values default to empty strings
    so callers can detect "no credentials configured" and skip live runs.
    """
    creds = {
        # Wikipedia bot credentials (full name, e.g. "ExampleBot@MyTask").
        "botuser": os.environ.get("WIKIPEDIA_BOT_USERNAME", ""),
        "botpass": os.environ.get("WIKIPEDIA_BOT_PASSWORD", ""),
    }
    logger.debug(
        "Loaded credentials: botuser='%s' (botpass set: %s)",
        creds["botuser"],
        bool(creds["botpass"]),
    )
    return creds


def has_credentials() -> bool:
    """
    Return True when the minimum credentials for a live run are present.

    Used by tests to skip integration tests that require real credentials.
    """
    creds = load_credentials()
    has = bool(creds["botuser"]) and bool(creds["botpass"])
    logger.debug("has_credentials=%s", has)
    return has


def load_wikis_config():
    """
    Load the wikis configuration from the config/wikis.yaml file.
    """
    path = BASE_DIR / "config" / "wikis.yaml"
    logger.debug("Loading wikis config from %s", path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    logger.info("Loaded wikis config: %d wiki(s)", len(data))
    return data


__all__ = [
    "LOG_DIR",
    "MESSAGES_DIR",
    "FALLBACK_LANG",
    "BASE_DIR",
    "MAX_PROJECT_SIZE",
    "BATCH_SIZE_THRESHOLD",
    "ASSESSMENT_CONFIG_URL",
    "PROJECT_NAME",
    "PROJECT_URL",
    "user_agent",
    "load_credentials",
    "has_credentials",
    "load_wikis_config",
    "VIEWS_DIR",
    "DATA_DIR",
    "VIEWS_DATA_DIR",
    "VIEWS_FETCH_BATCH",
    "VIEWS_FLUSH_TITLES",
]
