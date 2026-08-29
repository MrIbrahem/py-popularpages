"""
Tests for src.popularpages.report_updater.ReportUpdater.

The real WikiRepository performs network/DB I/O, so it is replaced with a
MagicMock via monkeypatch. We exercise the orchestration and rendering paths:
process_project (with and without a cache), update_reports, update_index,
validate_project_config, and the assessment resolver.
"""

import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.popularpages.config as cfg
import src.popularpages.report_updater.updater as ru_module
from src.generate_report import ReportUpdater
from src.popularpages.i18n import I18n
from src.popularpages.mapping import WikiProjectConfig
from src.popularpages.wiki_repository import WikiRepository


@pytest.fixture
def updater(tmp_path, monkeypatch):
    """Create a configured `ReportUpdater` and mocked wiki repository for tests.

    Parameters:
        tmp_path: Temporary directory used for the pageviews cache.
        monkeypatch: Pytest fixture used to replace repository and configuration dependencies.

    Returns:
        A tuple containing the configured `ReportUpdater` and its mocked repository.
    """
    repo = MagicMock()
    repo.i18n = I18n("en")
    repo.pageviews_repo = AsyncMock()
    # _build_views_cache exercises the pageviews client; make it return nothing.
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

    # Redirect the persisted views cache to a temp dir.
    new_cfg = dataclasses.replace(
        cfg.config,
        paths=dataclasses.replace(cfg.config.paths, views_data_dir=tmp_path),
    )
    monkeypatch.setattr("src.popularpages.pageviews.pageviews_cache.config", new_cfg)
    monkeypatch.setattr(ru_module, "config", new_cfg)

    return u, repo


def _project(report="Wikipedia:WikiProject Foo/Popular pages", name="Foo", limit="10"):
    """Build a wiki project configuration for the specified report, project name, and page limit."""
    return WikiProjectConfig.from_json(
        "Wikipedia:WikiProject Foo",
        data={"Report": report, "Limit": limit, "Name": name},
    )


# ---------------------------------------------------------------
# _resolve_assessment
# ---------------------------------------------------------------
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


# ---------------------------------------------------------------
# validate_project_config
# ---------------------------------------------------------------
class TestValidateProjectConfig:
    """Tests for the `validate_project_config` method of the `ReportUpdater` class."""

    def test_validate_project_config_valid(self, updater):
        u, repo = updater
        repo.does_title_exist.return_value = True
        assert u.validate_project_config("Wikipedia:WikiProject Foo", _project()) is True

    def test_validate_project_config_incomplete(self, updater):
        u, repo = updater
        incomplete = WikiProjectConfig(
            project_main_page="Wikipedia:WikiProject Foo",
            Report="Wikipedia:WikiProject Foo/Popular pages",
            report_without_ns="Wikipedia:WikiProject_Foo/Popular_pages",
            Limit="10",  # pyright: ignore[reportArgumentType]
            Name="",
        )
        assert u.validate_project_config("Wikipedia:WikiProject Foo", incomplete) is False

    def test_validate_project_config_rejects_mainspace_report(self, updater):
        u, repo = updater
        repo.does_title_exist.return_value = True
        mainspace = _project(report="Mainspace report")
        assert u.validate_project_config("Wikipedia:WikiProject Foo", mainspace) is False

    def test_validate_project_config_rejects_missing_project_page(self, updater):
        u, repo = updater
        repo.does_title_exist.return_value = False
        assert u.validate_project_config("Wikipedia:WikiProject Foo", _project()) is False


