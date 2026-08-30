"""
pageviews db.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from ..config import app_config
from .pageviews_models import Base, PageView

logger = logging.getLogger(__name__)


class PageviewsDb:

    # Conservative chunk size, safely under SQLite's SQLITE_MAX_VARIABLE_NUMBER
    # even on older builds that cap at 999 (vs. 32766 on SQLite >=3.32.0).
    # See: https://www.sqlite.org/limits.html#max_variable_number
    _SELECT_IN_CHUNK_SIZE = 500

    def __init__(
        self,
        wiki: str,
        year_month: str,
        path_dir: Path | None = None,
    ) -> None:
        """
        :param wiki: Wiki domain, e.g. 'en.wikipedia'.
        :param year_month: Month key, e.g. '2024-01'.
        """
        self.wiki = wiki

        _path_dir: Path = path_dir or app_config.data_paths.views_data_dir
        self.path: Path = _path_dir / wiki / f"{year_month}.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # SQLite creates the file on first connection if it doesn't exist yet.
        self._engine = create_engine(f"sqlite:///{self.path}", future=True)
        Base.metadata.create_all(self._engine)
        self._Session: sessionmaker[Session] = sessionmaker(bind=self._engine, future=True)

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------
    def close(self) -> None:
        """Dispose of the underlying SQLite engine/connection pool."""
        self._engine.dispose()
        logger.debug("Closed pageviews cache %s", self.path)

    def query_titles_from_db(self, chunk: list[str]) -> Sequence[str]:
        with self._Session() as session:
            query = select(PageView.title).where(PageView.title.in_(chunk))
            result = session.execute(query).scalars().all()
            return result

    def _upsert_many(self, title_views: dict[str, int]) -> None:
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

        logger.debug("Upserted %d title(s) into %s", len(title_views), self.path)

    def query_titles_cache(self, wanted: list[str]) -> set[str]:
        cached: set[str] = set()
        for i in range(0, len(wanted), self._SELECT_IN_CHUNK_SIZE):
            chunk = wanted[i : i + self._SELECT_IN_CHUNK_SIZE]
            result = self.query_titles_from_db(chunk)
            cached.update(result)
        return cached

    # ----------------------------------------------------------------
    # Lookup
    # ----------------------------------------------------------------
    def get_views(self, target: str, redirects: list[str]) -> int:
        """
        Return the total views for a target page plus its redirects.

        :param target: Target page title (spaces).
        :param redirects: Redirect titles (spaces) associated with the target.
        :return: Sum of cached views across target + redirects.
        """
        titles = [t for t in [target, *redirects] if t]
        if not titles:
            return 0

        with self._Session() as session:
            rows = session.execute(select(PageView.views).where(PageView.title.in_(titles))).scalars().all()

        return sum(rows)

    def get_views_many(
        self,
        targets: list[str],
        redirects_by_target: dict[str, list[str]],
    ) -> dict[str, int]:
        """
        Bulk variant of :meth:`get_views` for many targets at once.

        Instead of one SQLite query per target -- which, for projects with
        hundreds of thousands of titles, means hundreds of thousands of
        session opens plus round-trips -- this resolves every unique title
        across all targets and their redirects in a handful of chunked
        ``SELECT ... WHERE title IN (...)`` queries that reuse a single
        session, then aggregates the per-title views back to each target.

        :param targets: Target page titles (spaces).
        :param redirects_by_target: Map of target -> its redirect titles.
        :return: Map of target -> total views (target + redirects).
        """
        # Map every unique title to the targets that reference it (as the
        # target itself or as one of its redirects). A title may be referenced
        # by more than one target, so keep a list.
        title_to_targets: dict[str, list[str]] = {}
        for target in targets:
            for title in (target, *redirects_by_target.get(target, [])):
                if title:
                    title_to_targets.setdefault(title, []).append(target)

        if not title_to_targets:
            return dict.fromkeys(targets, 0)

        views_by_title: dict[str, int] = {}
        with self._Session() as session:
            titles = list(title_to_targets)
            for i in range(0, len(titles), self._SELECT_IN_CHUNK_SIZE):
                chunk = titles[i : i + self._SELECT_IN_CHUNK_SIZE]
                rows = session.execute(select(PageView.title, PageView.views).where(PageView.title.in_(chunk))).all()
                for title, views in rows:
                    views_by_title[title] = views

        result: dict[str, int] = {}
        for target in targets:
            total = 0
            for title in (target, *redirects_by_target.get(target, [])):
                if title:
                    total += views_by_title.get(title, 0)
            result[target] = total
        return result


__all__ = [
    "PageviewsDb",
]
