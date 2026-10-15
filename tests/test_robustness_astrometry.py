"""Targeted astrometry fallback tests for forced re-solve failures."""

from __future__ import annotations

import numpy as np
import pytest
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS

import src.astrometry as astrometry_module
from src.header_utils import recover_original_wcs


def _make_valid_header():
    """Create a minimal valid celestial WCS header."""
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [256.0, 256.0]
    wcs.wcs.cdelt = [-0.0002777778, 0.0002777778]
    wcs.wcs.crval = [150.0, 2.0]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    return wcs.to_header()


def test_recover_original_wcs_restores_valid_header_after_forced_solve_failure():
    """A valid original header should restore a usable WCS after solver failure."""
    original_header = _make_valid_header()

    def fake_load_fits_data(_science_file):
        return None, original_header

    restored_wcs, restored_header = recover_original_wcs(
        science_file=object(),
        load_fits_data_func=fake_load_fits_data,
        safe_wcs_create_func=lambda header: (WCS(header), None, []),
    )

    assert restored_wcs is not None
    assert restored_header == original_header
    assert restored_wcs.wcs.crval[0] == original_header["CRVAL1"]
    assert restored_wcs.wcs.crval[1] == original_header["CRVAL2"]


def test_recover_original_wcs_returns_none_when_reloaded_header_has_no_valid_wcs():
    """If the original header cannot create a WCS, the fallback should fail cleanly."""

    def fake_load_fits_data(_science_file):
        return None, {"SIMPLE": True}

    restored_wcs, restored_header = recover_original_wcs(
        science_file=object(),
        load_fits_data_func=fake_load_fits_data,
        safe_wcs_create_func=lambda _header: (None, "Missing WCS", []),
    )

    assert restored_wcs is None
    assert restored_header is None


def test_refinement_diagnostics_are_logged_and_none_keeps_initial_wcs(
    tmp_path, monkeypatch
):
    """Log STDPipe diagnostics and preserve the solved WCS on failure."""
    image_path = tmp_path / "image.fits"
    fits.PrimaryHDU(data=np.zeros((32, 32))).writeto(image_path)

    initial_wcs = WCS(naxis=2)
    initial_wcs.wcs.crpix = [16.0, 16.0]
    initial_wcs.wcs.cdelt = [-0.0002777778, 0.0002777778]
    initial_wcs.wcs.crval = [150.0, 2.0]
    initial_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

    sources = Table(
        {
            "x": np.arange(10, 20, dtype=float),
            "y": np.arange(12, 22, dtype=float),
            "xerr": np.full(10, 0.1),
            "yerr": np.full(10, 0.1),
            "flux": np.full(10, 1000.0),
            "fluxerr": np.full(10, 10.0),
            "mag": np.full(10, 15.0),
            "magerr": np.full(10, 0.01),
            "flags": np.zeros(10, dtype=int),
        }
    )
    catalog = Table(
        {
            "RAJ2000": np.full(11, 150.0),
            "DEJ2000": np.full(11, 2.0),
            "rmag": np.full(11, 15.0),
        }
    )

    monkeypatch.setattr(
        astrometry_module,
        "_try_source_detection_improved",
        lambda *_args, **_kwargs: sources,
    )
    monkeypatch.setattr(
        astrometry_module,
        "solve_with_enhanced_strategy",
        lambda *_args, **_kwargs: (initial_wcs, []),
    )
    monkeypatch.setattr(
        astrometry_module.astrometry,
        "get_frame_center",
        lambda **_kwargs: (150.0, 2.0, 0.01),
    )
    monkeypatch.setattr(
        astrometry_module.astrometry,
        "get_pixscale",
        lambda **_kwargs: 1 / 3600,
    )
    monkeypatch.setattr(
        astrometry_module.catalogs,
        "get_cat_vizier",
        lambda *_args, **_kwargs: catalog,
    )
    call_options = {}

    def fake_refine_with_scamp(*_args, verbose, **kwargs):
        call_options.update(kwargs)
        verbose("SCAMP invocation returned code 1")
        return None, {
            "executable": "/usr/bin/scamp",
            "reason": "SCAMP invocation returned code 1",
        }

    monkeypatch.setattr(
        astrometry_module,
        "refine_wcs_with_scamp",
        fake_refine_with_scamp,
    )

    solved_wcs, updated_header, messages, error = (
        astrometry_module.solve_with_astrometrynet(str(image_path))
    )

    assert error is None
    assert solved_wcs is initial_wcs
    assert WCS(updated_header).wcs.crval[0] == initial_wcs.wcs.crval[0]
    assert call_options["cat_col_mag"] == "rmag"
    assert call_options["order"] == 3
    assert call_options["sr"] == pytest.approx(7 / 3600)
    assert call_options["wcs"] is initial_wcs
    assert any(
        "SCAMP: SCAMP invocation returned code 1" in msg
        for msg in messages
    )
    assert any("missing source columns=none" in msg for msg in messages)
    assert any("missing catalog columns=none" in msg for msg in messages)
    assert any(
        "Pan-STARRS DR1" in msg for msg in messages
    )


