from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from streamlit.testing.v1 import AppTest

import api.config
import src.tools_app as tools_app
import src.utils as utils
from pages.api_client import can_view_pipeline_logs


ROOT = Path(__file__).resolve().parents[1]


def test_user_storage_is_distinct_and_legacy_files_are_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def output_directory(directory: str = "") -> str:
        path = tmp_path / directory
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(utils, "ensure_output_directory", output_directory)
    legacy = tmp_path / "alice_results"
    legacy.mkdir()
    (legacy / "old.zip").write_bytes(b"legacy")

    first = Path(utils.ensure_user_output_directory(1))
    second = Path(utils.ensure_user_output_directory(2))
    archive = Path(utils.ensure_user_archive_directory(1))

    assert first == tmp_path / "private_v2" / "user_1"
    assert second == tmp_path / "private_v2" / "user_2"
    assert archive == first / datetime.now().strftime("%Y") / datetime.now().strftime(
        "%m"
    )
    assert list(first.glob("*.zip")) == []
    assert (legacy / "old.zip").read_bytes() == b"legacy"


@pytest.mark.parametrize("user_id", [0, -1])
def test_existing_nonpositive_account_ids_have_distinct_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_id: int
) -> None:
    monkeypatch.setattr(
        utils, "ensure_output_directory", lambda directory: str(tmp_path / directory)
    )
    assert Path(utils.ensure_user_output_directory(user_id)) == (
        tmp_path / "private_v2" / f"user_{user_id}"
    )


@pytest.mark.parametrize("user_id", [None, True, "1"])
def test_user_storage_rejects_missing_or_invalid_identity(user_id: object) -> None:
    with pytest.raises(ValueError):
        utils.ensure_user_output_directory(user_id)


def test_permanent_wcs_fits_is_scoped_to_account_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fits_root = tmp_path / "fits"
    monkeypatch.setattr(api.config, "FITS_STORAGE_ROOT", fits_root)
    source = tmp_path / "image.fits"
    fits.PrimaryHDU(np.ones((4, 4))).writeto(source)
    header = fits.getheader(source)
    legacy = fits_root / "alice_image_wcs.fits"
    legacy.parent.mkdir()
    legacy.write_bytes(b"legacy")

    first, first_error, first_stored = utils.save_fits_with_wcs(
        str(source),
        header,
        str(tmp_path / "first"),
        original_filename="image.fits",
        username="alice",
        user_id=1,
    )
    second, second_error, second_stored = utils.save_fits_with_wcs(
        str(source),
        header,
        str(tmp_path / "second"),
        original_filename="image.fits",
        username="attacker/alice",
        user_id=2,
    )

    assert first and second and first_error is None and second_error is None
    assert first_stored and second_stored and first_stored != second_stored
    assert (fits_root / first_stored).is_file()
    assert (fits_root / second_stored).is_file()
    assert Path(first_stored).parts[:2] == ("private_v2", "user_1")
    assert Path(second_stored).parts[:2] == ("private_v2", "user_2")
    assert legacy.read_bytes() == b"legacy"


def test_wcs_fits_is_not_persisted_without_account_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fits_root = tmp_path / "fits"
    monkeypatch.setattr(api.config, "FITS_STORAGE_ROOT", fits_root)
    source = tmp_path / "image.fits"
    fits.PrimaryHDU(np.ones((4, 4))).writeto(source)
    output, error, stored = utils.save_fits_with_wcs(
        str(source),
        fits.getheader(source),
        str(tmp_path / "output"),
        username="alice",
    )
    assert output and error is None and stored is None
    assert not fits_root.exists()


def test_log_access_requires_explicit_account_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RPP_LOG_ADMIN_USER_IDS", raising=False)
    assert not can_view_pipeline_logs(1)
    monkeypatch.setenv("RPP_LOG_ADMIN_USER_IDS", "1, 42")
    assert can_view_pipeline_logs(1)
    assert can_view_pipeline_logs(42)
    assert not can_view_pipeline_logs(2)
    assert not can_view_pipeline_logs(True)
    monkeypatch.setenv("RPP_LOG_ADMIN_USER_IDS", "0,-1")
    assert can_view_pipeline_logs(0)
    assert can_view_pipeline_logs(-1)
    monkeypatch.setenv("RPP_LOG_ADMIN_USER_IDS", "1,invalid")
    assert not can_view_pipeline_logs(1)


