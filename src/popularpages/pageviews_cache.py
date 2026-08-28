"""
Cross-project pageviews cache.

Fetches monthly pageviews for every unique article title on a wiki **once** and
persists the results to ``data/views/<wiki>/<YYYY-MM>.jsonl`` so they survive
the run and can be reused by later runs.

On ``en.wikipedia`` many WikiProjects share the same popular articles (e.g.
*World War II*, *United States*). Without this cache each shared article would
be requested once per project that references it. The cache de-duplicates by
title across all projects for the month and writes the JSONL incrementally
(flushing at most once per :data:`VIEWS_FLUSH_TITLES` titles).

See docs/pageviews-persistence-and-dedup-plan.md.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import VIEWS_DATA_DIR, VIEWS_FETCH_BATCH, VIEWS_FLUSH_TITLES

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
        self.path: Path = VIEWS_DATA_DIR / wiki / f"{year_month}.jsonl"

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
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read pageviews cache %s: %s", self.path, exc)
            return

        loaded = 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                self._cache[obj["title"]] = int(obj["views"])
                loaded += 1
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                logger.debug("Skipping malformed cache line in %s: %r", self.path, line)
        if loaded:
            logger.info("Loaded %d cached title(s) from %s", loaded, self.path)

    def _flush(self) -> None:
        """Append buffered title/view pairs to the JSONL file."""
        if not self._pending:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            for title, views in self._pending:
                f.write(json.dumps({"title": title, "views": views}, ensure_ascii=False) + "\n")
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
        batches of :data:`VIEWS_FETCH_BATCH` and written to the JSONL file as
        they accumulate (flushing at most once per :data:`VIEWS_FLUSH_TITLES`
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

        for i in range(0, len(missing), VIEWS_FETCH_BATCH):
            chunk = missing[i : i + VIEWS_FETCH_BATCH]
            views = await self.repo.get_title_views(chunk, start, end)
            for title in chunk:
                value = views.get(title, 0)
                self._cache[title] = value
                self._pending.append((title, value))
            if len(self._pending) >= VIEWS_FLUSH_TITLES:
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
