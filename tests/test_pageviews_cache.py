"""
Tests for src.popularpages.pageviews_cache.PageviewsCache.

The PageviewsRepository is replaced with a lightweight async fake so no network
calls are made; we only assert on de-duplication, persistence, and flush
behavior.
"""

import json

import pytest

from src.popularpages.pageviews_cache import PageviewsCache


class FakeRepo:
    """Stand-in for PageviewsRepository.get_title_views."""

    def __init__(self, mapping: dict[str, int]):
        self.mapping = mapping
        self.calls: list[list[str]] = []

    async def get_title_views(self, titles, start, end) -> dict[str, int]:
        self.calls.append(list(titles))
        return {t: self.mapping.get(t, 0) for t in titles}


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Redirect the persisted cache directory to a temp path."""
    monkeypatch.setattr("src.popularpages.pageviews_cache.VIEWS_DATA_DIR", tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_ensure_fetches_all_titles_once_and_persists(cache_dir):
    repo = FakeRepo({"A": 10, "B": 20, "C": 30})
    cache = PageviewsCache("en.wikipedia", "2024-01", repo)
    await cache.ensure({"A", "B", "C"}, "2024010100", "2024013100")

    # All three titles fetched in a single batch call.
    assert repo.calls == [["A", "B", "C"]]

    path = cache_dir / "en.wikipedia" / "2024-01.jsonl"
    assert path.exists()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert {"title": "A", "views": 10} in lines
    assert {"title": "B", "views": 20} in lines
    assert {"title": "C", "views": 30} in lines


@pytest.mark.asyncio
async def test_get_sums_target_and_redirects(cache_dir):
    repo = FakeRepo({"A": 10, "A redir": 5})
    cache = PageviewsCache("en.wikipedia", "2024-01", repo)
    await cache.ensure({"A", "A redir"}, "2024010100", "2024013100")
    assert cache.get("A", ["A redir"]) == 15
    assert cache.get("A", []) == 10
    assert cache.get("Unknown", []) == 0


@pytest.mark.asyncio
async def test_load_reuses_previous_run_and_only_fetches_missing(cache_dir):
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


@pytest.mark.asyncio
async def test_flush_threshold_writes_incrementally(cache_dir, monkeypatch):
    monkeypatch.setattr("src.popularpages.pageviews_cache.VIEWS_FLUSH_TITLES", 3)
    mapping = {f"T{i}": i for i in range(10)}
    repo = FakeRepo(mapping)
    cache = PageviewsCache("en.wikipedia", "2024-02", repo)
    await cache.ensure(set(mapping), "2024020100", "2024022900")

    path = cache_dir / "en.wikipedia" / "2024-02.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    # 10 titles flushed in chunks of 3 -> 4 flushes -> 10 lines total.
    assert len(lines) == 10
    for i in range(10):
        assert json.loads(lines[i]) == {"title": f"T{i}", "views": i}
