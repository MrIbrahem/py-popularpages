from __future__ import annotations

import logging
import os
import sys
from collections.abc import Sequence
from typing import Any

import pymysql
import pymysql.connections
import pymysql.cursors

logger = logging.getLogger(__name__)


def decode_value(value) -> str:
    try:
        value = value.decode("utf-8")  # Assuming UTF-8 encoding
    except BaseException:
        try:
            value = str(value)
        except BaseException:
            logger.debug("decode_value: could not decode value, returning empty string")
            return ""
    return value


def resolve_bytes(rows) -> list[Any]:
    decoded_rows: list = []
    # ---
    for row in rows:
        decoded_row = {}
        for key, value in row.items():
            if isinstance(value, bytes):
                value = decode_value(value)
            decoded_row[key] = value
        decoded_rows.append(decoded_row)
    # ---
    logger.debug("resolve_bytes: decoded %d row(s)", len(decoded_rows))
    return decoded_rows


class WikiReplicaBaseDB:
    """
    A lightweight, well-behaved connection wrapper for Wikimedia wiki replica databases.
    This class is intended for read-only access.
    """

    def __init__(
        self,
        dbname: str,
        host: str,
        user: str,
        password: str,
        port: int = 3306,
    ) -> None:
        self.dbname = dbname
        self.host = host
        self.user = user
        self.password = password
        self.port = port
        self.connection: pymysql.connections.Connection | None = None

    def _ensure_connection(self) -> None:
        if not self.user or not self.password:
            logger.exception("No credentials provided, using anonymous connection.")
            raise pymysql.err.OperationalError("No credentials provided")

        args = {
            "user": self.user,
            "password": self.password,
        }
        if self.connection is None or not self.connection.open:
            logger.info("Opening connection to %s/%s", self.host, self.dbname)
            try:
                self.connection = pymysql.connect(
                    host=self.host,
                    database=self.dbname,
                    port=self.port,
                    charset="utf8mb4",
                    # force_unicode and use_unicode ensure bytes are converted to strings
                    use_unicode=True,
                    init_command="SET NAMES utf8mb4",  # Forces the connection to use utf8mb4
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=10,
                    **args,  # type: ignore
                )  # type: ignore
            except pymysql.Error as e:
                logger.error(f"Failed to connect to {self.host}/{self.dbname}: {e}")
                raise

            logger.debug("Connection to %s/%s established", self.host, self.dbname)

    def select(
        self,
        query: str,
        params: Sequence[Any] | dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Executes query and returns all results as a list of dictionaries.
        """
        self._ensure_connection()
        assert self.connection is not None
        logger.debug("Executing query on %s/%s: %s", self.host, self.dbname, query)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(query, params)
                rows = cursor.fetchall()
                result = resolve_bytes(rows)
                logger.debug("Query returned %d row(s)", len(result))
                return result
        except pymysql.Error as e:
            logger.error(f"Query failed: {e}")
            logger.info(query)
            raise

    def select_one(
        self,
        query: str,
        params: Sequence[Any] | dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Executes query and returns the first result or None.
        """
        results = self.select(query, params)
        logger.debug("select_one: %s row(s) returned", len(results))
        return results[0] if results else None

    def select_safe(
        self,
        query: str,
        params: Sequence[Any] | dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """ """
        results: list[dict[str, Any]] = []
        try:
            results = self.select(query, params)
        except pymysql.ProgrammingError:
            logger.exception("Query failed", exc_info=True)
            logger.info(query)
        except pymysql.Error as e:
            logger.exception("Query failed", exc_info=True)
            logger.info(query)
        logger.debug("select_safe returned %d row(s)", len(results))
        return results

    def close(self) -> None:
        if self.connection and self.connection.open:
            logger.debug("Closing connection to %s/%s", self.host, self.dbname)
            self.connection.close()
            self.connection = None

    def __enter__(self) -> WikiReplicaBaseDB:
        self._ensure_connection()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def get_sql() -> bool:
    """
    Checks if SQL database replica access is allowed and enabled.
    Returns True if replica credentials exist and 'nosql' is not requested in sys.argv.
    """

    if not os.getenv("TOOL_REPLICA_USER"):
        logger.debug("get_sql: no TOOL_REPLICA_USER set, SQL disabled")
        return False

    for arg in sys.argv:
        clean_arg = arg.removeprefix("-").partition(":")[0]
        if clean_arg == "nosql":
            return False
        if clean_arg == "usesql":
            return True

    return True


__all__ = [
    "WikiReplicaBaseDB",
    "decode_value",
    "resolve_bytes",
    "get_sql",
]
