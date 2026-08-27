"""Manually regenerate a report for a single WikiProject.

Ported from bin/generateReport.php.
"""

from __future__ import annotations

import argparse
import asyncio

from popularpages.logger import log_to_file
from popularpages.report_updater import ReportUpdater


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manually regenerate the popular pages report for a single WikiProject."
    )
    parser.add_argument("wiki", help="Wiki in the format lang.project (e.g. en.wikipedia).")
    parser.add_argument("project", help="WikiProject name as recorded in the JSON config.")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout instead of editing")
    args = parser.parse_args()

    if not __import__("re").match(r"^\w+\.\w+$", args.wiki):
        print("Please specify wiki in the format lang.project (such as en.wikipedia)")
        return

    updater = ReportUpdater(args.wiki, dry_run=args.dry_run)
    log_to_file(
        f"Running script to generate report for project {args.project} on {args.wiki}",
        args.wiki,
    )

    project_config = updater.wiki_repository.get_project(args.project)

    if project_config is None:
        print("Project configuration not found.")
    else:
        asyncio.run(updater.update_reports(project_config))


if __name__ == "__main__":
    main()
