"""Focused resilience tests for remote catalog and timeout handling."""

from __future__ import annotations

import base64
import json
import re
from contextlib import nullcontext

import requests
import pandas as pd
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.table import QTable, Table
from astropy.time import Time
from astropy.wcs import WCS
from xml.parsers.expat import ExpatError

import src.xmatch_catalogs as xmatch_catalogs
from src.tools_app import display_catalog_in_aladin
from src.transient import filter_skybot_candidates
from src.utils import safe_catalog_query
from src.xmatch_catalogs import cross_match_with_gaia, enhance_catalog


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


def test_filter_skybot_candidates_requests_votable_response(monkeypatch):
    """Astroquery's parser requires VOTable rather than SkyBoT's plain-text output."""
    candidates = _make_candidates_table()
    request_params = {}

    monkeypatch.setattr(
        "src.transient.stdpipe_astrometry.get_objects_center",
        lambda *_args, **_kwargs: (10.0, 20.0, 0.01),
    )

    class EmptyResponse:
        status_code = 200
        text = "<VOTABLE/>"

    def _capture_request(_url, *, params, timeout):
        request_params.update(params)
        assert timeout == 120
        return EmptyResponse()

    monkeypatch.setattr("requests.get", _capture_request)
    monkeypatch.setattr("src.transient.Skybot._parse_result", lambda _response: Table())
    monkeypatch.setattr("streamlit.warning", lambda _message: None)

    result = filter_skybot_candidates(candidates, "2026-01-01T00:00:00")

    assert result is candidates
    assert request_params["-mime"] == "votable"
    assert request_params["-observer"] == "500"


def test_filter_skybot_candidates_does_not_treat_parse_error_as_empty(monkeypatch):
    """An upstream error response should not be reported as an empty field."""
    candidates = _make_candidates_table()

    monkeypatch.setattr(
        "src.transient.stdpipe_astrometry.get_objects_center",
        lambda *_args, **_kwargs: (10.0, 20.0, 0.01),
    )

    class ErrorResponse:
        status_code = 200
        text = "# Flag: -1 # Ticket: 123456789012345678 computeEphemeris failed"

    monkeypatch.setattr("requests.get", lambda *_args, **_kwargs: ErrorResponse())

    def _raise_parse_error(_response):
        raise ExpatError("not well-formed")

    monkeypatch.setattr("src.transient.Skybot._parse_result", _raise_parse_error)
    warnings = []
    monkeypatch.setattr("streamlit.warning", warnings.append)

    result = filter_skybot_candidates(candidates, "2026-01-01T00:00:00")

    assert result is candidates
    assert any("unreadable response" in message for message in warnings)
    assert not any("No Solar System objects found" in message for message in warnings)


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


def _make_gaia_catalog_table(*, vizier_columns):
    if vizier_columns:
        return Table(
            {
                "Source": [123456789],
                "DR3Name": ["Gaia DR3 123456789"],
                "RA_ICRS": [150.0],
                "DE_ICRS": [2.0],
                "Gmag": [12.0],
                "BPmag": [12.5],
                "RPmag": [11.5],
                "BP-RP": [1.0],
                "VarFlag": ["CONSTANT"],
                "_r": [0.2],
            }
        )
    return Table(
        {
            "source_id": [123456789],
            "designation": ["Gaia DR3 123456789"],
            "ra": [150.0],
            "dec": [2.0],
            "phot_variable_flag": ["CONSTANT"],
            "bp_rp": [1.0],
            "phot_g_mean_mag": [12.0],
            "phot_bp_mean_mag": [12.5],
            "phot_rp_mean_mag": [11.5],
        }
    )


def _run_gaia_cross_match(monkeypatch):
    phot_table = Table({"xcenter": [50.0], "ycenter": [50.0]})
    science_header = {"RA": 150.0, "DEC": 2.0, "NAXIS1": 100, "NAXIS2": 100}
    return cross_match_with_gaia(
        phot_table,
        science_header,
        pixel_size_arcsec=1.5,
        mean_fwhm_pixel=3.0,
        filter_band="phot_g_mean_mag",
        filter_max_mag=18.0,
        refined_wcs=_make_valid_wcs(),
    )


