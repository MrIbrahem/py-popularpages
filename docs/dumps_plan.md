# Plan: Replace REST-API Pageview Fetching with Direct Dump Processing

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

## Confirmed data format (from real samples, validated with a working parser + unit tests)

Each line has a fixed structure of space-separated fields:

```
wiki_code  title  page_id  agent  daily_total  [hourly_counts]
```

-   **`page_id` is always present** as a field — either a numeric ID or the literal string `null`. It is never omitted.
-   **`hourly_counts` is optional** and may be absent at the end of the line — but it's **not needed for this use case** and is discarded entirely regardless of whether it's present.
-   A targeted sample of 999 consecutive `ar.wikipedia` lines showed 6 space-separated columns in 100% of cases when split naively via `line.split(' ')`. This is a useful sanity check but **must not be treated as a structural guarantee across the full ~5.28 GB file** or other wikis — the parser must not hardcode an assumption of exactly 6 columns.
-   Titles use underscores for spaces, as in standard MediaWiki API responses.
-   Titles can contain arbitrary punctuation, including leading special characters (`!`, `'`, `(`), Arabic script, and wiki markup remnants (`'''` for bold) — none of this should be misinterpreted as delimiters or escaped/stripped.
-   **Titles containing a literal double-quote character (`"`) use CSV-style conditional quoting — confirmed against raw file bytes and against a working parser validated with unit tests.** A title with **no** `"` character is written completely bare, with no wrapping. A title that **does** contain a literal `"` is wrapped in an outer pair of _unescaped_ double-quotes, and every literal `"` inside the title is escaped as `\"`. For example:

    -   Title `!` (no quote char) → written bare as `!`.
    -   Title `"` (a single quote character) → written as `"\""` (open-quote, escaped inner quote, close-quote).
    -   Title `"W"_تشير_الى_المنتهي` → written as `"\"W\"_تشير_الى_المنتهي"`.

    **Important correction from an earlier draft of this plan**: a naive `title.replace('\\"', '"')` is _insufficient and produces wrong output_ — it leaves the outer wrapper quotes in place (e.g. turns `"\""` into `"""` instead of `"`). The correct unescaping logic must first detect and strip the outer wrapper (only present when the title contains a `"`), then unescape the inner `\"` sequences:

    ```python
    def unescape_title(raw_title: str) -> str:
    ```

-   The same logical page can appear under **multiple different title strings** with the same `page_id` (e.g. an Arabic-script alias vs. a Latin transliteration). `page_id` must **not** be used as the aggregation key — aggregation is by `title` string only (post-unescaping), matching current REST-based behavior (which queries by exact title).

## Proposed plan

-   [ ] **Path resolution**: build the dump path from year/month, e.g. `/public/dumps/public/other/pageview_complete/monthly/{year}/{year}-{month:02d}/pageviews-{year}{month:02d}-user.bz2`; confirm exact path/filename pattern against the live mount before hardcoding.
-   [ ] **Streaming parser**: read via `bz2.open(path, "rt")` line-by-line directly from the NFS path; no copying the file locally first.
    -   Parse each line with `line.split(' ', maxsplit=4)` to get exactly 5 parts: `[wiki_code, title, page_id, agent, rest]`. Use `maxsplit=4` from the left rather than relying on a fixed total column count.
    -   Extract `daily_total = int(rest.split(' ', maxsplit=1)[0])`. This works whether `hourly_counts` is present or absent — anything after `daily_total` in `rest` is discarded, never inspected or parsed.
    -   **Unescape the title using the corrected wrap-then-unescape logic above** (`unescape_title`), not a plain string replace — a plain replace was tried, tested, and shown to produce incorrect output (leftover wrapper quotes) for every title containing a literal `"`.
    -   `page_id` is parsed but discarded (not used downstream).
    -   **Implementation validated**: a reference implementation (`bz2_dump_parser.py`) and a full pytest suite (`test_bz2_dump_parser.py`, 25 tests) have been written and pass against a real fixture built from actual `ar.wikipedia` dump lines. This can be used as the starting point for the production parser.
-   [ ] **Wiki filtering**: only keep lines where `wiki_code` matches one of the configured wikis.
-   [ ] **Title filtering (optional optimization)**: if the set of needed titles per wiki is known ahead of time (from WikiProject configs, same as current REST approach), skip totals for titles we'll never use — reduces memory footprint. Note this filtering must happen post-unescaping, so titles are compared in their true (unescaped) form.
-   [ ] **Aggregation**: single pass over the file, summing `daily_total` per `(wiki, title)` — **`title` (post-unescaping) is the sole aggregation key; `page_id` is explicitly not used for merging**, since the same `page_id` can legitimately appear under multiple distinct title strings and each must be kept/aggregated separately to match current REST behavior. Keep running totals per wiki in memory (or batch to disk if memory is a concern for very large wikis).
-   [ ] **DB write strategy**: for each wiki, create/open `data/views/<wiki>/<YYYY-MM>.sqlite3` and bulk-upsert into the `pageviews` table using the existing `PageView(title, views)` model — use `session.bulk_insert_mappings`/batched inserts rather than row-by-row commits, since a wiki like `en.wikipedia` can have millions of distinct titles.
-   [ ] **Fallback**: keep the REST API path available (`--source=api` vs `--source=dump`) in case a given month's dump isn't published yet, or the tool needs to run before the monthly dump lands.
-   [ ] **Toolforge job**: run as a Toolforge job (not webservice) given single-pass processing time over a multi-GB compressed file; decide whether to process all configured wikis in one pass (keeping multiple per-wiki dicts in memory) or one wiki at a time (multiple passes over the file, lower peak memory, more I/O).
-   [ ] **Wider validation pass before finalizing parser**: re-run the column-counting sanity script (as used for the 999-line `ar.wikipedia` sample) across a much larger sample — ideally the full file or a large multi-wiki subset — to check for any lines that don't fit the assumed structure, and specifically to check whether the CSV-style quoting rule holds for all wikis (not just `ar.wikipedia`) before treating it as final.
-   [ ] **Tests**: unit tests for the line parser using small local fixture files (built from real sample lines, not synthetic data). A working suite already exists covering:
    -   `page_id` numeric and `page_id = null` (string)
    -   Titles with leading special characters (`!`, `!!`, `(`)
    -   Titles with non-Latin (Arabic) script
    -   Bare (unquoted) titles vs. CSV-style wrapped/escaped titles containing a literal `"`, including the pure-quote title (`"\""` → `"`), mixed Arabic+quote titles, and quote-wrapped titles containing internal apostrophes
    -   `hourly_counts` containing unusual characters (backslash, brackets) — confirmed ignored safely without affecting parsing
    -   Two different title strings sharing the same `page_id` — confirmed both retained as separate aggregation entries, not merged
    -   Malformed lines (too few fields, non-numeric `daily_total`, empty line) — confirmed to raise a clear error rather than silently producing bad data
    -   Full-fixture aggregation test: sums `daily_total` per `(wiki, title)` across multiple lines/agents and checks against hand-computed expected totals
    -   Remaining item: extend the DB write path test so the aggregation + SQLite write produces a `PageView` table matching what the REST-based path currently produces, for a small fixture wiki (not yet covered — the current suite tests parsing/aggregation logic only, not the DB layer).

