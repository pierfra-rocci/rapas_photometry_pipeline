"""Regression tests for pure-logic helpers in src/ not covered elsewhere.

These tests target previously untested utility functions so that behaviour
changes in them are caught before deployment. They avoid network access and
Streamlit dependencies by design (pure logic only).
"""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from src.utils import (
    FIGURE_SIZES,
    get_base_filename,
    get_header_value,
    get_pipeline_figure_size,
    initialize_log,
    sanitize_username,
    safe_catalog_query,
    write_to_log,
)
from src.tools_pipeline import (  # noqa: E402
    extract_coordinates,
    extract_filter_from_header,
    extract_pixel_scale,
    get_filter_prefix,
    get_prefixed_photometry_column,
    resolve_photometric_color_columns,
)
from src.pipeline import airmass, make_border_mask  # noqa: E402
from src.astrometry import (  # noqa: E402
    estimate_pixel_scale_robust,
    get_adaptive_min_sources,
)


# ---------------------------------------------------------------------------
# src/utils.py
# ---------------------------------------------------------------------------


class TestGetHeaderValue:
    def test_returns_first_matching_key_in_priority_order(self):
        header = fits.Header([("EXPTIME", 120.0), ("EXPOSURE", 99.0)])
        assert get_header_value(header, ["EXPTIME", "EXPOSURE"]) == 120.0

    def test_falls_back_to_later_key(self):
        header = fits.Header([("EXPOSURE", 99.0)])
        assert get_header_value(header, ["EXPTIME", "EXPOSURE"]) == 99.0

    def test_returns_default_when_no_key_matches(self):
        header = fits.Header([("OTHER", 1)])
        assert get_header_value(header, ["EXPTIME"], 0.0) == 0.0

    def test_none_header_returns_default(self):
        assert get_header_value(None, ["EXPTIME"], "fallback") == "fallback"


class TestGetBaseFilename:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("image.fits", "image"),
            ("image.fits.fz", "image"),
            ("image.tar.gz", "image"),
            ("catalog.csv", "catalog"),
            ("noext", "noext"),
        ],
    )
    def test_common_names(self, name, expected):
        assert get_base_filename(SimpleNamespace(name=name)) == expected

    def test_none_returns_default(self):
        assert get_base_filename(None) == "photometry"


class TestSanitizeUsername:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("alice", "alice"),
            ("alice_1", "alice_1"),
            ("alice-1.dev", "alice-1.dev"),
            ("../../etc", "etc"),  # path components are dropped
            ("a/b/c", "c"),
            ("a\\b", "b"),
            ("..", "anonymous"),
            (".", "anonymous"),
            ("", "anonymous"),
            ("a" * 100, "a" * 64),  # truncated to 64 chars
            # unsafe chars become underscores, then edges are stripped
            ("bad*name!", "bad_name"),
        ],
    )
    def test_known_values(self, raw, expected):
        assert sanitize_username(raw) == expected

    def test_non_string_returns_fallback(self):
        assert sanitize_username(42) == "anonymous"

    def test_custom_fallback(self):
        assert sanitize_username(None, fallback="guest") == "guest"


class TestLogBuffer:
    def test_initialize_log_contains_header_and_filename(self):
        buf = initialize_log("science.fits")
        text = buf.getvalue()
        assert "RAPAS Photometry Pipeline Log" in text
        assert "science.fits" in text

    def test_write_to_log_formats_level_and_message(self):
        buf = StringIO()
        write_to_log(buf, "boom", level="error")
        line = buf.getvalue()
        assert "ERROR: boom" in line
        assert line.startswith("[")

    def test_write_to_log_none_buffer_is_noop(self):
        assert write_to_log(None, "ignored") is None


class TestSafeCatalogQuery:
    def test_returns_result_and_none_on_success(self):
        result, error = safe_catalog_query(lambda a, b: a + b, "msg", 1, b=2)
        assert result == 3
        assert error is None

    def test_timeout_message(self):
        import requests

        def boom():
            raise requests.exceptions.Timeout()

        result, error = safe_catalog_query(boom, "SIMBAD query")
        assert result is None
        assert error == "SIMBAD query: Query timed out"

    def test_generic_exception_message(self):
        def boom():
            raise RuntimeError("disk on fire")

        result, error = safe_catalog_query(boom, "catalog query")
        assert result is None
        assert error == "catalog query: disk on fire"


