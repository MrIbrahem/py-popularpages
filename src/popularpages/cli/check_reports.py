"""
Entry point for a new bot run across all configured wikis.

Ported from bin/checkReports.php: for every wiki listed in config/wikis.yaml,
fetches config for WikiProjects not already updated this month, and passes
it to ReportUpdater.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import asyncio

import yaml

from popularpages.logger import log_to_file
from popularpages.report_updater import ReportUpdater
from popularpages.wiki_repository import BASE_DIR


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate popular pages reports for stale WikiProjects.")
    parser.add_argument(
        "wiki",
        nargs="?",
        help="Wiki in the format lang.project (e.g. en.wikipedia). Omit to run all.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout instead of editing")
    args = parser.parse_args()

    wikis = yaml.safe_load((BASE_DIR / "config" / "wikis.yaml").read_text(encoding="utf-8"))

    if args.wiki:
        if args.wiki not in wikis:
            print(f"Unknown wiki '{args.wiki}'. Available: {', '.join(wikis)}")
            return
        selected = [args.wiki]
    else:
        selected = list(wikis)

    for wiki in selected:
        updater = ReportUpdater(wiki, dry_run=args.dry_run)
        log_to_file("Beginning new cycle", wiki)
        stale_config = updater.wiki_repository.get_stale_projects()
        log_to_file(f"Number of projects pending update: {len(stale_config)}", wiki)
        asyncio.run(updater.update_reports(stale_config))


if __name__ == "__main__":
    main()
