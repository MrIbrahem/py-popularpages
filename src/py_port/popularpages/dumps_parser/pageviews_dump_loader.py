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
       *not* ``page_id`` -- see :mod:`bz2_dump_parser` for why).
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

from ..pageviews.pageviews_db import PageviewsDb
from .bz2_dump_parser import MalformedLineError, ParsedPageview

logger = logging.getLogger(__name__)

# Toolforge NFS mount root for the pageview_complete dumps. Confirmed present
# at this path per the plan; not yet verified against the live mount for the
# exact monthly filename pattern (open question in the plan).
DUMPS_ROOT = Path("/public/dumps/public/other/pageview_complete/monthly")

# How often to log progress while streaming a multi-GB file, in lines.
_PROGRESS_LOG_EVERY = 5_000_000

# How many upserted rows to batch per PageviewsDb.upsert_many() call, per wiki.
# Keeps memory bounded for very large wikis and gives visible incremental
# progress, while staying well above the SQLite chunk size used internally
# by PageviewsDb for reads (that limit doesn't apply to writes, but batching
# writes still avoids holding one enormous dict-of-dicts across the whole run).
_UPSERT_BATCH_SIZE = 200_000


class DumpNotFoundError(FileNotFoundError):
    """Raised when the expected monthly dump file doesn't exist on disk."""


def dump_path_for_month(year: int, month: int, root: Path = DUMPS_ROOT) -> Path:
    """
    Build the on-disk path to a monthly ``pageview_complete`` dump.

    :param year: e.g. 2026.
    :param month: 1-12.
    :param root: Override for the dumps root (mainly for tests).
    :return: e.g.
        ``/public/dumps/public/other/pageview_complete/monthly/2026/2026-07/pageviews-202607-user.bz2``
    """
    yyyymm = f"{year:04d}{month:02d}"
    return root / f"{year:04d}" / f"{year:04d}-{month:02d}" / f"pageviews-{yyyymm}-user.bz2"


def iter_dump_lines(dump_file: Path) -> Iterator[str]:
    """
    Stream lines from a bz2-compressed dump file, one at a time.

    Never decompresses the whole file to disk or into memory at once --
    ``bz2.open`` in text mode streams and decompresses incrementally as the
    file object is iterated.

    :raises DumpNotFoundError: if ``dump_file`` doesn't exist.
    """
    if not dump_file.exists():
        raise DumpNotFoundError(f"Pageviews dump not found: {dump_file}")

    with bz2.open(dump_file, "rt", encoding="utf-8", errors="replace") as f:
        yield from f


def aggregate_dump(
    lines: Iterable[str],
    wanted_wiki_codes: set[str],
    wanted_titles_by_wiki: dict[str, set[str]] | None = None,
) -> dict[str, dict[str, int]]:
    """
    Single pass over dump lines, aggregating ``daily_total`` per (wiki, title).

    :param lines: An iterable of raw dump lines (e.g. from :func:`iter_dump_lines`).
    :param wanted_wiki_codes: Only lines whose ``wiki_code`` is in this set are
        kept; everything else is skipped immediately (before any title
        unescaping/aggregation work).
    :param wanted_titles_by_wiki: Optional per-wiki set of titles to keep.
        When a wiki code has an entry here, only those titles are aggregated
        for that wiki (memory optimization for wikis where the full set of
        titles we'll ever need is known ahead of time from WikiProject
        configs, mirroring the old REST-API approach's per-title fetching).
        A wiki code with *no* entry in this dict (or when the whole parameter
        is ``None``) has all its titles aggregated, unfiltered.
    :return: ``{wiki_code: {title: total_views}}`` for every wiki in
        ``wanted_wiki_codes`` that had at least one matching line.

    Malformed lines are logged and skipped rather than aborting the whole
    (multi-hour, multi-GB) run over a handful of bad rows.
    """
    totals: dict[str, dict[str, int]] = {}
    malformed_count = 0
    line_count = 0

    for line in lines:
        line_count += 1
        if line_count % _PROGRESS_LOG_EVERY == 0:
            logger.info("Processed %s dump lines so far...", f"{line_count:,}")

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

        if wanted_titles_by_wiki is not None:
            wanted_titles = wanted_titles_by_wiki.get(parsed.wiki_code)
            if wanted_titles is not None and parsed.title not in wanted_titles:
                continue

        wiki_totals = totals.setdefault(parsed.wiki_code, {})
        wiki_totals[parsed.title] = wiki_totals.get(parsed.title, 0) + parsed.daily_total

    if malformed_count:
        logger.warning("Skipped %d malformed line(s) while processing dump.", malformed_count)
    logger.info("Finished processing %s total dump lines.", f"{line_count:,}")

    return totals


