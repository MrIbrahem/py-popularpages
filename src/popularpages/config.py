"""
Configuration & credential loading.

Credentials (bot username/password and replica-database access) are read from
a ``.env`` file via python-dotenv, falling back to real environment variables.
``.env.example`` is the committed template:

    cp .env.example .env
    # then edit .env with your bot username/password (from Special:BotPasswords)

The flat ``creds`` mapping the rest of the code expects (botuser, botpass,
dbhost, dbuser, dbpass, dbport) is assembled by ``load_credentials()`` so callers
never need to know the underlying environment-variable names.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# .env lives in the repo root. python-dotenv also honours real environment
# variables (used in the Toolforge deployment), which take precedence.
ENV_PATH = BASE_DIR / ".env"

VIEWS_DIR = BASE_DIR / "views"

MESSAGES_DIR = BASE_DIR / "messages"

LOG_DIR = BASE_DIR / "logs"

# Load credentials from .env (or the real environment) as early as possible so
# that importing this module makes the env vars available everywhere.
load_dotenv(ENV_PATH, override=False)

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


def load_credentials() -> dict[str, str]:
    """
    Build the credentials dict consumed by WikiRepository / WikiDatabaseRepository.

    Values come from environment variables (loaded from ``.env`` at import time,
    or set directly in the environment). Missing values default to empty strings
    so callers can detect "no credentials configured" and skip live runs.
    """
    return {
        # Wikipedia bot credentials (full name, e.g. "ExampleBot@MyTask").
        "botuser": os.environ.get("WIKIPEDIA_BOT_USERNAME", ""),
        "botpass": os.environ.get("WIKIPEDIA_BOT_PASSWORD", ""),
        # Wikimedia replica database access (Toolforge environment).
        "dbhost": os.environ.get("TOOL_REPLICA_HOST", ""),
        "dbuser": os.environ.get("TOOL_REPLICA_USER", ""),
        "dbpass": os.environ.get("TOOL_REPLICA_PASSWORD", ""),
        "dbport": os.environ.get("TOOL_REPLICA_PORT", "3306"),
    }


def has_credentials() -> bool:
    """
    Return True when the minimum credentials for a live run are present.

    Used by tests to skip integration tests that require real credentials.
    """
    creds = load_credentials()
    return bool(creds["botuser"]) and bool(creds["botpass"])


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
    "ENV_PATH",
    "MAX_PROJECT_SIZE",
    "BATCH_SIZE_THRESHOLD",
    "ASSESSMENT_CONFIG_URL",
    "load_credentials",
    "has_credentials",
    "load_wikis_config",
    "VIEWS_DIR",
]
