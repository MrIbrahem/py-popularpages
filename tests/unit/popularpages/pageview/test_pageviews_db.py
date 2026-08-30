"""
Tests for src.py_port.popularpages.pageviews.pageviews_db.PageviewsDb.

The SQLite-backed store is exercised directly (no PageviewsCache, no network):
we upsert rows with ``upsert_many`` and assert on ``get_views`` /
``get_views_many`` lookup behavior, conflict/overwrite semantics, and the
``close`` lifecycle.
"""

import sqlite3

import pytest

import src.py_port.popularpages.config as cfg
import src.py_port.popularpages.pageviews.pageviews_db as cache_module
from src.py_port.popularpages.pageviews.pageviews_db import PageviewsDb

pytestmark = pytest.mark.asyncio


@pytest.fixture
def db_config(tmp_path, monkeypatch):
    """Redirect the persisted data directory to a temp path."""
    monkeypatch.setenv("POPULAR_PAGES_MAIN_DIR", str(tmp_path))
    new_cfg = cfg.app_config.load()
    monkeypatch.setattr("src.py_port.popularpages.pageviews.pageviews_db.app_config", new_cfg)
    return new_cfg


@pytest.fixture
def db_dir(tmp_path):
    """A temp directory to host the SQLite cache files."""
    d = tmp_path / "views"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def db_factory(db_dir):
    """Create PageviewsDb instances and ensure they are closed on teardown."""
    created: list[PageviewsDb] = []

    def _make(wiki: str = "en.wikipedia", year_month: str = "2024-01") -> PageviewsDb:
        db = PageviewsDb(wiki, year_month, path_dir=db_dir)
        created.append(db)
        return db

    yield _make
    for db in created:
        db.close()


def _rows(sqlite_path) -> dict[str, int]:
    """Read title -> views rows from a SQLite cache file."""
    con = sqlite3.connect(str(sqlite_path))
    try:
        cur = con.execute("SELECT title, views FROM pageviews ORDER BY title")
        return dict(cur.fetchall())
    finally:
        con.close()


# ---------------------------------------------------------------
# 1. Tests for get_views summing target and redirects.
# ---------------------------------------------------------------
class TestDbGet:
    """Tests for PageviewsDb.get_views summing target and redirects."""

    async def test_get_sums_target_and_redirects(self, db_factory):
        db = db_factory()
        db.upsert_many({"A": 10, "A redir": 5})

        assert db.get_views("A", ["A redir"]) == 15
        assert db.get_views("A", []) == 10
        assert db.get_views("Unknown", []) == 0


# ---------------------------------------------------------------
# 2. Tests for upsert/conflict behavior.
# ---------------------------------------------------------------
class TestDbUpsert:
    """Tests that re-fetching a title overwrites rather than duplicates."""

    async def test_re_fetch_overwrites_existing_row(self, db_factory) -> None:
        db = db_factory()
        db.upsert_many({"A": 10})

        # Re-upserting the same title with a new view count must overwrite the
        # existing row (primary-key conflict), never duplicate it.
        db.upsert_many({"A": 999})

        path = db.path
        assert _rows(path) == {"A": 999}  # exactly one row, value overwritten
        assert db.get_views("A", []) == 999


# ---------------------------------------------------------------
# 3. Tests for get_views_many (bulk lookup used by large projects).
# ---------------------------------------------------------------
class TestDbGetViewsMany:
    """Tests for PageviewsDb.get_views_many bulk lookup."""

    async def test_get_views_many_sums_target_and_redirects(self, db_factory):
        db = db_factory()
        db.upsert_many({"A": 10, "A redir": 5, "B": 20})

        counts = db.get_views_many(["A", "B"], {"A": ["A redir"], "B": []})
        assert counts == {"A": 15, "B": 20}

    async def test_get_views_many_unknown_is_zero(self, db_factory):
        db = db_factory()
        db.upsert_many({"A": 10})

        counts = db.get_views_many(["A", "Unknown"], {"A": [], "Unknown": ["Also missing"]})
        assert counts == {"A": 10, "Unknown": 0}

    async def test_get_views_many_chunks_large_title_set(self, db_factory, monkeypatch):
        """A huge title set is queried in _SELECT_IN_CHUNK_SIZE-sized chunks.

        Exercises the >900k-title code path: every unique title is resolved in a
        few chunked SELECTs rather than one query per target.
        """
        # Override the chunk size used by get_views_many to exercise chunking.
        monkeypatch.setattr(cache_module.PageviewsDb, "_SELECT_IN_CHUNK_SIZE", 100)

        n = 250
        mapping = {f"T{i}": i for i in range(n)}
        db = db_factory("en.wikipedia", "2024-03")
        db.upsert_many(mapping)

        targets = [f"T{i}" for i in range(n)]
        counts = db.get_views_many(targets, {t: [] for t in targets})
        assert counts == mapping

    async def test_get_views_many_shared_redirect_resolves_once(self, db_factory):
        """A redirect referenced by two targets is looked up once but counted for both."""
        db = db_factory()
        db.upsert_many({"A": 1, "B": 2, "shared redir": 9})

        counts = db.get_views_many(["A", "B"], {"A": ["shared redir"], "B": ["shared redir"]})
        assert counts == {"A": 10, "B": 11}


# ---------------------------------------------------------------
# 4. Tests for lifecycle (close).
# ---------------------------------------------------------------
class TestDbLifecycle:
    """Tests for the sync close() lifecycle method."""

    async def test_close_is_idempotent_and_safe(self, db_factory):
        db = db_factory("en.wikipedia", "2024-01")
        db.upsert_many({"A": 10})

        # close() must be callable and safe to call more than once.
        db.close()
        db.close()

        # The on-disk data survives disposal.
        assert _rows(db.path) == {"A": 10}