def test_cross_match_with_gaia_queries_vizier_cone(monkeypatch):
    """The preferred Gaia path should query VizieR's DR3 cone-search catalog."""
    query_args = {}
    catalog_table = _make_gaia_catalog_table(vizier_columns=True)

    class FakeVizier:
        def __init__(self, *, columns, row_limit):
            query_args["columns"] = columns
            query_args["row_limit"] = row_limit

        def query_region(self, center, *, radius, catalog):
            query_args.update(center=center, radius=radius, catalog=catalog)
            return [catalog_table]

    monkeypatch.setattr("src.xmatch_catalogs.Vizier", FakeVizier)

    def _unexpected_tap_query(*_args, **_kwargs):
        raise AssertionError("TAP should not run when VizieR succeeds")

    monkeypatch.setattr("src.xmatch_catalogs.Gaia.launch_job", _unexpected_tap_query)

    matched_table, log_messages = _run_gaia_cross_match(monkeypatch)

    assert matched_table is not None
    assert len(matched_table) == 1
    assert matched_table.loc[0, "catalog_source_id"] == "Gaia DR3 123456789"
    assert query_args["catalog"] == "I/355/gaiadr3"
    assert query_args["row_limit"] == 2000
    assert any("Retrieved 1 Gaia sources from VizieR" in message for message in log_messages)


def test_cross_match_with_gaia_tap_query_omits_distance_ordering(monkeypatch):
    """The direct Gaia fallback should use valid cone-search ADQL."""
    tap_queries = []

    class FakeVizier:
        def __init__(self, **_kwargs):
            pass

        def query_region(self, *_args, **_kwargs):
            raise RuntimeError("VizieR unavailable")

    class FakeJob:
        def get_results(self):
            return _make_gaia_catalog_table(vizier_columns=False)

    def _launch_job(*, query):
        tap_queries.append(query)
        return FakeJob()

    monkeypatch.setattr("src.xmatch_catalogs.Vizier", FakeVizier)
    monkeypatch.setattr("src.xmatch_catalogs.Gaia.launch_job", _launch_job)

    matched_table, log_messages = _run_gaia_cross_match(monkeypatch)

    assert matched_table is not None
    assert len(matched_table) == 1
    assert len(tap_queries) == 1
    assert "CONTAINS" in tap_queries[0]
    assert "ORDER BY DISTANCE" not in tap_queries[0]
    assert any("Retrieved 1 sources from direct GAIA TAP query" in message for message in log_messages)


