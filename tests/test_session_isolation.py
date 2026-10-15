import os
import time
import zipfile
from io import BytesIO

import numpy as np
import pytest
from astropy.io import fits

import src.utils as utils
from src.utils import (
    cleanup_stale_session_files,
    session_results_path,
    session_tmp_directory,
    zip_results_on_exit,
)


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    def _ensure(directory=""):
        path = results / directory
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(utils, "ensure_output_directory", _ensure)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "tmp"))
    return tmp_path


class TestSessionPaths:
    def test_distinct_sessions_get_distinct_dirs(self, tmp_path):
        a = session_results_path(str(tmp_path), "aaa111")
        b = session_results_path(str(tmp_path), "bbb222")
        assert a != b and os.path.isdir(a) and os.path.isdir(b)

    def test_no_session_id_returns_user_dir(self, tmp_path):
        assert session_results_path(str(tmp_path), None) == str(tmp_path)

    def test_session_id_cannot_escape(self, tmp_path):
        path = session_results_path(str(tmp_path), "../../etc")
        assert os.path.dirname(os.path.abspath(path)) == str(tmp_path)

    def test_tmp_directory_is_per_session(self, isolated_dirs):
        a = session_tmp_directory("alice", "s1")
        b = session_tmp_directory("alice", "s2")
        assert a != b and os.path.basename(a) == "work_s1"


class TestStaleCleanup:
    def test_removes_old_work_dirs_but_keeps_current_and_archive(self, isolated_dirs):
        user_dir = utils.ensure_output_directory(directory="alice_results")
        old = session_results_path(user_dir, "old")
        cur = session_results_path(user_dir, "cur")
        fresh = session_results_path(user_dir, "fresh")
        zip_path = os.path.join(user_dir, "result.zip")
        open(zip_path, "wb").close()
        open(os.path.join(old, "x.csv"), "w").close()
        long_ago = time.time() - 3 * 86400
        for path in (old, cur, os.path.join(old, "x.csv"), zip_path):
            os.utime(path, (long_ago, long_ago))

        removed = cleanup_stale_session_files("alice", current_session_id="cur")

        assert removed >= 1
        assert not os.path.exists(old)
        assert os.path.exists(cur) and os.path.exists(fresh)
        assert os.path.exists(zip_path)

    def test_removes_old_loose_temp_uploads(self, isolated_dirs):
        tmp = session_tmp_directory("alice", None)
        old = os.path.join(tmp, "upload.fits")
        new = os.path.join(tmp, "new.fits")
        open(old, "w").close()
        open(new, "w").close()
        long_ago = time.time() - 3 * 86400
        os.utime(old, (long_ago, long_ago))
        cleanup_stale_session_files("alice")
        assert not os.path.exists(old) and os.path.exists(new)


class TestZipArchive:
    def test_zip_goes_to_archive_dir_and_only_contains_session_files(self, tmp_path):
        work = tmp_path / "work_s1"
        work.mkdir()
        (work / "img_catalog.csv").write_text("a")
        (work / "img2_catalog.csv").write_text("other-image")
        science = type("F", (), {"name": "img.fits"})()
        name, path = zip_results_on_exit(science, str(work), archive_dir=str(tmp_path))
        assert name
        with zipfile.ZipFile(os.path.join(str(tmp_path), name)) as zf:
            assert "img_catalog.csv" in zf.namelist()
            assert "img2_catalog.csv" not in zf.namelist()


class TestParseFitsPurity:
    def test_parse_returns_messages_instead_of_touching_streamlit(self, monkeypatch):
        import src.tools_app as ta

        calls = []
        for fn in ("error", "warning", "info"):
            monkeypatch.setattr(ta.st, fn, lambda *a, _fn=fn, **k: calls.append(_fn))

        buf = BytesIO()
        fits.PrimaryHDU(np.zeros((4, 4, 4, 4), dtype=np.float32)).writeto(buf)
        data, header, messages = ta._parse_fits_bytes.__wrapped__(buf.getvalue())

        assert data.shape == (4, 4)
        assert calls == []
        assert any(ui == "warning" for ui, _, _ in messages)

    def test_corrupt_file_reports_error_message(self):
        import src.tools_app as ta

        data, header, messages = ta._parse_fits_bytes.__wrapped__(b"not a fits file")
        assert data is None and header is None
        assert messages and messages[0][0] == "error"


class _Rerun(Exception):
    pass


