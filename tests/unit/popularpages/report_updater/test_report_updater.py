"""
Tests for src.py_port.popularpages.report_updater.ReportUpdater (report generation)

The real WikiRepository performs network/DB I/O, so it is replaced with a
MagicMock via monkeypatch. We exercise the orchestration and rendering paths:
process_project (with and without a cache), update_reports, validate_project_config,
the assessment resolver
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import src.py_port.popularpages.report_updater.updater as ru_module
from src.py_port.generate_report import ReportUpdater
from src.py_port.popularpages.i18n import I18n
from src.py_port.popularpages.mapping import WikiProjectConfig
from src.py_port.popularpages.wiki_repository import WikiRepository


@pytest.fixture
def updater(monkeypatch) -> tuple[ReportUpdater, MagicMock]:
    """Create a configured `ReportUpdater` and mocked wiki repository for tests.

    Args:
        monkeypatch: Pytest fixture used to replace repository and configuration dependencies.

    Returns:
        A tuple containing the configured `ReportUpdater` and its mocked repository.
    """
    repo = MagicMock()
    repo.i18n = I18n("en")
    repo.pageviews_repo = AsyncMock()

    # PageviewsCache.ensure exercises the pageviews client; make it return nothing.
    repo.pageviews_repo.get_title_views = AsyncMock(return_value={})

    # process_project delegates sorting to the real static method.
    repo._sort_and_truncate_pages_list.side_effect = WikiRepository._sort_and_truncate_pages_list
    repo.get_wiki_config.return_value = {
        "category": "Category:WikiProject popular pages",
        "config": "Wikipedia:WikiProject/Popular pages",
        "index": "Wikipedia:WikiProject/Popular pages/Index",
    }
    repo.get_assessment_config.return_value = {
        "class": {
            "FA": {"color": "#fa", "category": "C-FA"},
            "Unknown": {"color": "#unknown", "category": "C-Unknown"},
        },
        "importance": {
            "Top": {"color": "#top", "category": "I-Top"},
            "Unknown": {"color": "#unknown", "category": "I-Unknown"},
        },
    }
    repo.has_lead_section.return_value = True
    repo.does_title_exist.return_value = True
    repo.get_json_config.return_value = {}

    # Replace the real WikiRepository with our fake.
    monkeypatch.setattr(ru_module, "WikiRepository", lambda *a, **k: repo)

    u = ru_module.ReportUpdater("en.wikipedia", dry_run=True)

    return u, repo


def _project(report="Wikipedia:WikiProject Foo/Popular pages", name="Foo", limit="10"):
    """Build a wiki project configuration for the specified report, project name, and page limit."""
    return WikiProjectConfig.from_json(
        "Wikipedia:WikiProject Foo",
        data={"Report": report, "Limit": limit, "Name": name},
    )


# ---------------------------------------------------
# _resolve_assessment
# ---------------------------------------------------
class TestResolveAssessment:
    """Tests for the `_resolve_assessment` method of the `ReportUpdater` class."""

    def test_resolve_assessment_found(self):
        assessment = {"class": {"FA": {"color": "#fa", "category": "C-FA"}}, "importance": {}}
        resolved = ru_module.ReportUpdater._resolve_assessment(assessment, "class", "FA")
        assert resolved["category"] == "C-FA"

    def test_resolve_assessment_falls_back_to_unknown(self):
        assessment = {
            "class": {"FA": {"color": "#fa", "category": "C-FA"}, "Unknown": {"color": "#u", "category": "C-Unknown"}},
            "importance": {},
        }
        resolved = ru_module.ReportUpdater._resolve_assessment(assessment, "class", "Nope")
        assert resolved["category"] == "C-Unknown"


# ---------------------------------------------------
# validate_project_config
# ---------------------------------------------------
class TestValidateProjectConfig:
    """Tests for the `validate_project_config` method of the `ReportUpdater` class."""

    def test_validate_project_config_valid(self, updater: tuple[ReportUpdater, MagicMock]) -> None:
        u, repo = updater
        repo.does_title_exist.return_value = True
        assert u.validate_project_config("Wikipedia:WikiProject Foo", _project()) is True

    def test_validate_project_config_incomplete(self, updater: tuple[ReportUpdater, MagicMock]) -> None:
        u, repo = updater
        incomplete = WikiProjectConfig(
            project_main_page="Wikipedia:WikiProject Foo",
            Report="Wikipedia:WikiProject Foo/Popular pages",
            report_without_ns="Wikipedia:WikiProject_Foo/Popular_pages",
            Limit="10",  # pyright: ignore[reportArgumentType]
            Name="",
        )
        assert u.validate_project_config("Wikipedia:WikiProject Foo", incomplete) is False

    def test_validate_project_config_rejects_mainspace_report(self, updater: tuple[ReportUpdater, MagicMock]) -> None:
        u, repo = updater
        repo.does_title_exist.return_value = True
        mainspace = _project(report="Mainspace report")
        assert u.validate_project_config("Wikipedia:WikiProject Foo", mainspace) is False

    def test_validate_project_config_rejects_missing_project_page(
        self, updater: tuple[ReportUpdater, MagicMock]
    ) -> None:
        u, repo = updater
        repo.does_title_exist.return_value = False
        assert u.validate_project_config("Wikipedia:WikiProject Foo", _project()) is False


# ---------------------------------------------------
# process_project
# ---------------------------------------------------
class TestProcessProject:
    """Tests for the `process_project` method of the `ReportUpdater` class."""

    @pytest.mark.asyncio
    async def test_process_project_renders_report_from_cache(self, updater: tuple[ReportUpdater, MagicMock]):
        u, repo = updater
        cache = MagicMock()
        # The updater reads already-fetched counts via `get_views_many`
        # (per-title `get_views` would mean one query per page).
        cache.db.get_views_many.return_value = {"Foo bar": 42}
        page_rows = [
            {
                "page_title": "Foo bar",
                "redir_title": "",
                "pa_class": "Unknown",
                "pa_importance": "Unknown",
            }
        ]
        await u.process_project(
            project="Wikipedia:WikiProject Foo",
            config=_project(),
            cache=cache,
            page_rows=page_rows,
        )
        repo.set_text.assert_called_once()
        written_page, written_text = repo.set_text.call_args.args[0], repo.set_text.call_args.args[1]
        assert written_page == "Wikipedia:WikiProject Foo/Popular pages"
        assert "Foo bar" in written_text
        assert "42" in written_text

    @pytest.mark.asyncio
    async def test_process_project_empty_rows_returns_early(self, updater: tuple[ReportUpdater, MagicMock]) -> None:
        u, repo = updater
        cache = MagicMock()
        await u.process_project(
            project="Wikipedia:WikiProject Foo",
            config=_project(),
            cache=cache,
            page_rows=[],
        )
        repo.set_text.assert_not_called()
        cache.db.get_views_many.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_project_without_cache_fetches_pageviews(
        self, updater: tuple[ReportUpdater, MagicMock]
    ) -> None:
        u, repo = updater
        repo.get_monthly_pageviews_and_assessments = AsyncMock(return_value={"Foo bar": 7})
        page_rows = [
            {
                "page_title": "Foo bar",
                "redir_title": "",
                "pa_class": "Unknown",
                "pa_importance": "Unknown",
            }
        ]
        await u.process_project(
            project="Wikipedia:WikiProject Foo",
            config=_project(),
            page_rows=page_rows,
        )
        repo.set_text.assert_called_once()
        assert "Foo bar" in repo.set_text.call_args.args[1]

    @pytest.mark.asyncio
    async def test_process_project_accepts_dict_config(self, updater: tuple[ReportUpdater, MagicMock]) -> None:
        u, repo = updater
        cache = MagicMock()
        cache.db.get_views_many.return_value = {}
        page_rows = [
            {
                "page_title": "Foo bar",
                "redir_title": "",
                "pa_class": "",
                "pa_importance": "",
            }
        ]
        await u.process_project(
            project="Wikipedia:WikiProject Foo",
            config={"Report": "Wikipedia:WikiProject Foo/Popular pages", "Limit": "10", "Name": "Foo"},
            cache=cache,
            page_rows=page_rows,
        )
        repo.set_text.assert_called_once()


# ---------------------------------------------------
# update_reports (full pipeline)
# ---------------------------------------------------
class TestUpdateReports:
    """Tests for the `update_reports` method of the `ReportUpdater` class."""

    @pytest.mark.asyncio
    async def test_update_reports_runs_pipeline(self, updater: tuple[ReportUpdater, MagicMock]):
        u, repo = updater
        page_rows = [
            {
                "page_title": "Foo bar",
                "redir_title": "",
                "pa_class": "Unknown",
                "pa_importance": "Unknown",
            }
        ]
        repo.db.get_project_pages.return_value = page_rows
        # _views_for_project_from_cache reads from cache.db.get_views_many, not the
        # API; provide an empty result so the report renders with 0 views.
        repo.get_json_config.return_value = {
            "Wikipedia:WikiProject Foo": {
                "Report": "Wikipedia:WikiProject Foo/Popular pages",
                "Limit": "10",
                "Name": "Foo",
            }
        }
        repo.get_projects_with_last_bot_timestamp.return_value = []
        repo.set_text.return_value = None

        await u.update_reports([_project()])

        assert repo.set_text.call_count == 1
        repo.pageviews_repo.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_reports_skips_invalid_project(self, updater: tuple[ReportUpdater, MagicMock]) -> None:
        u, repo = updater
        repo.does_title_exist.return_value = False
        repo.get_json_config.return_value = {
            "Wikipedia:WikiProject Foo": {
                "Report": "Wikipedia:WikiProject Foo/Popular pages",
                "Limit": "10",
                "Name": "Foo",
            }
        }
        repo.get_projects_with_last_bot_timestamp.return_value = []
        await u.update_reports([_project()])

        repo.set_text.assert_not_called()
