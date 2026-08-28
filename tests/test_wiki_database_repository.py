"""
Offline tests for WikiDatabaseRepository.

These verify that BINARY/VARBINARY columns (``page_title``, ``redir_title``,
``rev_timestamp``) returned by PyMySQL as ``bytes`` are decoded to ``str``. PHP's
``mysqli`` returned these as strings, so downstream code (URL building, strptime,
template rendering) expects ``str``. The live-DB tests in the PHP suite that
exercised this path were disabled upstream; these offline tests replace them.
"""

from popularpages.wiki_database_repository import WikiDatabaseRepository


def _make_repo() -> WikiDatabaseRepository:
    return WikiDatabaseRepository(
        wiki="en.wikipedia",
        creds={"dbhost": "x", "dbuser": "u", "dbpass": "p", "dbport": "3306"},
        wiki_config={"database": "enwiki"},
        username="bot",
    )


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        pass

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)

    def close(self):
        pass


def test_get_project_pages_decodes_binary_columns(monkeypatch):
    repo = _make_repo()
    rows = [
        {
            "page_title": b"Foo_Bar",
            "pa_class": b"",
            "pa_importance": b"FA",
            "redir_title": b"Foo",
        }
    ]
    monkeypatch.setattr(repo, "_connect", lambda: _FakeConn(rows))

    result = repo.get_project_pages("X")

    assert result[0]["page_title"] == "Foo_Bar"
    assert result[0]["redir_title"] == "Foo"
    assert result[0]["pa_class"] == ""
    assert result[0]["pa_importance"] == "FA"


def test_get_projects_with_last_bot_timestamp_decodes_binary(monkeypatch):
    repo = _make_repo()
    rows = [{"page_title": b"Popular_pages", "rev_timestamp": b"20230115000000"}]
    monkeypatch.setattr(repo, "_connect", lambda: _FakeConn(rows))

    projects = {"Popular_pages": "MyProject"}
    result = repo.get_projects_timestamps(["Popular_pages"])

    assert result[0]["page_title"] == "Popular_pages"
    assert result[0]["rev_timestamp"] == "20230115000000"
    # assert result[0]["name"] == "MyProject"


def test_get_stale_project_names_parses_str_timestamp(monkeypatch):
    repo = _make_repo()
    # A timestamp far in the future means "already updated this cycle".
    rows = [{"page_title": b"Popular_pages", "rev_timestamp": b"20990101000000"}]
    monkeypatch.setattr(repo, "_connect", lambda: _FakeConn(rows))

    config = {"MyProject": {"Report": "Wikipedia:Popular pages"}}
    projects = {"Popular_pages": "MyProject"}
    updated = repo.get_stale_project_names(config, projects)

    assert "MyProject" in updated
