"""Login and password-recovery tests for both backends.

A user who only remembers the address they registered with used to be locked
out: ``/login`` matched the username verbatim, while the recovery flow is keyed
on the email address. Re-registering was impossible too, because the address was
already taken. These tests pin down that

1. the login identifier may be the username *or* the registration email,
2. the email is matched case-insensitively (registration keeps the local-part
   casing, so ``Intern1@example.com`` must be reachable as
   ``intern1@example.com``), and
3. the authenticated-request paths still resolve the account, so configuration
   access keeps working when the user logged in with the email.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from api import main as api_main
from api.database import Base, get_db
from api.main import app


EMAIL = "Intern1@Example.COM"
USERNAME = "intern1"
PASSWORD = "OldPass123"
NEW_PASSWORD = "NewPass456"


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """Create a FastAPI test client backed by an isolated SQLite database.

    Recovery emails are captured instead of sent.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_auth.db'}",
        connect_args={"check_same_thread": False},
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    # The app lifespan runs create_all against the module-level engine; point
    # that at the isolated database so the developer's users.db is untouched.
    monkeypatch.setattr(api_main, "engine", engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        api_main,
        "_send_recovery_email",
        lambda to_email, code: sent.append((to_email, code)),
    )

    with TestClient(app) as client:
        yield client, testing_session, sent

    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def legacy_backend(tmp_path, monkeypatch):
    """Import ``backend`` with an isolated SQLite database and stubbed mail."""
    pytest.importorskip("flask")

    if importlib.util.find_spec("flask_cors") is None:
        stub = types.ModuleType("flask_cors")
        stub.CORS = lambda *args, **kwargs: None  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "flask_cors", stub)

    # backend.py resolves ``DB_PATH`` from its own location and then calls
    # ``init_db()`` at import time. Redirect just that one path join so the
    # import cannot create or lock the developer's repo-root users.db.
    db_path = tmp_path / "legacy_users.db"
    monkeypatch.setenv("RPP_DB_PATH", str(db_path))
    real_join = os.path.join

    def _redirected_join(*parts):
        if parts and parts[-1] in ("users.db", "users_dev.db"):
            return str(db_path)
        return real_join(*parts)

    monkeypatch.setattr(os.path, "join", _redirected_join)
    import backend  # noqa: PLC0415 - imported lazily to keep the tests independent

    monkeypatch.setattr(os.path, "join", real_join)
    monkeypatch.setattr(backend, "DB_PATH", str(db_path))
    backend.init_db()

    sent: list[tuple[str, str]] = []

    def fake_send(to_email, subject, body):
        sent.append((to_email, body))
        return True, "sent"

    monkeypatch.setattr(backend, "send_email", fake_send)
    yield backend, sent


def _recovery_code(mail: tuple[str, str]) -> str:
    """Extract the 6-digit code from a captured recovery message."""
    return mail[1].split(":")[-1].strip()


# ---------------------------------------------------------------------------
# FastAPI backend
# ---------------------------------------------------------------------------


def _register_api(client) -> None:
    response = client.post(
        "/api/register",
        json={"username": USERNAME, "password": PASSWORD, "email": EMAIL},
    )
    assert response.status_code == 201


def test_api_login_accepts_username_or_email(api_client):
    """Either identifier logs the account in; a bad password still fails."""
    client, _, _ = api_client
    _register_api(client)

    by_username = client.post(
        "/api/login", json={"username": USERNAME, "password": PASSWORD}
    )
    assert by_username.status_code == 200

    by_email = client.post("/api/login", json={"username": EMAIL, "password": PASSWORD})
    assert by_email.status_code == 200
    assert by_username.json()["user_id"] > 0
    assert by_username.json()["user_id"] == by_email.json()["user_id"]

    bad_password = client.post(
        "/api/login", json={"username": EMAIL, "password": "WrongPass1"}
    )
    assert bad_password.status_code == 401
    assert bad_password.json()["detail"] == "Invalid username or password."


