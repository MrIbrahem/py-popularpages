"""
pageviews db.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from .pageviews_models import Base, PageView

logger = logging.getLogger(__name__)


class PageviewsDb:

    # Conservative chunk size, safely under SQLite's SQLITE_MAX_VARIABLE_NUMBER
    # even on older builds that cap at 999 (vs. 32766 on SQLite >=3.32.0).
    # See: https://www.sqlite.org/limits.html#max_variable_number
    _SELECT_IN_CHUNK_SIZE = 500

    def __init__(self, db_file_path: Path, converte_underscore_to_space: bool = True,) -> None:
        """
        Open (creating if needed) the SQLite pageviews cache at the given path.

        The SQLAlchemy engine and session factory are initialized here, and the
        ``PageView`` table schema is created on first connection. The file is
        created by SQLite automatically if it does not already exist.

        Args:
            db_file_path (Path): Path to the SQLite database file.
            converte_underscore_to_space (bool): Whether to convert underscores to spaces in titles.
        """
        self.db_file_path = db_file_path
        self.converte_underscore_to_space = converte_underscore_to_space

        # SQLite creates the file on first connection if it doesn't exist yet.
        self._engine = create_engine(f"sqlite:///{self.db_file_path}", future=True)
        Base.metadata.create_all(self._engine)
        self._Session: sessionmaker[Session] = sessionmaker(bind=self._engine, future=True)

    # ---------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------
    def close_db(self) -> None:
        """Dispose of the underlying SQLite engine/connection pool."""
        self._engine.dispose()
        logger.debug("Closed pageviews cache %s", self.db_file_path)

    # ---------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------
    def _converte_underscore_to_space(self, title: str) -> str:
        if not self.converte_underscore_to_space:
            return title

        return title.replace("_", " ")

    @staticmethod
    def _chunked(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
        """Yield successive chunks of ``items`` of at most ``size`` elements."""
        for i in range(0, len(items), size):
            yield items[i : i + size]

    def one_title_views(self, title: str) -> int | None:
        """
        Retrieve the total number of page views for a specific title.

        Args:
            title (str): The title of the page to query views for.

        Returns:
            int | None: The number of views for the given title, or None if the title does not exist.
        """
        with self._Session() as session:
            query = select(PageView.title, PageView.views).where(PageView.title == title)
            result = session.execute(query).first()
            return result.views if result else None

    def _query_views_by_title(self, titles: list[str]) -> dict[str, int]:
        """
        Resolve title -> views for many titles, reusing a single session and
        querying in chunks to stay under SQLite's bound-variable limit.

        This is the single query primitive used by both the "does this title
        exist" lookups and the "what are its views" lookups, since the latter
        is a strict superset of the former (a title with no cached views
        simply won't appear as a key in the result).
        """
        views_by_title: dict[str, int] = {}

        with self._Session() as session:
            for chunk in self._chunked(titles, self._SELECT_IN_CHUNK_SIZE):
                query = select(PageView.title, PageView.views).where(PageView.title.in_(chunk))
                for title, views in session.execute(query).all():
                    views_by_title[title] = views

        return views_by_title

    # ---------------------------------------------------
    # Writes
    # ---------------------------------------------------

    def upsert_many_chunks(self, title_views: dict[str, int]) -> None:
        """
        to solve sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) too many SQL variables
        """
        if not title_views:
            return

        # Only SQLite has the SQLITE_MAX_VARIABLE_NUMBER constraint we're
        # working around here. For other dialects, fall back to a large chunk.
        if self._engine.dialect.name == "sqlite":
            # sqlite_version_info is a tuple (major, minor, patch); >= 3.32.0
            # raises SQLITE_MAX_VARIABLE_NUMBER from 999 to 32766.
            chunk_size = 30_000 if sqlite3.sqlite_version_info >= (3, 32, 0) else 900
        else:
            # Non-SQLite dialects (Postgres/MySQL) don't enforce SQLite's
            # SQLITE_MAX_VARIABLE_NUMBER; use a conservative batch size.
            chunk_size = 10_000

        if len(title_views) < chunk_size:
            self.upsert_many(title_views)
            return

        total = len(title_views)
        written = 0

        for i in range(0, len(title_views), chunk_size):
            batch = dict(list(title_views.items())[i : i + chunk_size])
            self.upsert_many(batch)
            written += len(batch)
            logger.debug("Upserted %d/%d rows", written, total)

    def upsert_many(self, title_views: dict[str, int]) -> None:
        """Upsert a batch of title -> views pairs, committing once for the batch."""
        if not title_views:
            return

        # Store titles without underscores (display form) from the moment they
        # enter the cache, so lookups never have to guess at the title format.
        title_views = {self._converte_underscore_to_space(title): views for title, views in title_views.items()}

        with self._Session() as session:
            stmt = sqlite_insert(PageView).values(
                [{"title": title, "views": views} for title, views in title_views.items()]
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[PageView.title],
                set_={"views": stmt.excluded.views},
            )
            session.execute(stmt)
            session.commit()

        logger.debug("Upserted %d titles into %s", len(title_views), self.db_file_path)

    # ---------------------------------------------------
    # Lookup
    # ---------------------------------------------------
    def query_titles_cache(self, wanted: list[str]) -> set[str]:
        """Return the subset of ``wanted`` titles already present in the cache."""
        if not wanted:
            return set()

        views_by_title = self._query_views_by_title(wanted)

        return set(views_by_title)

    def count_titles(self) -> int:
        """Return the number of distinct titles currently cached in this file."""
        with self._Session() as session:
            return int(session.scalar(select(func.count()).select_from(PageView)) or 0)

    def get_views(self, target: str, redirects: list[str]) -> int:
        """
        Return the total views for a target page plus its redirects.

        Looks up the cached view counts for the target title and each of its
        redirect titles, then sums them. A thin convenience wrapper around
        :meth:`get_views_many` for the single-target case.

        Args:
            target (str): Target page title (spaces).
            redirects (list[str]): Redirect titles (spaces) associated with the target.

        Returns:
            int: Sum of cached views across target + redirects.
        """
        return self.get_views_many({target: redirects}).get(target, 0)

    def get_views_many(
        self,
        targets_to_redirects: dict[str, list[str]],
    ) -> dict[str, int]:
        """
        Bulk variant of :meth:`get_views` for many targets at once.

        Instead of one SQLite query per target -- which, for projects with
        hundreds of thousands of titles, means hundreds of thousands of
        session opens plus round-trips -- this resolves every unique title
        across all targets and their redirects in a handful of chunked
        ``SELECT ... WHERE title IN (...)`` queries that reuse a single
        session, then aggregates the per-title views back to each target.

        """
        # 1. Collect all unique titles (targets + redirects) directly into a set
        all_titles = {
            title for target, redirects in targets_to_redirects.items() for title in (target, *redirects) if title
        }

        # 2. Fetch view counts for all unique titles in a single batch query
        views_by_title = self._query_views_by_title(list(all_titles))

        # 3. Aggregate view counts back to each main target
        result: dict[str, int] = {}
        for target, redirects in targets_to_redirects.items():
            total_views = views_by_title.get(target, 0)
            for redirect in redirects:
                total_views += views_by_title.get(redirect, 0)
            result[target] = total_views

        return result


__all__ = [
    "PageviewsDb",
]
