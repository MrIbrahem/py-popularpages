"""
Tests for src.popularpages.wiki_repository.WikiRepository.

Ported from tests/WikiRepositoryTest.php. These tests hit the live English
Wikipedia API (and, for the currently-skipped tests, the replica database),
matching the original PHP suite's approach -- they require valid credentials
(from a ``.env`` file, see ``.env.example``) to run.

The two tests that were disabled upstream (prefixed 'er' instead of 'test'
in the PHP version, so PHPUnit never actually ran them) are kept here as
@pytest.mark.skip for parity, rather than silently dropped.
"""

import mwclient.errors
import pytest

from src.popularpages.config import has_credentials
from src.popularpages.wiki_repository import WikiRepository

# Integration tests that hit the live wiki/DB require real credentials, which
# live in .env (gitignored). Skip them when absent so the suite stays green in
# CI.
requires_creds = pytest.mark.skipif(
    not has_credentials(),
    reason="requires credentials in .env with live credentials",
)


@pytest.fixture(scope="module")
def repository() -> WikiRepository:
    try:
        return WikiRepository()
    except mwclient.errors.LoginError:
        pytest.skip("requires valid live wiki credentials (.env present but login failed)")


@requires_creds
@pytest.mark.asyncio
def test_does_title_exist(repository: WikiRepository):
    assert repository.does_title_exist("Barack Obama")
    assert repository.does_title_exist("Mickey Mouse")
    assert not repository.does_title_exist("DumDeeDooDum")
    assert not repository.does_title_exist("Invalid title")


@requires_creds
@pytest.mark.asyncio
def test_has_lead_section(repository):
    assert repository.has_lead_section("Wikipedia:WikiProject Medicine/Popular pages")
    assert not repository.has_lead_section("User:Community Tech bot/Popular pages config.json")


@pytest.mark.skip(
    reason="Disabled upstream in the PHP version too (was 'ertestGetProjectPages', never actually run by PHPUnit)."
)
@pytest.mark.asyncio
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


@requires_creds
@pytest.mark.asyncio
def test_set_text(repository):
    result = repository.set_text("User:NKohli (WMF)/sandbox", "Hi there! This is a test")
    assert result["edit"]["result"] == "Success"


class TestGetBotLastEditDate:
    """
    tests for get_bot_last_edit_date
    """

    @pytest.mark.network
    def test_basic(self):
        repo = WikiRepository(dry_run=True)
        result1 = repo.get_bot_last_edit_date("Wikipedia:WikiProject A Song of Ice and Fire/Popular pages")
        assert result1 == "2026-08-04"


class TestWriteDryRunText:
    """
    _write_dry_run_text persists the rendered wikitext to the logs folder.
    Exercises the method directly (bypassing WikiRepository.__init__, which
    needs live credentials) by using __new__ and a monkeypatched LOG_DIR.
    """

    def test_writes_file_with_sanitized_title(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.popularpages.wiki_repository.LOG_DIR", tmp_path
        )
        repo = WikiRepository.__new__(WikiRepository)
        repo.wiki = "en.wikipedia"

        title = "Wikipedia:WikiProject Medicine/Popular pages"
        text = "== List ==\n| A | B\n"
        repo._write_dry_run_text(title, text)

        files = list(tmp_path.glob("*.wikitext"))
        assert len(files) == 1
        # Colons and slashes in the title must be sanitized out of the filename.
        assert ":" not in files[0].name and "/" not in files[0].name
        assert files[0].read_text(encoding="utf-8") == text

    def test_filename_includes_wiki(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.popularpages.wiki_repository.LOG_DIR", tmp_path
        )
        repo = WikiRepository.__new__(WikiRepository)
        repo.wiki = "ar.wikipedia"
        repo._write_dry_run_text("User:Foo/Bar", "x")
        assert any("ar.wikipedia" in f.name for f in tmp_path.glob("*.wikitext"))
