"""
Configuration & credential loading.

Credentials (bot username/password) are read from a ``.env`` file via
python-dotenv, falling back to real environment variables.
``.env.example`` is the committed template:

    cp .env.example .env
    # then edit .env with your bot username/password (from Special:BotPasswords)

The whole application reads its settings through the single :data:`app_config`
instance of :class:`AppConfig`. No other module should read raw environment
variables or hard-code paths/limits; import :data:`app_config` from this module
and access the nested sub-configs (``app_config.paths``, ``app_config.credentials``,
``app_config.pageviews``, ``app_config.wiki``, ``app_config.project``).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def resolve_path(_path: str | Path) -> Path:
    """Expand environment variables and user home directory in paths."""
    _path = os.path.expandvars(str(_path))
    path = Path(_path).expanduser()
    return path


@dataclass(frozen=True)
class OtherConfig:
    """configs not in specific sections"""

    user_agent: str

    @classmethod
    def load(cls) -> OtherConfig:
        user_agent = os.getenv(
            "USER_AGENT",
            "Popular Pages/0.1.0 (https://popular_pages.toolforge.org; tools.popular_pages@toolforge.org)",
        )
        return cls(
            user_agent=user_agent,
        )


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
        return bool(self.user and self.password)


@dataclass(frozen=True)
class ProjectPathsConfig:
    """Application filesystem paths."""

    base_dir: Path
    views_dir: Path
    messages_dir: Path
    wikis_config_file: Path

    @classmethod
    def load(cls) -> ProjectPathsConfig:
        """
        Create a paths configuration rooted at the specified base directory.

        Returns:
            ProjectPathsConfig: Configuration containing paths derived from the base directory.
        """
        base_dir = Path(__file__).parent.parent.parent
        return cls(
            base_dir=base_dir,
            views_dir=base_dir / "views",
            messages_dir=base_dir / "messages",
            wikis_config_file=base_dir / "config" / "wikis.yaml",
        )

    def load_wikis_config(self) -> dict[str, dict[str, str]]:
        """
        Load the wikis configuration from config/wikis.yaml.
        Data example: {
            "en.wikipedia": {
                "database": "enwiki",
                "index": "User:Community Tech bot/Popular pages",
                "config": "Wikipedia:WikiProject/Popular pages config.json",
                "category": "Category:Lists of popular pages by WikiProject"
            },
            "ar.wikipedia": {
                "database": "arwiki",
                "index": "ويكيبيديا:قائمة الصفحات الأكثر مشاهدة حسب مشروع الويكي",
                "config": "ويكيبيديا:قائمة الصفحات الأكثر مشاهدة حسب مشروع الويكي/الإعدادات.json",
                "category": "تصنيف:قائمة الصفحات الأكثر مشاهدة حسب مشروع الويكي"
            }
        }
        """
        logger.debug("Loading wikis config from %s", self.wikis_config_file)

        data = yaml.safe_load(self.wikis_config_file.read_text(encoding="utf-8"))

        logger.info("Loaded wikis config: %d wiki(s)", len(data))
        return data


@dataclass(frozen=True)
class DataPathsConfig:
    """
    Application filesystem paths.
    """

    popular_pages_dir: Path
    data_dir: Path
    views_data_dir: Path
    log_dir: Path

    @classmethod
    def load(cls) -> DataPathsConfig:
        """
        Create a paths configuration rooted at the specified base directory.

        Returns:
            DataPathsConfig: Configuration containing paths derived from the base directory.
        """
        base_dir = os.getenv("POPULAR_PAGES_MAIN_DIR")

        popular_pages_dir = Path(resolve_path(base_dir)) if base_dir else Path().resolve() / "popular_pages_dir"

        data_dir = popular_pages_dir / "data"

        data = cls(
            popular_pages_dir=popular_pages_dir,
            data_dir=data_dir,
            views_data_dir=data_dir / "views",
            log_dir=popular_pages_dir / "logs",
        )

        data.log_dir.mkdir(parents=True, exist_ok=True)
        data.views_data_dir.mkdir(parents=True, exist_ok=True)

        return data

    def build_db_file_path(self, wiki: str, year_month: str, path_dir: Path | None = None) -> Path:
        """Constructs and returns the database file path for a given wiki and month.

        Creates the parent directories for the database file if they do not already exist.

        Args:
            wiki (str): The name or identifier of the wiki.
            year_month (str): The year and month string, typically formatted as 'YYYY-MM'.
            path_dir (Path | None, optional): The base directory where the database
                file should be stored. Defaults to None, which falls back to
                `self.views_data_dir`.

        Returns:
            Path: The full path to the SQLite3 database file.

        Usage:
            app_config.data_paths.build_db_file_path(wiki, year_month)
        """
        _path_dir: Path = path_dir or self.views_data_dir

        _path: Path = _path_dir / wiki / f"{year_month}.sqlite3"
        _path.parent.mkdir(parents=True, exist_ok=True)

        return _path


@dataclass(frozen=True)
class CredentialsConfig:
    """Wikipedia bot credentials."""

    botuser: str = ""
    botpass: str = ""

    @classmethod
    def load(cls) -> CredentialsConfig:
        """
        Load Wikipedia bot credentials from environment variables.

        Environment variables take precedence over values loaded from ``.env``.
        """
        credentials = cls(
            botuser=os.environ.get("WIKIPEDIA_BOT_USERNAME", ""),
            botpass=os.environ.get("WIKIPEDIA_BOT_PASSWORD", ""),
        )

        logger.debug(
            "Loaded credentials: botuser='%s' (botpass set: %s)",
            credentials.botuser,
            bool(credentials.botpass),
        )

        return credentials

    def has_credentials(self) -> bool:
        """Return True when the minimum bot credentials are available."""
        return bool(self.botuser and self.botpass)


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

    @classmethod
    def load(cls) -> PageviewsConfig:
        return cls()


@dataclass(frozen=True)
class WikiConfig:
    """Wikipedia-related configuration."""

    fallback_lang: str = "en"
    max_project_size: int = 1_000_000
    assessment_config_url: str = "https://xtools.wmcloud.org/api/project/assessments"

    @classmethod
    def load(cls) -> WikiConfig:
        return cls()


@dataclass(frozen=True)
class AppConfig:
    """
    Application configuration.
    """

    paths: ProjectPathsConfig
    data_paths: DataPathsConfig
    credentials: CredentialsConfig
    pageviews: PageviewsConfig
    wiki: WikiConfig
    db: DbConfig
    other: OtherConfig

    @classmethod
    def load(cls) -> AppConfig:
        return cls(
            paths=ProjectPathsConfig.load(),
            data_paths=DataPathsConfig.load(),
            credentials=CredentialsConfig.load(),
            pageviews=PageviewsConfig(),
            wiki=WikiConfig(),
            db=DbConfig.load(),
            other=OtherConfig.load(),
        )


app_config = AppConfig.load()

__all__ = [
    "AppConfig",
    "CredentialsConfig",
    "PageviewsConfig",
    "ProjectPathsConfig",
    "DataPathsConfig",
    "WikiConfig",
    "OtherConfig",
    "app_config",
]
