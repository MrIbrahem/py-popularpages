# Popular Pages — PHP → Python Migration Analysis

**Author:** Code-level review (Pi)
**Date:** 2025
**Subject repos:**

-   Python (current, under analysis): `/root/codes/py-popularpages` (this project)
-   PHP (original): `https://github.com/MrIbrahem/popularpages` (cloned to `/root/codes/popularpages-php`)

**Method.** Both repositories were cloned/checked out and read file‑by‑file at the source level, not just the README. The Python test suite was installed and executed (`pytest`) to confirm real behavior. Targeted micro‑experiments were run to confirm URL‑encoding and PyMySQL binary‑column behavior. Where a claim could not be confirmed at runtime (live wiki/DB needed), it is explicitly labeled **inference** or **unverified**.

---

## 0. Summary of findings (read this first)

The Python project is a **faithful, near‑complete 1:1 port** of the PHP codebase. Every PHP class/function/script has a Python counterpart with the same control flow, the same SQL, the same templates, and the same config files (the `messages/*.json` and `config/wikis.yaml` files are byte‑identical between the two repos).

However, several **subtle behavioral differences** exist that are not visible from filenames alone. The most important, confirmed by execution, are:

1. **Confirmed bug — Pageviews API URL is not percent‑encoded** (`src/src_py/popularpages/pageviews_repository.py`, `get_pageviews`/`_get`). Titles containing `&`, `/`, `?`, `#`, `+`, `%`, etc. produce a malformed request and silently yield **0 pageviews** instead of the real count. PHP's `PageviewsRepository::get()` calls `rawurlencode($article)`.
2. **High‑confidence risk — Wikimedia replica DB columns are `BINARY`/`VARBINARY`**, which **PyMySQL returns as `bytes`**, whereas PHP's `mysqli` returns them as `str`. This breaks `get_stale_projects()` and `update_index()` (hard crash in `strptime`) and corrupts page titles in the pageviews URL (silent wrong results). The MySQL tests that would catch this are skipped.
3. **Test suite does not pass cleanly** in a fresh checkout: 1 assertion failure (`test_process_response_no_items_returns_none`) and 1 setup error (`test_set_text` is not gated to require credentials, so it errors on missing `.env`). The GitHub Actions CI runs `pytest --cov` and would go red.
4. **Section‑edit parameter differs**: PHP passes the boolean `true` (→ likely `section=1`), Python passes the string `"0"` (lead section). Intent is the lead (section 0), so Python is arguably "more correct," but it is a behavioral divergence.
5. **`check_reports` runs ALL wikis by default**; PHP `checkReports.php` only ever processes the single wiki passed as an argument.
6. **Timezone handling is inconsistent in the Python port** (`date.today()` local vs `datetime.now(timezone.utc)` for the stale cutoff), whereas PHP forces UTC in every entry point.
7. **Retry/backoff differs**: PHP uses Guzzle retry middleware (retries 429/503 _and_ timeouts, and honors `Retry-After`); Python `tenacity` retries only 429/503, no `Retry-After`, max 5 attempts. Concurrency model also differs (burst vs staggered dispatch).

Net assessment: **~85–92% functionally ported, but NOT yet proven functionally equivalent**, primarily because of the binary‑column DB risk (could be a runtime blocker) and the URL‑encoding bug (silent data corruption for special‑character titles).

---

## 1. Migration / Implementation Status

Status legend: **Fully implemented**, **Partially implemented**, **Not implemented**, **Implemented differently**, **Unable to verify**.

