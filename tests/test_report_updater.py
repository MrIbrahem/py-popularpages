"""
Unit tests for pure helper functions in popularpages.report_updater.

These don't require network/DB access, unlike ReportUpdater itself (which
constructs a WikiRepository on init and therefore needs live credentials).
"""

from popularpages.report_updater import ReportUpdater  # noqa: F401