def test_api_login_matches_email_case_insensitively(api_client):
    """The local-part casing stored at registration must not lock users out."""
    client, _, _ = api_client
    _register_api(client)

    response = client.post(
        "/api/login",
        json={"username": "intern1@example.com", "password": PASSWORD},
    )
    assert response.status_code == 200


def test_api_colliding_usernames_have_distinct_storage_ids(api_client: Any) -> None:
    client, _, _ = api_client
    ids = []
    for username, email in (
        ("alice", "alice@example.com"),
        ("attacker/alice", "other@example.com"),
    ):
        registered = client.post(
            "/api/register",
            json={"username": username, "password": PASSWORD, "email": email},
        )
        assert registered.status_code == 201
        logged_in = client.post(
            "/api/login", json={"username": username, "password": PASSWORD}
        )
        assert logged_in.status_code == 200
        ids.append(logged_in.json()["user_id"])
    assert len(set(ids)) == 2


def test_deleted_account_id_is_not_reused_for_registration(api_client: Any) -> None:
    from sqlalchemy import text

    client, testing_session, _ = api_client
    _register_api(client)
    original = client.post(
        "/api/login", json={"username": USERNAME, "password": PASSWORD}
    ).json()["user_id"]
    with testing_session() as session:
        session.execute(text("DELETE FROM users WHERE id = :id"), {"id": original})
        session.commit()

    assert (
        client.post(
            "/api/register",
            json={
                "username": "replacement",
                "password": PASSWORD,
                "email": "replacement@example.com",
            },
        ).status_code
        == 201
    )
    replacement = client.post(
        "/api/login", json={"username": "replacement", "password": PASSWORD}
    ).json()["user_id"]
    assert replacement != original


