"""
Report generation and saving, ported from src/ReportUpdater.php.

Uses Jinja2 in place of Twig for rendering the wikitext report and index
page templates.
"""

from __future__ import annotations

from datetime import date, timedelta

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

__all__ = [
    "uc_first",
    "previous_month_range",
]
