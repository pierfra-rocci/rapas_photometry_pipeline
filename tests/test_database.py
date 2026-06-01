"""
Unit tests for database models and tracking functionality.

This test suite validates:
- WcsFitsFile, ZipArchive, WcsFitsZipAssoc ORM models
- db_tracking module functions
- Migration script functionality
- Database relationships and constraints

Author: Automated Testing Suite
Date: 2026-01-09
"""

import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Import models
from api.models import (
    Base,
    User,
    WcsFitsFile,
    ZipArchive,
    WcsFitsZipAssoc,
)
from api.schemas import (
    WcsFitsFileSummary,
    ZipArchiveSummary,
    WcsFitsFileWithZips,
    ZipArchiveWithFits,
    WcsFitsFileListResponse,
    ZipArchiveListResponse,
)

# Import database setup
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class TestDatabaseModels:
    """Test ORM models for WCS FITS and ZIP tracking."""

    @pytest.fixture
    def engine(self):
        """Create an in-memory SQLite database for testing."""
        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(engine)
        return engine

    @pytest.fixture
    def session(self, engine):
        """Create a database session for testing."""
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    @pytest.fixture
    def test_user(self, session):
        """Create a test user."""
        user = User(
            username="testuser",
            password="hashed_password",
            email="test@example.com",
        )
        session.add(user)
        session.commit()
        return user

    def test_create_wcs_fits_file(self, session, test_user):
        """Test creating a WcsFitsFile record."""
        fits_file = WcsFitsFile(
            user_id=test_user.id,
            original_filename="science_image.fits",
            stored_filename="testuser_science_image_wcs.fits",
            has_wcs=True,
        )
        session.add(fits_file)
        session.commit()

        assert fits_file.id is not None
        assert fits_file.user_id == test_user.id
        assert fits_file.original_filename == "science_image.fits"
        assert fits_file.stored_filename == "testuser_science_image_wcs.fits"
        assert fits_file.has_wcs is True
        assert fits_file.created_at is not None

    def test_create_zip_archive(self, session, test_user):
        """Test creating a ZipArchive record."""
        archive = ZipArchive(
            user_id=test_user.id,
            archive_filename="science_image_20260109_143022.zip",
            stored_relpath="rpp_results/testuser_results/science_image_20260109_143022.zip",
        )
        session.add(archive)
        session.commit()

        assert archive.id is not None
        assert archive.user_id == test_user.id
        assert archive.archive_filename == "science_image_20260109_143022.zip"
        assert archive.created_at is not None

    def test_create_wcs_fits_zip_association(self, session, test_user):
        """Test creating an association between FITS and ZIP."""
        # Create FITS file
        fits_file = WcsFitsFile(
            user_id=test_user.id,
            original_filename="science.fits",
            stored_filename="testuser_science_wcs.fits",
            has_wcs=True,
        )
        session.add(fits_file)
        session.commit()

        # Create ZIP archive
        archive = ZipArchive(
            user_id=test_user.id,
            archive_filename="science_20260109.zip",
            stored_relpath="rpp_results/science_20260109.zip",
        )
        session.add(archive)
        session.commit()

        # Create association
        assoc = WcsFitsZipAssoc(
            wcs_fits_id=fits_file.id,
            zip_archive_id=archive.id,
        )
        session.add(assoc)
        session.commit()

        assert assoc.id is not None
        assert assoc.wcs_fits_id == fits_file.id
        assert assoc.zip_archive_id == archive.id

    def test_fits_to_zip_relationship(self, session, test_user):
        """Test that one FITS can be associated with multiple ZIPs."""
        # Create one FITS file
        fits_file = WcsFitsFile(
            user_id=test_user.id,
            original_filename="science.fits",
            stored_filename="testuser_science_wcs.fits",
            has_wcs=True,
        )
        session.add(fits_file)
        session.commit()

        # Create multiple ZIP archives
        archives = []
        for i in range(3):
            archive = ZipArchive(
                user_id=test_user.id,
                archive_filename=f"science_run{i}.zip",
                stored_relpath=f"rpp_results/science_run{i}.zip",
            )
            session.add(archive)
            archives.append(archive)
        session.commit()

        # Associate FITS with all ZIPs
        for archive in archives:
            assoc = WcsFitsZipAssoc(
                wcs_fits_id=fits_file.id,
                zip_archive_id=archive.id,
            )
            session.add(assoc)
        session.commit()

        # Verify associations
        session.refresh(fits_file)
        assert len(fits_file.zip_associations) == 3

    def test_user_wcs_fits_relationship(self, session, test_user):
        """Test User -> WcsFitsFile relationship."""
        # Create multiple FITS files for user
        for i in range(3):
            fits_file = WcsFitsFile(
                user_id=test_user.id,
                original_filename=f"image_{i}.fits",
                stored_filename=f"testuser_image_{i}_wcs.fits",
                has_wcs=True,
            )
            session.add(fits_file)
        session.commit()

        session.refresh(test_user)
        assert len(test_user.wcs_fits_files) == 3

    def test_user_zip_archives_relationship(self, session, test_user):
        """Test User -> ZipArchive relationship."""
        # Create multiple archives for user
        for i in range(3):
            archive = ZipArchive(
                user_id=test_user.id,
                archive_filename=f"result_{i}.zip",
                stored_relpath=f"rpp_results/result_{i}.zip",
            )
            session.add(archive)
        session.commit()

        session.refresh(test_user)
        assert len(test_user.zip_archives) == 3

    def test_unique_constraint_wcs_fits(self, session, test_user):
        """Test unique constraint on user_id + stored_filename."""
        fits_file1 = WcsFitsFile(
            user_id=test_user.id,
            original_filename="science.fits",
            stored_filename="testuser_science_wcs.fits",
            has_wcs=True,
        )
        session.add(fits_file1)
        session.commit()

        # Try to create duplicate
        fits_file2 = WcsFitsFile(
            user_id=test_user.id,
            original_filename="different.fits",
            stored_filename="testuser_science_wcs.fits",  # Same stored_filename
            has_wcs=True,
        )
        session.add(fits_file2)
        
        with pytest.raises(Exception):  # IntegrityError
            session.commit()

    def test_unique_constraint_zip_archive(self, session, test_user):
        """Test unique constraint on user_id + stored_relpath."""
        archive1 = ZipArchive(
            user_id=test_user.id,
            archive_filename="result.zip",
            stored_relpath="rpp_results/result.zip",
        )
        session.add(archive1)
        session.commit()

        # Try to create duplicate
        archive2 = ZipArchive(
            user_id=test_user.id,
            archive_filename="different.zip",
            stored_relpath="rpp_results/result.zip",  # Same stored_relpath
        )
        session.add(archive2)
        
        with pytest.raises(Exception):  # IntegrityError
            session.commit()


