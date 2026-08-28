"""
Entry point for a new bot run across all configured wikis.

Ported from bin/checkReports.php: for every wiki listed in config/wikis.yaml,
fetches config for WikiProjects not already updated this month, and passes
it to ReportUpdater.

Example:
    - python popularpages/check_reports.py --wiki en.wikipedia
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


from popularpages.config import load_wikis_config
from popularpages.logger import log_to_file
from popularpages.report_updater import ReportUpdater


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

    wikis_config = load_wikis_config()

    if args.wiki:
        if args.wiki not in wikis_config:
            print(f"Unknown wiki '{args.wiki}'. Available: {', '.join(wikis_config)}")
            return

        wikis = [args.wiki]
    else:
        wikis = list(wikis_config.keys())

    for wiki in wikis:
        # One wiki failing must not abort the whole run (matches the PHP
        # behavior where each wiki is invoked by its own cron job).
        try:
            updater = ReportUpdater(wiki, dry_run=args.dry_run)
            log_to_file("Beginning new cycle", wiki)

            stale_configs = updater.wiki_repository.get_stale_projects()
            log_to_file(f"Number of projects pending update: {len(stale_configs)}", wiki)

            asyncio.run(updater.update_reports(stale_configs))
        except Exception as exc:
            log_to_file(f"Error processing {wiki}: {exc}", wiki)


if __name__ == "__main__":
    main()