| Component                         | PHP location                                          | Python location                                | Status                                | Reasoning                                                                                                                                                                                                                                                                                                                                                                                                                   |
| --------------------------------- | ----------------------------------------------------- | ---------------------------------------------- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Logging (`wfLogToFile`)           | `src/Logger.php`                                      | `src/src_py/popularpages/logger.py` (`log_to_file`)   | Fully implemented                     | Same filename format `logs/log-{wiki}.txt`, same `YYYY-MM-DD HH:MM:SS` + `  ` + message. See §3 for timezone nuance.                                                                                                                                                                                                                                                                                                        |
| WikiRepository (API + DB)         | `src/WikiRepository.php`                              | `src/src_py/popularpages/wiki_repository.py`          | Implemented differently / **Partial** | All public methods ported (login, does_title_exist, has_lead_section, get_project_pages, get_monthly_pageviews_and_assessments, set_text, get_json_config, get_stale_projects, get_projects_with_last_bot_timestamp, get_project, get_bot_last_edit_date, get_assessment_config). SQL identical. **But** mwclient replaces hand‑rolled `apiQuery()`, and the PyMySQL `bytes` issue (§3) likely breaks two paths at runtime. |
| PageviewsRepository (REST client) | `src/PageviewsRepository.php`                         | `src/src_py/popularpages/pageviews_repository.py`     | **Partially implemented**             | Batches, sums, redirects, 404 handling, 3s timeouts ported. **But** confirmed missing URL‑encoding (§3) and narrower retry config.                                                                                                                                                                                                                                                                                          |
| ReportUpdater (orchestration)     | `src/ReportUpdater.php`                               | `src/src_py/popularpages/report_updater.py`           | Fully implemented                     | `update_reports`, `process_project`, `update_index`, `validate_project_config`, Twig helpers all ported to Jinja2. See §3 for date/tz/section nuances.                                                                                                                                                                                                                                                                      |
| CLI: checkReports                 | `bin/checkReports.php`                                | `src/src_py/popularpages/cli/check_reports.py`        | Implemented differently               | Same flow; **adds a multi‑wiki mode** (runs all wikis if `--wiki` omitted). PHP only ever runs one wiki.                                                                                                                                                                                                                                                                                                                    |
| CLI: generateReport               | `bin/generateReport.php`                              | `src/src_py/popularpages/cli/generate_report.py`      | Implemented differently               | Same behavior; uses `argparse` with named `--wiki`/`--dry-run` and a positional `project` (PHP used positional argv[1..3]).                                                                                                                                                                                                                                                                                                 |
| CLI: generateIndex                | `bin/generateIndex.php`                               | `src/src_py/popularpages/cli/generate_index.py`       | Fully implemented                     | Same one‑arg behavior.                                                                                                                                                                                                                                                                                                                                                                                                      |
| i18n (Intuition)                  | `krinkle/intuition`                                   | `src/src_py/popularpages/i18n.py` (`I18n`)            | Fully implemented                     | Custom `$1/$2` substitution reading the **identical** `messages/*.json`. Arabic + English present and byte‑identical to PHP.                                                                                                                                                                                                                                                                                                |
| Report/index templates            | `views/*.twig`                                        | `views/*.jinja`                                | Fully implemented                     | `{% verbatim %}`→`{% raw %}`, `replace({' ':'_'})`→`replace(' ','_')`, Twig `date('Y-m-d')`→custom `_format_date`. Output is equivalent.                                                                                                                                                                                                                                                                                    |
| Config: wikis.yaml                | `config/wikis.yaml`                                   | `config/wikis.yaml`                            | Fully implemented                     | **Byte‑identical** between repos.                                                                                                                                                                                                                                                                                                                                                                                           |
| Config: .env                        | `config.ini.example`                                  | `.env.example`                                 | Implemented differently               | Credentials now sourced from environment variables via python-dotenv (`.env.example` is the template, copied to `.env`). No INI parsing/quote‑stripping; `dbport` defaults to `3306`.                                                                                                                                                                                                                    |
| Assessment config fetch           | `WikiRepository::getAssessmentConfig`                 | `wiki_repository.get_assessment_config`        | Fully implemented                     | Same XTools endpoint, same caching.                                                                                                                                                                                                                                                                                                                                                                                         |
| Tests                             | `tests/WikiRepositoryTest.php` (PHPUnit, mostly live) | `tests/test_*.py` (pytest + mocks)             | **Partially implemented**             | More granular unit tests added, but the suite fails in a clean checkout (1 fail + 1 error). Two live tests kept as skipped.                                                                                                                                                                                                                                                                                                 |
| CI                                | `.github/workflows/php.yml` (phpcs only)              | `.github/workflows/python.yml` (ruff + pytest) | Implemented differently               | Python CI actually runs tests; PHP CI only lints.                                                                                                                                                                                                                                                                                                                                                                           |
| Packaging/tooling                 | `composer.json`/`composer.lock`/`phpcs.xml`           | `pyproject.toml`/`ruff`                        | Implemented differently               | Direct dependency‑by‑dependency mapping documented in README.                                                                                                                                                                                                                                                                                                                                                               |
| Deployment/runtime                | cron per wiki                                         | cron (single‑wiki) or new multi‑wiki mode      | Implemented differently               | See §3.                                                                                                                                                                                                                                                                                                                                                                                                                     |

---

## 2. Detailed Differences

### 2.1 Architecture & project structure

-   PHP: flat `src/*.php` + global helper `wfLogToFile()`; entry points in `bin/`.
-   Python: package `src/src_py/popularpages/` with modules; entry points in `src/src_py/popularpages/cli/` exposed as console scripts `popularpages-check|report|index` (`pyproject.toml` `[project.scripts]`). A new `utils.py` holds helpers that were inline in PHP (`uc_first`, `previous_month_range`, timestamp converters). The migration‑plan doc (`popularpages-php-to-python-migration-plan.md`, 28KB) is **added** in the Python repo and has no PHP counterpart.

### 2.2 Business logic

-   Previous‑month range: PHP `strtotime('first day of previous month')`/`('last day of previous month')`; Python `previous_month_range(date.today())` (`utils.py`) returns `date` objects. Verified equivalent for mid‑year and year‑boundary (`tests/test_utils.py`, `test_previous_month_range_*`).
-   Daily average: PHP `floor(pageviews / daysInMonth)`; Python `pageviews // daysInMonth`. Equal for non‑negative counts.
-   Sorting/limit: PHP `uasort` descending + `array_slice`; Python `sorted(..., reverse=True)[:limit]`. Equivalent; PHP <8 sort was not stable (negligible for ties).
-   1,000,000‑row guard (T164178): present and identical in both.
-   Batch threshold of 60: preserved (`config.py::BATCH_SIZE_THRESHOLD`, comment preserved).

### 2.3 Database schema & operations

