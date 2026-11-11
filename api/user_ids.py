"""Persistent SQLite account-ID reservations shared by both backends."""

from __future__ import annotations

import sqlite3
from typing import Any

from sqlalchemy.engine import Connection, CursorResult


def _execute(
    connection: sqlite3.Connection | Connection, statement: str
) -> sqlite3.Cursor | CursorResult[Any]:
    if isinstance(connection, sqlite3.Connection):
        return connection.execute(statement)
    return connection.exec_driver_sql(statement)


def initialize_user_id_reservations(
    connection: sqlite3.Connection | Connection,
) -> None:
    """Reserve all existing account IDs without changing their user records."""
    _execute(
        connection,
        "CREATE TABLE IF NOT EXISTS rpp_user_id_reservations "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT)",
    )
    _execute(
        connection,
        "INSERT OR IGNORE INTO rpp_user_id_reservations (id) SELECT id FROM users",
    )


def reserve_user_id(connection: sqlite3.Connection | Connection) -> int:
    """Allocate an ID in the caller's user-registration transaction."""
    initialize_user_id_reservations(connection)
    result = _execute(connection, "INSERT INTO rpp_user_id_reservations DEFAULT VALUES")
    if result.lastrowid is None:
        raise RuntimeError("The database did not return a reserved user ID.")
    return result.lastrowid
