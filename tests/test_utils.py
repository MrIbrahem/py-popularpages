"""
Unit tests for pure helper functions in popularpages.utils.
"""

from datetime import date, datetime, timezone

from popularpages.utils import (
    first_of_this_month_timestamp,
    format_date,
    mediawiki_timestamp_to_date,
    mediawiki_timestamp_to_epoch,
    previous_month_range,
    uc_first,
)


class TestUcFirst:
    def test_ucfirst_basic(self):
        assert uc_first("hello") == "Hello"

    def test_ucfirst_empty_string(self):
        assert uc_first("") == ""

    def test_ucfirst_does_not_lowercase_rest(self):
        # Unlike Jinja's builtin `capitalize`, only the first char should change.
        assert uc_first("hELLO") == "HELLO"


class TestPreviousMonthRange:
    def test_previous_month_range_mid_year(self):

        start, end = previous_month_range(date(2024, 6, 15))
        assert start == date(2024, 5, 1)
        assert end == date(2024, 5, 31)

    def test_previous_month_range_year_boundary(self):

        start, end = previous_month_range(date(2024, 1, 10))
        assert start == date(2023, 12, 1)
        assert end == date(2023, 12, 31)

    # ------------------------------------------------------------
    # Pure unit tests (no network/credentials required)
    # ------------------------------------------------------------
    def test_previous_month_range_midyear(self):
        today = datetime(2023, 6, 15, 10, 30, 0)
        start, end = previous_month_range(today)
        assert (start.year, start.month, start.day) == (2023, 5, 1)
        assert (end.year, end.month, end.day) == (2023, 5, 31)

    def test_previous_month_range_year_boundary2(self):
        today = datetime(2023, 1, 10, 0, 0, 0)
        start, end = previous_month_range(today)
        assert (start.year, start.month, start.day) == (2022, 12, 1)
        assert (end.year, end.month, end.day) == (2022, 12, 31)

    def test_previous_month_range_days_in_month(self):
        # February in a non-leap year.
        today = datetime(2023, 3, 5)
        start, end = previous_month_range(today)
        days_in_month = (end - start).days + 1
        assert days_in_month == 28


class TestFormatDate:
    def test_format_date_php_style_format(self):
        formatted = format_date(date(2024, 3, 5), "Y-m-d")
        assert formatted == "2024-03-05"


# first_of_this_month_timestamp
class TestFirstOfThisMonthTimestamp:
    def test_first_of_this_month_timestamp(self):
        now = datetime(2026, 8, 27, tzinfo=timezone.utc)
        assert first_of_this_month_timestamp(now) == 1785542400.0


class TestMediawikiTimestampToDate:
    def test_mediawiki_timestamp_to_date(self):
        # %Y-%m-%dT%H:%M:%SZ
        assert mediawiki_timestamp_to_date("2024-03-05T12:00:00Z") == "2024-03-05"


class TestMediawikiTimestampToEpoch:
    def test_mediawiki_timestamp_to_epoch(self):
        assert mediawiki_timestamp_to_epoch("20240305120000") == 1709640000.0
