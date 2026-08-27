"""
Tests for popularpages.wiki_repository.WikiRepository.

Ported from tests/WikiRepositoryTest.php. These tests hit the live English
Wikipedia API (and, for the currently-skipped tests, the replica database),
matching the original PHP suite's approach -- they require a valid
config.ini with bot credentials to run.

The two tests that were disabled upstream (prefixed 'er' instead of 'test'
in the PHP version, so PHPUnit never actually ran them) are kept here as
@pytest.mark.skip for parity, rather than silently dropped.
"""

from datetime import datetime

import pytest

from src.popularpages.report_updater import ReportUpdater
from src.popularpages.wiki_repository import BASE_DIR, WikiRepository

CONFIG_INI = BASE_DIR / "config.ini"

# Integration tests that hit the live wiki/DB require real credentials, which
# live in config.ini (gitignored). Skip them when that file is absent so the
# suite stays green in CI.
requires_creds = pytest.mark.skipif(
    not CONFIG_INI.exists(),
    reason="requires config.ini with live credentials",
)


@pytest.fixture(scope="module")
def repository() -> WikiRepository:
    return WikiRepository()


@requires_creds
def test_does_title_exist(repository: WikiRepository):
    assert repository.does_title_exist("Barack Obama")
    assert repository.does_title_exist("Mickey Mouse")
    assert not repository.does_title_exist("DumDeeDooDum")
    assert not repository.does_title_exist("Invalid title")


@requires_creds
def test_has_lead_section(repository):
    assert repository.has_lead_section("Wikipedia:WikiProject Medicine/Popular pages")
    assert not repository.has_lead_section("User:Community Tech bot/Popular pages config.json")


@pytest.mark.skip(
    reason="Disabled upstream in the PHP version too (was 'ertestGetProjectPages', never actually run by PHPUnit)."
)
def test_get_project_pages(repository):
    rows = repository.get_project_pages("Disney")
    titles = [row["page_title"] for row in rows]
    assert "Walt Disney" in titles
    assert "Pixar" in titles


@pytest.mark.skip(
    reason="Disabled upstream in the PHP version too (was 'ertestGetMonthlyPageviews', "
    "never actually run by PHPUnit)."
)
@pytest.mark.asyncio
async def test_get_monthly_pageviews(repository):
    pages = ["Star Wars", "Zootopia", "The Lion King"]
    batch = {p: [p] for p in pages}
    result = await repository.pageviews_repo.get_pageviews(batch, "2017020100", "2017022800")
    expected = {
        "Star Wars": 517930,
        "Zootopia": 313960,
        "The Lion King": 211521,
    }
    assert result == expected


def test_set_text(repository):
    result = repository.set_text("User:NKohli (WMF)/sandbox", "Hi there! This is a test")
    assert result["edit"]["result"] == "Success"


# ------------------------------------------------------------
# Pure unit tests (no network/credentials required)
# ------------------------------------------------------------
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
