"""
Unit tests for the pageview_complete dump line parser.
"""

import os

import pytest

from src.py_port.popularpages.dumps_parser.pageviews_dumps_parser import (
    MalformedLineError,
    ParsedPageview,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
AR_SAMPLE_PATH = os.path.join(FIXTURES_DIR, "ar_wikipedia_sample.txt")

# ---------------------------------------------------------------------------
# 1. page_id variants: numeric vs "null"
# ---------------------------------------------------------------------------


def test_page_id_numeric():
    line = "ar.wikipedia ! 199256 desktop 5 A1S1V1Y1^1"
    result = ParsedPageview.parse(line)
    assert result.page_id == "199256"
    assert result.wiki_code == "ar.wikipedia"
    assert result.title == "!"
    assert result.agent == "desktop"
    assert result.daily_total == 5


def test_page_id_null_string():
    line = """ar.wikipedia "\\"_لفيرجينيا_وولف" null desktop 1 ]1"""
    result = ParsedPageview.parse(line)
    assert result.page_id == "null"
    assert result.daily_total == 1
    assert result.title == '"_لفيرجينيا_وولف'


# ---------------------------------------------------------------------------
# 2. Titles with leading special characters (!, ', ()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected_title,expected_page_id,expected_agent,expected_total",
    [
        ("ar.wikipedia ! 199256 desktop 5 A1S1V1Y1^1", "!", "199256", "desktop", 5),
        ("ar.wikipedia ! 496583 desktop 5 A1B1D1I1\\1", "!", "496583", "desktop", 5),
        ("ar.wikipedia ! 199256 mobile-web 2 J2", "!", "199256", "mobile-web", 2),
        ("ar.wikipedia !! 2482800 desktop 6 A1B2X3", "!!", "2482800", "desktop", 6),
        (
            "ar.wikipedia !!_(توضيح) 2481200 desktop 7 I1K1L1M1O1P1R1",
            "!!_(توضيح)",
            "2481200",
            "desktop",
            7,
        ),
        (
            "ar.wikipedia !_(توضيح) 2481196 mobile-web 8 B1J1Q1R1S1W1Z1]1",
            "!_(توضيح)",
            "2481196",
            "mobile-web",
            8,
        ),
    ],
)
def test_leading_special_character_titles(line, expected_title, expected_page_id, expected_agent, expected_total):
    result = ParsedPageview.parse(line)
    assert result.title == expected_title
    assert result.page_id == expected_page_id
    assert result.agent == expected_agent
    assert result.daily_total == expected_total


# ---------------------------------------------------------------------------
# 3. Escaped double-quote titles (\" -> ") — the key new edge case
# ---------------------------------------------------------------------------


def test_unescape_title_helper_directly():
    # Bare titles (no outer wrapping quotes) pass through unchanged.
    assert ParsedPageview.unescape_title("no_quotes_here") == "no_quotes_here"
    assert ParsedPageview.unescape_title("!") == "!"

    # Titles containing a literal quote are wrapped in an outer pair of
    # unescaped quotes, with inner quotes escaped as \" — this wrapper
    # must be stripped and the inner escaping undone.
    assert ParsedPageview.unescape_title('"\\""') == '"'
    assert ParsedPageview.unescape_title('"\\"W\\"_تشير_الى_المنتهي"') == '"W"_تشير_الى_المنتهي'
    assert ParsedPageview.unescape_title('"\\"_\\""') == '"_"'


def test_pure_quote_title():
    # Raw dump line: title field is literally "\"" which represents a
    # single-character title: "
    line = 'ar.wikipedia "\\"" 3347002 desktop 26 C1D1L4Q1S1T1U1Y1Z2[6\\3^3_1'
    result = ParsedPageview.parse(line)
    assert result.title == '"'
    assert result.page_id == "3347002"
    assert result.daily_total == 26


def test_pure_quote_title_different_page_id():
    line = 'ar.wikipedia "\\"" 3371336 desktop 1 B1'
    result = ParsedPageview.parse(line)
    assert result.title == '"'
    assert result.page_id == "3371336"
    assert result.daily_total == 1


def test_mixed_quote_and_arabic_title():
    line = 'ar.wikipedia "\\"W\\"_تشير_الى_المنتهي" 7858501 desktop 7 E1F1J1P1T1U1X1'
    result = ParsedPageview.parse(line)
    assert result.title == '"W"_تشير_الى_المنتهي'
    assert result.page_id == "7858501"
    assert result.daily_total == 7


def test_quote_underscore_quote_title():
    # title field "\"_\"" -> unescaped: "_"
    line = 'ar.wikipedia "\\"_\\"" 3347002 desktop 6 O1S1T1W1Y2'
    result = ParsedPageview.parse(line)
    assert result.title == '"_"'
    assert result.page_id == "3347002"
    assert result.daily_total == 6


def test_quoted_english_title_with_internal_apostrophe():
    # Confirms apostrophes inside an already-quoted title survive untouched
    # (they are NOT escape sequences, just literal characters).
    line = (
        "ar.wikipedia \"\\\"Schumer_announces_'Maple_Tap_Act'_has_passed_the_Senate"
        '_at_part_of_Farm_Bill\\"" null desktop 1 A1'
    )
    result = ParsedPageview.parse(line)
    assert result.title == ("\"Schumer_announces_'Maple_Tap_Act'_has_passed_the_Senate_at_part_of_Farm_Bill\"")
    assert result.page_id == "null"
    assert result.daily_total == 1


def test_quote_prefix_no_suffix_arabic_title():
    # title field "\"_لفيرجينيا_وولف (no closing internal quote in this one)
    line = 'ar.wikipedia "\\"_لفيرجينيا_وولف" null desktop 1 ]1'
    result = ParsedPageview.parse(line)
    assert result.title == '"_لفيرجينيا_وولف'
    assert result.page_id == "null"
    assert result.daily_total == 1


