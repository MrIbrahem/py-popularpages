"""
Comparison tests for ``ReportUpdater.retrieve_project_updates`` (original
implementation) and ``ReportUpdater.retrieve_project_updates_new`` (refactored
implementation).

Both methods are meant to return the list of :class:`WikiProjectConfig` objects
for the wiki, with each project's ``Updated`` field populated from the bot's
last-edit timestamp (the ``rev_timestamp`` returned by
``WikiRepository.get_projects_with_last_bot_timestamp``).

The two differ in mechanics:

* ``retrieve_project_updates`` mutates the raw JSON-config dicts in place
  (``projects_config[...]["Updated"] = ...``) and only then builds the
  ``WikiProjectConfig`` objects via ``from_json_list``.
* ``retrieve_project_updates_new`` builds the ``WikiProjectConfig`` objects
  first and then sets the ``Updated`` *attribute* on each object
  (``x.Updated = ...``).

These tests drive both side-by-side with mocked ``WikiRepository`` data so we
can verify that the two implementations agree for every input shape. The
comparison helper captures exceptions as well as return values, so a shared
bug (e.g. a key-mapping mismatch) surfaces as *both* methods raising the same
exception rather than silently diverging.

Current status (see ``test_with_timestamps_both_agree``): with the real
keying -- ``get_json_config`` returns config keyed by ``project_main_page``
while the last-edit rows are keyed by the report db-title --
``row["page_title"] in projects_config`` is never true, so the guard drops
every row and *both* implementations return the projects with ``Updated``
left as ``None``. The comparison therefore documents that they agree (and
that the timestamp -> ``Updated`` mapping is currently broken in both).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.popularpages.mapping import WikiProjectConfig
from src.popularpages.report_updater import ReportUpdater


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
    raw: dict[str, dict] = {}
    for main, report, name in _specs():
        raw[main] = {"Report": report, "Limit": "500", "Name": name}
    return raw


def _expected_no_updated() -> list[WikiProjectConfig]:
    """Reference list with ``Updated`` left as ``None``."""
    objs: list[WikiProjectConfig] = []
    for main, report, name in _specs():
        objs.append(
            WikiProjectConfig(
                project_main_page=main,
                Report=report,
                report_without_ns=_report_key(report),
                Limit=500,
                Name=name,
                Updated=None,
            )
        )
    return objs


def _db_rows(timestamps: dict[str, str]) -> list[dict]:
    """Build last-edit rows. ``timestamps`` maps project *Name* -> rev_timestamp."""
    by_name = {name: (main, report) for main, report, name in _specs()}
    rows: list[dict] = []
    for name, ts in timestamps.items():
        _main, report = by_name[name]
        rows.append({"page_title": _report_key(report), "rev_timestamp": ts})
    return rows


# -- Harness ---------------------------------------------------------------


def _make_updater(json_config: dict[str, dict], db_rows: list[dict]) -> ReportUpdater:
    """Build a ``ReportUpdater`` without running its (network/DB) ``__init__``."""
    updater = object.__new__(ReportUpdater)
    updater.wiki_repository = MagicMock(name="wiki_repository")
    updater.wiki_repository.get_json_config.return_value = json_config
    updater.wiki_repository.get_projects_with_last_bot_timestamp.return_value = db_rows
    return updater


@dataclass
class _Outcome:
    value: Any = None
    error: BaseException | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def result(self):
        if self.error is not None:
            raise self.error
        return self.value


def _run(updater: ReportUpdater, method: str) -> _Outcome:
    try:
        return _Outcome(value=getattr(updater, method)())
    except Exception as exc:  # noqa: BLE001 - we want to compare any raised error
        return _Outcome(error=exc)


def _assert_same(old: _Outcome, new: _Outcome) -> None:
    """Assert the old and new methods agree: same value, or same exception."""
    if old.ok and new.ok:
        assert old.value == new.value
    else:
        assert not old.ok, "old succeeded but new raised"
        assert not new.ok, "new succeeded but old raised"
        assert type(old.error) is type(new.error)
        assert old.error.args == new.error.args


# -- Comparison tests ------------------------------------------------------


class TestCompareRetrieveProjectUpdates:
    def test_empty_config_both_return_empty(self) -> None:
        updater = _make_updater({}, [])
        old = _run(updater, "retrieve_project_updates")
        new = _run(updater, "retrieve_project_updates_new")
        _assert_same(old, new)
        assert old.value == []

    def test_no_timestamps_both_leave_updated_none(self) -> None:
        updater = _make_updater(_build_json_config(), [])
        old = _run(updater, "retrieve_project_updates")
        new = _run(updater, "retrieve_project_updates_new")
        _assert_same(old, new)
        # Both should produce the same WikiProjectConfig list, with no Updated date.
        assert old.value == _expected_no_updated()
        assert all(c.Updated is None for c in old.value)

    def test_with_timestamps_both_agree(self) -> None:
        # With the current keying (config keyed by project_main_page, rows keyed
        # by report db-title) the guard ``row["page_title"] in projects_config``
        # is never satisfied, so every row is dropped and *both* implementations
        # return the projects with ``Updated`` left as ``None``. The comparison
        # confirms they agree -- and surfaces the shared key-mapping gap (the
        # last-edit timestamp is never actually attached to a project).
        db_rows = _db_rows({"Dinosaurs": "20230115000000", "Medicine": "20230110000000"})
        updater = _make_updater(_build_json_config(), db_rows)
        old = _run(updater, "retrieve_project_updates")
        new = _run(updater, "retrieve_project_updates_new")
        _assert_same(old, new)
        assert old.ok
        assert all(c.Updated is None for c in old.value)

    def test_parity_across_scenarios(self) -> None:
        scenarios = [
            ({}, []),
            (_build_json_config(), []),
            (_build_json_config(), _db_rows({"Dinosaurs": "20230115000000"})),
            (
                _build_json_config(),
                _db_rows(
                    {
                        "Dinosaurs": "20230115000000",
                        "Medicine": "20221215000000",
                        "Physics": "20230101000000",
                    }
                ),
            ),
        ]
        for json_config, db_rows in scenarios:
            updater = _make_updater(json_config, db_rows)
            old = _run(updater, "retrieve_project_updates")
            new = _run(updater, "retrieve_project_updates_new")
            _assert_same(old, new)

    def test_returned_objects_are_wikiprojectconfig(self) -> None:
        updater = _make_updater(_build_json_config(), [])
        old = _run(updater, "retrieve_project_updates")
        new = _run(updater, "retrieve_project_updates_new")
        _assert_same(old, new)
        assert all(isinstance(c, WikiProjectConfig) for c in old.value)