-   All four SQL statements are **copied verbatim** (escaping the namespace `= 4` assumptions and the `revision_userindex`/`actor` joins). Queries: `get_project_pages`, `get_projects_with_last_bot_timestamp`, plus the stale‑project filter logic.
-   **Driver change (critical):** PHP `mysqli` returns `BINARY(14)`/`VARBINARY(255)` columns (e.g. `rev_timestamp`, `page_title`, `redir_title`) as **strings**; Python **PyMySQL returns them as `bytes`** by default. This is the single biggest DB‑behavior difference. Effects:
    -   `get_stale_projects()` → `mediawiki_timestamp_to_epoch(row["rev_timestamp"])` calls `datetime.strptime(bytes, …)` → **`TypeError` (hard crash)**.
    -   `update_index()` → `datetime.strptime(str(rev_date), …)` where `str(b'...')` is `"b'...'"` → **`ValueError` (hard crash)**.
    -   `get_monthly_pageviews_and_assessments()` → `row["page_title"]` is `bytes`; building the pageviews URL via f‑string yields `/metrics/user/b'Foo_Bar'/monthly` → **malformed URL → 404 → 0 pageviews (silent wrong result)**.
    -   PHP `getProjectsWithLastBotTimestamp` uses `fetch_all(MYSQLI_ASSOC)`; Python uses `pymysql.cursors.DictCursor` — equivalent shape.

### 2.4 APIs & external integrations

-   **MediaWiki Action API:** PHP `addwiki/mediawiki-api-base` (`FluentRequest` + `apiQuery` wrapper with a single retry‑on‑exception). Python uses **`mwclient`** (`Site.api`, `Site.login`, `page.edit`). mwclient manages tokens and transparent re‑login; the PHP "log exception and retry once" wrapper is not directly replicated (mwclient has its own internal retries).
-   **Pageviews REST API:** PHP `GuzzleHttp` async promises (`Utils::settle`); Python `httpx.AsyncClient` + `asyncio.gather`. Endpoint, path layout, timeouts (3s/3s), and 500ms inter‑request delay are preserved **in value** but the **encoding and pacing model differ** (see §3).
-   **XTools assessments API:** identical endpoint/usage; Python has a 10s `httpx` timeout, PHP uses Guzzle defaults.

### 2.5 Authentication / authorization

-   Both log in with a BotPassword (`botuser`/`botpass`). Python uses `mwclient.Site.login`; PHP uses `ApiUser` + `MediawikiApi::login`. Both set `bot=true` on edits. Python additionally retries once on `LoginError` (PHP re‑logs in inside `setText` only if `!isLoggedin()`).

### 2.6 Background jobs & scheduled tasks

-   No cron/scheduler code in either repo; both rely on external cron (`0 0 1 * *`). PHP README documents one cron per wiki; Python adds a single‑invocation **multi‑wiki** mode. Rate‑limit behavior differs (see §3).

### 2.7 Web routes / endpoints

-   Neither project serves HTTP endpoints; both are CLI bots that _write_ to the wiki via the API. (The "index" and "report" outputs are wiki pages, not web routes.)

### 2.8 Data processing / parsing

-   JSON config parse + removal of the `description` key: identical.
-   Pageviews summing + article‑name normalization (`_`→space) on the **last** reversed item: identical (both iterate `reversed(items)` and overwrite `article`).
-   Redirect aggregation by target: identical (first‑match wins).

### 2.9 Caching

-   Assessment config is cached in‑memory per `WikiRepository` instance in both. No external cache (Redis/Memcached) in either.

### 2.10 Error handling

-   PHP `setText`/`apiQuery` swallow **all** exceptions to keep the batch run going. Python `set_text` catches `LoginError` (retry once) then a broad `Exception` → `result=None`. Equivalent resilience intent.
-   PHP `getPageviews` discards any failed promise; Python `fetch_one` catches `HTTPStatusError`/`HTTPError` and returns `None` per title. Equivalent.
-   **Difference:** PHP's `processResponse` returns `null` on empty `items`; calling `[$page,$count] = null` would emit a PHP warning. Python `_process_response` returns the **tuple `(None, None)`**, which the caller unpacks safely — so Python is _more_ robust here. (This also exposes a broken internal test — see §4.)

### 2.11 Logging

-   Same file/format. **Timezone difference:** PHP entry scripts call `date_default_timezone_set('UTC')`, so `date()` is UTC. Python `log_to_file` uses `datetime.now()` = **system local time** (no UTC pin). On a UTC host the output matches; otherwise log timestamps diverge from PHP.

### 2.12 Configuration & environment variables

-   Credentials are now read from **environment variables** via python-dotenv, with `.env.example` as the committed template (copied to `.env`, which is git‑ignored). The flat `creds` mapping (`botuser`, `botpass`, `dbhost`, `dbuser`, `dbpass`, `dbport`) is assembled in `config.load_credentials()`, so no INI parsing or quote‑stripping is needed. `dbport` defaults to `3306`.
-   Python adds `pyproject.toml` config (ruff/black/isort/pyright/coverage); PHP adds `phpcs.xml`.

### 2.13 Performance‑related behavior

