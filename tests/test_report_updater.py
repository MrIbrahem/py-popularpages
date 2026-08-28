"""
Unit tests for pure helper functions in src.popularpages.report_updater.

These don't require network/DB access, unlike ReportUpdater itself (which
constructs a WikiRepository on init and therefore needs live credentials).
"""

from src.popularpages.report_updater import ReportUpdater  # noqa: F401


SAMPLE_ASSESSMENT_CONFIG = {
    "class": {
        "Featured article": {"color": "#ff6600", "category": "Category:FA-Class"},
        "A": {"color": "#66ff66", "category": "Category:A-Class"},
        "Unknown": {"color": "#cccccc", "category": "Category:Unknown-Class"},
    },
    "importance": {
        "Top": {"color": "#ff0000", "category": "Category:Top-Importance"},
        "Unknown": {"color": "#cccccc", "category": "Category:Unknown-Importance"},
    },
}


def test_resolve_assessment_exact_match():
    result = ReportUpdater._resolve_assessment(
        SAMPLE_ASSESSMENT_CONFIG, "class", "Featured article"
    )
    assert result == {"color": "#ff6600", "category": "Category:FA-Class"}


def test_resolve_assessment_case_insensitive():
    result = ReportUpdater._resolve_assessment(
        SAMPLE_ASSESSMENT_CONFIG, "class", "featured ARTICLE"
    )
    assert result["category"] == "Category:FA-Class"


def test_resolve_assessment_unknown_falls_back():
    result = ReportUpdater._resolve_assessment(
        SAMPLE_ASSESSMENT_CONFIG, "class", "Something weird"
    )
    assert result == SAMPLE_ASSESSMENT_CONFIG["class"]["Unknown"]


def test_resolve_assessment_importance():
    result = ReportUpdater._resolve_assessment(
        SAMPLE_ASSESSMENT_CONFIG, "importance", "Top"
    )
    assert result == {"color": "#ff0000", "category": "Category:Top-Importance"}
