"""
End-to-end tests for the dump-to-SQLite-cache pipeline.

These build small real bz2 fixture files (using the same lines confirmed
against the actual Wikimedia dump) and run them through the full
:meth:`PageviewsDumpLoader.load_dump_into_cache` pipeline, then verify the
results through the *real* :class:`PageviewsDb` read path -- the same
interface ``ReportUpdater`` and friends will use -- rather than peeking at
SQLite internals directly. This is the check called for in the plan: "test
that the aggregation + SQLite write path produces a PageView table matching
what the REST-based path currently produces, for a small fixture wiki."
"""

from __future__ import annotations

import bz2
from pathlib import Path

import pytest

from src.py_port.popularpages.dumps_parser.pageviews_dump_loader import (
    DumpNotFoundError,
    PageviewsDumpLoader,
)
from src.py_port.popularpages.pageviews.pageviews_db import PageviewsDb

# The exact ar.wikipedia lines from the real dump sample, plus lines for a
# wiki that is NOT configured (aa.wikipedia) and a wiki that IS (en.wikipedia),
# including one deliberately malformed en.wikipedia line.
FIXTURE_LINES = [
    "ar.wikipedia ! 199256 desktop 5 A1S1V1Y1^1",
    "ar.wikipedia ! 496583 desktop 5 A1B1D1I1\\1",
    "ar.wikipedia ! 199256 mobile-web 2 J2",
    "ar.wikipedia !! 2482800 desktop 6 A1B2X3",
    "ar.wikipedia !! 2481200 desktop 4 R1V1\\2",
    "ar.wikipedia !! 2481200 mobile-web 1 J1",
    'ar.wikipedia "\\"" 3347002 desktop 26 C1D1L4Q1S1T1U1Y1Z2[6\\3^3_1',
    'ar.wikipedia "\\"" 3347002 mobile-web 6 E1F1J1K1L1N1',
    # Third " line, daily_total 1 -> confiremd by comment: 26 + 6 + 1 = 33.
    'ar.wikipedia "\\"" 3371336 desktop 1 B1',
    'ar.wikipedia "\\"W\\"_تشير_الى_المنتهي" 7858501 desktop 7 E1F1J1P1T1U1X1',
    'ar.wikipedia "\\"W\\"_تشير_الى_المنتهي" 7858501 mobile-web 2 H1J1',
    # Not configured -> must never appear in output.
    "aa.wikipedia Special:WantedPages null desktop 3 A1Q1^1",
    "aa.wikipedia Special:WhatLinksHere null desktop 24 C3H2S2T3U2V1W2Z1[1\\1]4^1_1",
    # Configured -> two agent rows for the same title, must be summed.
    "en.wikipedia Main_Page 15580374 desktop 1000 A100B200",
    "en.wikipedia Main_Page 15580374 mobile-web 500 A50B50",
    # Malformed (non-numeric daily_total) -> must be skipped, not crash the run.
    "en.wikipedia Some_Page 999 desktop NOTANUMBER A1",
    "en.wikipedia page_zero_total_count 0 desktop 0 A1",
]

WANTED_WIKI_CODES = {"ar.wikipedia", "en.wikipedia"}


