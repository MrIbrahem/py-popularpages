"""
Entry point for a new bot run across all configured wikis.

Ported from bin/checkReports.php: for every wiki listed in config/wikis.yaml,
fetches config for WikiProjects not already updated this month, and passes
it to ReportUpdater.

Example:
    - python3 src/src_py/cli/check_reports.py --wiki en.wikipedia
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from src.src_py.popularpages.config import config, load_wikis_config
from src.src_py.popularpages.logger import log_to_file
from src.src_py.popularpages.report_updater import ReportUpdater

logger = logging.getLogger(__name__)


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
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(name)s: %(message)s",
    )

    wikis_config = load_wikis_config(config.paths)

    if args.wiki:
        if args.wiki not in wikis_config:
            logger.info(f"Unknown wiki '{args.wiki}'. Available: {', '.join(wikis_config)}")
            return

        wikis = [args.wiki]
    else:
        wikis = list(wikis_config.keys())

    for wiki in wikis:
        # One wiki failing must not abort the whole run (matches the PHP
        # behavior where each wiki is invoked by its own cron job).
        logger.info("Starting cycle for wiki '%s'", wiki)
        try:
            updater = ReportUpdater(wiki, dry_run=args.dry_run)
            log_to_file("Beginning new cycle", wiki)

            stale_configs = updater.wiki_repository.get_stale_projects()
            logger.info("Wiki '%s': %d project(s) pending update", wiki, len(stale_configs))
            log_to_file(f"Number of projects pending update: {len(stale_configs)}", wiki)

            asyncio.run(updater.update_reports(stale_configs))
            logger.info("Finished cycle for wiki '%s'", wiki)
        except Exception as exc:
            logger.exception("Error processing %s: %s", wiki, exc)
            log_to_file(f"Error processing {wiki}: {exc}", wiki)


if __name__ == "__main__":
    main()
