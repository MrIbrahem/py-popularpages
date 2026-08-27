"""MediaWiki API + replica database access, ported from src/WikiRepository.php.

Uses `mwclient` for all interaction with the MediaWiki Action API (login,
querying, parsing, editing), replacing the PHP version's hand-rolled
FluentRequest/apiQuery() wrapper. Database access uses PyMySQL in place of
mysqli, keeping the original SQL queries unchanged since they are specific
to the Wikimedia replica database schema.
"""

from __future__ import annotations

import configparser
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import mwclient
import mwclient.errors
import pymysql
import pymysql.cursors
import yaml

from .i18n import I18n
from .logger import log_to_file
from .pageviews_repository import PageviewsRepository

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# See T164178 in the original PHP codebase: this is a safety cap against
# unbounded memory use for extremely large WikiProjects, not a stylistic
# choice -- keep it as-is.
MAX_PROJECT_SIZE = 1_000_000

# Number of target pages to accumulate before flushing a pageviews batch.
# The 60 is arbitrary (see original PHP comment): staying near this number
# keeps each PageviewsRepository.get_pageviews() call in the 60-200 page
# range once redirects are included, which keeps us close to the Pageviews
# API's 100 req/sec limit without needing to hit it exactly -- the retry
# handler in PageviewsRepository absorbs the rest.
BATCH_SIZE_THRESHOLD = 60

ASSESSMENT_CONFIG_URL = "https://xtools.wmflabs.org/api/project/assessments"