def test_online_astrometry_fallback_uses_private_source_list_and_keeps_wcs(
    tmp_path, monkeypatch
):
    """A local failure should try the private online source-list solver."""
    image_path = tmp_path / "image.fits"
    fits.PrimaryHDU(
        data=np.zeros((32, 32)), header=_make_valid_header()
    ).writeto(image_path)

    initial_wcs = WCS(_make_valid_header())
    sources = Table(
        {
            "x": np.arange(10, 20, dtype=float),
            "y": np.arange(12, 22, dtype=float),
            "xerr": np.full(10, 0.1),
            "yerr": np.full(10, 0.1),
            "flux": np.full(10, 1000.0),
            "fluxerr": np.full(10, 10.0),
            "mag": np.full(10, 15.0),
            "magerr": np.full(10, 0.01),
            "flags": np.zeros(10, dtype=int),
        }
    )
    catalog = Table(
        {
            "RAJ2000": np.full(11, 150.0),
            "DEJ2000": np.full(11, 2.0),
            "rmag": np.full(11, 15.0),
        }
    )

    monkeypatch.setattr(
        astrometry_module,
        "_try_source_detection_improved",
        lambda *_args, **_kwargs: sources,
    )
    monkeypatch.setattr(
        astrometry_module,
        "solve_with_enhanced_strategy",
        lambda *_args, **_kwargs: (None, ["ERROR: local solve failed"]),
    )
    monkeypatch.setattr(
        astrometry_module,
        "_get_online_astrometry_api_key",
        lambda: "test-secret",
    )
    remote_options = {}

    def fake_online_solve(_sources, **kwargs):
        remote_options.update(kwargs)
        return initial_wcs

    monkeypatch.setattr(
        astrometry_module.astrometry,
        "blind_match_astrometrynet",
        fake_online_solve,
    )
    monkeypatch.setattr(
        astrometry_module.astrometry,
        "get_frame_center",
        lambda **_kwargs: (150.0, 2.0, 0.01),
    )
    monkeypatch.setattr(
        astrometry_module.astrometry,
        "get_pixscale",
        lambda **_kwargs: 1 / 3600,
    )
    monkeypatch.setattr(
        astrometry_module.catalogs,
        "get_cat_vizier",
        lambda *_args, **_kwargs: catalog,
    )
    monkeypatch.setattr(
        astrometry_module,
        "refine_wcs_with_scamp",
        lambda *_args, **_kwargs: (None, {"reason": "disabled for test"}),
    )

    solved_wcs, _updated_header, messages, error = (
        astrometry_module.solve_with_astrometrynet(str(image_path))
    )

    assert error is None
    assert solved_wcs is initial_wcs
    assert remote_options["api_key"] == "test-secret"
    assert remote_options["publicly_visible"] == "n"
    assert remote_options["solve_timeout"] == 600
    assert any(
        "Online Astrometry.net plate solving successful" in msg
        for msg in messages
    )
    assert all("test-secret" not in message for message in messages)


def test_build_scamp_header_converts_sip_to_tpv():
    """A SIP solution must be rewritten as TPV without moving the reference pixel."""
    from astropy.wcs import Sip

    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [10.0, 10.0]
    wcs.wcs.cdelt = [-0.0002778, 0.0002778]
    wcs.wcs.crval = [150.0, 2.0]
    wcs.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"]
    wcs.wcs.set()
    a = np.zeros((4, 4))
    b = np.zeros((4, 4))
    a[2, 0] = 1e-5
    wcs.sip = Sip(a, b, a, b, np.array([10.0, 10.0]))

    header = astrometry_module._build_scamp_header(wcs, (20, 20))

    assert header["CTYPE1"] == "RA---TPV"
    assert header["CTYPE2"] == "DEC--TPV"
    assert "PV1_1" in header
    assert header["CRVAL1"] == pytest.approx(150.0)
    assert header["CRVAL2"] == pytest.approx(2.0)

    converted = WCS(header)
    ra_orig, dec_orig = wcs.all_pix2world(10.0, 10.0, 0)
    ra_new, dec_new = converted.all_pix2world(10.0, 10.0, 0)
    assert float(ra_new) == pytest.approx(float(ra_orig), abs=1e-6)
    assert float(dec_new) == pytest.approx(float(dec_orig), abs=1e-6)


