"""Targeted astrometry fallback tests for forced re-solve failures."""

from __future__ import annotations

from astropy.wcs import WCS

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