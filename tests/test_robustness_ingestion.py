"""Robustness tests for FastAPI FITS ingestion and listing endpoints."""

from __future__ import annotations

import gzip
import io
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.security import generate_password_hash

from api.database import Base, get_db
from api.main import app
from api.models import FitsFile, User
from api import storage as storage_module


def _make_fits_bytes() -> bytes:
    """Create a tiny valid FITS payload for upload tests."""
    data = np.arange(16, dtype=np.float32).reshape((4, 4))
    buffer = io.BytesIO()
    fits.PrimaryHDU(data=data).writeto(buffer)
    return buffer.getvalue()


def _make_gzip_fits_bytes() -> bytes:
    """Create a gzipped FITS payload for .gz filename tests."""
    return gzip.compress(_make_fits_bytes())


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """Create a FastAPI test client with isolated DB and FITS storage."""
    storage_root = tmp_path / "fits_storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage_module, "FITS_STORAGE_ROOT", storage_root)

    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_ingestion.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, TestingSessionLocal, storage_root

    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _create_user(session_factory: sessionmaker[Session], username: str, password: str) -> User:
    """Insert an authenticated test user into the isolated database."""
    with session_factory() as session:
        user = User(
            username=username,
            password=generate_password_hash(password),
            email=f"{username}@example.com",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.expunge(user)
        return user


def _upload_file(client: TestClient, username: str, password: str, filename: str, content: bytes):
    """Upload a file to the FastAPI FITS endpoint with HTTP Basic auth."""
    return client.post(
        "/api/upload/fits",
        files={"file": (filename, content, "application/fits")},
        auth=(username, password),
    )


def test_empty_upload_returns_400_and_does_not_persist(api_client):
    """An empty upload should be rejected before disk or DB persistence."""
    client, session_factory, storage_root = api_client
    _create_user(session_factory, "alice", "secret")

    response = _upload_file(client, "alice", "secret", "empty.fits", b"")

    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded file is empty."

    with session_factory() as session:
        assert session.query(FitsFile).count() == 0

    assert list(storage_root.rglob("*")) == []


@pytest.mark.parametrize(
    ("filename", "content_factory"),
    [
        ("science.fits", _make_fits_bytes),
        ("science.fit", _make_fits_bytes),
        ("science.fts", _make_fits_bytes),
        ("science.fits.gz", _make_gzip_fits_bytes),
        ("science.fts.gz", _make_gzip_fits_bytes),
    ],
)
def test_supported_extensions_are_stored_under_user_scoped_paths(
    api_client,
    filename,
    content_factory,
):
    """Supported filenames should be stored beneath the authenticated user's folder."""
    client, session_factory, storage_root = api_client
    user = _create_user(session_factory, "alice", "secret")

    response = _upload_file(client, "alice", "secret", filename, content_factory())

    assert response.status_code == 201
    payload = response.json()
    assert payload["original_filename"] == filename
    assert payload["status"] == "stored"
    assert payload["stored_relpath"].startswith(f"user_{user.id}/")

    stored_path = storage_root / payload["stored_relpath"]
    assert stored_path.exists()
    assert stored_path.is_file()
    assert stored_path.name.endswith(storage_module.sanitize_filename(filename))


def test_duplicate_hash_returns_409_and_cleans_up_second_file(api_client):
    """A duplicate-content upload should not leave an orphaned file on disk."""
    client, session_factory, storage_root = api_client
    _create_user(session_factory, "alice", "secret")
    fits_bytes = _make_fits_bytes()

    first = _upload_file(client, "alice", "secret", "first.fits", fits_bytes)
    second = _upload_file(client, "alice", "secret", "second.fits", fits_bytes)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == "A file with the same hash already exists."

    with session_factory() as session:
        records = session.query(FitsFile).all()
        assert len(records) == 1
        assert records[0].original_filename == "first.fits"

    stored_files = [path for path in storage_root.rglob("*") if path.is_file()]
    assert len(stored_files) == 1


def test_listing_is_isolated_by_authenticated_user(api_client):
    """Each user should only see the FITS files linked to their own account."""
    client, session_factory, _ = api_client
    user_1 = _create_user(session_factory, "alice", "secret")
    user_2 = _create_user(session_factory, "bob", "secret")

    upload_one = _upload_file(client, "alice", "secret", "alice_science.fits", _make_fits_bytes())
    upload_two = _upload_file(client, "bob", "secret", "bob_science.fits", _make_fits_bytes() + b"-unique")

    assert upload_one.status_code == 201
    assert upload_two.status_code == 201

    alice_listing = client.get("/api/fits", auth=("alice", "secret"))
    bob_listing = client.get("/api/fits", auth=("bob", "secret"))

    assert alice_listing.status_code == 200
    assert bob_listing.status_code == 200

    alice_files = alice_listing.json()["files"]
    bob_files = bob_listing.json()["files"]

    assert len(alice_files) == 1
    assert len(bob_files) == 1
    assert alice_files[0]["original_filename"] == "alice_science.fits"
    assert bob_files[0]["original_filename"] == "bob_science.fits"
    assert alice_files[0]["stored_relpath"].startswith(f"user_{user_1.id}/")
    assert bob_files[0]["stored_relpath"].startswith(f"user_{user_2.id}/")
    assert alice_files[0]["stored_relpath"] != bob_files[0]["stored_relpath"]