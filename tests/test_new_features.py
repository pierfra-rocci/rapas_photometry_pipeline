"""
Non-regression tests for features added in the 1.7.x development cycle:

1. plot_astrocolibri_cutouts() (src/transient.py)
   - Early-return guards
   - Hemisphere-aware survey selection (PanSTARRS / SkyMapper)
   - Magnitude / error column fallback priority
     (select_astrocolibri_magnitude / select_astrocolibri_magnitude_error)
   - PNG filename sanitisation (sanitize_astrocolibri_name)

2. Residuals plot error bar column selection (src/pipeline.py)
   - select_residual_error_column() prefers aperture_mag_err_1_3 (fixed 1.3×
     aperture), then falls back to aperture_mag_err, then to zeros

3. Filter mismatch caption text (src/tools_pipeline.py)
   - format_filter_mismatch_message() no longer includes the verbose
     "Consider updating" suffix

These tests call the real production helpers rather than re-implementing their
logic, so a behavioural change in src/ actually fails the suite.
"""

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ── Stub out optional heavy dependencies before src.transient is imported ─────
# stdpipe transitively imports sip_tpv which requires pkg_resources (setuptools),
# which is absent in this test environment.  Inject MagicMock stand-ins for
# every module in the chain so the import succeeds without the real libraries.
_STUB_MODULES = [
    "stdpipe",
    "stdpipe.pipeline",
    "stdpipe.cutouts",
    "stdpipe.templates",
    "stdpipe.catalogs",
    "stdpipe.photometry",
    "stdpipe.plots",
    "stdpipe.astrometry",
    "sep",
    "sip_tpv",
    "astroquery",
    "astroquery.imcce",
    "streamlit",
]
for _mod in _STUB_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Make sure a cached failed import of src.transient doesn't block us
sys.modules.pop("src.transient", None)


# ─── Shared helpers ──────────────────────────────────────────────────────────

def _make_ac_table(n_sources=3, n_matches=2):
    """Build a minimal photometry DataFrame with Astro-Colibri columns.

    The first *n_matches* rows have a non-null astrocolibri_name; the rest are None.
    """
    names = [f"Src{i}" for i in range(n_matches)] + [None] * (n_sources - n_matches)
    types = [f"Type{i}" for i in range(n_matches)] + [None] * (n_sources - n_matches)
    classes = [f"Class{i}" for i in range(n_matches)] + [None] * (n_sources - n_matches)
    return pd.DataFrame({
        "ra": np.linspace(10.0, 30.0, n_sources),
        "dec": np.linspace(15.0, 45.0, n_sources),
        "xcenter": np.arange(100, 100 + n_sources, dtype=float),
        "ycenter": np.arange(200, 200 + n_sources, dtype=float),
        "psf_mag": np.full(n_sources, 18.5),
        "psf_mag_err": np.full(n_sources, 0.05),
        "aperture_mag_1_3": np.full(n_sources, 18.8),
        "aperture_mag_err_1_3": np.full(n_sources, 0.06),
        "astrocolibri_name": names,
        "astrocolibri_type": types,
        "astrocolibri_classification": classes,
    })


# ─── 1. plot_astrocolibri_cutouts — early-return guards ──────────────────────

class TestPlotAstrocolibriCutoutsGuards:
    """Early-return guard conditions — do not require external libraries."""

    def test_returns_none_for_none_table(self):
        from src.transient import plot_astrocolibri_cutouts

        result = plot_astrocolibri_cutouts(None, None, None, "/tmp", "test")
        assert result is None

    def test_returns_none_for_empty_dataframe(self):
        from src.transient import plot_astrocolibri_cutouts

        result = plot_astrocolibri_cutouts(pd.DataFrame(), None, None, "/tmp", "test")
        assert result is None

    def test_returns_none_when_column_missing(self):
        """Table without the astrocolibri_name column → early return."""
        from src.transient import plot_astrocolibri_cutouts

        df = pd.DataFrame({"ra": [10.0], "dec": [20.0], "psf_mag": [17.5]})
        result = plot_astrocolibri_cutouts(df, None, None, "/tmp", "test")
        assert result is None

    def test_returns_none_when_all_names_are_null(self):
        """All astrocolibri_name values are None → no matches → early return."""
        from src.transient import plot_astrocolibri_cutouts

        df = pd.DataFrame({
            "ra": [10.0, 20.0],
            "dec": [5.0, 10.0],
            "astrocolibri_name": [None, None],
        })
        result = plot_astrocolibri_cutouts(df, None, None, "/tmp", "test")
        assert result is None

    def test_returns_none_when_all_names_are_nan(self):
        """astrocolibri_name column filled with NaN → no matches → early return."""
        from src.transient import plot_astrocolibri_cutouts

        df = pd.DataFrame({
            "ra": [10.0],
            "dec": [5.0],
            "astrocolibri_name": [float("nan")],
        })
        result = plot_astrocolibri_cutouts(df, None, None, "/tmp", "test")
        assert result is None