def write_totals_to_cache(
    totals_by_wiki: dict[str, dict[str, int]],
    views_dir: Path,
    year: int,
    month: int,
    batch_size: int = _UPSERT_BATCH_SIZE,
) -> None:
    """
    Write aggregated per-wiki totals into the existing per-wiki/month SQLite
    cache, via :class:`PageviewsDb`, using batched upserts.

    :param totals_by_wiki: ``{wiki_code: {title: total_views}}``, as returned
        by :func:`aggregate_dump`.
    :param views_dir: Root ``data/views`` directory. Each wiki's cache file is
        written to ``<views_dir>/<wiki_code>/<YYYY-MM>.sqlite3``.
    :param year: Dump year, used to build the ``YYYY-MM`` cache filename.
    :param month: Dump month, used to build the ``YYYY-MM`` cache filename.
    :param batch_size: Max rows per ``upsert_many`` call (bounds peak memory
        and gives visible incremental progress for very large wikis).
    """
    yyyy_mm = f"{year:04d}-{month:02d}"

    for wiki_code, title_views in totals_by_wiki.items():
        if not title_views:
            continue

        wiki_dir = views_dir / wiki_code
        wiki_dir.mkdir(parents=True, exist_ok=True)
        db_file_path = wiki_dir / f"{yyyy_mm}.sqlite3"

        db = PageviewsDb(db_file_path)
        try:
            items = list(title_views.items())
            total = len(items)
            written = 0
            for start in range(0, total, batch_size):
                batch = dict(items[start : start + batch_size])
                db.upsert_many(batch)
                written += len(batch)
                logger.debug("[%s] Upserted %d/%d titles into %s", wiki_code, written, total, db_file_path)
            logger.info("[%s] Wrote %d titles to %s", wiki_code, total, db_file_path)
        finally:
            db.close_db()


def load_dump_into_cache(
    year: int,
    month: int,
    wanted_wiki_codes: set[str],
    views_dir: Path,
    dumps_root: Path = DUMPS_ROOT,
    wanted_titles_by_wiki: dict[str, set[str]] | None = None,
) -> dict[str, int]:
    """
    End-to-end entry point: locate the monthly dump, stream-parse it,
    aggregate totals for the configured wikis, and write them into the
    per-wiki/month SQLite cache.

    :param year: Dump year, e.g. 2026.
    :param month: Dump month, 1-12.
    :param wanted_wiki_codes: Wiki codes to keep, e.g. the keys of
        ``config/wikis.yaml`` (``{"en.wikipedia", "ar.wikipedia", ...}``).
    :param views_dir: Root ``data/views`` directory to write caches into.
    :param dumps_root: Override for the dumps root directory (mainly for tests).
    :param wanted_titles_by_wiki: Optional per-wiki title allow-list; see
        :func:`aggregate_dump`.
    :return: ``{wiki_code: number_of_titles_written}`` for every wiki that had
        at least one matching line in the dump.
    :raises DumpNotFoundError: if the dump for ``year``/``month`` isn't
        present on disk yet -- callers should catch this and fall back to the
        REST API path (``--source=api``) per the plan.
    """
    dump_file = dump_path_for_month(year, month, root=dumps_root)
    logger.info("Loading pageviews dump for %04d-%02d from %s", year, month, dump_file)

    lines = iter_dump_lines(dump_file)
    totals_by_wiki = aggregate_dump(lines, wanted_wiki_codes, wanted_titles_by_wiki)

    write_totals_to_cache(totals_by_wiki, views_dir, year, month)

    return {wiki_code: len(titles) for wiki_code, titles in totals_by_wiki.items()}


__all__ = [
    "DUMPS_ROOT",
    "DumpNotFoundError",
    "aggregate_dump",
    "dump_path_for_month",
    "iter_dump_lines",
    "load_dump_into_cache",
    "write_totals_to_cache",
]
