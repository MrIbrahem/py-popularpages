"""
Tests for src.popularpages.report_updater.ReportUpdater.

The real WikiRepository performs network/DB I/O, so it is replaced with a
MagicMock via monkeypatch. We exercise the orchestration and rendering paths:
process_project (with and without a cache), update_reports (sequential,
memory-bounded processing), update_index, validate_project_config, and the
assessment resolver.
"""

import dataclasses
import json

import pytest
from unittest.mock import AsyncMock, MagicMock

import src.popularpages.config as cfg
import src.popularpages.report_updater as ru_module
from src.popularpages.i18n import I18n
from src.popularpages.mapping import WikiProjectConfig
from src.popularpages.report_updater import ReportUpdater
from src.popularpages.wiki_repository import WikiRepository


SAMPLE_ASSESSMENT_CONFIG = {
    "class": {
        "Featured article": {"color": "#ff6600", "category": "Category:FA-Class"},
        "A": {"color": "#66ff66", "category": "Category:A-Class"},
        "Unknown": {"color": "#cccccc", "category": "Category:Unknown-Class"},
    },
    "importance": {
        "Top": {"color": "#ff0000", "category": "Category:Top-Importance"},
        "Unknown": {"color": "#cccccc", "category": "Category:Unknown-Importance"},
    },
}


def test_resolve_assessment_exact_match():
    result = ReportUpdater._resolve_assessment(SAMPLE_ASSESSMENT_CONFIG, "class", "Featured article")
    assert result == {"color": "#ff6600", "category": "Category:FA-Class"}


def test_resolve_assessment_case_insensitive():
    result = ReportUpdater._resolve_assessment(SAMPLE_ASSESSMENT_CONFIG, "class", "featured ARTICLE")
    assert result["category"] == "Category:FA-Class"


def test_resolve_assessment_unknown_falls_back():
    result = ReportUpdater._resolve_assessment(SAMPLE_ASSESSMENT_CONFIG, "class", "Something weird")
    assert result == SAMPLE_ASSESSMENT_CONFIG["class"]["Unknown"]


def test_resolve_assessment_importance():
    result = ReportUpdater._resolve_assessment(SAMPLE_ASSESSMENT_CONFIG, "importance", "Top")
    assert result == {"color": "#ff0000", "category": "Category:Top-Importance"}


# ---------------------------------------------------------------
# Fixture + helpers
# ---------------------------------------------------------------
@pytest.fixture
def updater(tmp_path, monkeypatch):
    repo = MagicMock()
    repo.i18n = I18n("en")
    repo.pageviews_repo = AsyncMock()
    # _process_one_project exercises the real PageviewsCache, which in turn
    # calls get_title_views on the (mocked) pageviews client.
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
    monkeypatch.setattr("src.popularpages.pageviews_cache.config", new_cfg)
    monkeypatch.setattr(ru_module, "config", new_cfg)

    return u, repo


def _project(report="Wikipedia:WikiProject Foo/Popular pages", name="Foo", limit="10"):
    return WikiProjectConfig.from_json(
        "Wikipedia:WikiProject Foo",
        data={"Report": report, "Limit": limit, "Name": name},
    )


class _RecordingCache:
    """Test double for PageviewsCache that records ensure() calls.

    Mirrors the real cache's de-duplication semantics: titles already present
    are not re-fetched from the injected repo.
    """

    instances: list["_RecordingCache"] = []

    def __init__(self, wiki, year_month, pageviews_repo):
        self.wiki = wiki
        self.year_month = year_month
        self.repo = pageviews_repo
        self._cache: dict[str, int] = {}
        self.ensure_calls: list[set[str]] = []
        _RecordingCache.instances.append(self)

    async def ensure(self, titles, start, end):
        self.ensure_calls.append(set(titles))
        missing = [t for t in titles if t and t not in self._cache]
        if missing:
            fetched = await self.repo.get_title_views(missing, start, end)
            for t in missing:
                self._cache[t] = fetched.get(t, 0)

    def get(self, target, redirects):
        total = 0
        for t in [target, *redirects]:
            if t:
                total += self._cache.get(t, 0)
        return total


