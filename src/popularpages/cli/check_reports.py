"""Entry point for a new bot run across all configured wikis.

Ported from bin/checkReports.php: for every wiki listed in config/wikis.yaml,
fetches config for WikiProjects not already updated this month, and passes
it to ReportUpdater.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from popularpages.report_updater import ReportUpdater

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Check for and update stale popular pages reports across all wikis.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print output instead of saving edits to the wiki.",
    )
    parser.add_argument(
        "--wiki",
        help="Only process this wiki (e.g. en.wikipedia), instead of all configured wikis.",
    )
    args = parser.parse_args()

    wikis_config = yaml.safe_load((BASE_DIR / "config" / "wikis.yaml").read_text(encoding="utf-8"))
    wikis = [args.wiki] if args.wiki else list(wikis_config.keys())

    for wiki in wikis:
        updater = ReportUpdater(wiki, dry_run=args.dry_run)
        stale_config = updater.wiki_repository.get_stale_projects()
        updater.update_reports(stale_config)


if __name__ == "__main__":
    main()
