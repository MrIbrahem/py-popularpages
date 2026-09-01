"""Tests for src/py_port/check_reports.py (all-wikis run entry point)."""

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_socket import enable_socket

import src.py_port.check_reports as cr_module
from src.py_port.popularpages.config import app_config


@pytest.fixture
def patched(monkeypatch):
    """Configure a mocked report updater for CLI tests.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the report updater.
    """
    # main() drives everything via a single asyncio.run() call, which needs a
    # real event loop (its self-pipe uses a socket). No real network occurs
    # because the updater is fully mocked, so allowing the socket is safe here.
    enable_socket()
    updater = MagicMock()
    updater.wiki_repository.get_stale_projects.return_value = []
    updater.update_reports = AsyncMock()
    monkeypatch.setattr(cr_module, "ReportUpdater", lambda *a, **k: updater)  # type: ignore

    # Avoid unrelated side effects (extra object construction, real file I/O)
    # in each per-wiki iteration so the "all wikis" test isn't paying for
    # work that isn't under test here.
    monkeypatch.setattr(cr_module, "IndexUpdater", MagicMock())  # type: ignore

    return updater


class TestMain:
    """Tests for the check_reports.py main() entry point."""

    def test_main_runs_for_explicit_wiki(self, patched, monkeypatch):
        # load_wikis_config reads the real config/wikis.yaml; en.wikipedia exists.
        """Verify that report checks run once for an explicitly selected wiki."""
        monkeypatch.setattr(sys, "argv", ["check", "--wiki", "en.wikipedia"])
        cr_module.main()
        patched.update_reports.assert_awaited_once_with([])

    def test_main_unknown_wiki_returns_early(self, patched, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["check", "--wiki", "zz.wikipedia"])
        cr_module.main()
        patched.update_reports.assert_not_awaited()

    def test_main_processes_all_wikis_when_none_specified(self, patched, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["check"])
        wikis = app_config.paths.load_wikis_config()
        cr_module.main()
        assert patched.update_reports.await_count == len(wikis)
