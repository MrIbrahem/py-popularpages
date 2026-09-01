"""
Tests for src.py_port.popularpages.pageviews.pageviews_db.PageviewsDb.

The SQLite-backed store is exercised directly (no PageviewsCache, no network):
we upsert rows with ``upsert_many`` and assert on ``get_views`` /
``get_views_many`` / ``query_titles_cache`` lookup behavior, conflict/overwrite
semantics, chunked-query safety, and the ``close_db`` lifecycle.

Note: PageviewsDb's methods are synchronous, so all tests here are plain
(non-async) functions/methods.
"""

from __future__ import annotations

import sqlite3

import pytest

import src.py_port.popularpages.pageviews.pageviews_db as cache_module
from src.py_port.popularpages.pageviews import PageviewsCache
from src.py_port.popularpages.pageviews.pageviews_db import PageviewsDb


# ---------------------------------------------------
# Fixtures
# ---------------------------------------------------
@pytest.fixture
def db_dir(tmp_path):
    """A temp directory to host the SQLite cache files."""
    d = tmp_path / "views"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def db_factory(db_dir, converte_underscore_to_space: bool = True):
    """Create PageviewsDb instances (optionally under a custom base dir) and
    ensure they are all closed on teardown."""
    created: list[PageviewsDb] = []

    def _make(wiki: str = "en.wikipedia", year_month: str = "2024-01", base_dir=None) -> PageviewsDb:
        db_file_path = PageviewsCache.build_db_file_path(wiki, year_month, base_dir or db_dir)
        db = PageviewsDb(db_file_path, converte_underscore_to_space=converte_underscore_to_space)
        created.append(db)
        return db

    yield _make
    for db in created:
        db.close_db()


@pytest.fixture
def load_db(db_factory) -> PageviewsDb:
    """A single default PageviewsDb instance for tests that don't need custom params."""
    return db_factory()


@pytest.fixture
def load_db_no_underscore_converte(db_factory) -> PageviewsDb:
    return db_factory(converte_underscore_to_space=False)


def _rows(sqlite_path) -> dict[str, int]:
    """Read title -> views rows directly from a SQLite cache file."""
    con = sqlite3.connect(str(sqlite_path))
    try:
        cur = con.execute("SELECT title, views FROM pageviews ORDER BY title")
        return dict(cur.fetchall())
    finally:
        con.close()


# ---------------------------------------------------
# Lifecycle / init
# ---------------------------------------------------
class TestInitAndClose:
    def test_creates_sqlite_file_at_expected_path(self, db_factory, db_dir):
        db = db_factory("en.wikipedia", "2024-01")
        expected_path = db_dir / "en.wikipedia" / "2024-01.sqlite3"
        assert db.db_file_path == expected_path
        assert expected_path.exists()

    def test_creates_parent_directories(self, db_factory, db_dir):
        db = db_factory("ar.wiktionary", "2023-12")
        assert (db_dir / "ar.wiktionary").is_dir()

    def test_close_is_idempotent_and_safe(self, db_factory):
        db = db_factory("en.wikipedia", "2024-01")
        db.upsert_many({"A": 10})

        # close_db() must be callable and safe to call more than once.
        db.close_db()
        db.close_db()

        # The on-disk data survives disposal.
        assert _rows(db.db_file_path) == {"A": 10}

    def test_reopening_same_path_reuses_data(self, db_factory):
        db1 = db_factory("en.wikipedia", "2024-02")
        db1.upsert_many({"Cairo": 100})
        db1.close_db()

        db2 = db_factory("en.wikipedia", "2024-02")
        assert db2.get_views("Cairo", []) == 100


# ---------------------------------------------------
# upsert_many
# ---------------------------------------------------
class TestUpsertMany:
    def test_empty_dict_is_a_noop(self, load_db: PageviewsDb):
        load_db.upsert_many({})
        assert load_db.query_titles_cache(["Cairo"]) == set()

    def test_inserts_new_rows(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10, "Alexandria": 20})
        assert load_db.get_views("Cairo", []) == 10
        assert load_db.get_views("Alexandria", []) == 20

    def test_re_fetch_overwrites_existing_row_not_duplicates(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10})
        # Re-upserting the same title with a new view count must overwrite the
        # existing row (primary-key conflict), never duplicate it.
        load_db.upsert_many({"Cairo": 999})

        assert _rows(load_db.db_file_path) == {"Cairo": 999}  # exactly one row
        assert load_db.get_views("Cairo", []) == 999
        assert load_db.query_titles_cache(["Cairo"]) == {"Cairo"}

    def test_mixed_insert_and_update_in_one_call(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10})
        load_db.upsert_many({"Cairo": 50, "Giza": 5})
        assert load_db.get_views("Cairo", []) == 50
        assert load_db.get_views("Giza", []) == 5


