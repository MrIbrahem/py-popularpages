"""
Tests for src.py_port.popularpages.wiki_repository.repository.WikiRepository.

Ported from tests/WikiRepositoryTest.php. These tests hit the live English
Wikipedia API (and, for the currently-skipped tests, the replica database),
matching the original PHP suite's approach -- they require valid credentials
(from a ``.env`` file, see ``.env.example``) to run.

"""

import json
from datetime import datetime
from unittest.mock import MagicMock

import mwclient.errors
import pytest

import src.py_port.popularpages.config as cfg
from src.py_port.popularpages.mapping import WikiProjectConfig
from src.py_port.popularpages.wiki_repository.repository import WikiRepository

# Integration tests that hit the live wiki/DB require real credentials, which
# live in .env (gitignored). Skip them when absent so the suite stays green in
# CI.
requires_creds = pytest.mark.skipif(
    not cfg.config.credentials.has_credentials(),
    reason="requires credentials in .env with live credentials",
)


@pytest.fixture(scope="module")
def repository() -> WikiRepository:
    try:
        return WikiRepository()
    except mwclient.errors.LoginError:
        pytest.skip("requires valid live wiki credentials (.env present but login failed)")


class TestLiveWikiIntegration:
    """Live English Wikipedia integration tests (require credentials; skipped in CI)."""

    @requires_creds
    @pytest.mark.network
    def test_does_title_exist(self, repository: WikiRepository):
        assert repository.does_title_exist("Barack Obama")
        assert repository.does_title_exist("Mickey Mouse")
        assert not repository.does_title_exist("DumDeeDooDum")
        assert not repository.does_title_exist("Invalid title")

    @requires_creds
    @pytest.mark.network
    def test_has_lead_section(self, repository):
        assert repository.has_lead_section("Wikipedia:WikiProject Medicine/Popular pages")
        assert not repository.has_lead_section("User:Community Tech bot/Popular pages config.json")

    @requires_creds
    @pytest.mark.network
    def test_set_text(self, repository):
        result = repository.set_text("User:NKohli (WMF)/sandbox", "Hi there! This is a test")
        assert result["edit"]["result"] == "Success"


class TestGetBotLastEditDate:
    """Tests for `get_bot_last_edit_date`."""

    @pytest.mark.network
    def test_basic(self):
        repo = WikiRepository(dry_run=True)
        result1 = repo.get_bot_last_edit_date("Wikipedia:WikiProject A Song of Ice and Fire/Popular pages")
        assert result1 == "2026-08-04"


class TestWriteDryRunText:
    """Tests for `_write_dry_run_text` dry-run persistence."""

    def test_writes_file_with_sanitized_title(self, tmp_path, monkeypatch):
        repo = WikiRepository.__new__(WikiRepository)
        repo.log_dir = tmp_path
        repo.wiki = "en.wikipedia"
        repo.log_dir = tmp_path

        title = "Wikipedia:WikiProject Medicine/Popular pages"
        text = "== List ==\n| A | B\n"
        repo._write_dry_run_text(title, text)

        files = list(tmp_path.glob("*.wikitext"))
        assert len(files) == 1

        # Colons and slashes in the title must be sanitized out of the filename.
        assert ":" not in files[0].name and "/" not in files[0].name
        # The dry-run writer prepends a 'Title:' header referencing the page.
        expected = f"Title: [[{title}]]\n\n{text}"
        assert files[0].read_text(encoding="utf-8") == expected

    def test_filename_includes_wiki(self, tmp_path, monkeypatch):

        repo = WikiRepository.__new__(WikiRepository)
        repo.log_dir = tmp_path
        repo.wiki = "ar.wikipedia"
        repo._write_dry_run_text("User:Foo/Bar", "x")
        assert any("ar.wikipedia" in f.name for f in tmp_path.glob("*.wikitext"))


