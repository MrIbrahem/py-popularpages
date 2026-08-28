# Popular Pages: PHP → Python Migration Plan

**Chosen MediaWiki client library: `mwclient`**

---

## 1. Project Overview

Popular Pages is a bot that generates monthly "most popular pages" reports for
WikiProjects. It:

1. Reads WikiProject configuration from an on-wiki JSON page.
2. Queries the Wikimedia replica database to get a project's member pages and
   their assessment (class/importance).
3. Fetches monthly pageview stats from the Wikimedia Pageviews REST API.
4. Renders wikitext via a template engine (Twig) and saves it back to the wiki
   through the MediaWiki Action API.
5. Updates an index page summarizing all configured WikiProjects.

---

## 2. Library Mapping

| PHP dependency                      | Purpose                           | Python replacement                                                                                                                         |
| ----------------------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `addwiki/mediawiki-api-base`        | MediaWiki API client, login, edit | **`mwclient`**                                                                                                                             |
| `krinkle/intuition`                 | i18n / message translation        | Custom `I18n` class reading the existing `messages/*.json` files                                                                           |
| `twig/twig`                         | Templating                        | `Jinja2`                                                                                                                                   |
| `symfony/yaml`                      | YAML parsing                      | `PyYAML`                                                                                                                                   |
| `ext-mysqli`                        | Replica DB access                 | `PyMySQL`                                                                                                                                  |
| `caseyamcl/guzzle_retry_middleware` | Retry w/ backoff                  | `tenacity`                                                                                                                                 |
| Guzzle async/promises               | Concurrent pageview requests      | `mwclient` handles the wiki API synchronously; pageviews requests to the separate REST API will use `httpx.AsyncClient` + `asyncio.gather` |
| `phpunit`                           | Testing                           | `pytest`                                                                                                                                   |
| `composer`                          | Dependency management             | `pyproject.toml` + `pip`/`uv`                                                                                                              |
| `mediawiki-codesniffer` (phpcs)     | Linting                           | `ruff`                                                                                                                                     |
| GitHub Actions `php.yml`            | CI                                | `python.yml`                                                                                                                               |

**Why `mwclient`:** it wraps login, tokens, `action=edit`, `action=parse`, and
generic `action=query` calls behind a clean `Site` object, removing the need
to hand-roll `FluentRequest`-style parameter building and token fetching that
`WikiRepository::apiQuery()` / `setText()` currently do manually. It also
manages session/cookie state and re-login transparently, which simplifies the
`isLoggedin()` check currently in `setText()`.

---

## 3. New Project Structure

```
popularpages-py/
├── pyproject.toml
├── README.md
├── LICENSE
├── .env.example
├── config/
│   └── wikis.yaml
├── messages/
│   ├── ar.json
│   └── en.json
├── views/
│   ├── index.wikitext.jinja
│   └── report.wikitext.jinja
├── src/
│   └── cli/
│       ├── check_reports.py     # bin/checkReports.php
│       ├── generate_report.py   # bin/generateReport.php
│       └── generate_index.py    # bin/generateIndex.php
│   └── popularpages/
│       ├── __init__.py
│       ├── logger.py                # Logger.php
│       ├── i18n.py                  # Intuition replacement
│       ├── pageviews_repository.py  # PageviewsRepository.php
│       ├── wiki_repository.py       # WikiRepository.php (mwclient-based)
│       ├── report_updater.py        # ReportUpdater.php
├── tests/
│   └── test_wiki_repository.py
├── logs/
│   └── .gitkeep
└── .github/
    └── workflows/
        └── python.yml
```

---

## 4. Dependencies (`pyproject.toml`)

```toml
[project]
name = "popularpages"
requires-python = ">=3.10"
dependencies = [
    "mwclient>=0.10.1",
    "Jinja2>=3.1",
    "PyYAML>=6.0",
    "PyMySQL>=1.1",
    "tenacity>=8.2",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov",
    "pytest-asyncio",
    "ruff",
]

[project.scripts]
popularpages-check = "popularpages.cli.check_reports:main"
popularpages-report = "popularpages.cli.generate_report:main"
popularpages-index = "popularpages.cli.generate_index:main"
```

---

## 5. Module-by-Module Plan

### 5.1 `logger.py` (from `src/Logger.php`)

-   Reproduce `wfLogToFile($message, $wiki)` as `log_to_file(message: str, wiki: str) -> None`.
-   Write to `logs/log-{wiki}.txt`, append mode, one line per call, timestamp
    format `Y-m-d H:i:s` → Python `datetime.now().strftime('%Y-%m-%d %H:%M:%S')`.