## ParsedPageview class

[src/dumps_parser/bz2_dump_parser.py](../src/py_port/dumps_parser/bz2_dump_parser.py)

```python
class MalformedLineError(ValueError):
    """Raised when a line does not have the minimum expected structure."""


@dataclass(frozen=True)
class ParsedPageview:
    wiki_code: str
    title: str
    page_id: str  # kept as-is (numeric string or "null"); unused downstream
    agent: str
    daily_total: int

    @staticmethod
    def unescape_title(raw_title: str) -> str:
        """Convert a raw dump title field into its true string form.

        The dump uses CSV-style conditional quoting: a title is wrapped in
        an outer, unescaped pair of double-quotes IF AND ONLY IF it contains
        a literal double-quote character; any literal " inside such a title
        is escaped as \". Titles without a " character are left bare with
        no wrapping at all.

        Examples (raw field -> true title):
            '!'                 -> '!'                  (no quote char, bare)
            '"\\""'             -> '"'                  (wrapped + escaped)
            '"\\"W\\"_x"'       -> '"W"_x'               (wrapped + escaped)
        """
        # Check if the raw_title is wrapped in double quotes
        if len(raw_title) >= 2 and raw_title.startswith('"') and raw_title.endswith('"'):
            # Extract the inner content by removing the outer quotes
            inner = raw_title[1:-1]
            # Replace escaped quotes (\") with actual quotes (")
            return inner.replace('\\"', '"')
        # If not wrapped in quotes, return as-is
        return raw_title


    @classmethod
    def parse(cls, line: str) -> "ParsedPageview":
        """Parse a single line of the pageview_complete dump.

        Raises MalformedLineError if the line doesn't have at least the
        5 fixed-position fields (wiki_code, title, page_id, agent, rest).
        """
        line = line.rstrip("\n")
        if not line:
            raise MalformedLineError("empty line")

        parts = line.split(" ", maxsplit=4)
        if len(parts) < 5:
            raise MalformedLineError(
                f"expected at least 5 space-separated fields, got {len(parts)}: {line!r}"
            )

        wiki_code, raw_title, page_id, agent, rest = parts

        # rest is "daily_total" or "daily_total hourly_counts"; we only need
        # the first token. hourly_counts (if present) is discarded untouched.
        daily_total_str = rest.split(" ", maxsplit=1)[0]
        try:
            daily_total = int(daily_total_str)
        except ValueError as exc:
            raise MalformedLineError(
                f"could not parse daily_total from {daily_total_str!r} in line: {line!r}"
            ) from exc

        title = cls.unescape_title(raw_title)

        return cls(
            wiki_code=wiki_code,
            title=title,
            page_id=page_id,
            agent=agent,
            daily_total=daily_total,
        )
```

## Open questions

-   Exact NFS path pattern — confirm by listing `/public/dumps/public/other/pageview_complete/monthly/` directly on Toolforge.
-   Timing: dumps for month M typically land a few days into month M+1 — does the existing `0 0 1 * *` cron need to shift later in the month?
-   Memory strategy: one full pass building per-wiki dicts for all configured wikis at once vs. one pass per wiki — trade-off between total I/O time and peak RAM.
-   Should the SQLite file be rebuilt from scratch each run, or upserted incrementally (matters if a partial/interrupted run needs to resume)?
-   Does the CSV-style quoting rule (bare title if no `"`, wrapped+escaped if it contains `"`) hold consistently across all wikis and the entire file, or are there other escape edge cases (e.g. literal backslashes in titles) not yet observed in the `ar.wikipedia` sample?

## References

-   Dump format docs: https://dumps.wikimedia.org/other/pageview_complete/readme.html
-   https://wikitech.wikimedia.org/wiki/Data_Platform/Data_Lake/Traffic/Pageviews
-   Local Toolforge path: `/public/dumps/public/other/pageview_complete/`
-   Existing cache model: `PageView` (`title: str` PK, `views: int`) in `pageviews_models.py`
-   Reference parser + test suite (validated against real sample data): `bz2_dump_parser.py`, `test_bz2_dump_parser.py`, `fixtures/ar_wikipedia_sample.txt`
