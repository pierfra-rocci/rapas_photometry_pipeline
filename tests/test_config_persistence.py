"""Persistence tests for the analysis parameters added to the sidebar.

The *Max Magnitude Error* (``mag_err_max``) and *Detector Gain*
(``effective_gain``) controls are stored inside
``analysis_parameters``. These tests check the three places that make the
save/login-reload promise hold:

1. the FastAPI ``/api/config`` round-trip (what the *Save Settings* button and
   the login-time ``get_config`` actually hit),
2. ``initialize_session_state`` seeding both keys with their sidebar defaults,
3. the Streamlit scripts that wire the parameters in and out of the blob
   (checked structurally, since both scripts have top-level side effects).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.security import generate_password_hash

from api import main as api_main
from api.database import Base, get_db
from api.main import app
from api.models import User
from src import tools_app


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_PATH = ROOT_DIR / "pages" / "app.py"
LOGIN_PATH = ROOT_DIR / "pages" / "login.py"


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """Create a FastAPI test client backed by an isolated SQLite database."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_config.db'}",
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

    with TestClient(app) as client:
        yield client, testing_session

    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _create_user(session_factory: sessionmaker[Session], username: str, password: str) -> None:
    """Insert a user into the isolated database."""
    with session_factory() as session:
        session.add(
            User(
                username=username,
                password=generate_password_hash(password),
                email=f"{username}@example.com",
            )
        )
        session.commit()


def test_config_round_trip_preserves_new_parameters(api_client):
    """Saving then reloading keeps mag_err_max and effective_gain verbatim."""
    client, session_factory = api_client
    _create_user(session_factory, "alice", "secret")

    analysis_parameters = {
        "seeing": 3.0,
        "mag_err_max": 1.5,
        "effective_gain": 1.35,
    }
    config = {
        "analysis_parameters": analysis_parameters,
        "observatory_data": {"name": "Obs"},
        "colibri_api_key": None,
    }

    saved = client.post(
        "/api/config",
        json={"config": config},
        auth=("alice", "secret"),
    )
    assert saved.status_code == 200

    reloaded = client.get("/api/config", auth=("alice", "secret"))
    assert reloaded.status_code == 200
    stored = reloaded.json()["config"]["analysis_parameters"]

    assert stored["mag_err_max"] == 1.5
    assert stored["effective_gain"] == 1.35


def test_config_round_trip_preserves_zero_mag_err_max(api_client):
    """A zero mag_err_max (the documented 'disable the cut' value) is kept.

    ``0`` must not be coerced to the 2.0 default nor dropped while serializing.
    """
    client, session_factory = api_client
    _create_user(session_factory, "bob", "secret")

    config = {
        "analysis_parameters": {"mag_err_max": 0.0, "effective_gain": 0.0},
    }
    client.post("/api/config", json={"config": config}, auth=("bob", "secret"))

    stored = client.get("/api/config", auth=("bob", "secret")).json()["config"][
        "analysis_parameters"
    ]
    assert stored["mag_err_max"] == 0.0
    assert stored["effective_gain"] == 0.0


class _FakeSessionState:
    """Minimal stand-in for ``st.session_state`` (item + attribute access)."""

    def __init__(self, initial: dict | None = None) -> None:
        super().__setattr__("_data", dict(initial or {}))

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __getitem__(self, key: str):
        return self._data[key]

    def __setitem__(self, key: str, value) -> None:
        self._data[key] = value

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def __getattr__(self, name: str):
        try:
            return self._data[name]
        except KeyError as exc:  # pragma: no cover - mirrors dict attribute access
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value) -> None:
        self._data[name] = value


def test_initialize_session_state_seeds_new_parameter_defaults(monkeypatch, tmp_path):
    """A fresh session gets both parameters in analysis_parameters."""
    fake_state = _FakeSessionState()
    monkeypatch.setattr(
        tools_app,
        "st",
        types.SimpleNamespace(session_state=fake_state),
    )
    monkeypatch.setattr(
        tools_app,
        "ensure_output_directory",
        lambda directory=None: str(tmp_path / (directory or "out")),
    )

    tools_app.initialize_session_state()

    seeded = fake_state["analysis_parameters"]
    assert seeded["mag_err_max"] == 2.0
    assert seeded["effective_gain"] == 0.0


def _assigned_subscripts(tree: ast.Module) -> set[tuple[str, ...]]:
    """Return ``(attribute, index)`` pairs used as assignment targets."""
    targets: set[tuple[str, ...]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Subscript):
                continue
            slice_node = target.slice
            if not (isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str)):
                continue
            value = target.value
            if isinstance(value, ast.Attribute):
                targets.add((value.attr, slice_node.value))
            elif isinstance(value, ast.Name):
                targets.add((value.id, slice_node.value))
    return targets


