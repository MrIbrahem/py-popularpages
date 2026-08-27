"""Tests for functions in wiki_repository.py and report_updater.py."""

from datetime import datetime

import pytest

from popularpages.report_updater import ReportUpdater
from popularpages.wiki_repository import BASE_DIR, WikiRepository

CONFIG_INI = BASE_DIR / "config.ini"

# Integration tests that hit the live wiki/DB require real credentials, which
# live in config.ini (gitignored). Skip them when that file is absent so the
# suite stays green in CI.
requires_creds = pytest.mark.skipif(
    not CONFIG_INI.exists(),
    reason="requires config.ini with live credentials",
)


@pytest.fixture
def wiki_repository():
    return WikiRepository()


@requires_creds
def test_does_title_exist(wiki_repository):
    assert wiki_repository.does_title_exist("Barack Obama")
    assert wiki_repository.does_title_exist("Mickey Mouse")
    assert not wiki_repository.does_title_exist("DumDeeDooDum")
    assert not wiki_repository.does_title_exist("Invalid title")


@requires_creds
def test_has_lead_section(wiki_repository):
    assert wiki_repository.has_lead_section("Wikipedia:WikiProject Medicine/Popular pages")
    assert not wiki_repository.has_lead_section("User:Community Tech bot/Popular pages config.json")


@pytest.mark.skip(reason="disabled upstream in PHP version too (er-prefixed)")
def test_get_project_pages(wiki_repository): ...


@pytest.mark.skip(reason="disabled upstream in PHP version too (er-prefixed)")
def test_get_monthly_pageviews(wiki_repository): ...


@requires_creds
def test_set_text(wiki_repository):
    result = wiki_repository.set_text("User:NKohli (WMF)/sandbox", "Hi there! This is a test")
    assert result["edit"]["result"] == "Success"


# --------------------------------------------------------------------------- #
# Pure unit tests (no network/credentials required)
# --------------------------------------------------------------------------- #
def test_previous_month_range_midyear():
    today = datetime(2023, 6, 15, 10, 30, 0)
    start, end = ReportUpdater.previous_month_range(today)
    assert (start.year, start.month, start.day) == (2023, 5, 1)
    assert (end.year, end.month, end.day) == (2023, 5, 31)


def test_previous_month_range_year_boundary():
    today = datetime(2023, 1, 10, 0, 0, 0)
    start, end = ReportUpdater.previous_month_range(today)
    assert (start.year, start.month, start.day) == (2022, 12, 1)
    assert (end.year, end.month, end.day) == (2022, 12, 31)


def test_previous_month_range_days_in_month():
    # February in a non-leap year.
    today = datetime(2023, 3, 5)
    start, end = ReportUpdater.previous_month_range(today)
    days_in_month = (end - start).days + 1
    assert days_in_month == 28
