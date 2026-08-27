from __future__ import annotations

import configparser
import json
import re
from datetime import datetime
from pathlib import Path

import httpx
import mwclient
import pymysql
import pymysql.cursors
import yaml

from .i18n import I18n
from .logger import log_to_file
from .pageviews_repository import PageviewsRepository

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class WikiRepository:
    """Fetches data from the MediaWiki action API and the replica database.

    Post-processing of this data is minimal.
    """

    def __init__(self, wiki: str = "en.wikipedia", dry_run: bool = False):
        self.wiki = wiki
        self.dry_run = dry_run
        self.creds = self._load_credentials()
        self.wiki_config = yaml.safe_load((BASE_DIR / "config" / "wikis.yaml").read_text(encoding="utf-8"))[wiki]

        lang = wiki.split(".")[0]
        self.i18n = I18n(lang)
        self.pageviews_repo = PageviewsRepository(wiki)
        self.assessment_config: dict | None = None

        host = f"{wiki}.org"
        self.site = mwclient.Site(host, path="/w/")
        self.username = self.creds["botuser"].split("@")[0]
        self.login()

    def _load_credentials(self) -> dict:
        config = configparser.ConfigParser()
        # config.ini has no section headers, like PHP's parse_ini_file.
        with (BASE_DIR / "config.ini").open(encoding="utf-8") as f:
            content = "[DEFAULT]\n" + f.read()
        config.read_string(content)
        return {k: v.strip("'\"") for k, v in config["DEFAULT"].items()}

    def login(self) -> None:
        self.site.login(self.creds["botuser"], self.creds["botpass"])

    def get_wiki_config(self) -> dict:
        return self.wiki_config

    # ------------------------------------------------------------------ #
    # API helpers
    # ------------------------------------------------------------------ #
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

    def get_json_config(self) -> dict:
        params = {"page": self.wiki_config["config"], "prop": "wikitext"}
        res = self.site.api("parse", **params)
        wikitext = res["parse"]["wikitext"]
        if isinstance(wikitext, dict):
            wikitext = wikitext.get("*", "")
        config = json.loads(wikitext)

        # Remove the 'description' entry which is meant only as explanatory text.
        config.pop("description", None)
        return config

    def get_stale_projects(self) -> dict:
        log_to_file("Checking for stale projects", self.wiki)
        config = self.get_json_config()

        bot_timestamps = self.get_projects_with_last_bot_timestamp()
        # Remove projects from the config that have already been updated.
        first_of_this_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        for row in bot_timestamps:
            rev_timestamp = datetime.strptime(row["rev_timestamp"], "%Y%m%d%H%M%S")
            if rev_timestamp >= first_of_this_month:
                config.pop(row["name"], None)

        return config

    def get_projects_with_last_bot_timestamp(self) -> list[dict]:
        log_to_file("Fetching timestamps of the bot's last edits", self.wiki)
        config = self.get_json_config()
        # Map the db-key page title (no namespace prefix) to the project name.
        projects = {re.sub(r"^.*?:", "", info["Report"].replace(" ", "_")): name for name, info in config.items()}
        titles = list(projects.keys())

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
                        SELECT actor_id FROM actor WHERE actor_name = %s
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
        config = self.get_json_config()
        for project, info in config.items():
            if info["Name"] == project_name:
                return {project: info}
        return None

    def get_bot_last_edit_date(self, page: str) -> str:
        result = self.site.api(
            "query",
            prop="revisions",
            titles=page,
            rvprop="timestamp",
            rvuser=self.username,
            rvlimit=1,
            formatversion=2,
        )
        for p in result["query"]["pages"]:
            revisions = p.get("revisions")
            if revisions:
                ts = revisions[0]["timestamp"]
                return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d")
        return ""

    def get_assessment_config(self) -> dict:
        if self.assessment_config is not None:
            return self.assessment_config

        resp = httpx.get("https://xtools.wmflabs.org/api/project/assessments", timeout=10)
        self.assessment_config = resp.json()["config"][f"{self.wiki}.org"]
        return self.assessment_config

    # ------------------------------------------------------------------ #
    # Database
    # ------------------------------------------------------------------ #
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

    # ------------------------------------------------------------------ #
    # Pageviews + assessments (batched)
    # ------------------------------------------------------------------ #
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

            # The $batchCount represents how many pages (incl. redirects) are
            # queued. The 60 is arbitrary (see T-plan notes): we keep batches
            # close to the API's ~100 req/sec limit without a hard cap.
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
            # Clear out batch only for this title, otherwise the target page
            # might get re-added in the next batch.
            batch[title] = []
        return total_pageviews

    def _sort_and_truncate_pages_list(self, out: dict, limit: int) -> dict:
        sorted_items = sorted(out.items(), key=lambda kv: kv[1]["pageviews"], reverse=True)
        return dict(sorted_items[:limit])

    # ------------------------------------------------------------------ #
    # Editing
    # ------------------------------------------------------------------ #
    def set_text(
        self,
        page_title: str,
        text: str,
        summary: str | None = None,
        section: bool = False,
    ):
        log_to_file(f'Attempting to update "{page_title}"', self.wiki)
        summary = summary or "Popular pages report update"

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
            # Swallow, matching PHP's silent-fail behaviour — a single failed
            # edit should not halt the whole run.
            result = None

        log_to_file(
            f'"{page_title}" updated' if result else f'"{page_title}" could not be updated',
            self.wiki,
        )
        return result
