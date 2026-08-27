"""Unit tests for pure helper functions in popularpages.report_updater.

These don't require network/DB access, unlike ReportUpdater itself (which
constructs a WikiRepository on init and therefore needs live credentials).
"""

from datetime import date

from popularpages.report_updater import ReportUpdater, _previous_month_range, _ucfirst


def test_ucfirst_basic():
    assert _ucfirst("hello") == "Hello"


def test_ucfirst_empty_string():
    assert _ucfirst("") == ""


def test_ucfirst_does_not_lowercase_rest():
    # Unlike Jinja's builtin `capitalize`, only the first char should change.
    assert _ucfirst("hELLO") == "HELLO"


def test_previous_month_range_mid_year(monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2024, 6, 15)

    import popularpages.report_updater as module

    monkeypatch.setattr(module, "date", FakeDate)
    start, end = _previous_month_range()
    assert start == date(2024, 5, 1)
    assert end == date(2024, 5, 31)


def test_previous_month_range_year_boundary(monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2024, 1, 10)

    import popularpages.report_updater as module

    monkeypatch.setattr(module, "date", FakeDate)
    start, end = _previous_month_range()
    assert start == date(2023, 12, 1)
    assert end == date(2023, 12, 31)


def test_format_date_php_style_format():
    formatted = ReportUpdater._format_date(date(2024, 3, 5), "Y-m-d")
    assert formatted == "2024-03-05"