class TestWikiRepositoryPureMethods:
    """Unit tests for parse-only WikiRepository methods that need no network/DB."""

    JSON = {"P": {"Report": "P/r", "Limit": "5", "Name": "N"}}

    def test_sort_and_truncate_pages_list(self):
        out = {"a": {"pageviews": 3}, "b": {"pageviews": 10}, "c": {"pageviews": 1}}
        res = WikiRepository._sort_and_truncate_pages_list(out, 2)
        assert list(res.keys()) == ["b", "a"]
        # Limit larger than the list keeps everything.
        assert len(WikiRepository._sort_and_truncate_pages_list(out, 100)) == 3

    def test_get_config_parses_json(self):
        repo = WikiRepository.__new__(WikiRepository)
        repo.log_dir = tmp_path
        repo.get_json_config = lambda *a, **k: self.JSON
        configs = repo.get_config()
        assert isinstance(configs, list)
        assert configs[0].Name == "N"

    def test_get_project_by_name(self):
        repo = WikiRepository.__new__(WikiRepository)
        repo.log_dir = tmp_path
        repo.get_json_config = lambda *a, **k: self.JSON
        assert repo.get_project("N").Name == "N"  # pyright: ignore[reportOptionalMemberAccess]
        assert repo.get_project("Nope") is None

    def test_get_wiki_config_returns_stored(self):
        repo = WikiRepository.__new__(WikiRepository)
        repo.log_dir = tmp_path
        repo.wiki_config = {"category": "X"}
        assert repo.get_wiki_config() == {"category": "X"}

    def test_get_stale_projects_returns_not_updated_this_month(self):
        repo = WikiRepository.__new__(WikiRepository)
        repo.log_dir = tmp_path
        repo.wiki = "en.wikipedia"
        repo.get_json_config = lambda *a, **k: {
            "P": {"Report": "P/r", "Limit": "5", "Name": "N"},
            "Q": {"Report": "Q/r", "Limit": "5", "Name": "M"},
        }
        repo.db = MagicMock()
        now_ts = datetime.now().strftime("%Y%m%d%H%M%S")
        # P was updated this month -> not stale; Q never was -> stale.
        repo.db.get_projects_timestamps.return_value = [
            {"page_title": "P/r", "rev_timestamp": now_ts},
        ]
        stale = repo.get_stale_projects()
        names = {c.Name for c in stale}
        assert "M" in names
        assert "N" not in names

    def test_get_json_config_strips_description(self):
        repo = WikiRepository.__new__(WikiRepository)
        repo.log_dir = tmp_path
        repo.wiki_config_page = "Wikipedia:WikiProject/Popular pages config.json"
        repo.site = MagicMock()
        payload = {
            "P": {"Report": "P/r", "Limit": "5", "Name": "N"},
            "description": "Please do not modify manually.",
        }
        repo.site.pages[repo.wiki_config_page].text.return_value = json.dumps(payload)
        data = repo.get_json_config()
        assert "description" not in data
        assert data["P"]["Name"] == "N"

    def test_get_projects_with_last_bot_timestamp(self):
        repo = WikiRepository.__new__(WikiRepository)
        repo.log_dir = tmp_path
        repo.db = MagicMock()
        repo.db.get_projects_timestamps.return_value = [
            {"page_title": "P/r", "rev_timestamp": "20240101120000"},
        ]
        repo.get_config = lambda *a, **k: [
            WikiProjectConfig.from_json("P", data={"Report": "P/r", "Limit": "5", "Name": "N"})
        ]
        result = repo.get_projects_with_last_bot_timestamp()
        assert result == [{"page_title": "P/r", "rev_timestamp": "20240101120000"}]


class TestWikiRepositoryPureMethodsSkipped:
    """Live-network tests for WikiRepository methods (skipped without credentials)."""

    # @pytest.mark.skip(
    #     reason="Disabled upstream in the PHP version too (was 'ertestGetProjectPages', never actually run by PHPUnit)."
    # )
    @pytest.mark.network
    def test_get_project_pages(self, repository):
        rows = repository.get_project_pages("Disney")
        titles = [row["page_title"] for row in rows]
        assert "Walt Disney" in titles
        assert "Pixar" in titles
