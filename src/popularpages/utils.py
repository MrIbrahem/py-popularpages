"""
Report generation and saving, ported from src/ReportUpdater.php.

Uses Jinja2 in place of Twig for rendering the wikitext report and index
page templates.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def uc_first(value: str) -> str:
    """
    Capitalize only the first character, leaving the rest untouched
    (Jinja's builtin `capitalize` also lowercases the remainder, unlike
    PHP's ucfirst() / Twig's custom filter used here)."""
    return value[:1].upper() + value[1:] if value else value


def previous_month_range(today: date) -> tuple[date, date]:
    """
    Return (first, last) day of the month preceding ``today``.

    Python has no ``strtotime('first day of previous month')`` equivalent,
    so compute it manually. Verified against year boundaries.
    """
    first_of_this_month = today.replace(day=1)
    last_day_of_prev_month = first_of_this_month - timedelta(days=1)
    first_day_of_prev_month = last_day_of_prev_month.replace(day=1)
    # TODO: Check diffrent
    # end = last_day_of_prev_month.replace()
    # return first_day_of_prev_month, end
    return first_day_of_prev_month, last_day_of_prev_month


# -- Module-level helpers ---------------------------------------------------


def mediawiki_timestamp_to_epoch(timestamp: str) -> float:
    """
    Convert a MediaWiki DB-style timestamp (YYYYMMDDHHMMSS) to a Unix epoch."""

    dt = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return dt.timestamp()


def mediawiki_timestamp_to_date(timestamp: str) -> str:
    """
    Convert an ISO 8601 MediaWiki API timestamp to YYYY-MM-DD."""

    # API (formatversion=2) timestamps look like '2023-01-15T00:00:00Z'.
    dt = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    return dt.strftime("%Y-%m-%d")


def first_of_this_month_timestamp(now: datetime | None = None) -> float:
    """
    Unix epoch for midnight on the first day of the current month (UTC)."""
    if now is None:
        now = datetime.now(timezone.utc)

    # Remove projects from the config that have already been updated.
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def format_date(value: date, fmt: str = "%Y-%m-%d") -> str:
    """
    Custom 'date' Jinja filter accepting PHP-style format strings
    (this project only ever uses 'Y-m-d'), so templates ported from
    Twig don't need their format-string literals rewritten."""
    php_to_strftime = {"Y": "%Y", "m": "%m", "d": "%d"}

    strftime_fmt = "".join(php_to_strftime.get(ch, ch) for ch in fmt)
    return value.strftime(strftime_fmt)


__all__ = [
    "format_date",
    "uc_first",
    "previous_month_range",
    "first_of_this_month_timestamp",
    "mediawiki_timestamp_to_date",
    "mediawiki_timestamp_to_epoch",
]
