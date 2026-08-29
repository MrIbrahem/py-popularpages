"""Tests for src/py_port/generate_index.py (index page entry point)."""

import sys
from unittest.mock import MagicMock

import pytest

import src.py_port.generate_index as gi_module


@pytest.fixture
def patched(monkeypatch):
    """
    Provide a mocked report updater and replace the module's report updater constructor for a test.

    Parameters:
        monkeypatch: Pytest monkeypatch fixture used to replace `ReportUpdater`.

    Returns:
        MagicMock: The mocked report updater with a mocked `update_index` method.
    """
    updater = MagicMock()
    updater.update_index = MagicMock()
    monkeypatch.setattr(gi_module, "ReportUpdater", lambda *a, **k: updater)
    return updater


class TestMain:
    """Tests for the generate_index.py main() entry point."""

    def test_main_updates_index(self, patched, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["idx", "--wiki", "en.wikipedia"])
        gi_module.main()
        patched.update_index.assert_called_once()

    def test_main_invalid_wiki_returns_early(self, patched, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["idx", "--wiki", "bogus"])
        gi_module.main()
        patched.update_index.assert_not_called()
