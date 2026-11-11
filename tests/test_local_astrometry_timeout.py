"""Local solver process, deadline, and fallback regressions."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from streamlit.testing.v1 import AppTest

import src.astrometry as astrometry
import src.pipeline as pipeline
import src.tools_app as tools_app
import src.utils as utils


def _sources() -> Table:
    return Table(
        {
            "x": np.arange(10.0, 30.0),
            "y": np.arange(11.0, 31.0),
            "flux": np.full(20, 1000.0),
            "fluxerr": np.full(20, 10.0),
        }
    )


def _wcs() -> WCS:
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [16.0, 16.0]
    wcs.wcs.cdelt = [-0.001, 0.001]
    wcs.wcs.crval = [150.0, 2.0]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    return wcs


def _fake_solver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    executable = bindir / "solve-field"
    executable.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))


@pytest.mark.skipif(os.name != "posix", reason="Uses a POSIX executable script")
def test_real_worker_preserves_stdpipe_iterations_and_solution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("RPP_TEST_SOLVER_CALLS", str(calls))
    _fake_solver(
        tmp_path,
        monkeypatch,
        """import json, os, sys
from astropy.io import fits
from astropy.wcs import WCS
with open(os.environ["RPP_TEST_SOLVER_CALLS"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
workdir = sys.argv[sys.argv.index("--dir") + 1]
wcs = WCS(naxis=2)
wcs.wcs.crpix = [16.0, 16.0]
wcs.wcs.cdelt = [-0.001, 0.001]
wcs.wcs.crval = [150.0, 2.0]
wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
fits.PrimaryHDU(header=wcs.to_header()).writeto(os.path.join(workdir, "list.wcs"))
""",
    )
    solved, _ = astrometry._run_local_astrometry(
        _sources(),
        {"order": 3, "scale_lower": 0.5, "scale_upper": 15.0, "verbose": True},
        time.monotonic() + 15,
    )
    assert solved is not None
    np.testing.assert_allclose(
        solved.all_pix2world([[5, 6], [16, 16]], 0),
        _wcs().all_pix2world([[5, 6], [16, 16]], 0),
    )
    commands = [
        json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()
    ]
    assert len(commands) == 2
    assert "--scale-low" in commands[0] and "--scale-high" in commands[0]
    assert "--verify" in commands[1]


def test_missing_solver_returns_a_diagnostic() -> None:
    solved, detail = astrometry._run_local_astrometry(
        _sources(),
        {"_exe": "/definitely/not/a/solve-field", "verbose": True},
        time.monotonic() + 15,
    )
    assert solved is None
    assert "Can't find Astrometry.Net binary" in detail


def test_interrupted_worker_is_cleaned_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InterruptedProcess:
        def wait(self, timeout: float) -> int:
            raise KeyboardInterrupt

    process = InterruptedProcess()
    stopped: list[InterruptedProcess] = []
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(
        astrometry.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(astrometry, "_stop_local_astrometry_process", stopped.append)
    with pytest.raises(KeyboardInterrupt):
        astrometry._run_local_astrometry(_sources(), {}, time.monotonic() + 10)
    assert stopped == [process]
    assert not list(tmp_path.glob("rpp_astrometry_*"))


@pytest.mark.skipif(sys.platform != "linux", reason="Checks Linux process states")
def test_timeout_stops_solver_descendants_and_removes_workdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pids = tmp_path / "pids.json"
    monkeypatch.setenv("RPP_TEST_SOLVER_PIDS", str(pids))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _fake_solver(
        tmp_path,
        monkeypatch,
        """import json, os, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open(os.environ["RPP_TEST_SOLVER_PIDS"], "w", encoding="utf-8") as handle:
    json.dump([os.getpid(), child.pid], handle)
time.sleep(60)
""",
    )
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="time budget exhausted"):
        astrometry._run_local_astrometry(
            _sources(),
            {"verbose": True},
            started + 5.0,
        )
    assert time.monotonic() - started < 8
    assert pids.exists(), "The fake solve-field must have started before the timeout"
    for pid in json.loads(pids.read_text(encoding="utf-8")):
        stat = Path(f"/proc/{pid}/stat")
        until = time.monotonic() + 1
        while stat.exists() and time.monotonic() < until:
            try:
                if stat.read_text(encoding="utf-8").split()[2] in {"Z", "X"}:
                    break
            except FileNotFoundError:
                break
            time.sleep(0.01)
        if stat.exists():
            assert stat.read_text(encoding="utf-8").split()[2] in {"Z", "X"}
    assert not list(tmp_path.glob("rpp_astrometry_*"))


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "invalid"])
def test_invalid_timeout_is_reported_and_uses_default(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv(astrometry.LOCAL_ASTROMETRY_TIMEOUT_ENV, value)
    messages: list[str] = []
    assert astrometry._local_astrometry_timeout(messages) == 180
    assert any(
        "Invalid" in message and "180 seconds" in message for message in messages
    )


def test_positive_timeout_configuration_is_honored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(astrometry.LOCAL_ASTROMETRY_TIMEOUT_ENV, "240.5")
    messages: list[str] = []
    assert astrometry._local_astrometry_timeout(messages) == 240.5
    assert not messages


def test_local_stages_share_one_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    deadlines: list[float] = []

    def no_solution(
        _sources: Table,
        _options: dict[str, object],
        deadline: float,
    ) -> tuple[None, str]:
        deadlines.append(deadline)
        clock[0] += 60
        return None, "no solution"

    monkeypatch.setattr(astrometry, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(astrometry, "_run_local_astrometry", no_solution)
    monkeypatch.delenv(astrometry.LOCAL_ASTROMETRY_TIMEOUT_ENV, raising=False)
    solved, _ = astrometry.solve_with_enhanced_strategy(
        _sources(), _wcs().to_header(), np.zeros((32, 32))
    )
    assert solved is None
    assert deadlines == [180, 180, 180]


def test_timeout_skips_local_retries_and_uses_private_online_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "image.fits"
    fits.PrimaryHDU(np.zeros((32, 32)), _wcs().to_header()).writeto(image_path)
    sources = _sources()
    assert sources.meta is not None
    sources.meta["detection_thresh"] = 3.0
    detections: list[float | None] = []
    clock = [0.0]
    remote_options: dict[str, object] = {}

    def detect(_image: np.ndarray, _header: fits.Header, **kwargs: object) -> Table:
        threshold = kwargs.get("max_thresh")
        assert threshold is None or isinstance(threshold, float)
        detections.append(threshold)
        return sources

    def time_out(
        _sources: Table, _options: dict[str, object], deadline: float
    ) -> tuple[None, str]:
        clock[0] = deadline + 0.001
        raise TimeoutError("synthetic timeout")

    def online_solve(_sources: Table, **kwargs: object) -> WCS:
        assert _sources is sources
        remote_options.update(kwargs)
        return _wcs()

    monkeypatch.setattr(astrometry, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(astrometry, "_try_source_detection_improved", detect)
    monkeypatch.setattr(astrometry, "_run_local_astrometry", time_out)
    monkeypatch.setattr(
        astrometry, "_get_online_astrometry_api_key", lambda: "synthetic-key"
    )
    monkeypatch.setattr(
        astrometry.astrometry, "blind_match_astrometrynet", online_solve
    )
    monkeypatch.setattr(
        astrometry.astrometry, "get_frame_center", lambda **_kwargs: (150.0, 2.0, 0.1)
    )
    monkeypatch.setattr(astrometry.astrometry, "get_pixscale", lambda **_kwargs: 0.001)
    monkeypatch.setattr(
        astrometry,
        "get_vizier_catalog_with_fallback",
        lambda *_args, **_kwargs: (None, False),
    )
    monkeypatch.delenv(astrometry.LOCAL_ASTROMETRY_TIMEOUT_ENV, raising=False)

    solved, header, messages, error = astrometry.solve_with_astrometrynet(image_path)
    assert error is None and solved is not None and header is not None
    assert detections == [None]
    assert remote_options["publicly_visible"] == "n"
    assert any(
        "Local astrometry budget: 180 seconds" in message for message in messages
    )
    assert any("time budget exhausted" in message for message in messages)
    assert any(
        "online" in message.lower() and "source list" in message.lower()
        for message in messages
    )
    assert all("synthetic-key" not in message for message in messages)


def test_cleanup_failure_does_not_start_online_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "image.fits"
    fits.PrimaryHDU(np.zeros((32, 32)), _wcs().to_header()).writeto(image_path)
    monkeypatch.setattr(
        astrometry,
        "_try_source_detection_improved",
        lambda *_args, **_kwargs: _sources(),
    )

    def fail_cleanup(
        _sources: Table,
        _options: dict[str, object],
        _deadline: float,
    ) -> tuple[None, str]:
        raise astrometry.LocalAstrometryCleanupError("synthetic cleanup failure")

    def unexpected_online() -> str:
        pytest.fail("Online fallback must not start when local cleanup failed")

    monkeypatch.setattr(astrometry, "_run_local_astrometry", fail_cleanup)
    monkeypatch.setattr(astrometry, "_get_online_astrometry_api_key", unexpected_online)
    solved, header, messages, error = astrometry.solve_with_astrometrynet(image_path)
    assert solved is None and header is None
    assert error and "synthetic cleanup failure" in error
    assert any("synthetic cleanup failure" in message for message in messages)


def test_online_failure_after_local_failure_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "image.fits"
    fits.PrimaryHDU(np.zeros((32, 32)), _wcs().to_header()).writeto(image_path)
    monkeypatch.setattr(
        astrometry,
        "_try_source_detection_improved",
        lambda *_args, **_kwargs: _sources(),
    )
    monkeypatch.setattr(
        astrometry, "_run_local_astrometry", lambda *_args: (None, "no local solution")
    )
    monkeypatch.setattr(
        astrometry, "_get_online_astrometry_api_key", lambda: "synthetic-key"
    )
    monkeypatch.setattr(
        astrometry.astrometry, "get_frame_center", lambda **_kwargs: (150.0, 2.0, 0.1)
    )
    monkeypatch.setattr(astrometry.astrometry, "get_pixscale", lambda **_kwargs: 0.001)

    def offline(_sources: Table, **_kwargs: object) -> None:
        raise OSError("synthetic network failure")

    monkeypatch.setattr(astrometry.astrometry, "blind_match_astrometrynet", offline)
    solved, header, messages, error = astrometry.solve_with_astrometrynet(image_path)
    assert solved is None and header is None and error == "Plate solving failed"
    assert any("synthetic network failure" in message for message in messages)
    assert all("synthetic-key" not in message for message in messages)


def test_forced_solve_timeout_retains_original_wcs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = _wcs().to_header()
    header["PIXSCALE"] = 1.0
    image_path = tmp_path / "image.fits"
    fits.PrimaryHDU(np.zeros((32, 32)), header).writeto(image_path)
    sources = _sources()
    assert sources.meta is not None
    sources.meta["detection_thresh"] = 3.0
    clock = [0.0]

    def output_directory(directory: str = "") -> str:
        path = tmp_path / "results" / directory
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def time_out(
        _sources: Table, _options: dict[str, object], deadline: float
    ) -> tuple[None, str]:
        clock[0] = deadline + 0.001
        raise TimeoutError("synthetic timeout")

    monkeypatch.setattr(utils, "ensure_output_directory", output_directory)
    monkeypatch.setattr(tools_app, "try_gaia_server", lambda: False)
    monkeypatch.setattr(astrometry, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(
        astrometry, "_try_source_detection_improved", lambda *_args, **_kwargs: sources
    )
    monkeypatch.setattr(astrometry, "_run_local_astrometry", time_out)
    monkeypatch.setattr(astrometry, "_get_online_astrometry_api_key", lambda: None)
    monkeypatch.setattr(pipeline, "airmass", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(
        pipeline,
        "detection_and_photometry",
        lambda *_args, **_kwargs: (None, None, None, None, None, None, None, None),
    )
    page = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "pages" / "app.py")
    )
    page.session_state["logged_in"] = True
    page.session_state["username"] = "synthetic"
    page.session_state["user_id"] = 9
    page.session_state["stale_files_cleaned"] = True
    page.session_state["uploaded_temp_path"] = str(image_path)
    page.session_state["uploaded_name"] = "image.fits"
    page.run(timeout=30)
    next(
        toggle for toggle in page.toggle if toggle.label == "Astrometry check"
    ).set_value(True)
    page.session_state["run_analysis_pipeline"] = True
    page.run(timeout=30)
    assert not page.exception
    assert not page.session_state["proceed_without_wcs"]
    assert page.session_state["calibrated_header"]["CTYPE1"] == header["CTYPE1"]
    assert any("time budget exhausted" in warning.value for warning in page.warning)
    assert any(
        "using the original valid WCS" in warning.value for warning in page.warning
    )
