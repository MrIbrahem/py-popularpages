"""
Tests for PageviewsDb.

Adjust the import below to match your project's package layout, e.g.:
    from myproject.pageviews.pageviews_db import PageviewsDb
"""

from __future__ import annotations

import pytest

from src.py_port.popularpages.pageviews import PageviewsCache
from src.py_port.popularpages.pageviews.pageviews_db import PageviewsDb


# ----------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------
@pytest.fixture
def load_db(tmp_path) -> PageviewsDb:  # pyright: ignore[reportInvalidTypeForm]
    """A fresh PageviewsDb backed by a temp directory, closed after the test."""
    db_file_path = PageviewsCache.build_db_file_path("en.wikipedia", "2024-01", tmp_path)

    instance = PageviewsDb(db_file_path)
    yield instance  # pyright: ignore[reportReturnType]
    instance.close_db()


# ----------------------------------------------------------------
# Lifecycle / init
# ----------------------------------------------------------------
class TestInitAndClose:
    def test_creates_sqlite_file_at_expected_path(self, load_db, tmp_path):
        try:
            expected_path = tmp_path / "en.wikipedia" / "2024-01.sqlite3"
            assert load_db.db_file_path == expected_path
            assert expected_path.exists()
        finally:
            load_db.close_db()

    def test_creates_parent_directories(self, tmp_path):
        db_file_path = PageviewsCache.build_db_file_path("ar.wiktionary", "2023-12", tmp_path)
        db = PageviewsDb(db_file_path)
        try:
            assert (tmp_path / "ar.wiktionary").is_dir()
        finally:
            db.close_db()

    def test_close_does_not_raise(self, load_db):
        load_db.close_db()  # should not raise

    def test_reopening_same_path_reuses_data(self, load_db):
        load_db.upsert_many({"Cairo": 100})
        load_db.close_db()

        try:
            assert load_db.get_views("Cairo", []) == 100
        finally:
            load_db.close_db()


# ----------------------------------------------------------------
# upsert_many
# ----------------------------------------------------------------
class TestUpsertMany:
    def test_empty_dict_is_a_noop(self, load_db: PageviewsDb):
        load_db.upsert_many({})
        assert load_db.query_titles_cache(["Cairo"]) == set()

    def test_inserts_new_rows(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10, "Alexandria": 20})
        assert load_db.get_views("Cairo", []) == 10
        assert load_db.get_views("Alexandria", []) == 20

    def test_updates_existing_row_on_conflict(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10})
        load_db.upsert_many({"Cairo": 999})
        assert load_db.get_views("Cairo", []) == 999
        # still a single row, not a duplicate
        assert load_db.query_titles_cache(["Cairo"]) == {"Cairo"}

    def test_mixed_insert_and_update_in_one_call(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10})
        load_db.upsert_many({"Cairo": 50, "Giza": 5})
        assert load_db.get_views("Cairo", []) == 50
        assert load_db.get_views("Giza", []) == 5


# ----------------------------------------------------------------
# query_titles_cache
# ----------------------------------------------------------------
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


# ----------------------------------------------------------------
# get_views
# ----------------------------------------------------------------
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


# ----------------------------------------------------------------
# get_views_many
# ----------------------------------------------------------------
class TestGetViewsMany:
    def test_empty_targets_returns_empty_dict(self, load_db: PageviewsDb):
        assert load_db.get_views_many([], {}) == {}

    def test_no_matching_titles_returns_zero_for_each_target(self, load_db: PageviewsDb):
        result = load_db.get_views_many(["A", "B"], {})
        assert result == {"A": 0, "B": 0}

    def test_aggregates_target_plus_redirects_per_target(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10, "Al-Qahira": 5, "Alexandria": 20})
        result = load_db.get_views_many(
            ["Cairo", "Alexandria"],
            {"Cairo": ["Al-Qahira"], "Alexandria": []},
        )
        assert result == {"Cairo": 15, "Alexandria": 20}

    def test_shared_redirect_title_counts_for_each_referencing_target(self, load_db: PageviewsDb):
        # A single cached title referenced as a redirect by two different targets
        # should contribute its views to both totals independently.
        load_db.upsert_many({"Shared": 7, "TargetA": 1, "TargetB": 2})
        result = load_db.get_views_many(
            ["TargetA", "TargetB"],
            {"TargetA": ["Shared"], "TargetB": ["Shared"]},
        )
        assert result == {"TargetA": 8, "TargetB": 9}

    def test_matches_get_views_for_single_target(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10, "Al-Qahira": 5})
        single = load_db.get_views("Cairo", ["Al-Qahira"])
        many = load_db.get_views_many(["Cairo"], {"Cairo": ["Al-Qahira"]})
        assert many["Cairo"] == single

    def test_target_missing_from_redirects_by_target_defaults_to_no_redirects(self, load_db: PageviewsDb):
        load_db.upsert_many({"Cairo": 10})
        result = load_db.get_views_many(["Cairo"], {})
        assert result == {"Cairo": 10}


# ----------------------------------------------------------------
# Chunking behaviour (SQLite bound-variable limit safety)
# ----------------------------------------------------------------
class TestChunking:
    def test_query_titles_cache_beyond_chunk_size(self, load_db: PageviewsDb):
        # _SELECT_IN_CHUNK_SIZE is 500; use more than one chunk's worth.
        titles = [f"Title {i}" for i in range(1200)]
        load_db.upsert_many({t: i for i, t in enumerate(titles)})

        cached = load_db.query_titles_cache(titles)
        assert cached == set(titles)

    def test_get_views_many_beyond_chunk_size(self, load_db: PageviewsDb):
        targets = [f"Target {i}" for i in range(1200)]
        load_db.upsert_many({t: i for i, t in enumerate(targets)})

        result = load_db.get_views_many(targets, {})
        assert all(result[f"Target {i}"] == i for i in range(1200))

    def test_upsert_many_large_batch(self, load_db: PageviewsDb):
        # Insert values in a single call spanning multiple chunks on read-back.
        title_views = {f"Bulk {i}": i * 2 for i in range(1000)}
        load_db.upsert_many(title_views)

        cached = load_db.query_titles_cache(list(title_views))
        assert len(cached) == 1000
