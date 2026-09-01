"""
Load monthly Wikimedia ``pageview_complete`` dumps directly from the
Toolforge NFS mount into the existing per-wiki/month SQLite pageviews cache.

This replaces the REST-API-per-title fetching path (:class:`PageviewsRepository`)
for wikis/months where the bulk dump is available, per the plan:

    1. Open the monthly dump directly from
       ``/public/dumps/public/other/pageview_complete/...`` (no download step).
    2. Stream and parse it (bzip2, line-by-line -- never fully decompressed
       to a temp file).
    3. Filter to only the wiki codes we actually care about (from
       ``config/wikis.yaml``).
    4. Aggregate monthly totals per title (``title`` is the aggregation key,
       *not* ``page_id`` -- see :mod:`pageviews_dumps_parser` for why).
    5. Write results into the existing per-wiki/month SQLite cache via
       :class:`PageviewsDb`, using the existing ``PageView`` model, so
       downstream code needs no changes -- only the *source* changes.

Only ``wiki_code``, ``title``, and ``daily_total`` are used; ``page_id`` and
``hourly_counts`` are parsed (where present) and discarded, per the plan.
"""

from __future__ import annotations

import bz2
import logging
from collections.abc import Iterable, Iterator
from pathlib import Path

from ..config import app_config
from ..pageviews.pageviews_db import PageviewsDb
from ..utils import get_memory
from .pageviews_dumps_parser import MalformedLineError, ParsedPageview

logger = logging.getLogger(__name__)


class DumpNotFoundError(FileNotFoundError):
    """Raised when the expected monthly dump file doesn't exist on disk."""