# ─── 2. plot_astrocolibri_cutouts — hemisphere-aware survey selection ─────────

def _run_patched(dec_center, filter_name="r"):
    """Run plot_astrocolibri_cutouts with all external deps mocked.

    Returns the first positional argument passed to templates.get_hips_image
    (i.e. the survey + filter string), or None if the mock was never called.
    """
    from astropy.io.fits import Header
    from src.transient import plot_astrocolibri_cutouts

    df = _make_ac_table(n_sources=2, n_matches=1)
    # Force dec of the matched source to the requested value
    df.loc[0, "dec"] = dec_center

    image = np.zeros((500, 500))
    header = Header()
    mock_cutout = {"image": np.zeros((25, 25)), "header": header}
    mock_fig = MagicMock()

    with patch("src.transient.st"), \
         patch("src.transient.fix_header", return_value=(header, None)), \
         patch("src.transient.cutouts.get_cutout", return_value=mock_cutout), \
         patch("src.transient.templates.get_hips_image",
               side_effect=Exception("network disabled")) as mock_hips, \
         patch("src.transient.plot_cutout", return_value=mock_fig), \
         patch("src.transient.plt.close"):
        plot_astrocolibri_cutouts(
            df, image, header, "/tmp", "test", filter_name, dec_center
        )
        return mock_hips.call_args


class TestAstrocolibriSurveySelection:
    """PanSTARRS chosen for dec ≥ 0, SkyMapper for dec < 0."""

    def test_panstarrs_for_northern_field(self):
        call_args = _run_patched(dec_center=30.0)
        assert call_args is not None
        assert "PanSTARRS" in call_args.args[0]

    def test_skymapper_for_southern_field(self):
        call_args = _run_patched(dec_center=-20.0)
        assert call_args is not None
        assert "SkyMapper" in call_args.args[0]

    def test_panstarrs_for_equatorial_field(self):
        """dec_center == 0.0 is not < 0, so PanSTARRS must be used."""
        call_args = _run_patched(dec_center=0.0)
        assert call_args is not None
        assert "PanSTARRS" in call_args.args[0]

    def test_filter_name_included_in_survey_string(self):
        """The chosen filter is appended after the survey path."""
        call_args = _run_patched(dec_center=10.0, filter_name="g")
        assert call_args is not None
        survey_arg = call_args.args[0]
        assert survey_arg.endswith("g")

    def test_skymapper_filter_name_appended(self):
        call_args = _run_patched(dec_center=-45.0, filter_name="i")
        assert call_args is not None
        survey_arg = call_args.args[0]
        assert "SkyMapper" in survey_arg
        assert survey_arg.endswith("i")


# ─── 3. plot_astrocolibri_cutouts — PNG filename sanitisation ─────────────────