-   **Request pacing:** PHP sets Guzzle `delay => 500` (Guzzle units are **seconds** per docs, though the PHP comment claims ms) and dispatches requests with that gap; Python sleeps `0.5s` inside each coroutine but `asyncio.gather` launches them near‑simultaneously, so requests **burst** ~0.5s after batch start rather than being staggered. This can hit the Pageviews API harder and trigger more 429/503s. Verify the actual effective `delay` — this is a unit/semantics ambiguity (see §3).
-   Async model: Python is `async` throughout pageviews; PHP uses promises. Functionally equivalent throughput, different shape.

### 2.14 Security

-   Both keep credentials out of version control (git‑ignored `.env` in Python, git‑ignored `config.ini` in PHP). Both use HTTPS endpoints.
-   Python `i18n.msg` does literal `$n` substring replacement — same XSS/ injection surface as Intuition (messages are trusted, static files). No user input reaches message keys.
-   No SQL injection: both use parameterized queries.
-   Minor: Python's `set_text` dry‑run `print`s a dict (safe); PHP `print_r`s params (safe).

### 2.15 Dependencies

| PHP                               | Python        |
| --------------------------------- | ------------- |
| addwiki/mediawiki-api-base        | mwclient      |
| krinkle/intuition                 | i18n.py       |
| twig/twig                         | Jinja2        |
| symfony/yaml                      | PyYAML        |
| ext-mysqli                        | PyMySQL       |
| caseyamcl/guzzle_retry_middleware | tenacity      |
| guzzlehttp/guzzle (async)         | httpx (async) |
| phpunit                           | pytest        |
| mediawiki-codesniffer             | ruff          |

### 2.16 CLI commands / scripts

-   PHP `checkReports.php en.wikipedia` → processes **one** wiki. Python `popularpages-check` (no arg) → processes **all** wikis in `wikis.yaml`; `--wiki X` scopes to one. This is the most user‑visible behavior change.
-   `generateReport.php en.wikipedia "Project"` → Python `popularpages-report --wiki en.wikipedia "Project"`.
-   `generateIndex.php en.wikipedia` → Python `popularpages-index --wiki en.wikipedia`.

### 2.17 Deployment / runtime

-   Both require credentials (`.env` in Python, `config.ini` in PHP) + external cron. Python installs via `pip install -e .` and exposes console scripts. PHP via `composer install` + `bin/*.php`. Python requires Python ≥3.10; PHP ≥8.1.

---

## 3. Important Things to Watch Out For

### 3.1 (CONFIRMED) Pageviews URL is not percent‑encoded

`pageviews_repository.py::get_pageviews` builds `article = title.replace(" ", "_")` and interpolates it into the URL **without encoding**. Verified: a title `"A & B/C"` becomes `…/user/A_&_B/C/monthly/…`, an invalid path, and the API returns no items → **pageviews counted as 0** (silently wrong). PHP `PageviewsRepository::get()` uses `rawurlencode($article)`. **Fix:** `urllib.parse.quote(article, safe="")` before interpolation. Priority **High**.

### 3.2 (HIGH‑CONFIDENCE INFERENCE, unverified against live DB) PyMySQL returns BINARY/VARBINARY as `bytes`

Confirmed by PyMySQL source/converters and by the MediaWiki schema (`page_title VARBINARY(255)`, `rev_timestamp BINARY(14)`). The PHP `mysqli` returns these as `str`. Consequences (demonstrated locally with `bytes` inputs):

-   `get_stale_projects()` → `TypeError` in `strptime`.
-   `update_index()` → `ValueError` (parses `"b'…'"`).
-   `get_monthly_pageviews_and_assessments()` → malformed title URL → wrong/zero pageviews.

The two tests that would catch this (`test_get_project_pages`, `test_get_monthly_pageviews`) are **skipped** (`@pytest.mark.skip`) — they were `er`-prefixed (disabled) in PHP too. **This is the top risk and could be a runtime blocker.** **Fix:** decode binary columns (`row["page_title"].decode("utf-8")` or set `use_unicode`/`charset` handling) at the cursor boundary in `_connect_db`/`get_project_pages`. Priority **Critical**.

### 3.3 (CONFIRMED) Test suite does not pass cleanly

`pytest` in a fresh checkout: `test_process_response_no_items_returns_none` **fails** (asserts `_process_response({}) is None`, but impl returns `(None, None)`), and `test_set_text` **errors** at setup because it is **not** wrapped in the `@requires_creds` skip that the other live tests use, and it has no `network` marker, so pytest‑socket blocks it. The CI job `pytest --cov=src` would go red. **Fix:** gate `test_set_text` with `@requires_creds`, and fix the `_process_response` test expectation (or return `None` to match PHP). Priority **Medium**.

### 3.4 (INFERENCE) Section‑edit parameter mismatch

PHP `setText(..., $section)` receives the boolean `$hasLeadSection` and does `if ($section) $params['section'] = $section;` → boolean `true`. MediaWiki `edit` `section` is an integer; a boolean `true` serializes (via `http_build_query` in the API client) to `section=1`, i.e. the **first numbered section**, not the lead. Python passes `section="0"` (the lead), matching the template's `{% if not hasLeadSection %}` header guard. **If** the PHP serialization is as inferred, PHP edits the wrong section; Python is the intended behavior. Either way it is a **behavioral divergence that must be verified** against a real edit. Priority **Medium/verify**.

