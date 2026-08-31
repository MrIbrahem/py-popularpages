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
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

from tqdm import tqdm

from ..config import app_config
from .pageviews_db import PageviewsDb
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

    def __init__(
        self,
        wiki: str,
        start: date | str,
        end: date | str,
        pageviews_repo: PageviewsRepository,
        path_dir: Path | None = None,
        fetch_batch: int | None = None,
    ) -> None:
        """
        :param wiki: Wiki domain, e.g. 'en.wikipedia'.
        :param start: start date in datetime.date format
        :param end: end date in datetime.date format
        :param pageviews_repo: A ``PageviewsRepository`` instance used to fetch
            any titles not already present in the cache.
        """
        if fetch_batch is None:
            fetch_batch = app_config.pageviews.fetch_batch

        self.fetch_batch = fetch_batch

        self.wiki = wiki

        self.start = start if isinstance(start, date) else date.fromisoformat(start)
        self.end = end if isinstance(end, date) else date.fromisoformat(end)

        self.year_month = self.start.strftime("%Y-%m")
        self.repo = pageviews_repo

        db_file_path = self.build_db_file_path(wiki, self.year_month, path_dir)

        self.db = PageviewsDb(db_file_path)

    @staticmethod
    def build_db_file_path(wiki: str, year_month: str, path_dir: Path | None = None) -> Path:
        _path_dir: Path = path_dir or app_config.data_paths.views_data_dir

        _path: Path = _path_dir / wiki / f"{year_month}.sqlite3"
        _path.parent.mkdir(parents=True, exist_ok=True)

        return _path

    def _find_missing(self, titles: set[str]) -> list[str]:
        """
        Return the subset of ``titles`` not already present in the cache.
        """
        wanted = [t for t in titles if t]
        if not wanted:
            return []

        cached = self.db.query_titles_cache(wanted)

        return [t for t in wanted if t not in cached]

    async def ensure(self, titles: set[str]) -> None:
        """
        Make sure every title in ``titles`` has a cached view count.

        Titles already present in the cache (from this run or a previous one)
        are not re-fetched. Missing titles are fetched from the Pageviews API in
        batches of :data:`config.pageviews.fetch_batch` and upserted into
        SQLite as they accumulate (committing once per batch).

        :param titles: Unique article titles (spaces) to ensure.
        """
        start_date = self.start.strftime("%Y%m%d00")
        end_date = self.end.strftime("%Y%m%d00")

        logger.info(
            "Building pageviews cache for %d unique titles (window: %s-%s)",
            len(titles),
            start_date,
            end_date,
        )

        missing = self._find_missing(titles)
        if not missing:
            logger.info(
                "Pageviews cache %s/%s: all %d titles already cached, nothing to fetch",
                self.wiki,
                self.year_month,
                len(titles),
            )
            return

        already_cached = len(titles) - len(missing)
        logger.info(
            "Pageviews cache %s/%s: fetching %s new title (%s already cached)",
            self.wiki,
            self.year_month,
            f"{len(missing):,}",
            f"{already_cached:,}",
        )

        batches = range(0, len(missing), self.fetch_batch)

        for i in tqdm(batches, desc=f"Fetching pageviews for {len(missing):,} titles", disable=not sys.stderr.isatty()):
            chunk = missing[i : i + self.fetch_batch]
            views = await self.repo.get_title_views(chunk, start_date, end_date)

            fetched_views = {title: views.get(title, 0) for title in chunk}
            self.db.upsert_many(fetched_views)


__all__ = [
    "PageviewsCache",
]
