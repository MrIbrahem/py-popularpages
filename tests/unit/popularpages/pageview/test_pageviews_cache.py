"""
Tests for src.py_port.popularpages.pageviews.pageviews_cache.PageviewsCache.

The PageviewsRepository is replaced with a lightweight async fake so no network
calls are made; we only assert on de-duplication, persistence (SQLite), and
lookup behavior of the new SQLAlchemy-backed cache.
"""

import dataclasses
import sqlite3

import pytest

import src.py_port.popularpages.config as cfg
import src.py_port.popularpages.pageviews.pageviews_cache as cache_module
from src.py_port.popularpages.pageviews.pageviews_cache import PageviewsCache

pytestmark = pytest.mark.asyncio


class FakeRepo:
    """Stand-in for PageviewsRepository.get_title_views."""

    def __init__(self, mapping: dict[str, int]):
        self.mapping = mapping
        self.calls: list[list[str]] = []

    async def get_title_views(self, titles, start, end) -> dict[str, int]:
        self.calls.append(list(titles))
        return {t: self.mapping.get(t, 0) for t in titles}


@pytest.fixture
def cache_config(tmp_path, monkeypatch):
    """Redirect the persisted cache directory to a temp path."""
    monkeypatch.setenv("POPULAR_PAGES_MAIN_DIR", str(tmp_path))
    new_cfg = cfg.app_config.load()

    monkeypatch.setattr("src.py_port.popularpages.pageviews.pageviews_cache.app_config", new_cfg)
    monkeypatch.setattr(cache_module, "app_config", new_cfg)

    monkeypatch.setattr("src.py_port.popularpages.pageviews.pageviews_db.app_config", new_cfg)
    # monkeypatch.setattr(cache_module, "app_config", new_cfg)
    return new_cfg


@pytest.fixture
def cache_factory():
    """Create PageviewsCache instances and ensure they are closed on teardown."""
    created: list[PageviewsCache] = []

    def _make(*args, **kwargs) -> PageviewsCache:
        c = PageviewsCache(*args, **kwargs)
        created.append(c)
        return c

    yield _make
    for c in created:
        c.close()


def _rows(sqlite_path) -> dict[str, int]:
    """Read title -> views rows from a migrated/runtime SQLite cache file."""
    con = sqlite3.connect(str(sqlite_path))
    try:
        cur = con.execute("SELECT title, views FROM pageviews ORDER BY title")
        return dict(cur.fetchall())
    finally:
        con.close()


# ---------------------------------------------------------------
# 1. Tests for PageviewsCache.ensure batch fetch and persistence.
# ---------------------------------------------------------------
class TestCacheEnsureAndFetch:
    """Tests for PageviewsCache.ensure batch fetch and persistence."""

    async def test_ensure_fetches_all_titles_once_and_persists(self, cache_config, cache_factory):
        repo = FakeRepo({"A": 10, "B": 20, "C": 30})
        cache = cache_factory("en.wikipedia", "2024-01", repo)
        await cache.ensure({"A", "B", "C"}, "2024010100", "2024013100")

        # All three titles fetched in a single batch call (order is unspecified).
        assert len(repo.calls) == 1
        assert set(repo.calls[0]) == {"A", "B", "C"}

        path = cache_config.data_paths.views_data_dir / "en.wikipedia" / "2024-01.sqlite3"
        assert path.exists()
        assert _rows(path) == {"A": 10, "B": 20, "C": 30}

    async def test_incremental_only_fetches_missing(self, cache_config, cache_factory):
        """Two ensures persist cumulatively; the 2nd only fetches the new title."""
        repo = FakeRepo({"A": 1, "B": 2, "C": 3})
        cache = cache_factory("en.wikipedia", "2024-06", repo)
        await cache.ensure({"A", "B"}, "2024060100", "2024063000")
        await cache.ensure({"C"}, "2024060100", "2024063000")

        path = cache_config.data_paths.views_data_dir / "en.wikipedia" / "2024-06.sqlite3"
        assert _rows(path) == {"A": 1, "B": 2, "C": 3}
        # A and B were already cached on the second ensure, so only C was fetched.
        assert [sorted(c) for c in repo.calls] == [["A", "B"], ["C"]]

    async def test_fetch_respects_fetch_batch(self, cache_config, cache_factory, monkeypatch):
        """Titles are fetched in batches of config.pageviews.fetch_batch."""

        mapping = {f"T{i}": i for i in range(10)}
        repo = FakeRepo(mapping)
        cache = cache_factory("en.wikipedia", "2024-02", repo, fetch_batch=3)
        await cache.ensure(set(mapping), "2024020100", "2024022900")

        path = cache_config.data_paths.views_data_dir / "en.wikipedia" / "2024-02.sqlite3"
        # 10 titles in batches of 3 -> 4 API calls.
        assert len(repo.calls) == 4
        assert _rows(path) == mapping


