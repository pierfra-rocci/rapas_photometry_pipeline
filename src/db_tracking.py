"""
Database helper functions for tracking WCS FITS files and ZIP archives.

This module provides functions to record FITS files and ZIP archives
in the database from the Streamlit frontend.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional, Tuple

# Try to import from api module if available
try:
    from api.config import DB_PATH
except ImportError:
    # Fallback: database in project root
    DB_PATH = Path(__file__).resolve().parent.parent / "users.db"


def get_db_connection() -> sqlite3.Connection:
    """Get a connection to the SQLite database.
    
    Returns:
        sqlite3.Connection: Database connection.
    
    Raises:
        FileNotFoundError: If database doesn't exist.
    """
    db_path = Path(DB_PATH) if isinstance(DB_PATH, str) else DB_PATH
    
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def get_user_id_by_username(username: str) -> Optional[int]:
    """Look up user ID from username.
    
    Args:
        username: The username to look up.
        
    Returns:
        User ID if found, None otherwise.
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,)
        )
        row = cursor.fetchone()
        conn.close()
        return row["id"] if row else None
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()


def record_wcs_fits_file(
    username: str,
    original_filename: str,
    stored_filename: str,
    has_wcs: bool = True
) -> Optional[int]:
    """Record a WCS FITS file in the database.
    
    If a file with the same stored_filename exists for this user,
    returns the existing record ID (upsert behavior).
    
    Args:
        username: The username who owns the file.
        original_filename: Original uploaded filename.
        stored_filename: Filename as stored in rpp_data/fits/.
        has_wcs: True if file has WCS solution, False if original.
        
    Returns:
        The wcs_fits_file ID if successful, None on error.
    """
    conn = None
    try:
        user_id = get_user_id_by_username(username)
        if user_id is None:
            print(f"Warning: User '{username}' not found in database")
            return None
        
        conn = get_db_connection()
        
        # Check if record already exists
        cursor = conn.execute(
            """
            SELECT id FROM wcs_fits_files 
            WHERE user_id = ? AND stored_filename = ?
            """,
            (user_id, stored_filename)
        )
        existing = cursor.fetchone()
        
        if existing:
            # Return existing ID
            conn.close()
            return existing["id"]
        
        # Insert new record
        cursor = conn.execute(
            """
            INSERT INTO wcs_fits_files (user_id, original_filename, stored_filename, has_wcs)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, original_filename, stored_filename, 1 if has_wcs else 0)
        )
        conn.commit()
        fits_id = cursor.lastrowid
        conn.close()
        
        return fits_id
        
    except Exception as e:
        print(f"Warning: Could not record WCS FITS file in database: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


def record_zip_archive(
    username: str,
    archive_filename: str,
    stored_relpath: str
) -> Optional[int]:
    """Record a ZIP archive in the database.
    
    Args:
        username: The username who owns the archive.
        archive_filename: Just the ZIP filename.
        stored_relpath: Relative path where ZIP is stored.
        
    Returns:
        The zip_archive ID if successful, None on error.
    """
    conn = None
    try:
        user_id = get_user_id_by_username(username)
        if user_id is None:
            print(f"Warning: User '{username}' not found in database")
            return None
        
        conn = get_db_connection()
        
        # Check if record already exists
        cursor = conn.execute(
            """
            SELECT id FROM zip_archives 
            WHERE user_id = ? AND stored_relpath = ?
            """,
            (user_id, stored_relpath)
        )
        existing = cursor.fetchone()
        
        if existing:
            # Return existing ID
            conn.close()
            return existing["id"]
        
        # Insert new record
        cursor = conn.execute(
            """
            INSERT INTO zip_archives (user_id, archive_filename, stored_relpath)
            VALUES (?, ?, ?)
            """,
            (user_id, archive_filename, stored_relpath)
        )
        conn.commit()
        zip_id = cursor.lastrowid
        conn.close()
        
        return zip_id
        
    except Exception as e:
        print(f"Warning: Could not record ZIP archive in database: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


def link_fits_to_zip(wcs_fits_id: int, zip_archive_id: int) -> bool:
    """Create an association between a WCS FITS file and a ZIP archive.
    
    Args:
        wcs_fits_id: ID of the WCS FITS file.
        zip_archive_id: ID of the ZIP archive.
        
    Returns:
        True if successful or already exists, False on error.
    """
    conn = None
    try:
        conn = get_db_connection()
        
        # Check if association already exists
        cursor = conn.execute(
            """
            SELECT id FROM wcs_fits_zip_assoc 
            WHERE wcs_fits_id = ? AND zip_archive_id = ?
            """,
            (wcs_fits_id, zip_archive_id)
        )
        
        if cursor.fetchone():
            # Already linked
            conn.close()
            return True
        
        # Create association
        conn.execute(
            """
            INSERT INTO wcs_fits_zip_assoc (wcs_fits_id, zip_archive_id)
            VALUES (?, ?)
            """,
            (wcs_fits_id, zip_archive_id)
        )
        conn.commit()
        conn.close()
        
        return True
        
    except Exception as e:
        print(f"Warning: Could not link FITS to ZIP in database: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()


def record_analysis_result(
    username: str,
    original_filename: str,
    stored_fits_filename: str,
    zip_archive_filename: str,
    zip_stored_relpath: str,
    has_wcs: bool = True
) -> Tuple[Optional[int], Optional[int]]:
    """Convenience function to record both FITS and ZIP, and link them.
    
    This is the main function to call after an analysis is complete.
    
    Args:
        username: The username who owns the files.
        original_filename: Original uploaded FITS filename.
        stored_fits_filename: Filename as stored in rpp_data/fits/.
        zip_archive_filename: Just the ZIP filename.
        zip_stored_relpath: Relative path where ZIP is stored.
        has_wcs: True if FITS has WCS solution.
        
    Returns:
        Tuple of (wcs_fits_id, zip_archive_id), either may be None on error.
    """
    # Record the FITS file
    fits_id = record_wcs_fits_file(
        username=username,
        original_filename=original_filename,
        stored_filename=stored_fits_filename,
        has_wcs=has_wcs
    )
    
    # Record the ZIP archive
    zip_id = record_zip_archive(
        username=username,
        archive_filename=zip_archive_filename,
        stored_relpath=zip_stored_relpath
    )
    
    # Link them if both were recorded
    if fits_id and zip_id:
        link_fits_to_zip(fits_id, zip_id)
    
    return fits_id, zip_id


def get_fits_files_for_user(username: str) -> list:
    """Get all WCS FITS files for a user.
    
    Args:
        username: The username to look up.
        
    Returns:
        List of dicts with FITS file info.
    """
    conn = None
    try:
        user_id = get_user_id_by_username(username)
        if user_id is None:
            return []
        
        conn = get_db_connection()
        cursor = conn.execute(
            """
            SELECT id, original_filename, stored_filename, has_wcs, created_at
            FROM wcs_fits_files
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
        
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()


def get_zip_archives_for_user(username: str) -> list:
    """Get all ZIP archives for a user.
    
    Args:
        username: The username to look up.
        
    Returns:
        List of dicts with ZIP archive info.
    """
    conn = None
    try:
        user_id = get_user_id_by_username(username)
        if user_id is None:
            return []
        
        conn = get_db_connection()
        cursor = conn.execute(
            """
            SELECT id, archive_filename, stored_relpath, created_at
            FROM zip_archives
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
        
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()


def get_zips_for_fits(wcs_fits_id: int) -> list:
    """Get all ZIP archives associated with a FITS file.
    
    Args:
        wcs_fits_id: The WCS FITS file ID.
        
    Returns:
        List of dicts with ZIP archive info.
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.execute(
            """
            SELECT z.id, z.archive_filename, z.stored_relpath, z.created_at
            FROM zip_archives z
            JOIN wcs_fits_zip_assoc a ON z.id = a.zip_archive_id
            WHERE a.wcs_fits_id = ?
            ORDER BY z.created_at DESC
            """,
            (wcs_fits_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
        
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()


def get_fits_for_zip(zip_archive_id: int) -> list:
    """Get all FITS files associated with a ZIP archive.
    
    Args:
        zip_archive_id: The ZIP archive ID.
        
    Returns:
        List of dicts with FITS file info.
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.execute(
            """
            SELECT f.id, f.original_filename, f.stored_filename, f.has_wcs, f.created_at
            FROM wcs_fits_files f
            JOIN wcs_fits_zip_assoc a ON f.id = a.wcs_fits_id
            WHERE a.zip_archive_id = ?
            ORDER BY f.created_at DESC
            """,
            (zip_archive_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
        
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()


