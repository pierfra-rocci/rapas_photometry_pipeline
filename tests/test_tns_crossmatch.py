"""Tests for the TNS (Transient Name Server) cross-match service.

The staged TNS catalogue is downloaded at most once per day, so the tests seed
the daily cache (or a local catalogue file) and never touch the network.
"""

import io
import json
import os
import time
import zipfile
from contextlib import nullcontext

import pandas as pd
import pytest
from astropy.coordinates import SkyCoord

from src.tns_catalog import (
    TNS_MATCH_RADII_ARCSEC,
    cross_match_tns,
    load_tns_public_objects,
    mirror_tns_objects,
    parse_tns_mirror_objects,
    parse_tns_public_objects,
    query_tns_mirror,
    tns_cache_is_fresh,
    tns_cache_path,
    tns_field_mask,
    tns_match_radius,
    tns_spatial_index,
)
from src.utils import build_export_columns

# Column names of the second header line of the staged TNS CSV.
TNS_HEADER = (
    '"objid","name_prefix","name","ra","declination","redshift","typeid","type",'
    '"reporting_groupid","reporting_group","source_groupid","source_group",'
    '"discoverydate","discoverymag","discmagfilter","filter","reporters",'
    '"time_received","internal_names","Discovery_ADS_bibcode",'
    '"Class_ADS_bibcodes","creationdate","lastmodified"'
)


def _tns_row(
    objid,
    prefix,
    name,
    ra,
    dec,
    redshift="",
    objtype="",
    date="2026-01-01 00:00:00",
    mag="",
):
    fields = [
        str(objid),
        prefix,
        name,
        f"{ra:.6f}",
        f"{dec:.6f}",
        str(redshift),
        "0",
        objtype,
        "1",
        "Test Group",
        "1",
        "Test Group",
        date,
        str(mag),
        "r",
        "r",
        "Reporter",
        "2026-01-02 00:00:00",
        "",
        "",
        "",
        "2026-01-01 00:00:00",
        "2026-01-01 00:00:00",
    ]
    return ",".join(fields)


def _tns_text(rows):
    """Build a staged-style CSV: creation date line, header line, data."""
    body = "\n".join(_tns_row(*row) for row in rows)
    return f'"2026-11-03 00:00:00"\n{TNS_HEADER}\n{body}\n'


def _tns_zip(text):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("tns_public_objects.csv", text)
    return buffer.getvalue()


def _seed_cache(tmp_path, text):
    path = tns_cache_path(cache_dir=str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _run_cross_match(tmp_path, table, objects, field_width_arcmin=30.0):
    _seed_cache(tmp_path, _tns_text(objects))
    mask = pd.Series(True, index=table.index)
    return cross_match_tns(
        table,
        mask,
        ra_center=150.0,
        dec_center=2.0,
        field_width_arcmin=field_width_arcmin,
        cache_dir=str(tmp_path),
    )


def _age_cache(path, hours):
    """Backdate a cached catalogue so it is older than the refresh delay."""
    stamp = time.time() - hours * 3600.0
    os.utime(path, (stamp, stamp))


def _clear_tns_env(monkeypatch):
    """Remove every TNS environment variable so a test never uses the network."""
    for name in (
        "TNS_API_KEY",
        "TNS_BOT_ID",
        "TNS_BOT_NAME",
        "TNS_USER_ID",
        "TNS_USER_NAME",
        "RPP_TNS_MARKER_NAME",
        "RPP_TNS_CATALOG_PATH",
        "RPP_TNS_MIRROR_URL",
        "RPP_TNS_CACHE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def _no_post_request(*_args, **_kwargs):
    raise AssertionError("the public TNS catalogue is downloaded with GET only")


def _no_request(*_args, **_kwargs):
    raise AssertionError("no TNS request is expected here")


class _OfflineResponse:
    """Staging server unreachable stand-in."""

    status_code = 503
    content = b""


# ─── Progressive radii ───────────────────────────────────────────────────────


def test_tns_match_radius_tiers():
    assert TNS_MATCH_RADII_ARCSEC[-1] == 30.0
    assert tns_match_radius(0.5) == 5.0
    assert tns_match_radius(7.0) == 10.0
    assert tns_match_radius(14.9) == 15.0
    assert tns_match_radius(19.9) == 20.0
    assert tns_match_radius(25.0) == 30.0
    assert tns_match_radius(29.9) == 30.0
    assert tns_match_radius(30.0) is None


# ─── Catalogue parsing and loading ───────────────────────────────────────────


def test_parse_tns_public_objects_handles_staging_date_line():
    text = _tns_text(
        [(1, "SN", "2026abc", 150.0, 2.0, 0.05, "SN Ia", "2026-01-01", 17.2)]
    )
    frame = parse_tns_public_objects(text)
    assert len(frame) == 1
    assert frame.loc[0, "name_prefix"] == "SN"
    assert frame.loc[0, "name"] == "2026abc"
    assert frame.loc[0, "ra"] == pytest.approx(150.0)
    assert frame.loc[0, "dec"] == pytest.approx(2.0)
    assert frame.loc[0, "type"] == "SN Ia"


def test_parse_tns_public_objects_accepts_plain_header_and_drops_bad_rows():
    text = (
        TNS_HEADER
        + "\n"
        + _tns_row(1, "AT", "2026aaa", 150.0, 2.0)
        + "\n"
        + _tns_row(2, "AT", "bad", 999.0, 999.0)
        + "\n"
    )
    frame = parse_tns_public_objects(text)
    # 999 deg is numeric, so both rows parse; coordinates stay finite only
    assert len(frame) == 2
    assert list(frame["objid"]) == ["1", "2"]


def test_load_tns_public_objects_uses_daily_cache(monkeypatch, tmp_path):
    _seed_cache(tmp_path, _tns_text([(1, "SN", "2026abc", 150.0, 2.0)]))

    def _fail(*_args, **_kwargs):
        raise AssertionError("the cached catalogue must not be downloaded again")

    monkeypatch.setattr("src.tns_catalog.requests.get", _fail)

    frame, messages = load_tns_public_objects(cache_dir=str(tmp_path))

    assert frame is not None and len(frame) == 1
    assert any("cached public catalogue" in message for message in messages)


def test_load_tns_public_objects_downloads_without_credentials(monkeypatch, tmp_path):
    _clear_tns_env(monkeypatch)
    calls = []
    payload = _tns_zip(_tns_text([(1, "SN", "2026abc", 150.0, 2.0)]))

    class Response:
        status_code = 200
        content = payload

    def _get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers})
        return Response()

    monkeypatch.setattr("src.tns_catalog.requests.get", _get)
    monkeypatch.setattr("src.tns_catalog.requests.post", _no_post_request)

    frame, messages = load_tns_public_objects(cache_dir=str(tmp_path))

    assert frame is not None and len(frame) == 1
    assert len(calls) == 1
    assert calls[0]["url"] == (
        "https://www.wis-tns.org/sites/default/files/tns_public_objects/"
        "tns_public_objects.csv.zip"
    )
    assert "api_key" not in calls[0]["headers"]
    marker = calls[0]["headers"]["User-Agent"]
    assert marker.startswith("tns_marker{")
    assert '"tns_id":0' in marker and '"type":"guest"' in marker