# ---------------------------------------------------------------
# 2. Tests for PageviewsCache.db.get_views summing target and redirects.
# ---------------------------------------------------------------
class TestCacheGet:
    """Tests for PageviewsCache.db.get_views summing target and redirects."""

    async def test_get_sums_target_and_redirects(self, cache_config, cache_factory):
        repo = FakeRepo({"A": 10, "A redir": 5})
        cache = cache_factory("en.wikipedia", "2024-01", repo)
        await cache.ensure({"A", "A redir"}, "2024010100", "2024013100")
        assert cache.db.get_views("A", ["A redir"]) == 15
        assert cache.db.get_views("A", []) == 10
        assert cache.db.get_views("Unknown", []) == 0


# ---------------------------------------------------------------
# 3. Tests for loading/reusing a previously persisted cache.
# ---------------------------------------------------------------
class TestCacheLoadReuse:
    """Tests for loading/reusing a previously persisted SQLite cache."""

    async def test_load_reuses_previous_run_and_only_fetches_missing(self, cache_config, cache_factory):
        repo1 = FakeRepo({"A": 10, "B": 20})
        cache1 = cache_factory("en.wikipedia", "2024-01", repo1)
        await cache1.ensure({"A", "B"}, "2024010100", "2024013100")
        assert repo1.calls  # fetched on first run

        # New cache for the same wiki/month should not re-fetch existing titles.
        repo2 = FakeRepo({"A": 999, "B": 999, "C": 999})
        cache2 = cache_factory("en.wikipedia", "2024-01", repo2)
        assert repo2.calls == []  # nothing fetched at construction

        await cache2.ensure({"A", "B", "C"}, "2024010100", "2024013100")
        # Only the previously-missing title "C" is fetched.
        assert repo2.calls == [["C"]]
        # Values come from disk (10/20), not the fake's 999.
        assert cache2.db.get_views("A", []) == 10
        assert cache2.db.get_views("B", []) == 20
        assert cache2.db.get_views("C", []) == 999

    async def test_missing_file_creates_empty_sqlite(self, cache_config, cache_factory):
        """A wiki/month with no cache file gets an empty .sqlite3 created."""
        repo = FakeRepo({"A": 10})
        cache = cache_factory("en.wikipedia", "2099-12", repo)
        # The SQLite file is created at construction (schema initialized).
        path = cache_config.data_paths.views_data_dir / "en.wikipedia" / "2099-12.sqlite3"
        assert path.exists()
        assert cache.db.get_views("A", []) == 0


