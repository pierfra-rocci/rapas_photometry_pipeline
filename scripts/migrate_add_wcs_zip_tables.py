#!/usr/bin/env python3
"""
Migration script to add WCS FITS and ZIP Archive tracking tables.

This script safely adds new tables to an existing database without
destroying existing data. It can be run multiple times (idempotent).

Usage:
    python scripts/migrate_add_wcs_zip_tables.py [--db-path PATH]

Options:
    --db-path PATH    Path to the SQLite database file.
                      Default: uses api/config.py settings (users.db or users_dev.db)

The script will:
1. Create a backup of the existing database
2. Add new tables: wcs_fits_files, zip_archives, wcs_fits_zip_assoc
3. Skip tables that already exist
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
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    )
    return cursor.fetchone() is not None


def create_wcs_fits_files_table(conn: sqlite3.Connection) -> bool:
    """Create the wcs_fits_files table if it doesn't exist.

    Returns:
        True if table was created, False if it already exists.
    """
    if table_exists(conn, "wcs_fits_files"):
        print("  Table 'wcs_fits_files' already exists - skipping")
        return False

    print("  Creating table 'wcs_fits_files'...")
    conn.execute("""
        CREATE TABLE wcs_fits_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            original_filename VARCHAR(255) NOT NULL,
            stored_filename VARCHAR(512) NOT NULL,
            has_wcs BOOLEAN NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            CONSTRAINT uq_wcs_fits_user_filename UNIQUE (user_id, stored_filename)
        )
    """)

    # Create index for faster lookups
    conn.execute("""
        CREATE INDEX idx_wcs_fits_user_id ON wcs_fits_files(user_id)
    """)

    return True


def create_zip_archives_table(conn: sqlite3.Connection) -> bool:
    """Create the zip_archives table if it doesn't exist.

    Returns:
        True if table was created, False if it already exists.
    """
    if table_exists(conn, "zip_archives"):
        print("  Table 'zip_archives' already exists - skipping")
        return False

    print("  Creating table 'zip_archives'...")
    conn.execute("""
        CREATE TABLE zip_archives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            archive_filename VARCHAR(255) NOT NULL,
            stored_relpath VARCHAR(1024) NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            CONSTRAINT uq_zip_user_relpath UNIQUE (user_id, stored_relpath)
        )
    """)

    # Create index for faster lookups
    conn.execute("""
        CREATE INDEX idx_zip_archives_user_id ON zip_archives(user_id)
    """)

    return True


def create_wcs_fits_zip_assoc_table(conn: sqlite3.Connection) -> bool:
    """Create the junction table for WCS FITS to ZIP associations.

    Returns:
        True if table was created, False if it already exists.
    """
    if table_exists(conn, "wcs_fits_zip_assoc"):
        print("  Table 'wcs_fits_zip_assoc' already exists - skipping")
        return False

    print("  Creating table 'wcs_fits_zip_assoc'...")
    conn.execute("""
        CREATE TABLE wcs_fits_zip_assoc (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wcs_fits_id INTEGER NOT NULL,
            zip_archive_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            FOREIGN KEY (wcs_fits_id) REFERENCES wcs_fits_files(id),
            FOREIGN KEY (zip_archive_id) REFERENCES zip_archives(id),
            CONSTRAINT uq_wcs_fits_zip UNIQUE (wcs_fits_id, zip_archive_id)
        )
    """)

    # Create indexes for faster lookups
    conn.execute("""
        CREATE INDEX idx_wcs_fits_zip_fits_id ON wcs_fits_zip_assoc(wcs_fits_id)
    """)
    conn.execute("""
        CREATE INDEX idx_wcs_fits_zip_archive_id ON wcs_fits_zip_assoc(zip_archive_id)
    """)

    return True


def verify_migration(conn: sqlite3.Connection) -> dict:
    """Verify that all tables exist and return their row counts.

    Returns:
        Dictionary with table names and row counts.
    """
    tables = ["wcs_fits_files", "zip_archives", "wcs_fits_zip_assoc"]
    result = {}

    for table in tables:
        if table_exists(conn, table):
            cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
            count = cursor.fetchone()[0]
            result[table] = count
        else:
            result[table] = None  # Table doesn't exist

    return result


def print_table_info(conn: sqlite3.Connection, table_name: str) -> None:
    """Print the schema of a table."""
    cursor = conn.execute(f"PRAGMA table_info({table_name})")  # noqa: S608
    columns = cursor.fetchall()

    print(f"\n  {table_name}:")
    print("    Columns:")
    for col in columns:
        col_id, name, dtype, notnull, default, pk = col
        nullable = "" if notnull else " (nullable)"
        primary = " [PK]" if pk else ""
        print(f"      - {name}: {dtype}{nullable}{primary}")


def run_migration(db_path: Path, skip_backup: bool = False) -> int:
    """Run the migration.

    Args:
        db_path: Path to the SQLite database.
        skip_backup: If True, skip creating a backup.

    Returns:
        0 on success, 1 on error.
    """
    print(f"\n{'=' * 60}")
    print("WCS FITS and ZIP Archive Tables Migration")
    print(f"{'=' * 60}\n")

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

    # Step 2: Create tables
    print("\n[Step 2/3] Creating new tables...")

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")

        tables_created = 0

        if create_wcs_fits_files_table(conn):
            tables_created += 1

        if create_zip_archives_table(conn):
            tables_created += 1

        if create_wcs_fits_zip_assoc_table(conn):
            tables_created += 1

        conn.commit()

        if tables_created > 0:
            print(f"\n  Created {tables_created} new table(s)")
        else:
            print("\n  No new tables needed - all tables already exist")

    except Exception as e:
        print(f"\n  ERROR during migration: {e}")
        conn.rollback()
        conn.close()
        return 1

    # Step 3: Verify
    print("\n[Step 3/3] Verifying migration...")

    try:
        verification = verify_migration(conn)

        print("\n  Table Status:")
        all_ok = True
        for table, count in verification.items():
            if count is not None:
                print(f"    ✓ {table}: OK ({count} rows)")
            else:
                print(f"    ✗ {table}: MISSING")
                all_ok = False

        if all_ok:
            print("\n  Schema details:")
            for table in verification.keys():
                print_table_info(conn, table)

        conn.close()

        if not all_ok:
            print("\n  WARNING: Some tables are missing!")
            return 1

    except Exception as e:
        print(f"\n  ERROR during verification: {e}")
        conn.close()
        return 1

    print(f"\n{'=' * 60}")
    print("Migration completed successfully!")
    print(f"{'=' * 60}\n")

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Add WCS FITS and ZIP Archive tracking tables to the database.",
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
