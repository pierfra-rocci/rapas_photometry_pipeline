#!/usr/bin/env python3
"""
Migration script to scope FITS content-hash uniqueness to each user.

Historically ``fits_files.sha256`` carried a column-level UNIQUE constraint,
so the *second* user to upload a given file was rejected with HTTP 409 and
could infer that somebody else already held that exact content. The ORM now
enforces ``UNIQUE (user_id, sha256)`` instead, which still de-duplicates a
user's own uploads (same behaviour for a single account) but never blocks one
user because of another user's data.

SQLite cannot drop a column-level UNIQUE constraint, so the table is rebuilt
in place. This script is idempotent and preserves all existing rows.

Usage:
    python scripts/migrate_scope_fits_hash_to_user.py [--db-path PATH]

Options:
    --db-path PATH    Path to the SQLite database file.
                      Default: uses api/config.py settings (users.db or users_dev.db)
    --skip-backup     Skip creating a backup of the database

The script will:
1. Create a backup of the existing database (unless --skip-backup)
2. Rebuild ``fits_files`` with UNIQUE (user_id, sha256)
3. Verify the resulting schema and row count
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path to import api modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TABLE_NAME = "fits_files"
TEMP_TABLE_NAME = "fits_files_new"
TARGET_CONSTRAINT = "uq_fits_user_sha256"


def get_default_db_path() -> Path:
    """Get the default database path from api/config.py."""
    try:
        from api.config import DB_PATH
        return DB_PATH
    except ImportError:
        # Fallback to default location
        return Path(__file__).resolve().parent.parent / "users.db"


def backup_database(db_path: Path) -> Path:
    """Create a timestamped backup of the database.

    Returns:
        Path to the backup file.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.parent / f"{db_path.stem}_backup_{timestamp}{db_path.suffix}"

    print(f"Creating backup: {backup_path}")
    shutil.copy2(db_path, backup_path)

    return backup_path


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check if a table exists in the database."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cursor.fetchone() is not None


def unique_index_columns(conn: sqlite3.Connection, table_name: str) -> set[tuple[str, ...]]:
    """Return the column tuples of every UNIQUE index on a table.

    A column-level ``UNIQUE`` shows up here as an auto-index over exactly that
    column, which is what makes the old global constraint detectable.
    """
    indexes: set[tuple[str, ...]] = set()
    for row in conn.execute(f"PRAGMA index_list({table_name})"):  # noqa: S608
        # (seq, name, unique, origin, partial)
        name, is_unique = row[1], row[2]
        if not is_unique:
            continue
        columns = tuple(
            info[2] for info in conn.execute(f"PRAGMA index_info({name})")  # noqa: S608
        )
        indexes.add(columns)
    return indexes