class TestPydanticSchemas:
    """Test Pydantic schemas for API responses."""

    def test_wcs_fits_file_summary_from_orm(self):
        """Test WcsFitsFileSummary can be created from dict."""
        data = {
            "id": 1,
            "original_filename": "science.fits",
            "stored_filename": "user_science_wcs.fits",
            "has_wcs": True,
            "created_at": datetime.now(),
        }
        summary = WcsFitsFileSummary(**data)
        
        assert summary.id == 1
        assert summary.original_filename == "science.fits"
        assert summary.has_wcs is True

    def test_zip_archive_summary_from_orm(self):
        """Test ZipArchiveSummary can be created from dict."""
        data = {
            "id": 1,
            "archive_filename": "result.zip",
            "stored_relpath": "rpp_results/result.zip",
            "created_at": datetime.now(),
        }
        summary = ZipArchiveSummary(**data)
        
        assert summary.id == 1
        assert summary.archive_filename == "result.zip"

    def test_wcs_fits_file_with_zips(self):
        """Test WcsFitsFileWithZips schema."""
        zip_data = {
            "id": 1,
            "archive_filename": "result.zip",
            "stored_relpath": "rpp_results/result.zip",
            "created_at": datetime.now(),
        }
        fits_data = {
            "id": 1,
            "original_filename": "science.fits",
            "stored_filename": "user_science_wcs.fits",
            "has_wcs": True,
            "created_at": datetime.now(),
            "zip_archives": [zip_data],
        }
        fits_with_zips = WcsFitsFileWithZips(**fits_data)
        
        assert len(fits_with_zips.zip_archives) == 1
        assert fits_with_zips.zip_archives[0].archive_filename == "result.zip"

    def test_wcs_fits_file_list_response(self):
        """Test WcsFitsFileListResponse schema."""
        response = WcsFitsFileListResponse(files=[])
        assert response.files == []

    def test_zip_archive_list_response(self):
        """Test ZipArchiveListResponse schema."""
        response = ZipArchiveListResponse(archives=[])
        assert response.archives == []


