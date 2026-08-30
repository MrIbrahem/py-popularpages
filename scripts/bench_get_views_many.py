"""
Benchmark: per-target `get_views` loop vs bulk `get_views_many`.

Reproduces the >900,000-title scenario that prompted the bulk lookup. Populates
a real SQLite cache (via a fake repo, no network) and times both lookup paths
over the same targets + redirects.

Usage:
    python scripts/bench_get_views_many.py [n_titles]

Defaults to 900,000 titles. Lower it (e.g. 50000) for a quick smoke run.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

# Allow running as `python scripts/bench_get_views_many.py` (sys.path[0] would
# otherwise be the scripts dir, not the repo root that exposes `src`).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.py_port.popularpages.config as cfg
from src.py_port.popularpages.pageviews.pageviews_cache import PageviewsCache


class FakeRepo:
    async def get_title_views(self, titles, start, end):
        return {t: hash(t) % 10_000 for t in titles}


def _build(n: int, wiki_dir: Path) -> PageviewsCache:
    """Populate a cache with `n` target titles and `n` redirect titles."""
    # Redirect the data dir to a temp location via env so the cache lives there.
    os.environ["POPULAR_PAGES_MAIN_DIR"] = str(wiki_dir)
    new_cfg = cfg.config.load()
    import src.py_port.popularpages.pageviews.pageviews_cache as m

    m.config = new_cfg

    repo = FakeRepo()
    cache = PageviewsCache("en.wikipedia", "2024-01", repo, path_dir=wiki_dir)
    targets = {f"Target {i}" for i in range(n)}
    # Silence the tqdm progress bar during the build phase (it floods piped output).
    import src.py_port.popularpages.pageviews.pageviews_cache as m

    old_tqdm = m.tqdm
    m.tqdm = lambda it, *a, **k: it
    try:
        asyncio.run(cache.ensure(targets | redirects, "2024010100", "2024013100"))
    finally:
        m.tqdm = old_tqdm
    redirects = {f"Redirect {i}" for i in range(n)}
    asyncio.run(cache.ensure(targets | redirects, "2024010100", "2024013100"))
    return cache


def _make_payload(n: int):
    targets = [f"Target {i}" for i in range(n)]
    redirects = {t: [f"Redirect {i}"] for i, t in enumerate(targets)}
    return targets, redirects


def _old_loop(cache: PageviewsCache, targets, redirects) -> int:
    total = 0
    for t in targets:
        total += cache.get_views(t, redirects[t])
    return total


def _new_bulk(cache: PageviewsCache, targets, redirects) -> int:
    counts = cache.get_views_many(targets, redirects)
    return sum(counts.values())


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 900_000
    with tempfile.TemporaryDirectory() as tmp:
        wiki_dir = Path(tmp) / "data"
        print(f"Building cache with {n:,} targets + {n:,} redirects...")
        t0 = time.perf_counter()
        cache = _build(n, wiki_dir)
        print(f"  build took {time.perf_counter() - t0:.2f}s")

        targets, redirects = _make_payload(n)

        # Warm up the connection pool.
        cache.get_views(targets[0], redirects[targets[0]])

        print("Timing per-target `get_views` loop (old) ...")
        t0 = time.perf_counter()
        old_total = _old_loop(cache, targets, redirects)
        old_dt = time.perf_counter() - t0
        print(f"  old: {old_dt:.3f}s  (total={old_total:,})")

        print("Timing bulk `get_views_many` (new) ...")
        t0 = time.perf_counter()
        new_total = _new_bulk(cache, targets, redirects)
        new_dt = time.perf_counter() - t0
        print(f"  new: {new_dt:.3f}s  (total={new_total:,})")

        assert old_total == new_total, "lookup totals disagree!"
        speedup = old_dt / new_dt if new_dt else float("inf")
        print(f"\nSpeedup (new vs old): {speedup:.1f}x  ({old_dt:.3f}s -> {new_dt:.3f}s)")
        cache.close()


if __name__ == "__main__":
    main()