def test_load_tns_public_objects_sends_the_configured_marker_name(monkeypatch, tmp_path):
    _clear_tns_env(monkeypatch)
    monkeypatch.setenv("RPP_TNS_MARKER_NAME", "My observatory")

    class Response:
        status_code = 200
        content = _tns_zip(_tns_text([(1, "SN", "2026abc", 150.0, 2.0)]))

    captured = []

    def _get(url, headers=None, timeout=None):
        captured.append(headers)
        return Response()

    monkeypatch.setattr("src.tns_catalog.requests.get", _get)

    frame, _messages = load_tns_public_objects(cache_dir=str(tmp_path))

    assert frame is not None
    assert '"name":"My observatory"' in captured[0]["User-Agent"]


def test_load_tns_public_objects_downloads_once_and_caches(monkeypatch, tmp_path):
    _clear_tns_env(monkeypatch)
    calls = []
    payload = _tns_zip(_tns_text([(1, "SN", "2026abc", 150.0, 2.0)]))

    class Response:
        status_code = 200
        content = payload

    def _get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers})
        return Response()

    monkeypatch.setattr("src.tns_catalog.requests.get", _get)

    frame, messages = load_tns_public_objects(cache_dir=str(tmp_path))

    assert frame is not None and len(frame) == 1
    assert len(calls) == 1
    assert "tns_marker" in calls[0]["headers"]["User-Agent"]
    assert os.path.exists(tns_cache_path(cache_dir=str(tmp_path)))
    assert any("downloaded the public catalogue" in m for m in messages)

    # A second run in the same day reads the cache instead of the network.
    frame_again, messages_again = load_tns_public_objects(cache_dir=str(tmp_path))
    assert len(calls) == 1
    assert len(frame_again) == 1
    assert any("cached public catalogue" in m for m in messages_again)


def test_load_tns_public_objects_refreshes_a_cache_older_than_24_hours(
    monkeypatch, tmp_path
):
    _clear_tns_env(monkeypatch)
    path = _seed_cache(tmp_path, _tns_text([(1, "SN", "2026old", 150.0, 2.0)]))
    _age_cache(path, 24.5)
    assert not tns_cache_is_fresh(path)

    payload = _tns_zip(
        _tns_text(
            [
                (1, "SN", "2026abc", 150.0, 2.0),
                (2, "AT", "2026def", 10.0, -30.0),
            ]
        )
    )
    calls = []

    class Response:
        status_code = 200
        content = payload

    monkeypatch.setattr(
        "src.tns_catalog.requests.get",
        lambda url, headers=None, timeout=None: calls.append(url) or Response(),
    )

    frame, messages = load_tns_public_objects(cache_dir=str(tmp_path))

    assert len(calls) == 1
    assert len(frame) == 2
    assert any("downloaded the public catalogue (2 objects)" in m for m in messages)
    # The same file is overwritten, not added next to the old one.
    refreshed, _ = load_tns_public_objects(cache_dir=str(tmp_path))
    assert len(calls) == 1
    assert len(refreshed) == 2
    assert sorted(item.name for item in tmp_path.iterdir()) == [
        "tns_public_objects.csv"
    ]


def test_load_tns_public_objects_keeps_the_old_copy_when_the_download_fails(
    monkeypatch, tmp_path
):
    _clear_tns_env(monkeypatch)
    path = _seed_cache(tmp_path, _tns_text([(1, "SN", "2026old", 150.0, 2.0)]))
    _age_cache(path, 30.0)
    monkeypatch.setattr(
        "src.tns_catalog.requests.get", lambda *_a, **_k: _OfflineResponse()
    )

    frame, messages = load_tns_public_objects(cache_dir=str(tmp_path))

    assert frame is not None and len(frame) == 1
    assert any("could not be refreshed" in m for m in messages)
    assert any("30.0 h old" in m for m in messages)


def test_load_tns_public_objects_reports_a_failed_download(monkeypatch, tmp_path):
    _clear_tns_env(monkeypatch)
    monkeypatch.setattr(
        "src.tns_catalog.requests.get", lambda *_a, **_k: _OfflineResponse()
    )

    frame, messages = load_tns_public_objects(cache_dir=str(tmp_path))

    assert frame is None
    assert any("HTTP 503" in message for message in messages)
    assert any("could not be downloaded" in message for message in messages)


def test_load_tns_public_objects_falls_back_to_the_second_staging_url(
    monkeypatch, tmp_path
):
    _clear_tns_env(monkeypatch)
    urls = []

    class Response:
        status_code = 200
        content = _tns_zip(_tns_text([(1, "SN", "2026abc", 150.0, 2.0)]))

    def _get(url, headers=None, timeout=None):
        urls.append(url)
        if "/sites/default/files/" in url:
            return _OfflineResponse()
        return Response()

    monkeypatch.setattr("src.tns_catalog.requests.get", _get)

    frame, _messages = load_tns_public_objects(cache_dir=str(tmp_path))

    assert frame is not None and len(frame) == 1
    assert urls == [
        "https://www.wis-tns.org/sites/default/files/tns_public_objects/"
        "tns_public_objects.csv.zip",
        "https://www.wis-tns.org/system/files/tns_public_objects/"
        "tns_public_objects.csv.zip",
    ]


