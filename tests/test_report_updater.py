"""
Unit tests for pure helper functions in popularpages.report_updater.

These don't require network/DB access, unlike ReportUpdater itself (which
constructs a WikiRepository on init and therefore needs live credentials).
"""

from datetime import date

from popularpages.report_updater import ReportUpdater

def test_format_date_php_style_format():
    formatted = ReportUpdater._format_date(date(2024, 3, 5), "Y-m-d")
    assert formatted == "2024-03-05"