class PageviewsDumpLoader:
    """
    Loads monthly ``pageview_complete`` dumps into the per-wiki/month SQLite
    pageviews cache.

    An instance is bound to a ``views_dir`` (where per-wiki/month SQLite
    caches are written) and a ``dumps_root`` (where the monthly dumps are
    read from on the Toolforge NFS mount). Both default to the real
    locations and are only overridden in tests.
    """

    # Toolforge NFS mount root for the pageview_complete dumps. Confirmed
    # present at this path per the plan; not yet verified against the live
    # mount for the exact monthly filename pattern (open question in the
    # plan).
    DUMPS_ROOT: Path = Path("/public/dumps/public/other/pageview_complete/monthly")

    # How often to log progress while streaming a multi-GB file, in lines.
    _PROGRESS_LOG_EVERY = 1_000_000

    # Upper bound on the number of distinct (wiki, title) entries held in
    # memory at once across all wikis. The dump can contain billions of
    # distinct titles, so memory is driven by how many we accumulate
    # *between* cache flushes, not by the number of raw lines. We flush (and
    # free) as soon as this many distinct titles are buffered, which keeps
    # peak memory bounded regardless of how the titles are distributed across
    # wikis or how many lines the dump has.
    _MAX_ACCUMULATED_TITLES = 500_000

    def __init__(
        self,
        views_dir: Path = app_config.paths.views_dir,
        dumps_root: Path = DUMPS_ROOT,
    ) -> None:
        """
        Bind the loader to a views directory and a dumps root.

        The loader writes per-wiki/month SQLite pageviews caches under
        ``views_dir`` and reads the monthly ``pageview_complete`` dumps from
        ``dumps_root``. Both default to the real Toolforge locations and are
        only overridden in tests.

        Args:
            views_dir (Path): Root ``data/views`` directory. Each wiki's cache
                file is written to ``<views_dir>/<wiki_code>/<YYYY-MM>.sqlite3``.
            dumps_root (Path): Override for the dumps root directory (mainly for
                tests).
        """
        self.views_dir = views_dir
        self.dumps_root = dumps_root

    def _dump_path_for_month(self, year: int, month: int) -> Path:
        """
        Build the on-disk path to a monthly ``pageview_complete`` dump.

        Constructs the expected filesystem location of the compressed dump for
        the given year and month, following the Toolforge ``pageview_complete``
        directory layout. The returned path is then opened by
        :meth:`_iter_dump_lines`.

        Args:
            year (int): Dump year, e.g. 2026.
            month (int): Dump month, 1-12.

        Returns:
            Path: e.g.
                ``/public/dumps/public/other/pageview_complete/monthly/2026/2026-07/pageviews-202607-user.bz2``
        """
        yyyymm = f"{year:04d}{month:02d}"
        return self.dumps_root / f"{year:04d}" / f"{year:04d}-{month:02d}" / f"pageviews-{yyyymm}-user.bz2"

    def _iter_dump_lines(self, dump_file: Path) -> Iterator[str]:
        """
        Stream lines from a bz2-compressed dump file, one at a time.

        Never decompresses the whole file to disk or into memory at once --
        ``bz2.open`` in text mode streams and decompresses incrementally as
        the file object is iterated.

        Args:
            dump_file (Path): Path to the bz2-compressed dump file.

        Raises:
            DumpNotFoundError: if ``dump_file`` doesn't exist.
        """
        if not dump_file.exists():
            raise DumpNotFoundError(f"Pageviews dump not found: {dump_file}")

        with bz2.open(dump_file, "rt", encoding="utf-8", errors="replace") as f:
            yield from f

    def _write_totals_to_cache(
        self,
        totals_by_wiki: dict[str, dict[str, int]],
        yyyy_mm: str,
    ) -> None:
        """
        Write aggregated per-wiki totals into the existing per-wiki/month
        SQLite cache, via :class:`PageviewsDb`, using batched upserts.

        For each wiki present in ``totals_by_wiki``, opens the corresponding
        cache file and upserts the title -> total mapping, logging progress and
        freeing the connection afterward so peak memory and open handles stay
        bounded across the whole dump.

        Args:
            totals_by_wiki (dict[str, dict[str, int]]): ``{wiki_code: {title: total_titles}}``, as
                returned by :meth:`_process_dump_lines`.
            yyyy_mm (str): ``YYYY-MM`` string used to build the cache filename.
        """
        for wiki_code, title_views in totals_by_wiki.items():
            if not title_views:
                continue

            db_file_path = app_config.data_paths.build_db_file_path(
                wiki_code,
                yyyy_mm,
                self.views_dir,
            )

            logger.info("[%s] Writing %s titles to %s", wiki_code, f"{len(title_views):,}", db_file_path.name)
            db = PageviewsDb(db_file_path)
            try:
                # upsert_many_chunks will handle the batch_size
                db.upsert_many_chunks(title_views)
            finally:
                db.close_db()

            logger.info("[%s] Upsert done", wiki_code)

    def _process_dump_lines(
        self,
        lines: Iterable[str],
        wanted_wiki_codes: set[str],
        yyyy_mm: str,
        wanted_titles_by_wiki: dict[str, set[str]] | None = None,
    ) -> dict[str, int]:
        """
        Single pass over dump lines, aggregating ``daily_total`` per (wiki, title).

        Each line is filtered down to the configured wikis and, optionally, a
        per-wiki allow-list of titles before its ``daily_total`` is added to a
        running total. Malformed lines are logged and skipped rather than
        aborting the whole (multi-hour, multi-GB) run over a handful of bad
        rows. The exact distinct-title count is read back from the SQLite cache
        afterward.

        Args:
            lines (Iterable[str]): An iterable of raw dump lines (e.g. from
                :meth:`_iter_dump_lines`).
            wanted_wiki_codes (set[str]): Only lines whose ``wiki_code`` is in this
                set are kept; everything else is skipped immediately (before any
                title unescaping/aggregation work).
            yyyy_mm (str): ``YYYY-MM`` string used to build the cache filename.
            wanted_titles_by_wiki (dict[str, set[str]] | None): Optional per-wiki set of titles to
                keep. When a wiki code has an entry here, only those titles are
                aggregated for that wiki (memory optimization for wikis where the
                full set of titles we'll ever need is known ahead of time from
                WikiProject configs, mirroring the old REST-API approach's
                per-title fetching). A wiki code with *no* entry in this dict (or
                when the whole parameter is ``None``) has all its titles
                aggregated, unfiltered.

        Returns:
            dict[str, int]: ``{wiki_code: total_titles}`` for every wiki in
                ``wanted_wiki_codes`` that had at least one matching line.

        Malformed lines are logged and skipped rather than aborting the whole
        (multi-hour, multi-GB) run over a handful of bad rows.
        """
        totals_len: dict[str, int] = {}
        totals: dict[str, dict[str, int]] = {}

        zero_daily_total_count = 0
        malformed_count = 0
        line_count = 0
        valid_lines_count = 0
        accumulated_titles = 0

        for wiki in wanted_wiki_codes:
            totals[wiki] = {}
            totals_len[wiki] = 0

        def _flush() -> None:
            nonlocal accumulated_titles
            # Update per-wiki distinct-title counts (titles only count once,
            # even though the dict holds their running total which may
            # change on later flushes for the same title).

            # 1. update totals lengths
            for wiki_code, rows in totals.items():
                totals_len[wiki_code] += len(rows)

            # 2. save batch to cache
            self._write_totals_to_cache(
                totals_by_wiki=totals,
                yyyy_mm=yyyy_mm,
            )

            # 3. clear the dicts to free memory, but keep the outer dict keys
            # for the next batch
            for wiki_code in totals.keys():
                totals[wiki_code] = {}

            accumulated_titles = 0

        for line in lines:
            line_count += 1
            if line_count % self._PROGRESS_LOG_EVERY == 0:
                logger.info(get_memory())
                logger.info("Processed %s dump lines so far...", f"{line_count:,}")
                logger.info(
                    "malformed_count: %s, with valid lines: %s, with zero total: %s",
                    f"{malformed_count:,}",
                    f"{valid_lines_count:,}",
                    f"{zero_daily_total_count:,}",
                )

            # Cheap pre-filter before doing any real parsing work: every line
            # starts with "wiki_code ", so we can reject the vast majority of
            # lines (wikis we don't care about) with a simple prefix check
            # instead of splitting+parsing every single line in the file.
            space_idx = line.find(" ")
            if space_idx == -1:
                continue

            wiki_code = line[:space_idx]
            if wiki_code not in wanted_wiki_codes:
                continue

            try:
                parsed = ParsedPageview.parse(line)
            except MalformedLineError:
                malformed_count += 1
                logger.debug("Skipping malformed line: %r", line)
                continue

            cache_title = parsed.title.replace("_", " ")

            if wanted_titles_by_wiki is not None:
                wanted_titles = wanted_titles_by_wiki.get(parsed.wiki_code)
                if wanted_titles is not None and cache_title not in wanted_titles:
                    continue

            valid_lines_count += 1

            wiki_totals = totals[parsed.wiki_code]
            if cache_title not in wiki_totals:
                accumulated_titles += 1

            # skip lines without page_id such as disambiguation pages
            if not parsed.is_valid():
                continue

            if parsed.daily_total == 0:
                zero_daily_total_count += 1
                continue

            wiki_totals[cache_title] = wiki_totals.get(cache_title, 0) + parsed.daily_total

            if accumulated_titles >= self._MAX_ACCUMULATED_TITLES:
                _flush()

        # Save any remaining buffered titles to cache.
        _flush()

        if malformed_count:
            logger.warning("Skipped %d malformed line(s) while processing dump.", malformed_count)

        logger.info(
            "Finished processing %s total dump lines., with valid lines: %s, with zero total: %s",
            f"{line_count:,}",
            f"{valid_lines_count:,}",
            f"{zero_daily_total_count:,}",
        )

        # The exact distinct-title total is not required
        return totals_len

    def load_dump_into_cache(
        self,
        year: int,
        month: int,
        wanted_wiki_codes: set[str],
        wanted_titles_by_wiki: dict[str, set[str]] | None = None,
    ) -> dict[str, int]:
        """
        End-to-end entry point: locate the monthly dump, stream-parse it,
        aggregate totals for the configured wikis, and write them into the
        per-wiki/month SQLite cache.

        Resolves the dump path for the given month, streams and aggregates its
        lines for the requested wiki codes (and optional title allow-list), and
        returns the distinct-title counts read back from cache. Callers should
        catch :class:`DumpNotFoundError` and fall back to the REST API path.

        Args:
            year (int): Dump year, e.g. 2026.
            month (int): Dump month, 1-12.
            wanted_wiki_codes (set[str]): Wiki codes to keep, e.g. the keys of
                ``config/wikis.yaml`` (``{"en.wikipedia", "ar.wikipedia", ...}``).
            wanted_titles_by_wiki (dict[str, set[str]] | None): Optional per-wiki title allow-list; see
                :meth:`_process_dump_lines`.

        Returns:
            dict[str, int]: ``{wiki_code: number_of_distinct_titles}`` for every wiki
                that had at least one matching line in the dump (read back from
                the SQLite cache after writing).

        Raises:
            DumpNotFoundError: if the dump for ``year``/``month`` isn't
                present on disk yet -- callers should catch this and fall back to
                the REST API path (``--source=api``) per the plan.
        """
        dump_file = self._dump_path_for_month(year, month)
        logger.info("Loading pageviews dump for %04d-%02d from %s", year, month, dump_file)

        yyyy_mm = f"{year:04d}-{month:02d}"

        lines = self._iter_dump_lines(dump_file)

        totals_by_wiki = self._process_dump_lines(
            lines=lines,
            wanted_wiki_codes=wanted_wiki_codes,
            yyyy_mm=yyyy_mm,
            wanted_titles_by_wiki=wanted_titles_by_wiki,
        )

        return totals_by_wiki


__all__ = [
    "DumpNotFoundError",
    "PageviewsDumpLoader",
]
