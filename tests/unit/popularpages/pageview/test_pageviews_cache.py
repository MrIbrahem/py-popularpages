"""
Tests for src.py_port.popularpages.pageviews.pageviews_cache.PageviewsCache.

The PageviewsRepository is replaced with a lightweight async fake so no network
calls are made; we only assert on de-duplication, persistence, and flush
behavior.
"""

import dataclasses
import json
from unittest.mock import MagicMock

import jsonlines
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
    new_cfg = cfg.config.load()

    monkeypatch.setattr("src.py_port.popularpages.pageviews.pageviews_cache.config", new_cfg)
    monkeypatch.setattr(cache_module, "config", new_cfg)
    return new_cfg


# ---------------------------------------------------------------
# 1. Tests for PageviewsCache.ensure batch fetch and persistence.
# ---------------------------------------------------------------
class TestCacheEnsureAndFetch:
    """Tests for PageviewsCache.ensure batch fetch and persistence."""

    async def test_ensure_fetches_all_titles_once_and_persists(self, cache_config):
        repo = FakeRepo({"A": 10, "B": 20, "C": 30})
        cache = PageviewsCache("en.wikipedia", "2024-01", repo)
        await cache.ensure({"A", "B", "C"}, "2024010100", "2024013100")

        # All three titles fetched in a single batch call (order is unspecified).
        assert len(repo.calls) == 1
        assert set(repo.calls[0]) == {"A", "B", "C"}

        path = cache_config.data_paths.views_data_dir / "en.wikipedia" / "2024-01.jsonl"
        assert path.exists()
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert {"title": "A", "views": 10} in lines
        assert {"title": "B", "views": 20} in lines
        assert {"title": "C", "views": 30} in lines

    async def test_incremental_appends_do_not_truncate(self, cache_config):
        """Two ensures append to the same JSONL file rather than overwriting."""
        repo = FakeRepo({"A": 1, "B": 2, "C": 3})
        cache = PageviewsCache("en.wikipedia", "2024-06", repo)
        await cache.ensure({"A", "B"}, "2024060100", "2024063000")
        await cache.ensure({"C"}, "2024060100", "2024063000")

        path = cache_config.data_paths.views_data_dir / "en.wikipedia" / "2024-06.jsonl"
        with jsonlines.open(path, mode="r") as reader:
            lines = list(reader)
        assert len(lines) == 3
        titles = {line["title"] for line in lines}
        assert titles == {"A", "B", "C"}
        # A and B were already cached on the second ensure, so only C was fetched.
        assert [sorted(c) for c in repo.calls] == [["A", "B"], ["C"]]

    async def test_flush_threshold_writes_incrementally(self, tmp_path, monkeypatch):
        small_flush = dataclasses.replace(
            cfg.config,
            pageviews=dataclasses.replace(cfg.config.pageviews, flush_titles=3),
        )
        monkeypatch.setattr(cache_module, "config", small_flush)

        mapping = {f"T{i}": i for i in range(10)}
        repo = FakeRepo(mapping)
        cache = PageviewsCache(
            "en.wikipedia", "2024-02", repo, path_dir=tmp_path
        )  # pyright: ignore[reportArgumentType]
        await cache.ensure(set(mapping), "2024020100", "2024022900")

        path = tmp_path / "en.wikipedia" / "2024-02.jsonl"
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        # 10 titles flushed in chunks of 3 -> 4 flushes -> 10 lines total.
        assert len(lines) == 10
        expected = [{"title": f"T{i}", "views": i} for i in range(10)]
        assert sorted(lines, key=lambda d: d["title"]) == sorted(expected, key=lambda d: d["title"])


# ---------------------------------------------------------------
# 2. Tests for PageviewsCache.get summing target and redirects.
# ---------------------------------------------------------------
class TestCacheGet:
    """Tests for PageviewsCache.get summing target and redirects."""

    async def test_get_sums_target_and_redirects(self, cache_config):
        repo = FakeRepo({"A": 10, "A redir": 5})
        cache = PageviewsCache("en.wikipedia", "2024-01", repo)
        await cache.ensure({"A", "A redir"}, "2024010100", "2024013100")
        assert cache.get("A", ["A redir"]) == 15
        assert cache.get("A", []) == 10
        assert cache.get("Unknown", []) == 0


