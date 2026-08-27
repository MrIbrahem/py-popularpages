"""Report generation and saving, ported from src/ReportUpdater.php.

Uses Jinja2 in place of Twig for rendering the wikitext report and index
page templates.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from jinja2 import Environment, FileSystemLoader

from .logger import log_to_file
from .wiki_repository import BASE_DIR, WikiRepository

VIEWS_DIR = BASE_DIR / "views"


class ReportUpdater:
    """Creates popular pages reports for one or more WikiProjects on a wiki."""

    def __init__(self, wiki: str = "en.wikipedia", dry_run: bool = False):
        self.wiki_repository = WikiRepository(wiki, dry_run)
        self.wiki = wiki
        self.i18n = self.wiki_repository.i18n

        # Set dates for the previous month.
        self.start, self.end = self.previous_month_range(datetime.now())

        self.env = Environment(loader=FileSystemLoader(str(VIEWS_DIR)))
        self._register_template_helpers()

    @staticmethod
    def previous_month_range(today: datetime) -> tuple[datetime, datetime]:
        """Return (first, last) day of the month preceding ``today``.

        Python has no ``strtotime('first day of previous month')`` equivalent,
        so compute it manually. Verified against year boundaries.
        """
        first_of_this_month = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_end = first_of_this_month - timedelta(days=1)
        start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = last_month_end.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, end

    def _register_template_helpers(self) -> None:
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

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    async def update_reports(self, config: dict) -> None:
        # Make sure config isn't empty.
        if not config:
            log_to_file("Error: Invalid config. Aborting!", self.wiki)
            return

        for project, project_config in config.items():
            if not self.validate_project_config(project, project_config):
                continue

            await self.process_project(project, project_config)
            log_to_file(f"Finished processing: {project_config['Name']}", self.wiki)

        # Update index page.
        self.update_index()

    async def process_project(self, project: str, config: dict) -> None:
        page_rows = self.wiki_repository.get_project_pages(config["Name"])

        if not page_rows:
            log_to_file(f'No pages found for "{project}"', self.wiki)
            return

        # See T164178: guard against runaway memory for very large projects.
        if len(page_rows) > 1_000_000:
            log_to_file(f"Error: {project} is too large. Skipping.", self.wiki)
            return

        start_date = self.start.strftime("%Y%m%d") + "00"
        end_date = self.end.strftime("%Y%m%d") + "00"

        data, total_views = await self.wiki_repository.get_monthly_pageviews_and_assessments(
            page_rows, start_date, end_date, config["Limit"]
        )

        days_in_month = (self.end - self.start).days + 1

        # Add in averages.
        for datum in data.values():
            datum["avgPageviews"] = datum["pageviews"] // days_in_month

        has_lead_section = self.wiki_repository.has_lead_section(config["Report"])

        # Generate and return wikitext.
        output = self.env.render(
            "report.wikitext.jinja",
            {
                "hasLeadSection": has_lead_section,
                "wiki": self.wiki,
                "start": self.start,
                "end": self.end,
                "project": project,
                "pages": data,
                "totalViews": total_views,
                "category": self.wiki_repository.get_wiki_config()["category"],
            },
        )

        self.wiki_repository.set_text(
            config["Report"],
            output,
            self.i18n.msg("edit-summary"),
            has_lead_section,
        )

    def update_index(self) -> None:
        log_to_file("Updating index page", self.wiki)

        projects_config = self.wiki_repository.get_json_config()
        last_edits = self.wiki_repository.get_projects_with_last_bot_timestamp()

        # Add the last updated date to the config.
        for row in last_edits:
            projects_config[row["name"]]["Updated"] = datetime.strptime(row["rev_timestamp"], "%Y%m%d%H%M%S").strftime(
                "%Y-%m-%d"
            )

        # Generate and return wikitext.
        output = self.env.render(
            "index.wikitext.jinja",
            {
                "projects": projects_config,
                "configPage": self.wiki_repository.get_wiki_config()["config"],
            },
        )

        self.wiki_repository.set_text(
            self.wiki_repository.get_wiki_config()["index"],
            output,
            self.i18n.msg("edit-summary"),
        )

    def validate_project_config(self, project: str, config: dict) -> bool:
        # Check that config values are set.
        if not all(k in config for k in ("Name", "Limit", "Report")):
            log_to_file(f"Error: Incomplete data in config for {project}. Skipping.", self.wiki)
            return False

        # Don't allow writing report to main namespace. There's no easy way to
        # grab the namespace ID, so just reject titles without a colon.
        if ":" not in config["Report"]:
            log_to_file(
                f"Error: {project} is configured to write to the mainspace. Skipping.",
                self.wiki,
            )
            return False

        log_to_file(f"Beginning to process: {config['Name']}", self.wiki)

        # Check the project exists.
        if not self.wiki_repository.does_title_exist(project):
            log_to_file(
                f"Error: Project page for {config['Name']} does not exist! Skipping.",
                self.wiki,
            )
            return False

        return True
