"""
Manually regenerate a report for a single WikiProject.

Ported from bin/generateReport.php.

Example:
    - python3 src/py_port/generate_report.py --wiki en.wikipedia --project Dinosaurs
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys

from popularpages.report_updater import ReportUpdater

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manually regenerate a popular pages report for a single WikiProject.",
    )
    parser.add_argument(
        "--wiki",
        default="en.wikipedia",
        help="Target wiki, e.g. en.wikipedia.",
    )
    parser.add_argument(
        "--project",
        help="WikiProject 'Name' as given in the on-wiki JSON config.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print output instead of saving edits to the wiki.",
    )
    args = parser.parse_args()

    if not re.match(r"^\w+\.\w+$", args.wiki):
        logger.info("Please specify wiki in the format lang.project (such as en.wikipedia)")
        return

    logger.info(
        "Generating report for project '%s' on wiki '%s' (dry_run=%s)",
        args.project,
        args.wiki,
        args.dry_run,
    )
    updater = ReportUpdater(args.wiki, dry_run=args.dry_run)
    project_config = updater.wiki_repository.get_project(args.project)

    if not project_config:
        logger.info(f"No WikiProject found with Name '{args.project}' on {args.wiki}.")
        sys.exit(1)

    asyncio.run(
        updater.update_reports(
            [project_config],
        ),
    )
    logger.info("Finished generating report for project '%s'", args.project)


if __name__ == "__main__":
    main()
