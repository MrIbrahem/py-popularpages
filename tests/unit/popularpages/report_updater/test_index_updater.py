"""
Tests for index page generation:
at src.py_port.popularpages.report_updater.index_updater.IndexUpdater
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import src.py_port.popularpages.report_updater.index_updater as iu_module
from src.py_port.popularpages.i18n import I18n
from src.py_port.popularpages.mapping import WikiProjectConfig
from src.py_port.popularpages.report_updater.index_updater import IndexUpdater

# -- Sample data ---------------------------------------------------


def _specs() -> list[tuple[str, str, str]]:
    """Return (project_main_page, Report, Name) triples."""
    return [
        (
            "Wikipedia:WikiProject Dinosaurs",
            "Wikipedia:WikiProject Dinosaurs/Popular pages",
            "Dinosaurs",
        ),
        (
            "Wikipedia:WikiProject Medicine",
            "Wikipedia:WikiProject Medicine/Popular pages",
            "Medicine",
        ),
        (
            "Wikipedia:WikiProject Physics",
            "Wikipedia:WikiProject Physics/Popular pages",
            "Physics",
        ),
    ]


def _report_key(report: str) -> str:
    """The db-style title used as the ``page_title`` key in last-edit rows."""
    return WikiProjectConfig.trim_report_prefix(report)


def _build_json_config() -> dict[str, dict]:
    """JSON config as returned by ``WikiRepository.get_json_config``.

    It is keyed by ``project_main_page`` (the on-wiki config page shape).
    """
    return {main: {"Report": report, "Limit": "500", "Name": name} for main, report, name in _specs()}


def _db_rows(timestamps: dict[str, str]) -> list[dict]:
    """Build last-edit rows. ``timestamps`` maps project *Name* -> rev_timestamp."""
    by_name = {name: report for _main, report, name in _specs()}
    return [{"page_title": _report_key(by_name[name]), "rev_timestamp": ts} for name, ts in timestamps.items()]


# -- Harness ---------------------------------------------------


def _make_updater(json_config: dict[str, dict], db_rows: list[dict]) -> IndexUpdater:
    """Build a ``IndexUpdater`` without running its (network/DB) ``__init__``."""
    updater = object.__new__(IndexUpdater)
    updater.wiki = "en.wikipedia"
    updater.wiki_repository = MagicMock(name="wiki_repository")
    updater.wiki_repository.get_json_config.return_value = json_config
    updater.wiki_repository.get_projects_with_last_bot_timestamp.return_value = db_rows
    return updater


# -- Tests ---------------------------------------------------


class TestRetrieveProjectUpdatesNew:
    """Tests for `IndexUpdater.retrieve_project_updates`, documenting current behaviour of the timestamp→Updated mapping."""

    def test_empty_config_returns_empty(self) -> None:
        updater = _make_updater({}, [])
        assert updater.retrieve_project_updates() == []

    def test_no_timestamps_leaves_updated_none(self) -> None:
        updater = _make_updater(_build_json_config(), [])
        result = updater.retrieve_project_updates()
        assert all(isinstance(c, WikiProjectConfig) for c in result)
        assert all(c.Updated is None for c in result)

    def test_with_timestamps_present(self) -> None:
        # Rows are keyed by the report db-title (report_without_ns), which is
        # mapped back to the project main page so the last-edit timestamp is
        # attached to the correct WikiProjectConfig as ``Updated``.
        db_rows = _db_rows({"Dinosaurs": "20230115000000", "Medicine": "20230110000000"})
        updater = _make_updater(_build_json_config(), db_rows)
        result = updater.retrieve_project_updates()
        assert len(result) == 3

        by_name = {c.Name: c for c in result}
        assert by_name["Dinosaurs"].Updated == "2023-01-15"
        assert by_name["Medicine"].Updated == "2023-01-10"
        # Physics had no last-edit row, so its Updated stays None.
        assert by_name["Physics"].Updated is None

    def test_returned_objects_are_wikiprojectconfig(self) -> None:
        updater = _make_updater(_build_json_config(), [])
        result = updater.retrieve_project_updates()
        assert all(isinstance(c, WikiProjectConfig) for c in result)


@pytest.fixture
def index_updater(monkeypatch):
    """Create a configured `IndexUpdater` and mocked wiki repository for tests.

    Returns:
        A tuple containing the configured `IndexUpdater` and its mocked repository.
    """
    repo = MagicMock()
    repo.i18n = I18n("en")
    repo.get_wiki_config.return_value = {
        "category": "Category:WikiProject popular pages",
        "config": "Wikipedia:WikiProject/Popular pages",
        "index": "Wikipedia:WikiProject/Popular pages/Index",
    }
    repo.get_json_config.return_value = {}
    repo.get_projects_with_last_bot_timestamp.return_value = []

    monkeypatch.setattr(iu_module, "WikiRepository", lambda *a, **k: repo)

    u = iu_module.IndexUpdater("en.wikipedia", dry_run=True)
    return u, repo


# ---------------------------------------------------
# update_index (now lives in IndexUpdater)
# ---------------------------------------------------
class TestUpdateIndex:
    """Tests for the `update_index` method of the `IndexUpdater` class."""

    def test_update_index_renders(self, index_updater):
        u, repo = index_updater
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

    def test_update_index_no_projects(self, index_updater):
        u, repo = index_updater
        repo.get_json_config.return_value = {}
        repo.get_projects_with_last_bot_timestamp.return_value = []
        repo.get_wiki_config.return_value = {"config": "X", "index": "Y"}
        u.update_index()
        # No projects -> update_index returns early and writes nothing.
        repo.set_text.assert_not_called()
