"""Tests for the curated CSV/VOT export, source ids and the transient table."""

import numpy as np
import pandas as pd
from astropy.io.votable import parse_single_table

from src.utils import (
    build_export_columns,
    ensure_source_ids,
    prepare_export_table,
    save_catalog_files,
)
from src.transient import build_transient_table

FILTER_BAND = "phot_g_mean_mag"  # prefix: rapasg


def _make_table(radius="1_5", n=4):
    data = {
        "id": [1, 2, np.nan, 4][:n],
        "xcenter": [10.0, 20.0, 30.0, 40.0][:n],
        "ycenter": [11.0, 21.0, 31.0, 41.0][:n],
        "x_center": [10.0, 20.0, np.nan, 40.0][:n],
        "y_center": [11.0, 21.0, np.nan, 41.0][:n],
        "ra": [10.0, 10.01, 10.02, 10.03][:n],
        "dec": [20.0, 20.01, 20.02, 20.03][:n],
        "astrocolibri_name": ["", "", "AT2024xyz", ""][:n],
        "astrocolibri_type": ["", "", "SN", ""][:n],
        "simbad_main_id": ["a", "", "", "d"][:n],
        "catalog_matches": ["GAIA", "", "", "GAIA"][:n],
        "phot_method": ["both"] * n,
        "extra_junk": [1.0] * n,
        "rapasg_psf_mag": [15.0, 16.0, 17.0, 18.0][:n],
        "rapasg_psf_mag_err": [0.01] * n,
        "rapasg_psf_mag_colorcorr": [15.1] * n,
        "psf_mag": [15.0, 16.0, 17.0, 18.0][:n],
    }
    for sfx in ("1_1", "1_3", radius):
        data[f"snr_{sfx}"] = [50.0] * n
        data[f"quality_flag_{sfx}"] = ["good"] * n
        data[f"rapasg_aperture_mag_{sfx}"] = [15.0] * n
        data[f"rapasg_aperture_mag_err_{sfx}"] = [0.02] * n
        data[f"rapasg_psf_minus_aperture_mag_{sfx}"] = [0.1] * n
    return pd.DataFrame(data)


def test_ensure_source_ids_adds_and_completes():
    df = _make_table()
    out = ensure_source_ids(df)
    assert out["id"].notna().all()
    assert out["id"].is_unique
    assert list(out["id"][[0, 1, 3]]) == [1, 2, 4]
    assert str(out["id"].dtype) == "Int64"

    no_id = ensure_source_ids(df.drop(columns="id"))
    assert list(no_id["id"]) == [1, 2, 3, 4]


def test_ensure_source_ids_renumbers_duplicates():
    df = pd.DataFrame({"ra": [1.0, 2.0, 3.0], "id": [1, 1, 2]})
    assert list(ensure_source_ids(df)["id"]) == [1, 2, 3]


def test_export_columns_order_and_dynamic_radius():
    cols = build_export_columns(_make_table("1_5").columns, FILTER_BAND)
    assert cols[:3] == ["id", "ra", "dec"]
    assert cols[-3:] == [
        "rapasg_psf_minus_aperture_mag_1_1",
        "rapasg_psf_minus_aperture_mag_1_3",
        "rapasg_psf_minus_aperture_mag_1_5",
    ]
    assert cols.index("snr_1_5") < cols.index("rapasg_aperture_mag_1_1")
    assert cols.index("rapasg_aperture_mag_1_1") + 1 == cols.index("quality_flag_1_1")
    assert cols.index("rapasg_psf_mag") < cols.index("rapasg_aperture_mag_err_1_1")
    assert "extra_junk" not in cols
    assert "phot_method" not in cols
    assert "rapasg_psf_mag_colorcorr" not in cols
    assert "xcenter" not in cols

    cols_21 = build_export_columns(_make_table("2_0").columns, FILTER_BAND)
    assert "snr_2_0" in cols_21 and "snr_1_5" not in cols_21
    assert cols_21.index("snr_1_3") < cols_21.index("snr_2_0")


def test_export_columns_fall_back_to_legacy_names():
    df = pd.DataFrame({"ra": [1.0], "dec": [2.0], "psf_mag": [15.0]})
    assert build_export_columns(df.columns, None) == ["ra", "dec", "psf_mag"]


def test_prepare_export_table_fills_ids_coords_and_sorts():
    out = prepare_export_table(_make_table(), FILTER_BAND)
    assert out.columns[0] == "id"
    assert out["astrocolibri_name"].iloc[0] == "AT2024xyz"
    assert out["id"].iloc[0] == 5  # filled id kept, row moved to the top
    assert out["x_center"].notna().all()
    assert out["x_center"].iloc[0] == 30.0 and out["y_center"].iloc[0] == 31.0
    assert list(out["id"].iloc[1:]) == [1, 2, 4]  # others keep their order


def test_prepare_export_table_without_detection_keeps_order():
    df = _make_table()
    df["astrocolibri_name"] = ""
    out = prepare_export_table(df, FILTER_BAND)
    assert list(out["id"].iloc[[0, 1, 3]]) == [1, 2, 4]


def test_save_catalog_files_csv_and_vot(tmp_path):
    ok, errors = save_catalog_files(
        _make_table(), "field", str(tmp_path), filter_band=FILTER_BAND
    )
    assert not errors, errors
    csv = pd.read_csv(tmp_path / "field.csv")
    expected = build_export_columns(_make_table().columns, FILTER_BAND)
    assert list(csv.columns) == expected
    assert csv["astrocolibri_name"].iloc[0] == "AT2024xyz"

    vot = parse_single_table(str(tmp_path / "field.vot")).to_table()
    assert list(vot.colnames) == expected
    assert vot["astrocolibri_name"][0] == "AT2024xyz"
    assert int(vot["id"][0]) == 5


def test_build_transient_table_matches_catalog_rows():
    table = _make_table()
    candidates = [
        {"ra": 10.0201, "dec": 20.0201, "x": 30.5, "y": 31.5,
         "mag_calib": 17.0, "mag_calib_err": 0.01},
        {"ra": 50.0, "dec": -10.0, "x": 5.0, "y": 6.0,
         "mag_calib": 19.0, "mag_calib_err": 0.2},
    ]
    out = build_transient_table(candidates, table, FILTER_BAND, match_radius_arcsec=10.0)
    assert len(out) == 2
    assert out.columns[0] == "id"
    assert out["id"].iloc[0] == 5
    assert out["astrocolibri_name"].iloc[0] == "AT2024xyz"
    assert pd.isna(out["id"].iloc[1])
    assert out["ra"].iloc[1] == 50.0
    assert out["rapasg_psf_mag"].iloc[1] == 19.0
    assert out["x_center"].iloc[1] == 5.0


def test_build_transient_table_without_photometry():
    cands = [{"ra": 1.0, "dec": 2.0, "x": 3.0, "y": 4.0, "mag_calib": 18.0, "mag_calib_err": 0.1}]
    out = build_transient_table(cands, None, FILTER_BAND)
    assert list(out["ra"]) == [1.0] and list(out["psf_mag"]) == [18.0]
    assert build_transient_table([], None).empty
