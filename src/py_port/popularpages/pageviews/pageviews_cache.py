"""
Cross-project pageviews cache.

Fetches monthly pageviews for every unique article title on a wiki **once** and
persists the results to ``data/views/<wiki>/<YYYY-MM>.jsonl`` so they survive
the run and can be reused by later runs.

On ``en.wikipedia`` many WikiProjects share the same popular articles (e.g.
*World War II*, *United States*). Without this cache each shared article would
be requested once per project that references it. The cache de-duplicates by
title across all projects for the month and writes the JSONL incrementally
(flushing at most once per :data:`config.pageviews.flush_titles` titles).

See docs/pageviews-persistence-and-dedup-plan.md.
"""

from __future__ import annotations

import logging
from pathlib import Path

import jsonlines

from ..config import config

logger = logging.getLogger(__name__)


class PageviewsCache:
    """
    Per-wiki, per-month pageviews cache backed by a JSONL file.

    Each line of the backing file is a JSON object ``{"title": ..., "views":
    ...}``. The cache is loaded on construction (so a previous run's data is
    reused) and appended to as new titles are fetched.
    """

    def __init__(self, wiki: str, year_month: str, pageviews_repo):
        """
        :param wiki: Wiki domain, e.g. 'en.wikipedia'.
        :param year_month: Month key, e.g. '2024-01'.
        :param pageviews_repo: A ``PageviewsRepository`` instance used to fetch
            any titles not already present in the cache.
        """
        self.wiki = wiki
        self.year_month = year_month
        self.repo = pageviews_repo
        self.path: Path = config.data_paths.views_data_dir / wiki / f"{year_month}.jsonl"

        self._cache: dict[str, int] = {}
        self._pending: list[tuple[str, int]] = []
        self._load()

    # ----------------------------------------------------------------
    # Loading / flushing
    # ----------------------------------------------------------------
    def _load(self) -> None:
        """Load any previously persisted titles for this wiki + month."""
        if not self.path.exists():
            return
        try:
            with jsonlines.open(self.path, mode="r") as reader:
                loaded = 0
                # skip_invalid drops lines that are not valid JSON; type=dict
                # drops any non-object lines. Malformed-but-valid objects
                # (e.g. missing a key) are caught below.
                for obj in reader.iter(type=dict, skip_invalid=True):
                    try:
                        self._cache[obj["title"]] = int(obj["views"])
                        loaded += 1
                    except (KeyError, TypeError, ValueError):
                        logger.debug("Skipping malformed cache object in %s: %r", self.path, obj)
        except OSError as exc:
            logger.warning("Could not read pageviews cache %s: %s", self.path, exc)
            return
        if loaded:
            logger.info("Loaded %d cached title(s) from %s", loaded, self.path)

    def _flush(self) -> None:
        """Append buffered title/view pairs to the JSONL file."""
        if not self._pending:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)

        with jsonlines.open(self.path, mode="a") as writer:
            for title, views in self._pending:
                writer.write({"title": title, "views": views})

        logger.debug("Flushed %d title(s) to %s", len(self._pending), self.path)
        self._pending = []

    # ----------------------------------------------------------------
    # Fetching
    # ----------------------------------------------------------------
    async def ensure(self, titles: set[str], start: str, end: str) -> None:
        """
        Make sure every title in ``titles`` has a cached view count.

        Titles already present in the cache (from this run or a previous one)
        are not re-fetched. Missing titles are fetched from the Pageviews API in
        batches of :data:`config.pageviews.fetch_batch` and written to the JSONL file as
        they accumulate (flushing at most once per :data:`config.pageviews.flush_titles`
        titles).

        :param titles: Unique article titles (spaces) to ensure.
        :param start: Start date in YYYYMMDD00 format.
        :param end: End date in YYYYMMDD00 format.
        """
        missing = [t for t in titles if t and t not in self._cache]
        if not missing:
            logger.info(
                "Pageviews cache %s/%s: %d title(s) already cached, nothing to fetch",
                self.wiki,
                self.year_month,
                len(self._cache),
            )
            return

        logger.info(
            "Pageviews cache %s/%s: fetching %d new title(s) (%d already cached)",
            self.wiki,
            self.year_month,
            len(missing),
            len(self._cache),
        )

        for i in range(0, len(missing), config.pageviews.fetch_batch):
            chunk = missing[i : i + config.pageviews.fetch_batch]
            views = await self.repo.get_title_views(chunk, start, end)
            for title in chunk:
                value = views.get(title, 0)
                self._cache[title] = value
                self._pending.append((title, value))
            if len(self._pending) >= config.pageviews.flush_titles:
                self._flush()

        # Flush any remainder so the on-disk file reflects the full run.
        self._flush()

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
        total = 0
        for title in [target, *redirects]:
            if title:
                total += self._cache.get(title, 0)
        return total


__all__ = [
    "PageviewsCache",
]