def test_quoted_arabic_only_title():
    line = 'ar.wikipedia "\\"أول_مكرر\\"" null mobile-web 1 E1'
    result = ParsedPageview.parse(line)
    assert result.title == '"أول_مكرر"'
    assert result.page_id == "null"
    assert result.agent == "mobile-web"
    assert result.daily_total == 1


# ---------------------------------------------------------------------------
# 4. hourly_counts is discarded regardless of its (absent/odd) content
# ---------------------------------------------------------------------------


def test_hourly_counts_with_backslash_is_ignored_safely():
    # hourly_counts here contains a literal backslash char - must not
    # affect title/daily_total parsing since it's never inspected.
    line = "ar.wikipedia !! 2481200 desktop 4 R1V1\\2"
    result = ParsedPageview.parse(line)
    assert result.title == "!!"
    assert result.daily_total == 4


def test_daily_total_parses_even_with_trailing_junk():
    line = "ar.wikipedia !_(توضيح) 2481196 desktop 15 F2G1I1J2L1R1T1V1X1Y2]1_1"
    result = ParsedPageview.parse(line)
    assert result.daily_total == 15


# ---------------------------------------------------------------------------
# 5. Same page_id, different title strings -> must NOT be treated as identical
# ---------------------------------------------------------------------------


def test_same_page_id_different_titles_are_distinct():
    line_a = 'ar.wikipedia "\\"_\\"" 3347002 desktop 6 O1S1T1W1Y2'
    line_b = "ar.wikipedia ! 199256 desktop 5 A1S1V1Y1^1"  # different page_id, sanity
    line_c = 'ar.wikipedia "\\"" 3347002 desktop 26 C1D1L4Q1S1T1U1Y1Z2[6\\3^3_1'

    result_a = ParsedPageview.parse(line_a)  # title '"_"', page_id 3347002
    result_c = ParsedPageview.parse(line_c)  # title '"',   page_id 3347002

    assert result_a.page_id == result_c.page_id == "3347002"
    assert result_a.title != result_c.title
    # aggregation key must be title, not page_id
    assert (result_a.wiki_code, result_a.title) != (result_c.wiki_code, result_c.title)


# ---------------------------------------------------------------------------
# 6. Malformed lines
# ---------------------------------------------------------------------------


def test_malformed_line_too_few_fields():
    with pytest.raises(MalformedLineError):
        ParsedPageview.parse("ar.wikipedia only_two_fields")


def test_malformed_line_non_numeric_daily_total():
    with pytest.raises(MalformedLineError):
        ParsedPageview.parse("ar.wikipedia title 123 desktop NOTANUMBER extra")


def test_empty_line_raises():
    with pytest.raises(MalformedLineError):
        ParsedPageview.parse("")


# ---------------------------------------------------------------------------
# 7. Full fixture file: every line in the real sample must parse cleanly
# ---------------------------------------------------------------------------


def test_full_fixture_file_all_lines_parse_without_error():
    with open(AR_SAMPLE_PATH, "r", encoding="utf-8") as f:
        lines = [line for line in f if line.strip()]

    assert len(lines) == 22, "fixture line count changed unexpectedly"

    results = [ParsedPageview.parse(line) for line in lines]

    for r in results:
        assert isinstance(r, ParsedPageview)
        assert r.wiki_code == "ar.wikipedia"
        assert isinstance(r.daily_total, int)
        assert r.daily_total > 0
        # No raw escape sequence should survive into the final title
        assert '\\"' not in r.title


def test_full_fixture_file_no_double_escaped_quotes_remain():
    with open(AR_SAMPLE_PATH, "r", encoding="utf-8") as f:
        lines = [line for line in f if line.strip()]

    results = [ParsedPageview.parse(line) for line in lines]
    quote_titles = [r.title for r in results if '"' in r.title]

    # Sanity: we do expect several titles to legitimately contain a quote
    # character after unescaping.
    assert len(quote_titles) >= 5
    for t in quote_titles:
        assert "\\" not in t or t.count("\\") == 0  # no stray backslashes left


def test_full_fixture_aggregation_by_title_matches_expected_totals():
    """
    Simulates the aggregation step: sum daily_total per (wiki, title),
    across agents (desktop/mobile-web), the way the real pipeline will.
    Verifies a few hand-checked totals from the sample.
    """
    with open(AR_SAMPLE_PATH, "r", encoding="utf-8") as f:
        lines = [line for line in f if line.strip()]

    totals = {}
    for line in lines:
        r = ParsedPageview.parse(line)
        key = (r.wiki_code, r.title)
        totals[key] = totals.get(key, 0) + r.daily_total

    # "!" appears 3 times: 199256/desktop(5) + 496583/desktop(5) + 199256/mobile-web(2) = 12
    assert totals[("ar.wikipedia", "!")] == 12

    # "!!" appears 3 times: 2482800/desktop(6) + 2481200/desktop(4) + 2481200/mobile-web(1) = 11
    assert totals[("ar.wikipedia", "!!")] == 11

    # '"' (pure quote title) appears 3 times: 26 + 6 + 1 = 33, across 3 different page_ids
    assert totals[("ar.wikipedia", '"')] == 33

    # '"_"' appears 3 times: 6 + 2 + 1 = 9, across 2 different page_ids
    assert totals[("ar.wikipedia", '"_"')] == 9

    # '"W"_تشير_الى_المنتهي' appears twice, same page_id: 7 + 2 = 9
    assert totals[("ar.wikipedia", '"W"_تشير_الى_المنتهي')] == 9
