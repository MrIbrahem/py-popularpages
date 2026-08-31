"""
Unit tests for pure helper functions in src.py_port.popularpages.utils.
"""

from datetime import date, datetime, timezone

from src.py_port.popularpages.utils import (
    first_of_this_month_timestamp,
    format_date,
    mediawiki_timestamp_to_date,
    mediawiki_timestamp_to_epoch,
    uc_first,
)


class TestUcFirst:
    """Tests for the uc_first helper that capitalizes the first character."""

    def test_ucfirst_basic(self):
        assert uc_first("hello") == "Hello"

    def test_ucfirst_empty_string(self):
        assert uc_first("") == ""

    def test_ucfirst_does_not_lowercase_rest(self):
        # Unlike Jinja's builtin `capitalize`, only the first char should change.
        assert uc_first("hELLO") == "HELLO"


class TestFormatDate:
    """Tests for formatting dates with PHP-style format strings."""

    def test_format_date_php_style_format(self):
        formatted = format_date(date(2024, 3, 5), "Y-m-d")
        assert formatted == "2024-03-05"


# first_of_this_month_timestamp
class TestFirstOfThisMonthTimestamp:
    """Tests for the timestamp of the first day of the current month."""

    def test_first_of_this_month_timestamp(self):
        now = datetime(2026, 8, 27, tzinfo=timezone.utc)
        assert first_of_this_month_timestamp(now) == 1785542400.0


class TestMediawikiTimestampToDate:
    """Tests for converting MediaWiki timestamps to date strings."""

    def test_mediawiki_timestamp_to_date(self):
        # %Y-%m-%dT%H:%M:%SZ
        assert mediawiki_timestamp_to_date("2024-03-05T12:00:00Z") == "2024-03-05"


class TestMediawikiTimestampToEpoch:
    """Tests for converting MediaWiki timestamps to Unix epoch values."""

    def test_mediawiki_timestamp_to_epoch(self):
        assert mediawiki_timestamp_to_epoch("20240305120000") == 1709640000.0
