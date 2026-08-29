"""
Tests for src.popularpages.db_analytics.maps.WikiReplicaMaps.

The meta_p query path requires a live replica, so these tests exercise the
file-backed cache: loading a local wikimap, resolving identifiers, and saving.
The DB loader is stubbed out so construction never touches the network.
"""

import json
import time

import pytest

from src.popularpages.db_analytics.maps import WikiReplicaMaps

SAMPLE_ROW = {"lang": "en", "dbname": "enwiki", "url": "en.wikipedia.org", "slice": "s1"}


@pytest.fixture
def no_db(monkeypatch):
    """Stub out the live DB loader so construction never hits the network."""
    monkeypatch.setattr(WikiReplicaMaps, "_load_new_maps", lambda self: {"enwiki": dict(SAMPLE_ROW)})
    yield


@pytest.fixture
def reset_singleton():
    WikiReplicaMaps._instance = None
    yield
    WikiReplicaMaps._instance = None


def _write_fresh_map(path):
    """
    Write a fresh test cache file containing a timestamp and sample English wiki mapping.

    Parameters:
        path (Path): Destination path for the JSON cache file
    """
    path.write_text(
        json.dumps({"last_cache_update": time.time(), "data": {"enwiki": dict(SAMPLE_ROW)}}),
        encoding="utf-8",
    )


def test_load_local_wikimap_present(tmp_path, no_db, reset_singleton):
    f = tmp_path / "wikimap.json"
    _write_fresh_map(f)

    maps = WikiReplicaMaps(file_name=str(f))
    assert maps._wiki_map == {"enwiki": dict(SAMPLE_ROW)}
    assert "enwiki" in maps._wiki_map


def test_load_local_wikimap_missing(tmp_path, no_db, reset_singleton):
    f = tmp_path / "wikimap.json"  # never created
    maps = WikiReplicaMaps(file_name=str(tmp_path / "present.json"))  # safe constructor
    maps.file_name = str(f)
    assert maps.load_local_wikimap().data == {}


def test_load_local_wikimap_corrupt(tmp_path, no_db, reset_singleton):
    f = tmp_path / "wikimap.json"
    f.write_text("not json{", encoding="utf-8")
    maps = WikiReplicaMaps(file_name=str(tmp_path / "present.json"))  # safe constructor
    maps.file_name = str(f)
    assert maps.load_local_wikimap().data == {}


def test_resolve_wiki_direct(tmp_path, no_db, reset_singleton):
    f = tmp_path / "wikimap.json"
    _write_fresh_map(f)
    maps = WikiReplicaMaps(file_name=str(f))
    assert maps.resolve_wiki("enwiki")["dbname"] == "enwiki"


def test_resolve_wiki_with_wiki_suffix(tmp_path, no_db, reset_singleton):
    f = tmp_path / "wikimap.json"
    _write_fresh_map(f)
    maps = WikiReplicaMaps(file_name=str(f))
    # "en" matches "enwiki".
    assert maps.resolve_wiki("en")["dbname"] == "enwiki"


def test_resolve_wiki_no_match(tmp_path, no_db, reset_singleton):
    f = tmp_path / "wikimap.json"
    _write_fresh_map(f)
    maps = WikiReplicaMaps(file_name=str(f))
    assert maps.resolve_wiki("zzwiki") is None


def test_save_wikimap_writes_file(tmp_path, no_db, reset_singleton):
    f = tmp_path / "wikimap.json"
    maps = WikiReplicaMaps(file_name=str(f))
    maps._wiki_map = {"enwiki": dict(SAMPLE_ROW)}
    maps.save_wikimap(time.time())

    loaded = json.loads(f.read_text(encoding="utf-8"))
    assert loaded["data"] == {"enwiki": dict(SAMPLE_ROW)}


def test_save_wikimap_skipped_when_disabled(tmp_path, no_db, reset_singleton):
    f = tmp_path / "wikimap.json"
    maps = WikiReplicaMaps(file_name=str(f), save_new_data=False)
    maps._wiki_map = {"enwiki": dict(SAMPLE_ROW)}
    maps.save_wikimap(time.time())
    assert not f.exists()
