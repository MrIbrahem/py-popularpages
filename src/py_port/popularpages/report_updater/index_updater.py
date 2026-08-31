"""
Report generation and saving, ported from src/ReportUpdater.php.

Uses Jinja2 in place of Twig for rendering the wikitext report and index
page templates.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pymysql
from jinja2 import Environment, FileSystemLoader

from ..config import app_config
from ..mapping import WikiProjectConfig
from ..utils import format_date, uc_first
from ..wiki_repository import WikiRepository

logger = logging.getLogger(__name__)


class IndexUpdater:
    """
    Responsible for creating reports for one or more WikiProjects on a wiki.
    """

    def __init__(self, wiki: str = "en.wikipedia", dry_run: bool = False):
        """
        :param wiki: Target wiki, e.g. 'en.wikipedia'.
        :param dry_run: Passed through to WikiRepository -- if True, prints
            instead of saving edits to the wiki.
        """
        self.wiki_repository = WikiRepository(wiki, dry_run)
        self.wiki = wiki
        self.i18n = self.wiki_repository.i18n
        logger.info("ReportUpdater initialized for wiki '%s' (dry_run=%s)", wiki, dry_run)

        self.env = Environment(loader=FileSystemLoader(str(app_config.paths.views_dir)))
        self._register_template_helpers()

    def _register_template_helpers(self) -> None:
        logger.debug("Registering template helpers (ucfirst, date, msg, assessments)")
        self.env.filters["ucfirst"] = uc_first
        self.env.filters["date"] = format_date

        def _msg(key: str, params: list | None = None) -> str:
            return self.i18n.msg(key, params or [])

        # NOTE: Deliberately *no* `assessments` global here. Jinja templates must
        # not perform network I/O, and the assessment config is fetched/resolved
        # in the data-fetch phase (see `process_project`) before rendering.
        self.env.globals["msg"] = _msg  # type: ignore[assignment]

    def retrieve_project_updates(self) -> list[WikiProjectConfig]:
        """
        Retrieve project configurations and update them with their last edit timestamps.

        Fetches the JSON configuration for WikiProjects and their corresponding
        last bot edit timestamps from the repository. It then parses the raw
        timestamp (YYYYMMDDHHMMSS format) from the database into a standardized
        date string (YYYY-MM-DD format) and assigns it to each project's
        `Updated` attribute.

        Returns:
            list[WikiProjectConfig]: A list of WikiProjectConfig objects,
            where each object contains its configuration and the formatted
            last update date (if available).
        """
        projects_config = self.wiki_repository.get_json_config()
        list_config_obj = WikiProjectConfig.from_json_list(projects_config)
        logger.debug("Retrieved %d project config(s)", len(list_config_obj))

        try:
            last_edits = self.wiki_repository.get_projects_with_last_bot_timestamp()
        except pymysql.err.OperationalError as e:
            logger.error("Error retrieving last bot edit timestamps: %s", e)
            return []
        except pymysql.Error as e:
            logger.error("Error retrieving last bot edit timestamps: %s", e)
            return []

        logger.debug("Retrieved %d last-edit timestamp(s)", len(last_edits))

        if not last_edits:
            logger.info("Error: No last-edit timestamps found!")
            return list_config_obj

        # `last_edits` rows are keyed by the report db-title (report_without_ns,
        # e.g. "WikiProject_Foo/Popular_pages"), NOT by the project main page.
        # Map those db-titles back to the project main page title so we can
        # attach each timestamp to the right WikiProjectConfig. Without this
        # mapping every `Updated` stayed `None` and rendered as the literal
        # string "None" in the index table.
        report_to_project = {x.report_without_ns: x.project_main_page for x in list_config_obj}

        last_edits_times = {
            report_to_project[row["page_title"]]: row["rev_timestamp"]
            for row in last_edits
            if row["page_title"] in report_to_project
        }

        for x in list_config_obj:
            if x.project_main_page in last_edits_times:
                rev_date = last_edits_times[x.project_main_page]
                # rev_timestamp from the DB is YYYYMMDDHHMMSS.
                parsed = datetime.strptime(str(rev_date), "%Y%m%d%H%M%S")
                x.Updated = parsed.strftime("%Y-%m-%d")

        return list_config_obj

    # ---------------------------------------------------
    # Public Methods
    # ---------------------------------------------------

    def update_index(self) -> None:
        """
        Update the index page listing each WikiProject, its report,
        and when it was last updated."""
        logger.info("Updating index page for wiki '%s'", self.wiki)

        list_config_obj = self.retrieve_project_updates()

        if not list_config_obj:
            logger.error("No project updates retrieved")
            return

        wiki_config = self.wiki_repository.get_wiki_config()
        # Generate and return wikitext.
        output = self.env.get_template("index.wikitext.jinja").render(
            projects=list_config_obj,
            configPage=wiki_config["config"],
        )

        self.wiki_repository.set_text(
            page_title=wiki_config["index"],
            text=output,
            summary=self.i18n.msg("edit-summary"),
            file_name="index.wikitext",
        )


__all__ = [
    "IndexUpdater",
]
