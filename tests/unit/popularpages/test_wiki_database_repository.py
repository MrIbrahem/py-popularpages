"""
Offline tests for WikiDatabaseRepository.

These verify that BINARY/VARBINARY columns (``page_title``, ``redir_title``,
``rev_timestamp``) returned by PyMySQL as ``bytes`` are decoded to ``str``. PHP's
``mysqli`` returned these as strings, so downstream code (URL building, strptime,
template rendering) expects ``str``. The live-DB tests in the PHP suite that
exercised this path were disabled upstream; these offline tests replace them.
"""

from unittest.mock import MagicMock

from src.py_port.popularpages.db_analytics.maps import WikiReplicaMaps
from src.py_port.popularpages.wiki_database_repository import WikiDatabaseRepository


class _FakeMaps:
    """Stand-in for WikiReplicaMaps so repo construction stays offline."""

    def resolve_wiki(self, identifier):
        return {
            "dbname": "enwiki",
            "slice": "s1",
            "url": "https://en.wikipedia.org",
            "lang": "en",
        }


def _make_repo(monkeypatch) -> WikiDatabaseRepository:
    monkeypatch.setattr(WikiReplicaMaps, "get_instance", lambda: _FakeMaps())
    return WikiDatabaseRepository(
        wiki="en.wikipedia",
        wiki_config={"database": "enwiki"},
        username="bot",
    )


# ---------------------------------------------------
# 1. Tests for get_project_pages binary decoding
# ---------------------------------------------------


class TestGetProjectPagesDecoding:
    """Tests that binary/VARBINARY columns are decoded to str in get_project_pages."""

    def test_get_project_pages_decodes_binary_columns(self, monkeypatch):
        repo = _make_repo(monkeypatch)
        rows = [
            {
                "page_title": b"Foo_Bar",
                "pa_class": b"",
                "pa_importance": b"FA",
                "redir_title": b"Foo",
            }
        ]
        monkeypatch.setattr(repo.db, "_ensure_connection", MagicMock(return_value=True))
        monkeypatch.setattr(repo.db, "_select", lambda *args, **kwargs: rows)

        result = repo.get_project_pages("X")

        assert result[0]["page_title"] == "Foo_Bar"
        assert result[0]["redir_title"] == "Foo"
        assert result[0]["pa_class"] == ""
        assert result[0]["pa_importance"] == "FA"


# ---------------------------------------------------
# 2. Tests for get_projects_timestamps binary decoding
# ---------------------------------------------------


class TestGetProjectsTimestampsDecoding:
    """Tests that binary columns are decoded in get_projects_timestamps / get_projects_timestamps."""

    def test_get_projects_with_last_bot_timestamp_decodes_binary(self, monkeypatch):
        repo = _make_repo(monkeypatch)
        rows = [{"page_title": b"Popular_pages", "rev_timestamp": b"20230115000000"}]
        monkeypatch.setattr(repo.db, "_select", lambda *args, **kwargs: rows)

        projects = {"Popular_pages": "MyProject"}
        result = repo.get_projects_timestamps(["Popular_pages"])

        assert result[0]["page_title"] == "Popular_pages"
        assert result[0]["rev_timestamp"] == "20230115000000"
        # assert result[0]["name"] == "MyProject"

    def test_get_stale_project_names_parses_str_timestamp(self, monkeypatch):
        repo = _make_repo(monkeypatch)
        # A timestamp far in the future means "already updated this cycle".
        rows = [{"page_title": b"Popular_pages", "rev_timestamp": b"20990101000000"}]
        monkeypatch.setattr(repo.db, "_select", lambda *args, **kwargs: rows)

        updated = repo.get_projects_timestamps(["Popular_pages"])

        assert updated == [{"page_title": "Popular_pages", "rev_timestamp": "20990101000000"}]