def test_load_tns_public_objects_reports_http_errors(monkeypatch, tmp_path):
    _clear_tns_env(monkeypatch)
    monkeypatch.setattr(
        "src.tns_catalog.requests.get", lambda *_a, **_k: _OfflineResponse()
    )

    frame, messages = load_tns_public_objects(cache_dir=str(tmp_path))

    assert frame is None
    assert any("HTTP 503" in message for message in messages)


def test_load_tns_public_objects_cleans_a_stale_lock_and_temp_file(
    monkeypatch, tmp_path
):
    import os

    _clear_tns_env(monkeypatch)
    cache = tns_cache_path(cache_dir=str(tmp_path))
    with open(cache, "w", encoding="utf-8") as handle:
        handle.write(_tns_text([(1, "SN", "2026abc", 150.0, 2.0)]))

    lock = tmp_path / "tns_public_objects.download.lock"
    temp = tmp_path / f"tns_public_objects.csv.{os.getpid() + 1}.tmp"
    lock.write_text("", encoding="utf-8")
    temp.write_text("half a catalogue", encoding="utf-8")
    old = time.time() - 2 * 3600
    os.utime(lock, (old, old))
    os.utime(temp, (old, old))

    monkeypatch.setattr("src.tns_catalog.requests.get", _no_request)
    frame, messages = load_tns_public_objects(cache_dir=str(tmp_path))

    assert len(frame) == 1
    assert any("using the shared cached public catalogue" in m for m in messages)
    assert not lock.exists()
    assert not temp.exists()

    cleanup_lines = [m for m in messages if "interrupted download" in m]
    assert len(cleanup_lines) == 1
    assert "tns_public_objects.download.lock" in cleanup_lines[0]
    assert temp.name in cleanup_lines[0]
    assert cleanup_lines[0].startswith("INFO")


def test_load_tns_public_objects_keeps_a_fresh_temp_file(monkeypatch, tmp_path):
    import os

    _clear_tns_env(monkeypatch)
    cache = tns_cache_path(cache_dir=str(tmp_path))
    with open(cache, "w", encoding="utf-8") as handle:
        handle.write(_tns_text([(1, "SN", "2026abc", 150.0, 2.0)]))

    lock = tmp_path / "tns_public_objects.download.lock"
    temp = tmp_path / f"tns_public_objects.csv.{os.getpid() + 1}.tmp"
    lock.write_text("", encoding="utf-8")
    temp.write_text("being written", encoding="utf-8")

    monkeypatch.setattr("src.tns_catalog.requests.get", _no_request)
    _frame, messages = load_tns_public_objects(cache_dir=str(tmp_path))

    # A download may own them right now: never delete the live ones.
    assert lock.exists()
    assert temp.exists()
    assert not any("interrupted download" in m for m in messages)


def test_load_tns_public_objects_uses_local_catalogue(monkeypatch, tmp_path):
    path = tmp_path / "tns_local.csv"
    path.write_text(_tns_text([(1, "SN", "2026abc", 150.0, 2.0)]), encoding="utf-8")
    monkeypatch.setenv("RPP_TNS_CATALOG_PATH", str(path))
    monkeypatch.setattr("src.tns_catalog.requests.get", _no_request)
    monkeypatch.setattr("src.tns_catalog.requests.post", _no_request)

    frame, messages = load_tns_public_objects(cache_dir=str(tmp_path / "cache"))

    assert frame is not None and len(frame) == 1
    assert any("loaded 1 public objects" in message for message in messages)


# ─── Cross-match ─────────────────────────────────────────────────────────────


def test_cross_match_tns_matches_the_closest_source(tmp_path):
    table = pd.DataFrame(
        {
            "ra": [150.0, 150.0 + 20.0 / 3600.0],
            "dec": [2.0 + 10.0 / 3600.0, 2.0],
        }
    )
    objects = [(1, "SN", "2026abc", 150.0, 2.0, 0.05, "SN Ia", "2026-01-01", 17.2)]

    enhanced, log_messages = _run_cross_match(tmp_path, table, objects)

    assert enhanced.loc[0, "tns_name"] == "SN 2026abc"
    assert enhanced.loc[0, "tns_type"] == "SN Ia"
    assert enhanced.loc[0, "tns_ra"] == pytest.approx(150.0)
    assert enhanced.loc[0, "tns_dec"] == pytest.approx(2.0)
    assert enhanced.loc[0, "tns_separation_arcsec"] == pytest.approx(10.0, abs=0.1)
    assert pd.isna(enhanced.loc[1, "tns_name"])
    assert enhanced.loc[0, "catalog_matches"] == "TNS; "
    line = next(
        m for m in log_messages if m.startswith("INFO: TNS:") and "matched within" in m
    )
    assert "1 source(s) matched within 30 arcsec" in line


def test_cross_match_tns_keeps_the_closest_object_per_source(tmp_path):
    table = pd.DataFrame({"ra": [150.0], "dec": [2.0]})
    objects = [
        (1, "AT", "2026far", 150.0, 2.0 + 25.0 / 3600.0, "", "AT", "2026-01-02", 18.0),
        (
            2,
            "SN",
            "2026near",
            150.0,
            2.0 + 8.0 / 3600.0,
            "",
            "SN Ia",
            "2026-01-03",
            17.0,
        ),
    ]

    enhanced, _ = _run_cross_match(tmp_path, table, objects)

    assert enhanced.loc[0, "tns_name"] == "SN 2026near"
    assert enhanced.loc[0, "tns_separation_arcsec"] == pytest.approx(8.0, abs=0.1)

    objects_view = enhanced.attrs["tns_objects"]
    by_name = objects_view.set_index("designation")
    assert bool(by_name.loc["SN 2026near", "matched"]) is True
    assert bool(by_name.loc["AT 2026far", "matched"]) is False
    assert pd.isna(by_name.loc["AT 2026far", "separation_arcsec"])


