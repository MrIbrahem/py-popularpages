"""
Report generation and saving, ported from src/ReportUpdater.php.

Uses Jinja2 in place of Twig for rendering the wikitext report and index
page templates.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader

from .config import config
from .logger import log_to_file
from .mapping import WikiProjectConfig
from .pageviews_cache import PageviewsCache
from .utils import format_date, previous_month_range, uc_first
from .wiki_repository import WikiRepository

logger = logging.getLogger(__name__)


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
        logger.info("ReportUpdater initialized for wiki '%s' (dry_run=%s)", wiki, dry_run)

        # Dates for the previous month, mirroring PHP's
        # strtotime('first day of previous month') / ('last day of previous month').
        # NOTE: dont use date.today(), use datetime.now(timezone.utc).date() instead to avoid timezone issues.
        self.start, self.end = previous_month_range(datetime.now(timezone.utc).date())

        self.env = Environment(loader=FileSystemLoader(str(config.paths.views_dir)))
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

    @staticmethod
    def _resolve_assessment(config: dict, type_: str, value: str) -> dict:
        """
        Resolve a class/importance value to its display config (color, category).

        Pure function over an already-fetched assessment config dict; performs
        no I/O so it is safe to call from the render path.
        """
        dataset = config[type_]
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
            logger.info("update_reports: processing %d project(s)", len(config))

            # --- Phase 1: validate + gather titles (single DB pass per project) ---
            valid_projects: list[WikiProjectConfig] = []
            project_pages: dict[str, list[dict]] = {}
            all_titles: set[str] = set()
            for project in config:
                if not self.validate_project_config(project.project_main_page, project):
                    continue

                page_rows = self.wiki_repository.get_project_pages(project.Name)
                if not page_rows:
                    log_to_file(f'No pages found for "{project.project_main_page}"', self.wiki)
                    continue

                # See T164178: guard against runaway memory for very large projects.
                if len(page_rows) > config.wiki.max_project_size:
                    log_to_file(f"Error: {project.project_main_page} is too large. Skipping.", self.wiki)
                    continue

                project_pages[project.project_main_page] = page_rows
                valid_projects.append(project)

                for row in page_rows:
                    target = (row["page_title"] or "").replace("_", " ")
                    redir = (row["redir_title"] or "").replace("_", " ")
                    if target:
                        all_titles.add(target)
                    if redir:
                        all_titles.add(redir)

            # --- Phase 2: fetch pageviews once per unique title (cross-project) ---
            cache = await self._build_views_cache(valid_projects, all_titles)

            # --- Phase 3: render + save each project from the shared cache ---
            for project in valid_projects:
                logger.info("Processing project '%s'", project.Name)
                await self.process_project(
                    project.project_main_page,
                    project,
                    cache=cache,
                    page_rows=project_pages[project.project_main_page],
                )
                log_to_file(f"Finished processing: {project.Name}", self.wiki)

            # Update index page.
            self.update_index()
        finally:
            # Release the per-run Pageviews HTTP client (async context).
            await self.wiki_repository.pageviews_repo.aclose()

    async def _build_views_cache(
        self, projects: list[WikiProjectConfig], all_titles: set[str]
    ) -> PageviewsCache:
        """
        Build a :class:`PageviewsCache` for this wiki's reporting month and fetch
        every unique title across ``projects`` exactly once.

        Results are persisted to ``data/views/<wiki>/<YYYY-MM>.jsonl`` (see the
        plan doc), so titles fetched in a previous run are reused and not
        dropped when the task finishes.
        """
        year_month = self.start.strftime("%Y-%m")
        cache = PageviewsCache(self.wiki, year_month, self.wiki_repository.pageviews_repo)

        start_date = self.start.strftime("%Y%m%d00")
        end_date = self.end.strftime("%Y%m%d00")
        logger.info(
            "Building pageviews cache for %d project(s); %d unique title(s) (window %s..%s)",
            len(projects),
            len(all_titles),
            start_date,
            end_date,
        )
        await cache.ensure(all_titles, start_date, end_date)
        return cache

    async def process_project(
        self,
        project: str,
        config: dict | WikiProjectConfig,
        cache: PageviewsCache | None = None,
        page_rows: list[dict] | None = None,
    ) -> None:
        """
        Process an individual WikiProject and update its popular pages report.

        :param project: WikiProject key/title.
        :param config: As specified in the on-wiki JSON config.
        :param cache: Optional :class:`PageviewsCache`. When provided, pageviews
            are read from the shared, persisted cache instead of being fetched
            per-project from the Pageviews API (the default path when invoked via
            ``update_reports``).
        :param page_rows: Optional pre-fetched page rows (targets + assessments
            + redirects). When provided, avoids a second DB query.
        """
        if isinstance(config, dict):
            config = WikiProjectConfig.from_json(project, data=config)

        logger.info("Process project '%s' (config report='%s')", config.Name, config.Report)
        if page_rows is None:
            page_rows = self.wiki_repository.get_project_pages(config.Name)
        logger.debug("Fetched %d page(s) for project '%s'", len(page_rows), config.Name)

        if not page_rows:
            log_to_file(f'No pages found for "{project}"', self.wiki)
            return

        # See T164178: guard against runaway memory for very large projects.
        if len(page_rows) > config.wiki.max_project_size:
            log_to_file(f"Error: {project} is too large. Skipping.", self.wiki)
            return

        if cache is not None:
            data, total_views = await self._views_for_project_from_cache(
                page_rows, config.Limit, cache
            )
        else:
            start_date = self.start.strftime("%Y%m%d00")
            end_date = self.end.strftime("%Y%m%d00")
            logger.debug("Pageviews window: start=%s end=%s", start_date, end_date)

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

        # Resolve assessment colors/categories here, in the data-fetch phase, so
        # the template performs no network I/O (issue #4: Jinja templates must
        # not make network requests). `get_assessment_config()` is fetched once
        # and cached on the WikiRepository, so this is a single network call per
        # run, reused across every page and project.
        assessment_cfg = self.wiki_repository.get_assessment_config()
        for datum in data.values():
            datum["class_assessment"] = self._resolve_assessment(
                assessment_cfg, "class", datum["class"]
            )
            datum["importance_assessment"] = self._resolve_assessment(
                assessment_cfg, "importance", datum["importance"]
            )

        has_lead_section = self.wiki_repository.has_lead_section(config.Report)
        logger.debug("Report has lead section: %s", has_lead_section)

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

    async def _views_for_project_from_cache(
        self, page_rows: list[dict], limit: int, cache: PageviewsCache
    ) -> tuple[dict, int]:
        """
        Compute per-project pageviews from the shared :class:`PageviewsCache`.

        Mirrors the sort/truncate/total semantics of
        ``WikiRepository.get_monthly_pageviews_and_assessments``, but reads
        already-fetched (and persisted) view counts from ``cache`` instead of
        hitting the Pageviews API. A shared article is therefore counted once
        per project that references it while having been fetched only once for
        the whole wiki.

        :param page_rows: Rows as returned by ``get_project_pages()``.
        :param limit: Max number of pages to include in the final report.
        :param cache: The shared pageviews cache.
        :return: (pages_dict, total_pageviews).
        """
        unknown_msg = self.i18n.msg("unknown")
        out: dict[str, dict] = {}
        redirects: dict[str, list[str]] = {}
        total_pageviews = 0

        for row in page_rows:
            target = (row["page_title"] or "").replace("_", " ")
            redir = (row["redir_title"] or "").replace("_", " ")
            if target not in out:
                out[target] = {
                    "pageviews": 0,
                    "class": row["pa_class"] or unknown_msg,
                    "importance": row["pa_importance"] or unknown_msg,
                }
                redirects[target] = []
            if redir:
                redirects[target].append(redir)

        for target in out:
            count = cache.get(target, redirects[target])
            out[target]["pageviews"] = count
            total_pageviews += count

        return self.wiki_repository._sort_and_truncate_pages_list(out, limit), total_pageviews

    def update_index(self) -> None:
        """
        Update the index page listing each WikiProject, its report,
        and when it was last updated."""
        logger.info("Updating index page for wiki '%s'", self.wiki)
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
        logger.debug("Retrieved %d project config(s)", len(list_config_obj))

        last_edits = self.wiki_repository.get_projects_with_last_bot_timestamp()
        logger.debug("Retrieved %d last-edit timestamp(s)", len(last_edits))

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
        logger.debug("Validating project config for '%s'", project)
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
