"""Generate/update only the index page for a wiki.

Ported from bin/generateIndex.php.
"""

from __future__ import annotations

import argparse

from popularpages.report_updater import ReportUpdater


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate the popular pages index page.")
    parser.add_argument("--wiki", default="en.wikipedia", help="Target wiki, e.g. en.wikipedia.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print output instead of saving edits to the wiki.",
    )
    args = parser.parse_args()

    updater = ReportUpdater(args.wiki, dry_run=args.dry_run)
    updater.update_index()


if __name__ == "__main__":
    main()
