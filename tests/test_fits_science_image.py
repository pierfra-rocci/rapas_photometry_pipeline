"""Regressions for consistent FITS image selection, solving and export."""

from __future__ import annotations

import os
import gzip
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from streamlit.testing.v1 import AppTest

import src.astrometry as astrometry
import src.tools_app as tools_app
import src.utils as utils
from src.tools_pipeline import safe_wcs_create


def _fits_bytes(hdus: list) -> bytes:
    buffer = BytesIO()
    fits.HDUList(hdus).writeto(buffer)
    return buffer.getvalue()


def _solved_header(width: int, height: int) -> fits.Header:
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [150.0, 2.0]
    wcs.wcs.crpix = [width / 2, height / 2]
    wcs.wcs.cdelt = [-0.001, 0.001]
    header = wcs.to_header()
    header["NAXIS1"], header["NAXIS2"] = width, height
    return header


def _table_hdu() -> fits.BinTableHDU:
    return fits.BinTableHDU.from_columns(
        [fits.Column(name="number", format="J", array=np.array([1, 2]))],
        name="CATALOG",
    )


@pytest.mark.parametrize("compressed", [False, True])
def test_loader_skips_tables_and_selects_first_image(compressed: bool) -> None:
    image = np.arange(120, dtype=np.int16).reshape(10, 12)
    image_hdu = (
        fits.CompImageHDU(image, name="SCI")
        if compressed
        else fits.ImageHDU(image, name="SCI")
    )
    image_hdu.header["FILTER"] = "r"
    payload = _fits_bytes([fits.PrimaryHDU(), _table_hdu(), image_hdu])
    data, header, messages = tools_app._parse_fits_bytes.__wrapped__(payload)
    np.testing.assert_array_equal(data, image)
    assert header["FILTER"] == "r"
    assert any("HDU #2" in message for _, _, message in messages)


@pytest.mark.parametrize("case", ["valid", "invalid_fits", "truncated_gzip"])
def test_gzip_loader_closes_streams(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    image = np.arange(120, dtype=np.int16).reshape(10, 12)
    content = (
        b"not a FITS file"
        if case == "invalid_fits"
        else _fits_bytes([fits.PrimaryHDU(image)])
    )
    payload = gzip.compress(content)
    if case == "truncated_gzip":
        payload = payload[:12]
    buffers: list[BytesIO] = []
    compressed_streams: list[gzip.GzipFile] = []

    def tracked_buffer(content: bytes) -> BytesIO:
        buffer = BytesIO(content)
        buffers.append(buffer)
        return buffer

    def tracked_gzip(fileobj: BytesIO, mode: str) -> gzip.GzipFile:
        stream = gzip.GzipFile(fileobj=fileobj, mode=mode)
        compressed_streams.append(stream)
        return stream

    monkeypatch.setattr(tools_app, "BytesIO", tracked_buffer)
    monkeypatch.setattr(tools_app, "gzip", SimpleNamespace(GzipFile=tracked_gzip))
    data, _, messages = tools_app._parse_fits_bytes.__wrapped__(payload)
    if case == "valid":
        np.testing.assert_array_equal(data, image)
    else:
        assert data is None
        assert any(ui == "error" for ui, _, _ in messages)
    assert len(buffers) == len(compressed_streams) == 1
    assert buffers[0].closed and compressed_streams[0].closed


def test_table_only_or_non_image_dimensions_are_reported() -> None:
    payload = _fits_bytes(
        [fits.PrimaryHDU(), _table_hdu(), fits.ImageHDU(np.arange(5))]
    )
    data, header, messages = tools_app._parse_fits_bytes.__wrapped__(payload)
    assert data is None and header is None
    assert any(
        ui in {"warning", "error"} and "image" in message.lower()
        for ui, _, message in messages
    )


def test_loader_skips_one_dimensional_image_before_science_image() -> None:
    image = np.arange(120, dtype=np.int16).reshape(10, 12)
    payload = _fits_bytes([fits.PrimaryHDU(np.arange(5)), fits.ImageHDU(image)])
    data, _, messages = tools_app._parse_fits_bytes.__wrapped__(payload)
    np.testing.assert_array_equal(data, image)
    assert any("HDU #1" in message for _, _, message in messages)


@pytest.mark.parametrize("kind", ["primary", "extension", "compressed"])
@pytest.mark.parametrize("gzip_input", [False, True])
def test_solver_uses_same_2d_image_selected_by_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    gzip_input: bool,
) -> None:
    image = np.arange(120, dtype=np.uint16).reshape((10, 12)) + 32760
    path = tmp_path / ("image.fits.gz" if gzip_input else "image.fits")
    if kind == "primary":
        hdus = [fits.PrimaryHDU(image), _table_hdu()]
    else:
        image_hdu = (
            fits.CompImageHDU(image, name="SCI")
            if kind == "compressed"
            else fits.ImageHDU(image, name="SCI")
        )
        hdus = [fits.PrimaryHDU(), _table_hdu(), image_hdu]
    fits.HDUList(hdus).writeto(path)
    loaded, header, _ = tools_app._parse_fits_bytes.__wrapped__(path.read_bytes())
    observed: list[tuple[np.ndarray, fits.Header]] = []

    def detect(
        image: np.ndarray, science_header: fits.Header, **_kwargs: object
    ) -> None:
        observed.append((image.copy(), science_header.copy()))
        return None

    monkeypatch.setattr(astrometry, "_try_source_detection_improved", detect)
    solved, _, _, error = astrometry.solve_with_astrometrynet(path)
    assert solved is None and error
    assert len(observed) == 1
    np.testing.assert_array_equal(observed[0][0], loaded)
    assert observed[0][1]["NAXIS1"] == header["NAXIS1"] == loaded.shape[1]
    assert observed[0][1]["NAXIS2"] == header["NAXIS2"] == loaded.shape[0]