### 3.5 (INFERENCE) Timezone inconsistency in the Python port

PHP forces UTC in every CLI entry point (`date_default_timezone_set('UTC')`), so `strtotime`/`date` are UTC. Python `previous_month_range` uses `date.today()` (local), while `first_of_this_month_timestamp` uses `datetime.now(timezone.utc)`. On a non‑UTC host these disagree, and the "previous month" could be computed against local date while the stale‑project cutoff is UTC. PHP is internally consistent (UTC). **Fix:** pin everything to UTC (`date.today()` → `datetime.now(timezone.utc).date()`). Priority **Medium**.

### 3.6 (CONFIRMED) Retry/backoff differs from PHP

PHP `GuzzleRetryMiddleware` retries on 429/503 **and** connection timeouts, and honors the server `Retry-After` header. Python `tenacity` retry (`_is_retryable`) triggers **only** on 429/503, ignores `Retry-After`, and stops after 5 attempts. A connection timeout (`httpx.TimeoutException`) is **not** retried. This is a reliability/availability regression under flaky networks. Priority **Medium**.

### 3.7 (INFERENCE) `delay` unit ambiguity (Guzzle seconds vs comment "ms")

PHP `REQUEST_DELAY = 500` is passed to Guzzle's `delay` request option, whose documented unit is **seconds** (implying 500s between requests — implausible for a working tool, so either Guzzle or the original code treats it otherwise). Python interprets it as `0.5s`. The effective pacing may differ substantially. **Verify** which cadence the production PHP bot actually uses and align Python. Priority **Low/verify**.

### 3.8 (CONFIRMED) Resource cleanup

`PageviewsRepository` creates an `httpx.AsyncClient` per report run but never calls `aclose()`. Over a multi‑wiki run many clients leak (unclosed connections). Minor, but add `async with`/cleanup. Priority **Low**.

### 3.9 Edge cases / defaults already matched

-   `Limit` truncation, `Unknown` assessment fallback, empty‑config abort, mainspace‑reject heuristic (no `:` in Report), project‑existence check, 1M guard, `description` key removal, namespace‑4 assumption (`FIXME` in both): all preserved.

### 3.10 PHP features easy to overlook during a rewrite

-   `getBotLastEditDate()` exists in **both** repos but is **dead code** (never called by any entry point). Ported faithfully but unused.
-   `apiQuery`'s retry‑once‑on‑exception wrapper is implicit in mwclient; ensure equivalent resilience if mwclient is ever swapped.
-   The PHP `getPageviews` 404‑as‑"omit" and "other error → omit" branches are both ported, but PHP's `reason->getCode()` only special‑cases 404; Python special‑cases 404 and logs other `HTTPStatusError`. Equivalent outcome; PHP's broad `continue` also silently drops truly unexpected errors (no log), whereas Python logs them. **Difference:** Python logs non‑404 HTTP errors (better observability).

---

## 4. What Is Still Missing (prioritized)

### Critical

1. **Binary‑column handling (PyMySQL `bytes`).** Decode `BINARY`/`VARBINARY` (`page_title`, `redir_title`, `rev_timestamp`) to `str` at the cursor boundary. Without this, `checkReports`/`generateIndex` crash and pageviews are silently wrong. _Where:_ `wiki_repository.py::_connect_db`/`get_project_pages`; `utils.py::mediawiki_timestamp_to_epoch`/`mediawiki_timestamp_to_date`; `report_updater.py::update_index`. _(Inference, high confidence; the only way to be 100% sure is a live DB run — but the relevant tests are skipped.)_

### High

2. **Percent‑encode article titles in the Pageviews URL.** _Where:_ `pageviews_repository.py::get_pageviews`/`_get`. Confirmed bug; fix with `urllib.parse.quote(..., safe="")`.
3. **Multi‑wiki semantics decision.** Decide whether `popularpages-check` (no `--wiki`) running _all_ wikis is intended. If not, default to requiring `--wiki` to match PHP; if yes, document it and ensure per‑wiki isolation/error containment. _Where:_ `cli/check_reports.py`.
4. **Make the test suite green in CI.** Gate `test_set_text` with `@requires_creds`; fix the `_process_response` test/impl mismatch. Currently CI `pytest --cov` fails. _Where:_ `tests/test_wiki_repository.py`, `tests/test_pageviews_repository.py`.

### Medium

5. **Pin all date math to UTC.** Replace `date.today()` with `datetime.now(timezone.utc).date()` for consistency with PHP and with `first_of_this_month_timestamp`. _Where:_ `utils.py::previous_month_range`, `report_updater.py`.
6. **Align retry/backoff with PHP.** Retry on timeouts, honor `Retry-After`, and confirm attempt counts. _Where:_ `pageviews_repository.py` (tenacity config).
7. **Verify & reconcile the `section` edit parameter** (lead vs first section) against a real edit; keep Python's explicit `"0"` if that matches intent. _Where:_ `wiki_repository.py::set_text`.
8. **Enable/author the skipped DB/API tests** or replace with offline fixtures (sqlite‑mirror of the relevant tables, recorded Pageviews responses) so the binary‑column and batching logic are actually exercised.