def _write_fixture_dump(loader: PageviewsDumpLoader, year: int, month: int, lines: list[str]) -> Path:
    """Write ``lines`` into a real bz2 file at the expected dump path."""
    dump_file = loader._dump_path_for_month(year, month)
    dump_file.parent.mkdir(parents=True, exist_ok=True)
    with bz2.open(dump_file, "wt", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    return dump_file


# ---------------------------------------------------------------------------
# _dump_path_for_month
# ---------------------------------------------------------------------------


def test__dump_path_for_month_pattern(tmp_path: Path):
    loader = PageviewsDumpLoader(views_dir=tmp_path / "views", dumps_root=tmp_path)
    path = loader._dump_path_for_month(2026, 7)
    assert path == tmp_path / "2026" / "2026-07" / "pageviews-202607-user.bz2"


def test__dump_path_for_month_pads_single_digit_month(tmp_path: Path):
    loader = PageviewsDumpLoader(views_dir=tmp_path / "views", dumps_root=tmp_path)
    path = loader._dump_path_for_month(2026, 1)
    assert path.name == "pageviews-202601-user.bz2"
    assert path.parent.name == "2026-01"


# ---------------------------------------------------------------------------
# _iter_dump_lines
# ---------------------------------------------------------------------------


def test_iter_dump_lines_missing_file_raises(tmp_path: Path):
    loader = PageviewsDumpLoader(views_dir=tmp_path / "views", dumps_root=tmp_path)
    missing = tmp_path / "does_not_exist.bz2"
    with pytest.raises(DumpNotFoundError):
        list(loader._iter_dump_lines(missing))


def test_iter_dump_lines_streams_real_bz2_file(tmp_path: Path):
    loader = PageviewsDumpLoader(views_dir=tmp_path / "views", dumps_root=tmp_path / "dumps")
    dump_file = _write_fixture_dump(loader, 2026, 7, FIXTURE_LINES)
    lines = list(loader._iter_dump_lines(dump_file))
    assert len(lines) == len(FIXTURE_LINES)
    assert lines[0].startswith("ar.wikipedia ! 199256")


# ---------------------------------------------------------------------------
# _process_dump_lines
# ---------------------------------------------------------------------------


def test_aggregate_dump_filters_unwanted_wikis(tmp_path: Path):
    views_dir = tmp_path / "views"
    loader = PageviewsDumpLoader(views_dir=views_dir, dumps_root=tmp_path / "dumps")
    totals = loader._process_dump_lines(FIXTURE_LINES, WANTED_WIKI_CODES, "2026-08")
    assert "aa.wikipedia" not in totals
    assert set(totals.keys()) == {"ar.wikipedia", "en.wikipedia"}
    assert totals == {"ar.wikipedia": 4, "en.wikipedia": 1}


class TestAggregateDump:
    """Aggregation behavior, verified through the real SQLite cache read path.

    ``_process_dump_lines`` streams aggregated titles into the SQLite cache in
    bounded-memory batches instead of returning them in memory, so these tests
    run the aggregation against a fixture and read the upserted totals back via
    :class:`PageviewsDb` (the same interface downstream code uses).
    """

    @pytest.fixture
    def views_dir(self, tmp_path: Path) -> Path:
        return tmp_path / "views"

    @pytest.fixture
    def loader(self, views_dir: Path, tmp_path: Path) -> PageviewsDumpLoader:
        return PageviewsDumpLoader(views_dir=views_dir, dumps_root=tmp_path / "dumps")

    def test_aggregate_dump_sums_across_agents_and_page_ids(self, loader: PageviewsDumpLoader, views_dir: Path):
        loader._process_dump_lines(FIXTURE_LINES, WANTED_WIKI_CODES, "2026-08")

        db = PageviewsDb(views_dir / "ar.wikipedia" / "2026-08.sqlite3")
        try:
            views = db.get_views_many(
                {
                    "!": [],
                    "!!": [],
                    '"': [],
                    '"W" تشير الى المنتهي': [],
                }
            )
        finally:
            db.close_db()

        # "!" : 199256/desktop(5) + 496583/desktop(5) + 199256/mobile-web(2) = 12
        assert views["!"] == 12
        # "!!" : 2482800/desktop(6) + 2481200/desktop(4) + 2481200/mobile-web(1) = 11
        assert views["!!"] == 11
        # '"' appears under 3 different page_ids: 26 + 6 + 1 = 33
        assert views['"'] == 33
        # '"W"_تشير_اللى_المنتهي' : same page_id, two agents: 7 + 2 = 9
        assert views['"W" تشير الى المنتهي'] == 9

    def test_aggregate_dump_sums_across_agents_for_en_wikipedia(self, loader: PageviewsDumpLoader, views_dir: Path):
        loader._process_dump_lines(FIXTURE_LINES, WANTED_WIKI_CODES, "2026-08")

        db = PageviewsDb(views_dir / "en.wikipedia" / "2026-08.sqlite3")
        try:
            # Main_Page: desktop(1000) + mobile-web(500) = 1500
            assert db.get_views("Main Page", []) == 1500
        finally:
            db.close_db()

    def test_aggregate_dump_skips_malformed_line_without_crashing(self, loader: PageviewsDumpLoader, views_dir: Path):
        loader._process_dump_lines(FIXTURE_LINES, WANTED_WIKI_CODES, "2026-08")

        db = PageviewsDb(views_dir / "en.wikipedia" / "2026-08.sqlite3")
        try:
            # "Some_Page" had a non-numeric daily_total and must not appear at all.
            assert db.get_views("Some_Page", []) == 0

            assert db.get_views_many({"Some_Page": []}) == {"Some_Page": 0}
        finally:
            db.close_db()

    def test_aggregate_dump_title_filtering_optimization(self, loader: PageviewsDumpLoader, views_dir: Path):
        # Only keep "!" for ar.wikipedia; en.wikipedia unfiltered (no entry).
        wanted_titles = {"ar.wikipedia": {"!"}}
        loader._process_dump_lines(
            FIXTURE_LINES,
            WANTED_WIKI_CODES,
            "2026-08",
            wanted_titles_by_wiki=wanted_titles,
        )

        ar_db = PageviewsDb(views_dir / "ar.wikipedia" / "2026-08.sqlite3")
        try:
            assert ar_db.get_views("!", []) == 12
            # Only "!" was requested, so other ar titles must not be present.
            assert ar_db.get_views("!!", []) == 0
        finally:
            ar_db.close_db()

        en_db = PageviewsDb(views_dir / "en.wikipedia" / "2026-08.sqlite3")
        try:
            # en.wikipedia had no filter entry -> everything (valid) still aggregated.
            assert en_db.get_views("Main Page", []) == 1500
        finally:
            en_db.close_db()


# ---------------------------------------------------------------------------
# End-to-end: load_dump_into_cache -> real SQLite file -> read via PageviewsDb
# ---------------------------------------------------------------------------


def test_load_dump_into_cache_end_to_end(tmp_path: Path):
    dumps_root = tmp_path / "dumps"
    views_dir = tmp_path / "data" / "views"
    loader = PageviewsDumpLoader(views_dir=views_dir, dumps_root=dumps_root)
    _write_fixture_dump(loader, 2026, 7, FIXTURE_LINES)

    result = loader.load_dump_into_cache(
        year=2026,
        month=7,
        wanted_wiki_codes=WANTED_WIKI_CODES,
    )

    assert result == {"ar.wikipedia": 4, "en.wikipedia": 1}

    ar_db_path = views_dir / "ar.wikipedia" / "2026-07.sqlite3"
    en_db_path = views_dir / "en.wikipedia" / "2026-07.sqlite3"
    assert ar_db_path.exists()
    assert en_db_path.exists()
    # aa.wikipedia was never configured -> no directory/file should exist for it.
    assert not (views_dir / "aa.wikipedia").exists()

    # Read back through the *real* PageviewsDb interface -- the same one
    # ReportUpdater uses -- to confirm downstream code needs no changes.
    ar_db = PageviewsDb(ar_db_path)
    try:
        views = ar_db.get_views_many(
            {
                "!": [],
                "!!": [],
                '"': [],
                '"W" تشير الى المنتهي': [],
            }
        )
    finally:
        ar_db.close_db()

    assert views == {
        "!": 12,
        "!!": 11,
        '"': 33,
        '"W" تشير الى المنتهي': 9,
    }

    en_db = PageviewsDb(en_db_path)
    try:
        assert en_db.get_views("Main Page", []) == 1500
        # The malformed line's title must simply not exist in the cache.
        assert en_db.get_views("Some_Page", []) == 0
    finally:
        en_db.close_db()


def test_load_dump_into_cache_missing_dump_raises(tmp_path: Path):
    loader = PageviewsDumpLoader(
        views_dir=tmp_path / "views",
        dumps_root=tmp_path / "empty_dumps_dir",
    )
    with pytest.raises(DumpNotFoundError):
        loader.load_dump_into_cache(
            year=2099,
            month=1,
            wanted_wiki_codes=WANTED_WIKI_CODES,
        )


def test_load_dump_into_cache_is_upsert_not_replace(tmp_path: Path):
    """
    Running the loader twice for the same wiki/month must upsert (update
    existing rows, keep others), matching PageviewsDb.upsert_many semantics,
    not silently duplicate or error out.
    """
    dumps_root = tmp_path / "dumps"
    views_dir = tmp_path / "data" / "views"
    loader = PageviewsDumpLoader(views_dir=views_dir, dumps_root=dumps_root)
    _write_fixture_dump(loader, 2026, 7, FIXTURE_LINES)

    loader.load_dump_into_cache(
        year=2026,
        month=7,
        wanted_wiki_codes=WANTED_WIKI_CODES,
    )

    # Second run, same input -> totals should be identical (upsert of the
    # same values), not doubled.
    loader.load_dump_into_cache(
        year=2026,
        month=7,
        wanted_wiki_codes=WANTED_WIKI_CODES,
    )

    db = PageviewsDb(views_dir / "ar.wikipedia" / "2026-07.sqlite3")
    try:
        assert db.get_views("!", []) == 12
    finally:
        db.close_db()