def test_cross_match_tns_warns_for_loose_tiers(tmp_path):
    table = pd.DataFrame({"ra": [150.0], "dec": [2.0]})

    _, log_messages = _run_cross_match(
        tmp_path,
        table,
        [(1, "AT", "2026loose", 150.0, 2.0 + 25.0 / 3600.0)],
    )
    warnings = [m for m in log_messages if m.startswith("WARNING: TNS:")]
    assert len(warnings) == 1 and "30 arcsec tier" in warnings[0]

    _, log_messages = _run_cross_match(
        tmp_path,
        table,
        [(1, "AT", "2026edge", 150.0, 2.0 + 17.0 / 3600.0)],
    )
    warnings = [m for m in log_messages if m.startswith("WARNING: TNS:")]
    assert len(warnings) == 1 and "20 arcsec tier" in warnings[0]

    _, log_messages = _run_cross_match(
        tmp_path,
        table,
        [(1, "AT", "2026tight", 150.0, 2.0 + 12.0 / 3600.0)],
    )
    assert not [m for m in log_messages if m.startswith("WARNING: TNS:")]


def test_cross_match_tns_ignores_objects_beyond_30_arcsec(tmp_path):
    table = pd.DataFrame({"ra": [150.0], "dec": [2.0]})

    enhanced, log_messages = _run_cross_match(
        tmp_path,
        table,
        [(1, "AT", "2026far", 150.0, 2.0 + 35.0 / 3600.0)],
    )

    assert pd.isna(enhanced.loc[0, "tns_name"])
    assert any("no source matched within 30 arcsec" in m for m in log_messages)


def test_cross_match_tns_discards_objects_outside_the_field(tmp_path):
    table = pd.DataFrame({"ra": [150.0], "dec": [2.0]})

    enhanced, log_messages = _run_cross_match(
        tmp_path,
        table,
        [(1, "AT", "2026away", 151.0, 2.0)],
        field_width_arcmin=1.0,
    )

    assert "tns_name" not in enhanced.columns
    assert any("0 public transient(s)" in m for m in log_messages)


def test_cross_match_tns_without_catalogue_leaves_table_untouched(
    monkeypatch, tmp_path
):
    _clear_tns_env(monkeypatch)
    monkeypatch.setattr(
        "src.tns_catalog.requests.get", lambda *_a, **_k: _OfflineResponse()
    )
    table = pd.DataFrame({"ra": [150.0], "dec": [2.0]})

    enhanced, log_messages = cross_match_tns(
        table,
        pd.Series(True, index=table.index),
        ra_center=150.0,
        dec_center=2.0,
        cache_dir=str(tmp_path),
    )

    assert "tns_name" not in enhanced.columns
    assert any("could not be downloaded" in m for m in log_messages)


# ─── Pipeline integration ────────────────────────────────────────────────────


def test_enhance_catalog_adds_tns_columns(monkeypatch, tmp_path):
    from src import xmatch_catalogs

    catalog_path = tmp_path / "tns.csv"
    catalog_path.write_text(
        _tns_text([(1, "SN", "2026abc", 150.0, 2.0 + 5.0 / 3600.0)]),
        encoding="utf-8",
    )
    monkeypatch.setenv("RPP_TNS_CATALOG_PATH", str(catalog_path))

    final_table = pd.DataFrame({"ra": [150.0], "dec": [2.0]})
    header = {
        "CRVAL1": 150.0,
        "CRVAL2": 2.0,
        "NAXIS1": 100,
        "NAXIS2": 100,
        "DATE-OBS": "2025-06-17T23:44:07",
    }

    class FakeSimbad:
        def add_votable_fields(self, *_args):
            return None

        def query_region(self, *_args, **_kwargs):
            return None

    class FakeVizier:
        ROW_LIMIT = -1

        def __init__(self, *args, **kwargs):
            pass

        def query_region(self, *_args, **_kwargs):
            return []

        def query_constraints(self, *_args, **_kwargs):
            return []

    class AstroResponse:
        status_code = 503
        content = b""

    monkeypatch.setattr("src.xmatch_catalogs.Simbad", FakeSimbad)
    monkeypatch.setattr("src.xmatch_catalogs.Vizier", FakeVizier)
    monkeypatch.setattr(
        "src.xmatch_catalogs.skybot_cone_search", lambda *_a, **_k: (None, "skipped")
    )
    monkeypatch.setattr(
        "src.xmatch_catalogs.requests.post", lambda *_a, **_k: AstroResponse()
    )

    enhanced, log_messages = xmatch_catalogs.enhance_catalog(
        None, final_table, None, header, pixel_scale_arcsec=1.0
    )

    assert enhanced.loc[0, "tns_name"] == "SN 2026abc"
    assert enhanced.loc[0, "tns_type"] is not None or pd.isna(
        enhanced.loc[0, "tns_type"]
    )
    assert enhanced.loc[0, "catalog_matches"] == "TNS; "
    assert any(
        m.startswith("INFO: TNS:") and "matched within" in m for m in log_messages
    )
    assert "tns_objects" in enhanced.attrs


# ─── Exports and UI ──────────────────────────────────────────────────────────


def test_export_columns_include_tns_after_astrocolibri():
    columns = [
        "id",
        "ra",
        "dec",
        "astrocolibri_name",
        "tns_name",
        "tns_type",
        "tns_ra",
        "tns_dec",
        "tns_separation_arcsec",
        "simbad_main_id",
    ]

    ordered = build_export_columns(columns, None)

    assert ordered == [
        "id",
        "ra",
        "dec",
        "astrocolibri_name",
        "tns_name",
        "tns_type",
        "tns_ra",
        "tns_dec",
        "simbad_main_id",
    ]
    # Like astrocolibri_separation_arcsec, the separation stays in the in-app
    # table (it is what the loose-match warning is based on).
    assert "tns_separation_arcsec" not in ordered