# ---------------------------------------------------------------
# 4. Tests for upsert/conflict behavior.
# ---------------------------------------------------------------
class TestCacheUpsert:
    """Tests that re-fetching a title overwrites rather than duplicates."""

    async def test_re_fetch_overwrites_existing_row(self, cache_config: cfg.AppConfig, cache_factory) -> None:
        repo = FakeRepo({"A": 10})
        cache = cache_factory("en.wikipedia", "2024-01", repo)
        await cache.ensure({"A"}, "2024010100", "2024013100")

        # Re-upserting the same title with a new view count must overwrite the
        # existing row (primary-key conflict), never duplicate it.
        cache.db._upsert_many({"A": 999})

        path = cache_config.data_paths.views_data_dir / "en.wikipedia" / "2024-01.sqlite3"
        assert _rows(path) == {"A": 999}  # exactly one row, value overwritten
        assert cache.db.get_views("A", []) == 999


# ---------------------------------------------------------------
# 5. Tests for get_views_many (bulk lookup used by large projects).
# ---------------------------------------------------------------
class TestCacheGetViewsMany:
    """Tests for PageviewsCache.db.get_views_many bulk lookup."""

    async def test_get_views_many_sums_target_and_redirects(self, cache_config, cache_factory):
        repo = FakeRepo({"A": 10, "A redir": 5, "B": 20})
        cache = cache_factory("en.wikipedia", "2024-01", repo)
        await cache.ensure({"A", "A redir", "B"}, "2024010100", "2024013100")

        counts = cache.db.get_views_many(["A", "B"], {"A": ["A redir"], "B": []})
        assert counts == {"A": 15, "B": 20}

    async def test_get_views_many_unknown_is_zero(self, cache_config, cache_factory):
        repo = FakeRepo({"A": 10})
        cache = cache_factory("en.wikipedia", "2024-01", repo)
        await cache.ensure({"A"}, "2024010100", "2024013100")

        counts = cache.db.get_views_many(["A", "Unknown"], {"A": [], "Unknown": ["Also missing"]})
        assert counts == {"A": 10, "Unknown": 0}

    async def test_get_views_many_chunks_large_title_set(self, cache_config, cache_factory, monkeypatch):
        """A huge title set is queried in _SELECT_IN_CHUNK_SIZE-sized chunks.

        Exercises the >900k-title code path: every unique title is resolved in a
        few chunked SELECTs rather than one query per target.
        """
        # Override the chunk size used by get_views_many to exercise chunking.
        monkeypatch.setattr(cache_module.PageviewsDb, "_SELECT_IN_CHUNK_SIZE", 100)

        n = 250
        mapping = {f"T{i}": i for i in range(n)}
        repo = FakeRepo(mapping)
        cache = cache_factory("en.wikipedia", "2024-03", repo, fetch_batch=1000)
        await cache.ensure(set(mapping), "2024030100", "2024033100")

        targets = [f"T{i}" for i in range(n)]
        counts = cache.db.get_views_many(targets, {t: [] for t in targets})
        assert counts == mapping

    async def test_get_views_many_shared_redirect_resolves_once(self, cache_config, cache_factory):
        """A redirect referenced by two targets is looked up once but counted for both."""
        repo = FakeRepo({"A": 1, "B": 2, "shared redir": 9})
        cache = cache_factory("en.wikipedia", "2024-01", repo)
        await cache.ensure({"A", "B", "shared redir"}, "2024010100", "2024013100")

        counts = cache.db.get_views_many(["A", "B"], {"A": ["shared redir"], "B": ["shared redir"]})
        assert counts == {"A": 10, "B": 11}


# ---------------------------------------------------------------
# 6. Tests for lifecycle (close).
# ---------------------------------------------------------------
class TestCacheLifecycle:
    """Tests for the sync close() lifecycle method."""

    async def test_close_is_idempotent_and_safe(self, cache_config, cache_factory):
        repo = FakeRepo({"A": 10})
        cache = cache_factory("en.wikipedia", "2024-01", repo)
        await cache.ensure({"A"}, "2024010100", "2024013100")

        # close() must be callable and safe to call more than once.
        cache.close()
        cache.close()

        # The on-disk data survives disposal.
        path = cache_config.data_paths.views_data_dir / "en.wikipedia" / "2024-01.sqlite3"
        assert _rows(path) == {"A": 10}