class TestPipelineFigureSize:
    def test_scales_both_axes_by_pipeline_scale(self):
        w, h = get_pipeline_figure_size((10, 8))
        assert w == pytest.approx(10 * 2 * 0.75)
        assert h == pytest.approx(8 * 0.75)

    def test_known_size_keys_exist(self):
        for key in ("small", "medium", "large", "wide", "stars_grid"):
            assert key in FIGURE_SIZES


# ---------------------------------------------------------------------------
# src/tools_pipeline.py
# ---------------------------------------------------------------------------


class TestFilterPrefix:
    def test_gaia_g(self):
        assert get_filter_prefix("phot_g_mean_mag") == "rapasg"

    def test_gaia_bp(self):
        assert get_filter_prefix("phot_bp_mean_mag") == "rapasbp"

    def test_johnson_v(self):
        assert get_filter_prefix("v_jkc_mag") == "v"

    def test_none_returns_none(self):
        assert get_filter_prefix(None) is None

    def test_unknown_returns_none(self):
        assert get_filter_prefix("not_a_band") is None

    def test_prefixed_column(self):
        assert (
            get_prefixed_photometry_column("psf_mag", "phot_g_mean_mag")
            == "rapasg_psf_mag"
        )

    def test_prefixed_column_unknown_filter(self):
        assert get_prefixed_photometry_column("psf_mag", "bogus") is None


class TestResolvePhotometricColorColumns:
    def test_gaia_bands_support_bp_rp(self):
        for band in ("phot_g_mean_mag", "phot_bp_mean_mag", "phot_rp_mean_mag"):
            info = resolve_photometric_color_columns(band)
            assert info["supported"] is True
            assert info["color_label"] == "BP-RP"
            assert info["derived_column"] == "bp_rp"

    def test_none_is_unsupported(self):
        info = resolve_photometric_color_columns(None)
        assert info["supported"] is False

    def test_unknown_is_unsupported(self):
        info = resolve_photometric_color_columns("v_jkc_mag")
        assert info["supported"] is False


class TestExtractCoordinates:
    def test_none_header(self):
        assert extract_coordinates(None) == (None, None, "No header available")

    def test_ra_dec_keywords(self):
        header = fits.Header([("RA", 150.1), ("DEC", 2.2)])
        ra, dec, source = extract_coordinates(header)
        assert ra == pytest.approx(150.1)
        assert dec == pytest.approx(2.2)
        assert source == "RA/DEC"

    def test_falls_back_to_objra(self):
        header = fits.Header([("OBJRA", 10.0), ("OBJDEC", -30.0)])
        ra, dec, source = extract_coordinates(header)
        assert (ra, dec) == (10.0, -30.0)
        assert source == "OBJRA/OBJDEC"

    def test_falls_back_to_crval(self):
        header = fits.Header([("CRVAL1", 200.0), ("CRVAL2", 45.0)])
        ra, dec, source = extract_coordinates(header)
        assert (ra, dec) == (200.0, 45.0)
        assert source == "CRVAL1/CRVAL2"

    def test_out_of_range_ra(self):
        header = fits.Header([("RA", 999.0), ("DEC", 10.0)])
        ra, dec, message = extract_coordinates(header)
        assert ra is None and dec is None
        assert "Invalid RA" in message

    def test_out_of_range_dec(self):
        header = fits.Header([("RA", 10.0), ("DEC", 95.0)])
        ra, dec, message = extract_coordinates(header)
        assert ra is None and dec is None
        assert "Invalid DEC" in message

    def test_non_numeric(self):
        header = fits.Header([("RA", "abc"), ("DEC", "def")])
        ra, dec, message = extract_coordinates(header)
        assert ra is None and dec is None
        assert "Non-numeric" in message

    def test_missing_keywords(self):
        ra, dec, message = extract_coordinates(fits.Header())
        assert ra is None and dec is None
        assert message == "Coordinates not found in header"