class TestDbTrackingModule:
    """Test the db_tracking helper module."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database with schema for testing."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        
        # Create tables
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username VARCHAR(80) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                email VARCHAR(120) UNIQUE NOT NULL
            )
        """)
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
        
        # Insert test user
        conn.execute(
            "INSERT INTO users (username, password, email) VALUES (?, ?, ?)",
            ("testuser", "hashed", "test@example.com")
        )
        conn.commit()
        conn.close()
        
        yield db_path
        
        # Cleanup
        os.unlink(db_path)

    def test_get_user_id_by_username(self, temp_db):
        """Test looking up user ID by username."""
        from src.db_tracking import get_user_id_by_username
        
        with patch("src.db_tracking.DB_PATH", temp_db):
            user_id = get_user_id_by_username("testuser")
            assert user_id == 1
            
            # Non-existent user
            user_id = get_user_id_by_username("nonexistent")
            assert user_id is None

    def test_record_wcs_fits_file(self, temp_db):
        """Test recording a WCS FITS file."""
        from src.db_tracking import record_wcs_fits_file
        
        with patch("src.db_tracking.DB_PATH", temp_db):
            fits_id = record_wcs_fits_file(
                username="testuser",
                original_filename="science.fits",
                stored_filename="testuser_science_wcs.fits",
                has_wcs=True
            )
            assert fits_id is not None
            assert fits_id > 0
            
            # Recording same file should return existing ID
            fits_id2 = record_wcs_fits_file(
                username="testuser",
                original_filename="science.fits",
                stored_filename="testuser_science_wcs.fits",
                has_wcs=True
            )
            assert fits_id2 == fits_id

    def test_record_zip_archive(self, temp_db):
        """Test recording a ZIP archive."""
        from src.db_tracking import record_zip_archive
        
        with patch("src.db_tracking.DB_PATH", temp_db):
            zip_id = record_zip_archive(
                username="testuser",
                archive_filename="result_20260109.zip",
                stored_relpath="rpp_results/result_20260109.zip"
            )
            assert zip_id is not None
            assert zip_id > 0

    def test_link_fits_to_zip(self, temp_db):
        """Test linking FITS file to ZIP archive."""
        from src.db_tracking import (
            record_wcs_fits_file,
            record_zip_archive,
            link_fits_to_zip,
        )
        
        with patch("src.db_tracking.DB_PATH", temp_db):
            fits_id = record_wcs_fits_file(
                username="testuser",
                original_filename="science.fits",
                stored_filename="testuser_science_wcs.fits",
                has_wcs=True
            )
            zip_id = record_zip_archive(
                username="testuser",
                archive_filename="result.zip",
                stored_relpath="rpp_results/result.zip"
            )
            
            result = link_fits_to_zip(fits_id, zip_id)
            assert result is True
            
            # Linking again should succeed (idempotent)
            result = link_fits_to_zip(fits_id, zip_id)
            assert result is True

    def test_record_analysis_result(self, temp_db):
        """Test the convenience function for recording analysis."""
        from src.db_tracking import record_analysis_result
        
        with patch("src.db_tracking.DB_PATH", temp_db):
            fits_id, zip_id = record_analysis_result(
                username="testuser",
                original_filename="science.fits",
                stored_fits_filename="testuser_science_wcs.fits",
                zip_archive_filename="result_20260109.zip",
                zip_stored_relpath="rpp_results/result_20260109.zip",
                has_wcs=True
            )
            
            assert fits_id is not None
            assert zip_id is not None

    def test_record_analysis_result_reuses_fits_and_links_multiple_archives(self, temp_db):
        """One stored FITS file should be linkable to multiple result archives."""
        from src.db_tracking import record_analysis_result, get_fits_for_zip, get_zips_for_fits

        with patch("src.db_tracking.DB_PATH", temp_db):
            first_fits_id, first_zip_id = record_analysis_result(
                username="testuser",
                original_filename="science.fits",
                stored_fits_filename="user_1/2026/06/science_wcs.fits",
                zip_archive_filename="science_run_a.zip",
                zip_stored_relpath="rpp_results/user_1/2026/06/science_run_a.zip",
                has_wcs=True,
            )
            second_fits_id, second_zip_id = record_analysis_result(
                username="testuser",
                original_filename="science.fits",
                stored_fits_filename="user_1/2026/06/science_wcs.fits",
                zip_archive_filename="science_run_b.zip",
                zip_stored_relpath="rpp_results/user_1/2026/06/science_run_b.zip",
                has_wcs=True,
            )

            assert first_fits_id is not None
            assert first_fits_id == second_fits_id
            assert first_zip_id is not None
            assert second_zip_id is not None
            assert first_zip_id != second_zip_id

            linked_zips = get_zips_for_fits(first_fits_id)
            assert len(linked_zips) == 2
            assert {zip_row["stored_relpath"] for zip_row in linked_zips} == {
                "rpp_results/user_1/2026/06/science_run_a.zip",
                "rpp_results/user_1/2026/06/science_run_b.zip",
            }

            linked_fits_a = get_fits_for_zip(first_zip_id)
            linked_fits_b = get_fits_for_zip(second_zip_id)
            assert len(linked_fits_a) == 1
            assert len(linked_fits_b) == 1
            assert linked_fits_a[0]["id"] == first_fits_id
            assert linked_fits_b[0]["id"] == first_fits_id

    def test_get_fits_files_for_user(self, temp_db):
        """Test getting FITS files for a user."""
        from src.db_tracking import record_wcs_fits_file, get_fits_files_for_user
        
        with patch("src.db_tracking.DB_PATH", temp_db):
            # Record some files
            record_wcs_fits_file(
                username="testuser",
                original_filename="image1.fits",
                stored_filename="testuser_image1_wcs.fits",
                has_wcs=True
            )
            record_wcs_fits_file(
                username="testuser",
                original_filename="image2.fits",
                stored_filename="testuser_image2_wcs.fits",
                has_wcs=True
            )
            
            files = get_fits_files_for_user("testuser")
            assert len(files) == 2

    def test_get_zip_archives_for_user(self, temp_db):
        """Test getting ZIP archives for a user."""
        from src.db_tracking import record_zip_archive, get_zip_archives_for_user
        
        with patch("src.db_tracking.DB_PATH", temp_db):
            # Record some archives
            record_zip_archive(
                username="testuser",
                archive_filename="result1.zip",
                stored_relpath="rpp_results/result1.zip"
            )
            record_zip_archive(
                username="testuser",
                archive_filename="result2.zip",
                stored_relpath="rpp_results/result2.zip"
            )
            
            archives = get_zip_archives_for_user("testuser")
            assert len(archives) == 2

    def test_get_zips_for_fits(self, temp_db):
        """Test getting ZIPs associated with a FITS file."""
        from src.db_tracking import (
            record_wcs_fits_file,
            record_zip_archive,
            link_fits_to_zip,
            get_zips_for_fits,
        )
        
        with patch("src.db_tracking.DB_PATH", temp_db):
            fits_id = record_wcs_fits_file(
                username="testuser",
                original_filename="science.fits",
                stored_filename="testuser_science_wcs.fits",
                has_wcs=True
            )
            
            # Create multiple ZIPs and link them
            for i in range(3):
                zip_id = record_zip_archive(
                    username="testuser",
                    archive_filename=f"result_{i}.zip",
                    stored_relpath=f"rpp_results/result_{i}.zip"
                )
                link_fits_to_zip(fits_id, zip_id)
            
            zips = get_zips_for_fits(fits_id)
            assert len(zips) == 3

    def test_get_fits_for_zip(self, temp_db):
        """Test getting FITS files associated with a ZIP."""
        from src.db_tracking import (
            record_wcs_fits_file,
            record_zip_archive,
            link_fits_to_zip,
            get_fits_for_zip,
        )
        
        with patch("src.db_tracking.DB_PATH", temp_db):
            zip_id = record_zip_archive(
                username="testuser",
                archive_filename="combined_result.zip",
                stored_relpath="rpp_results/combined_result.zip"
            )
            
            # This is an edge case - normally one ZIP has one FITS
            # But the schema supports many-to-many
            fits_id = record_wcs_fits_file(
                username="testuser",
                original_filename="science.fits",
                stored_filename="testuser_science_wcs.fits",
                has_wcs=True
            )
            link_fits_to_zip(fits_id, zip_id)
            
            fits_files = get_fits_for_zip(zip_id)
            assert len(fits_files) == 1

    def test_nonexistent_user(self, temp_db):
        """Test handling of non-existent user."""
        from src.db_tracking import record_wcs_fits_file, record_zip_archive
        
        with patch("src.db_tracking.DB_PATH", temp_db):
            fits_id = record_wcs_fits_file(
                username="nonexistent",
                original_filename="science.fits",
                stored_filename="nonexistent_science_wcs.fits",
                has_wcs=True
            )
            assert fits_id is None
            
            zip_id = record_zip_archive(
                username="nonexistent",
                archive_filename="result.zip",
                stored_relpath="rpp_results/result.zip"
            )
            assert zip_id is None