@pytest.mark.parametrize("science_in_primary", [False, True])
def test_wcs_export_preserves_original_hdus(
    tmp_path: Path,
    science_in_primary: bool,
) -> None:
    image = np.arange(120, dtype=np.float32).reshape(10, 12)
    primary = fits.PrimaryHDU(image if science_in_primary else None)
    science = (
        fits.ImageHDU(image, name="SCI")
        if not science_in_primary
        else fits.ImageHDU(np.ones((10, 12)), name="AUX")
    )
    science.header["OBSERVER"] = "Synthetic"
    path = tmp_path / "image.fits"
    fits.HDUList(
        [
            primary,
            _table_hdu(),
            science,
            fits.ImageHDU(np.full((10, 12), 7), name="ERR"),
        ]
    ).writeto(path)
    original_bytes = path.read_bytes()
    updated = (fits.getheader(path, 0 if science_in_primary else 2)).copy()
    updated.update(_solved_header(12, 10))
    output, error, stored = utils.save_fits_with_wcs(
        str(path),
        updated,
        str(tmp_path / "out"),
        also_save_to_data_dir=False,
    )
    assert output and error is None and stored is None
    assert path.read_bytes() == original_bytes
    with fits.open(path) as original, fits.open(output) as exported:
        assert len(exported) == len(original)
        science_index = 0 if science_in_primary else 2
        assert safe_wcs_create(exported[science_index].header)[0] is not None
        for index in range(len(original)):
            if original[index].data is not None:
                np.testing.assert_array_equal(
                    exported[index].data, original[index].data
                )
            if index != science_index:
                assert exported[index].header == original[index].header
        assert "CHECKSUM" not in exported[science_index].header
        assert "DATASUM" not in exported[science_index].header


@pytest.mark.parametrize("kind", ["primary", "extension", "compressed"])
@pytest.mark.parametrize("gzip_input", [False, True])
def test_export_preserves_scaled_pixels_and_existing_checksums(
    tmp_path: Path,
    kind: str,
    gzip_input: bool,
) -> None:
    image = np.arange(120, dtype=np.uint16).reshape(10, 12) + 32760
    image_hdu = (
        fits.PrimaryHDU(image)
        if kind == "primary"
        else fits.CompImageHDU(image, name="SCI")
        if kind == "compressed"
        else fits.ImageHDU(image, name="SCI")
    )
    image_hdu.header["FILTER"] = "r"
    image_hdu.header.add_history("Original observation")
    hdus = (
        [image_hdu, _table_hdu()]
        if kind == "primary"
        else [fits.PrimaryHDU(), _table_hdu(), image_hdu]
    )
    index = 0 if kind == "primary" else 2
    path = tmp_path / ("image.fits.gz" if gzip_input else "image.fits")
    fits.HDUList(hdus).writeto(path, checksum=True)
    original_bytes = path.read_bytes()
    with fits.open(path, memmap=False) as original:
        np.testing.assert_array_equal(original[index].data, image)
        updated = original[index].header.copy()
    updated.update(_solved_header(12, 10))
    output, error, _ = utils.save_fits_with_wcs(
        str(path),
        updated,
        str(tmp_path / "out"),
        also_save_to_data_dir=False,
    )
    assert output and error is None
    assert path.read_bytes() == original_bytes
    assert Path(output).read_bytes()[:6] == b"SIMPLE"
    with fits.open(output, memmap=False) as exported:
        np.testing.assert_array_equal(exported[index].data, image)
        assert exported[index].header["FILTER"] == "r"
        assert "Original observation" in exported[index].header["HISTORY"]
        assert safe_wcs_create(exported[index].header)[0] is not None
    with fits.open(output, disable_image_compression=True, memmap=False) as physical:
        for hdu in physical:
            assert hdu.verify_checksum() == 1
            assert hdu.verify_datasum() == 1


