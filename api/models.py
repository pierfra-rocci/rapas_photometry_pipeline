"""ORM models for the FastAPI backend."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class FitsFileStatus(str, Enum):
    """Lifecycle states for an uploaded FITS asset."""

    PENDING = "pending"
    STORED = "stored"
    FAILED = "failed"


class JobStatus(str, Enum):
    """Processing states for photometry analysis jobs."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class User(Base):
    """Represents an authenticated RAPAS user."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(
        String(80),
        unique=True,
        nullable=False,
    )
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(
        String(120),
        unique=True,
        nullable=False,
    )
    config_json: Mapped[Optional[str]] = mapped_column(Text)

    fits_files: Mapped[List["FitsFile"]] = relationship(
        "FitsFile",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    jobs: Mapped[List["AnalysisJob"]] = relationship(
        "AnalysisJob",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    wcs_fits_files: Mapped[List["WcsFitsFile"]] = relationship(
        "WcsFitsFile",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    zip_archives: Mapped[List["ZipArchive"]] = relationship(
        "ZipArchive",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"User(id={self.id!r}, username={self.username!r})"


class PasswordRecoveryCode(Base):
    """Hashed recovery codes for password reset flows."""

    __tablename__ = "recovery_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def is_expired(self, reference: Optional[datetime] = None) -> bool:
        """Return True if the recovery code is past its expiration."""
        ref_time = reference or datetime.utcnow()
        return self.expires_at <= ref_time


class FitsFile(Base):
    """Metadata for FITS files stored on disk."""

    __tablename__ = "fits_files"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "stored_relpath",
            name="uq_fits_user_relpath",
        ),
        # Deduplicate per user only: a global hash constraint would leak the
        # fact that another account already holds some content and would block
        # a user from uploading a file another user happens to own.
        UniqueConstraint(
            "user_id",
            "sha256",
            name="uq_fits_user_sha256",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_relpath: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[FitsFileStatus] = mapped_column(
        SAEnum(FitsFileStatus),
        nullable=False,
        default=FitsFileStatus.PENDING,
        server_default=FitsFileStatus.PENDING.value,
    )
    header_json: Mapped[Optional[str]] = mapped_column(Text)
    upload_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    upload_completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship("User", back_populates="fits_files")
    jobs: Mapped[List["AnalysisJob"]] = relationship(
        "AnalysisJob",
        back_populates="fits_file",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"FitsFile(id={self.id!r}, relpath={self.stored_relpath!r})"


class AnalysisJob(Base):
    """Represents a photometry analysis execution request."""

    __tablename__ = "analysis_jobs"
    __table_args__ = (
        UniqueConstraint(
            "fits_file_id",
            "created_at",
            name="uq_job_file_created",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    fits_file_id: Mapped[int] = mapped_column(
        ForeignKey("fits_files.id"),
        nullable=False,
    )
    parameters_json: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus),
        nullable=False,
        default=JobStatus.QUEUED,
        server_default=JobStatus.QUEUED.value,
    )
    result_relpath: Mapped[Optional[str]] = mapped_column(String(1024))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship("User", back_populates="jobs")
    fits_file: Mapped["FitsFile"] = relationship(
        "FitsFile",
        back_populates="jobs",
    )
    events: Mapped[List["JobEvent"]] = relationship(
        "JobEvent",
        back_populates="job",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"AnalysisJob(id={self.id!r}, status={self.status!r})"


class JobEvent(Base):
    """Timeline entries for analysis jobs."""

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_jobs.id"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    job: Mapped["AnalysisJob"] = relationship(
        "AnalysisJob",
        back_populates="events",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"JobEvent(id={self.id!r}, type={self.event_type!r})"


# ---------------------------------------------------------------------------
# WCS FITS Files and ZIP Archives Tracking (v1.6.0)
# ---------------------------------------------------------------------------


class WcsFitsFile(Base):
    """Tracks WCS-solved (or original) FITS files stored in rpp_data/fits/."""

    __tablename__ = "wcs_fits_files"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "stored_filename",
            name="uq_wcs_fits_user_filename",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    has_wcs: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship("User", back_populates="wcs_fits_files")
    zip_associations: Mapped[List["WcsFitsZipAssoc"]] = relationship(
        "WcsFitsZipAssoc",
        back_populates="wcs_fits_file",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"WcsFitsFile(id={self.id!r}, filename={self.stored_filename!r})"


class ZipArchive(Base):
    """Tracks result ZIP archives stored in rpp_results/."""

    __tablename__ = "zip_archives"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "stored_relpath",
            name="uq_zip_user_relpath",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    archive_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_relpath: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship("User", back_populates="zip_archives")
    fits_associations: Mapped[List["WcsFitsZipAssoc"]] = relationship(
        "WcsFitsZipAssoc",
        back_populates="zip_archive",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"ZipArchive(id={self.id!r}, relpath={self.stored_relpath!r})"


class WcsFitsZipAssoc(Base):
    """Junction table linking WCS FITS files to ZIP archives (many-to-many)."""

    __tablename__ = "wcs_fits_zip_assoc"
    __table_args__ = (
        UniqueConstraint(
            "wcs_fits_id",
            "zip_archive_id",
            name="uq_wcs_fits_zip",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wcs_fits_id: Mapped[int] = mapped_column(
        ForeignKey("wcs_fits_files.id"),
        nullable=False,
    )
    zip_archive_id: Mapped[int] = mapped_column(
        ForeignKey("zip_archives.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    wcs_fits_file: Mapped["WcsFitsFile"] = relationship(
        "WcsFitsFile",
        back_populates="zip_associations",
    )
    zip_archive: Mapped["ZipArchive"] = relationship(
        "ZipArchive",
        back_populates="fits_associations",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"WcsFitsZipAssoc(fits={self.wcs_fits_id!r}, zip={self.zip_archive_id!r})"