def _projects(names):
    """Build one WikiProjectConfig per name with a unique report page."""
    return [_project(name=n, report=f"Wikipedia:WikiProject {n}/Popular pages") for n in names]


# ---------------------------------------------------------------
# validate_project_config
# ---------------------------------------------------------------
def test_validate_project_config_valid(updater):
    u, repo = updater
    repo.does_title_exist.return_value = True
    assert u.validate_project_config("Wikipedia:WikiProject Foo", _project()) is True


def test_validate_project_config_incomplete(updater):
    u, repo = updater
    incomplete = WikiProjectConfig(
        project_main_page="Wikipedia:WikiProject Foo",
        Report="Wikipedia:WikiProject Foo/Popular pages",
        report_without_ns="Wikipedia:WikiProject_Foo/Popular_pages",
        Limit="10",
        Name="",
    )
    assert u.validate_project_config("Wikipedia:WikiProject Foo", incomplete) is False


def test_validate_project_config_rejects_mainspace_report(updater):
    u, repo = updater
    repo.does_title_exist.return_value = True
    mainspace = _project(report="Mainspace report")
    assert u.validate_project_config("Wikipedia:WikiProject Foo", mainspace) is False


def test_validate_project_config_rejects_missing_project_page(updater):
    u, repo = updater
    repo.does_title_exist.return_value = False
    assert u.validate_project_config("Wikipedia:WikiProject Foo", _project()) is False


# ---------------------------------------------------------------
# process_project
# ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_process_project_renders_report_from_cache(updater):
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
async def test_process_project_empty_rows_returns_early(updater):
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
async def test_process_project_without_cache_fetches_pageviews(updater):
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
async def test_process_project_accepts_dict_config(updater):
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
@pytest.mark.asyncio
async def test_update_reports_runs_pipeline(updater):
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
    repo.get_json_config.return_value = {}  # keep update_index lightweight
    repo.set_text.return_value = None

    await u.update_reports([_project()])

    # One set_text for the report, one for the index page.
    assert repo.set_text.call_count == 2
    repo.pageviews_repo.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_reports_skips_invalid_project(updater):
    u, repo = updater
    repo.does_title_exist.return_value = False
    repo.get_json_config.return_value = {}
    await u.update_reports([_project()])
    # Invalid project -> no report written, but index still attempted.
    repo.set_text.assert_called_once()  # index page only


# ---------------------------------------------------------------
# Issue #20: sequential, memory-bounded processing
# ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_sequential_processing_order(updater):
    """Project 2 is not loaded until project 1 has finished."""
    u, repo = updater
    events: list[tuple[str, str]] = []

    def pages(name):
        events.append(("load", name))
        return [{"page_title": f"{name}_bar", "redir_title": "", "pa_class": "Unknown", "pa_importance": "Unknown"}]

    repo.get_project_pages.side_effect = pages

    def set_text(page_title, *a, **k):
        events.append(("save", page_title))

    repo.set_text.side_effect = set_text
    repo.get_json_config.return_value = {}
    repo.get_projects_with_last_bot_timestamp.return_value = []

    await u.update_reports(_projects(["A", "B", "C"]))

    # Each project must be fully loaded+saved before the next is loaded.
    assert events[0] == ("load", "A")
    assert events[1][0] == "save" and events[1][1].startswith("Wikipedia:WikiProject A")
    assert events[2] == ("load", "B")
    assert events[3][0] == "save" and events[3][1].startswith("Wikipedia:WikiProject B")
    assert events[4] == ("load", "C")
    assert events[5][0] == "save" and events[5][1].startswith("Wikipedia:WikiProject C")
    # Final event is the index page save.
    assert events[-1][0] == "save" and "Index" in events[-1][1]


