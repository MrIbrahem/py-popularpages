"""
Configuration & credential loading.

Credentials (bot username/password) are read from a ``.env`` file via
python-dotenv, falling back to real environment variables.
``.env.example`` is the committed template:

    cp .env.example .env
    # then edit .env with your bot username/password (from Special:BotPasswords)

The whole application reads its settings through the single :data:`config`
instance of :class:`AppConfig`. No other module should read raw environment
variables or hard-code paths/limits; import :data:`config` from this module
and access the nested sub-configs (``config.paths``, ``config.credentials``,
``config.pageviews``, ``config.wiki``, ``config.project``).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class DbConfig:
    user: str
    password: str
    cache_ttl: int = 60 * 60 * 24 * 7  # 1 week

    @classmethod
    def load(cls) -> DbConfig:
        return cls(
            cache_ttl=int(os.getenv("TOOL_REPLICA_CACHE_TTL", 60 * 60 * 24 * 7)),
            user=os.getenv("TOOL_REPLICA_USER") or "",
            password=os.getenv("TOOL_REPLICA_PASSWORD") or "",
        )

    def has_db_data(self) -> bool:
        return not self.user or not self.password


@dataclass(frozen=True)
class PathsConfig:
    """Application filesystem paths."""

    base_dir: Path
    views_dir: Path
    data_dir: Path
    views_data_dir: Path
    messages_dir: Path
    log_dir: Path
    wikis_config_file: Path

    @classmethod
    def from_base_dir(cls, base_dir: Path) -> PathsConfig:
        """
        Create a paths configuration rooted at the specified base directory.

        Parameters:
            base_dir (Path): Root directory used to derive application paths.

        Returns:
            PathsConfig: Configuration containing paths derived from the base directory.
        """
        data_dir = base_dir / "data"

        return cls(
            base_dir=base_dir,
            views_dir=base_dir / "views",
            data_dir=data_dir,
            views_data_dir=data_dir / "views",
            messages_dir=base_dir / "messages",
            log_dir=base_dir / "logs",
            wikis_config_file=base_dir / "config" / "wikis.yaml",
        )


@dataclass(frozen=True)
class CredentialsConfig:
    """Wikipedia bot credentials."""

    botuser: str = ""
    botpass: str = ""


@dataclass(frozen=True)
class PageviewsConfig:
    """Pageviews fetching and persistence settings."""

    # --- persistence ---
    fetch_batch: int = 100
    flush_titles: int = 100
    batch_size_threshold: int = 60

    # --- Pageviews REST API client (https://wikimedia.org/api/rest_v1/metrics/pageviews) ---
    endpoint_url: str = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
    request_timeout_seconds: float = 3.0
    connect_timeout_seconds: float = 3.0
    # Delay between individual outgoing requests within a batch. This
    # approximates the PHP client's `delay` option (500ms), which staggers
    # dispatch of the underlying Guzzle promises.
    request_delay_seconds: float = 0.5  # matches PHP's REQUEST_DELAY = 500ms
    max_retry_attempts: int = 5
    retry_status_codes: frozenset = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class WikiConfig:
    """Wikipedia-related configuration."""

    fallback_lang: str = "en"
    max_project_size: int = 1_000_000
    assessment_config_url: str = "https://xtools.wmcloud.org/api/project/assessments"


@dataclass(frozen=True)
class ProjectConfig:
    """Project identity configuration."""

    name: str = "py-popularpages"
    url: str = "https://github.com/MrIbrahem/py-popularpages"


@dataclass(frozen=True)
class AppConfig:
    """Application configuration.

    A single frozen object that aggregates every sub-config. The HTTP
    User-Agent is computed on demand via the :py:attr:`user_agent` property
    (it depends on the resolved credentials), so it is intentionally *not* a
    stored field.
    """

    paths: PathsConfig
    credentials: CredentialsConfig
    pageviews: PageviewsConfig
    wiki: WikiConfig
    project: ProjectConfig
    db: DbConfig

    @property
    def user_agent(self) -> str:
        return user_agent(self.project, self.credentials)


def load_credentials() -> CredentialsConfig:
    """
    Load Wikipedia bot credentials from environment variables.

    Environment variables take precedence over values loaded from ``.env``.
    """
    credentials = CredentialsConfig(
        botuser=os.environ.get("WIKIPEDIA_BOT_USERNAME", ""),
        botpass=os.environ.get("WIKIPEDIA_BOT_PASSWORD", ""),
    )

    logger.debug(
        "Loaded credentials: botuser='%s' (botpass set: %s)",
        credentials.botuser,
        bool(credentials.botpass),
    )

    return credentials


def has_credentials(credentials: CredentialsConfig) -> bool:
    """Return True when the minimum bot credentials are available."""
    has = bool(credentials.botuser and credentials.botpass)
    logger.debug("has_credentials=%s", has)
    return has


def user_agent(
    project: ProjectConfig,
    credentials: CredentialsConfig,
) -> str:
    """
    Build an HTTP User-Agent compliant with Wikimedia's UA policy.

    Falls back to the generic 'tool' contact when no bot username is
    configured.
    """
    contact = credentials.botuser or "tool"
    return f"{project.name} (contact: {contact}; +{project.url})"


def load_wikis_config(
    paths: PathsConfig,
) -> dict:
    """Load the wikis configuration from config/wikis.yaml."""
    logger.debug("Loading wikis config from %s", paths.wikis_config_file)

    data = yaml.safe_load(paths.wikis_config_file.read_text(encoding="utf-8"))

    logger.info("Loaded wikis config: %d wiki(s)", len(data))
    return data


config = AppConfig(
    paths=PathsConfig.from_base_dir(BASE_DIR),
    credentials=load_credentials(),
    pageviews=PageviewsConfig(),
    wiki=WikiConfig(),
    project=ProjectConfig(),
    db=DbConfig.load(),
)


__all__ = [
    "AppConfig",
    "CredentialsConfig",
    "PageviewsConfig",
    "PathsConfig",
    "ProjectConfig",
    "WikiConfig",
    "config",
    "has_credentials",
    "load_credentials",
    "load_wikis_config",
    "user_agent",
]
