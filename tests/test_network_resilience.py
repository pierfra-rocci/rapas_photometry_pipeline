"""Focused resilience tests for remote catalog and timeout handling."""

from __future__ import annotations

import requests

import pytest
from astropy.table import Table
from astropy.wcs import WCS

from src.transient import filter_skybot_candidates
from src.utils import safe_catalog_query
from src.xmatch_catalogs import cross_match_with_gaia


def _make_candidates_table() -> Table:
    """Create a minimal candidate table for SkyBoT filtering tests."""
    return Table(
        {
            "ra": [10.0, 10.001],
            "dec": [20.0, 20.001],
        }
    )


def _make_valid_wcs() -> WCS:
    """Create a minimal celestial WCS for catalog cross-match tests."""
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [50.0, 50.0]
    wcs.wcs.cdelt = [-0.0002777778, 0.0002777778]
    wcs.wcs.crval = [150.0, 2.0]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    return wcs


def test_safe_catalog_query_reports_timeout_message():
    """Timeouts should produce the dedicated timeout message, not a generic network error."""

    def _raise_timeout():
        raise requests.exceptions.Timeout("catalog timed out")

    result, error = safe_catalog_query(_raise_timeout, "Failed to query SIMBAD")

    assert result is None
    assert error == "Failed to query SIMBAD: Query timed out"


def test_filter_skybot_candidates_returns_input_on_timeout(monkeypatch):
    """SkyBoT failures should keep the current candidates instead of aborting filtering."""
    candidates = _make_candidates_table()

    monkeypatch.setattr(
        "src.transient.stdpipe_astrometry.get_objects_center",
        lambda *_args, **_kwargs: (10.0, 20.0, 0.01),
    )

    def _raise_timeout(*_args, **_kwargs):
        raise requests.exceptions.Timeout("skybot timed out")

    monkeypatch.setattr("requests.get", _raise_timeout)

    warnings = []
    monkeypatch.setattr("streamlit.warning", warnings.append)

    result = filter_skybot_candidates(candidates, "2026-01-01T00:00:00")

    assert result is candidates
    assert len(result) == len(candidates)
    assert warnings
    assert "SkyBoT query failed" in warnings[0]
    assert "Skipping Solar System object filtering" in warnings[0]


def test_cross_match_with_gaia_returns_none_after_vizier_and_tap_failures(monkeypatch):
    """If both Gaia VizieR and direct TAP fail, the helper should return None plus logs."""
    phot_table = Table({"xcenter": [50.0, 52.0], "ycenter": [50.0, 49.0]})
    science_header = {"RA": 150.0, "DEC": 2.0, "NAXIS1": 100, "NAXIS2": 100}

    class FakeVizier:
        def __init__(self, *args, **kwargs):
            self.ROW_LIMIT = -1

        def query_region(self, *_args, **_kwargs):
            raise RuntimeError("vizier unavailable")

    monkeypatch.setattr("src.xmatch_catalogs.Vizier", FakeVizier)

    def _raise_tap_failure(*_args, **_kwargs):
        raise RuntimeError("tap failure")

    monkeypatch.setattr("src.xmatch_catalogs.Gaia.launch_job", _raise_tap_failure)

    matched_table, log_messages = cross_match_with_gaia(
        phot_table,
        science_header,
        pixel_size_arcsec=1.5,
        mean_fwhm_pixel=3.0,
        filter_band="phot_g_mean_mag",
        filter_max_mag=18.0,
        refined_wcs=_make_valid_wcs(),
    )

    assert matched_table is None
    assert any("Gaia VizieR query failed" in message for message in log_messages)
    assert any("Direct GAIA TAP query failed" in message for message in log_messages)
    assert any("Both VizieR and direct GAIA queries failed" in message for message in log_messages)