-   Keep the manual open/append/close style (or wrap in Python's `logging`
    module with a per-wiki `FileHandler`, formatted to match exactly) — either
    is fine as long as output format is unchanged, since these logs may be
    parsed by humans on Toolforge.

```python
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent.parent / "logs"

def log_to_file(message: str, wiki: str) -> None:
    log_path = LOG_DIR / f"log-{wiki}.txt"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"{timestamp}  {message}\n")
```

### 5.2 `i18n.py` (Intuition replacement)

`Intuition::msg($key, ['domain' => ..., 'variables' => [...]])` does:

-   Loads `messages/{lang}.json`.
-   Falls back to English for missing keys.
-   Substitutes `$1`, `$2`, ... placeholders with positional variables.

Python equivalent — **do not modify the existing `messages/*.json` files**:

```python
import json
import re
from pathlib import Path

MESSAGES_DIR = Path(__file__).parent.parent.parent / "messages"

class I18n:
    def __init__(self, lang: str = "en"):
        self.lang = lang
        self._cache: dict[str, dict] = {}

    def _load(self, lang: str) -> dict:
        if lang not in self._cache:
            path = MESSAGES_DIR / f"{lang}.json"
            with path.open(encoding="utf-8") as f:
                self._cache[lang] = json.load(f)
        return self._cache[lang]

    def msg(self, key: str, variables: list[str] | None = None) -> str:
        variables = variables or []
        messages = self._load(self.lang)
        text = messages.get(key)
        if text is None:
            text = self._load("en").get(key, key)
        for i, value in enumerate(variables, start=1):
            text = text.replace(f"${i}", str(value))
        return text
```

### 5.3 `pageviews_repository.py` (from `src/PageviewsRepository.php`)