def test_export_preserves_nontrivial_scaling_and_blank_pixels(tmp_path: Path) -> None:
    raw = np.arange(120, dtype=np.int16).reshape(10, 12)
    raw[0, 0] = -32768
    hdu = fits.PrimaryHDU(raw)
    hdu.header["BSCALE"] = 2.5
    hdu.header["BZERO"] = 123.5
    hdu.header["BLANK"] = -32768
    path = tmp_path / "scaled.fits"
    hdu.writeto(path)
    with fits.open(path, memmap=False) as source:
        pixels = source[0].data.copy()
        updated = source[0].header.copy()
    updated.update(_solved_header(12, 10))
    output, error, _ = utils.save_fits_with_wcs(
        str(path),
        updated,
        str(tmp_path / "out"),
        also_save_to_data_dir=False,
    )
    assert output and error is None
    with fits.open(output, memmap=False) as exported:
        np.testing.assert_allclose(exported[0].data, pixels, equal_nan=True)
    with fits.open(output, memmap=False, do_not_scale_image_data=True) as exported:
        np.testing.assert_array_equal(exported[0].data, raw)
        assert exported[0].header["BSCALE"] == 2.5
        assert exported[0].header["BZERO"] == 123.5
        assert exported[0].header["BLANK"] == -32768