### Low

9. **Close the `httpx` client** (`aclose`) after each run. _Where:_ `report_updater.py`/`pageviews_repository.py`.
10. **Resolve `delay` unit** (§3.7) and document the chosen cadence.
11. **`dbport` example value** differs (`3306` vs `4711`); align or document intentionally.
12. **Dead code:** `get_bot_last_edit_date` (both repos) is unused — keep for API parity or remove deliberately.

---

## 5. Migration Coverage

Percentages are reasoned estimates, not precise measurements. Basis: every PHP source file/class/script has a Python counterpart; the gaps are concentrated in (a) correctness details (encoding, DB types, tz, retry) rather than missing features, and (b) test quality.

| Area                                                       | Estimate   | Basis                                                                                                 |
| ---------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------- |
| Core functionality (orchestration, report gen)             | **90–95%** | `ReportUpdater` faithfully ported; only date/tz/section nuances.                                      |
| API / backend functionality                                | **85–90%** | mwclient covers query/parse/edit; section param + retry/timeout gaps; binary‑timestamp crash risk.    |
| Database functionality                                     | **80–88%** | SQL copied verbatim (strong), but PyMySQL `bytes` handling likely breaks two paths + corrupts titles. |
| Background processing (batched pageviews/async)            | **85–90%** | Concurrency model changed (burst vs staggered); multi‑wiki added; encoding bug.                       |
| User‑facing functionality (wikitext, i18n, index/report)   | **95%**    | Templates/messages/config byte‑identical; output equivalent.                                          |
| Supporting / infra (CLI, tests, CI, config, logging, deps) | **85–90%** | CLI superset; CI runs tests; but suite fails clean + 1M‑guard/namespace assumptions unchanged.        |

**Overall migration completeness: ~85–92%.** A defensible single number is **~88%**. The remaining ~10–15% is _not_ missing features — it is correctness/robustness work (encoding, DB typing, timezone, retry, test greening). Because the binary‑column and URL‑encoding issues can silently corrupt or crash real runs, the project should **not** be called functionally equivalent until items 1–4 in §4 are resolved and a live end‑to‑end run succeeds.

---

## 6. Feature Mapping Table

