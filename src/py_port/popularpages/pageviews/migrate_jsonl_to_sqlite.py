"""
One-off migration: convert existing ``data/views/<wiki>/<YYYY-MM>.jsonl``
pageviews caches to the new SQLite format (``<YYYY-MM>.sqlite3``).

Usage:
    python3 -m py_port.popularpages.pageviews.migrate_jsonl_to_sqlite [--data-dir PATH] [--delete-jsonl] [--dry-run]

For every ``*.jsonl`` file found under the views data directory, this creates
(or updates) a sibling ``.sqlite3`` file with the same title/views rows, using
the same :class:`PageView` model and upsert logic as the runtime cache -- so
the migrated files are byte-for-byte compatible with what a fresh run would
produce.

Malformed lines are skipped and counted, matching the tolerant behavior of
the old ``PageviewsCache._load()``. The script is idempotent: re-running it
just re-upserts the same rows.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import jsonlines
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import sessionmaker

from ..config import config
from .pageviews_models import Base, PageView

logger = logging.getLogger(__name__)

UPSERT_BATCH_SIZE = 5000


def _load_jsonl(path: Path) -> dict[str, int]:
    """Read a legacy JSONL cache file into a title -> views dict, skipping bad lines."""
    loaded: dict[str, int] = {}
    skipped = 0
    with jsonlines.open(path, mode="r") as reader:
        for obj in reader.iter(type=dict, skip_invalid=True):
            try:
                loaded[obj["title"]] = int(obj["views"])
            except (KeyError, TypeError, ValueError):
                skipped += 1
    if skipped:
        logger.warning("Skipped %d malformed line(s) in %s", skipped, path)
    return loaded


def _write_sqlite(sqlite_path: Path, title_views: dict[str, int], dry_run: bool) -> None:
    """Create/update a SQLite cache file with the given title -> views rows."""
    if dry_run:
        logger.info("[dry-run] Would write %d row(s) to %s", len(title_views), sqlite_path)
        return

    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{sqlite_path}", future=True)
    try:
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, future=True)

        items = list(title_views.items())
        with Session() as session:
            for i in range(0, len(items), UPSERT_BATCH_SIZE):
                batch = items[i : i + UPSERT_BATCH_SIZE]
                stmt = sqlite_insert(PageView).values([{"title": title, "views": views} for title, views in batch])
                stmt = stmt.on_conflict_do_update(
                    index_elements=[PageView.title],
                    set_={"views": stmt.excluded.views},
                )
                session.execute(stmt)
            session.commit()
    finally:
        engine.dispose()

    logger.info("Wrote %d row(s) to %s", len(title_views), sqlite_path)


def migrate(data_dir: Path, delete_jsonl: bool, dry_run: bool) -> None:
    jsonl_files = sorted(data_dir.glob("*/*.jsonl"))
    if not jsonl_files:
        logger.info("No .jsonl files found under %s", data_dir)
        return

    logger.info("Found %d .jsonl file(s) under %s", len(jsonl_files), data_dir)

    for jsonl_path in jsonl_files:
        sqlite_path = jsonl_path.with_suffix(".sqlite3")
        logger.info("Migrating %s -> %s", jsonl_path, sqlite_path)

        title_views = _load_jsonl(jsonl_path)
        if not title_views:
            logger.warning("No valid rows found in %s, skipping", jsonl_path)
            continue

        _write_sqlite(sqlite_path, title_views, dry_run)

        if delete_jsonl and not dry_run:
            jsonl_path.unlink()
            logger.info("Deleted %s", jsonl_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate pageviews JSONL caches to SQLite.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Views data directory (default: config.data_paths.views_data_dir)",
    )
    parser.add_argument(
        "--delete-jsonl",
        action="store_true",
        help="Delete the original .jsonl file after a successful migration.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without writing or deleting anything.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    data_dir = args.data_dir or config.data_paths.views_data_dir
    migrate(data_dir, delete_jsonl=args.delete_jsonl, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