# ---------------------------------------------------------------
# 3. Tests for loading/reusing a previously persisted cache.
# ---------------------------------------------------------------
class TestCacheLoadReuse:
    """Tests for loading/reusing a previously persisted cache."""

    async def test_load_reuses_previous_run_and_only_fetches_missing(self, cache_config):
        repo1 = FakeRepo({"A": 10, "B": 20})
        cache1 = PageviewsCache("en.wikipedia", "2024-01", repo1)
        await cache1.ensure({"A", "B"}, "2024010100", "2024013100")
        assert repo1.calls  # fetched on first run

        # New cache for the same wiki/month should not re-fetch existing titles.
        repo2 = FakeRepo({"A": 999, "B": 999, "C": 999})
        cache2 = PageviewsCache("en.wikipedia", "2024-01", repo2)
        assert repo2.calls == []  # nothing fetched at construction

        await cache2.ensure({"A", "B", "C"}, "2024010100", "2024013100")
        # Only the previously-missing title "C" is fetched.
        assert repo2.calls == [["C"]]
        # Values come from disk (10), not the fake's 999.
        assert cache2.get("A", []) == 10
        assert cache2.get("C", []) == 999

    async def test_missing_file_loads_empty(self, cache_config):
        """A wiki/month with no cache file loads an empty cache, no fetch."""
        repo = FakeRepo({"A": 10})
        cache = PageviewsCache("en.wikipedia", "2099-12", repo)
        # _load() ran at construction and found nothing.
        assert cache.get("A", []) == 0
        # No on-disk file is created until something is flushed.
        path = cache_config.data_paths.views_data_dir / "en.wikipedia" / "2099-12.jsonl"
        assert not path.exists()

    async def test_empty_file_loads_empty(self, cache_config):
        """An existing but empty cache file loads an empty cache, no crash."""
        path = cache_config.data_paths.views_data_dir / "en.wikipedia" / "2024-05.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

        repo = FakeRepo({"A": 10})
        cache = PageviewsCache("en.wikipedia", "2024-05", repo)
        assert cache.get("A", []) == 0

    async def test_load_oserror_is_swallowed(self, cache_config, monkeypatch):
        """A read error while loading is logged and treated as an empty cache."""
        repo = FakeRepo({"A": 10})
        cache = PageviewsCache("en.wikipedia", "2024-07", repo)
        # Make path.report exist (so we reach the read) but have read_text raise.
        fake = MagicMock()
        fake.exists.return_value = True
        fake.read_text.side_effect = OSError("permission denied")
        cache.path = fake
        cache._load()  # must not raise
        assert cache.get("A", []) == 0


# ---------------------------------------------------------------
# 4. Tests for on-disk JSONL format, non-ASCII, and malformed-line handling.
# ---------------------------------------------------------------
class TestCacheJsonlFormat:
    """Tests for on-disk JSONL format, non-ASCII, and malformed-line handling."""

    async def test_written_file_is_valid_jsonl_readable_by_jsonlines(self, cache_config):
        """The on-disk cache must be valid JSONL that jsonlines can read back."""
        repo = FakeRepo({"A": 10, "B": 20, "C": 30})
        cache = PageviewsCache("en.wikipedia", "2024-01", repo)
        await cache.ensure({"A", "B", "C"}, "2024010100", "2024013100")

        path = cache_config.data_paths.views_data_dir / "en.wikipedia" / "2024-01.jsonl"
        # Read the file back with jsonlines itself (not json.loads line-by-line).
        with jsonlines.open(path, mode="r") as reader:
            lines = list(reader)
        assert {"title": "A", "views": 10} in lines
        assert {"title": "B", "views": 20} in lines
        assert {"title": "C", "views": 30} in lines
        # Values come from disk, not the fake.
        assert cache.get("A", []) == 10

    async def test_non_ascii_title_round_trips(self, cache_config):
        """Non-ASCII titles are written as UTF-8 and read back identically."""
        title = "Café_İstanbul"
        repo = FakeRepo({title: 7})
        cache = PageviewsCache("en.wikipedia", "2024-03", repo)
        await cache.ensure({title}, "2024030100", "2024033100")

        path = cache_config.data_paths.views_data_dir / "en.wikipedia" / "2024-03.jsonl"
        with jsonlines.open(path, mode="r") as reader:
            lines = list(reader)
        assert lines == [{"title": title, "views": 7}]
        # The title is stored as literal UTF-8, not \u-escaped ASCII.
        raw = path.read_text(encoding="utf-8")
        assert "Café_İstanbul" in raw
        assert cache.get(title, []) == 7

    async def test_malformed_lines_are_skipped_on_load(self, cache_config):
        """Invalid JSON and malformed-but-valid objects are skipped, not fatal."""
        repo1 = FakeRepo({"A": 10, "B": 20})
        cache1 = PageviewsCache("en.wikipedia", "2024-01", repo1)
        await cache1.ensure({"A", "B"}, "2024010100", "2024013100")

        path = cache_config.data_paths.views_data_dir / "en.wikipedia" / "2024-01.jsonl"
        # Append a line that is not valid JSON and a dict missing 'views'.
        with path.open("a", encoding="utf-8") as f:
            f.write("this is not json\n")
            f.write(json.dumps({"title": "Z"}) + "\n")

        # A fresh cache must load only the two valid entries and not crash.
        repo2 = FakeRepo({"A": 999, "B": 999})
        cache2 = PageviewsCache("en.wikipedia", "2024-01", repo2)
        assert repo2.calls == []  # nothing fetched -- both entries were on disk
        assert cache2.get("A", []) == 10
        assert cache2.get("B", []) == 20
        assert cache2.get("Z", []) == 0  # malformed object skipped
