"""
Report generation and saving, ported from src/ReportUpdater.php.

Uses Jinja2 in place of Twig for rendering the wikitext report and index
page templates.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader

from ..config import app_config
from ..logger import log_to_file
from ..mapping import WikiProjectConfig
from ..pageviews.pageviews_cache import PageviewsCache
from ..utils import format_date, previous_month_range, uc_first
from ..wiki_repository import WikiRepository

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

    @staticmethod
    def _resolve_assessment(config: dict, type_: str, value: str) -> dict:
        """
        Resolve a class/importance value to its display config (color, category).

        Pure function over an already-fetched assessment config dict; performs
        no I/O so it is safe to call from the render path.
        """
        default_result = {"name": "Unknown", "color": "gray", "category": "unknown"}
        if not config:
            return default_result

        dataset = config.get(type_)

        if not dataset:
            return default_result

        value = value or ""

        for key, values in dataset.items():
            if value.lower() == key.lower():
                return values

        return dataset.get("Unknown", default_result)

    @staticmethod
    def _titles_for_pages(page_rows: list[dict]) -> set[str]:
        """
        Extract the unique target/redirect titles referenced by page_rows.

        Mirrors the title-collection logic previously done in update_reports'
        cross-project Phase 1, but scoped to a single project's page rows.
        """
        titles: set[str] = set()
        for row in page_rows:
            target = (row["page_title"] or "").replace("_", " ")
            redir = (row["redir_title"] or "").replace("_", " ")
            if target:
                titles.add(target)
            if redir:
                titles.add(redir)
        return titles

    async def process_project(
        self,
        project: str,
        config: dict | WikiProjectConfig,
        cache: PageviewsCache | None = None,
        page_rows: list[dict] | None = None,
    ) -> None:
        """
        Process a WikiProject and update its monthly popular-pages report.

        Parameters:
            project (str): WikiProject key or title.
            config (dict | WikiProjectConfig): WikiProject report configuration.
            cache (PageviewsCache | None): Shared pageview cache, if available.
            page_rows (list[dict] | None): Pre-fetched project pages with assessments and redirects.
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
        if len(page_rows) > app_config.wiki.max_project_size:
            log_to_file(f"Error: {project} is too large. Skipping.", self.wiki)
            return

        if cache is not None:
            data, total_views = await self._views_for_project_from_cache(page_rows, config.Limit, cache)
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
        self.populate_assessment_categories(data, days_in_month)

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

    def populate_assessment_categories(self, data: dict[str, dict], days_in_month) -> dict[str, dict]:
        for datum in data.values():
            datum["avgPageviews"] = datum["pageviews"] // days_in_month

        # Resolve assessment colors/categories here, in the data-fetch phase, so
        # the template performs no network I/O (issue #4: Jinja templates must
        # not make network requests). `get_assessment_config()` is fetched once
        # and cached on the WikiRepository, so this is a single network call per
        # run, reused across every page and project.
        assessment_cfg = self.wiki_repository.get_assessment_config()

        for datum in data.values():
            datum["class_assessment"] = self._resolve_assessment(assessment_cfg, "class", datum["class"])
            datum["importance_assessment"] = self._resolve_assessment(assessment_cfg, "importance", datum["importance"])

        return data

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

    async def _build_views_cache(self, projects: list[WikiProjectConfig], all_titles: set[str]) -> PageviewsCache:
        """
        Build a :class:`PageviewsCache` for this wiki's reporting month and fetch
        every unique title across ``projects`` exactly once.

        Results are persisted to ``data/views/<wiki>/<YYYY-MM>.jsonl`` (see the
        plan doc), so titles fetched in a previous run -- or by a previously
        processed project earlier in this same run -- are reused and not
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

    async def _views_for_project_from_cache(
        self,
        page_rows: list[dict],
        limit: int,
        cache: PageviewsCache,
    ) -> tuple[dict[str, dict], int]:
        """
        Compute per-project pageviews from the shared :class:`PageviewsCache`.

        Mirrors the sort/truncate/total semantics of
        ``WikiRepository.get_monthly_pageviews_and_assessments``, but reads
        already-fetched (and persisted) view counts from ``cache`` instead of
        hitting the Pageviews API. A shared article is therefore counted once
        per project that references it while having been fetched only once for
        the whole wiki (or read back from the on-disk cache if a previously
        processed project in this run already fetched it).

        :param page_rows: Rows as returned by ``get_project_pages()``.
        :param limit: Max number of pages to include in the final report.
        :param cache: The shared pageviews cache.
        :return: (pages_dict, total_pageviews).
        """
        _t0 = time.perf_counter()
        logger.info("Fetching pageviews for %d page(s), limit: %d", len(page_rows), limit)
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

        # Bio-like projects can have >900,000 titles. A per-target
        # `get_views` call would mean >900,000 separate SQLite queries; instead
        # resolve every unique title across all targets + redirects in a few
        # chunked queries that share one session, then aggregate back per target.
        counts = cache.get_views_many(list(out), redirects)
        for target, count in counts.items():
            out[target]["pageviews"] = count
            total_pageviews += count

        out = self.wiki_repository._sort_and_truncate_pages_list(out, limit)

        _elapsed = time.perf_counter() - _t0
        logger.info(
            "took %.4f s for %d page(s), limit: %d",
            _elapsed,
            len(page_rows),
            limit,
        )
        return out, total_pageviews

    # ---------------------------------------------------
    # Public API
    # ---------------------------------------------------

    async def update_reports(self, config: list[WikiProjectConfig]) -> None:
        """
        Generate and save popular-page reports for the configured WikiProjects,
        then update the index.

        Projects are processed one at a time -- validate, fetch pages, build a
        pageviews cache scoped to that project's titles, render, save, then
        discard -- instead of loading all projects' page data into memory up
        front. With several hundred stale projects, batching everything before
        rendering any report caused OOM kills; this keeps peak memory bounded
        to roughly one project's worth of data (itself capped by
        `app_config.wiki.max_project_size`).

        Parameters:
            config (list[WikiProjectConfig]): WikiProject configurations to process. An empty list aborts the update.
        """
        # Make sure config isn't empty.
        if not config:
            log_to_file("Error: Invalid config. Aborting!", self.wiki)
            return

        try:
            logger.info("update_reports: processing %d project(s) sequentially", len(config))

            processed = 0
            skipped = 0

            for project in config:
                if not self.validate_project_config(project.project_main_page, project):
                    skipped += 1
                    continue

                page_rows = self.wiki_repository.get_project_pages(project.Name)
                if not page_rows:
                    log_to_file(f'No pages found for "{project.project_main_page}"', self.wiki)
                    skipped += 1
                    continue

                # See T164178: guard against runaway memory for very large projects.
                if len(page_rows) > app_config.wiki.max_project_size:
                    log_to_file(f"Error: {project.project_main_page} is too large. Skipping.", self.wiki)
                    skipped += 1
                    continue

                cache = None
                try:
                    # Build a pageviews cache scoped to *this project's* titles
                    # only (instead of accumulating titles across all stale
                    # projects before fetching anything).
                    titles = self._titles_for_pages(page_rows)
                    cache = await self._build_views_cache([project], titles)

                    logger.info("Processing project '%s'", project.Name)
                    await self.process_project(
                        project=project.project_main_page,
                        config=project,
                        cache=cache,
                        page_rows=page_rows,
                    )
                    log_to_file(f"Finished processing: {project.Name}", self.wiki)
                    processed += 1
                except Exception as exc:
                    # One project failing must not abort the whole run (mirrors
                    # the per-wiki isolation in check_reports.py, but at the
                    # per-project level).
                    logger.exception("Error processing project '%s': %s", project.Name, exc)
                    log_to_file(f"Error processing {project.Name}: {exc}", self.wiki)
                    skipped += 1
                finally:
                    # Close the per-project SQLite cache so its engine/file
                    # handle is released before the next iteration; otherwise
                    # open SQLite handles accumulate across project iterations.
                    if cache is not None:
                        cache.close()
                    # Drop references so this project's page/pageview data is
                    # eligible for GC before the next iteration allocates more,
                    # rather than living until the whole batch finishes.
                    page_rows = None
                    cache = None

            logger.info("update_reports: done (%d processed, %d skipped)", processed, skipped)

            # NOTE: update index moved into IndexUpdater
            # self.update_index()
        finally:
            # Release the per-run Pageviews HTTP client (async context).
            await self.wiki_repository.pageviews_repo.aclose()


__all__ = [
    "ReportUpdater",
]
