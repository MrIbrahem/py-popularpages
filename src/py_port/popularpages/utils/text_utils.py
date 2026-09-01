"""
Report generation and saving, ported from src/ReportUpdater.php.

Uses Jinja2 in place of Twig for rendering the wikitext report and index
page templates.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)


def uc_first(value: str) -> str:
    """
    Capitalize only the first character, leaving the rest untouched
    (Jinja's builtin `capitalize` also lowercases the remainder, unlike
    PHP's ucfirst() / Twig's custom filter used here)."""
    result = value[:1].upper() + value[1:] if value else value
    logger.debug("uc_first(%r) -> %r", value, result)
    return result


# -- Module-level helpers ---------------------------------------------------


def mediawiki_timestamp_to_epoch(timestamp: str) -> float:
    """
    Convert a MediaWiki DB-style timestamp (YYYYMMDDHHMMSS) to a Unix epoch."""

    dt = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    epoch = dt.timestamp()
    logger.debug("mediawiki_timestamp_to_epoch(%s) -> %s", timestamp, epoch)
    return epoch


def mediawiki_timestamp_to_date(timestamp: str) -> str:
    """
    Convert an ISO 8601 MediaWiki API timestamp to YYYY-MM-DD."""

    # API (formatversion=2) timestamps look like '2023-01-15T00:00:00Z'.
    dt = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    result = dt.strftime("%Y-%m-%d")
    logger.debug("mediawiki_timestamp_to_date(%s) -> %s", timestamp, result)
    return result


def first_of_this_month_timestamp(now: datetime | None = None) -> float:
    """
    Unix epoch for midnight on the first day of the current month (UTC)."""
    if now is None:
        now = datetime.now(timezone.utc)

    # Remove projects from the config that have already been updated.
    epoch = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    logger.debug("first_of_this_month_timestamp(%s) -> %s", now, epoch)
    return epoch


def format_date(value: date, fmt: str = "%Y-%m-%d") -> str:
    """
    Custom 'date' Jinja filter accepting PHP-style format strings
    (this project only ever uses 'Y-m-d'), so templates ported from
    Twig don't need their format-string literals rewritten."""
    php_to_strftime = {"Y": "%Y", "m": "%m", "d": "%d"}

    strftime_fmt = "".join(php_to_strftime.get(ch, ch) for ch in fmt)
    result = value.strftime(strftime_fmt)
    logger.debug("format_date(%s, %s) -> %s", value, fmt, result)
    return result


__all__ = [
    "format_date",
    "uc_first",
    "first_of_this_month_timestamp",
    "mediawiki_timestamp_to_date",
    "mediawiki_timestamp_to_epoch",
]
