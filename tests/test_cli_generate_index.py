"""Tests for src/cli/generate_index.py (index page entry point)."""

import sys

from unittest.mock import MagicMock

import pytest

import src.cli.generate_index as gi_module


@pytest.fixture
def patched(monkeypatch):
    updater = MagicMock()
    updater.update_index = MagicMock()
    monkeypatch.setattr(gi_module, "ReportUpdater", lambda *a, **k: updater)
    return updater


def test_main_updates_index(patched, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["idx", "--wiki", "en.wikipedia"])
    gi_module.main()
    patched.update_index.assert_called_once()


def test_main_invalid_wiki_returns_early(patched, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["idx", "--wiki", "bogus"])
    gi_module.main()
    patched.update_index.assert_not_called()