class TestAstrocolibriSafeFilename:
    """sanitize_astrocolibri_name() produces the PNG filename in production."""

    @staticmethod
    def _safe(name: str) -> str:
        from src.transient import sanitize_astrocolibri_name

        return sanitize_astrocolibri_name(name)

    def test_alphanumeric_name_unchanged(self):
        assert self._safe("GRB20240101A") == "GRB20240101A"

    def test_spaces_replaced_by_underscore(self):
        assert self._safe("SN 2024xyz") == "SN_2024xyz"

    def test_slashes_replaced(self):
        assert self._safe("AT2024/01") == "AT2024_01"

    def test_colons_replaced(self):
        assert self._safe("src:name") == "src_name"

    def test_hyphens_and_underscores_kept(self):
        assert self._safe("src_name-v2") == "src_name-v2"

    def test_all_output_chars_are_safe(self):
        nasty = "Src@Name:Test! #weird"
        result = self._safe(nasty)
        assert all(c.isalnum() or c in "_-" for c in result)

    def test_empty_string(self):
        assert self._safe("") == ""

    def test_saved_png_path_uses_sanitized_name(self, tmp_path):
        """End-to-end: the production save path embeds the sanitized name."""
        from astropy.io.fits import Header

        from src.transient import plot_astrocolibri_cutouts

        df = _make_ac_table(n_sources=1, n_matches=1)
        df.loc[0, "astrocolibri_name"] = "SN 2024/xyz"
        header = Header()
        mock_fig = MagicMock()

        with patch("src.transient.st"), \
             patch("src.transient.fix_header", return_value=(header, None)), \
             patch("src.transient.cutouts.get_cutout",
                   return_value={"image": np.zeros((25, 25)), "header": header}), \
             patch("src.transient.templates.get_hips_image",
                   side_effect=Exception("network disabled")), \
             patch("src.transient.plot_cutout", return_value=mock_fig), \
             patch("src.transient.plt.close"):
            saved = plot_astrocolibri_cutouts(
                df, np.zeros((500, 500)), header, str(tmp_path), "science", "r", 10.0
            )

        assert len(saved) == 1
        assert saved[0].endswith("science_astrocolibri_01_SN_2024_xyz.png")


# ─── 4. plot_astrocolibri_cutouts — magnitude column priority ─────────────────

class TestAstrocolibriMagColumnPriority:
    """Fallback priority: psf_mag → aperture_mag_1_3 → aperture_mag_1_1 → None.

    Drives the real select_astrocolibri_magnitude[_error]() helpers.
    """

    @staticmethod
    def _pick_mag(row: dict):
        from src.transient import select_astrocolibri_magnitude

        return select_astrocolibri_magnitude(row)

    @staticmethod
    def _pick_mag_err(row: dict):
        from src.transient import select_astrocolibri_magnitude_error

        return select_astrocolibri_magnitude_error(row)

    def test_psf_mag_takes_priority(self):
        row = {"psf_mag": 17.5, "aperture_mag_1_3": 17.8, "aperture_mag_1_1": 17.9}
        assert self._pick_mag(row) == pytest.approx(17.5)

    def test_fallback_to_aperture_1_3_when_psf_missing(self):
        row = {"aperture_mag_1_3": 17.8, "aperture_mag_1_1": 17.9}
        assert self._pick_mag(row) == pytest.approx(17.8)

    def test_fallback_to_aperture_1_1_when_1_3_missing(self):
        row = {"aperture_mag_1_1": 18.0}
        assert self._pick_mag(row) == pytest.approx(18.0)

    def test_returns_none_when_all_absent(self):
        row = {"ra": 10.0, "dec": 20.0}
        assert self._pick_mag(row) is None

    def test_skips_nan_psf_mag_and_falls_back(self):
        row = {"psf_mag": float("nan"), "aperture_mag_1_3": 17.8}
        assert self._pick_mag(row) == pytest.approx(17.8)

    def test_skips_none_psf_mag_and_falls_back(self):
        row = {"psf_mag": None, "aperture_mag_1_3": 17.8}
        assert self._pick_mag(row) == pytest.approx(17.8)

    def test_skips_inf_psf_mag_and_falls_back(self):
        row = {"psf_mag": float("inf"), "aperture_mag_1_3": 17.8}
        assert self._pick_mag(row) == pytest.approx(17.8)

    def test_error_columns_follow_same_priority(self):
        row = {"psf_mag_err": 0.05, "aperture_mag_err_1_3": 0.06}
        assert self._pick_mag_err(row) == pytest.approx(0.05)
        assert self._pick_mag_err({"aperture_mag_err_1_3": 0.06}) == pytest.approx(0.06)
        assert self._pick_mag_err({"aperture_mag_err_1_1": 0.07}) == pytest.approx(0.07)
        assert self._pick_mag_err({"ra": 1.0}) is None

    def test_non_numeric_value_is_skipped(self):
        row = {"psf_mag": "not-a-number", "aperture_mag_1_3": 17.8}
        assert self._pick_mag(row) == pytest.approx(17.8)

    def test_cutout_title_uses_selected_magnitude(self):
        """End-to-end: the production title shows the selected magnitude."""
        from astropy.io.fits import Header

        from src.transient import plot_astrocolibri_cutouts

        df = _make_ac_table(n_sources=1, n_matches=1)
        df.loc[0, "psf_mag"] = 17.5
        df.loc[0, "psf_mag_err"] = 0.05
        header = Header()
        mock_fig = MagicMock()

        with patch("src.transient.st"), \
             patch("src.transient.fix_header", return_value=(header, None)), \
             patch("src.transient.cutouts.get_cutout",
                   return_value={"image": np.zeros((25, 25)), "header": header}), \
             patch("src.transient.templates.get_hips_image",
                   side_effect=Exception("network disabled")), \
             patch("src.transient.plot_cutout", return_value=mock_fig) as mock_plot, \
             patch("src.transient.plt.close"):
            plot_astrocolibri_cutouts(
                df, np.zeros((500, 500)), header, "/tmp", "science", "r", 10.0
            )

        title = mock_plot.call_args.kwargs["title"]
        assert "mag=17.50 ± 0.050" in title


