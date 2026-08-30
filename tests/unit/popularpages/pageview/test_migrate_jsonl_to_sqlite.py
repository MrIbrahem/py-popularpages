"""
Tests for the one-off JSONL -> SQLite migration script.

These exercise :func:`migrate` (and the ``--dry-run`` / ``--delete-jsonl``
CLI flags via :func:`main`) against synthetic legacy ``.jsonl`` caches,
asserting that valid rows survive and malformed lines are skipped, matching
the old ``PageviewsCache._load()`` tolerance.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path


from src.py_port.popularpages.pageviews.migrate_jsonl_to_sqlite import main, migrate


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def _rows(sqlite_path: Path) -> dict[str, int]:
    con = sqlite3.connect(str(sqlite_path))
    try:
        cur = con.execute("SELECT title, views FROM pageviews ORDER BY title")
        return dict(cur.fetchall())
    finally:
        con.close()


class TestMigrateJsonlToSqlite:
    """Round-trip migration behavior."""

    def test_round_trip_skips_malformed_lines(self, tmp_path):
        data_dir = tmp_path / "views"
        jsonl = data_dir / "en.wikipedia" / "2024-01.jsonl"
        _write_jsonl(
            jsonl,
            [
                '{"title": "A", "views": 10}',
                '{"title": "B", "views": 20}',
                "this is not json",  # invalid JSON -> skipped
                '{"title": "C"}',  # missing views -> skipped
                '{"views": 30}',  # missing title -> skipped
                '{"title": "A", "views": 99}',  # duplicate title -> last value wins
            ],
        )

        migrate(data_dir, delete_jsonl=False, dry_run=False)

        sqlite_path = jsonl.with_suffix(".sqlite3")
        assert sqlite_path.exists()
        # "A" appears twice; the dict loader keeps the last value (99).
        assert _rows(sqlite_path) == {"A": 99, "B": 20}
        # Original .jsonl preserved (delete_jsonl default is False).
        assert jsonl.exists()

    def test_dry_run_does_not_write_or_delete(self, tmp_path):
        data_dir = tmp_path / "views"
        jsonl = data_dir / "en.wikipedia" / "2024-01.jsonl"
        _write_jsonl(jsonl, ['{"title": "A", "views": 1}'])

        migrate(data_dir, delete_jsonl=False, dry_run=True)

        assert not jsonl.with_suffix(".sqlite3").exists()
        assert jsonl.exists()

    def test_delete_jsonl_removes_original(self, tmp_path):
        data_dir = tmp_path / "views"
        jsonl = data_dir / "en.wikipedia" / "2024-01.jsonl"
        _write_jsonl(jsonl, ['{"title": "A", "views": 1}'])

        migrate(data_dir, delete_jsonl=True, dry_run=False)

        assert jsonl.with_suffix(".sqlite3").exists()
        assert not jsonl.exists()

    def test_no_jsonl_files_is_a_no_op(self, tmp_path):
        migrate(tmp_path / "empty", delete_jsonl=False, dry_run=False)
        assert not list((tmp_path / "empty").glob("**/*.sqlite3"))

    def test_cli_dry_run_does_not_write(self, tmp_path, monkeypatch, caplog):
        data_dir = tmp_path / "views"
        jsonl = data_dir / "en.wikipedia" / "2024-01.jsonl"
        _write_jsonl(jsonl, ['{"title": "A", "views": 1}'])

        monkeypatch.setattr(
            "sys.argv",
            [
                "migrate_jsonl_to_sqlite",
                "--data-dir",
                str(data_dir),
                "--dry-run",
            ],
        )
        with caplog.at_level(logging.INFO):
            main()

        assert not jsonl.with_suffix(".sqlite3").exists()
        assert jsonl.exists()
        assert any("dry-run" in rec.message.lower() for rec in caplog.records)