class TestExtractPixelScale:
    def test_none_header(self):
        assert extract_pixel_scale(None) == (1.0, "default (no header)")

    def test_direct_keyword(self):
        header = fits.Header([("PIXSCALE", 0.62)])
        value, source = extract_pixel_scale(header)
        assert value == pytest.approx(0.62)
        assert "PIXSCALE" in source

    def test_unsane_direct_keyword_is_ignored(self):
        header = fits.Header([("PIXSCALE", 500.0)])
        # Falls through to default fallback
        assert extract_pixel_scale(header)[0] == 0.0

    def test_cd_matrix(self):
        header = fits.Header([("CD1_1", -2.0 / 3600.0), ("CD2_2", 2.0 / 3600.0)])
        value, source = extract_pixel_scale(header)
        assert value == pytest.approx(2.0)
        assert "CD matrix" in source

    def test_pixel_size_and_focal_length(self):
        header = fits.Header([("XPIXSZ", 3.91), ("FOCALLEN", 800.0)])
        value, source = extract_pixel_scale(header)
        assert value == pytest.approx(206.0 * 3.91 / 800.0)
        assert "calculated" in source

    def test_pixel_size_in_mm_units(self):
        header = fits.Header(
            [("XPIXSZ", 0.00391), ("XPIXSZU", "mm"), ("FOCALLEN", 800.0)]
        )
        value, _ = extract_pixel_scale(header)
        assert value == pytest.approx(206.0 * 3.91 / 800.0)

    def test_default_fallback(self):
        value, source = extract_pixel_scale(fits.Header())
        assert value == 0.0
        assert source == "default fallback value"


class TestExtractFilterFromHeader:
    def test_none_header(self):
        assert extract_filter_from_header(None) == ("Unknown", "phot_g_mean_mag")

    def test_simple_filter(self):
        raw, mapped = extract_filter_from_header(fits.Header([("FILTER", "r")]))
        assert raw == "r"
        assert mapped == "r_jkc_mag"

    def test_filter_with_spaces_and_case(self):
        raw, mapped = extract_filter_from_header(fits.Header([("FILTER", "Cousins R")]))
        assert raw == "Cousins R"
        assert mapped == "r_jkc_mag"

    def test_empty_and_none_values_map_to_unknown(self):
        for value in ("", "NONE", "None"):
            assert extract_filter_from_header(
                fits.Header([("FILTER", value)])
            ) == ("Unknown", "phot_g_mean_mag")

    def test_unknown_filter_maps_to_gaia_g(self):
        raw, mapped = extract_filter_from_header(fits.Header([("FILTER", "mystery")]))
        assert raw == "mystery"
        assert mapped == "phot_g_mean_mag"

    def test_filter_keyword_priority(self):
        # FILTER wins over FILTERS which wins over FLT
        header = fits.Header([("FILTER", "g"), ("FLT", "r")])
        raw, _ = extract_filter_from_header(header)
        assert raw == "g"


# ---------------------------------------------------------------------------
# src/pipeline.py
# ---------------------------------------------------------------------------


class TestMakeBorderMask:
    # Contract (as used by detection_and_photometry, where "True = masked"):
    # invert=True -> border pixels are True (masked) and the inner region False.
    def test_int_border_inverted(self):
        image = np.ones((20, 20))
        mask = make_border_mask(image, 5)
        assert mask.shape == (20, 20)
        assert mask.dtype == bool
        assert mask[:5, :].all()
        assert mask[-5:, :].all()
        assert mask[:, :5].all()
        assert mask[:, -5:].all()
        assert not mask[5:-5, 5:-5].any()

    def test_non_inverted_marks_border_false(self):
        image = np.ones((20, 20))
        mask = make_border_mask(image, 5, invert=False)
        assert not mask[:5, :].any()
        assert mask[5:-5, 5:-5].all()

    def test_tuple_of_two(self):
        image = np.ones((40, 50))
        mask = make_border_mask(image, (10, 20))
        assert mask[:10, :].all()
        assert mask[-10:, :].all()
        assert mask[:, :20].all()
        assert mask[:, -20:].all()
        assert not mask[10:-10, 20:-20].any()

    def test_tuple_of_four_asymmetric(self):
        image = np.ones((30, 40))
        mask = make_border_mask(image, (1, 2, 3, 4))
        assert mask[:1, :].all()
        assert mask[-2:, :].all()
        assert mask[:, :3].all()
        assert mask[:, -4:].all()
        assert not mask[1:-2, 3:-4].any()

    def test_dtype_cast(self):
        image = np.ones((10, 10))
        mask = make_border_mask(image, 2, dtype=np.uint8)
        assert mask.dtype == np.uint8
        assert set(np.unique(mask)) <= {0, 1}

    @pytest.mark.parametrize(
        ("image", "border", "exc"),
        [
            (None, 5, ValueError),
            ([1, 2, 3], 5, TypeError),
            (np.ones((0, 0)), 5, ValueError),
            (np.ones(10), 5, ValueError),
            (np.ones((10, 10)), -1, ValueError),
            (np.ones((10, 10)), (5, 6, 7), ValueError),
            (np.ones((10, 10)), 10, ValueError),  # border == height
            (np.ones((10, 10)), (0, 0, 5, 6), ValueError),  # left+right == width
        ],
    )
    def test_invalid_inputs_raise(self, image, border, exc):
        with pytest.raises(exc):
            make_border_mask(image, border)