def test_refine_wcs_with_scamp_reports_missing_executable(monkeypatch):
    """Without a SCAMP binary the refinement must fail cleanly, not raise."""
    monkeypatch.setattr(
        astrometry_module, "find_scamp_executable", lambda *_a, **_k: None
    )
    sources = Table(
        {
            "x": np.arange(10, 20, dtype=float),
            "y": np.arange(10, 20, dtype=float),
            "xerr": np.full(10, 0.1),
            "yerr": np.full(10, 0.1),
            "flux": np.full(10, 1000.0),
            "fluxerr": np.full(10, 10.0),
            "mag": np.full(10, 15.0),
            "magerr": np.full(10, 0.01),
            "flags": np.zeros(10, dtype=int),
        }
    )
    catalog = Table(
        {
            "RAJ2000": np.full(10, 150.0),
            "DEJ2000": np.full(10, 2.0),
            "rmag": np.full(10, 15.0),
        }
    )
    wcs = WCS(naxis=2)
    wcs.wcs.crval = [150.0, 2.0]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

    refined_wcs, diagnostics = astrometry_module.refine_wcs_with_scamp(
        sources, catalog, wcs=wcs, sr=7 / 3600.0
    )

    assert refined_wcs is None
    assert diagnostics["success"] is False
    assert diagnostics["executable"] is None
    assert "SCAMP executable not found" in diagnostics["reason"]


def test_refine_wcs_with_scamp_accepts_good_and_rejects_worse_solution(
    monkeypatch,
):
    """The direct SCAMP path must validate the solution before returning it."""
    import os
    import subprocess as subprocess_module

    true_wcs = WCS(naxis=2)
    true_wcs.wcs.crpix = [64.0, 64.0]
    true_wcs.wcs.cdelt = [-1 / 3600.0, 1 / 3600.0]
    true_wcs.wcs.crval = [150.0, 2.0]
    true_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

    grid = np.arange(5.0, 5.0 + 8 * 15, 15.0)
    xs = np.tile(grid, grid.size)
    ys = np.repeat(grid, grid.size)
    ra, dec = true_wcs.all_pix2world(xs, ys, 0)

    sources = Table(
        {
            "x": xs,
            "y": ys,
            "xerr": np.full(xs.size, 0.1),
            "yerr": np.full(xs.size, 0.1),
            "flux": np.full(xs.size, 1000.0),
            "fluxerr": np.full(xs.size, 10.0),
            "mag": np.full(xs.size, 15.0),
            "magerr": np.full(xs.size, 0.01),
            "flags": np.zeros(xs.size, dtype=int),
        }
    )
    catalog = Table(
        {
            "RAJ2000": ra,
            "DEJ2000": dec,
            "rmag": np.full(xs.size, 15.0),
        }
    )

    def shifted_wcs(offset_pixels):
        wcs = WCS(naxis=2)
        wcs.wcs.crpix = [64.0 + offset_pixels, 64.0]
        wcs.wcs.cdelt = list(true_wcs.wcs.cdelt)
        wcs.wcs.crval = [150.0, 2.0]
        wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        return wcs

    initial_wcs = shifted_wcs(1.0)  # one pixel off the truth

    monkeypatch.setattr(
        astrometry_module,
        "find_scamp_executable",
        lambda *_a, **_k: "/usr/bin/scamp",
    )

    def fake_run_writing(output_wcs):
        def fake_run(command, cwd=None, **_kwargs):
            header = output_wcs.to_header(relax=True)
            with open(os.path.join(cwd, "objects.head"), "w") as handle:
                handle.write(header.tostring(sep="\n", endcard=True))
            Table(
                {"NDeg_Reference": [len(sources)], "Chi2_Reference": [1.0]}
            ).write(os.path.join(cwd, "scamp.xml"), format="votable")
            return subprocess_module.CompletedProcess(command, 0, "", "")

        return fake_run

    monkeypatch.setattr(
        astrometry_module.subprocess, "run", fake_run_writing(true_wcs)
    )
    refined_wcs, diagnostics = astrometry_module.refine_wcs_with_scamp(
        sources, catalog, wcs=initial_wcs, sr=7 / 3600.0
    )
    assert refined_wcs is not None
    assert diagnostics["success"] is True
    assert diagnostics["refined_rms_arcsec"] < diagnostics["initial_rms_arcsec"]

    monkeypatch.setattr(
        astrometry_module.subprocess, "run", fake_run_writing(shifted_wcs(3.0))
    )
    refined_wcs, diagnostics = astrometry_module.refine_wcs_with_scamp(
        sources, catalog, wcs=initial_wcs, sr=7 / 3600.0
    )
    assert refined_wcs is None
    assert diagnostics["success"] is False
    assert "worse than the initial" in diagnostics["reason"]


def test_refine_wcs_with_scamp_retries_with_wider_tolerances(monkeypatch):
    calls = []

    def fake_once(sources, cat, wcs, sr, extra_options=None, **kwargs):
        calls.append(extra_options)
        if extra_options:
            return "wcs", {"n_matches": 120.0}
        return None, {"n_matches": 0.0, "reason": "only 0 reference matches"}

    monkeypatch.setattr(astrometry_module, "_refine_wcs_with_scamp_once", fake_once)
    refined, diagnostics = astrometry_module.refine_wcs_with_scamp(
        None, None, None, 1.0
    )
    assert refined == "wcs"
    assert calls[0] == {} and "POSITION_MAXERR" in calls[1]
    assert diagnostics["attempts"] == 2
