# Plan: Persist pageviews to disk and de-duplicate shared articles

## Problem

Today the pageviews for every project are fetched **fresh from the Pageviews
REST API on every run**, and the results are thrown away as soon as a project's
report is rendered:

- `ReportUpdater.update_reports()` loops over projects and calls
  `process_project()` once per project.
- `process_project()` calls
  `WikiRepository.get_monthly_pageviews_and_assessments()`, which pulls
  pageviews from `PageviewsRepository.get_pageviews()` and **discards** them
  when the function returns.
- On `en.wikipedia` many WikiProjects share the same popular articles (e.g.
  *World War II*, *United States*, *Donald Trump*). The same title is therefore
  requested **once per project that contains it** — potentially dozens or
  hundreds of redundant API calls per run.
- If a run is interrupted (network error, rate limit, partial failure) the
  already-fetched views are lost and must be re-fetched on the next run.

## Goal

1. **Persist** fetched pageviews to
   `data/views/<wiki>/<year-month>.jsonl` (one JSON object per line) so the
   data survives the task and can be reused by later runs.
2. **De-duplicate** articles across projects: collect every unique title
   (target + redirects) across *all* projects on a wiki, fetch each title
   **exactly once** for the month, and reuse it for every project that needs
   it.
3. **Batch the disk writes** — append to the JSONL file once per 100 titles
   instead of opening/writing it on every single title.

`year-month` is derived from the previous-month reporting window
(`ReportUpdater.start` → `YYYY-MM`), so each month gets its own append-only
file and data for a finished month is stable.

## Design

### 1. New config constants (`src/popularpages/config.py`)

```python
DATA_DIR = BASE_DIR / "data"
VIEWS_DATA_DIR = DATA_DIR / "views"      # persisted pageviews cache
VIEWS_FETCH_BATCH = 100                  # titles per API batch
VIEWS_FLUSH_TITLES = 100                 # titles buffered before a jsonl write
```

Export them in `__all__`.

### 2. Title-level fetch in `PageviewsRepository` (`pageviews_repository.py`)

Extract the existing per-title fetch logic out of `get_pageviews()` into a
single private coroutine and add a public title-level helper:

```python
async def _fetch_title_views(self, title: str, start: str, end: str) -> int:
    """Total views for one title; 0 on 404 / transport error."""

async def get_title_views(self, titles: list[str], start: str, end: str) -> dict[str, int]:
    """Fetch each title once; returns {title: views}."""
```

`get_pageviews()` is rewritten on top of `_fetch_title_views()` so existing
behavior (sum target + redirects, 0 for missing) and its tests are unchanged.

### 3. New `PageviewsCache` (`src/popularpages/pageviews_cache.py`)

A small async-aware cache keyed by title for one wiki + one month.

```python
class PageviewsCache:
    def __init__(self, wiki: str, year_month: str, pageviews_repo):
        # path = VIEWS_DATA_DIR / wiki / f"{year_month}.jsonl"
        self._cache: dict[str, int] = {}
        self._pending: list[tuple[str, int]] = []
        self._load()                      # reuse previous run's data

    def _load(self):
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    obj = json.loads(line)
                    self._cache[obj["title"]] = obj["views"]

    async def ensure(self, titles: set[str], start: str, end: str) -> None:
        missing = [t for t in titles if t and t not in self._cache]
        for i in range(0, len(missing), VIEWS_FETCH_BATCH):
            chunk = missing[i:i + VIEWS_FETCH_BATCH]
            views = await self.repo.get_title_views(chunk, start, end)
            for t in chunk:
                v = views.get(t, 0)
                self._cache[t] = v
                self._pending.append((t, v))
            if len(self._pending) >= VIEWS_FLUSH_TITLES:
                self._flush()
        self._flush()                     # final partial buffer

    def get(self, target: str, redirects: list[str]) -> int:
        return sum(self._cache.get(t, 0) for t in [target, *redirects] if t)

    def _flush(self):
        if not self._pending:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            for t, v in self._pending:
                f.write(json.dumps({"title": t, "views": v}, ensure_ascii=False) + "\n")
        self._pending = []
```

- `_load()` makes already-fetched titles free on subsequent runs (the
  "not dropped after the task" guarantee).
- `ensure()` only fetches titles missing from the cache and writes the JSONL
  incrementally, flushing every `VIEWS_FLUSH_TITLES` titles.
- `get()` sums a target plus its redirects from the in-memory cache, so a
  shared article is counted once per project but fetched once across the wiki.

### 4. Refactor `ReportUpdater` (`report_updater.py`)

`__init__` already exposes `self.start` / `self.end` (previous month) and
`self.wiki_repository.pageviews_repo`.

**`update_reports()`** becomes a two-phase loop:

1. **Gather + validate (pre-pass).** For every config project, validate it and
   fetch its `get_project_pages()` rows once, storing them in
   `project_pages`. While doing so, accumulate every unique title (target +
   redirect) into `all_titles`.
2. **Build the cache.** `cache = await self._build_views_cache(...)` which
   constructs a `PageviewsCache(self.wiki, year_month, repo)` and calls
   `await cache.ensure(all_titles, start_date, end_date)`. This issues the
   *single* set of API calls for the whole wiki.
3. **Process each project** passing `cache` and the already-fetched
   `page_rows` into `process_project()`, so no project re-queries the API or
   the DB.

**`process_project()`** gains optional `cache` and `page_rows` args. When a
cache is supplied it computes views via a new helper
`_views_for_project_from_cache(page_rows, limit, cache)` (which mirrors the
sort/truncate/total logic of
`WikiRepository.get_monthly_pageviews_and_assessments` but reads from the
cache). The rest of `process_project()` (averages, assessment resolution,
render, `set_text`) is unchanged. The old per-project API path remains as a
fallback when `cache is None`.

`year_month = self.start.strftime("%Y-%m")` (e.g. `2024-01`).

### 5. Ignore the cache on disk (` .gitignore`)

Append `data/` so the persisted pageviews cache is never committed.

## Files touched

| File | Change |
|------|--------|
| `src/popularpages/config.py` | Add `DATA_DIR`, `VIEWS_DATA_DIR`, `VIEWS_FETCH_BATCH`, `VIEWS_FLUSH_TITLES`. |
| `src/popularpages/pageviews_repository.py` | Extract `_fetch_title_views`; add `get_title_views`; rewrite `get_pageviews` on top of it. |
| `src/popularpages/pageviews_cache.py` | **New** `PageviewsCache`. |
| `src/popularpages/report_updater.py` | Two-phase `update_reports`; cache-aware `process_project`; `_build_views_cache`, `_views_for_project_from_cache`. |
| `.gitignore` | Ignore `data/`. |
| `README.md` | Document the `data/views` cache. |
| `tests/test_pageviews_cache.py` | Unit tests for the cache (load, dedup fetch, flush threshold, `get` sum). |
| `tests/test_pageviews_repository.py` | Add a `get_title_views` test. |

## Acceptance criteria

- All unique article titles on a wiki are requested from the Pageviews API at
  most once per month, regardless of how many projects reference them.
- After a run, `data/views/<wiki>/<YYYY-MM>.jsonl` exists and contains every
  fetched title with its view count.
- The JSONL is written incrementally, flushing at most once per 100 titles.
- A second run in the same month reuses the on-disk cache and re-fetches only
  titles not already present (no data loss across runs).
- Existing behavior of `get_pageviews` (target + redirect summation, 0 for
  missing/404) is preserved; existing tests still pass.
- `pytest` is green.