def test_log_page_rejects_unprovisioned_admin_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RPP_LOG_ADMIN_USER_IDS", "1")
    (tmp_path / "fastapi_backend.log").write_text("private log")
    page = AppTest.from_file(str(ROOT / "pages" / "showlogs.py"))
    page.session_state["logged_in"] = True
    page.session_state["username"] = "admin"
    page.session_state["user_id"] = 2
    page.run()
    assert not page.exception
    assert any("Forbidden" in error.value for error in page.error)
    assert not page.text_area

    page.session_state["user_id"] = 1
    page.run()
    assert not page.exception
    assert any("private log" in (text.value or "") for text in page.text_area)


def test_login_refuses_backend_without_account_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pages.api_client as api_client

    monkeypatch.setattr(
        api_client,
        "detect_backend",
        lambda: {
            "mode": "api",
            "api_base_url": "http://localhost:8000",
            "legacy_backend_url": "http://localhost:5000",
            "message": "",
        },
    )

    class OldBackendClient:
        def __init__(self, _base_url: str) -> None:
            self.username = "alice"
            self.user_id = None

        def login(self, username: str, password: str) -> str:
            return "Logged in"

    monkeypatch.setattr(api_client, "ApiClient", OldBackendClient)
    page = AppTest.from_file(str(ROOT / "pages" / "login.py"))
    page.run()
    page.text_input(key="login_username").set_value("alice")
    page.text_input(key="login_password").set_value("ExamplePass123")
    page.button(key="login_btn").click().run()
    assert not page.exception
    assert not page.session_state["logged_in"]
    assert any(
        "did not provide an account identity" in item.value for item in page.error
    )


@pytest.mark.parametrize("user_id", [0, -1])
def test_login_accepts_existing_nonpositive_account_id(
    monkeypatch: pytest.MonkeyPatch, user_id: int
) -> None:
    import pages.api_client as api_client

    monkeypatch.setattr(
        api_client,
        "detect_backend",
        lambda: {
            "mode": "api",
            "api_base_url": "http://localhost:8000",
            "legacy_backend_url": "http://localhost:5000",
            "message": "",
        },
    )

    class ExistingAccountClient:
        def __init__(self, _base_url: str) -> None:
            self.username = "existing"
            self.user_id = user_id

        def login(self, username: str, password: str) -> str:
            return "Logged in"

        def get_config(self, username: str, password: str) -> None:
            return None

    monkeypatch.setattr(api_client, "ApiClient", ExistingAccountClient)
    page = AppTest.from_file(str(ROOT / "pages" / "login.py"))
    page.run()
    page.text_input(key="login_username").set_value("existing")
    page.text_input(key="login_password").set_value("ExamplePass123")
    page.button(key="login_btn").click().run()
    assert not page.exception
    assert page.session_state["logged_in"]
    assert page.session_state["user_id"] == user_id


def test_archive_browser_discards_selection_from_another_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / "user_2" / "2026" / "01"
    current.mkdir(parents=True)
    (current / "own.zip").write_bytes(b"own")
    other = tmp_path / "user_1" / "other.zip"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"private")
    (current / "linked.zip").symlink_to(other)
    (current.parent.parent / "linked_dir").symlink_to(
        other.parent, target_is_directory=True
    )
    work = current.parent.parent / "work_session"
    work.mkdir()
    (work / "temporary.zip").write_bytes(b"temporary")

    class Column:
        def __enter__(self) -> Column:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    class FakeStreamlit:
        def __init__(self) -> None:
            self.session_state = {
                "archived_selected_zip": {"name": "other.zip", "path": str(other)},
                "archived_selected_zip_data": b"private",
            }
            self.downloads: list[object] = []

        def columns(self, *_args: object, **_kwargs: object) -> tuple[Column, Column]:
            return Column(), Column()

        def button(self, *_args: object, **_kwargs: object) -> bool:
            return False

        def download_button(self, *_args: object, **kwargs: object) -> bool:
            self.downloads.append(kwargs["data"])
            return False

        def caption(self, *_args: object, **_kwargs: object) -> None:
            pass

        def text(self, *_args: object, **_kwargs: object) -> None:
            pass

        def warning(self, *_args: object, **_kwargs: object) -> None:
            pass

        def error(self, message: object) -> None:
            pytest.fail(str(message))

    fake = FakeStreamlit()
    monkeypatch.setattr(tools_app, "st", fake)
    tools_app.display_archived_files_browser(str(tmp_path / "user_2"))
    assert fake.downloads == []
    assert "archived_selected_zip" not in fake.session_state
    assert "archived_selected_zip_data" not in fake.session_state

    monkeypatch.setattr(fake, "button", lambda *_args, **_kwargs: True)
    tools_app.display_archived_files_browser(str(tmp_path / "user_2"))
    assert fake.downloads == [b"own"]