def test_aladin_popup_includes_tns_match(monkeypatch):
    import base64
    import json
    import re

    from src.tools_app import display_catalog_in_aladin

    table = pd.DataFrame(
        {
            "ra": [150.0],
            "dec": [2.0],
            "tns_name": ["SN 2026abc"],
            "tns_type": ["SN Ia"],
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

    assert sources[0]["catalog_matches"] == {"TNS": "SN 2026abc"}


def test_format_tns_objects_orders_matched_first():
    from src.tools_app import format_tns_objects

    objects = pd.DataFrame(
        {
            "designation": ["B", "A"],
            "ra": [150.1, 150.0],
            "dec": [2.1, 2.0],
            "type": ["AT", "SN Ia"],
            "redshift": [None, 0.05],
            "discoverydate": ["2026-01-02", "2026-01-01"],
            "discoverymag": [18.0, 17.0],
            "matched": [False, True],
            "separation_arcsec": [float("nan"), 3.5],
        }
    )

    view = format_tns_objects(objects)

    assert list(view["Name"]) == ["A", "B"]
    assert list(view["Matched"]) == ["Yes", "No"]
    assert format_tns_objects(None).empty
    assert format_tns_objects(pd.DataFrame()).empty


def test_show_tns_status_reports_matches(monkeypatch):
    from src import tools_app

    messages = []
    monkeypatch.setattr(
        "src.tools_app.st.success",
        lambda text, **_kwargs: messages.append(("success", text)),
    )
    monkeypatch.setattr(
        "src.tools_app.st.info", lambda text, **_kwargs: messages.append(("info", text))
    )
    monkeypatch.setattr("src.tools_app.st.expander", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr("src.tools_app.st.dataframe", lambda *_a, **_k: None)

    table = pd.DataFrame({"ra": [150.0], "dec": [2.0], "tns_name": ["SN 2026abc"]})
    objects = pd.DataFrame(
        {
            "designation": ["SN 2026abc"],
            "matched": [True],
            "separation_arcsec": [3.0],
        }
    )

    tools_app.show_tns_status(
        ["INFO: TNS: 1 source(s) matched within 30 arcsec."], table, objects
    )

    assert messages and messages[0][0] == "success"
    assert "SN 2026abc" in messages[0][1]


# ─── Shared mirror ───────────────────────────────────────────────────────────


class _MirrorResponse:
    """Minimal stand-in for a ``requests`` response carrying JSON."""

    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


def test_parse_tns_mirror_objects_accepts_common_payloads():
    single = {
        "name": "2026abc",
        "name_prefix": "SN",
        "ra": 150.0,
        "dec": 2.0,
        "type": "SN Ia",
        "redshift": 0.05,
    }

    parsed = parse_tns_mirror_objects(single)
    assert parsed[0]["designation"] == "SN 2026abc"
    assert parsed[0]["type"] == "SN Ia"
    assert parsed[0]["redshift"] == "0.05"

    assert (
        parse_tns_mirror_objects({"object": single})[0]["designation"] == "SN 2026abc"
    )
    assert len(parse_tns_mirror_objects({"objects": [single, single]})) == 2
    assert parse_tns_mirror_objects(None) == []
    assert parse_tns_mirror_objects([]) == []

    # A full designation is not prefixed twice.
    assert (
        parse_tns_mirror_objects(
            {"name": "SN 2026abc", "name_prefix": "SN", "ra": 1, "dec": 2}
        )[0]["designation"]
        == "SN 2026abc"
    )
    # Staged TNS column names and rows without usable coordinates.
    staged = parse_tns_mirror_objects(
        {"objname": "AT 2026xyz", "right_ascension": 150, "declination": 2}
    )
    assert staged[0]["designation"] == "AT 2026xyz"
    assert parse_tns_mirror_objects({"name": "2026abc"}) == []


def test_query_tns_mirror_handles_http_status(monkeypatch):
    _clear_tns_env(monkeypatch)
    assert query_tns_mirror(150.0, 2.0) == (
        None,
        "no mirror URL configured",
    )

    monkeypatch.setattr(
        "src.tns_catalog.requests.get",
        lambda *_a, **_k: _MirrorResponse(None, status_code=404),
    )
    assert query_tns_mirror(150.0, 2.0, url="http://mirror.local") == ([], "")

    monkeypatch.setattr(
        "src.tns_catalog.requests.get",
        lambda *_a, **_k: _MirrorResponse(None, status_code=503),
    )
    objects, reason = query_tns_mirror(150.0, 2.0, url="http://mirror.local")
    assert objects is None and "HTTP 503" in reason


def test_mirror_tns_objects_skips_large_fields(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.tns_catalog.requests.get",
        lambda *_a, **_k: calls.append(1) or _MirrorResponse(None),
    )

    frame, messages = mirror_tns_objects(
        [150.0, 150.1, 150.2], [2.0, 2.1, 2.2], url="http://mirror", max_sources=2
    )

    assert frame is None
    assert calls == []
    assert "exceed the shared-mirror limit" in messages[0]


def test_cross_match_tns_uses_shared_mirror_without_download(monkeypatch, tmp_path):
    _clear_tns_env(monkeypatch)
    monkeypatch.setenv("RPP_TNS_MIRROR_URL", "http://mirror.local/api/")
    calls = []

    def _get(url, params=None, timeout=None):
        calls.append({"url": url, "params": dict(params or {})})
        return _MirrorResponse(
            {
                "name": "SN 2026abc",
                "type": "SN Ia",
                "redshift": 0.05,
                "ra": params["ra"],
                "dec": params["dec"] + 4.0 / 3600.0,
                "separation_arcsec": 4.0,
            }
        )

    monkeypatch.setattr("src.tns_catalog.requests.get", _get)
    monkeypatch.setattr("src.tns_catalog.requests.post", _no_post_request)

    table = pd.DataFrame({"ra": [150.0], "dec": [2.0]})
    enhanced, log_messages = cross_match_tns(
        table,
        pd.Series(True, index=table.index),
        ra_center=150.0,
        dec_center=2.0,
        cache_dir=str(tmp_path),
    )

    assert enhanced.loc[0, "tns_name"] == "SN 2026abc"
    assert enhanced.loc[0, "tns_type"] == "SN Ia"
    assert enhanced.loc[0, "tns_ra"] == pytest.approx(150.0)
    assert enhanced.loc[0, "tns_separation_arcsec"] == pytest.approx(4.0, abs=0.1)
    assert enhanced.loc[0, "catalog_matches"] == "TNS; "
    assert calls == [
        {
            "url": "http://mirror.local/api/v1/nearest",
            "params": {
                "ra": 150.0,
                "dec": 2.0,
                "radius_arcsec": 30.0,
            },
        }
    ]
    assert any("shared mirror at http://mirror.local/api/" in m for m in log_messages)
    assert not list(tmp_path.glob("tns_public_objects_*.csv"))


def test_cross_match_tns_mirror_keeps_the_closest_source(monkeypatch, tmp_path):
    _clear_tns_env(monkeypatch)
    monkeypatch.setenv("RPP_TNS_MIRROR_URL", "http://mirror.local")

    # Both sources are answered with the same object; the closest one keeps it.
    monkeypatch.setattr(
        "src.tns_catalog.requests.get",
        lambda *_a, **_k: _MirrorResponse(
            {"name": "AT 2026xyz", "type": "AT", "ra": 150.0, "dec": 2.0}
        ),
    )
    monkeypatch.setattr("src.tns_catalog.requests.post", _no_post_request)

    table = pd.DataFrame(
        {"ra": [150.0 + 20.0 / 3600.0, 150.0 + 6.0 / 3600.0], "dec": [2.0, 2.0]}
    )
    enhanced, log_messages = cross_match_tns(
        table,
        pd.Series(True, index=table.index),
        ra_center=150.0,
        dec_center=2.0,
        cache_dir=str(tmp_path),
    )

    assert pd.isna(enhanced.loc[0, "tns_name"])
    assert enhanced.loc[1, "tns_name"] == "AT 2026xyz"
    assert enhanced.loc[1, "tns_separation_arcsec"] == pytest.approx(6.0, abs=0.1)
    assert any("1 object(s) within 30 arcsec" in m for m in log_messages)


def test_cross_match_tns_mirror_without_objects_skips_the_download(
    monkeypatch, tmp_path
):
    _clear_tns_env(monkeypatch)
    monkeypatch.setenv("RPP_TNS_MIRROR_URL", "http://mirror.local")
    monkeypatch.setattr(
        "src.tns_catalog.requests.get", lambda *_a, **_k: _MirrorResponse(None)
    )
    monkeypatch.setattr("src.tns_catalog.requests.post", _no_post_request)

    table = pd.DataFrame({"ra": [150.0], "dec": [2.0]})
    enhanced, log_messages = cross_match_tns(
        table,
        pd.Series(True, index=table.index),
        ra_center=150.0,
        dec_center=2.0,
        cache_dir=str(tmp_path),
    )

    assert "tns_name" not in enhanced.columns
    assert any("no object within 30 arcsec" in m for m in log_messages)
    assert any("no source matched within 30 arcsec" in m for m in log_messages)


def test_cross_match_tns_falls_back_when_mirror_fails(monkeypatch, tmp_path):
    _clear_tns_env(monkeypatch)
    monkeypatch.setenv("RPP_TNS_MIRROR_URL", "http://mirror.local")
    _seed_cache(tmp_path, _tns_text([(1, "SN", "2026abc", 150.0, 2.0 + 5.0 / 3600.0)]))

    def _get(*_args, **_kwargs):
        raise ConnectionError("mirror down")

    monkeypatch.setattr("src.tns_catalog.requests.get", _get)

    table = pd.DataFrame({"ra": [150.0], "dec": [2.0]})
    enhanced, log_messages = cross_match_tns(
        table,
        pd.Series(True, index=table.index),
        ra_center=150.0,
        dec_center=2.0,
        cache_dir=str(tmp_path),
    )

    assert enhanced.loc[0, "tns_name"] == "SN 2026abc"
    assert any(m.startswith("WARNING: TNS: shared mirror") for m in log_messages)
    assert any("using the daily catalogue instead" in m for m in log_messages)
    assert any("cached public catalogue" in m for m in log_messages)


def test_cross_match_tns_ignores_the_mirror_when_no_url_is_configured(
    monkeypatch, tmp_path
):
    _clear_tns_env(monkeypatch)
    _seed_cache(tmp_path, _tns_text([(1, "SN", "2026abc", 150.0, 2.0)]))
    monkeypatch.setattr(
        "src.tns_catalog.requests.get",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no mirror query")),
    )

    table = pd.DataFrame({"ra": [150.0], "dec": [2.0]})
    enhanced, _ = cross_match_tns(
        table,
        pd.Series(True, index=table.index),
        ra_center=150.0,
        dec_center=2.0,
        cache_dir=str(tmp_path),
    )

    assert enhanced.loc[0, "tns_name"] == "SN 2026abc"


# ─── Spatial index ───────────────────────────────────────────────────────────


def test_parse_tns_public_objects_attaches_a_spatial_index():
    frame = parse_tns_public_objects(
        _tns_text(
            [
                (1, "SN", "2026abc", 150.0, 2.0),
                (2, "AT", "2026def", 10.0, -30.0),
            ]
        )
    )

    tree = tns_spatial_index(frame)

    assert tree is not None
    assert tree.n == 2


def test_cached_catalogue_keeps_its_spatial_index(tmp_path):
    _seed_cache(tmp_path, _tns_text([(1, "SN", "2026abc", 150.0, 2.0)]))

    first, _ = load_tns_public_objects(cache_dir=str(tmp_path))
    second, _ = load_tns_public_objects(cache_dir=str(tmp_path))

    assert first is second
    assert tns_spatial_index(second) is not None


def test_tns_field_mask_agrees_with_the_angular_separation():
    frame = parse_tns_public_objects(
        _tns_text(
            [
                (1, "AT", "seamwest", 359.99, 0.0),
                (2, "AT", "seameast", 0.01, -0.2),
                (3, "AT", "far", 180.0, 0.0),
                (4, "AT", "pole", 123.0, 89.99),
                (5, "AT", "mid", 150.0, 2.0),
            ]
        )
    )
    coords = SkyCoord(frame["ra"].to_numpy(), frame["dec"].to_numpy(), unit="deg")

    for ra, dec, radius in (
        (0.0, 0.0, 0.05),
        (0.0, 0.0, 0.5),
        (150.0, 2.0, 0.01),
        (123.0, 89.99, 0.02),
        (180.0, 0.0, 0.1),
    ):
        center = SkyCoord(ra=ra, dec=dec, unit="deg")
        expected = list(center.separation(coords).deg <= radius)
        mask = list(tns_field_mask(frame, center, radius))
        assert mask == expected, (ra, dec, radius)


def test_tns_field_mask_without_an_index_falls_back_to_separations():
    frame = pd.DataFrame({"ra": [150.0, 150.5], "dec": [2.0, 2.5]})
    center = SkyCoord(ra=150.0, dec=2.0, unit="deg")

    assert tns_spatial_index(frame) is None
    assert list(tns_field_mask(frame, center, 0.1)) == [True, False]


# ─── Search API fallback ─────────────────────────────────────────────────────


class _ApiResponse(_MirrorResponse):
    def __init__(self, reply=None, status_code=200, headers=None, content=b""):
        super().__init__({"id_code": status_code, "data": {"reply": reply}}, status_code)
        self.headers = headers or {}
        self.content = content


def _set_tns_credentials(monkeypatch):
    _clear_tns_env(monkeypatch)
    monkeypatch.setenv("TNS_API_KEY", "secret")
    monkeypatch.setenv("TNS_BOT_ID", "42")
    monkeypatch.setenv("TNS_BOT_NAME", "rpp")


def _fake_tns_post(calls, objects):
    """``requests.post`` stand-in: Search API and Get API."""

    def _post(url, headers=None, data=None, timeout=None):
        calls.append({"url": url, "headers": headers, "data": data})
        payload = json.loads(data["data"])
        if url.endswith("/api/get/search"):
            reply = [
                {"objname": obj["objname"], "prefix": obj["prefix"], "objid": 1}
                for obj in objects
            ]
            return _ApiResponse(reply if payload["page"] == 0 else [])
        for obj in objects:
            if obj["objname"] == payload["objname"]:
                return _ApiResponse(
                    {
                        "objname": obj["objname"],
                        "name_prefix": obj["prefix"],
                        "radeg": obj["ra"],
                        "decdeg": obj["dec"],
                        "object_type": {"name": obj["type"], "id": 3},
                        "redshift": 0.05,
                        "discoverydate": "2026-01-01 00:00:00",
                        "discoverymag": 19.5,
                    }
                )
        return _ApiResponse(None, status_code=404)

    return _post


def test_cross_match_tns_falls_back_to_the_search_api(monkeypatch, tmp_path):
    _set_tns_credentials(monkeypatch)
    calls = []
    objects = [
        {"objname": "2026abc", "prefix": "SN", "ra": 150.0,
         "dec": 2.0 + 5.0 / 3600.0, "type": "SN Ia"},
        {"objname": "2026far", "prefix": "AT", "ra": 150.0,
         "dec": 2.0 + 600.0 / 3600.0, "type": ""},
    ]
    monkeypatch.setattr(
        "src.tns_catalog.requests.get", lambda *_a, **_k: _OfflineResponse()
    )
    monkeypatch.setattr("src.tns_catalog.requests.post", _fake_tns_post(calls, objects))

    table = pd.DataFrame({"ra": [150.0], "dec": [2.0]})
    enhanced, log_messages = cross_match_tns(
        table,
        pd.Series(True, index=table.index),
        ra_center=150.0,
        dec_center=2.0,
        field_width_arcmin=30.0,
        cache_dir=str(tmp_path),
    )

    assert enhanced.loc[0, "tns_name"] == "SN 2026abc"
    assert enhanced.loc[0, "tns_type"] == "SN Ia"
    assert enhanced.loc[0, "tns_separation_arcsec"] == pytest.approx(5.0, abs=0.01)
    assert len(enhanced.attrs["tns_objects"]) == 2
    assert any("querying the Search API" in m for m in log_messages)
    assert any("Search API: 2 object(s)" in m for m in log_messages)

    search_call = next(c for c in calls if c["url"].endswith("/api/get/search"))
    assert search_call["url"].startswith("https://www.wis-tns.org/")
    assert "tns_marker" in search_call["headers"]["User-Agent"]
    search_data = json.loads(search_call["data"]["data"])
    assert search_data["units"] == "arcsec"
    assert float(search_data["radius"]) > 15 * 60


def test_tns_api_uses_the_sandbox_when_requested(monkeypatch):
    from src.tns_catalog import tns_api_base_url

    monkeypatch.delenv("RPP_TNS_SANDBOX", raising=False)
    assert tns_api_base_url() == "https://www.wis-tns.org/api"
    monkeypatch.setenv("RPP_TNS_SANDBOX", "1")
    assert tns_api_base_url() == "https://sandbox.wis-tns.org/api"


def test_search_api_fallback_failure_leaves_table_untouched(monkeypatch, tmp_path):
    _set_tns_credentials(monkeypatch)
    monkeypatch.setattr(
        "src.tns_catalog.requests.get", lambda *_a, **_k: _OfflineResponse()
    )
    monkeypatch.setattr(
        "src.tns_catalog.requests.post", lambda *_a, **_k: _ApiResponse(status_code=403)
    )

    table = pd.DataFrame({"ra": [150.0], "dec": [2.0]})
    enhanced, log_messages = cross_match_tns(
        table,
        pd.Series(True, index=table.index),
        ra_center=150.0,
        dec_center=2.0,
        cache_dir=str(tmp_path),
    )

    assert "tns_name" not in enhanced.columns
    assert any("Search API failed: HTTP 403" in m for m in log_messages)


def test_search_api_is_not_used_with_an_explicit_catalogue_path(monkeypatch, tmp_path):
    _set_tns_credentials(monkeypatch)
    monkeypatch.setenv("RPP_TNS_CATALOG_PATH", str(tmp_path / "missing.csv"))

    monkeypatch.setattr("src.tns_catalog.requests.post", _no_request)
    monkeypatch.setattr("src.tns_catalog.requests.get", _no_request)

    table = pd.DataFrame({"ra": [150.0], "dec": [2.0]})
    enhanced, log_messages = cross_match_tns(
        table,
        pd.Series(True, index=table.index),
        ra_center=150.0,
        dec_center=2.0,
        cache_dir=str(tmp_path),
    )

    assert "tns_name" not in enhanced.columns
    assert any("catalogue file not found" in m for m in log_messages)


def test_search_api_waits_for_the_rate_limit_reset(monkeypatch):
    from src.tns_catalog import search_tns_api, tns_credentials

    _set_tns_credentials(monkeypatch)
    answers = [
        _ApiResponse(status_code=429, headers={"x-rate-limit-reset": "2"}),
        _ApiResponse([{"objname": "2026abc", "prefix": "SN"}]),
    ]
    sleeps = []
    monkeypatch.setattr("src.tns_catalog.requests.post", lambda *_a, **_k: answers.pop(0))
    monkeypatch.setattr("src.tns_catalog.time.sleep", sleeps.append)

    names, _messages = search_tns_api(tns_credentials(), 150.0, 2.0, 600.0)

    assert names == [("SN", "2026abc")]
    assert sleeps == [3]


def test_search_api_gives_up_on_a_long_rate_limit(monkeypatch):
    from src.tns_catalog import search_tns_api, tns_credentials

    _set_tns_credentials(monkeypatch)
    monkeypatch.setattr(
        "src.tns_catalog.requests.post",
        lambda *_a, **_k: _ApiResponse(
            status_code=429, headers={"x-rate-limit-reset": "3600"}
        ),
    )

    names, messages = search_tns_api(tns_credentials(), 150.0, 2.0, 600.0)

    assert names is None
    assert any("rate limit reached" in m for m in messages)


def test_search_api_caps_the_number_of_objects(monkeypatch):
    from src.tns_catalog import search_tns_api, tns_credentials

    _set_tns_credentials(monkeypatch)
    page = [{"objname": f"2026a{i:02d}", "prefix": "AT"} for i in range(50)]
    monkeypatch.setattr(
        "src.tns_catalog.requests.post", lambda *_a, **_k: _ApiResponse(list(page))
    )

    names, messages = search_tns_api(
        tns_credentials(), 150.0, 2.0, 600.0, max_objects=50
    )

    assert len(names) == 50
    assert any("reached the limit of 50 objects" in m for m in messages)


def test_startup_warm_up_downloads_the_catalogue_without_a_key(monkeypatch, tmp_path):
    from src.tools_app import _load_shared_tns_catalogue

    _clear_tns_env(monkeypatch)
    monkeypatch.setenv("RPP_TNS_CACHE_DIR", str(tmp_path))

    class Response:
        status_code = 200
        content = _tns_zip(_tns_text([(1, "SN", "2026abc", 150.0, 2.0)]))

    monkeypatch.setattr("src.tns_catalog.requests.get", lambda *_a, **_k: Response())

    assert _load_shared_tns_catalogue()[0] is True
    assert (tmp_path / "tns_public_objects.csv").exists()

    # The first analysis then finds the catalogue already parsed and cached.
    monkeypatch.setattr("src.tns_catalog.requests.get", _no_request)
    frame, messages = load_tns_public_objects(cache_dir=str(tmp_path))
    assert len(frame) == 1
    assert any("using the shared cached public catalogue" in m for m in messages)


def test_startup_warm_up_is_skipped_when_a_mirror_is_configured(monkeypatch, tmp_path):
    from src.tools_app import _load_shared_tns_catalogue

    _clear_tns_env(monkeypatch)
    monkeypatch.setenv("RPP_TNS_MIRROR_URL", "http://mirror.test")
    monkeypatch.setenv("RPP_TNS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("src.tns_catalog.requests.get", _no_request)

    assert _load_shared_tns_catalogue() == (True, [])
    assert not list(tmp_path.iterdir())


def test_startup_warm_up_reports_a_failed_download(monkeypatch, tmp_path):
    from src.tools_app import _load_shared_tns_catalogue

    _clear_tns_env(monkeypatch)
    monkeypatch.setenv("RPP_TNS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("src.tns_catalog.requests.get", lambda *_a, **_k: _OfflineResponse())

    ok, messages = _load_shared_tns_catalogue()

    assert ok is False
    assert any("could not be downloaded" in m for m in messages)


def test_startup_warm_up_does_not_block_the_page(monkeypatch):
    import threading

    from src import tools_app

    release = threading.Event()
    running = threading.Event()
    calls = []

    def slow_load():
        calls.append(1)
        running.set()
        release.wait(10)
        return True, []

    monkeypatch.setattr(tools_app, "_load_shared_tns_catalogue", slow_load)
    monkeypatch.setattr(tools_app, "_TNS_WARM_UP_THREAD", None)

    thread = tools_app._start_tns_warm_up()

    assert running.wait(10), "the catalogue download never started"
    assert thread.is_alive(), "the caller waited for the download"
    assert calls == [1]
    assert tools_app._start_tns_warm_up() is thread, "a second download was started"

    release.set()
    thread.join(10)
    assert not thread.is_alive()


def _status_line(monkeypatch, tmp_path, age_seconds=None, running=False):
    """Return the TNS status line for a cache of the given age."""
    import os

    from src import tools_app

    _clear_tns_env(monkeypatch)
    monkeypatch.setenv("RPP_TNS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(tools_app, "_TNS_WARM_UP_THREAD", None)
    if age_seconds is not None:
        cache = tns_cache_path(cache_dir=str(tmp_path))
        with open(cache, "w", encoding="utf-8") as handle:
            handle.write("objid\n1\n")
        old = time.time() - age_seconds
        os.utime(cache, (old, old))
    if running:

        class Running:
            def is_alive(self):
                return True

        monkeypatch.setattr(tools_app, "_TNS_WARM_UP_THREAD", Running())
    return tools_app.tns_catalogue_status_line()


def test_tns_status_line_reports_a_missing_catalogue(monkeypatch, tmp_path):
    assert "not downloaded yet" in _status_line(monkeypatch, tmp_path)


def test_tns_status_line_reports_a_background_download(monkeypatch, tmp_path):
    line = _status_line(monkeypatch, tmp_path, running=True)
    assert "download in progress" in line


def test_tns_status_line_reports_the_age_and_next_refresh(monkeypatch, tmp_path):
    line = _status_line(monkeypatch, tmp_path, age_seconds=3.5 * 3600)

    assert "3.5 h ago" in line
    assert "refresh in 20.5 h" in line


def test_tns_status_line_reports_a_fresh_download(monkeypatch, tmp_path):
    line = _status_line(monkeypatch, tmp_path, age_seconds=30)

    assert "downloaded just now" in line
    assert "refresh in 24.0 h" in line


def test_tns_status_line_asks_for_a_refresh_when_stale(monkeypatch, tmp_path):
    line = _status_line(monkeypatch, tmp_path, age_seconds=25 * 3600)

    assert "refresh on the next load" in line


def test_tns_status_line_reports_a_background_refresh(monkeypatch, tmp_path):
    line = _status_line(monkeypatch, tmp_path, age_seconds=3 * 3600, running=True)

    assert "3.0 h ago" in line
    assert "refreshing in the background" in line