@pytest.mark.asyncio
async def test_no_global_project_data_accumulation(updater, monkeypatch):
    """update_reports never builds a structure holding every project's page data."""
    u, repo = updater
    loaded: list[str] = []

    def pages(name):
        loaded.append(name)
        return [{"page_title": f"{name}_bar", "redir_title": "", "pa_class": "Unknown", "pa_importance": "Unknown"}]

    repo.get_project_pages.side_effect = pages
    repo.get_json_config.return_value = {}
    repo.get_projects_with_last_bot_timestamp.return_value = []

    # Spy on process_project: when each project is processed, only the projects
    # already processed (plus the current one) have been loaded -- never a
    # future project, i.e. no up-front bulk load of all projects.
    real_process = u.process_project
    counter = {"n": 0}

    async def spy(project, config, cache=None, page_rows=None):
        counter["n"] += 1
        assert len(loaded) == counter["n"], f"loaded={loaded} while processing {config.Name}"
        return await real_process(project, config, cache=cache, page_rows=page_rows)

    monkeypatch.setattr(u, "process_project", spy)

    await u.update_reports(_projects(["A", "B", "C"]))
    assert loaded == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_shared_cache_reused_across_projects(updater, monkeypatch):
    """The same PageviewsCache instance is reused for every project."""
    u, repo = updater
    _RecordingCache.instances.clear()
    monkeypatch.setattr(ru_module, "PageviewsCache", _RecordingCache)
    repo.get_project_pages.return_value = [
        {"page_title": "Foo_bar", "redir_title": "", "pa_class": "Unknown", "pa_importance": "Unknown"}
    ]
    repo.get_json_config.return_value = {}
    repo.get_projects_with_last_bot_timestamp.return_value = []

    await u.update_reports(_projects(["A", "B"]))
    assert len(_RecordingCache.instances) == 1


@pytest.mark.asyncio
async def test_shared_title_fetched_once(updater, monkeypatch):
    """A title shared by many projects is fetched from the API only once."""
    u, repo = updater
    _RecordingCache.instances.clear()
    monkeypatch.setattr(ru_module, "PageviewsCache", _RecordingCache)
    shared = "United States"
    repo.get_project_pages.return_value = [
        {"page_title": shared.replace(" ", "_"), "redir_title": "", "pa_class": "Unknown", "pa_importance": "Unknown"}
    ]
    repo.pageviews_repo.get_title_views = AsyncMock(return_value={shared: 100})
    repo.get_json_config.return_value = {}
    repo.get_projects_with_last_bot_timestamp.return_value = []

    await u.update_reports(_projects(["A", "B"]))

    fetched_titles: list[str] = []
    for call in repo.pageviews_repo.get_title_views.call_args_list:
        fetched_titles.extend(call.args[0])
    assert fetched_titles.count(shared) == 1


@pytest.mark.asyncio
async def test_failure_isolation_continues(updater, monkeypatch):
    """A failed project does not abort the run; neighbours still process."""
    u, repo = updater
    repo.get_project_pages.return_value = [
        {"page_title": "Foo_bar", "redir_title": "", "pa_class": "Unknown", "pa_importance": "Unknown"}
    ]
    repo.get_json_config.return_value = {}
    repo.get_projects_with_last_bot_timestamp.return_value = []

    real_process = u.process_project

    async def maybe_raise(project, config, cache=None, page_rows=None):
        if config.Name == "B":
            raise RuntimeError("boom in B")
        return await real_process(project, config, cache=cache, page_rows=page_rows)

    monkeypatch.setattr(u, "process_project", maybe_raise)

    await u.update_reports(_projects(["A", "B", "C"]))

    saved_reports = [
        (c.args[0] if c.args else c.kwargs.get("page_title"))
        for c in repo.set_text.call_args_list
        if (c.args[0] if c.args else c.kwargs.get("page_title")).endswith("/Popular pages")
    ]
    assert "Wikipedia:WikiProject A/Popular pages" in saved_reports
    assert "Wikipedia:WikiProject C/Popular pages" in saved_reports
    assert "Wikipedia:WikiProject B/Popular pages" not in saved_reports
    # Index is still updated after the whole run.
    assert any(
        (c.args[0] if c.args else c.kwargs.get("page_title")) == "Wikipedia:WikiProject/Popular pages/Index"
        for c in repo.set_text.call_args_list
    )