# ---------------------------------------------------
# query_titles_cache
# ---------------------------------------------------
class TestQueryTitlesCache:
    def test_empty_wanted_returns_empty_set(self, load_db: PageviewsDb):
        assert load_db.query_titles_cache([]) == set()

    def test_returns_only_cached_titles(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10, "Alexandria": 20})
        result = load_db.query_titles_cache(["Cairo", "Alexandria", "Luxor"])
        assert result == {"Cairo", "Alexandria"}

    def test_none_cached_returns_empty_set(self, load_db: PageviewsDb):
        result = load_db.query_titles_cache(["Unknown 1", "Unknown 2"])
        assert result == set()


# ---------------------------------------------------
# get_views
# ---------------------------------------------------
class TestGetViews:
    def test_target_with_no_redirects(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 42})
        assert load_db.get_views("Cairo", []) == 42

    def test_sums_target_and_redirects(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10, "Al-Qahira": 5, "Qahira": 3})
        assert load_db.get_views("Cairo", ["Al-Qahira", "Qahira"]) == 18

    def test_missing_title_returns_zero(self, load_db: PageviewsDb):
        assert load_db.get_views("Nonexistent", []) == 0

    def test_missing_redirect_is_ignored_not_erroring(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10})
        assert load_db.get_views("Cairo", ["Unknown Redirect"]) == 10

    def test_falsy_titles_in_redirects_are_skipped(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10})
        assert load_db.get_views("Cairo", ["", None]) == 10  # type: ignore[list-item]

    def test_empty_target_and_no_redirects_returns_zero(self, load_db: PageviewsDb):
        assert load_db.get_views("", []) == 0


# ---------------------------------------------------
# one_title_views
# ---------------------------------------------------
class TestOneTitleViews:

    def test_with_underscore(self, load_db: PageviewsDb):
        load_db.upsert_many({"test_ye": 15_000})
        assert load_db.one_title_views("test_ye") is None
        assert load_db.one_title_views("test ye") == 15_000

    def test_target_with_no_redirects(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 42})
        assert load_db.one_title_views("Cairo") == 42

    def test_missing_title_returns_zero(self, load_db: PageviewsDb):
        assert load_db.one_title_views("Nonexistent") is None


# ---------------------------------------------------
# get_views_many (bulk lookup used by large projects)
# ---------------------------------------------------
class TestGetViewsMany:
    def test_empty_targets_returns_empty_dict(self, load_db: PageviewsDb):
        assert load_db.get_views_many({}) == {}

    def test_no_matching_titles_returns_zero_for_each_target(self, load_db: PageviewsDb):
        result2 = load_db.get_views_many({"A": [], "B": []})
        assert result2 == {"A": 0, "B": 0}

    def test_aggregates_target_plus_redirects_per_target(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10, "Al-Qahira": 5, "Alexandria": 20})

        result2 = load_db.get_views_many(
            {"Cairo": ["Al-Qahira"], "Alexandria": []},
        )
        assert result2 == {"Cairo": 15, "Alexandria": 20}

    def test_unknown_target_with_missing_redirects_is_zero(self, load_db: PageviewsDb):
        load_db.upsert_many({"A": 10})

        result2 = load_db.get_views_many({"A": [], "Unknown": ["Also missing"]})
        assert result2 == {"A": 10, "Unknown": 0}

    def test_shared_redirect_counts_for_each_referencing_target(self, load_db: PageviewsDb):
        """A redirect referenced by two targets is looked up once but its
        views are counted independently towards each target's total."""
        load_db.upsert_many({"Shared": 9, "TargetA": 1, "TargetB": 2})

        result2 = load_db.get_views_many(
            {"TargetA": ["Shared"], "TargetB": ["Shared"]},
        )
        assert result2 == {"TargetA": 10, "TargetB": 11}

    def test_matches_get_views_for_single_target(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10, "Al-Qahira": 5})
        single = load_db.get_views("Cairo", ["Al-Qahira"])

        many2 = load_db.get_views_many({"Cairo": ["Al-Qahira"]})
        assert many2["Cairo"] == single

    def test_target_missing_from_redirects_by_target_defaults_to_no_redirects(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10})

        result2 = load_db.get_views_many({"Cairo": []})
        assert result2 == {"Cairo": 10}