# ---------------------------------------------------------------
# process_project
# ---------------------------------------------------------------
class TestProcessProject:
    """Tests for the `process_project` method of the `ReportUpdater` class."""

    @pytest.mark.asyncio
    async def test_process_project_renders_report_from_cache(self, updater):
        u, repo = updater
        cache = MagicMock()
        cache.get.return_value = 42
        page_rows = [
            {
                "page_title": "Foo_bar",
                "redir_title": "",
                "pa_class": "Unknown",
                "pa_importance": "Unknown",
            }
        ]
        await u.process_project(
            "Wikipedia:WikiProject Foo",
            _project(),
            cache=cache,
            page_rows=page_rows,
        )
        repo.set_text.assert_called_once()
        written_page, written_text = repo.set_text.call_args.args[0], repo.set_text.call_args.args[1]
        assert written_page == "Wikipedia:WikiProject Foo/Popular pages"
        assert "Foo bar" in written_text
        assert "42" in written_text

    @pytest.mark.asyncio
    async def test_process_project_empty_rows_returns_early(self, updater):
        u, repo = updater
        cache = MagicMock()
        await u.process_project(
            "Wikipedia:WikiProject Foo",
            _project(),
            cache=cache,
            page_rows=[],
        )
        repo.set_text.assert_not_called()
        cache.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_project_without_cache_fetches_pageviews(self, updater):
        u, repo = updater
        repo.get_monthly_pageviews_and_assessments = AsyncMock(
            return_value=(
                {"Foo bar": {"pageviews": 7, "class": "Unknown", "importance": "Unknown"}},
                7,
            )
        )
        page_rows = [
            {
                "page_title": "Foo_bar",
                "redir_title": "",
                "pa_class": "Unknown",
                "pa_importance": "Unknown",
            }
        ]
        await u.process_project(
            "Wikipedia:WikiProject Foo",
            _project(),
            page_rows=page_rows,
        )
        repo.get_monthly_pageviews_and_assessments.assert_awaited_once()
        repo.set_text.assert_called_once()
        assert "Foo bar" in repo.set_text.call_args.args[1]

    @pytest.mark.asyncio
    async def test_process_project_accepts_dict_config(self, updater):
        u, repo = updater
        cache = MagicMock()
        cache.get.return_value = 0
        page_rows = [
            {
                "page_title": "Foo_bar",
                "redir_title": "",
                "pa_class": "",
                "pa_importance": "",
            }
        ]
        await u.process_project(
            "Wikipedia:WikiProject Foo",
            {"Report": "Wikipedia:WikiProject Foo/Popular pages", "Limit": "10", "Name": "Foo"},
            cache=cache,
            page_rows=page_rows,
        )
        repo.set_text.assert_called_once()


# ---------------------------------------------------------------
# update_reports (full pipeline)
# ---------------------------------------------------------------
class TestUpdateReports:
    """Tests for the `update_reports` method of the `ReportUpdater` class."""

    @pytest.mark.asyncio
    async def test_update_reports_runs_pipeline(self, updater: tuple[ReportUpdater, MagicMock]):
        u, repo = updater
        page_rows = [
            {
                "page_title": "Foo_bar",
                "redir_title": "",
                "pa_class": "Unknown",
                "pa_importance": "Unknown",
            }
        ]
        repo.get_project_pages.return_value = page_rows
        repo.get_monthly_pageviews_and_assessments.return_value = ({}, 0)
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

        # One set_text for the report, one for the index page.
        assert repo.set_text.call_count == 2
        repo.pageviews_repo.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_reports_skips_invalid_project(self, updater):
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
        # Invalid project -> no report written, but index still attempted.
        repo.set_text.assert_called_once()  # index page only


# ---------------------------------------------------------------
# update_index
# ---------------------------------------------------------------
class TestUpdateIndex:
    """Tests for the `update_index` method of the `ReportUpdater` class."""

    def test_update_index_renders(self, updater):
        u, repo = updater
        repo.get_json_config.return_value = {
            "Wikipedia:WikiProject Foo": {
                "Report": "Wikipedia:WikiProject Foo/Popular pages",
                "Limit": "10",
                "Name": "Foo",
            }
        }
        repo.get_projects_with_last_bot_timestamp.return_value = []
        repo.get_wiki_config.return_value = {
            "config": "Wikipedia:WikiProject/Popular pages",
            "index": "Wikipedia:WikiProject/Popular pages/Index",
        }
        u.update_index()
        repo.set_text.assert_called_once()
        assert repo.set_text.call_args.kwargs["page_title"] == ("Wikipedia:WikiProject/Popular pages/Index")

    def test_update_index_no_projects(self, updater):
        u, repo = updater
        repo.get_json_config.return_value = {}
        repo.get_projects_with_last_bot_timestamp.return_value = []
        repo.get_wiki_config.return_value = {"config": "X", "index": "Y"}
        u.update_index()
        # No projects -> update_index returns early and writes nothing.
        repo.set_text.assert_not_called()
