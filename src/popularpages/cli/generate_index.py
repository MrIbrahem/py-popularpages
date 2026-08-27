import argparse

from popularpages.logger import log_to_file
from popularpages.report_updater import ReportUpdater


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the WikiProject index page.")
    parser.add_argument("wiki", help="Wiki in the format lang.project (e.g. en.wikipedia).")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout instead of editing")
    args = parser.parse_args()

    if not __import__("re").match(r"^\w+\.\w+$", args.wiki):
        print("Please specify wiki in the format lang.project (such as en.wikipedia)")
        return

    log_to_file("Generating index page", args.wiki)
    updater = ReportUpdater(args.wiki, dry_run=args.dry_run)
    updater.update_index()


if __name__ == "__main__":
    main()