# ---------------------------------------------------
# Chunking behaviour (SQLite bound-variable limit safety)
# ---------------------------------------------------
class TestChunking:
    def test_get_views_many_chunks_large_title_set(self, db_factory, monkeypatch):
        """A huge title set is queried in _SELECT_IN_CHUNK_SIZE-sized chunks.

        Overrides the chunk size to exercise the chunked-query code path
        without needing to insert an unwieldy number of rows.
        """
        monkeypatch.setattr(cache_module.PageviewsDb, "_SELECT_IN_CHUNK_SIZE", 100)

        n = 250
        mapping = {f"T{i}": i for i in range(n)}
        db = db_factory("en.wikipedia", "2024-03")
        db.upsert_many(mapping)

        targets = [f"T{i}" for i in range(n)]

        counts2 = db.get_views_many({t: [] for t in targets})
        assert counts2 == mapping

    def test_query_titles_cache_beyond_chunk_size(self, load_db: PageviewsDb):
        # Default _SELECT_IN_CHUNK_SIZE is 500; use more than one chunk's worth.
        titles = [f"Title {i}" for i in range(1200)]
        load_db.upsert_many({t: i for i, t in enumerate(titles)})

        cached = load_db.query_titles_cache(titles)
        assert cached == set(titles)

    def test_get_views_many_beyond_chunk_size(self, load_db: PageviewsDb):
        targets = [f"Target {i}" for i in range(1200)]
        load_db.upsert_many({t: i for i, t in enumerate(targets)})

        result2 = load_db.get_views_many(dict.fromkeys(targets, []))
        assert all(result2[f"Target {i}"] == i for i in range(1200))

    def test_upsert_many_large_batch(self, load_db: PageviewsDb):
        # Insert values in a single call spanning multiple chunks on read-back.
        title_views = {f"Bulk {i}": i * 2 for i in range(1000)}
        load_db.upsert_many(title_views)

        cached = load_db.query_titles_cache(list(title_views))
        assert len(cached) == 1000


# ---------------------------------------------------
# count_titles
# ---------------------------------------------------
class TestCountTitles:
    def test_empty_db_returns_zero(self, load_db: PageviewsDb):
        assert load_db.count_titles() == 0

    def test_counts_distinct_titles(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10, "Alexandria": 20})
        assert load_db.count_titles() == 2

    def test_reupserting_same_title_does_not_inflate_count(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10})
        load_db.upsert_many({"Cairo": 99})
        assert load_db.count_titles() == 1


# ---------------------------------------------------
# upsert_many_chunks (SQLite bound-variable limit workaround)
# ---------------------------------------------------
class TestUpsertManyChunks:
    def test_empty_dict_is_a_noop(self, load_db: PageviewsDb):
        load_db.upsert_many_chunks({})
        assert load_db.query_titles_cache(["Cairo"]) == set()

    def test_small_batch_delegates_to_upsert_many(self, load_db: PageviewsDb):
        """A batch smaller than the chunk_size takes the early-return path
        straight to ``upsert_many``."""
        load_db.upsert_many_chunks({"Cairo": 10, "Giza": 5})
        assert load_db.get_views("Cairo", []) == 10
        assert load_db.get_views("Giza", []) == 5

    def test_large_batch_is_split_into_chunks(self, load_db: PageviewsDb, monkeypatch):
        """Force the small SQLite bound limit so a modest batch still exercises
        the multi-chunk write loop inside ``upsert_many_chunks``."""
        monkeypatch.setattr("sqlite3.sqlite_version_info", (3, 31, 0))
        n = 2500
        title_views = {f"Bulk {i}": i for i in range(n)}
        load_db.upsert_many_chunks(title_views)

        # Every title was written despite spanning 3 chunks (900 + 900 + 700).
        assert load_db.count_titles() == n
        assert load_db.get_views("Bulk 0", []) == 0
        assert load_db.get_views(f"Bulk {n - 1}", []) == n - 1

    def test_large_batch_preserves_values_across_chunks(self, load_db: PageviewsDb, monkeypatch):
        monkeypatch.setattr("sqlite3.sqlite_version_info", (3, 31, 0))
        title_views = {f"T{i}": i * 3 for i in range(2000)}
        load_db.upsert_many_chunks(title_views)

        sampled = load_db.get_views_many({f"T{i}": [] for i in (0, 500, 1999)})
        assert sampled == {"T0": 0, "T500": 1500, "T1999": 5997}


# ---------------------------------------------------
# Underscore -> space normalization in upsert_many
# ---------------------------------------------------
class TestUnderscoreNormalization:
    def test_underscores_converted_to_spaces_on_upsert(self, load_db: PageviewsDb):
        load_db.upsert_many({"New_York_City": 100})
        # The stored key uses spaces, so the underscore form is not found...
        assert load_db.get_views("New_York_City", []) == 0
        # ...and the space form is.
        assert load_db.get_views("New York City", []) == 100
        assert _rows(load_db.db_file_path) == {"New York City": 100}

    def test_mixed_underscore_and_space_titles(self, load_db: PageviewsDb):
        load_db.upsert_many({"Los_Angeles": 5, "San Francisco": 7})
        assert load_db.get_views("Los Angeles", []) == 5
        assert load_db.get_views("San Francisco", []) == 7
        assert set(_rows(load_db.db_file_path)) == {"Los Angeles", "San Francisco"}

    def test_normalization_applies_in_chunked_upsert(self, load_db: PageviewsDb, monkeypatch):
        monkeypatch.setattr("sqlite3.sqlite_version_info", (3, 31, 0))
        load_db.upsert_many_chunks({f"Title_{i}": i for i in range(2000)})

        assert load_db.one_title_views("Title 0") == 0
        assert load_db.get_views("Title 0", []) == 0

        assert load_db.get_views("Title 1999", []) == 1999
        assert "Title 0" in _rows(load_db.db_file_path)
        assert "Title_0" not in _rows(load_db.db_file_path)
