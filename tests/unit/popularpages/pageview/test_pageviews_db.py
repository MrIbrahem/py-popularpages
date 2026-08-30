# ruff: noqa: F401
"""
Tests for src.py_port.popularpages.pageviews.pageviews_db.PageviewsDb.

TODO: write tests
"""

import sqlite3

import pytest

import src.py_port.popularpages.config as cfg
import src.py_port.popularpages.pageviews.pageviews_db as cache_module

pytestmark = pytest.mark.asyncio
