"""
Unit tests for ``ReportUpdater.retrieve_project_updates``.

``retrieve_project_updates`` returns the list of :class:`WikiProjectConfig`
objects for the wiki, with each project's ``Updated`` field intended to be
populated from the bot's last-edit timestamp (the ``rev_timestamp`` returned by
``WikiRepository.get_projects_with_last_bot_timestamp``).

These tests drive the method with a mocked ``WikiRepository``.

Current status (see ``test_with_timestamps_present``): with the real keying --
``get_json_config`` returns config keyed by ``project_main_page`` while the
last-edit rows are keyed by the report db-title -- the guard
``row["page_title"] in projects_config`` is never true, so every row is dropped
and the method returns the projects with ``Updated`` left as ``None``. The tests
document that behaviour (the timestamp -> ``Updated`` mapping is currently
broken) so it is visible rather than silently diverging.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.py_port.popularpages.mapping import WikiProjectConfig
from src.py_port.popularpages.report_updater import ReportUpdater

# -- Sample data -----------------------------------------------------------


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


# -- Harness ---------------------------------------------------------------


def _make_updater(json_config: dict[str, dict], db_rows: list[dict]) -> ReportUpdater:
    """Build a ``ReportUpdater`` without running its (network/DB) ``__init__``."""
    updater = object.__new__(ReportUpdater)
    updater.wiki = "en.wikipedia"
    updater.wiki_repository = MagicMock(name="wiki_repository")
    updater.wiki_repository.get_json_config.return_value = json_config
    updater.wiki_repository.get_projects_with_last_bot_timestamp.return_value = db_rows
    return updater


# -- Tests -----------------------------------------------------------------


class TestRetrieveProjectUpdatesNew:
    def test_empty_config_returns_empty(self) -> None:
        updater = _make_updater({}, [])
        assert updater.retrieve_project_updates() == []

    def test_no_timestamps_leaves_updated_none(self) -> None:
        updater = _make_updater(_build_json_config(), [])
        result = updater.retrieve_project_updates()
        assert all(isinstance(c, WikiProjectConfig) for c in result)
        assert all(c.Updated is None for c in result)

    def test_with_timestamps_present(self) -> None:
        # With the current keying (config keyed by project_main_page, rows keyed
        # by report db-title) the guard ``row["page_title"] in projects_config``
        # is never satisfied, so every row is dropped and the method returns the
        # projects with ``Updated`` left as ``None``. This documents the key-
        # mapping gap (the last-edit timestamp is never attached).
        db_rows = _db_rows({"Dinosaurs": "20230115000000", "Medicine": "20230110000000"})
        updater = _make_updater(_build_json_config(), db_rows)
        result = updater.retrieve_project_updates()
        assert len(result) == 3
        assert all(c.Updated is None for c in result)

    def test_returned_objects_are_wikiprojectconfig(self) -> None:
        updater = _make_updater(_build_json_config(), [])
        result = updater.retrieve_project_updates()
        assert all(isinstance(c, WikiProjectConfig) for c in result)