@pytest.mark.parametrize("compressed", [False, True])
def test_selected_image_flows_through_solver_and_private_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compressed: bool,
) -> None:
    import api.config

    image = np.arange(120, dtype=np.uint16).reshape(10, 12) + 32760
    science = (
        fits.CompImageHDU(image, name="SCI")
        if compressed
        else fits.ImageHDU(image, name="SCI")
    )
    science.header["FILTER"] = "r"
    science.header.add_comment("Original observation comment")
    science.header.add_history("Original observation history")
    path = tmp_path / "image.fits"
    fits.HDUList([fits.PrimaryHDU(), _table_hdu(), science]).writeto(path)
    original_bytes = path.read_bytes()
    loaded, _, _ = tools_app._parse_fits_bytes.__wrapped__(original_bytes)
    sources = Table({"x": np.arange(5.0), "y": np.arange(5.0)})
    wcs = WCS(_solved_header(12, 10))

    def detect(data: np.ndarray, _header: fits.Header, **_kwargs: object) -> Table:
        np.testing.assert_array_equal(data, loaded)
        return sources

    monkeypatch.setattr(astrometry, "_try_source_detection_improved", detect)
    monkeypatch.setattr(
        astrometry, "solve_with_enhanced_strategy", lambda *_args: (wcs, [])
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
    fits_root = tmp_path / "private-fits"
    monkeypatch.setattr(api.config, "FITS_STORAGE_ROOT", fits_root)

    solved, updated, messages, error = astrometry.solve_with_astrometrynet(path)
    assert solved is not None and updated is not None and error is None
    assert any("HDU #2" in message for message in messages)
    output, error, stored = utils.save_fits_with_wcs(
        str(path),
        updated,
        str(tmp_path / "out"),
        user_id=73,
    )
    assert output and stored and error is None
    assert Path(stored).parts[:2] == ("private_v2", "user_73")
    assert path.read_bytes() == original_bytes
    for saved in (Path(output), fits_root / stored):
        with fits.open(saved, memmap=False) as exported:
            assert len(exported) == 3
            np.testing.assert_array_equal(exported[2].data, image)
            np.testing.assert_array_equal(exported[1].data["number"], [1, 2])
            assert exported[2].header["FILTER"] == "r"
            assert "Original observation comment" in exported[2].header["COMMENT"]
            assert "Original observation history" in exported[2].header["HISTORY"]
            assert safe_wcs_create(exported[2].header)[0] is not None


def test_export_rejects_files_without_an_image(tmp_path: Path) -> None:
    path = tmp_path / "table.fits"
    fits.HDUList([fits.PrimaryHDU(), _table_hdu()]).writeto(path)
    output_dir = tmp_path / "out"
    output, error, stored = utils.save_fits_with_wcs(
        str(path),
        _solved_header(12, 10),
        str(output_dir),
        also_save_to_data_dir=False,
    )
    assert output is None and stored is None and "image" in error.lower()
    assert list(output_dir.iterdir()) == []


def test_export_cannot_overwrite_original(
    tmp_path: Path,
) -> None:
    path = tmp_path / "image.fits"
    fits.PrimaryHDU(np.ones((4, 4))).writeto(path)
    original_bytes = path.read_bytes()
    output, error, _ = utils.save_fits_with_wcs(
        str(path),
        fits.getheader(path),
        str(tmp_path),
        filename_suffix="",
        also_save_to_data_dir=False,
    )
    assert output is None and error
    assert path.read_bytes() == original_bytes


def test_private_export_does_not_modify_a_hardlinked_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.config

    path = tmp_path / "image.fits"
    fits.PrimaryHDU(np.ones((4, 4))).writeto(path)
    original_bytes = path.read_bytes()
    fits_root = tmp_path / "private-fits"
    monkeypatch.setattr(api.config, "FITS_STORAGE_ROOT", fits_root)
    now = datetime.now()
    private_dir = fits_root / "private_v2" / "user_73" / f"{now:%Y}" / f"{now:%m}"
    private_dir.mkdir(parents=True)
    os.link(path, private_dir / "image_wcs.fits")
    updated = fits.getheader(path)
    updated.update(_solved_header(4, 4))

    output, error, stored = utils.save_fits_with_wcs(
        str(path),
        updated,
        str(tmp_path / "out"),
        user_id=73,
    )
    assert output and stored and error is None
    assert path.read_bytes() == original_bytes
    assert safe_wcs_create(fits.getheader(fits_root / stored))[0] is not None


@pytest.mark.parametrize("filename", ["image.fits.gz", "image.fts.gz"])
def test_streamlit_uploads_compressed_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    def session_tmp_directory(user_id: int, session_id: str | None = None) -> str:
        return str(tmp_path)

    monkeypatch.setattr(utils, "session_tmp_directory", session_tmp_directory)
    page = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "pages" / "app.py")
    )
    page.session_state["logged_in"] = True
    page.session_state["username"] = "synthetic"
    page.session_state["user_id"] = 9
    page.session_state["stale_files_cleaned"] = True
    page.run(timeout=30)
    assert not page.exception
    image = np.arange(120, dtype=np.int16).reshape(10, 12)
    payload = gzip.compress(_fits_bytes([fits.PrimaryHDU(image)]))
    page.file_uploader[0].upload(filename, payload).run(timeout=30)
    assert not page.exception
    assert page.session_state["uploaded_name"] == filename
    assert page.session_state["preview_header"]["NAXIS1"] == 12
    assert page.session_state["preview_header"]["NAXIS2"] == 10


def test_corrupt_fits_stops_before_solving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "broken.fits"
    path.write_bytes(b"not a fits file")

    def output_directory(directory: str = "") -> str:
        result = tmp_path / "results" / directory
        result.mkdir(parents=True, exist_ok=True)
        return str(result)

    def unexpected_solve(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Invalid FITS must not reach the solver")

    monkeypatch.setattr(utils, "ensure_output_directory", output_directory)
    monkeypatch.setattr(astrometry, "solve_with_astrometrynet", unexpected_solve)
    page = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "pages" / "app.py")
    )
    page.session_state["logged_in"] = True
    page.session_state["username"] = "synthetic"
    page.session_state["user_id"] = 9
    page.session_state["stale_files_cleaned"] = True
    page.session_state["uploaded_temp_path"] = str(path)
    page.session_state["uploaded_name"] = "broken.fits"
    page.session_state["run_analysis_pipeline"] = True
    page.run(timeout=30)
    assert not page.exception
    assert any("could not be loaded" in error.value for error in page.error)
