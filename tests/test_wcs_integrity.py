"""Scientific-integrity regressions for FITS headers and celestial WCS."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pytest
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from streamlit.testing.v1 import AppTest

import src.pipeline as pipeline
import src.utils as utils
from src.tools_app import _parse_fits_bytes
from src.tools_pipeline import extract_pixel_scale, fix_header, safe_wcs_create
from src.xmatch_catalogs import cross_match_with_gaia


def _header(projection: str = "TAN", center: tuple[float, float] = (150, 2)) -> fits.Header:
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = [f"RA---{projection}", f"DEC--{projection}"]
    wcs.wcs.crval = center
    wcs.wcs.crpix = [40.0, 40.0]
    wcs.wcs.cdelt = [-0.01, 0.01]
    header = wcs.to_header()
    header["NAXIS"] = 2
    header["NAXIS1"] = 80
    header["NAXIS2"] = 80
    return header


@pytest.mark.parametrize("projection", ["TAN", "SIN"])
@pytest.mark.parametrize("center", [(150, 2), (0, 0)])
def test_valid_wcs_survives_header_repair_without_coordinate_changes(
    projection: str, center: tuple[float, float]
) -> None:
    original = _header(projection, center)
    original["PIXSCALE"] = 1.23
    original["XPIXELSZ"] = 3.76
    fixed, messages = fix_header(original)
    parsed, error, _ = safe_wcs_create(fixed)

    assert error is None, (error, messages)
    assert parsed is not None
    assert fixed["CTYPE1"] == original["CTYPE1"]
    assert fixed["CTYPE2"] == original["CTYPE2"]
    assert fixed["PIXSCALE"] == original["PIXSCALE"]
    assert fixed["XPIXELSZ"] == original["XPIXELSZ"]
    assert extract_pixel_scale(fixed)[0] == pytest.approx(1.23)
    before = WCS(_header(projection, center)).pixel_to_world([0, 40, 79], [0, 40, 79])
    after = parsed.pixel_to_world([0, 40, 79], [0, 40, 79])
    assert np.max(before.separation(after).arcsec) < 1e-6


def test_invalid_cd_matrix_is_not_replaced_by_a_fabricated_wcs() -> None:
    header = _header()
    del header["CDELT1"]
    del header["CDELT2"]
    for key in ("CD1_1", "CD1_2", "CD2_1", "CD2_2"):
        header[key] = 0.0
    original = header.copy()
    buffer = BytesIO()
    fits.PrimaryHDU(np.ones((80, 80)), header=header).writeto(buffer)

    data, fixed, messages = _parse_fits_bytes.__wrapped__(buffer.getvalue())
    parsed, error, _ = safe_wcs_create(fixed)

    assert data is not None
    assert parsed is None
    assert error
    assert any(level == "warning" for level, _, _ in messages)
    assert fixed["CRVAL1"] == original["CRVAL1"]
    assert fixed["CRVAL2"] == original["CRVAL2"]
    assert header == original


def test_missing_scale_does_not_default_to_one_degree_per_pixel() -> None:
    header = _header()
    del header["CDELT1"]
    del header["CDELT2"]
    wcs, error, _ = safe_wcs_create(header)
    assert wcs is None
    assert error


def test_noncelestial_wcs_is_not_accepted_for_sky_coordinates() -> None:
    header = _header()
    header["CTYPE1"] = "LINEAR"
    header["CTYPE2"] = "LINEAR"
    wcs, error, _ = safe_wcs_create(header)
    assert wcs is None
    assert error


def test_small_valid_scale_and_outside_reference_pixel_are_preserved() -> None:
    header = _header()
    header["CRPIX1"] = -20.0
    header["CDELT1"] = -1e-8
    header["CDELT2"] = 1e-8
    fixed, _ = fix_header(header)
    wcs, error, _ = safe_wcs_create(fixed)
    assert error is None
    assert wcs is not None
    assert fixed["CRPIX1"] == -20.0


def test_catalog_matching_rejects_noncelestial_header_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = _header()
    header["CTYPE1"] = "LINEAR"
    header["CTYPE2"] = "LINEAR"
    header["RA"] = 150.0
    header["DEC"] = 2.0

    def unexpected_query(*_args: object, **_kwargs: object) -> None:
        pytest.fail("A catalog query must not run without celestial WCS")

    monkeypatch.setattr("src.xmatch_catalogs.query_vizier_with_fallback", unexpected_query)
    matched, messages = cross_match_with_gaia(
        Table({"xcenter": [40.0], "ycenter": [40.0]}),
        header, 1.0, 3.0, "phot_g_mean_mag", 21.0,
    )
    assert matched is None
    assert any("WCS" in message for message in messages)


@pytest.mark.parametrize("frame", ["invalid", "broken", "galactic", "swapped"])
def test_photometry_uses_icrs_or_omits_coordinates(
    monkeypatch: pytest.MonkeyPatch, frame: str,
) -> None:
    y, x = np.indices((128, 128))
    rng = np.random.default_rng(42)
    image = 200 + rng.normal(0, 2, (128, 128))
    for cx, cy in [(32, 32), (64, 32), (96, 32), (32, 64), (64, 64), (96, 64)]:
        image += 1000 * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / 4.0)
    header = _header()
    header["NAXIS1"] = header["NAXIS2"] = 128
    if frame == "invalid":
        header["CTYPE1"] = "LINEAR"
        header["CTYPE2"] = "LINEAR"
    elif frame == "galactic":
        header["CTYPE1"] = "GLON-TAN"
        header["CTYPE2"] = "GLAT-TAN"
    elif frame == "swapped":
        header["CTYPE1"] = "DEC--TAN"
        header["CTYPE2"] = "RA---TAN"
        header["CRVAL1"], header["CRVAL2"] = 2.0, 150.0
    header["RA"], header["DEC"], header["PIXSCALE"] = 150.0, 2.0, 1.0
    if frame == "broken":
        def fail_transform(*_args: object, **_kwargs: object) -> None:
            raise ValueError("synthetic transform failure")
        monkeypatch.setattr(WCS, "pixel_to_world", fail_transform)

    messages: list[tuple[str, tuple[object, ...]]] = []

    class FakeStreamlit:
        session_state: dict[str, object] = {}

        def __getattr__(self, _name: str) -> object:
            return lambda *_args, **_kwargs: messages.append((_name, _args))

    monkeypatch.setattr(pipeline, "st", FakeStreamlit())
    monkeypatch.setattr(
        pipeline, "mask_and_remove_cosmic_rays", lambda *_args, **_kwargs: np.zeros_like(image, dtype=bool)
    )
    monkeypatch.setattr(
        pipeline, "detect_and_mask_satellite_trails", lambda *_args, **_kwargs: np.zeros_like(image, dtype=bool)
    )
    monkeypatch.setattr(pipeline, "perform_psf_photometry", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(pipeline, "fwhm_fit", lambda *_args, **_kwargs: (3.0, 2.0))
    result = pipeline.detection_and_photometry(image, header, 3.0, 5.0, 10, 1.5)
    table, _, _, _, wcs, *_ = result
    assert table is not None and len(table) > 0, "\n".join(
        f"{name}: {args}" for name, args in messages
    )
    if frame in {"invalid", "broken"}:
        assert wcs is None
        assert "ra" not in table.colnames and "dec" not in table.colnames
    else:
        assert wcs is not None
        expected = WCS(header).pixel_to_world(table["xcenter"], table["ycenter"]).icrs
        np.testing.assert_allclose(table["ra"].to_value("deg"), expected.ra.degree, atol=1e-10)
        np.testing.assert_allclose(table["dec"].to_value("deg"), expected.dec.degree, atol=1e-10)


def test_sip_distortion_and_celestial_cube_survive_validation() -> None:
    from astropy.wcs import Sip

    original = WCS(_header())
    coefficients = np.zeros((3, 3))
    coefficients[2, 0] = 1e-5
    original.sip = Sip(coefficients, np.zeros((3, 3)), None, None, original.wcs.crpix)
    original.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"]
    header = original.to_header(relax=True)
    header["NAXIS1"], header["NAXIS2"] = 80, 80
    validated, error, _ = safe_wcs_create(header)
    assert error is None and validated is not None
    assert validated.sip is not None
    assert original.pixel_to_world(10, 20).separation(
        validated.pixel_to_world(10, 20)
    ).arcsec < 1e-6

    cube = WCS(naxis=3)
    cube.wcs.ctype = ["RA---TAN", "DEC--TAN", "FREQ"]
    cube.wcs.crpix = [40, 40, 1]
    cube.wcs.crval = [150, 2, 1e9]
    cube.wcs.cdelt = [-0.01, 0.01, 1e6]
    cube_header = cube.to_header()
    cube_header["NAXIS1"], cube_header["NAXIS2"] = 80, 80
    celestial, error, _ = safe_wcs_create(cube_header)
    assert error is None and celestial is not None
    assert celestial.pixel_n_dim == 2 and celestial.is_celestial
    import src.astrometry as astrometry
    accepted = astrometry._accept_solved_wcs(cube, cube_header, [])
    assert accepted is not None and accepted.pixel_n_dim == 2


def test_conflicting_matrices_and_nonfinite_projection_are_rejected() -> None:
    conflicting = _header()
    conflicting["PC1_1"] = 1.0
    conflicting["CD1_1"] = -0.01
    conflicting["CD2_2"] = 0.01
    assert safe_wcs_create(conflicting)[0] is None
    header = _header("SIN")
    header["CDELT1"], header["CDELT2"] = -10.0, 10.0
    wcs, error, _ = safe_wcs_create(header)
    assert wcs is None and error


def test_invalid_local_solver_result_tries_next_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.astrometry as astrometry

    invalid = WCS(naxis=2)
    invalid.wcs.ctype = ["LINEAR", "LINEAR"]
    valid = WCS(_header())
    attempts: list[str] = []

    def fake_solve(*_args: object, **_kwargs: object) -> tuple[WCS, str]:
        attempts.append("called")
        return (invalid if len(attempts) == 1 else valid), ""

    monkeypatch.setattr(astrometry, "_run_local_astrometry", fake_solve)
    solved, messages = astrometry.solve_with_enhanced_strategy(
        Table({"x": [40.0], "y": [40.0]}), _header(), np.zeros((80, 80))
    )
    assert solved is valid
    assert len(attempts) == 2
    assert any("Rejected invalid plate solution" in message for message in messages)


def test_invalid_local_solution_falls_back_online_without_invalid_hints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.astrometry as astrometry

    header = _header()
    header["CTYPE1"] = header["CTYPE2"] = "LINEAR"
    image_path = tmp_path / "image.fits"
    fits.PrimaryHDU(np.zeros((80, 80)), header).writeto(image_path)
    invalid, valid = WCS(header), WCS(_header())
    sources = Table({"x": np.arange(10.0), "y": np.arange(10.0)})
    options: dict[str, object] = {}
    monkeypatch.setattr(astrometry, "_try_source_detection_improved", lambda *_args, **_kwargs: sources)
    monkeypatch.setattr(astrometry, "solve_with_enhanced_strategy", lambda *_args: (invalid, []))
    monkeypatch.setattr(astrometry, "_get_online_astrometry_api_key", lambda: "test-key")
    monkeypatch.setattr(astrometry, "get_vizier_catalog_with_fallback", lambda *_args, **_kwargs: (None, False))
    monkeypatch.setattr(astrometry.astrometry, "get_frame_center", lambda **_kwargs: (150.0, 2.0, 0.1))
    monkeypatch.setattr(astrometry.astrometry, "get_pixscale", lambda **_kwargs: 0.01)

    def online_solve(_sources: Table, **kwargs: object) -> WCS:
        options.update(kwargs)
        return valid

    monkeypatch.setattr(astrometry.astrometry, "blind_match_astrometrynet", online_solve)
    solved, updated, messages, error = astrometry.solve_with_astrometrynet(str(image_path))
    assert error is None and solved is not None and updated is not None
    assert safe_wcs_create(updated)[0] is not None
    assert "center_ra" not in options and "center_dec" not in options
    assert options["publicly_visible"] == "n"
    assert any("Rejected invalid plate solution" in message for message in messages)
    assert any("Ignoring invalid WCS solver hints" in message for message in messages)


def test_invalid_wcs_skips_transient_search_and_cutouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.transient as transient

    header = _header()
    header["CTYPE1"] = header["CTYPE2"] = "LINEAR"
    warnings: list[str] = []
    monkeypatch.setattr(transient.st, "warning", warnings.append)
    monkeypatch.setattr(transient, "resolve_effective_gain", lambda *_args: 1.0)

    def unexpected_query(*_args: object, **_kwargs: object) -> None:
        pytest.fail("No network query may run with invalid WCS")

    monkeypatch.setattr(transient, "get_vizier_catalog_with_fallback", unexpected_query)
    monkeypatch.setattr(transient, "_fetch_hips_template", unexpected_query)
    frame = pd.DataFrame({"astrocolibri_name": ["candidate"], "ra": [150.0], "dec": [2.0]})
    image = np.zeros((80, 80))
    assert transient.find_candidates(frame, image, header, 3.0, 1.0, 150.0, 2.0, 0.1) == []
    assert transient.plot_astrocolibri_cutouts(frame, image, header, str(tmp_path), "image") == []
    assert len(warnings) == 2


@pytest.mark.parametrize("frame", ["galactic", "swapped"])
def test_catalog_query_uses_wcs_image_center_in_icrs(
    monkeypatch: pytest.MonkeyPatch, frame: str,
) -> None:
    header = _header()
    if frame == "galactic":
        header["CTYPE1"], header["CTYPE2"] = "GLON-TAN", "GLAT-TAN"
    else:
        header["CTYPE1"], header["CTYPE2"] = "DEC--TAN", "RA---TAN"
        header["CRVAL1"], header["CRVAL2"] = 2.0, 150.0
    header["RA"], header["DEC"] = 1.0, 1.0
    query_centers = []

    class FakeVizier:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def query_region(self, center: object, **_kwargs: object) -> list:
            query_centers.append(center)
            raise RuntimeError("offline")

    monkeypatch.setattr("src.xmatch_catalogs.Vizier", FakeVizier)

    def fail_tap(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("offline")

    monkeypatch.setattr("src.xmatch_catalogs.Gaia.launch_job", fail_tap)
    cross_match_with_gaia(
        Table({"xcenter": [40.0], "ycenter": [40.0]}),
        header, 1.0, 3.0, "phot_g_mean_mag", 21.0,
    )
    assert query_centers
    expected = WCS(header).pixel_to_world(39.5, 39.5).icrs
    assert query_centers[0].separation(expected).arcsec < 1e-6


def test_forced_solve_rejects_invalid_header_and_retains_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.astrometry as astrometry
    import src.tools_app as tools_app

    header = _header()
    header["PIXSCALE"] = 1.0
    image_path = tmp_path / "image.fits"
    fits.PrimaryHDU(np.zeros((80, 80)), header).writeto(image_path)
    invalid_header = header.copy()
    invalid_header["CTYPE1"] = invalid_header["CTYPE2"] = "LINEAR"

    def output_directory(directory: str = "") -> str:
        path = tmp_path / "results" / directory
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(utils, "ensure_output_directory", output_directory)
    monkeypatch.setattr(tools_app, "try_gaia_server", lambda: False)
    monkeypatch.setattr(astrometry, "solve_with_astrometrynet", lambda _path: (WCS(header), invalid_header, [], None))
    monkeypatch.setattr(pipeline, "airmass", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(
        pipeline, "detection_and_photometry",
        lambda *_args, **_kwargs: (None, None, None, None, None, None, None, None),
    )
    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "pages" / "app.py"))
    page.session_state["logged_in"] = True
    page.session_state["username"] = "synthetic"
    page.session_state["user_id"] = 9
    page.session_state["stale_files_cleaned"] = True
    page.session_state["uploaded_temp_path"] = str(image_path)
    page.session_state["uploaded_name"] = "image.fits"
    page.run(timeout=30)
    next(toggle for toggle in page.toggle if toggle.label == "Astrometry check").set_value(True)
    page.session_state["run_analysis_pipeline"] = True
    page.run(timeout=30)
    assert not page.exception
    assert not page.session_state["proceed_without_wcs"]
    assert page.session_state["calibrated_header"]["CTYPE1"] == header["CTYPE1"]
    assert any("using the original valid WCS" in warning.value for warning in page.warning)
    assert any("invalid WCS" in error.value for error in page.error)
    assert not any("Plate-solve successful" in success.value for success in page.success)


@pytest.mark.parametrize("invalid_solution", [False, True])
def test_unsolved_image_exports_instrumental_results_without_sky_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_solution: bool,
) -> None:
    import src.astrometry as astrometry
    import src.xmatch_catalogs as catalogs

    header = _header()
    del header["CDELT1"]
    del header["CDELT2"]
    for key in ("CD1_1", "CD1_2", "CD2_1", "CD2_2"):
        header[key] = 0.0
    header["PIXSCALE"] = 1.0
    header["RA"], header["DEC"] = 150.0, 2.0
    image_path = tmp_path / "image.fits"
    fits.PrimaryHDU(np.random.default_rng(42).normal(200, 3, (80, 80)), header).writeto(image_path)
    results = tmp_path / "results"

    def output_directory(directory: str = "") -> str:
        path = results / directory
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    solve_calls: list[str] = []
    monkeypatch.setattr(utils, "ensure_output_directory", output_directory)
    def failed_solve(path: str) -> tuple[WCS | None, fits.Header | None, list[str], str | None]:
        solve_calls.append(path)
        if invalid_solution:
            return WCS(_header()), header, [], None
        return None, None, [], "synthetic solve failure"

    monkeypatch.setattr(astrometry, "solve_with_astrometrynet", failed_solve)
    monkeypatch.setattr(pipeline, "airmass", lambda *_args, **_kwargs: 0.0)
    photometry = Table(
        {
            "xcenter": [30.0],
            "ycenter": [40.0],
            "aperture_sum_bkg_corr_1_3": [100.0],
            "instrumental_mag_1_3": [-5.0],
        }
    )
    monkeypatch.setattr(
        pipeline,
        "detection_and_photometry",
        lambda *_args, **_kwargs: (photometry, None, None, None, None, None, 3.0, None),
    )

    def unexpected_query(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Catalog matching must not run without celestial WCS")

    monkeypatch.setattr(catalogs, "cross_match_with_gaia", unexpected_query)
    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "pages" / "app.py"))
    page.session_state["logged_in"] = True
    page.session_state["username"] = "synthetic"
    page.session_state["user_id"] = 9
    page.session_state["stale_files_cleaned"] = True
    page.session_state["uploaded_temp_path"] = str(image_path)
    page.session_state["uploaded_name"] = "image.fits"
    page.session_state["run_analysis_pipeline"] = True
    page.run(timeout=30)

    assert not page.exception
    assert solve_calls == [str(image_path)]
    assert page.session_state["proceed_without_wcs"]
    if invalid_solution:
        assert any("invalid WCS" in error.value for error in page.error)
        assert not any("Initial solve successful" in success.value for success in page.success)
    assert "ra" not in page.session_state["final_phot_table"].columns
    archives = list(results.rglob("*.zip"))
    assert len(archives) == 1, "\n".join(
        [error.value for error in page.error]
        + [str(path.relative_to(results)) for path in results.rglob("*")]
    )
    assert not any("Required columns" in error.value for error in page.error)
    with ZipFile(archives[0]) as archive:
        csv = archive.read("image_instrumental_catalog.csv").decode()
        votable = Table.read(
            BytesIO(archive.read("image_instrumental_catalog.vot")), format="votable"
        )
    assert "instrumental_mag_1_3" in csv
    assert "aperture_sum_bkg_corr_1_3" in csv
    assert "-5.0" in csv
    assert {"xcenter", "ycenter", "instrumental_mag_1_3", "aperture_sum_bkg_corr_1_3"} <= set(votable.colnames)
    assert "ra" not in votable.colnames and "dec" not in votable.colnames


@pytest.mark.parametrize(
    "failure",
    ["calibration_offline", "calibration_exception", "zero_point", "psf_merge_error", "optional_services"],
)
def test_offline_failures_preserve_catalog_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    import matplotlib.pyplot as plt

    from src import astrometry, tools_app, tools_pipeline, transient
    from src import xmatch_catalogs as catalogs

    header = _header()
    header["PIXSCALE"] = 1.0
    header["RA"], header["DEC"] = 150.0, 2.0
    wcs = WCS(header)
    image_path = tmp_path / "image.fits"
    fits.PrimaryHDU(np.random.default_rng(42).normal(200, 3, (80, 80)), header).writeto(image_path)
    results = tmp_path / "results"

    def output_directory(directory: str = "") -> str:
        path = results / directory
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def offline(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated offline service")

    monkeypatch.setattr(utils, "ensure_output_directory", output_directory)
    monkeypatch.setattr(tools_app, "try_gaia_server", lambda: False)
    monkeypatch.setattr(astrometry, "solve_with_astrometrynet", offline)
    monkeypatch.setattr(pipeline, "airmass", lambda *_args, **_kwargs: 0.0)
    photometry = Table({
        "xcenter": [30.0], "ycenter": [40.0],
        "aperture_sum_bkg_corr_1_3": [100.0], "instrumental_mag_1_3": [-5.0],
    })
    psf = Table({"x_init": [30.0], "y_init": [40.0], "flux_fit": [90.0], "instrumental_mag": [-4.9]})
    monkeypatch.setattr(
        pipeline, "detection_and_photometry",
        lambda *_args, **_kwargs: (photometry, psf, None, None, wcs, None, 3.0, None),
    )
    matched = (
        None if failure in ("calibration_offline", "psf_merge_error")
        else pd.DataFrame({"ra": [150.0], "dec": [2.0]})
    )
    monkeypatch.setattr(
        catalogs, "cross_match_with_gaia",
        offline if failure == "calibration_exception" else lambda *_args, **_kwargs: (matched, []),
    )
    monkeypatch.setattr(tools_pipeline, "validate_cross_match_results", lambda *_args: True)
    if failure == "psf_merge_error":
        monkeypatch.setattr(tools_pipeline, "merge_photometry_catalogs", offline)
    zero_point = None if failure == "zero_point" else 25.0
    monkeypatch.setattr(pipeline, "calculate_zero_point", lambda *_args: (zero_point, 0.1, None))
    monkeypatch.setattr(pipeline, "fit_color_term_correction", lambda *_args, **_kwargs: {"fitted": False, "supported": False})
    monkeypatch.setattr(
        tools_pipeline, "add_calibrated_magnitudes",
        lambda table, **_kwargs: table.assign(aperture_mag_1_1=20.0),
    )
    monkeypatch.setattr(tools_pipeline, "clean_photometry_table", lambda table, **_kwargs: (table, []))
    monkeypatch.setattr(tools_pipeline, "apply_color_term_correction", lambda table, *_args, **_kwargs: table)
    monkeypatch.setattr(tools_app, "plot_magnitude_distribution", lambda *_args: plt.figure())
    monkeypatch.setattr(transient, "plot_astrocolibri_cutouts", lambda *_args, **_kwargs: [])
    transient_calls: list[bool] = []

    def fail_transient(*_args: object, **_kwargs: object) -> None:
        transient_calls.append(True)
        offline()

    monkeypatch.setattr(catalogs, "enhance_catalog", offline)
    monkeypatch.setattr(transient, "find_candidates", fail_transient)
    monkeypatch.setattr(
        tools_app, "display_catalog_in_aladin",
        offline if failure == "optional_services" else lambda *_args, **_kwargs: None,
    )
    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "pages" / "app.py"))
    page.session_state["logged_in"] = True
    page.session_state["username"] = "synthetic"
    page.session_state["user_id"] = 9
    page.session_state["stale_files_cleaned"] = True
    page.session_state["uploaded_temp_path"] = str(image_path)
    page.session_state["uploaded_name"] = "image.fits"
    page.run(timeout=30)
    next(toggle for toggle in page.toggle if toggle.label == "Astrometry check").set_value(False)
    next(box for box in page.checkbox if box.label == "Enable Transient Finder").set_value(True)
    page.session_state["run_analysis_pipeline"] = True
    page.run(timeout=30)

    if failure == "calibration_exception":
        assert len(page.exception) == 1
        assert "simulated offline service" in page.exception[0].message
    else:
        assert not page.exception
    errors = [item.value for item in page.error]
    archives = list(results.rglob("*.zip"))
    assert len(archives) == 1, errors
    with ZipFile(archives[0]) as archive:
        names = set(archive.namelist())
        assert "image.log" in names
        assert "Processing completed" in archive.read("image.log").decode()
        if failure == "optional_services":
            assert {"image.csv", "image.vot"} <= names
            assert "image_transients.csv" not in names
            assert any("Cross-match failed" in message for message in errors)
            assert any("Error during transient finding" in message for message in errors)
            assert any("Error displaying Aladin viewer" in message for message in errors)
            assert transient_calls
        else:
            assert {"image_instrumental_catalog.csv", "image_instrumental_catalog.vot"} <= names
            csv = pd.read_csv(BytesIO(archive.read("image_instrumental_catalog.csv")))
            votable = Table.read(BytesIO(archive.read("image_instrumental_catalog.vot")), format="votable")
            assert {"ra", "dec", "instrumental_mag_1_3"} <= set(csv.columns)
            assert {"ra", "dec", "instrumental_mag_1_3"} <= set(votable.colnames)
            assert np.isfinite(csv["ra"]).all() and np.isfinite(csv["dec"]).all()
            assert csv["instrumental_mag_1_3"].iloc[0] == -5.0
            if failure == "psf_merge_error":
                assert "psf_instrumental_mag" not in csv.columns
                assert any("Could not merge PSF photometry" in item.value for item in page.warning)
            else:
                assert csv["psf_instrumental_mag"].iloc[0] == -4.9
                assert "psf_instrumental_mag" in votable.colnames
            assert "image_transients.csv" not in names
            assert not transient_calls
            assert any("Transient search skipped" in item.value for item in page.warning)

    assert page.session_state["last_result_zip_path"] == str(archives[0])
    page.run(timeout=30)
    assert not page.exception
    assert any("Previously archived results" in caption.value for caption in page.caption)
    assert list(results.rglob("*.zip")) == archives