-   Uses `httpx.AsyncClient` for the Pageviews REST API (this API is separate
    from the wiki action API, so `mwclient` doesn't apply here).
-   Retry policy on 429/503 with exponential backoff via `tenacity`:

```python
import asyncio
import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
from .logger import log_to_file

ENDPOINT = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
REQUEST_DELAY_SECONDS = 0.5  # matches PHP's REQUEST_DELAY = 500ms

def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 503)

class PageviewsRepository:
    def __init__(self, domain: str):
        self.domain = domain
        self._client = httpx.AsyncClient(timeout=3.0)

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
    )
    async def _get(self, article: str, start: str, end: str) -> httpx.Response:
        url = f"{ENDPOINT}/{self.domain}/all-access/user/{article}/monthly/{start}/{end}"
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp

    async def get_pageviews(self, batch: dict[str, list[str]], start: str, end: str) -> dict[str, int]:
        target_titles = list(batch.keys())
        pageviews = {t: 0 for t in target_titles}

        # Unique set of all titles (targets + redirects) across the batch.
        all_titles = set()
        for titles in batch.values():
            all_titles.update(titles)

        async def fetch_one(title: str):
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
            try:
                resp = await self._get(title.replace(" ", "_"), start, end)
                return self._process_response(resp.json())
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return None  # no data available; acceptable to skip
                log_to_file(f"Exception during pageviews request: {exc}", self.domain)
                return None

        results = await asyncio.gather(*(fetch_one(t) for t in all_titles))

        for result in results:
            if result is None:
                continue
            page, count = result
            for target in target_titles:
                if page in batch[target]:
                    pageviews[target] += count
                    break

        return pageviews

    def _process_response(self, response: dict) -> tuple[str, int] | None:
        items = response.get("items")
        if not items:
            return None
        article = None
        total = 0
        for item in items:
            total += int(item["views"])
            article = item["article"].replace("_", " ")
        return article, total
```

Note: PHP fires all promises at once but Guzzle's client-level `delay` option
staggers dispatch; the `asyncio.sleep` inside each coroutine before firing its
request approximates the same pacing without needing a custom scheduler.

### 5.4 `wiki_repository.py` (from `src/WikiRepository.php`, using `mwclient`)

This is the module most reshaped by switching to `mwclient`.

**Construction / login:**

```python
from pathlib import Path
from dotenv import load_dotenv
import mwclient
import pymysql
import yaml

from .logger import log_to_file
from .i18n import I18n
from .pageviews_repository import PageviewsRepository

BASE_DIR = Path(__file__).parent.parent.parent

class WikiRepository:
    def __init__(self, wiki: str = "en.wikipedia", dry_run: bool = False):
        self.wiki = wiki
        self.dry_run = dry_run
        self.creds = self._load_credentials()
        self.wiki_config = yaml.safe_load(
            (BASE_DIR / "config" / "wikis.yaml").read_text()
        )[wiki]

        lang = wiki.split(".")[0]
        self.i18n = I18n(lang)
        self.pageviews_repo = PageviewsRepository(wiki)

        host = f"{wiki}.org"
        self.site = mwclient.Site(host, path="/w/")
        self.username = self.creds["botuser"].split("@")[0]
        self.login()

    def login(self) -> None:
        self.site.login(self.creds["botuser"], self.creds["botpass"])
```

**`doesTitleExist` / `hasLeadSection`:**

```python
    def does_title_exist(self, title: str) -> bool:
        result = self.site.api("query", titles=title, formatversion=2)
        for page in result["query"]["pages"]:
            if "missing" in page or "invalid" in page:
                return False
        return True

    def has_lead_section(self, title: str) -> bool:
        if not self.does_title_exist(title):
            return False
        result = self.site.api("parse", page=title, prop="sections", formatversion=2)
        return bool(result.get("parse", {}).get("sections"))
```

`mwclient.Site.api()` is the direct equivalent of `WikiRepository::apiQuery()`
— it accepts arbitrary action-API parameters as kwargs and returns the parsed
JSON. This removes the need for a bespoke `apiQuery()` wrapper and its manual
retry/exception handling (mwclient raises `mwclient.errors.APIError` and
handles maxlag/retry internally to a good extent, though we should still wrap
calls needing custom retry — see §7 below).

**Database access (unchanged logic, `PyMySQL` instead of `mysqli`):**

```python
    def _connect_db(self):
        db_name = self.wiki_config["database"].removesuffix("_p")
        host = self.creds["dbhost"].replace("*", db_name)
        return pymysql.connect(
            host=host,
            user=self.creds["dbuser"],
            password=self.creds["dbpass"],
            database=f"{db_name}_p",
            port=int(self.creds["dbport"]),
            cursorclass=pymysql.cursors.DictCursor,
        )

    def get_project_pages(self, project: str) -> list[dict]:
        log_to_file(f"Fetching pages and assessments for project {project}", self.wiki)
        conn = self._connect_db()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT page_title, pa_class, pa_importance, (
                        SELECT rp.page_title
                        FROM page rp
                        WHERE rd_from = page_id
                        AND rp.page_namespace = 0
                    ) AS redir_title
                    FROM page
                    JOIN page_assessments ON page_id = pa_page_id
                    LEFT OUTER JOIN redirect ON rd_title = page_title AND rd_namespace = 0
                    WHERE pa_project_id = (
                        SELECT pap_project_id
                        FROM page_assessments_projects
                        WHERE pap_project_title = %s
                    )
                    AND page_namespace = 0
                    """,
                    (project,),
                )
                return cursor.fetchall()
        finally:
            conn.close()
```

**`getMonthlyPageviewsAndAssessments` — batching logic preserved exactly:**

The PHP batches every 60 target pages (comment explains this is a deliberate
approximation, not a hard API limit) before calling
`PageviewsRepository::getPageviews()`, to bound memory and stay close to the
API's rate limit. Port this 1:1:

```python
    async def get_monthly_pageviews_and_assessments(
        self, rows: list[dict], start: str, end: str, limit: int
    ) -> tuple[dict, int]:
        log_to_file("Fetching monthly pageviews", self.wiki)

        out: dict[str, dict] = {}
        batch: dict[str, list[str]] = {}
        batch_count = 0
        total_pageviews = 0
        num_results = len(rows)

        for index, row in enumerate(rows, start=1):
            target = row["page_title"].replace("_", " ")
            redir = (row["redir_title"] or "").replace("_", " ")

            if target not in out:
                unknown_msg = self.i18n.msg("unknown")
                out[target] = {
                    "pageviews": 0,
                    "class": row["pa_class"] or unknown_msg,
                    "importance": row["pa_importance"] or unknown_msg,
                }

            if target not in batch:
                batch[target] = [target, redir]
            else:
                batch[target].append(redir)

            batch_count += 1
            if batch_count > 60:
                log_to_file(f"Processing page {index} of {num_results}", self.wiki)
                total_pageviews = await self._process_batch(batch, out, start, end, total_pageviews)
                batch_count = 0

        total_pageviews = await self._process_batch(batch, out, start, end, total_pageviews)
        log_to_file("Pageviews fetch complete", self.wiki)

        return self._sort_and_truncate_pages_list(out, limit), total_pageviews

    async def _process_batch(self, batch, out, start, end, total_pageviews):
        batch_result = await self.pageviews_repo.get_pageviews(batch, start, end)
        for title, count in batch_result.items():
            out[title]["pageviews"] += count
            total_pageviews += count
            batch[title] = []
        return total_pageviews

    def _sort_and_truncate_pages_list(self, out: dict, limit: int) -> dict:
        sorted_items = sorted(out.items(), key=lambda kv: kv[1]["pageviews"], reverse=True)
        return dict(sorted_items[:limit])
```

**`setText` — using `mwclient` page editing:**

```python
    def set_text(self, page_title: str, text: str, summary: str | None = None, section: bool = False):
        log_to_file(f'Attempting to update "{page_title}"', self.wiki)
        summary = summary or "Popular pages report update"

        if self.dry_run:
            print({"title": page_title, "text": text, "summary": summary, "section": section})
            return None

        try:
            page = self.site.pages[page_title]
            kwargs = {"summary": summary, "bot": True}
            if section:
                kwargs["section"] = 0
            result = page.edit(text, **kwargs)
        except mwclient.errors.LoginError:
            self.login()
            page = self.site.pages[page_title]
            result = page.edit(text, summary=summary, bot=True)
        except Exception:
            # Swallow, matching PHP's silent-fail behavior — a single failed
            # edit should not halt the whole run.
            result = None

        log_to_file(
            f'"{page_title}" updated' if result else f'"{page_title}" could not be updated',
            self.wiki,
        )
        return result
```

`mwclient`'s `page.edit()` handles CSRF token fetching internally
(`Site.get_token`), replacing the manual `MediawikiSession.getToken('edit')`
call from the PHP version. It also auto-retries on expired-token errors in
most cases, but we still guard with an explicit re-login/retry for robustness
since the current PHP behavior is to silently swallow all exceptions here —
we should preserve that "never let one failed edit break the whole batch run"
contract.

**Remaining methods**, ported the same way as above (JSON config fetch via
`self.site.api('parse', page=..., prop='wikitext')`, stale-project filtering,
last-bot-edit-timestamp queries, single-project lookup, assessment config via
a plain `httpx`/`requests` GET to the XTools API):

-   `get_json_config()`
-   `get_stale_projects()`
-   `get_projects_with_last_bot_timestamp()`
-   `get_project(project_name)`
-   `get_bot_last_edit_date(title)`
-   `get_assessment_config()`

### 5.5 `report_updater.py` (from `src/ReportUpdater.php`)

-   Jinja2 environment setup:

```python
from datetime import datetime, timedelta
from calendar import monthrange
from jinja2 import Environment, FileSystemLoader

from .wiki_repository import WikiRepository
from .logger import log_to_file

VIEWS_DIR = BASE_DIR / "views"

class ReportUpdater:
    def __init__(self, wiki: str = "en.wikipedia", dry_run: bool = False):
        self.wiki_repository = WikiRepository(wiki, dry_run)
        self.wiki = wiki
        self.i18n = self.wiki_repository.i18n

        today = datetime.now()
        first_of_this_month = today.replace(day=1)
        last_day_of_prev_month = first_of_this_month - timedelta(days=1)
        self.start = last_day_of_prev_month.replace(day=1)
        self.end = last_day_of_prev_month

        self.env = Environment(loader=FileSystemLoader(str(VIEWS_DIR)))
        self._register_template_helpers()

    def _register_template_helpers(self):
        self.env.globals["msg"] = lambda key, params=None: self.i18n.msg(key, params or [])
        self.env.globals["assessments"] = self._assessments
        self.env.filters["ucfirst"] = lambda s: s[:1].upper() + s[1:] if s else s
        self.env.filters["date"] = lambda dt, fmt: dt.strftime(self._php_to_strftime(fmt))

    def _assessments(self, type_: str, value: str) -> dict:
        dataset = self.wiki_repository.get_assessment_config()[type_]
        for key, values in dataset.items():
            if value.lower() == key.lower():
                return values
        return dataset["Unknown"]

    @staticmethod
    def _php_to_strftime(fmt: str) -> str:
        # Twig templates only use 'Y-m-d' in this project.
        return fmt.replace("Y", "%Y").replace("m", "%m").replace("d", "%d")
```

-   `update_reports(config)`, `process_project(project, config)`,
    `update_index()`, `validate_project_config(project, config)` port with the
    same control flow as the PHP originals; `process_project` becomes `async`
    since it awaits `get_monthly_pageviews_and_assessments`.

### 5.6 CLI scripts (from `bin/*.php`)

```python
# src/src_py/popularpages/cli/check_reports.py
import argparse
import asyncio
import yaml

from popularpages.report_updater import ReportUpdater
from popularpages.wiki_repository import BASE_DIR

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    wikis = yaml.safe_load((BASE_DIR / "config" / "wikis.yaml").read_text())
    for wiki in wikis:
        updater = ReportUpdater(wiki, dry_run=args.dry_run)
        stale_config = updater.wiki_repository.get_stale_projects()
        asyncio.run(updater.update_reports(stale_config))

if __name__ == "__main__":
    main()
```

`generate_report.py` and `generate_index.py` follow the same pattern for a
single wiki/project passed as CLI arguments.

---

## 6. Templates: Twig → Jinja2

Both files (`views/index.wikitext.twig`, `views/report.wikitext.twig`) need
syntax adjustments when renamed to `.jinja`:

| Twig                                               | Jinja2                           | Notes                                                                                                                                                                       |
| -------------------------------------------------- | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `{{ msg('key') }}`                                 | same                             | registered as `env.globals["msg"]`                                                                                                                                          |
| `{% set var %}...{% endset %}`                     | same                             | supported identically                                                                                                                                                       |
| `{% verbatim %}...{% endverbatim %}`               | `{% raw %}...{% endraw %}`       | used to output literal `{{FORMATNUM:`                                                                                                                                       |
| `loop.index`, `loop.index0`                        | same                             | identical semantics                                                                                                                                                         |
| `{{ value\|replace({' ': '_'}) }}`                 | `{{ value\|replace(' ', '_') }}` | Jinja's `replace` filter takes two positional args, not a dict                                                                                                              |
| `{{ start\|date('Y-m-d') }}`                       | `{{ start\|date('Y-m-d') }}`     | keep the same PHP-style format string in the template; convert it inside the custom `date` filter (see `_php_to_strftime` above) so the `.twig`→`.jinja` diff stays minimal |
| `config.Name`, `data.pageviews` (attribute access) | same                             | Jinja2 supports dict-as-attribute access identically                                                                                                                        |

---

## 7. Edge Cases and Migration Risks

1. **`strtotime('first day of previous month')`** has no direct Python
   equivalent — computed manually via `datetime.replace(day=1) - timedelta(days=1)`
   as shown in §5.5. Verify against several months (including year boundaries,
   e.g. running in January to get December of the previous year) with a unit
   test.
2. **PHP `date()` format vs Python `strftime`** — different symbol tables
   (`Y`/`m`/`d` happen to overlap once `%`-prefixed, but don't assume other
   PHP format characters map 1:1 if the templates are extended later).
3. **Async batching pace** — the PHP `REQUEST_DELAY` staggers _dispatch_ of
   promises at the Guzzle client level; the Python port approximates this with
   a per-request `asyncio.sleep` before firing, which is not identical but
   achieves the same rate-limiting goal. Load-test against the real Pageviews
   API to confirm 429s stay rare.
4. **mwclient exception granularity** — `mwclient.errors` exposes distinct
   exception types (`APIError`, `LoginError`, `ProtectedPageError`, etc.)
   rather than PHP's generic `Exception` catch-alls. Decide whether to keep
   the PHP-style "catch everything, log, continue" approach (safer for
   preserving current behavior) or use this migration to add more granular
   error handling — recommend keeping it coarse-grained for parity in v1,
   and revisiting later.
5. **1,000,000-row safety cap** — preserve the `> 1_000_000` project-size
   guard from `processProject` (see T164178 in the original code) exactly;
   this exists as protection against runaway memory use for very large
   WikiProjects, not a stylistic choice.
6. **Credentials come from environment variables** — read via python-dotenv
   from `.env` (see `.env.example`), exposed through `config.load_credentials()`
   as the same flat `creds` mapping (`botuser`, `botpass`, `dbhost`, `dbuser`,
   `dbpass`, `dbport`). No INI parsing is needed; `dbport` defaults to `3306`.
7. **mysqli `bind_param` with dynamic type strings** (used in
   `getProjectsWithLastBotTimestamp` for the `IN (...)` clause) — with
   PyMySQL, build the placeholder string the same way
   (`', '.join(['%s'] * len(titles))`) and pass all values as a single tuple
   to `cursor.execute()`.
8. **Toolforge environment** — confirm the target Python version available on
   Toolforge's Kubernetes backend (commonly 3.9–3.12) and that outbound
   connections to `*.web.db.svc.wikimedia.cloud` and the public internet
   (Pageviews REST API, XTools API) are permitted from the job/cron container,
   same as today.

---

## 8. Testing (`tests/WikiRepositoryTest.php` → `pytest`)

```python
# tests/test_wiki_repository.py
import pytest
from popularpages.wiki_repository import WikiRepository

@pytest.fixture
def wiki_repository():
    return WikiRepository()

def test_does_title_exist(wiki_repository):
    assert wiki_repository.does_title_exist("Barack Obama")
    assert wiki_repository.does_title_exist("Mickey Mouse")
    assert not wiki_repository.does_title_exist("DumDeeDooDum")
    assert not wiki_repository.does_title_exist("Invalid title")

def test_has_lead_section(wiki_repository):
    assert wiki_repository.has_lead_section("Wikipedia:WikiProject Medicine/Popular pages")
    assert not wiki_repository.has_lead_section(
        "User:Community Tech bot/Popular pages config.json"
    )

@pytest.mark.skip(reason="disabled upstream in PHP version too (er-prefixed)")
def test_get_project_pages(wiki_repository):
    ...

@pytest.mark.skip(reason="disabled upstream in PHP version too (er-prefixed)")
def test_get_monthly_pageviews(wiki_repository):
    ...

def test_set_text(wiki_repository):
    result = wiki_repository.set_text(
        "User:NKohli (WMF)/sandbox", "Hi there! This is a test"
    )
    assert result["edit"]["result"] == "Success"
```

Use `pytest-asyncio` (`@pytest.mark.asyncio`) for the pageviews-related async
methods once those tests are re-enabled.

---

## 9. CI (`.github/workflows/python.yml`)

```yaml
name: CI

on:
    push:
        branches: [master]
    pull_request:
        branches: [master]

jobs:
    build:
        runs-on: ubuntu-latest
        steps:
            - uses: actions/checkout@v4
            - uses: actions/setup-python@v5
              with:
                  python-version: "3.12"
            - name: Install dependencies
              run: pip install -e ".[dev]"
            - name: Lint
              run: ruff check .
            - name: Run test suite
              run: pytest --cov=src --cov-report=xml tests/
```

---

## 10. Suggested Execution Order

1. Scaffold `pyproject.toml` + directory layout; copy static files
   (`wikis.yaml`, `messages/*.json`, `LICENSE`, `.env.example`) unchanged.
2. `logger.py` — no dependencies, quick win, unblocks everything else.
3. `i18n.py` — needed by both `WikiRepository` and `ReportUpdater`.
4. `wiki_repository.py`, built incrementally:
   a. `mwclient` login + `does_title_exist` + `has_lead_section` (API-only, easiest to test).
   b. DB methods (`get_project_pages`, `get_projects_with_last_bot_timestamp`).
   c. `set_text`, `get_json_config`, `get_assessment_config`, `get_stale_projects`, `get_project`, `get_bot_last_edit_date`.
5. `pageviews_repository.py` + async batching logic in `wiki_repository.py`.
6. Convert templates to Jinja2 (`.twig` → `.jinja`).
7. `report_updater.py`.
8. CLI scripts under `cli/`.
9. Port tests, wire up `pytest` + CI.
10. Update `README.md` to describe the new Python structure and
    `pip install -e .` setup instead of `composer install`.

---

## 11. Open Items to Confirm

-   Keep `set_text`'s current "swallow every exception, log, continue" behavior
    as-is for v1 parity, or tighten it now that `mwclient` gives clearer
    exception types? (Recommendation: keep coarse for v1, revisit after the
    port is stable in production.)

    -   keep coarse for v1, revisit after the port is stable in production..

-   Target Python version for Toolforge the (affects allowed syntax,
    e.g. `str.removesuffix` requires 3.9+, structural pattern matching would
    need 3.10+ if used elsewhewhere whereould `logs/log-{wiki}.txt` continue to be flat text files, or is this a
    good opportunity to move to structured/rotating logging via the `logging`
    module while keeping the on-disk format human-readable for existing
    tooling/habitshabits

    -   version >= 3.12
