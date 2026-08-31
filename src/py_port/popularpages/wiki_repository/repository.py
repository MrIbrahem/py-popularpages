"""
MediaWiki API access, ported from src/WikiRepository.php.

Uses `mwclient` for all interaction with the MediaWiki Action API (login,
querying, parsing, editing), replacing the PHP version's hand-rolled
FluentRequest/apiQuery() wrapper.

Direct replica-database access now lives in WikiDatabaseRepository
(wiki_database_repository.py); this class delegates to it for anything
that requires SQL.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import httpx
import mwclient
import mwclient.errors
import wikitextparser as wtp

from ..config import app_config
from ..i18n import I18n
from ..logger import log_to_file
from ..mapping import WikiProjectConfig
from ..pageviews.pageviews_repository import PageviewsRepository
from ..utils import first_of_this_month_timestamp, mediawiki_timestamp_to_epoch
from ..wiki_database_repository import WikiDatabaseRepository

logger = logging.getLogger(__name__)


class WikiRepository:
    """
    Fetches data from the MediaWiki action API.

    Post-processing of this data is minimal. Replica-database access is
    delegated to WikiDatabaseRepository (self.db).
    """

    def __init__(
        self,
        wiki: str = "en.wikipedia",
        dry_run: bool = False,
        log_dir: Path | None = None,
    ) -> None:
        """
        :param wiki: Wiki in the form lang.project, e.g. 'en.wikipedia'.
        :param dry_run: If True, `set_text()` prints instead of saving to the wiki.
        """
        self.wiki = wiki
        self.dry_run = dry_run
        self.log_dir: Path = log_dir or app_config.data_paths.log_dir
        self.i18n = I18n(wiki.split(".")[0])
        self.creds = app_config.credentials
        self.username = self.creds.botuser.split("@")[0]
        self.host = f"{wiki}.org"

        _config: dict = app_config.paths.load_wikis_config()
        self.wiki_config: dict = _config.get(wiki) or {}
        if not self.wiki_config:
            raise ValueError(f"Wiki {wiki} not found in config")
        logger.debug("Loaded wiki config: %s", self.wiki_config)

        self.wiki_config_page: str = self.wiki_config["config"]

        self.pageviews_repo = PageviewsRepository(wiki)

        # lazy loading objects will be initialized on first use
        self._assessment_config: dict | None = None
        self._site: mwclient.Site | None = None

        self.db = WikiDatabaseRepository(
            wiki=self.wiki,
            wiki_config=self.wiki_config,
            username=self.username,
        )

        self._http_client = httpx.Client(
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": app_config.other.user_agent},
        )

        logger.info(
            "WikiRepository initialized for wiki '%s' (user='%s', dry_run=%s)",
            wiki,
            self.username,
            dry_run,
        )

    # ---------------------------------------------------
    # Lazy Properties
    # ---------------------------------------------------

    @property
    def site(self) -> mwclient.Site:
        """
        Create the mwclient connection and log in on first use.
        """
        if self._site is None:
            self._site = mwclient.Site(
                self.host,
                path="/w/",
                clients_useragent=app_config.other.user_agent,
            )
            self.login()
        return self._site

    def get_config(self, title: str | None = None) -> list[WikiProjectConfig]:
        """
        Get the WikiProject config for the given title.

        :param title: WikiProject page title, e.g. 'Wikipedia:WikiProject
        Popular pages'.
        :return: WikiProjectConfig object.
        """
        json_data = self.get_json_config(title)
        logger.debug("Loaded %d WikiProject config(s) for title '%s'", len(json_data), title)
        return WikiProjectConfig.from_json_list(json_data)

    def get_json_config(self, title: str | None = None) -> dict:
        """
        Fetch JSON config from the wiki's config page.

        Example: Wikipedia:WikiProject/Popular pages config.json

        :return: Config data, with the 'description' explanatory entry removed.
        """
        if title is None:
            title = self.wiki_config_page

        logger.debug("Fetching JSON config from '%s'", title)
        page = self.site.pages[title]

        wikitext = page.text()

        config = json.loads(wikitext)
        logger.debug("Parsed JSON config with %d top-level key(s)", len(config))

        # Remove the 'description' entry which is meant only as explanatory text.
        config.pop("description", None)
        return config

    # -- Setup / credentials ---------------------------------------------------

    def login(self) -> None:
        """
        Log in to the wiki using bot password credentials."""
        if not self.dry_run:
            logger.info("Logging in as '%s'", self.creds.botuser)
            self.site.login(self.creds.botuser, self.creds.botpass)
            logger.info("Logged in to %s", self.host)
        else:
            logger.info("dry_run=True; skipping login")

    def get_wiki_config(self) -> dict:
        """
        Get the configuration for the wiki as a whole (index/config/category)."""
        return self.wiki_config

    def get_projects_with_last_bot_timestamp(self) -> list[dict]:
        """
        Get timestamps of the bot's last edits for all configured WikiProjects.

        :return: List of dicts with 'page_title', 'rev_timestamp', and 'name'.
        """
        config = self.get_config()
        projects = {x.report_without_ns: x.project_main_page for x in config}

        titles = list(projects.keys())
        logger.debug("Looking up last-bot timestamps for %d project(s)", len(titles))
        if not titles:
            return []

        return self.db.get_projects_timestamps(titles)

    # ---------------------------------------------------
    # API helpers
    def does_title_exist(self, title: str) -> bool:
        """
        Check if a given title exists on the wiki.

        :param title: Title to check existence for.
        :return: True if the title exists, else False.
        """
        page = self.site.pages[title]
        logger.debug("does_title_exist('%s') -> exists=%s", title, page.exists)
        if page.exists:
            return True
        return True

    def has_lead_section(self, title: str) -> bool:
        """
        Check whether the page already has a first (lead) section.

        :param title: The page title to check.
        :return: True if it exists, else False.
        """
        logger.debug("Checking lead section for '%s'", title)
        page = self.site.pages[title]

        if not page.exists:
            logger.debug("'%s' does not exist", title)
            return False

        page_text = page.text()

        parsed = wtp.parse(page_text)
        sections = parsed.sections

        if not sections or len(sections) < 1:
            # We return false if we didn't find any section
            logger.debug("'%s' has no sections", title)
            return False

        logger.debug("'%s' has a lead section", title)
        return True

    def get_project(self, project_name: str) -> WikiProjectConfig | None:
        """
        Get config for a single WikiProject by its display name.

        :param project_name: Name of WikiProject as specified in the 'Name'
            parameter of the JSON config.
        :return: WikiProjectConfig for the matching project, or None.
        """
        logger.debug("Looking up project by name '%s'", project_name)
        config = self.get_config()
        for project in config:
            if project.Name == project_name:
                return project
        logger.debug("No project found with name '%s'", project_name)
        return None

    def get_assessment_config(self) -> dict:
        """
        Get the wiki's assessment configuration (colors/icons per class/importance).

        :return: Nested dict, e.g. {'class': {...}, 'importance': {...}}.
        """
        if self._assessment_config is not None:
            logger.debug("Returning cached assessment config")
            return self._assessment_config

        logger.info("Fetching assessment config from %s", app_config.wiki.assessment_config_url)
        try:
            resp = self._http_client.get(app_config.wiki.assessment_config_url)
            resp.raise_for_status()
            data = resp.json()

            self._assessment_config = data["config"][f"{self.wiki}.org"]
        except Exception as e:
            logger.error("Failed to fetch assessment config: %s", e)

        logger.debug("Loaded assessment config for '%s.org'", self.wiki)
        return self._assessment_config  # pyright: ignore[reportReturnType]

    # ---------------------------------------------------
    # Pageviews + assessments (batched)
    async def get_monthly_pageviews_and_assessments(
        self,
        rows: list[dict],
        start: str,
        end: str,
        limit: int,
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
        logger.info("Fetching monthly pageviews for %d row(s) (limit=%d)", len(rows), limit)

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
            if batch_count > app_config.pageviews.batch_size_threshold:
                log_to_file(f"Processing page {index} of {num_results}", self.wiki)
                total_pageviews = await self._process_batch(batch, out, start, end, total_pageviews)
                batch_count = 0

        # Finish processing any leftover pages.
        total_pageviews = await self._process_batch(batch, out, start, end, total_pageviews)

        log_to_file("Pageviews fetch complete", self.wiki)
        logger.info("Pageviews fetch complete: %d total pageviews", total_pageviews)

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
        logger.debug("Processing batch of %d page(s)", len(batch))
        batch_result = await self.pageviews_repo.get_pageviews(batch, start, end)
        logger.debug("Batch returned %d result(s)", len(batch_result))
        for title, count in batch_result.items():
            out[title]["pageviews"] += count
            total_pageviews += count
            # Clear out batch only for this title, otherwise the target page
            # might get re-added in the next batch.
            batch[title] = []
        return total_pageviews

    @staticmethod
    def _sort_and_truncate_pages_list(out: dict[str, dict], limit: int) -> dict[str, dict]:
        """
        Sort by pageviews descending and truncate to the configured limit."""

        def zz(kv):
            return kv[1]["pageviews"]

        sorted_items = sorted(out.items(), key=zz, reverse=True)
        return dict(sorted_items[:limit])

    # ---------------------------------------------------
    # Editing
    def set_text(
        self,
        page_title: str,
        text: str,
        summary: str | None = None,
        section_number: int | None = None,
    ) -> dict | None:
        """
        Update a wiki page with the given text.

        :param page_title: Page to set text for.
        :param text: Text to set on the page.
        :param summary: Edit summary.
        :param section: Section to update. If None, the entire page is updated.
        :return: The API result dict, or None if the edit failed or this is
            a dry run.
        """
        logger.info(
            "set_text: attempting to update '%s' (section=%s, dry_run=%s)", page_title, section_number, self.dry_run
        )
        log_to_file(f'Attempting to update "{page_title}"', self.wiki)

        if not self.site.logged_in:
            self.login()

        summary = summary or self.i18n.msg("edit-summary")

        if self.dry_run:
            logger.info(
                {
                    "title": page_title,
                    "summary": summary,
                    "section": section_number,
                    "bot": True,
                }
            )
            self._write_dry_run_text(page_title, text)
            return None

        page = self.site.pages[page_title]

        # NOTE: In the original PHP setText, $hasLeadSection (bool) is passed directly as the section param
        # `if ( $section ) $params['section'] = $section;`. Since True casts to 1, this sets section=1, not
        # section=0 -- and that's intentional, not a bug: when the page has a lead section.
        # report.wikitext.jinja skips the 'report-header' line and starts the generated content straight at
        # '== {{ msg('list') }} ==', meant to replace the section AFTER the lead (section 1), leaving the
        # human-written intro (section 0) untouched.

        section = str(section_number) if section_number else None
        result = None
        try:
            result = page.edit(text=text, summary=summary, bot=True, section=section)
        except mwclient.errors.LoginError:
            # Session likely expired; log back in and retry once.
            self.login()
            result = page.edit(text=text, summary=summary, bot=True, section=section)
        except Exception:
            result = None

        msg = f'"{page_title}" updated' if result else f'"{page_title}" could not be updated'
        logger.info("set_text: %s", msg)

        log_to_file(msg, self.wiki)

        return result

    def _write_dry_run_text(self, page_title: str, text: str) -> None:
        """
        Persist the rendered wikitext to the logs folder when in dry-run mode.

        Lets you inspect the exact output that *would* have been saved to the
        wiki, since a dry run otherwise discards the text after logging it.
        The page title is sanitized so it is safe as a filename (colons and
        slashes in wiki titles are common).
        """
        safe_title = re.sub(r"[^\w.\-]+", "_", page_title)

        out_path = self.log_dir / f"dryrun-{self.wiki}-{safe_title}.wikitext"

        text_with_header = f"Title: [[{page_title}]]\n\n{text}"
        out_path.write_text(text_with_header, encoding="utf-8")

        logger.info("dry-run: wrote wikitext for '%s' to %s", page_title, out_path)

    def get_bot_last_edit_date(self, title: str) -> str:
        """
        Get the date the bot last edited the given page.

        :param page: Page title.
        :return: Date in YYYY-MM-DD format, or '' if never edited by the bot.
        """
        logger.debug("Looking up bot's last edit date for '%s'", title)
        page = self.site.pages[title]

        revisions = page.revisions(
            prop="timestamp",
            user=self.username,
            api_chunk_size=1,
        )
        if revisions:
            for rev in revisions:
                timestamp: time.struct_time = rev["timestamp"]
                date_str = time.strftime("%Y-%m-%d", timestamp)
                logger.debug("Bot last edited '%s' on %s", title, date_str)
                return date_str

        logger.debug("No bot edits found for '%s'", title)
        return ""

    def get_stale_projects(self) -> list[WikiProjectConfig]:
        """
        Get WikiProjects that have not yet been updated for the current cycle.

        :return: Config for WikiProjects not updated so far this month.
        """
        log_to_file("Checking for stale projects", self.wiki)
        logger.info("Checking for stale projects on '%s'", self.wiki)

        _config = self.get_config()
        projects = {x.report_without_ns: x.project_main_page for x in _config}

        if not projects:
            return _config

        titles = list(projects.keys())
        bot_timestamps = self.db.get_projects_timestamps(titles)

        first_of_this_month = first_of_this_month_timestamp()

        to_pop = []
        for row in bot_timestamps:
            proj_name = projects[row["page_title"]]
            rev_timestamp = mediawiki_timestamp_to_epoch(row["rev_timestamp"])
            if rev_timestamp >= first_of_this_month:
                to_pop.append(proj_name)

        stale = [x for x in _config if x.project_main_page not in to_pop]
        logger.info("Found %d stale project(s) of %d", len(stale), len(_config))
        return stale


__all__ = [
    "WikiRepository",
]