class TestMigrationScript:
    """Test the migration script functionality."""

    @pytest.fixture
    def temp_db_with_users(self):
        """Create a temp database with users table (simulating existing DB)."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username VARCHAR(80) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                email VARCHAR(120) UNIQUE NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO users (username, password, email) VALUES (?, ?, ?)",
            ("admin", "hashed", "admin@example.com")
        )
        conn.commit()
        conn.close()
        
        yield db_path
        
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)
        # Clean up backup files
        for f in Path(db_path).parent.glob(f"{Path(db_path).stem}_backup_*"):
            f.unlink()

    def test_table_exists_function(self, temp_db_with_users):
        """Test table_exists helper function."""
        from scripts.migrate_add_wcs_zip_tables import table_exists
        
        conn = sqlite3.connect(temp_db_with_users)
        
        assert table_exists(conn, "users") is True
        assert table_exists(conn, "nonexistent") is False
        
        conn.close()

    def test_create_wcs_fits_files_table(self, temp_db_with_users):
        """Test creating wcs_fits_files table."""
        from scripts.migrate_add_wcs_zip_tables import (
            create_wcs_fits_files_table,
            table_exists,
        )
        
        conn = sqlite3.connect(temp_db_with_users)
        
        # Table shouldn't exist yet
        assert table_exists(conn, "wcs_fits_files") is False
        
        # Create it
        result = create_wcs_fits_files_table(conn)
        conn.commit()
        
        assert result is True
        assert table_exists(conn, "wcs_fits_files") is True
        
        # Second call should skip (idempotent)
        result = create_wcs_fits_files_table(conn)
        assert result is False
        
        conn.close()

    def test_create_zip_archives_table(self, temp_db_with_users):
        """Test creating zip_archives table."""
        from scripts.migrate_add_wcs_zip_tables import (
            create_zip_archives_table,
            table_exists,
        )
        
        conn = sqlite3.connect(temp_db_with_users)
        
        assert table_exists(conn, "zip_archives") is False
        
        result = create_zip_archives_table(conn)
        conn.commit()
        
        assert result is True
        assert table_exists(conn, "zip_archives") is True
        
        conn.close()

    def test_create_wcs_fits_zip_assoc_table(self, temp_db_with_users):
        """Test creating junction table."""
        from scripts.migrate_add_wcs_zip_tables import (
            create_wcs_fits_files_table,
            create_zip_archives_table,
            create_wcs_fits_zip_assoc_table,
            table_exists,
        )
        
        conn = sqlite3.connect(temp_db_with_users)
        
        # Need to create parent tables first
        create_wcs_fits_files_table(conn)
        create_zip_archives_table(conn)
        conn.commit()
        
        assert table_exists(conn, "wcs_fits_zip_assoc") is False
        
        result = create_wcs_fits_zip_assoc_table(conn)
        conn.commit()
        
        assert result is True
        assert table_exists(conn, "wcs_fits_zip_assoc") is True
        
        conn.close()

    def test_verify_migration(self, temp_db_with_users):
        """Test migration verification."""
        from scripts.migrate_add_wcs_zip_tables import (
            create_wcs_fits_files_table,
            create_zip_archives_table,
            create_wcs_fits_zip_assoc_table,
            verify_migration,
        )
        
        conn = sqlite3.connect(temp_db_with_users)
        
        # Before migration
        result = verify_migration(conn)
        assert result["wcs_fits_files"] is None
        assert result["zip_archives"] is None
        assert result["wcs_fits_zip_assoc"] is None
        
        # After migration
        create_wcs_fits_files_table(conn)
        create_zip_archives_table(conn)
        create_wcs_fits_zip_assoc_table(conn)
        conn.commit()
        
        result = verify_migration(conn)
        assert result["wcs_fits_files"] == 0
        assert result["zip_archives"] == 0
        assert result["wcs_fits_zip_assoc"] == 0
        
        conn.close()

    def test_backup_database(self, temp_db_with_users):
        """Test database backup creation."""
        from scripts.migrate_add_wcs_zip_tables import backup_database
        
        backup_path = backup_database(Path(temp_db_with_users))
        
        assert backup_path.exists()
        assert "_backup_" in backup_path.name
        
        # Verify backup has same content
        conn_orig = sqlite3.connect(temp_db_with_users)
        conn_backup = sqlite3.connect(backup_path)
        
        orig_tables = conn_orig.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        backup_tables = conn_backup.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        
        assert orig_tables == backup_tables
        
        conn_orig.close()
        conn_backup.close()
        
        # Cleanup backup
        backup_path.unlink()

    def test_full_migration(self, temp_db_with_users):
        """Test complete migration process."""
        from scripts.migrate_add_wcs_zip_tables import run_migration
        
        result = run_migration(Path(temp_db_with_users), skip_backup=True)
        
        assert result == 0  # Success
        
        # Verify all tables exist
        conn = sqlite3.connect(temp_db_with_users)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        
        assert "wcs_fits_files" in tables
        assert "zip_archives" in tables
        assert "wcs_fits_zip_assoc" in tables
        assert "users" in tables  # Original table preserved
        
        conn.close()

    def test_migration_preserves_existing_data(self, temp_db_with_users):
        """Test that migration doesn't affect existing data."""
        from scripts.migrate_add_wcs_zip_tables import run_migration
        
        # Add more test data before migration
        conn = sqlite3.connect(temp_db_with_users)
        conn.execute(
            "INSERT INTO users (username, password, email) VALUES (?, ?, ?)",
            ("user2", "hashed2", "user2@example.com")
        )
        conn.commit()
        conn.close()
        
        # Run migration
        result = run_migration(Path(temp_db_with_users), skip_backup=True)
        assert result == 0
        
        # Verify existing data is preserved
        conn = sqlite3.connect(temp_db_with_users)
        cursor = conn.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        
        assert count == 2  # Both users still exist
        
        conn.close()

    def test_migration_idempotent(self, temp_db_with_users):
        """Test that migration can be run multiple times safely."""
        from scripts.migrate_add_wcs_zip_tables import run_migration
        
        # Run migration twice
        result1 = run_migration(Path(temp_db_with_users), skip_backup=True)
        result2 = run_migration(Path(temp_db_with_users), skip_backup=True)
        
        assert result1 == 0
        assert result2 == 0  # Should succeed even if tables exist


