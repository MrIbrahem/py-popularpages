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

import pytest

from popularpages.wiki_repository import WikiRepository


@pytest.fixture(scope="module")
def wiki_repository():
    return WikiRepository()


def test_does_title_exist(wiki_repository):
    assert wiki_repository.does_title_exist("Barack Obama")
    assert wiki_repository.does_title_exist("Mickey Mouse")
    assert not wiki_repository.does_title_exist("DumDeeDooDum")
    assert not wiki_repository.does_title_exist("Invalid title")


def test_has_lead_section(wiki_repository):
    assert wiki_repository.has_lead_section("Wikipedia:WikiProject Medicine/Popular pages")
    assert not wiki_repository.has_lead_section("User:Community Tech bot/Popular pages config.json")


@pytest.mark.skip(
    reason="Disabled upstream in the PHP version too (was 'ertestGetProjectPages', never actually run by PHPUnit)."
)
def test_get_project_pages(wiki_repository):
    rows = wiki_repository.get_project_pages("Disney")
    titles = [row["page_title"] for row in rows]
    assert "Walt Disney" in titles
    assert "Pixar" in titles


@pytest.mark.skip(
    reason="Disabled upstream in the PHP version too (was 'ertestGetMonthlyPageviews', "
    "never actually run by PHPUnit)."
)
@pytest.mark.asyncio
async def test_get_monthly_pageviews(wiki_repository):
    pages = ["Star Wars", "Zootopia", "The Lion King"]
    batch = {p: [p] for p in pages}
    result = await wiki_repository.pageviews_repo.get_pageviews(batch, "2017020100", "2017022800")
    expected = {
        "Star Wars": 517930,
        "Zootopia": 313960,
        "The Lion King": 211521,
    }
    assert result == expected


def test_set_text(wiki_repository):
    result = wiki_repository.set_text("User:NKohli (WMF)/sandbox", "Hi there! This is a test")
    assert result["edit"]["result"] == "Success"