def test_enhance_catalog_matches_simbad_skybot_milliquas_and_10pc(monkeypatch):
    """Catalog results should match and populate their source rows correctly."""
    catalog_epoch_coord = SkyCoord(
        ra=150.0001 * u.deg,
        dec=2.0001 * u.deg,
        distance=(1000.0 / 150.0) * u.pc,
        pm_ra_cosdec=0.0 * u.mas / u.yr,
        pm_dec=4000.0 * u.mas / u.yr,
        radial_velocity=0.0 * u.km / u.s,
        obstime=Time(2016.0, format="jyear"),
    )
    observed_coord = catalog_epoch_coord.apply_space_motion(
        new_obstime=Time("2025-06-17T23:44:07")
    )
    final_table = pd.DataFrame(
        {"ra": [observed_coord.ra.deg], "dec": [observed_coord.dec.deg]},
        index=[7],
    )
    header = {
        "CRVAL1": 150.0,
        "CRVAL2": 2.0,
        "NAXIS1": 100,
        "NAXIS2": 100,
        "DATE-OBS": "2025-06-17T23:44:07",
    }
    simbad_result = QTable(
        {
            "ra": [observed_coord.ra.deg] * u.deg,
            "dec": [observed_coord.dec.deg] * u.deg,
            "main_id": ["SIMBAD source"],
            "otype": ["Star"],
            "ids": ["Catalog ID"],
            "B": [13.2],
            "V": [12.8],
        }
    )
    skybot_result = Table(
        {
            "RA": [observed_coord.ra.deg],
            "DEC": [observed_coord.dec.deg],
            "Name": ["Asteroid"],
            "Type": ["Asteroid"],
            "V": [18.0],
            "epoch": [2460000.0],
        }
    )
    milliquas_result = Table(
        {
            "RAJ2000": [observed_coord.ra.deg],
            "DEJ2000": [observed_coord.dec.deg],
            "Name": ["QSO source"],
            "z": [1.2],
            "Rmag": [18.3],
        }
    )
    pc10_result = Table(
        {
            "RAICRS": [150.0001],
            "DEICRS": [2.0001],
            "Name": ["Nearby star"],
            "ObjType": ["Star"],
            "Gmag": [10.1],
            "GBPmag": [10.5],
            "GRPmag": [9.7],
            "Epoch": [2016.0],
            "pmRA": [0.0],
            "pmDE": [4000.0],
            "plx": [150.0],
            "RV": [0.0],
        }
    )

    def _simbad_query(_self, *_args, **_kwargs):
        return simbad_result

    def _vizier_query(_self, *_args, catalog=None, **_kwargs):
        if catalog == ["B/vsx/vsx"]:
            return {}
        if catalog == "VII/294":
            return [milliquas_result]
        if catalog == "J/A+A/650/A201/tablea1":
            return [pc10_result]
        raise AssertionError(f"Unexpected VizieR catalog: {catalog}")

    def _vizier_constraints(_self, *, catalog):
        assert catalog == "J/A+A/650/A201/tablea1"
        return [pc10_result]

    class Response:
        status_code = 200
        text = "<VOTABLE/>"

    request_params = {}

    def _skybot_request(_url, *, params, timeout):
        request_params.update(params)
        assert timeout == 120
        return Response()

    class FailedAstroColibriResponse:
        status_code = 503
        content = b""

    monkeypatch.setattr(type(xmatch_catalogs.Simbad), "query_region", _simbad_query)
    monkeypatch.setattr(type(xmatch_catalogs.Vizier), "query_region", _vizier_query)
    monkeypatch.setattr(
        type(xmatch_catalogs.Vizier), "query_constraints", _vizier_constraints
    )
    monkeypatch.setattr("src.xmatch_catalogs.requests.post", lambda *_args, **_kwargs: FailedAstroColibriResponse())
    monkeypatch.setattr("src.xmatch_catalogs.requests.get", _skybot_request)
    monkeypatch.setattr(
        "src.xmatch_catalogs.Skybot._parse_result",
        lambda _response: skybot_result,
    )

    enhanced, log_messages = enhance_catalog(
        None, final_table, None, header, pixel_scale_arcsec=1.0
    )

    assert enhanced.loc[7, "simbad_main_id"] == "SIMBAD source"
    assert enhanced.loc[7, "skybot_NAME"] == "Asteroid"
    assert enhanced.loc[7, "qso_name"] == "QSO source"
    assert enhanced.loc[7, "pc10_name"] == "Nearby star"
    assert request_params["-mime"] == "votable"
    assert request_params["-observer"] == "500"
    assert not any("Error querying VizieR 10 Parsec" in message for message in log_messages)


def test_aladin_popup_includes_skybot_and_10pc_matches(monkeypatch):
    """Aladin popups should retain all catalog IDs from enhancement output."""
    table = pd.DataFrame(
        {
            "ra": [150.0],
            "dec": [2.0],
            "simbad_main_id": ["SIMBAD source"],
            "skybot_NAME": ["Asteroid"],
            "qso_name": ["QSO source"],
            "pc10_name": ["Nearby star"],
        }
    )
    rendered_html = []
    monkeypatch.setattr("src.tools_app.st.spinner", lambda *_args: nullcontext())
    monkeypatch.setattr(
        "src.tools_app.st.iframe",
        lambda html, **_kwargs: rendered_html.append(html),
    )

    display_catalog_in_aladin(table, ra_center=150.0, dec_center=2.0)

    encoded_data = re.search(
        r'JSON.parse\(atob\("([^"]+)"\)\)', rendered_html[0]
    ).group(1)
    sources = json.loads(base64.b64decode(encoded_data))

    assert sources[0]["catalog_matches"] == {
        "SIMBAD": "SIMBAD source",
        "QSO": "QSO source",
        "SkyBoT": "Asteroid",
        "10 Parsec": "Nearby star",
    }