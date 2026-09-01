"""
End-to-end tests for the dump-to-SQLite-cache pipeline.

These build small real bz2 fixture files (using the same lines confirmed
against the actual Wikimedia dump) and run them through the full
:func:`load_dump_into_cache` pipeline, then verify the results through the
*real* :class:`PageviewsDb` read path -- the same interface
``ReportUpdater`` and friends will use -- rather than peeking at SQLite
internals directly. This is the check called for in the plan: "test that the
aggregation + SQLite write path produces a PageView table matching what the
REST-based path currently produces, for a small fixture wiki."
"""

from __future__ import annotations

import bz2
from pathlib import Path

import pytest

from src.py_port.popularpages.pageviews.pageviews_db import PageviewsDb
from src.py_port.dumps_parser.pageviews_dump_loader import (
    DumpNotFoundError,
    aggregate_dump,
    dump_path_for_month,
    iter_dump_lines,
    load_dump_into_cache,
)

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
]

WANTED_WIKI_CODES = {"ar.wikipedia", "en.wikipedia"}


def _write_fixture_dump(dumps_root: Path, year: int, month: int, lines: list[str]) -> Path:
    """Write ``lines`` into a real bz2 file at the expected dump path."""
    dump_file = dump_path_for_month(year, month, root=dumps_root)
    dump_file.parent.mkdir(parents=True, exist_ok=True)
    with bz2.open(dump_file, "wt", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    return dump_file


# ---------------------------------------------------------------------------
# dump_path_for_month
# ---------------------------------------------------------------------------

def test_dump_path_for_month_pattern(tmp_path: Path):
    path = dump_path_for_month(2026, 7, root=tmp_path)
    assert path == tmp_path / "2026" / "2026-07" / "pageviews-202607-user.bz2"


def test_dump_path_for_month_pads_single_digit_month(tmp_path: Path):
    path = dump_path_for_month(2026, 1, root=tmp_path)
    assert path.name == "pageviews-202601-user.bz2"
    assert path.parent.name == "2026-01"


# ---------------------------------------------------------------------------
# iter_dump_lines
# ---------------------------------------------------------------------------

def test_iter_dump_lines_missing_file_raises(tmp_path: Path):
    missing = tmp_path / "does_not_exist.bz2"
    with pytest.raises(DumpNotFoundError):
        list(iter_dump_lines(missing))


def test_iter_dump_lines_streams_real_bz2_file(tmp_path: Path):
    dump_file = _write_fixture_dump(tmp_path, 2026, 7, FIXTURE_LINES)
    lines = list(iter_dump_lines(dump_file))
    assert len(lines) == len(FIXTURE_LINES)
    assert lines[0].startswith("ar.wikipedia ! 199256")


# ---------------------------------------------------------------------------
# aggregate_dump
# ---------------------------------------------------------------------------

def test_aggregate_dump_filters_unwanted_wikis():
    totals = aggregate_dump(FIXTURE_LINES, WANTED_WIKI_CODES)
    assert "aa.wikipedia" not in totals
    assert set(totals.keys()) == {"ar.wikipedia", "en.wikipedia"}


def test_aggregate_dump_sums_across_agents_and_page_ids():
    totals = aggregate_dump(FIXTURE_LINES, WANTED_WIKI_CODES)
    ar_totals = totals["ar.wikipedia"]

    # "!" : 199256/desktop(5) + 496583/desktop(5) + 199256/mobile-web(2) = 12
    assert ar_totals["!"] == 12
    # "!!" : 2482800/desktop(6) + 2481200/desktop(4) + 2481200/mobile-web(1) = 11
    assert ar_totals["!!"] == 11
    # '"' appears under 3 different page_ids: 26 + 6 + 1 = 33
    assert ar_totals['"'] == 33
    # '"W"_تشير_الى_المنتهي' : same page_id, two agents: 7 + 2 = 9
    assert ar_totals['"W"_تشير_الى_المنتهي'] == 9


def test_aggregate_dump_sums_across_agents_for_en_wikipedia():
    totals = aggregate_dump(FIXTURE_LINES, WANTED_WIKI_CODES)
    en_totals = totals["en.wikipedia"]
    # Main_Page: desktop(1000) + mobile-web(500) = 1500
    assert en_totals["Main_Page"] == 1500


def test_aggregate_dump_skips_malformed_line_without_crashing():
    totals = aggregate_dump(FIXTURE_LINES, WANTED_WIKI_CODES)
    # "Some_Page" had a non-numeric daily_total and must not appear at all.
    assert "Some_Page" not in totals["en.wikipedia"]


def test_aggregate_dump_title_filtering_optimization():
    # Only keep "!" for ar.wikipedia; en.wikipedia unfiltered (no entry).
    wanted_titles = {"ar.wikipedia": {"!"}}
    totals = aggregate_dump(FIXTURE_LINES, WANTED_WIKI_CODES, wanted_titles_by_wiki=wanted_titles)

    assert set(totals["ar.wikipedia"].keys()) == {"!"}
    assert totals["ar.wikipedia"]["!"] == 12
    # en.wikipedia had no filter entry -> everything (valid) still aggregated.
    assert totals["en.wikipedia"]["Main_Page"] == 1500


# ---------------------------------------------------------------------------
# End-to-end: load_dump_into_cache -> real SQLite file -> read via PageviewsDb
# ---------------------------------------------------------------------------

def test_load_dump_into_cache_end_to_end(tmp_path: Path):
    dumps_root = tmp_path / "dumps"
    views_dir = tmp_path / "data" / "views"
    _write_fixture_dump(dumps_root, 2026, 7, FIXTURE_LINES)

    result = load_dump_into_cache(
        year=2026,
        month=7,
        wanted_wiki_codes=WANTED_WIKI_CODES,
        views_dir=views_dir,
        dumps_root=dumps_root,
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
                '"W"_تشير_الى_المنتهي': [],
            }
        )
    finally:
        ar_db.close_db()

    assert views == {
        "!": 12,
        "!!": 11,
        '"': 33,
        '"W"_تشير_الى_المنتهي': 9,
    }

    en_db = PageviewsDb(en_db_path)
    try:
        assert en_db.get_views("Main_Page", []) == 1500
        # The malformed line's title must simply not exist in the cache.
        assert en_db.get_views("Some_Page", []) == 0
    finally:
        en_db.close_db()