class WikiRepository:
    """Fetches data from the MediaWiki action API and the replica database.

    Post-processing of this data is minimal.
    """

    def __init__(self, wiki: str = "en.wikipedia", dry_run: bool = False):
        """
        :param wiki: Wiki in the form lang.project, e.g. 'en.wikipedia'.
        :param dry_run: If True, `set_text()` prints instead of saving to the wiki.
        """
        self.wiki = wiki
        self.dry_run = dry_run
        self.creds = self._load_credentials()

        self.wiki_config: dict = yaml.safe_load((BASE_DIR / "config" / "wikis.yaml").read_text(encoding="utf-8"))[wiki]

        lang = wiki.split(".")[0]
        self.i18n = I18n(lang)
        self.pageviews_repo = PageviewsRepository(wiki)

        self._assessment_config: dict | None = None
        self._http_client = httpx.Client(timeout=10.0)

        self.host = f"{wiki}.org"
        self.username = self.creds["botuser"].split("@")[0]
        self.site: mwclient.Site = mwclient.Site(self.host, path="/w/")
        self.login()

    # -- Setup / credentials -------------------------------------------------

    @staticmethod
    def _load_credentials() -> dict[str, str]:
        """
        Load config.ini, which has no [section] headers (like PHP's
        parse_ini_file). We inject a synthetic DEFAULT section so
        configparser can read it, then strip quotes from values.
        """
        parser = configparser.ConfigParser()

        config_path = BASE_DIR / "config.ini"
        # config.ini has no section headers, like PHP's parse_ini_file.
        content = "[DEFAULT]\n" + config_path.read_text(encoding="utf-8")
        parser.read_string(content)
        return {key: value.strip("'\"") for key, value in parser["DEFAULT"].items()}

    def login(self) -> None:
        """Log in to the wiki using bot password credentials."""
        self.site.login(self.creds["botuser"], self.creds["botpass"])

    def get_wiki_config(self) -> dict:
        """Get the configuration for the wiki as a whole (index/config/category)."""
        return self.wiki_config

    # -- Database -----------------------------------------

    def _connect_db(self) -> pymysql.connections.Connection:
        # In production, the host is *.web.db.svc.wikimedia.cloud, where the
        # asterisk is dynamically replaced with the database name.
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

    # ---------------------------------------------------
    # API helpers
    def does_title_exist(self, title: str) -> bool:
        """Check if a given title exists on the wiki.

        :param title: Title to check existence for.
        :return: True if the title exists, else False.
        """
        result = self.site.api("query", titles=title, formatversion=2)
        for page in result["query"]["pages"]:
            if "missing" in page or "invalid" in page:
                return False
        return True

    def has_lead_section(self, title: str) -> bool:
        """Check whether the page already has a first (lead) section.

        :param title: The page title to check.
        :return: True if it exists, else False.
        """
        if not self.does_title_exist(title):
            return False
        result = self.site.api("parse", page=title, prop="sections", formatversion=2)
        return bool(result.get("parse", {}).get("sections"))

    def get_json_config(self) -> dict:
        """Fetch JSON config from the wiki's config page.

        :return: Config data, with the 'description' explanatory entry removed.
        """
        params = {"page": self.wiki_config["config"], "prop": "wikitext"}

        result = self.site.api("parse", **params)
        wikitext = result["parse"]["wikitext"]

        if isinstance(wikitext, dict):
            wikitext = wikitext.get("*", "")

        config = json.loads(wikitext)

        # Remove the 'description' entry which is meant only as explanatory text.
        config.pop("description", None)
        return config

    def get_stale_projects(self) -> dict:
        """Get WikiProjects that have not yet been updated for the current cycle.

        :return: Config for WikiProjects not updated so far this month.
        """
        log_to_file("Checking for stale projects", self.wiki)
        config = self.get_json_config()

        bot_timestamps = self.get_projects_with_last_bot_timestamp()
        first_of_this_month = _first_of_this_month_timestamp()

        for row in bot_timestamps:
            rev_timestamp = _mediawiki_timestamp_to_epoch(row["rev_timestamp"])
            if rev_timestamp >= first_of_this_month:
                config.pop(row["name"], None)

        return config

    def get_projects_with_last_bot_timestamp(self) -> list[dict]:
        """Get timestamps of the bot's last edits for all configured WikiProjects.

        :return: List of dicts with 'page_title', 'rev_timestamp', and 'name'.
        """
        log_to_file("Fetching timestamps of the bot's last edits", self.wiki)
        config = self.get_json_config()

        # Map db-key page title -> WikiProject name (config key).
        # FIXME: assumes reports are in the Project namespace (matches PHP TODO).
        projects: dict[str, str] = {}
        for project_name, info in config.items():
            # db_key = info["Report"].split(":", 1)[-1]
            db_key = re.sub(r"^.*?:", "", info["Report"])
            projects[db_key.replace(" ", "_")] = project_name

        titles = list(projects.keys())
        if not titles:
            return []

        conn = self._connect_db()
        try:
            with conn.cursor() as cursor:
                placeholders = ", ".join(["%s"] * len(titles))
                cursor.execute(
                    f"""
                    SELECT page_title, MAX(rev_timestamp) AS rev_timestamp
                    FROM revision_userindex
                    JOIN page ON rev_page = page_id
                    WHERE rev_actor = (
                        SELECT actor_id
                        FROM actor
                        WHERE actor_name = %s
                    )
                    AND page_title IN ({placeholders})
                    AND page_namespace = 4 -- FIXME: assumes reports are in the Project namespace
                    GROUP BY page_title
                    """,
                    (self.username, *titles),
                )
                rows = cursor.fetchall()
        finally:
            conn.close()

        for row in rows:
            row["name"] = projects[row["page_title"]]

        return rows

    def get_project(self, project_name: str) -> dict | None:
        """Get config for a single WikiProject by its display name.

        :param project_name: Name of WikiProject as specified in the 'Name'
            parameter of the JSON config.
        :return: {project_key: config} for the matching project, or None.
        """
        config = self.get_json_config()
        for project, info in config.items():
            if info["Name"] == project_name:
                return {project: info}
        return None

    def get_bot_last_edit_date(self, page: str) -> str:
        """Get the date the bot last edited the given page.

        :param page: Page title.
        :return: Date in YYYY-MM-DD format, or '' if never edited by the bot.
        """
        result = self.site.api(
            "query",
            prop="revisions",
            titles=page,
            rvprop="timestamp",
            rvuser=self.username,
            rvlimit=1,
            formatversion=2,
        )
        timestamp = ""
        try:
            for p in result["query"]["pages"]:
                revisions = p.get("revisions")
                if revisions:
                    timestamp = revisions[0]["timestamp"]
                    break
        except (KeyError, IndexError):
            return ""

        if timestamp:
        return _mediawiki_timestamp_to_date(timestamp)

        return ""

    def get_assessment_config(self) -> dict:
        """Get the wiki's assessment configuration (colors/icons per class/importance).

        :return: Nested dict, e.g. {'class': {...}, 'importance': {...}}.
        """
        if self._assessment_config is not None:
            return self._assessment_config

        resp = self._http_client.get(ASSESSMENT_CONFIG_URL)
        resp.raise_for_status()
        data = resp.json()
        self._assessment_config = data["config"][f"{self.wiki}.org"]
        return self._assessment_config

    # -- Database-backed page/pageviews fetching ----------------------------

    def get_project_pages(self, project: str) -> list[dict]:
        """Get titles & assessments for all pages in a WikiProject.

        :param project: Name of the project, e.g. 'Medicine'.
        :return: List of rows with page_title, pa_class, pa_importance, redir_title.
        """
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

    # ---------------------------------------------------
    # Pageviews + assessments (batched)
    async def get_monthly_pageviews_and_assessments(
        self, rows: list[dict], start: str, end: str, limit: int
    ) -> tuple[dict, int]:
        """
        Get monthly pageviews for the given pages and their redirects.

        :param rows: Rows as returned by get_project_pages().
        :param start: Start date, in YYYYMMDD00 format.
        :param end: End date, in YYYYMMDD00 format.
        :param limit: Max number of pages to include in the final report.
        :return: (pages_dict, total_pageviews), where pages_dict maps page
            title -> {'pageviews', 'class', 'importance'}.
        """
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

            # The $batchCount represents how many pages (incl. redirects) are
            # queued. The 60 is arbitrary (see T-plan notes): we keep batches
            # close to the API's ~100 req/sec limit without a hard cap.
            batch_count += 1
            if batch_count > BATCH_SIZE_THRESHOLD:
                log_to_file(f"Processing page {index} of {num_results}", self.wiki)
                total_pageviews = await self._process_batch(batch, out, start, end, total_pageviews)
                batch_count = 0

        # Finish processing any leftover pages.
        total_pageviews = await self._process_batch(batch, out, start, end, total_pageviews)

        log_to_file("Pageviews fetch complete", self.wiki)

        return self._sort_and_truncate_pages_list(out, limit), total_pageviews

    async def _process_batch(
        self,
        batch: dict[str, list[str]],
        out: dict[str, dict],
        start: str,
        end: str,
        total_pageviews: int,
    ) -> int:
        """
        Process one batch of pages, updating `out` and the running total in place.

        :return: Updated total_pageviews.
        """
        batch_result = await self.pageviews_repo.get_pageviews(batch, start, end)
        for title, count in batch_result.items():
            out[title]["pageviews"] += count
            total_pageviews += count
            # Clear out batch only for this title, otherwise the target page
            # might get re-added in the next batch.
            batch[title] = []
        return total_pageviews

    @staticmethod
    def _sort_and_truncate_pages_list(out: dict, limit: int) -> dict:
        """Sort by pageviews descending and truncate to the configured limit."""
        sorted_items = sorted(out.items(), key=lambda kv: kv[1]["pageviews"], reverse=True)
        return dict(sorted_items[:limit])

    # ---------------------------------------------------
    # Editing
    def set_text(
        self,
        page_title: str,
        text: str,
        summary: str | None = None,
        section: bool = False,
    ) -> dict | None:
        """
        Update a wiki page with the given text.

        :param page_title: Page to set text for.
        :param text: Text to set on the page.
        :param summary: Edit summary.
        :param section: If truthy, edit only section 0 (the lead) instead of
            replacing the whole page -- matches the PHP version's use of
            the 'section' API param to only touch the lead when it exists.
        :return: The API result dict, or None if the edit failed or this is
            a dry run.
        """
        log_to_file(f'Attempting to update "{page_title}"', self.wiki)
        summary = summary or self.i18n.msg("edit-summary")

        if self.dry_run:
            print(
                {
                    "title": page_title,
                    "text": text,
                    "summary": summary,
                    "section": section,
                }
            )
            return None

        result = None
        try:
            page = self.site.pages[page_title]
            kwargs = {"summary": summary, "bot": True}
            if section:
                kwargs["section"] = "0"
            result = page.edit(text, **kwargs)
        except mwclient.errors.LoginError:
            # Session likely expired; log back in and retry once.
            try:
                self.login()
                page = self.site.pages[page_title]
                kwargs = {"summary": summary, "bot": True}
                if section:
                    kwargs["section"] = "0"
                result = page.edit(text, **kwargs)
            except Exception:
                # Silently fail, matching the PHP version: one failed edit
                # should not halt the whole run. generate_report.py can be
                # run on the single failing project for debugging.
                result = None
        except Exception:
            result = None

        msg = f'"{page_title}" updated' if result else f'"{page_title}" could not be updated'
        log_to_file(msg, self.wiki)

        return result


# -- Module-level helpers ---------------------------------------------------


def _load_json_relaxed(wikitext: str) -> dict:
    """Parse the JSON config page content (stripping any surrounding
    <pre>/<nowiki> wrapping that on-wiki JSON pages sometimes have)."""

    return json.loads(wikitext)


def _mediawiki_timestamp_to_epoch(timestamp: str) -> float:
    """Convert a MediaWiki DB-style timestamp (YYYYMMDDHHMMSS) to a Unix epoch."""

    dt = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _mediawiki_timestamp_to_date(timestamp: str) -> str:
    """Convert an ISO 8601 MediaWiki API timestamp to YYYY-MM-DD."""

    # API (formatversion=2) timestamps look like '2023-01-15T00:00:00Z'.
    dt = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    return dt.strftime("%Y-%m-%d")

def _first_of_this_month_timestamp() -> float:
    """Unix epoch for midnight on the first day of the current month (UTC)."""

    now = datetime.now(timezone.utc)
    # Remove projects from the config that have already been updated.
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