def needs_migration(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Decide whether the table rebuild is required.

    Returns:
        (needs_rebuild, reason)
    """
    if not table_exists(conn, TABLE_NAME):
        return False, f"table '{TABLE_NAME}' does not exist yet"

    indexes = unique_index_columns(conn, TABLE_NAME)
    if ("user_id", "sha256") in indexes:
        return False, f"already scoped to (user_id, sha256) via {TARGET_CONSTRAINT}"

    if ("sha256",) in indexes:
        return True, "found a global UNIQUE (sha256) constraint"

    # No global hash constraint: nothing to rebuild, but make sure the
    # per-user constraint is not silently missing either.
    return False, "no global UNIQUE (sha256) constraint found"


def build_new_table_ddl() -> str:
    """Compile the corrected CREATE TABLE statement from the ORM model.

    Generating the DDL from ``FitsFile`` keeps the migrated schema identical to
    what SQLAlchemy expects on a fresh database.
    """
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.schema import CreateTable

    from api.models import FitsFile

    ddl = str(
        CreateTable(FitsFile.__table__).compile(dialect=sqlite.dialect())
    ).strip()
    # Retarget the compiled statement at the staging table name.
    prefix = f"CREATE TABLE {TABLE_NAME} ("
    if not ddl.startswith(prefix):
        raise RuntimeError(f"Unexpected DDL from ORM:\n{ddl}")
    return f"CREATE TABLE {TEMP_TABLE_NAME} (" + ddl[len(prefix):]


def column_names(conn: sqlite3.Connection, table_name: str) -> list[str]:
    """Return the column names of a table, in definition order."""
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")]  # noqa: S608


def rebuild_fits_files(conn: sqlite3.Connection) -> None:
    """Rebuild ``fits_files`` with the per-user hash constraint.

    Foreign keys are disabled for the duration because child rows in
    ``analysis_jobs`` point at this table; the primary keys are copied verbatim
    so those references stay valid.
    """
    columns = column_names(conn, TABLE_NAME)
    if not columns:
        raise RuntimeError(f"Could not read columns for '{TABLE_NAME}'")

    row_count_before = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE_NAME}"  # noqa: S608
    ).fetchone()[0]

    quoted = ", ".join(f'"{name}"' for name in columns)
    conn.execute(f"DROP TABLE IF EXISTS {TEMP_TABLE_NAME}")  # noqa: S608
    conn.execute(build_new_table_ddl())
    conn.execute(
        f"INSERT INTO {TEMP_TABLE_NAME} ({quoted}) "  # noqa: S608
        f"SELECT {quoted} FROM {TABLE_NAME}"
    )
    conn.execute(f"DROP TABLE {TABLE_NAME}")  # noqa: S608
    conn.execute(f"ALTER TABLE {TEMP_TABLE_NAME} RENAME TO {TABLE_NAME}")  # noqa: S608

    row_count_after = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE_NAME}"  # noqa: S608
    ).fetchone()[0]
    if row_count_after != row_count_before:
        raise RuntimeError(
            f"Row count changed during rebuild: {row_count_before} -> {row_count_after}"
        )
    print(f"  Rebuilt '{TABLE_NAME}' with {row_count_after} row(s) preserved")


def verify_migration(conn: sqlite3.Connection) -> dict:
    """Confirm the rebuilt schema enforces the intended constraints."""
    result: dict = {}
    result["table_exists"] = table_exists(conn, TABLE_NAME)
    result["row_count"] = (
        conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]  # noqa: S608
        if result["table_exists"]
        else None
    )
    indexes = unique_index_columns(conn, TABLE_NAME) if result["table_exists"] else set()
    result["unique_indexes"] = indexes
    result["global_hash_constraint_gone"] = ("sha256",) not in indexes
    result["user_hash_constraint_present"] = ("user_id", "sha256") in indexes
    result["staging_table_gone"] = not table_exists(conn, TEMP_TABLE_NAME)
    result["ok"] = (
        result["table_exists"]
        and result["global_hash_constraint_gone"]
        and result["user_hash_constraint_present"]
        and result["staging_table_gone"]
    )
    return result


def run_migration(db_path: Path, skip_backup: bool = False) -> int:
    """Run the migration.

    Args:
        db_path: Path to the SQLite database.
        skip_backup: If True, skip creating a backup.

    Returns:
        0 on success, 1 on error.
    """
    print(f"\n{'='*60}")
    print("Scope FITS hash uniqueness to each user")
    print(f"{'='*60}\n")

    print(f"Database: {db_path}")

    if not db_path.exists():
        print(f"\nERROR: Database file not found: {db_path}")
        print("Please ensure the database exists before running this migration.")
        return 1

    # Step 1: Backup
    if not skip_backup:
        print("\n[Step 1/3] Creating backup...")
        try:
            backup_path = backup_database(db_path)
            print(f"  Backup created: {backup_path}")
        except Exception as e:
            print(f"  ERROR creating backup: {e}")
            return 1
    else:
        print("\n[Step 1/3] Skipping backup (--skip-backup flag)")

    # Step 2: Rebuild the table if needed
    print("\n[Step 2/3] Checking fits_files constraints...")

    conn = sqlite3.connect(db_path)
    try:
        rebuild, reason = needs_migration(conn)
        if rebuild:
            print(f"  Migration needed: {reason}")
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                conn.execute("BEGIN")
                rebuild_fits_files(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
        else:
            print(f"  No migration needed: {reason}")
    except Exception as e:
        print(f"\n  ERROR during migration: {e}")
        conn.close()
        return 1

    # Step 3: Verify
    print("\n[Step 3/3] Verifying migration...")

    try:
        verification = verify_migration(conn)
        conn.close()
    except Exception as e:
        print(f"\n  ERROR during verification: {e}")
        conn.close()
        return 1

    print("\n  Verification:")
    print(f"    fits_files present            : {verification['table_exists']}")
    print(f"    rows preserved                : {verification['row_count']}")
    print(f"    global UNIQUE(sha256) removed : {verification['global_hash_constraint_gone']}")
    print(f"    UNIQUE(user_id, sha256) added : {verification['user_hash_constraint_present']}")
    print(f"    staging table cleaned up      : {verification['staging_table_gone']}")
    print(f"    unique indexes                : {sorted(verification['unique_indexes'])}")

    if not verification["ok"]:
        print("\n  ERROR: post-migration schema does not look correct.")
        return 1

    print(f"\n{'='*60}")
    print("Migration completed successfully!")
    print(f"{'='*60}\n")

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Scope the fits_files content-hash uniqueness to each user by "
            "rebuilding the table."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Path to the SQLite database file (default: from api/config.py)",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Skip creating a backup of the database",
    )

    args = parser.parse_args()

    db_path = args.db_path or get_default_db_path()

    return run_migration(db_path, skip_backup=args.skip_backup)


if __name__ == "__main__":
    sys.exit(main())