class TestResetIsSessionLocal:
    def _fake_st(self, monkeypatch, state):
        import src.tools_app as ta

        cache_cleared = []

        class FakeCache:
            def clear(self):
                cache_cleared.append("data")

        class FakeSt:
            session_state = state
            cache_data = FakeCache()
            cache_resource = FakeCache()

            @staticmethod
            def rerun():
                raise _Rerun()

            @staticmethod
            def error(*a, **k):
                raise AssertionError(f"unexpected st.error: {a}")

        monkeypatch.setattr(ta, "st", FakeSt)
        return ta, cache_cleared

    def test_reset_clears_only_this_session(self, isolated_dirs, monkeypatch):
        tmp_dir = session_tmp_directory("alice", "mine")
        other_dir = session_tmp_directory("alice", "theirs")
        upload = os.path.join(tmp_dir, "up.fits")
        open(upload, "w").close()
        state = {
            "username": "alice",
            "session_id": "mine",
            "uploaded_temp_path": upload,
            "science_uploader_0": object(),
            "upload_generation": 0,
            "files_loaded": {"science_file": object()},
            "final_phot_table": object(),
            "run_analysis_pipeline": True,
            "base_filename": "img",
        }
        ta, cache_cleared = self._fake_st(monkeypatch, state)

        with pytest.raises(_Rerun):
            ta.clear_all_caches()

        assert cache_cleared == []
        assert not os.path.exists(tmp_dir) and os.path.isdir(other_dir)
        assert state["upload_generation"] == 1
        assert "science_uploader_0" not in state
        assert "final_phot_table" not in state and "uploaded_temp_path" not in state
        assert state["run_analysis_pipeline"] is False
        assert state["files_loaded"] == {"science_file": None}
        assert state["session_id"] == "mine"
        assert state["base_filename"] == "photometry"


class TestDownloadButtonPrefix:
    def test_does_not_pool_files_of_images_sharing_a_prefix(self, tmp_path, monkeypatch):
        import src.tools_app as ta

        for name in ("img_catalog.csv", "img.log", "img2_catalog.csv", "image_x.csv"):
            (tmp_path / name).write_text("x")
        captured = {}

        class FakeSt:
            session_state = {"base_filename": "img"}

            @staticmethod
            def warning(*a, **k):
                pass

            @staticmethod
            def caption(msg):
                captured["caption"] = msg

            @staticmethod
            def download_button(*a, data=None, **k):
                with zipfile.ZipFile(data) as zf:
                    captured["names"] = sorted(zf.namelist())
                return False

        monkeypatch.setattr(ta, "st", FakeSt)
        ta.provide_download_buttons(str(tmp_path))
        assert captured["names"] == ["img.log", "img_catalog.csv"]


class TestLoadFitsReplaysMessages:
    def test_messages_reach_the_calling_session(self, monkeypatch):
        import src.tools_app as ta

        shown, logged = [], []

        class FakeSt:
            session_state = {"log_buffer": object()}
            error = staticmethod(lambda m: shown.append(("error", m)))
            warning = staticmethod(lambda m: shown.append(("warning", m)))
            info = staticmethod(lambda m: shown.append(("info", m)))

        monkeypatch.setattr(ta, "st", FakeSt)
        monkeypatch.setattr(
            ta, "write_to_log", lambda buf, msg, level="INFO": logged.append((level, msg))
        )
        buf = BytesIO()
        fits.PrimaryHDU(np.zeros((2, 3, 3, 3), dtype=np.float32)).writeto(buf)
        buf.seek(0)

        data, header = ta.load_fits_data(buf)
        assert data.shape == (3, 3)
        assert any(kind == "warning" for kind, _ in shown)

        # A second, identical call (cache hit) must replay them again.
        shown.clear()
        buf.seek(0)
        ta.load_fits_data(buf)
        assert any(kind == "warning" for kind, _ in shown)

    def test_empty_upload_is_reported(self, monkeypatch):
        import src.tools_app as ta

        errors = []
        monkeypatch.setattr(
            ta, "st", type("S", (), {"session_state": {}, "error": staticmethod(errors.append)})
        )
        assert ta.load_fits_data(BytesIO(b"")) == (None, None)
        assert errors


class TestAstroqueryConfiguredOnce:
    def test_shared_clients_are_configured_at_import(self):
        import src.xmatch_catalogs as xm

        assert xm.Gaia.MAIN_GAIA_TABLE == "gaiadr3.gaia_source"
        assert xm.Skybot._get_raw_response is False

    def test_no_per_request_writes_to_shared_clients(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src"
        for fname in ("xmatch_catalogs.py", "transient.py"):
            tree = ast.parse((root / fname).read_text(encoding="utf-8"))
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(func):
                    if (
                        isinstance(node, ast.Assign)
                        and isinstance(node.targets[0], ast.Attribute)
                        and isinstance(node.targets[0].value, ast.Name)
                        and node.targets[0].value.id in ("Gaia", "Skybot", "Simbad")
                    ):
                        pytest.fail(
                            f"{fname}:{node.lineno} mutates shared client "
                            f"{node.targets[0].value.id} inside a function"
                        )