def test_load_dump_into_cache_missing_dump_raises(tmp_path: Path):
    with pytest.raises(DumpNotFoundError):
        load_dump_into_cache(
            year=2099,
            month=1,
            wanted_wiki_codes=WANTED_WIKI_CODES,
            views_dir=tmp_path / "views",
            dumps_root=tmp_path / "empty_dumps_dir",
        )


def test_load_dump_into_cache_is_upsert_not_replace(tmp_path: Path):
    """
    Running the loader twice for the same wiki/month must upsert (update
    existing rows, keep others), matching PageviewsDb.upsert_many semantics,
    not silently duplicate or error out.
    """
    dumps_root = tmp_path / "dumps"
    views_dir = tmp_path / "data" / "views"
    _write_fixture_dump(dumps_root, 2026, 7, FIXTURE_LINES)

    load_dump_into_cache(
        year=2026,
        month=7,
        wanted_wiki_codes=WANTED_WIKI_CODES,
        views_dir=views_dir,
        dumps_root=dumps_root,
    )

    # Second run, same input -> totals should be identical (upsert of the
    # same values), not doubled.
    load_dump_into_cache(
        year=2026,
        month=7,
        wanted_wiki_codes=WANTED_WIKI_CODES,
        views_dir=views_dir,
        dumps_root=dumps_root,
    )

    db = PageviewsDb(views_dir / "ar.wikipedia" / "2026-07.sqlite3")
    try:
        assert db.get_views("!", []) == 12
    finally:
        db.close_db()
