"""Tests for the transient-finder source detection backend.

``find_candidates`` detects sources with SExtractor through ``stdpipe``. When
no SExtractor executable is available, ``stdpipe`` returns ``None`` instead of
raising, which used to be reported as "No objects found in the image." and
silently produced zero candidates. The finder now distinguishes that case from
a genuinely empty detection, reports it, and falls back to stdpipe's built-in
SEP detector.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from astropy.io import fits
from astropy.table import Table

import src.transient as transient


class _FakeStreamlit:
    """Minimal Streamlit stand-in that records the messages it receives."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.successes: list[str] = []
        self.infos: list[str] = []
        self.writes: list[str] = []

    def error(self, message, *args, **kwargs) -> None:
        self.errors.append(str(message))

    def warning(self, message, *args, **kwargs) -> None:
        self.warnings.append(str(message))

    def success(self, message, *args, **kwargs) -> None:
        self.successes.append(str(message))

    def info(self, message, *args, **kwargs) -> None:
        self.infos.append(str(message))

    def write(self, message, *args, **kwargs) -> None:
        self.writes.append(str(message))


@pytest.fixture
def fake_st(monkeypatch):
    fake = _FakeStreamlit()
    monkeypatch.setattr(transient, "st", fake)
    return fake


def _header() -> fits.Header:
    """Return a small but valid TAN WCS header."""
    return fits.Header(
        [
            ("NAXIS", 2),
            ("NAXIS1", 32),
            ("NAXIS2", 32),
            ("CTYPE1", "RA---TAN"),
            ("CTYPE2", "DEC--TAN"),
            ("CRVAL1", 150.0),
            ("CRVAL2", 2.0),
            ("CRPIX1", 16.5),
            ("CRPIX2", 16.5),
            ("CDELT1", -0.000277),
            ("CDELT2", 0.000277),
            ("DATE-OBS", "2025-01-01T00:00:00"),
        ]
    )


def _detections(n: int = 2) -> Table:
    return Table(
        {
            "x": np.arange(n, dtype=float) + 10.0,
            "y": np.arange(n, dtype=float) + 12.0,
            "flux": np.ones(n, dtype=float),
            "flags": np.zeros(n, dtype=int),
        }
    )


def _run(monkeypatch, sextractor_result, sep_result):
    """Call ``find_candidates`` with stubbed detectors and a valid image WCS.

    The photometry table is empty on purpose: it keeps the test focused on the
    detection step by returning right after it, while ``find_candidates`` still
    exercises the real WCS, gain and detection plumbing.
    """
    calls = {"sextractor": [], "sep": []}

    def _sextractor(image, **kwargs):
        calls["sextractor"].append(kwargs)
        if isinstance(sextractor_result, Exception):
            raise sextractor_result
        return sextractor_result

    def _sep(image, **kwargs):
        calls["sep"].append(kwargs)
        if isinstance(sep_result, Exception):
            raise sep_result
        return sep_result

    monkeypatch.setattr(transient.photometry, "get_objects_sextractor", _sextractor)
    monkeypatch.setattr(transient.photometry, "get_objects_sep", _sep)

    result = transient.find_candidates(
        pd.DataFrame(),
        np.zeros((32, 32), dtype=np.float32),
        _header(),
        3.0,
        1.0,
        150.0,
        2.0,
        0.5,
    )
    return result, calls


def test_missing_sextractor_binary_falls_back_to_sep(monkeypatch, fake_st):
    monkeypatch.setattr(transient, "_sextractor_binary_available", lambda: False)

    result, calls = _run(monkeypatch, None, _detections())

    assert result == []
    assert len(calls["sextractor"]) == 1
    assert len(calls["sep"]) == 1
    assert any("not found on PATH" in message for message in fake_st.warnings)
    assert any("SEP" in message for message in fake_st.warnings)
    # The fallback reaching the photometry step proves its detections were used.
    assert any("photometry_table" in message for message in fake_st.warnings)


def test_sextractor_failure_with_binary_present_is_reported(monkeypatch, fake_st):
    monkeypatch.setattr(transient, "_sextractor_binary_available", lambda: True)

    result, calls = _run(monkeypatch, None, _detections())

    assert result == []
    assert len(calls["sep"]) == 1
    assert any("SExtractor returned no result" in message for message in fake_st.warnings)


def test_sep_fallback_receives_the_same_detection_parameters(monkeypatch, fake_st):
    monkeypatch.setattr(transient, "_sextractor_binary_available", lambda: False)

    _result, calls = _run(monkeypatch, None, _detections())
    sex_calls = calls["sextractor"][0]
    sep_calls = calls["sep"][0]

    assert sep_calls == sex_calls
    assert sep_calls["thresh"] == transient.TRANSIENT_DETECT_THRESH
    assert sep_calls["aper"] == pytest.approx(4.5)
    assert sep_calls["sn"] == pytest.approx(2.0)
    assert sep_calls["edge"] == 10
    assert sep_calls["bg_size"] == 64
    assert sep_calls["wcs"] is not None


def test_sextractor_detection_does_not_call_sep(monkeypatch, fake_st):
    monkeypatch.setattr(transient, "_sextractor_binary_available", lambda: True)

    result, calls = _run(monkeypatch, _detections(), AssertionError("SEP must not run"))

    assert result == []
    assert len(calls["sextractor"]) == 1
    assert calls["sep"] == []
    assert any("Found 2 objects" in message for message in fake_st.successes)


def test_empty_sextractor_detection_is_not_a_fallback_case(monkeypatch, fake_st):
    monkeypatch.setattr(transient, "_sextractor_binary_available", lambda: True)

    result, calls = _run(monkeypatch, _detections(0), AssertionError("SEP must not run"))

    assert result == []
    assert calls["sep"] == []
    assert "No objects found in the image." in fake_st.warnings
    assert not any("SExtractor" in message for message in fake_st.errors)


def test_both_backends_unavailable_reports_actionable_error(monkeypatch, fake_st):
    monkeypatch.setattr(transient, "_sextractor_binary_available", lambda: False)

    result, calls = _run(monkeypatch, None, None)

    assert result == []
    assert len(calls["sep"]) == 1
    assert fake_st.errors, "a failing detection backend must not be silent"
    message = fake_st.errors[0]
    assert "SExtractor" in message
    assert "SEP" in message


def test_sep_fallback_exception_is_reported(monkeypatch, fake_st):
    monkeypatch.setattr(transient, "_sextractor_binary_available", lambda: False)

    result, calls = _run(monkeypatch, None, RuntimeError("sep exploded"))

    assert result == []
    assert any("SEP fallback source detection failed" in message for message in fake_st.errors)


def test_sextractor_exception_still_returns_empty(monkeypatch, fake_st):
    result, calls = _run(monkeypatch, RuntimeError("sextractor exploded"), _detections())

    assert result == []
    assert calls["sep"] == []
    assert any("SExtractor source detection failed" in message for message in fake_st.errors)
