"""
Standalone entry point: load a monthly Wikimedia ``pageview_complete`` dump
directly into the existing per-wiki/month SQLite pageviews cache.

This is intentionally decoupled from ``check_reports.py`` / ``PageviewsRepository``:
it only *populates* ``data/views/<wiki>/<YYYY-MM>.sqlite3`` from the bulk dump.
The existing report-generation code already checks that cache before falling
back to the REST API, so simply running this beforehand (e.g. as a Toolforge
job, a few days into the following month once the dump has landed) means the
REST path ends up doing little to no work for that wiki/month -- with zero
changes required to the existing REST-based code.

Usage examples
--------------
    # Load July 2026 for every wiki configured in config/wikis.yaml:
    python -m src.load_pageviews_dump --year 2026 --month 7

    # Same, but only for specific wikis:
    python -m src.load_pageviews_dump --year 2026 --month 7 \\
        --wiki en.wikipedia --wiki ar.wikipedia

    # Point at a different wikis.yaml / dumps root / views dir (e.g. for
    # local testing away from the real Toolforge NFS mount):
    python -m src.load_pageviews_dump --year 2026 --month 7 \\
        --dumps-root /path/to/fake/dumps \\
        --views-dir /path/to/data/views

Exit codes
----------
    0  Success (dump found and processed; individual malformed lines within
       the dump are logged and skipped, not treated as failure).
    2  The dump file for the requested year/month does not exist yet (e.g.
       it hasn't been published). Callers/cron can treat this distinctly
       from a real error -- it just means "try again later" or "fall back
       to the REST path for this month", not "something is broken".
    1  Any other unexpected error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from popularpages.config import app_config
from popularpages.dumps_parser.pageviews_dump_loader import (
    DUMPS_ROOT,
    DumpNotFoundError,
    load_dump_into_cache,
)

logger = logging.getLogger(__name__)

# Repo-relative defaults. Overridable via CLI flags for tests / non-standard
# layouts; on Toolforge these should resolve correctly as-is when run from
# the repo root.
DEFAULT_WIKIS_YAML = app_config.paths.wikis_config_file
DEFAULT_VIEWS_DIR = app_config.data_paths.views_data_dir

EXIT_OK = 0
EXIT_DUMP_NOT_FOUND = 2
EXIT_ERROR = 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load a monthly pageview_complete dump into the SQLite pageviews cache.",
    )

    parser.add_argument("debug", help="Enable debug logs")

    parser.add_argument("--year", type=int, required=True, help="Dump year, e.g. 2026.")
    parser.add_argument(
        "--month",
        type=int,
        required=True,
        choices=range(1, 13),
        metavar="1-12",
        help="Dump month.",
    )
    parser.add_argument(
        "--wiki",
        action="append",
        dest="wikis",
        default=None,
        metavar="WIKI_CODE",
        help=(
            "Limit processing to this wiki code (e.g. en.wikipedia). "
            "May be given multiple times. Defaults to every wiki in wikis.yaml."
        ),
    )
    parser.add_argument(
        "--dumps-root",
        type=Path,
        default=DUMPS_ROOT,
        help=f"Root directory of the pageview_complete monthly dumps (default: {DUMPS_ROOT}).",
    )
    parser.add_argument(
        "--views-dir",
        type=Path,
        default=DEFAULT_VIEWS_DIR,
        help=f"Root data/views directory to write SQLite caches into (default: {DEFAULT_VIEWS_DIR}).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG-level logging (default: INFO).",
    )
    return parser.parse_args(argv)


def _resolve_wanted_wiki_codes(args: argparse.Namespace) -> set[str]:
    """
    Resolve the final set of wiki codes to process: either the explicit
    ``--wiki`` list (validated against wikis.yaml so a typo doesn't
    silently process nothing), or every wiki configured in wikis.yaml.
    """
    configured_wikis = app_config.paths.load_wikis_config()
    if not configured_wikis:
        raise SystemExit("No wikis found in wikis.yaml -- nothing to do.")

    configured_wikis_keys = set(configured_wikis.keys())

    if not args.wikis:
        return configured_wikis_keys

    requested = set(args.wikis)
    unknown = requested - configured_wikis_keys
    if unknown:
        raise SystemExit(
            "Unknown --wiki value(s) not present in wikis.yaml: "
            f"{sorted(unknown)}. Configured wikis: {sorted(configured_wikis)}"
        )
    return requested


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        wanted_wiki_codes = _resolve_wanted_wiki_codes(args)
    except SystemExit as exc:
        logger.error(str(exc))
        return EXIT_ERROR

    logger.info(
        "Loading %04d-%02d pageviews dump for %d wiki(s): %s",
        args.year,
        args.month,
        len(wanted_wiki_codes),
        ", ".join(sorted(wanted_wiki_codes)),
    )

    try:
        result = load_dump_into_cache(
            year=args.year,
            month=args.month,
            wanted_wiki_codes=wanted_wiki_codes,
            views_dir=args.views_dir,
            dumps_root=args.dumps_root,
        )
    except DumpNotFoundError as exc:
        # Not a bug -- this month's dump likely hasn't been published yet.
        # A distinct exit code lets a cron/job wrapper decide to retry later
        # or fall back to the REST API path for this run, without treating
        # it as a crash.
        logger.warning("%s. Falling back to REST API path is recommended for this run.", exc)
        return EXIT_DUMP_NOT_FOUND
    except Exception:
        logger.exception("Unexpected error while loading pageviews dump for %04d-%02d", args.year, args.month)
        return EXIT_ERROR

    if not result:
        logger.warning(
            "Dump processed successfully but no matching lines were found for any of: %s",
            sorted(wanted_wiki_codes),
        )
    else:
        for wiki_code in sorted(wanted_wiki_codes):
            count = result.get(wiki_code, 0)
            if count:
                logger.info("[%s] %s titles cached.", wiki_code, f"{count:,}")
            else:
                logger.warning("[%s] No pageview data found in this month's dump.", wiki_code)

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