# ─── 5. plot_astrocolibri_cutouts — skip rows with invalid coordinates ─────────

class TestAstrocolibriInvalidCoordinates:
    """Rows with NaN or None coordinates must be skipped without crashing."""

    def test_nan_ra_row_is_skipped(self):
        from src.transient import plot_astrocolibri_cutouts

        df = pd.DataFrame({
            "ra": [float("nan")],
            "dec": [20.0],
            "xcenter": [100.0],
            "ycenter": [200.0],
            "astrocolibri_name": ["BadSrc"],
        })
        # Should not raise and should return an empty list (0 saved files)
        from astropy.io.fits import Header

        image = np.zeros((100, 100))
        header = Header()

        with patch("src.transient.st"), \
             patch("src.transient.fix_header", return_value=(header, None)), \
             patch("src.transient.cutouts.get_cutout") as mock_cutout:
            result = plot_astrocolibri_cutouts(
                df, image, header, "/tmp", "test", "r", 20.0
            )
        # get_cutout must never be called for an invalid coordinate row
        mock_cutout.assert_not_called()
        assert result == []

    def test_nan_dec_row_is_skipped(self):
        from src.transient import plot_astrocolibri_cutouts

        df = pd.DataFrame({
            "ra": [83.8],
            "dec": [float("nan")],
            "xcenter": [100.0],
            "ycenter": [200.0],
            "astrocolibri_name": ["BadSrc"],
        })
        from astropy.io.fits import Header

        image = np.zeros((100, 100))
        header = Header()

        with patch("src.transient.st"), \
             patch("src.transient.fix_header", return_value=(header, None)), \
             patch("src.transient.cutouts.get_cutout") as mock_cutout:
            result = plot_astrocolibri_cutouts(
                df, image, header, "/tmp", "test", "r", 10.0
            )
        mock_cutout.assert_not_called()
        assert result == []


# ─── 6. Residuals plot error bar column selection ─────────────────────────────

