"""Tests for src/cli/generate_report.py (single-project report entry point)."""

import sys

from unittest.mock import AsyncMock, MagicMock

import pytest

import src.cli.generate_report as gr_module


@pytest.fixture
def patched(monkeypatch):
    updater = MagicMock()
    updater.wiki_repository.get_project.return_value = MagicMock()
    updater.update_reports = AsyncMock()
    monkeypatch.setattr(gr_module, "ReportUpdater", lambda *a, **k: updater)
    return updater


def test_main_generates_report_for_valid_project(patched, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gen", "--wiki", "en.wikipedia", "--project", "Dinosaurs"])
    gr_module.main()
    patched.update_reports.assert_awaited_once()


def test_main_invalid_wiki_format_returns_early(patched, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gen", "--wiki", "bogus", "--project", "X"])
    gr_module.main()
    patched.update_reports.assert_not_awaited()


def test_main_exits_when_project_not_found(patched, monkeypatch):
    patched.wiki_repository.get_project.return_value = None
    monkeypatch.setattr(sys, "argv", ["gen", "--wiki", "en.wikipedia", "--project", "Nope"])
    with pytest.raises(SystemExit) as exc:
        gr_module.main()
    assert exc.value.code == 1
    patched.update_reports.assert_not_awaited()
