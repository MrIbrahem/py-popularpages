"""
Both functions are supposed to return the WikiProjects that have *not* yet been
updated for the current cycle (i.e. the "stale" projects). These tests exercise
both side-by-side with mocked config/DB so we can verify whether the two
implementations agree.

The two functions differ in how they identify a project to drop:

* ``get_stale_projects`` -> maps each project's display-form report title
  (``x.report_title``) to its ``project_main_page``, looks up the bot's last
  edit by that title, and keeps every project whose ``project_main_page`` is
  not in ``to_pop``.

The comparison assertions below document the expected (correct) behaviour and
_surface any divergence between the two._
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.py_port.popularpages.mapping import WikiProjectConfig
from src.py_port.popularpages.utils import mediawiki_timestamp_to_epoch
from src.py_port.popularpages.wiki_repository import repository
from src.py_port.popularpages.wiki_repository.repository import WikiRepository

# Fixed "first of this month" epoch used to make staleness deterministic.
FIRST_OF_MONTH = mediawiki_timestamp_to_epoch("20230101000000")

# A timestamp already in the current cycle (>= first of month) -> "updated".
UPDATED_TS = "20230115000000"
# A timestamp from last cycle (< first of month) -> "stale".
STALE_TS = "20221215000000"


# -- Sample config ---------------------------------------------------


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


def _build_configs() -> tuple[list[WikiProjectConfig], dict[str, dict]]:
    """Build the list-of-configs and the matching JSON-config dict.

    The JSON dict mirrors what ``get_json_config`` returns: it is keyed by
    ``project_main_page`` (exactly what ``get_stale_projects`` pops from).
    """
    objs: list[WikiProjectConfig] = []
    raw: dict[str, dict] = {}
    for main, report, name in _specs():
        objs.append(
            WikiProjectConfig(
                project_main_page=main,
                Report=report,
                report_without_ns=WikiProjectConfig.trim_report_prefix(report),
                Limit=500,
                Name=name,
            )
        )
        raw[main] = {"Report": report, "Limit": "500", "Name": name}
    return objs, raw


def _report_key(name: str) -> str:
    """Report key (display title) for a project's Name, per the specs above."""
    for _, report, n in _specs():
        if n == name:
            return WikiProjectConfig.trim_report_prefix(report).replace("_", " ")
    raise KeyError(name)


def _db_rows(updated: list[str], stale: list[str]) -> list[dict]:
    """Build db rows. ``updated``/``stale`` are lists of project Names."""
    rows: list[dict] = []
    for name in updated:
        rows.append({"page_title": _report_key(name), "rev_timestamp": UPDATED_TS})
    for name in stale:
        rows.append({"page_title": _report_key(name), "rev_timestamp": STALE_TS})
    return rows


def _expected_stale(config_objs: list[WikiProjectConfig], db_rows: list[dict]) -> set[str]:
    """Reference computation of the correct stale-project Name set."""
    updated_pages = {
        r["page_title"] for r in db_rows if mediawiki_timestamp_to_epoch(r["rev_timestamp"]) >= FIRST_OF_MONTH
    }
    return {c.Name for c in config_objs if c.report_title not in updated_pages}


# -- Fixtures ---------------------------------------------------


@pytest.fixture
def repo() -> WikiRepository:
    """Build a WikiRepository without running the heavy live __init__."""
    r = object.__new__(WikiRepository)
    r.wiki = "en.wikipedia"
    r.db = MagicMock(name="wiki_db")
    return r


def _wire(
    repo: WikiRepository,
    config_objs: list[WikiProjectConfig],
    json_config: dict[str, dict],
    db_rows: list[dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo.get_config = MagicMock(return_value=config_objs)
    repo.get_json_config = MagicMock(return_value=json_config)
    repo.db.get_projects_timestamps.return_value = db_rows
    monkeypatch.setattr(repository, "first_of_this_month_timestamp", lambda *a, **k: FIRST_OF_MONTH)


# -- Comparison tests ---------------------------------------------------


class TestCompareStaleProjects:
    """Comparison tests for `get_stale_projects`, verifying it returns projects not yet updated this cycle."""

    def test_empty_config_returns_empty_value(self, repo, monkeypatch):
        _wire(repo, [], {}, [], monkeypatch)

        old = repo.get_stale_projects()

        assert old == []

    def test_no_timestamps_all_stale_both_agree(self, repo, monkeypatch):
        config_objs, json_config = _build_configs()
        _wire(repo, config_objs, json_config, [], monkeypatch)

        old = {c.Name for c in repo.get_stale_projects()}

        assert old == {"Dinosaurs", "Medicine", "Physics"}

    def test_all_updated_this_cycle_both_agree(self, repo, monkeypatch):
        config_objs, json_config = _build_configs()
        db_rows = _db_rows(updated=["Dinosaurs", "Medicine", "Physics"], stale=[])
        _wire(repo, config_objs, json_config, db_rows, monkeypatch)

        old = {c.Name for c in repo.get_stale_projects()}

        expected = _expected_stale(config_objs, db_rows)
        assert old == expected  # old implementation is correct

    def test_mixed_updated_and_stale_both_agree(self, repo, monkeypatch):
        config_objs, json_config = _build_configs()
        # Dinosaurs & Medicine updated this cycle; Physics untouched (stale).
        db_rows = _db_rows(updated=["Dinosaurs", "Medicine"], stale=["Physics"])
        _wire(repo, config_objs, json_config, db_rows, monkeypatch)

        old = {c.Name for c in repo.get_stale_projects()}

        expected = _expected_stale(config_objs, db_rows)
        assert old == expected  # old implementation is correct

    def test_single_project_updated_is_removed_both_agree(self, repo: WikiRepository, monkeypatch):
        config_objs, json_config = _build_configs()
        db_rows = _db_rows(updated=["Medicine"], stale=[])
        _wire(repo, config_objs, json_config, db_rows, monkeypatch)

        old = {c.Name for c in repo.get_stale_projects()}

        expected = _expected_stale(config_objs, db_rows)
        assert old == expected  # old implementation is correct
