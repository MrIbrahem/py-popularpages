"""
pageviews db.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from .pageviews_models import Base, PageView

logger = logging.getLogger(__name__)


class PageviewsDb:

    # Conservative chunk size, safely under SQLite's SQLITE_MAX_VARIABLE_NUMBER
    # even on older builds that cap at 999 (vs. 32766 on SQLite >=3.32.0).
    # See: https://www.sqlite.org/limits.html#max_variable_number
    _SELECT_IN_CHUNK_SIZE = 500

    def __init__(self, db_file_path: Path) -> None:
        """
        :param db_file_path: Path to the SQLite database file.
        """
        self.db_file_path = db_file_path

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
    @staticmethod
    def _chunked(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
        """Yield successive chunks of ``items`` of at most ``size`` elements."""
        for i in range(0, len(items), size):
            yield items[i : i + size]

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
    def upsert_many(self, title_views: dict[str, int]) -> None:
        """Upsert a batch of title -> views pairs, committing once for the batch."""
        if not title_views:
            return

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

    def get_views(self, target: str, redirects: list[str]) -> int:
        """
        Return the total views for a target page plus its redirects.

        :param target: Target page title (spaces).
        :param redirects: Redirect titles (spaces) associated with the target.
        :return: Sum of cached views across target + redirects.
        """
        return self.get_views_many2({target: redirects}).get(target, 0)

    def get_views_many2(
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
        targets = list(targets_to_redirects.keys())
        title_to_targets = self.map_titles_to_targets(targets, targets_to_redirects)

        views_by_title = self._query_views_by_title(list(title_to_targets))

        result: dict[str, int] = {}
        for target in targets:
            result[target] = views_by_title.get(target, 0)

            for title in targets_to_redirects.get(target, []):
                result[target] += views_by_title.get(title, 0)

        return result

    def map_titles_to_targets(self, targets, redirects_by_target) -> dict[str, list[str]]:
        """
        Maps canonical targets and their associated redirect titles back to the original targets.

        This method iterates through a collection of targets and their corresponding
        redirect titles. It constructs a dictionary where each key is a title (either
        a target itself or one of its redirects), and the corresponding value is a
        list of original targets that the title maps to or redirects to.

        Args:
            targets (Iterable[str]): A collection of canonical target strings.
            redirects_by_target (dict[str, list[str]]): A dictionary mapping a target
                string to a list of its redirect titles.

        Returns:
            dict[str, list[str]]: A dictionary mapping each title (target or redirect)
                to a list of original targets it is associated with.
        """
        t2t: dict[str, list[str]] = {}
        for target in targets:
            for title in (target, *redirects_by_target.get(target, [])):
                if title:
                    t2t.setdefault(title, []).append(target)

        return t2t


__all__ = [
    "PageviewsDb",
]
