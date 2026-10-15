#!/usr/bin/env python3
"""Migrate legacy Streamlit SQLite data into the SQLAlchemy-backed database."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from typing import Iterable, Mapping

from sqlalchemy import MetaData, Table, and_, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

DEFAULT_UNIQUE_KEYS = {
    "users": ("username", "email"),
    "recovery_codes": ("email",),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert the legacy Streamlit-managed SQLite database to the "
            "SQLAlchemy schema used by the FastAPI backend."
        )
    )
    parser.add_argument(
        "--legacy-db",
        default="users.db",
        help="Path to the legacy users.db SQLite file (default: users.db).",
    )
    parser.add_argument(
        "--target-url",
        default=None,
        help="SQLAlchemy URL for the destination database (overrides config).",
    )
    parser.add_argument(
        "--target-db",
        default=None,
        help=(
            "Filesystem path for destination SQLite database "
            "(shortcut for sqlite:///path)."
        ),
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help=(
            "Skip inserts when a conflicting row already exists in the "
            "target table."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Do not write anything; only report the actions that would be "
            "taken."
        ),
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help=(
            "Create a timestamped backup copy of the legacy database "
            "before migrating."
        ),
    )
    parser.add_argument(
        "--backup-target",
        action="store_true",
        help=(
            "Create a timestamped backup of the target SQLite database "
            "before writing to it."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Do not ask for confirmation before writing to the target.",
    )
    return parser.parse_args()


def resolve_target_url(args: argparse.Namespace) -> str:
    if args.target_url:
        return args.target_url
    if args.target_db:
        path = os.path.abspath(args.target_db)
        return f"sqlite:///{path}"
    try:
        from api.config import SQLALCHEMY_DATABASE_URL  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Could not locate api.config.SQLALCHEMY_DATABASE_URL. Provide "
            "--target-url or --target-db."
        ) from exc
    if not SQLALCHEMY_DATABASE_URL:
        raise SystemExit(
            "api.config.SQLALCHEMY_DATABASE_URL is empty. Provide "
            "--target-url or --target-db."
        )
    return SQLALCHEMY_DATABASE_URL


def ensure_target_schema(engine: Engine) -> None:
    try:
        # Ensure models are registered with SQLAlchemy's metadata.
        import importlib

        importlib.import_module("api.models")  # type: ignore import-error
    except ModuleNotFoundError:
        # The project might bundle models elsewhere; continue and hope
        # metadata already knows them.
        pass
    try:
        from api.database import Base  # type: ignore import-error
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Could not import api.database.Base. Ensure the FastAPI backend "
            "is installed and PYTHONPATH is set."
        ) from exc
    Base.metadata.create_all(bind=engine)


def backup_legacy_db(db_path: str) -> None:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.bak-{timestamp}"
    shutil.copy2(db_path, backup_path)
    print(f"Backup created at {backup_path}")


def sqlite_path_from_url(url: str) -> str | None:
    prefix = "sqlite:///"
    if url.startswith(prefix) and url != prefix + ":memory:":
        return url[len(prefix):] or None
    return None


def backup_target_db(db_path: str) -> None:
    if not os.path.isfile(db_path):
        return
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.bak-{timestamp}"
    with closing(sqlite3.connect(db_path)) as src, closing(
        sqlite3.connect(backup_path)
    ) as dst:
        src.backup(dst)
    print(f"Target backup created at {backup_path}")


def fetch_legacy_rows(
    connection: sqlite3.Connection,
    table_name: str,
) -> list[sqlite3.Row]:
    try:
        cursor = connection.execute(f"SELECT * FROM {table_name}")
        rows = cursor.fetchall()
        print(f"Found {len(rows)} rows in legacy '{table_name}' table.")
        return rows
    except sqlite3.Error as exc:
        print(f"Skipping table '{table_name}': {exc}")
        return []


def build_payload(row: sqlite3.Row, table: Table) -> dict[str, object]:
    payload: dict[str, object] = {}
    table_columns = set(table.c.keys())
    for key in row.keys():
        if key in table_columns:
            value = row[key]
            if isinstance(value, str):
                try:
                    is_datetime = table.c[key].type.python_type is dt.datetime
                except NotImplementedError:
                    is_datetime = False
                if is_datetime:
                    try:
                        value = dt.datetime.fromisoformat(value)
                    except ValueError:
                        pass
            payload[key] = value
    return payload


def row_exists(
    session: Session,
    table: Table,
    payload: Mapping[str, object],
    unique_keys: Iterable[str],
) -> bool:
    filters = []
    for key in unique_keys:
        if key in payload and key in table.c:
            filters.append(table.c[key] == payload[key])
    if not filters:
        return False
    stmt = (
        select(table.c[next(iter(table.c.keys()))])
        .where(and_(*filters))
        .limit(1)
    )
    return session.execute(stmt).first() is not None


def transfer_rows(
    session: Session,
    table: Table,
    rows: Iterable[sqlite3.Row],
    skip_existing: bool,
    dry_run: bool,
) -> None:
    if not rows:
        return
    unique_keys = DEFAULT_UNIQUE_KEYS.get(table.name, ())
    inserted, skipped = 0, 0
    for row in rows:
        payload = build_payload(row, table)
        if not payload:
            continue
        if skip_existing and row_exists(session, table, payload, unique_keys):
            skipped += 1
            continue
        if dry_run:
            inserted += 1
            continue
        session.execute(table.insert().values(**payload))
        inserted += 1
    if dry_run:
        print(
            f"[DRY RUN] Would insert {inserted} rows into '{table.name}' "
            f"(skipped {skipped})."
        )
    else:
        session.commit()
        print(
            f"Inserted {inserted} rows into '{table.name}' "
            f"(skipped {skipped})."
        )


def main() -> None:
    args = parse_args()
    legacy_path = os.path.abspath(args.legacy_db)
    if not os.path.isfile(legacy_path):
        raise SystemExit(f"Legacy database not found at {legacy_path}")

    target_url = resolve_target_url(args)
    print(f"Legacy database: {legacy_path}")
    print(f"Target database URL: {target_url}")

    if args.backup:
        backup_legacy_db(legacy_path)

    target_path = sqlite_path_from_url(target_url)
    if not args.dry_run:
        if args.backup_target and target_path:
            backup_target_db(target_path)
        if (
            not args.yes
            and not args.target_url
            and not args.target_db
            and sys.stdin.isatty()
        ):
            answer = input(
                "No explicit target given: the default application database "
                "will be modified. Type 'yes' to continue: "
            )
            if answer.strip().lower() != "yes":
                raise SystemExit("Aborted.")

    engine = create_engine(target_url, future=True)
    ensure_target_schema(engine)

    metadata = MetaData()
    metadata.reflect(bind=engine)

    required_tables = ["users", "recovery_codes"]
    for table_name in required_tables:
        if table_name not in metadata.tables:
            raise SystemExit(
                "Target database does not define table "
                f"'{table_name}'. Ensure your models are up to date."
            )

    try:
        with closing(sqlite3.connect(legacy_path)) as legacy_conn:
            legacy_conn.row_factory = sqlite3.Row
            table_rows = {
                table: fetch_legacy_rows(legacy_conn, table)
                for table in required_tables
            }
    except sqlite3.Error as exc:
        raise SystemExit(f"Failed to read legacy database: {exc}")

    try:
        with Session(engine, future=True) as session:
            for table_name in required_tables:
                table = metadata.tables[table_name]
                transfer_rows(
                    session=session,
                    table=table,
                    rows=table_rows.get(table_name, []),
                    skip_existing=args.skip_existing,
                    dry_run=args.dry_run,
                )
    except SQLAlchemyError as exc:
        raise SystemExit(f"Migration failed: {exc}")

    if args.dry_run:
        print("Dry run complete. No changes were written.")
    else:
        print("Migration completed successfully.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Aborted by user.")
