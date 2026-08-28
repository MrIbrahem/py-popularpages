"""
Report generation and saving, ported from src/ReportUpdater.php.

Uses Jinja2 in place of Twig for rendering the wikitext report and index
page templates.
"""

from __future__ import annotations

from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader

from .config import VIEWS_DIR
from .logger import log_to_file
from .mapping import WikiProjectConfig
from .utils import format_date, previous_month_range, uc_first
from .wiki_repository import WikiRepository


class ReportUpdater:
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

        # Dates for the previous month, mirroring PHP's
        # strtotime('first day of previous month') / ('last day of previous month').
        # NOTE: dont use date.today(), use datetime.now(timezone.utc).date() instead to avoid timezone issues.
        self.start, self.end = previous_month_range(datetime.now(timezone.utc).date())

        self.env = Environment(loader=FileSystemLoader(str(VIEWS_DIR)))
        self._register_template_helpers()

    def _register_template_helpers(self) -> None:
        self.env.filters["ucfirst"] = uc_first
        self.env.filters["date"] = format_date

        def _msg(key: str, params: list | None = None) -> str:
            return self.i18n.msg(key, params or [])

        self.env.globals["msg"] = _msg  # type: ignore[assignment]
        self.env.globals["assessments"] = self._assessments  # type: ignore[assignment]

    def _assessments(self, type_: str, value: str) -> dict:
        _config = self.wiki_repository.get_assessment_config()
        dataset = _config[type_]

        for key, values in dataset.items():
            if value.lower() == key.lower():
                return values

        return dataset["Unknown"]

    # ---------------------------------------------------
    # Execution
    # ---------------------------------------------------
    async def update_reports(self, config: list[WikiProjectConfig]) -> None:
        """
        Update popular pages reports. Primary async execution point.

        :param config: The JSON config from the wiki page.
        """
        # Make sure config isn't empty.
        if not config:
            log_to_file("Error: Invalid config. Aborting!", self.wiki)
            return

        try:
            for project in config:
                if not self.validate_project_config(project.project_main_page, project):
                    continue

                await self.process_project(project.project_main_page, project)

                log_to_file(f"Finished processing: {project.Name}", self.wiki)

            # Update index page.
            self.update_index()
        finally:
            # Release the per-run Pageviews HTTP client (async context).
            await self.wiki_repository.pageviews_repo.aclose()

    async def process_project(self, project: str, config: dict | WikiProjectConfig) -> None:
        """
        Process an individual WikiProject and update its popular pages report.

        :param project: WikiProject key/title.
        :param config: As specified in the on-wiki JSON config.
        """
        if isinstance(config, dict):
            config = WikiProjectConfig.from_json(project, data=config)

        page_rows = self.wiki_repository.get_project_pages(config.Name)

        if not page_rows:
            log_to_file(f'No pages found for "{project}"', self.wiki)
            return

        # See T164178: guard against runaway memory for very large projects.
        if len(page_rows) > 1_000_000:
            log_to_file(f"Error: {project} is too large. Skipping.", self.wiki)
            return

        start_date = self.start.strftime("%Y%m%d00")
        end_date = self.end.strftime("%Y%m%d00")

        data, total_views = await self.wiki_repository.get_monthly_pageviews_and_assessments(
            page_rows,
            start_date,
            end_date,
            config.Limit,
        )

        days_in_month = (self.end - self.start).days + 1

        # Add in averages.
        for datum in data.values():
            datum["avgPageviews"] = datum["pageviews"] // days_in_month

        has_lead_section = self.wiki_repository.has_lead_section(config.Report)

        # Generate and return wikitext.
        render_argv = {
            "hasLeadSection": has_lead_section,
            "wiki": self.wiki,
            "start": self.start,
            "end": self.end,
            "project": project,
            "pages": data,
            "totalViews": total_views,
            "category": self.wiki_repository.get_wiki_config()["category"],
        }
        output = self.env.get_template("report.wikitext.jinja").render(render_argv)

        section_number = 1 if has_lead_section else None

        self.wiki_repository.set_text(
            config.Report,
            output,
            self.i18n.msg("edit-summary"),
            section_number=section_number,
        )

    def update_index(self) -> None:
        """
        Update the index page listing each WikiProject, its report,
        and when it was last updated."""
        log_to_file("Updating index page", self.wiki)

        list_config_obj = self.retrieve_project_updates()

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
        )

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

        last_edits = self.wiki_repository.get_projects_with_last_bot_timestamp()

        # Add the last updated date to the config.
        last_edits_times = {
            projects_config[row["page_title"]]: row["rev_timestamp"]
            for row in last_edits
            if row["page_title"] in projects_config
        }

        for x in list_config_obj:
            if x.project_main_page in last_edits_times:
                rev_date = last_edits_times[x.project_main_page]
                # rev_timestamp from the DB is YYYYMMDDHHMMSS.
                parsed = datetime.strptime(str(rev_date), "%Y%m%d%H%M%S")
                x.Updated = parsed.strftime("%Y-%m-%d")

        return list_config_obj

    def validate_project_config(self, project: str, config: dict | WikiProjectConfig) -> bool:
        """
        Validate a WikiProject config entry: required keys, target
        namespace, and target page existence.

        :return: True if valid, else False (with the reason logged).
        """
        if isinstance(config, dict):
            config = WikiProjectConfig.from_json(project, data=config)

        if config.is_incomplete():
            log_to_file(f"Error: Incomplete data in config for {config.project_main_page}. Skipping.", self.wiki)
            return False

        # Don't allow writing the report to the main namespace. There's no easy way to grab the namespace ID here
        # so just reject titles that don't have a colon in them (matches the PHP heuristic).
        if ":" not in config.Report:
            log_to_file(
                f"Error: {config.project_main_page} is configured to write to the mainspace. Skipping.", self.wiki
            )
            return False

        log_to_file(f"Beginning to process: {config.Name}", self.wiki)

        # Check the project exists.
        if not self.wiki_repository.does_title_exist(config.project_main_page):
            log_to_file(f"Error: Project page for {config.Name} does not exist! Skipping.", self.wiki)
            return False

        return True