def test_existing_accounts_keep_ids_and_credentials_on_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_backend: Any
) -> None:
    from werkzeug.security import generate_password_hash

    from api.models import User

    engine = create_engine(
        f"sqlite:///{tmp_path / 'existing.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    password_hash = generate_password_hash(PASSWORD)
    with testing_session() as session:
        session.add_all(
            [
                User(
                    id=-5,
                    username="negative",
                    email="negative@example.com",
                    password=password_hash,
                ),
                User(
                    id=0,
                    username="zero",
                    email="zero@example.com",
                    password=password_hash,
                ),
                User(id=41, username=USERNAME, email=EMAIL, password=password_hash),
            ]
        )
        session.commit()

    monkeypatch.setattr(api_main, "engine", engine)

    def override_get_db() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            accounts = (("negative", -5), ("zero", 0), (USERNAME, 41))
            for username, expected_id in accounts:
                response = client.post(
                    "/api/login",
                    json={"username": username, "password": PASSWORD},
                )
                assert response.status_code == 200
                assert response.json()["user_id"] == expected_id
            backend, _ = legacy_backend
            monkeypatch.setattr(backend, "DB_PATH", str(tmp_path / "existing.db"))
            backend.init_db()
            with backend.app.test_client() as legacy:
                for username, expected_id in accounts:
                    response = legacy.post(
                        "/login", data={"username": username, "password": PASSWORD}
                    )
                    assert response.status_code == 200
                    assert int(response.headers["X-RPP-User-Id"]) == expected_id
            with testing_session() as session:
                existing_user = session.get(User, 41)
                assert existing_user is not None
                assert existing_user.username == USERNAME
                assert existing_user.email == EMAIL
                assert existing_user.password == password_hash
                session.delete(existing_user)
                session.commit()
            assert (
                client.post(
                    "/api/register",
                    json={
                        "username": "replacement",
                        "password": PASSWORD,
                        "email": "replacement@example.com",
                    },
                ).status_code
                == 201
            )
            response = client.post(
                "/api/login",
                json={"username": "replacement", "password": PASSWORD},
            )
            assert response.json()["user_id"] > 41
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_both_backends_share_id_reservations(
    api_client: Any, legacy_backend: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text

    client, testing_session, _ = api_client
    backend, _ = legacy_backend
    with testing_session() as session:
        db_path = session.get_bind().url.database
    monkeypatch.setattr(backend, "DB_PATH", db_path)
    backend.init_db()
    _register_api(client)
    original = client.post(
        "/api/login", json={"username": USERNAME, "password": PASSWORD}
    ).json()["user_id"]
    with backend.app.test_client() as legacy:
        logged_in = legacy.post(
            "/login", data={"username": USERNAME, "password": PASSWORD}
        )
        assert int(logged_in.headers["X-RPP-User-Id"]) == original
        with testing_session() as session:
            session.execute(text("DELETE FROM users WHERE id = :id"), {"id": original})
            session.commit()
        assert (
            legacy.post(
                "/register",
                data={
                    "username": "replacement",
                    "password": PASSWORD,
                    "email": "replacement@example.com",
                },
            ).status_code
            == 201
        )
        logged_in = legacy.post(
            "/login", data={"username": "replacement", "password": PASSWORD}
        )
        replacement = int(logged_in.headers["X-RPP-User-Id"])
        assert replacement != original
        assert (
            client.post(
                "/api/login", json={"username": "replacement", "password": PASSWORD}
            ).json()["user_id"]
            == replacement
        )
        with testing_session() as session:
            session.execute(
                text("DELETE FROM users WHERE id = :id"), {"id": replacement}
            )
            session.commit()
        assert (
            client.post(
                "/api/register",
                json={
                    "username": "third",
                    "password": PASSWORD,
                    "email": "third@example.com",
                },
            ).status_code
            == 201
        )
        third = legacy.post("/login", data={"username": "third", "password": PASSWORD})
        assert third.status_code == 200
        assert int(third.headers["X-RPP-User-Id"]) not in {original, replacement}


def test_api_recovery_is_case_insensitive_and_updates_password(api_client):
    """A differently-cased address still receives and can redeem the code."""
    client, _, sent = api_client
    _register_api(client)

    requested = client.post(
        "/api/recovery/request", json={"email": "intern1@example.com"}
    )
    assert requested.status_code == 200
    assert len(sent) == 1, "the recovery email was not sent for a case-variant address"

    confirmed = client.post(
        "/api/recovery/confirm",
        json={
            "email": "intern1@example.com",
            "code": _recovery_code(sent[-1]),
            "new_password": NEW_PASSWORD,
        },
    )
    assert confirmed.status_code == 200

    # The new password works with either identifier.
    for identifier in (USERNAME, "intern1@example.com"):
        login = client.post(
            "/api/login", json={"username": identifier, "password": NEW_PASSWORD}
        )
        assert login.status_code == 200

    # The old password no longer does.
    stale = client.post("/api/login", json={"username": USERNAME, "password": PASSWORD})
    assert stale.status_code == 401


def test_api_basic_auth_accepts_the_registration_email(api_client):
    """Config access keeps working after a login that used the email."""
    client, _, _ = api_client
    _register_api(client)

    saved = client.post(
        "/api/config",
        json={"config": {"analysis_parameters": {"seeing": 2.5}}},
        auth=("intern1@example.com", PASSWORD),
    )
    assert saved.status_code == 200

    reloaded = client.get("/api/config", auth=(EMAIL, PASSWORD))
    assert reloaded.status_code == 200
    assert reloaded.json()["config"]["analysis_parameters"]["seeing"] == 2.5


# ---------------------------------------------------------------------------
# Legacy Flask backend
# ---------------------------------------------------------------------------


def _register_legacy(client) -> None:
    response = client.post(
        "/register",
        data={"username": USERNAME, "password": PASSWORD, "email": EMAIL},
    )
    assert response.status_code == 201


def test_legacy_login_accepts_username_or_email(legacy_backend):
    """The legacy backend accepts either identifier, case-insensitively."""
    backend, _ = legacy_backend
    with backend.app.test_client() as client:
        _register_legacy(client)

        assert (
            client.post(
                "/login", data={"username": USERNAME, "password": PASSWORD}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/login", data={"username": "intern1@example.com", "password": PASSWORD}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/login", data={"username": EMAIL, "password": "WrongPass1"}
            ).status_code
            == 401
        )


def test_legacy_recovery_is_case_insensitive_and_updates_password(legacy_backend):
    """A case-variant address receives the code and can redeem it."""
    backend, sent = legacy_backend
    with backend.app.test_client() as client:
        _register_legacy(client)
        sent.clear()

        requested = client.post(
            "/recover_request", data={"email": "intern1@example.com"}
        )
        assert requested.status_code == 200
        assert len(sent) == 1, "no recovery email for a case-variant address"

        confirmed = client.post(
            "/recover_confirm",
            data={
                "email": "intern1@example.com",
                "code": _recovery_code(sent[-1]),
                "new_password": NEW_PASSWORD,
            },
        )
        assert confirmed.status_code == 200

        login = client.post(
            "/login", data={"username": EMAIL, "password": NEW_PASSWORD}
        )
        assert login.status_code == 200


def test_legacy_login_returns_distinct_ids_and_unicode_username(
    legacy_backend: Any,
) -> None:
    backend, _ = legacy_backend
    identities = []
    with backend.app.test_client() as client:
        for username, email in (
            ("alice", "alice@example.com"),
            ("attacker/alice", "other@example.com"),
            ("élise", "unicode@example.com"),
        ):
            registered = client.post(
                "/register",
                data={"username": username, "password": PASSWORD, "email": email},
            )
            assert registered.status_code == 201
            logged_in = client.post(
                "/login", data={"username": email, "password": PASSWORD}
            )
            assert logged_in.status_code == 200
            assert unquote(logged_in.headers["X-RPP-Username-Encoded"]) == username
            identities.append(int(logged_in.headers["X-RPP-User-Id"]))
    assert len(set(identities)) == 3


def test_legacy_backend_uses_database_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "override.db"
    monkeypatch.setenv("RPP_DB_PATH", str(db_path))
    spec = importlib.util.spec_from_file_location(
        "isolated_legacy_backend", Path(__file__).resolve().parents[1] / "backend.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DB_PATH == str(db_path)
    assert db_path.exists()


def test_legacy_config_auth_stores_against_the_canonical_username(legacy_backend):
    """Signing in with the email still persists config for the account."""
    backend, _ = legacy_backend
    params = {"analysis_parameters": {"seeing": 2.5}}

    with backend.app.test_client() as client:
        _register_legacy(client)

        saved = client.post(
            "/save_config",
            json={"config_json": json.dumps(params)},
            auth=("intern1@example.com", PASSWORD),
        )
        assert saved.status_code == 200

        stored = client.get("/get_config", auth=(EMAIL, PASSWORD))
        assert stored.status_code == 200
        assert json.loads(stored.get_data(as_text=True)) == params

        # The canonical username row is the one that changed.
        connection = sqlite3.connect(backend.DB_PATH)
        try:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT username, config_json FROM users"
            ).fetchall()
        finally:
            connection.close()

    assert len(rows) == 1
    assert rows[0]["username"] == USERNAME
    assert json.loads(rows[0]["config_json"]) == params


def test_legacy_recover_confirm_rejects_unknown_email(legacy_backend):
    """An unknown address reports an invalid code instead of a silent success."""
    backend, _ = legacy_backend
    with backend.app.test_client() as client:
        response = client.post(
            "/recover_confirm",
            data={
                "email": "nobody@example.com",
                "code": "123456",
                "new_password": NEW_PASSWORD,
            },
        )
        assert response.status_code == 400
        assert response.get_data(as_text=True) == "Invalid or expired recovery code."