@pytest.mark.asyncio
async def test_empty_project_skipped(updater):
    """A project with no pages is skipped without stopping the run."""
    u, repo = updater
    repo.get_project_pages.return_value = []
    repo.get_json_config.return_value = {}
    repo.get_projects_with_last_bot_timestamp.return_value = []

    await u.update_reports([_project()])
    # No report saved for the empty project; only the index is updated.
    repo.set_text.assert_called_once()
    assert repo.set_text.call_args.kwargs["page_title"] == "Wikipedia:WikiProject/Popular pages/Index"


@pytest.mark.asyncio
async def test_oversized_project_skipped(updater, monkeypatch):
    """A project exceeding max_project_size is skipped; later projects continue."""
    u, repo = updater
    small = dataclasses.replace(
        cfg.config,
        wiki=dataclasses.replace(cfg.config.wiki, max_project_size=1),
    )
    monkeypatch.setattr(ru_module, "app_config", small)
    # Two rows exceed the size limit of 1.
    repo.get_project_pages.return_value = [
        {"page_title": "One_bar", "redir_title": "", "pa_class": "Unknown", "pa_importance": "Unknown"},
        {"page_title": "Two_bar", "redir_title": "", "pa_class": "Unknown", "pa_importance": "Unknown"},
    ]
    repo.get_json_config.return_value = {}
    repo.get_projects_with_last_bot_timestamp.return_value = []

    await u.update_reports([_project()])
    repo.set_text.assert_called_once()
    assert repo.set_text.call_args.kwargs["page_title"] == "Wikipedia:WikiProject/Popular pages/Index"


@pytest.mark.asyncio
async def test_cache_persists_across_projects(updater):
    """Pageviews written for one project are available to subsequent projects."""
    u, repo = updater

    class _FakePageviewsRepo:
        def __init__(self):
            self.calls: list[list[str]] = []

        async def get_title_views(self, titles, start, end):
            self.calls.append(list(titles))
            return dict.fromkeys(titles, 5)

        async def aclose(self):
            return None

    fake = _FakePageviewsRepo()
    repo.pageviews_repo = fake
    shared = "United States"
    repo.get_project_pages.side_effect = lambda name: [
        {"page_title": shared.replace(" ", "_"), "redir_title": "", "pa_class": "Unknown", "pa_importance": "Unknown"}
    ]
    repo.get_json_config.return_value = {}
    repo.get_projects_with_last_bot_timestamp.return_value = []

    await u.update_reports(_projects(["A", "B"]))

    fetched = [t for call in fake.calls for t in call]
    # Shared title fetched once; reused for the second project from the cache.
    assert fetched.count(shared) == 1
    # The JSONL backing file holds the cached title written during the first
    # project, so a later run would reuse it instead of re-fetching.
    cache_file = ru_module.config.paths.views_data_dir / u.wiki / f"{u.start.strftime('%Y-%m')}.jsonl"
    assert cache_file.exists()
    lines = [json.loads(line) for line in cache_file.read_text(encoding="utf-8").splitlines()]
    assert {"title": shared, "views": 5} in lines


# ---------------------------------------------------------------
# update_index
# ---------------------------------------------------------------
def test_update_index_renders(updater):
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


def test_update_index_no_projects(updater):
    u, repo = updater
    repo.get_json_config.return_value = {}
    repo.get_projects_with_last_bot_timestamp.return_value = []
    repo.get_wiki_config.return_value = {"config": "X", "index": "Y"}
    u.update_index()
    repo.set_text.assert_called_once()
