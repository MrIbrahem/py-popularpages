"""
Tests for src.py_port.popularpages.db_analytics.replica_db.

The DB connection paths require a live replica, so we only exercise the
pure helpers (decode_value / resolve_bytes / get_sql) and the
no-credentials connection guard, which can be tested without a database.
"""

import sys

import pymysql
import pytest

from src.py_port.popularpages.db_analytics.replica_db import (
    WikiReplicaBaseDB,
    decode_value,
    get_sql,
    resolve_bytes,
)


# ---------------------------------------------------
# 1. Tests for decode_value bytes-to-str decoding
# ---------------------------------------------------
class TestDecodeValue:
    """Tests for `decode_value` bytes-to-str decoding."""

    def test_decode_value_bytes_valid(self):
        assert decode_value(b"hello") == "hello"

    def test_decode_value_bytes_invalid_falls_back_to_str(self):
        # Invalid UTF-8 -> str() of the bytes object.
        assert decode_value(b"\xff\xfe") == "b'\\xff\\xfe'"

    def test_decode_value_non_bytes(self):
        assert decode_value("already str") == "already str"


# ---------------------------------------------------
# 2. Tests for resolve_bytes row decoding
# ---------------------------------------------------
class TestResolveBytes:
    """Tests for `resolve_bytes` row decoding."""

    def test_resolve_bytes_decodes_byte_values(self):
        rows = [{"a": b"x", "b": 1}, {"a": "y", "b": 2}]
        decoded = resolve_bytes(rows)
        assert decoded == [{"a": "x", "b": 1}, {"a": "y", "b": 2}]

    def test_resolve_bytes_empty(self):
        assert resolve_bytes([]) == []


# ---------------------------------------------------
# 3. Tests for the get_sql connection-guard flag
# ---------------------------------------------------
class TestGetSql:
    """Tests for the `get_sql` connection-guard flag."""

    def test_get_sql_requires_credentials(self, monkeypatch):
        monkeypatch.delenv("TOOL_REPLICA_USER", raising=False)
        monkeypatch.delenv("TOOL_REPLICA_PASSWORD", raising=False)
        monkeypatch.setattr(sys, "argv", ["popularpages"])
        assert get_sql() is False

    def test_get_sql_nosql_flag_disables(self, monkeypatch):
        monkeypatch.setenv("TOOL_REPLICA_USER", "u")
        monkeypatch.setenv("TOOL_REPLICA_PASSWORD", "p")
        monkeypatch.setattr(sys, "argv", ["popularpages", "-nosql"])
        assert get_sql() is False

    def test_get_sql_enabled_with_credentials(self, monkeypatch):
        monkeypatch.setenv("TOOL_REPLICA_USER", "u")
        monkeypatch.setenv("TOOL_REPLICA_PASSWORD", "p")
        monkeypatch.setattr(sys, "argv", ["popularpages"])
        assert get_sql() is True


# ---------------------------------------------------
# 4. Tests for the DB connection guard without credentials
# ---------------------------------------------------
class TestEnsureConnection:
    """Tests for the DB connection guard without credentials."""

    def test_ensure_connection_raises_without_credentials(self, monkeypatch):
        monkeypatch.delenv("TOOL_REPLICA_USER", raising=False)
        monkeypatch.delenv("TOOL_REPLICA_PASSWORD", raising=False)
        db = WikiReplicaBaseDB(
            dbname="enwiki",
            host="localhost",
            user=None,  # pyright: ignore[reportArgumentType]
            password=None,  # pyright: ignore[reportArgumentType]
        )
        with pytest.raises(pymysql.err.OperationalError):
            db._ensure_connection()
