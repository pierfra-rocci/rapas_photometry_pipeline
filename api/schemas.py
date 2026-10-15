"""Pydantic models for FastAPI requests and responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .models import FitsFileStatus


class Message(BaseModel):
    """Generic response message payload."""

    message: str


class LoginResponse(Message):
    """Login response carrying the canonical account username."""

    username: Optional[str] = None


class RegisterRequest(BaseModel):
    """Registration payload."""

    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=256)
    email: EmailStr


class LoginRequest(BaseModel):
    """Login payload."""

    username: str
    password: str


class RecoveryRequest(BaseModel):
    """Initiate password recovery for a user."""

    email: EmailStr


class RecoveryConfirmRequest(BaseModel):
    """Confirm password reset with a code."""

    email: EmailStr
    code: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=8, max_length=256)


class ConfigResponse(BaseModel):
    """User configuration blob."""

    config: Optional[Dict[str, Any]] = None


class ConfigUpdateRequest(BaseModel):
    """Persisted configuration wrapper."""

    config: Dict[str, Any]


class FitsFileSummary(BaseModel):
    """Metadata describing a stored FITS asset."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    stored_relpath: str
    sha256: str
    size_bytes: int
    status: FitsFileStatus
    upload_completed_at: Optional[datetime]


class FitsUploadResponse(BaseModel):
    """Response returned after a successful FITS upload."""

    id: int
    original_filename: str
    stored_relpath: str
    sha256: str
    size_bytes: int
    status: FitsFileStatus
    upload_completed_at: Optional[datetime]


class FitsFileListResponse(BaseModel):
    """List of FITS files for the current user."""

    files: List[FitsFileSummary] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# WCS FITS Files and ZIP Archives Tracking Schemas (v1.6.0)
# ---------------------------------------------------------------------------


class WcsFitsFileSummary(BaseModel):
    """Metadata describing a WCS-solved FITS file."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    stored_filename: str
    has_wcs: bool
    created_at: datetime


class ZipArchiveSummary(BaseModel):
    """Metadata describing a result ZIP archive."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    archive_filename: str
    stored_relpath: str
    created_at: datetime


class WcsFitsFileWithZips(WcsFitsFileSummary):
    """WCS FITS file with its associated ZIP archives."""

    zip_archives: List[ZipArchiveSummary] = Field(default_factory=list)


class ZipArchiveWithFits(ZipArchiveSummary):
    """ZIP archive with its associated FITS files."""

    fits_files: List[WcsFitsFileSummary] = Field(default_factory=list)


class WcsFitsFileListResponse(BaseModel):
    """List of WCS FITS files for the current user."""

    files: List[WcsFitsFileSummary] = Field(default_factory=list)


class ZipArchiveListResponse(BaseModel):
    """List of ZIP archives for the current user."""

    archives: List[ZipArchiveSummary] = Field(default_factory=list)
