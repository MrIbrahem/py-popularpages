"""
Entry point for a new bot run across all configured wikis.

Ported from bin/checkReports.php: for every wiki listed in config/wikis.yaml,
fetches config for WikiProjects not already updated this month, and passes
it to ReportUpdater.

Example:
    - python3 src/py_port/check_reports.py --wiki en.wikipedia
    - python3 src/py_port/check_reports.py --wiki en.wikipedia --update-index  # update index page after updating reports
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from popularpages.config import app_config
from popularpages.report_updater import IndexUpdater, ReportUpdater

logger = logging.getLogger(__name__)


async def _process_wiki(wiki: str, *, dry_run: bool, update_index: bool) -> None:
    """Run one cycle (update reports + optional index update) for a single wiki.

    One wiki failing must not abort the whole run (matches the PHP behavior
    where each wiki is invoked by its own cron job), so exceptions are caught
    and logged here rather than propagated to the gather() caller.

    Parameters:
        wiki: The wiki key to process (e.g. "en.wikipedia").
        dry_run: Whether to print output instead of saving edits to the wiki.
        update_index: Whether to update the index page after updating reports.
    """
    logger.info("Starting cycle for wiki '%s'", wiki)
    try:
        updater = ReportUpdater(wiki, dry_run=dry_run)
        logger.info("[%s] Beginning new cycle", wiki)

        stale_configs = updater.wiki_repository.get_stale_projects()
        logger.info("Wiki '%s': %d project(s) pending update", wiki, len(stale_configs))
        logger.info("[%s] Number of projects pending update: %d", wiki, len(stale_configs))

        await updater.update_reports(stale_configs)

        if update_index:
            # Update index page.
            index_updater = IndexUpdater(wiki, dry_run=dry_run)
            index_updater.update_index()

        logger.info("Finished cycle for wiki '%s'", wiki)

    except Exception as exc:
        logger.exception("Error processing %s: %s", wiki, exc)
        logger.error("[%s] Error processing %s: %s", wiki, wiki, exc)


async def _run_all(wikis: list[str], *, dry_run: bool, update_index: bool) -> None:
    """Process every wiki concurrently on a single shared event loop.

    Parameters:
        wikis: The wiki keys to process.
        dry_run: Whether to print output instead of saving edits to the wiki.
        update_index: Whether to update the index page after updating reports.
    """
    await asyncio.gather(*(_process_wiki(wiki, dry_run=dry_run, update_index=update_index) for wiki in wikis))


def main() -> None:
    parser = argparse.ArgumentParser(description="Check for and update stale popular pages reports across all wikis.")
    parser.add_argument(
        "--wiki",
        help="Only process this wiki (e.g. en.wikipedia), instead of all configured wikis.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print output instead of saving edits to the wiki.",
    )
    # add an argument for update_index with default false value
    parser.add_argument(
        "--update-index",
        action="store_true",
        help="Update the index page after updating reports.",
    )
    parser.add_argument(
        "--debug",
        "-v",
        action="store_true",
        help="Enable DEBUG-level logging (default: INFO).",
    )
    args = parser.parse_args()

    wikis_config = app_config.paths.load_wikis_config()

    if args.wiki:
        if args.wiki not in wikis_config:
            logger.info(f"Unknown wiki '{args.wiki}'. Available: {', '.join(wikis_config)}")
            return

        wikis = [args.wiki]
    else:
        wikis = list(wikis_config.keys())

    # A single event loop for the whole run instead of one asyncio.run() per
    # wiki: creating/tearing down a loop per wiki was the main cost in runs
    # over many wikis. Wikis also now update concurrently.
    asyncio.run(
        _run_all(
            wikis,
            dry_run=args.dry_run,
            update_index=args.update_index,
        )
    )


if __name__ == "__main__":
    main()