| PHP Component / Feature                        | PHP Location                                        | Python Equivalent                            | Python Location           | Status                    | Differences / Missing Work                                                                                                          | Priority      |
| ---------------------------------------------- | --------------------------------------------------- | -------------------------------------------- | ------------------------- | ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| File logger                                    | `src/Logger.php` `wfLogToFile`                      | `log_to_file`                                | `logger.py`               | Fully implemented         | Uses local time, not forced UTC (PHP sets UTC in entry pts)                                                                         | Low           |
| Wiki API login                                 | `WikiRepository::login` (`addwiki`)                 | `WikiRepository.login` (`mwclient`)          | `wiki_repository.py`      | Implemented differently   | Library swap; re‑login handled by mwclient                                                                                          | Low           |
| `doesTitleExist`                               | `WikiRepository::doesTitleExist`                    | `does_title_exist`                           | `wiki_repository.py`      | Fully implemented         | —                                                                                                                                   | —             |
| `hasLeadSection`                               | `WikiRepository::hasLeadSection`                    | `has_lead_section`                           | `wiki_repository.py`      | Fully implemented         | —                                                                                                                                   | —             |
| Fetch project pages + assessments (SQL)        | `WikiRepository::getProjectPages`                   | `get_project_pages`                          | `wiki_repository.py`      | Implemented differently   | SQL identical; PyMySQL returns `bytes` for `page_title`/`redir_title` → corrupts later URL                                          | Critical      |
| Batch pageviews + assessments                  | `WikiRepository::getMonthlyPageviewsAndAssessments` | `get_monthly_pageviews_and_assessments`      | `wiki_repository.py`      | Implemented differently   | Same algo; `bytes` titles break URL; async wrapper                                                                                  | Critical      |
| Sort + truncate to Limit                       | `WikiRepository::sortAndTruncatePagesList`          | `_sort_and_truncate_pages_list`              | `wiki_repository.py`      | Fully implemented         | —                                                                                                                                   | —             |
| Edit wiki page (`setText`)                     | `WikiRepository::setText`                           | `set_text`                                   | `wiki_repository.py`      | Implemented differently   | Passes `section="0"` (PHP likely `section=1`); LoginError retry added                                                               | Medium/verify |
| Fetch on‑wiki JSON config                      | `WikiRepository::getJSONConfig`                     | `get_json_config`                            | `wiki_repository.py`      | Fully implemented         | Handles dict wikitext form                                                                                                          | —             |
| Stale‑project filtering                        | `WikiRepository::getStaleProjects`                  | `get_stale_projects`                         | `wiki_repository.py`      | Implemented differently   | `rev_timestamp` as `bytes` → `strptime` TypeError (crash)                                                                           | Critical      |
| Last bot edit timestamps (SQL)                 | `WikiRepository::getProjectsWithLastBotTimestamp`   | `get_projects_with_last_bot_timestamp`       | `wiki_repository.py`      | Implemented differently   | SQL identical; `bytes` `rev_timestamp` → crash in `update_index`                                                                    | Critical      |
| Single‑project lookup                          | `WikiRepository::getProject`                        | `get_project`                                | `wiki_repository.py`      | Fully implemented         | —                                                                                                                                   | —             |
| Last edit date of a page                       | `WikiRepository::getBotLastEditDate`                | `get_bot_last_edit_date`                     | `wiki_repository.py`      | Fully implemented         | **Dead code in both** (never called)                                                                                                | Low           |
| XTools assessments config                      | `WikiRepository::getAssessmentConfig`               | `get_assessment_config`                      | `wiki_repository.py`      | Fully implemented         | 10s httpx timeout (PHP uses Guzzle default)                                                                                         | Low           |
| Generic API wrapper + retry                    | `WikiRepository::apiQuery`                          | (replaced by `mwclient`)                     | `wiki_repository.py`      | Implemented differently   | PHP retries‑once‑on‑exception; mwclient internal retries                                                                            | Low           |
| Pageviews REST client                          | `PageviewsRepository`                               | `PageviewsRepository`                        | `pageviews_repository.py` | **Partially implemented** | **No URL encoding (confirmed → 0 pageviews for special chars)**; narrower retry (no timeout/Retry‑After); burst vs staggered pacing | High          |
| `processResponse`                              | `PageviewsRepository::processResponse`              | `_process_response`                          | `pageviews_repository.py` | Implemented differently   | Returns `(None, None)` tuple vs PHP `null` (more robust)                                                                            | Low           |
| Report orchestration                           | `ReportUpdater`                                     | `ReportUpdater`                              | `report_updater.py`       | Fully implemented         | Jinja2 replaces Twig; date/tz nuances                                                                                               | Medium        |
| `updateReports`                                | `ReportUpdater::updateReports`                      | `update_reports` (async)                     | `report_updater.py`       | Implemented differently   | Async; otherwise identical flow                                                                                                     | Low           |
| `processProject`                               | `ReportUpdater::processProject`                     | `process_project`                            | `report_updater.py`       | Fully implemented         | 1M guard, avg floor, template render all ported                                                                                     | —             |
| `updateIndex`                                  | `ReportUpdater::updateIndex`                        | `update_index`                               | `report_updater.py`       | Implemented differently   | `rev_timestamp` `bytes` → `strptime` ValueError (crash)                                                                             | Critical      |
| `validateProjectConfig`                        | `ReportUpdater::validateProjectConfig`              | `validate_project_config`                    | `report_updater.py`       | Fully implemented         | Mainspace‑reject heuristic preserved                                                                                                | —             |
| Twig helpers (`msg`, `assessments`, `ucfirst`) | `ReportUpdater::addTwigFunctions`                   | `report_updater._register_template_helpers`  | `report_updater.py`       | Fully implemented         | `ucfirst` keeps rest intact (matches PHP/Twig)                                                                                      | —             |
| Report template                                | `views/report.wikitext.twig`                        | `views/report.wikitext.jinja`                | `views/`                  | Fully implemented         | Byte‑equivalent output                                                                                                              | —             |
| Index template                                 | `views/index.wikitext.twig`                         | `views/index.wikitext.jinja`                 | `views/`                  | Fully implemented         | Byte‑equivalent output                                                                                                              | —             |
| i18n (messages)                                | `krinkle/intuition` + `messages/*.json`             | `I18n` + `messages/*.json`                   | `i18n.py`, `messages/`    | Fully implemented         | `messages/*.json` byte‑identical to PHP; `$1/$2` substitution                                                                       | —             |
| CLI: checkReports                              | `bin/checkReports.php`                              | `cli/check_reports.py`                       | `cli/`                    | Implemented differently   | **Runs ALL wikis by default** (PHP: one wiki only)                                                                                  | High          |
| CLI: generateReport                            | `bin/generateReport.php`                            | `cli/generate_report.py`                     | `cli/`                    | Implemented differently   | argparse named args; same behavior                                                                                                  | Low           |
| CLI: generateIndex                             | `bin/generateIndex.php`                             | `cli/generate_index.py`                      | `cli/`                    | Fully implemented         | —                                                                                                                                   | —             |
| Wiki config (wikis.yaml)                       | `config/wikis.yaml`                                 | `config/wikis.yaml`                          | `config/`                 | Fully implemented         | Byte‑identical                                                                                                                      | —             |
| Bot credentials config                         | `config.ini.example`                                | `.env.example`                              | `config/`                 | Implemented differently   | Now read from environment variables via python-dotenv; `dbport` defaults to `3306`                                                   | Low           |
| Unit/integration tests                         | `tests/WikiRepositoryTest.php` (PHPUnit)            | `tests/test_*.py` (pytest)                   | `tests/`                  | **Partially implemented** | Clean checkout: 1 fail + 1 error; 2 live tests skipped                                                                              | Medium        |
| CI pipeline                                    | `.github/workflows/php.yml` (phpcs)                 | `.github/workflows/python.yml` (ruff+pytest) | `.github/workflows/`      | Implemented differently   | Python CI runs tests (would currently fail)                                                                                         | Medium        |
| Packaging/lint                                 | `composer.json`/`phpcs.xml`                         | `pyproject.toml`/ruff                        | repo root                 | Implemented differently   | 1:1 dependency mapping                                                                                                              | —             |

