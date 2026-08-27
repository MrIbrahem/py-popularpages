"""
Unit tests for pure helper functions in popularpages.utils.
"""

from datetime import date

from popularpages.utils import uc_first, previous_month_range


def test_ucfirst_basic():
    assert uc_first("hello") == "Hello"


def test_ucfirst_empty_string():
    assert uc_first("") == ""


def test_ucfirst_does_not_lowercase_rest():
    # Unlike Jinja's builtin `capitalize`, only the first char should change.
    assert uc_first("hELLO") == "HELLO"


def test_previous_month_range_mid_year(monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2024, 6, 15)

    import popularpages.report_updater as module

    monkeypatch.setattr(module, "date", FakeDate)
    start, end = previous_month_range(date.today())
    assert start == date(2024, 5, 1)
    assert end == date(2024, 5, 31)


def test_previous_month_range_year_boundary(monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2024, 1, 10)

    import popularpages.report_updater as module

    monkeypatch.setattr(module, "date", FakeDate)
    start, end = previous_month_range(date.today())
    assert start == date(2023, 12, 1)
    assert end == date(2023, 12, 31)