DEFAULT_OBSERVATORY = {
    "name": "RAPAS",
    "latitude": 45.2,
    "longitude": 0.2,
    "elevation": 100.0,
}


class TestAirmassFromHeader:
    def _header(self, **extra):
        pairs = [
            ("RA", 150.1),
            ("DEC", 2.2),
            ("DATE-OBS", "2026-01-15T22:30:00"),
        ]
        pairs.extend(extra.items())
        return fits.Header(pairs)

    def test_existing_airmass_keyword_is_reused(self):
        header = self._header(AIRMASS=1.42)
        result = airmass(header, observatory=DEFAULT_OBSERVATORY)
        assert result == pytest.approx(1.42)

    def test_secz_keyword_is_reused(self):
        header = self._header(SECZ=1.5)
        assert airmass(header, observatory=DEFAULT_OBSERVATORY) == pytest.approx(1.5)

    def test_details_report_source_keyword(self):
        header = self._header(AIRMASS=1.2)
        value, details = airmass(
            header, observatory=DEFAULT_OBSERVATORY, return_details=True
        )
        assert value == pytest.approx(1.2)
        assert details["airmass_source"] == "header_AIRMASS"

    def test_invalid_header_airmass_is_rejected_and_recomputed(self):
        header = self._header(AIRMASS=99.0)  # out of the 1..30 validity window
        value = airmass(header, observatory=DEFAULT_OBSERVATORY)
        assert 1.0 <= value <= 30.0

    def test_missing_keywords_return_zero(self):
        # Errors are swallowed: the function returns 0.0 instead of raising.
        assert airmass(fits.Header([("RA", 10.0)]), observatory=DEFAULT_OBSERVATORY) == 0.0

    def test_no_observatory_returns_zero(self):
        assert airmass(self._header()) == 0.0


# ---------------------------------------------------------------------------
# src/astrometry.py
# ---------------------------------------------------------------------------


class TestAdaptiveMinSources:
    def test_small_image(self):
        # 800 x 600 = 480_000 px -> small bucket, floor 10
        assert get_adaptive_min_sources((600, 800)) == max(
            10, min(25, 480_000 // 20_000)
        )

    def test_medium_image(self):
        # 2000 x 1500 = 3_000_000 px -> medium bucket, floor 20
        assert get_adaptive_min_sources((1500, 2000)) == max(
            20, min(30, 3_000_000 // 50_000)
        )

    def test_large_image(self):
        # 6000 x 6000 = 36_000_000 px -> large bucket, capped at 50
        assert get_adaptive_min_sources((6000, 6000)) == 50

    def test_tiny_image_hits_floor(self):
        assert get_adaptive_min_sources((100, 100)) == 10


class TestEstimatePixelScaleRobust:
    def test_header_keyword_preferred(self):
        header = fits.Header(
            [("PIXSCALE", 0.9), ("FOCALLEN", 800.0), ("XPIXSZ", 3.91)]
        )
        assert estimate_pixel_scale_robust(header, (1000, 1000)) == pytest.approx(0.9)

    def test_focal_length_fallback(self):
        header = fits.Header([("FOCALLEN", 800.0), ("XPIXSZ", 3.91)])
        scale = estimate_pixel_scale_robust(header, (1000, 1000))
        assert scale == pytest.approx(206 * 3.91 / 800.0, rel=1e-2)

    def test_cd_matrix_fallback(self):
        header = fits.Header([("CD1_1", -1.0 / 3600.0), ("CD2_2", 1.0 / 3600.0)])
        scale = estimate_pixel_scale_robust(header, (1000, 1000))
        assert scale == pytest.approx(1.0, rel=1e-2)

    def test_size_heuristic_for_small_images(self):
        scale = estimate_pixel_scale_robust(fits.Header(), (800, 800))
        assert scale == pytest.approx(4.0)

    def test_size_heuristic_for_large_images(self):
        scale = estimate_pixel_scale_robust(fits.Header(), (6000, 6000))
        assert scale == pytest.approx(1.5)
