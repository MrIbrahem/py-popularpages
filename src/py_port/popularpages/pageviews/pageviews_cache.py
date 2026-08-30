"""
Cross-project pageviews cache.

Fetches monthly pageviews for every unique article title on a wiki **once** and
persists the results to ``data/views/<wiki>/<YYYY-MM>.sqlite3`` so they survive
the run and can be reused by later runs.

On ``en.wikipedia`` many WikiProjects share the same popular articles (e.g.
*World War II*, *United States*). Without this cache each shared article would
be requested once per project that references it. The cache de-duplicates by
title across all projects for the month and upserts rows in batches of
:data:`config.pageviews.fetch_batch` titles (committing after each batch so a
partial run's progress is not lost).

Backed by SQLite via SQLAlchemy (sync engine -- SQLite I/O is local and fast
enough that async buys nothing here; only network calls to the Pageviews API,
made through :class:`PageviewsRepository`, are ``async``).

See docs/pageviews-persistence-and-dedup-plan.md.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker
from tqdm import tqdm

from ..config import config
from .pageviews_models import Base, PageView
from .pageviews_repository import PageviewsRepository

logger = logging.getLogger(__name__)


class PageviewsCache:
    """
    Per-wiki, per-month pageviews cache backed by a SQLite file.

    Each ``data/views/<wiki>/<YYYY-MM>.sqlite3`` file holds one ``pageviews``
    table (see :mod:`pageviews_models`) mapping title -> total views for that
    wiki and month. Reads (:meth:`get`) query SQLite directly rather than
    keeping an in-memory dict, so the process's memory footprint doesn't grow
    with the number of cached titles.
    """

    # Conservative chunk size, safely under SQLite's SQLITE_MAX_VARIABLE_NUMBER
    # even on older builds that cap at 999 (vs. 32766 on SQLite >=3.32.0).
    # See: https://www.sqlite.org/limits.html#max_variable_number
    _SELECT_IN_CHUNK_SIZE = 500
    
    def __init__(
        self,
        wiki: str,
        year_month: str,
        pageviews_repo: PageviewsRepository,
        path_dir: Path | None = None,
    ) -> None:
        """
        :param wiki: Wiki domain, e.g. 'en.wikipedia'.
        :param year_month: Month key, e.g. '2024-01'.
        :param pageviews_repo: A ``PageviewsRepository`` instance used to fetch
            any titles not already present in the cache.
        """
        self.wiki = wiki
        self.year_month = year_month
        self.repo = pageviews_repo

        _path_dir: Path = path_dir or config.data_paths.views_data_dir
        self.path: Path = _path_dir / wiki / f"{year_month}.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # SQLite creates the file on first connection if it doesn't exist yet.
        self._engine = create_engine(f"sqlite:///{self.path}", future=True)
        Base.metadata.create_all(self._engine)
        self._Session: sessionmaker[Session] = sessionmaker(bind=self._engine, future=True)

        logger.debug("Pageviews cache backed by %s", self.path)

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------
    def close(self) -> None:
        """Dispose of the underlying SQLite engine/connection pool."""
        self._engine.dispose()
        logger.debug("Closed pageviews cache %s", self.path)

    # ----------------------------------------------------------------
    # Fetching
    # ----------------------------------------------------------------
    async def ensure(self, titles: set[str], start: str, end: str) -> None:
        """
        Make sure every title in ``titles`` has a cached view count.

        Titles already present in the cache (from this run or a previous one)
        are not re-fetched. Missing titles are fetched from the Pageviews API in
        batches of :data:`config.pageviews.fetch_batch` and upserted into
        SQLite as they accumulate (committing once per batch).

        :param titles: Unique article titles (spaces) to ensure.
        :param start: Start date in YYYYMMDD00 format.
        :param end: End date in YYYYMMDD00 format.
        """
        missing = self._find_missing(titles)
        if not missing:
            logger.info(
                "Pageviews cache %s/%s: all %d title(s) already cached, nothing to fetch",
                self.wiki,
                self.year_month,
                len(titles),
            )
            return

        logger.info(
            "Pageviews cache %s/%s: fetching %d new title(s) (%d requested)",
            self.wiki,
            self.year_month,
            len(missing),
            len(titles),
        )

        batches = range(0, len(missing), config.pageviews.fetch_batch)

        for i in tqdm(batches, desc=f"Fetching pageviews for {len(missing):,} titles"):
            chunk = missing[i : i + config.pageviews.fetch_batch]
            views = await self.repo.get_title_views(chunk, start, end)

            self._upsert_many({title: views.get(title, 0) for title in chunk})

    def _find_missing(self, titles: set[str]) -> list[str]:
        """
        Return the subset of ``titles`` not already present in the cache.

        Performs one ``SELECT ... WHERE title IN (...)`` per chunk of at most
        ``_SELECT_IN_CHUNK_SIZE`` titles, rather than a single query with one
        bound parameter per title -- large projects (thousands of unique
        titles) could otherwise exceed SQLite's SQLITE_MAX_VARIABLE_NUMBER,
        which is as low as 999 on some system SQLite builds regardless of the
        Python/SQLite version in use.
        """
        wanted = [t for t in titles if t]
        if not wanted:
            return []

        cached: set[str] = set()
        with self._Session() as session:
            for i in range(0, len(wanted), self._SELECT_IN_CHUNK_SIZE):
                chunk = wanted[i : i + self._SELECT_IN_CHUNK_SIZE]
                cached.update(
                session.execute(select(PageView.title).where(PageView.title.in_(chunk))).scalars().all()
            )

        return [t for t in wanted if t not in cached]

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

    # ----------------------------------------------------------------
    # Lookup
    # ----------------------------------------------------------------
    def get(self, target: str, redirects: list[str]) -> int:
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


__all__ = [
    "PageviewsCache",
]
