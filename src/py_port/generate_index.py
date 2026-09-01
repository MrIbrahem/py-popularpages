"""
Generate/update only the index page for a wiki.

Ported from bin/generateIndex.php.
Example:
    - python3 src/py_port/generate_index.py --wiki en.wikipedia
"""

from __future__ import annotations

import argparse
import logging
import re

# from popularpages.report_updater import ReportUpdater
from popularpages.report_updater import IndexUpdater

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate the popular pages index page.",
    )
    parser.add_argument(
        "--wiki",
        default="en.wikipedia",
        help="Target wiki, e.g. en.wikipedia.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print output instead of saving edits to the wiki.",
    )
    parser.add_argument(
        "--debug",
        "-v",
        action="store_true",
        help="Enable DEBUG-level logging (default: INFO).",
    )
    args = parser.parse_args()

    if not re.match(r"^\w+\.\w+$", args.wiki):
        logger.info("Please specify wiki in the format lang.project (such as en.wikipedia)")
        return

    logger.info("Generating index page for wiki '%s' (dry_run=%s)", args.wiki, args.dry_run)
    updater = IndexUpdater(args.wiki, dry_run=args.dry_run)
    updater.update_index()


if __name__ == "__main__":
    main()
