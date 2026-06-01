"""Helpers for managing FITS file storage on disk."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple
from uuid import uuid4

from .config import FITS_STORAGE_ROOT

_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str) -> str:
    """Return a filesystem-safe filename while preserving the extension."""
    stem, dot, suffix = name.partition(".")
    safe_stem = _SAFE_NAME_PATTERN.sub("_", stem) or "fits"
    safe_suffix = _SAFE_NAME_PATTERN.sub("", suffix)
    return f"{safe_stem}{dot}{safe_suffix}" if dot else safe_stem


def build_storage_path(user_id: int, original_name: str) -> Tuple[Path, str]:
    """Compute the target path for a user's FITS upload.

    Returns the full filesystem path and the relative path stored in the DB.
    """
    now = datetime.now(timezone.utc)
    user_folder = FITS_STORAGE_ROOT / f"user_{user_id}" / f"{now:%Y}" / f"{now:%m}"
    user_folder.mkdir(parents=True, exist_ok=True)

    safe_original = sanitize_filename(original_name)
    token = uuid4().hex
    filename = f"{now:%Y%m%dT%H%M%S}_{token}_{safe_original}"

    full_path = user_folder / filename
    rel_path = full_path.relative_to(FITS_STORAGE_ROOT).as_posix()
    return full_path, rel_path


def resolve_storage_path(rel_path: str) -> Path:
    """Resolve a stored relative path to an absolute file path."""
    return FITS_STORAGE_ROOT / Path(rel_path)
