"""Regression tests for the shared SQLite account-ID reservation table."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine

from api.user_ids import initialize_user_id_reservations, reserve_user_id


def _create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
        connection.executemany(
            "INSERT INTO users (id, username) VALUES (?, ?)",
            [(-5, "negative"), (0, "zero"), (41, "existing")],
        )
        initialize_user_id_reservations(connection)
        initialize_user_id_reservations(connection)


def test_initialization_and_sqlite_rollback_preserve_existing_users(tmp_path: Path) -> None:
    db_path = tmp_path / "ids.db"
    _create_database(db_path)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT id, username FROM users ORDER BY id"
        ).fetchall() == [(-5, "negative"), (0, "zero"), (41, "existing")]
        assert connection.execute(
            "SELECT id FROM rpp_user_id_reservations ORDER BY id"
        ).fetchall() == [(-5,), (0,), (41,)]
        first = reserve_user_id(connection)
        connection.execute("INSERT INTO users VALUES (?, ?)", (first, "rolled-back"))
        connection.rollback()
        assert connection.execute(
            "SELECT id FROM rpp_user_id_reservations WHERE id = ?", (first,)
        ).fetchone() is None
        replacement = reserve_user_id(connection)
        connection.execute("INSERT INTO users VALUES (?, ?)", (replacement, "replacement"))
        connection.commit()
        connection.execute("DELETE FROM users WHERE id = ?", (replacement,))
        connection.commit()
        assert reserve_user_id(connection) > replacement


def test_sqlalchemy_rollback_keeps_reservation_atomic(tmp_path: Path) -> None:
    db_path = tmp_path / "ids.db"
    _create_database(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            first = reserve_user_id(connection)
            connection.exec_driver_sql(
                "INSERT INTO users VALUES (?, ?)", (first, "rolled-back")
            )
            transaction.rollback()
        with engine.begin() as connection:
            assert connection.exec_driver_sql(
                "SELECT id FROM rpp_user_id_reservations WHERE id = ?", (first,)
            ).fetchone() is None
            replacement = reserve_user_id(connection)
            connection.exec_driver_sql(
                "INSERT INTO users VALUES (?, ?)", (replacement, "replacement")
            )
            assert replacement > 41
    finally:
        engine.dispose()


def test_concurrent_sqlite_and_sqlalchemy_registrations_do_not_reuse_ids(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "ids.db"
    _create_database(db_path)
    engine = create_engine(f"sqlite:///{db_path}")

    def register(index: int) -> int:
        if index % 2:
            with sqlite3.connect(db_path) as connection:
                user_id = reserve_user_id(connection)
                connection.execute(
                    "INSERT INTO users VALUES (?, ?)", (user_id, f"user-{index}")
                )
                return user_id
        with engine.begin() as connection:
            user_id = reserve_user_id(connection)
            connection.exec_driver_sql(
                "INSERT INTO users VALUES (?, ?)", (user_id, f"user-{index}")
            )
            return user_id

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(register, range(16)))
        assert len(set(ids)) == 16
        assert all(user_id > 41 for user_id in ids)
        with sqlite3.connect(db_path) as connection:
            registered = connection.execute(
                "SELECT id FROM users WHERE id > 41"
            ).fetchall()
            reserved = connection.execute(
                "SELECT id FROM rpp_user_id_reservations WHERE id > 41"
            ).fetchall()
        assert {row[0] for row in registered} == set(ids)
        assert {row[0] for row in reserved} == set(ids)
    finally:
        engine.dispose()
