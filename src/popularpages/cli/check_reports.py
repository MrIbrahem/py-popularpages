"""
Entry point for a new bot run across all configured wikis.

Ported from bin/checkReports.php: for every wiki listed in config/wikis.yaml,
fetches config for WikiProjects not already updated this month, and passes
it to ReportUpdater.
"""

from __future__ import annotations

import argparse

import yaml

from popularpages.logger import log_to_file
from popularpages.report_updater import ReportUpdater
from popularpages.wiki_repository import BASE_DIR


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

    wikis_config = yaml.safe_load((BASE_DIR / "config" / "wikis.yaml").read_text(encoding="utf-8"))

    if args.wiki:
        if args.wiki not in wikis_config:
            print(f"Unknown wiki '{args.wiki}'. Available: {', '.join(wikis_config)}")
            return
        wikis = [args.wiki]
    else:
        wikis = list(wikis_config.keys())

    for wiki in wikis:
        updater = ReportUpdater(wiki, dry_run=args.dry_run)
        log_to_file("Beginning new cycle", wiki)
        stale_config = updater.wiki_repository.get_stale_projects()
        log_to_file(f"Number of projects pending update: {len(stale_config)}", wiki)
        updater.update_reports(stale_config)


if __name__ == "__main__":
    main()