class TestResidualsErrorBarSelection:
    """Drives the real select_residual_error_column() from src/pipeline.py.

    The residuals plot always prefers aperture_mag_err_1_3 (fixed 1.3× aperture),
    with fallbacks to aperture_mag_err and then zeros.
    """

    @staticmethod
    def _select_aperture_err(matched_table: pd.DataFrame, residuals: np.ndarray):
        from src.pipeline import select_residual_error_column

        return select_residual_error_column(matched_table, residuals)

    def test_uses_aperture_mag_err_1_3_when_present(self):
        residuals = np.array([0.01, -0.02, 0.03])
        table = pd.DataFrame({
            "aperture_mag_err_1_3": [0.05, 0.06, 0.07],
            "aperture_mag_err": [0.99, 0.99, 0.99],  # must NOT be selected
        })
        result = self._select_aperture_err(table, residuals)
        np.testing.assert_array_equal(result, [0.05, 0.06, 0.07])

    def test_fallback_to_generic_aperture_err(self):
        """When aperture_mag_err_1_3 is absent, fall back to aperture_mag_err."""
        residuals = np.array([0.01, -0.02, 0.03])
        table = pd.DataFrame({
            "aperture_mag_err": [0.10, 0.11, 0.12],
        })
        result = self._select_aperture_err(table, residuals)
        np.testing.assert_array_equal(result, [0.10, 0.11, 0.12])

    def test_fallback_to_zeros_when_no_err_columns(self):
        """When neither error column exists, return zeros with the same shape."""
        residuals = np.array([0.01, -0.02, 0.03])
        table = pd.DataFrame({"mag_cat": [17.0, 18.0, 19.0]})
        result = self._select_aperture_err(table, residuals)
        np.testing.assert_array_equal(result, np.zeros(3))

    def test_1_3_column_wins_over_generic(self):
        """Explicit regression: aperture_mag_err_1_3 takes priority even when
        aperture_mag_err has larger values that would be visually obvious."""
        residuals = np.zeros(4)
        err_1_3 = np.array([0.02, 0.03, 0.04, 0.05])
        err_generic = np.full(4, 99.0)
        table = pd.DataFrame({
            "aperture_mag_err_1_3": err_1_3,
            "aperture_mag_err": err_generic,
        })
        result = self._select_aperture_err(table, residuals)
        np.testing.assert_array_equal(result, err_1_3)

    def test_every_branch_returns_ndarray_of_matching_shape(self):
        """Every fallback branch returns a numpy array aligned with residuals."""
        residuals = np.zeros(3)
        for table in (
            pd.DataFrame({"aperture_mag_err_1_3": [0.1, 0.2, 0.3]}),
            pd.DataFrame({"aperture_mag_err": [0.1, 0.2, 0.3]}),
            pd.DataFrame({"other": [1, 2, 3]}),
        ):
            result = self._select_aperture_err(table, residuals)
            assert isinstance(result, np.ndarray)
            assert result.shape == (3,)


# ─── 7. Filter mismatch caption text ─────────────────────────────────────────

class TestFilterWarningText:
    """Drives the real format_filter_mismatch_message() from src/tools_pipeline.py."""

    @staticmethod
    def _build_warning(filter_raw: str, filter_mapped: str) -> str:
        from src.tools_pipeline import format_filter_mismatch_message

        return format_filter_mismatch_message(filter_raw, filter_mapped)

    def test_exact_caption_format(self):
        """The caption text is exactly what pages/app.py displays."""
        assert (
            self._build_warning("G", "phot_g_mean_mag")
            == "Filter in FITS header (G) maps to 'phot_g_mean_mag'."
        )

    def test_warning_contains_raw_filter(self):
        w = self._build_warning("G", "gmag")
        assert "G" in w

    def test_warning_contains_mapped_filter(self):
        w = self._build_warning("G", "gmag")
        assert "gmag" in w

    def test_warning_does_not_suggest_updating(self):
        w = self._build_warning("G", "gmag")
        assert "Consider updating" not in w

    def test_warning_does_not_include_selected_filter(self):
        """The selected_filter variable must not appear in the warning."""
        w = self._build_warning("G", "gmag")
        assert "phot_g_mean_mag" not in w
        assert "but selected filter" not in w

    def test_warning_ends_with_period(self):
        """Warning should be a complete, properly-terminated sentence."""
        w = self._build_warning("R", "rmag")
        assert w.endswith(".")

    def test_various_filter_names(self):
        """Warning format is consistent across different filter names."""
        for raw, mapped in [("R", "rmag"), ("B", "bmag"), ("V", "vmag"), ("I", "imag")]:
            w = self._build_warning(raw, mapped)
            assert raw in w
            assert mapped in w
            assert "Consider updating" not in w
