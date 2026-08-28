"""
Unit tests for pure helper functions in src.popularpages.report_updater.

These don't require network/DB access, unlike ReportUpdater itself (which
constructs a WikiRepository on init and therefore needs live credentials).
"""

from src.popularpages.report_updater import ReportUpdater  # noqa: F401