---

## Final Assessment

1. **What has been successfully achieved**
   A remarkably complete and structurally faithful port. Every PHP class, method, CLI script, template, and config file has a Python equivalent with identical control flow and (in the DB/SQL and template cases) frequently byte‑identical content. The `messages/*.json` and `config/wikis.yaml` are unchanged. New value was added: a proper `argparse` CLI, a real unit‑test suite with mocked Pageviews/DB paths, and a CI that actually runs tests.

2. **The most significant differences between the implementations**

    - **DB driver typing:** mysqli (strings) vs PyMySQL (`bytes` for BINARY/VARBINARY) — the highest‑impact difference.
    - **Pageviews URL encoding:** absent in Python (confirmed silent 0‑counts for special‑character titles); present in PHP via `rawurlencode`.
    - **Concurrency/retry model:** Python `asyncio.gather` burst + `tenacity` (429/503 only, no `Retry-After`) vs PHP Guzzle promises + retry middleware (429/503 + timeouts + `Retry-After`).
    - **CLI scope:** Python `check` runs all wikis by default; PHP runs exactly one.
    - **Timezone:** Python mixes local `date.today()` and UTC cutoff; PHP is uniformly UTC.
    - **Edit section param:** Python `"0"` (lead) vs likely PHP `1`.

3. **The biggest risks in the current rewrite**

    - The PyMySQL `bytes` issue can **crash** `checkReports`/`generateIndex` and **silently corrupt** pageview counts (Critical, §3.2/§4.1).
    - The URL‑encoding bug **silently undercounts** popular pages for any title with `&`, `/`, `?`, `#`, `+`, `%` (High, §3.1).
    - The test suite is **red in CI** (§3.3), so regressions can ship unnoticed.
    - The `section`/timezone/retry nuances (§3.4–3.7) can cause subtly wrong edits or throttling without any error.

4. **The most important missing functionality**

    - Correct handling of `BINARY`/`VARBINARY` columns (decode to `str`) — blocks correct runtime behavior.
    - Percent‑encoding of article titles before building the Pageviews URL.
    - A green, meaningful test suite (gate `test_set_text`; fix `_process_response` expectation; add offline DB/Pageviews fixtures so the skipped paths are actually covered).
    - A decision + documentation on multi‑wiki `check` semantics.

5. **Recommended implementation order**

    1. **Fix binary‑column decoding** (Critical) — smallest change, unblocks real runs.
    2. **Fix URL percent‑encoding** in `pageviews_repository` (High, confirmed).
    3. **Make tests green + add offline fixtures** for the currently‑skipped DB/Pageviews paths (Medium, but protects everything else).
    4. **Pin UTC** everywhere in date logic (Medium).
    5. **Align retry/backoff + pacing** with PHP (Medium).
    6. **Verify the `section` edit param** against a live edit; keep `"0"` if that is the intended lead‑section behavior (Medium/verify).
    7. **Decide/document multi‑wiki `check`** default (High, product decision).
    8. **Cleanups:** close `httpx` client, resolve `delay` unit, align `dbport` example, remove/keep dead `get_bot_last_edit_date` (Low).

6. **Overall assessment — is the Python project functionally equivalent to the PHP project?**
   **Not yet.** Structurally and feature‑wise it is ~88% there and a near‑mirror of the original, but **three concrete issues prevent equivalence claims**: (a) a confirmed URL‑encoding bug that silently miscounts pageviews, (b) a high‑confidence PyMySQL binary‑typing problem that would crash or corrupt real runs (and is exactly the kind of DB‑behavior difference called out in the brief), and (c) a test suite that fails in CI. Once items 1–4 of §4 are addressed and a live end‑to‑end run (one wiki, one project) is demonstrated to produce byte‑equivalent wikitext, the port can be considered functionally equivalent. Until then it should be treated as a **faithful but not yet verified** rewrite.

---

### Evidence notes

-   **Confirmed by execution:** URL‑encoding behavior (`pageviews_repository` produced `…/user/A_&_B/C/…` → 0 views); `pytest` result (1 failed `test_process_response_no_items_returns_none`, 1 errored `test_set_text`); PyMySQL `bytes` interactions (`strptime(bytes)` → `TypeError`; `strptime(str(bytes))` → `ValueError`; f‑string with `bytes` → `b'…'` URL); `messages/*.json` and `config/wikis.yaml` are byte‑identical between repos (diff).
-   **Inference (high confidence, needs live verification):** PyMySQL returning `bytes` for `BINARY`/`VARBINARY` columns and the resulting runtime failures; Guzzle `delay` unit semantics; the PHP `section=true` → `section=1` serialization. These are based on documented library behavior and the MediaWiki schema, not on a live Toolforge run (no credentials/DB available in this environment).
-   **Unable to verify:** Live MediaWiki API + replica‑DB end‑to‑end behavior, real edit `section` outcome, and actual production request cadence, because they require credentials/network access not present here.