def test_app_writes_new_parameters_into_saved_analysis_parameters():
    """``pages/app.py`` must store both controls in ``analysis_parameters``.

    The Save Settings button persists the whole ``analysis_parameters`` dict, so
    the sidebar has to write each value into it for the save to include them.
    """
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    targets = _assigned_subscripts(tree)

    assert ("analysis_parameters", "mag_err_max") in targets
    assert ("analysis_parameters", "effective_gain") in targets


def test_login_restores_analysis_parameters_from_stored_config():
    """``pages/login.py`` must copy the stored blob back into session state."""
    tree = ast.parse(LOGIN_PATH.read_text(encoding="utf-8"))

    restored = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Subscript)
            and isinstance(value.slice, ast.Constant)
            and value.slice.value == "analysis_parameters"
        ):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "analysis_parameters"
            ):
                restored = True

    assert restored, "login.py no longer restores analysis_parameters from the config"


# ---------------------------------------------------------------------------
# Legacy Flask backend: /save_config and /get_config
# ---------------------------------------------------------------------------
# The legacy backend is optional: Flask is not a dependency in pyproject.toml and
# flask-cors is only used to attach the CORS middleware, which is irrelevant to
# configuration persistence. Skip cleanly when Flask itself is unavailable and
# stub flask-cors when only it is missing, so the endpoint logic is still
# exercised wherever the legacy backend can load.


@pytest.fixture
def legacy_backend(tmp_path, monkeypatch):
    """Import ``backend`` with an isolated SQLite database."""
    import os

    pytest.importorskip("flask")

    if importlib.util.find_spec("flask_cors") is None:
        stub = types.ModuleType("flask_cors")
        stub.CORS = lambda *args, **kwargs: None  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "flask_cors", stub)

    # backend.py resolves ``DB_PATH`` from its own location and then calls
    # ``init_db()`` at import time. Redirect just that one path join so the
    # import cannot create or lock the developer's repo-root users.db.
    db_path = tmp_path / "legacy_users.db"
    real_join = os.path.join

    def _redirected_join(*parts):
        if parts and parts[-1] in ("users.db", "users_dev.db"):
            return str(db_path)
        return real_join(*parts)

    monkeypatch.setattr(os.path, "join", _redirected_join)
    import backend  # noqa: PLC0415 - imported lazily to keep FastAPI tests independent

    monkeypatch.setattr(os.path, "join", real_join)
    monkeypatch.setattr(backend, "DB_PATH", str(db_path))
    backend.init_db()
    yield backend


def _create_legacy_user(db_path: str, username: str, password: str) -> None:
    """Insert an authenticated user directly (avoids the SMTP welcome email)."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO users (username, password, email) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), f"{username}@example.com"),
        )
        connection.commit()
    finally:
        connection.close()


def test_legacy_config_round_trip_preserves_new_parameters(legacy_backend):
    """The Flask /save_config then /get_config keeps both parameters verbatim."""
    db_path = legacy_backend.DB_PATH
    _create_legacy_user(db_path, "alice", "secret")

    # Mirrors the payload pages/app.py posts in legacy mode.
    params = {
        "analysis_parameters": {"mag_err_max": 1.5, "effective_gain": 1.35},
        "observatory_data": {"name": "Obs"},
        "colibri_api_key": None,
    }

    with legacy_backend.app.test_client() as client:
        saved = client.post(
            "/save_config",
            json={"username": "alice", "config_json": json.dumps(params)},
            auth=("alice", "secret"),
        )
        assert saved.status_code == 200

        reloaded = client.get("/get_config", auth=("alice", "secret"))
        assert reloaded.status_code == 200

    stored = json.loads(reloaded.get_data(as_text=True))
    assert stored["analysis_parameters"]["mag_err_max"] == 1.5
    assert stored["analysis_parameters"]["effective_gain"] == 1.35


def test_legacy_config_round_trip_preserves_zero_values(legacy_backend):
    """``0`` for both parameters must survive the legacy JSON round-trip."""
    db_path = legacy_backend.DB_PATH
    _create_legacy_user(db_path, "bob", "secret")

    params = {"analysis_parameters": {"mag_err_max": 0.0, "effective_gain": 0.0}}

    with legacy_backend.app.test_client() as client:
        client.post(
            "/save_config",
            json={"username": "bob", "config_json": json.dumps(params)},
            auth=("bob", "secret"),
        )
        reloaded = client.get("/get_config", auth=("bob", "secret"))
        assert reloaded.status_code == 200

    stored = json.loads(reloaded.get_data(as_text=True))
    assert stored["analysis_parameters"]["mag_err_max"] == 0.0
    assert stored["analysis_parameters"]["effective_gain"] == 0.0
