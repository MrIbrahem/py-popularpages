## Problem

`pageviews_repository.py` currently fetches monthly view counts by issuing **one Wikimedia Pageviews REST API request per article title**, deduplicated once per wiki per month. For large wikis like `en.wikipedia`, this still means hundreds of thousands to millions of HTTP requests per run, which is:

-   Slow (dominates total runtime of `check_reports.py`).
-   Heavy load on the Wikimedia REST API for data already published in bulk.
-   Fragile: subject to rate limiting, timeouts, and partial failures across a huge number of individual requests.

Since the tool already runs **on Toolforge**, the monthly `pageview_complete` dumps are available directly on the local filesystem via NFS — no download needed:

```
/public/dumps/public/other/pageview_complete/monthly/YYYY/YYYY-MM/pageviews-YYYYMM-user.bz2
```

Example (2026-07): ~5.28 GB compressed, containing per-article daily totals for **every Wikimedia project** for the whole month, in a single file already sitting on disk.

## Goal

Replace the REST-API-per-title fetching path with a pipeline that:

1. Opens the monthly dump directly from `/public/dumps/public/other/pageview_complete/...` (no download step).
2. Streams and parses it (bzip2, line-by-line — never fully decompressed to a temp file).
3. Filters only the wiki codes we actually care about (from `config/wikis.yaml` / configured WikiProjects).
4. Aggregates monthly totals per title.
5. Writes results into the existing per-wiki/month SQLite cache, one file per `data/views/<wiki>/<YYYY-MM>.sqlite3`, using the existing `PageView` model (`title` primary key, `views` integer) — so downstream code (`ReportUpdater`, etc.) needs no changes, only the _source_ of the cached data changes.

## Format notes (from dumps README)

Each line: `wiki_code article_title page_id daily_total hourly_counts`

-   **Known issue**: rows without a `page_id` have only 5 columns instead of 6 — parser must handle both cases.
-   We only need `wiki_code`, `article_title`, and `daily_total`; `hourly_counts`/`page_id` can be ignored for our use case.
-   Titles likely use underscores like standard MediaWiki API responses — verify against a real sample before finalizing the parser.

## Proposed plan

-   [ ] **Path resolution**: build the dump path from year/month, e.g. `/public/dumps/public/other/pageview_complete/monthly/{year}/{year}-{month:02d}/pageviews-{year}{month:02d}-user.bz2`; confirm exact path/filename pattern against the live mount before hardcoding.
-   [ ] **Streaming parser**: read via `bz2.open(path, "rt")` line-by-line directly from the NFS path; no copying the file locally first.
-   [ ] **Wiki filtering**: only keep lines where `wiki_code` matches one of the configured wikis.
-   [ ] **Title filtering (optional optimization)**: if the set of needed titles per wiki is known ahead of time (from WikiProject configs, same as current REST approach), skip totals for titles we'll never use — reduces memory footprint.
-   [ ] **Aggregation**: single pass over the file, summing `daily_total` per `(wiki, title)`, keeping running totals per wiki in memory (or batching to disk if memory is a concern for very large wikis).
-   [ ] **DB write strategy**: for each wiki, create/open `data/views/<wiki>/<YYYY-MM>.sqlite3` and bulk-upsert into the `pageviews` table using the existing `PageView(title, views)` model — use `session.bulk_insert_mappings`/batched inserts rather than row-by-row commits, since a wiki like `en.wikipedia` can have millions of distinct titles.
-   [ ] **Fallback**: keep the REST API path available (`--source=api` vs `--source=dump`) in case a given month's dump isn't published yet, or the tool needs to run before the monthly dump lands.
-   [ ] **Toolforge job**: run as a Toolforge job (not webservice) given single-pass processing time over a multi-GB compressed file; decide whether to process all configured wikis in one pass (keeping multiple per-wiki dicts in memory) or one wiki at a time (multiple passes over the file, lower peak memory, more I/O).
-   [ ] **Tests**: unit tests for the line parser (5-column vs 6-column, malformed lines, unicode titles) using small local fixture files — not the live dump. Also test that the aggregation + SQLite write path produces a `PageView` table matching what the REST-based path currently produces, for a small fixture wiki.

## Open questions

-   Exact NFS path pattern — confirm by listing `/public/dumps/public/other/pageview_complete/monthly/` directly on Toolforge.
-   Timing: dumps for month M typically land a few days into month M+1 — does the existing `0 0 1 * *` cron need to shift later in the month?
-   Memory strategy: one full pass building per-wiki dicts for all configured wikis at once vs. one pass per wiki — trade-off between total I/O time and peak RAM.
-   Should the SQLite file be rebuilt from scratch each run, or upserted incrementally (matters if a partial/interrupted run needs to resume)?

## References

-   Dump format docs: https://dumps.wikimedia.org/other/pageview_complete/readme.html
-   https://wikitech.wikimedia.org/wiki/Data_Platform/Data_Lake/Traffic/Pageviews
-   Local Toolforge path: `/public/dumps/public/other/pageview_complete/`
-   Existing cache model: `PageView` (`title: str` PK, `views: int`) in `pageviews_models.py`