class TestIntegration:
    """Integration tests for the full workflow."""

    @pytest.fixture
    def temp_db_full(self):
        """Create a fully migrated test database."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        
        # Create all tables
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username VARCHAR(80) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                email VARCHAR(120) UNIQUE NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE wcs_fits_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                original_filename VARCHAR(255) NOT NULL,
                stored_filename VARCHAR(512) NOT NULL,
                has_wcs BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute("""
            CREATE TABLE zip_archives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                archive_filename VARCHAR(255) NOT NULL,
                stored_relpath VARCHAR(1024) NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute("""
            CREATE TABLE wcs_fits_zip_assoc (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wcs_fits_id INTEGER NOT NULL,
                zip_archive_id INTEGER NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY (wcs_fits_id) REFERENCES wcs_fits_files(id),
                FOREIGN KEY (zip_archive_id) REFERENCES zip_archives(id)
            )
        """)
        
        # Insert test users
        conn.execute(
            "INSERT INTO users (username, password, email) VALUES (?, ?, ?)",
            ("alice", "hashed", "alice@example.com")
        )
        conn.execute(
            "INSERT INTO users (username, password, email) VALUES (?, ?, ?)",
            ("bob", "hashed", "bob@example.com")
        )
        conn.commit()
        conn.close()
        
        yield db_path
        
        os.unlink(db_path)

    def test_full_analysis_workflow(self, temp_db_full):
        """Test recording a complete analysis workflow."""
        from src.db_tracking import (
            record_analysis_result,
            get_fits_files_for_user,
            get_zip_archives_for_user,
            get_zips_for_fits,
        )
        
        with patch("src.db_tracking.DB_PATH", temp_db_full):
            # Simulate Alice's analysis
            fits_id, zip_id = record_analysis_result(
                username="alice",
                original_filename="ngc7331_r.fits",
                stored_fits_filename="alice_ngc7331_r_wcs.fits",
                zip_archive_filename="ngc7331_r_20260109_120000.zip",
                zip_stored_relpath="rpp_results/alice_results/ngc7331_r_20260109_120000.zip",
                has_wcs=True
            )
            
            assert fits_id is not None
            assert zip_id is not None
            
            # Verify data was recorded
            fits_files = get_fits_files_for_user("alice")
            assert len(fits_files) == 1
            assert fits_files[0]["original_filename"] == "ngc7331_r.fits"
            
            archives = get_zip_archives_for_user("alice")
            assert len(archives) == 1
            
            # Verify association
            zips = get_zips_for_fits(fits_id)
            assert len(zips) == 1

    def test_multiple_analyses_same_fits(self, temp_db_full):
        """Test multiple analysis runs on the same FITS file."""
        from src.db_tracking import (
            record_analysis_result,
            get_zips_for_fits,
        )
        
        with patch("src.db_tracking.DB_PATH", temp_db_full):
            # First analysis
            fits_id1, zip_id1 = record_analysis_result(
                username="alice",
                original_filename="science.fits",
                stored_fits_filename="alice_science_wcs.fits",
                zip_archive_filename="science_run1.zip",
                zip_stored_relpath="rpp_results/science_run1.zip",
                has_wcs=True
            )
            
            # Second analysis with same FITS but different parameters
            fits_id2, zip_id2 = record_analysis_result(
                username="alice",
                original_filename="science.fits",
                stored_fits_filename="alice_science_wcs.fits",  # Same file
                zip_archive_filename="science_run2.zip",
                zip_stored_relpath="rpp_results/science_run2.zip",
                has_wcs=True
            )
            
            # Should return same FITS ID
            assert fits_id1 == fits_id2
            # But different ZIP IDs
            assert zip_id1 != zip_id2
            
            # FITS should have two associated ZIPs
            zips = get_zips_for_fits(fits_id1)
            assert len(zips) == 2

    def test_multiple_users_isolation(self, temp_db_full):
        """Test that different users' data is isolated."""
        from src.db_tracking import (
            record_analysis_result,
            get_fits_files_for_user,
            get_zip_archives_for_user,
        )
        
        with patch("src.db_tracking.DB_PATH", temp_db_full):
            # Alice's analysis
            record_analysis_result(
                username="alice",
                original_filename="image.fits",
                stored_fits_filename="alice_image_wcs.fits",
                zip_archive_filename="alice_result.zip",
                zip_stored_relpath="rpp_results/alice_result.zip",
                has_wcs=True
            )
            
            # Bob's analysis
            record_analysis_result(
                username="bob",
                original_filename="image.fits",  # Same filename!
                stored_fits_filename="bob_image_wcs.fits",
                zip_archive_filename="bob_result.zip",
                zip_stored_relpath="rpp_results/bob_result.zip",
                has_wcs=True
            )
            
            # Each user should only see their own files
            alice_fits = get_fits_files_for_user("alice")
            bob_fits = get_fits_files_for_user("bob")
            
            assert len(alice_fits) == 1
            assert len(bob_fits) == 1
            assert alice_fits[0]["stored_filename"] != bob_fits[0]["stored_filename"]
            
            alice_zips = get_zip_archives_for_user("alice")
            bob_zips = get_zip_archives_for_user("bob")
            
            assert len(alice_zips) == 1
            assert len(bob_zips) == 1
