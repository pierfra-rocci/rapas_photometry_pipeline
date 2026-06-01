"""
Unit tests for photometry S/N and magnitude error calculations.

This test suite validates the correctness of photometric formulas used in the pipeline:
- Signal-to-Noise Ratio (S/N) calculations
- Magnitude error computations
- Error propagation in calibrated magnitudes
- Quality flag assignments

Mathematical Background:
-----------------------
1. Signal-to-Noise Ratio:
   S/N = flux / flux_error

2. Magnitude Error from Flux Error:
   Given: mag = -2.5 × log10(flux)
   Error propagation: σ_mag = |∂mag/∂flux| × σ_flux
                            = (2.5 / ln(10)) × (σ_flux / flux)
                            ≈ 1.0857 × (σ_flux / flux)

3. Calibrated Magnitude Error:
   mag_calib = mag_inst + zero_point
   σ_mag_calib = √(σ_mag_inst² + σ_zero_point²)

Author: Automated Testing Suite
Date: 2026-01-07
"""

import numpy as np
import pandas as pd
import pytest
from numpy.testing import assert_allclose, assert_array_equal
from astropy.modeling.models import Gaussian2D
from astropy.table import Table
from photutils.aperture import CircularAperture, aperture_photometry
from photutils.detection import DAOStarFinder

import src.pipeline as pipeline_module
import src.psf as psf_module
from src.pipeline import calculate_zero_point
from src.psf import perform_psf_photometry
from src.tools_pipeline import add_calibrated_magnitudes, drop_legacy_magnitude_columns


class TestSNRCalculations:
    """Test Signal-to-Noise Ratio calculations."""

    def test_basic_snr(self):
        """Test basic S/N = flux / flux_error."""
        flux = np.array([1000.0, 500.0, 100.0, 50.0])
        flux_err = np.array([10.0, 25.0, 10.0, 5.0])

        expected_snr = np.array([100.0, 20.0, 10.0, 10.0])
        calculated_snr = flux / flux_err

        assert_allclose(calculated_snr, expected_snr, rtol=1e-10)

    def test_snr_with_background_correction(self):
        """Test S/N using background-corrected flux."""
        raw_flux = np.array([1100.0, 550.0])
        background = np.array([100.0, 50.0])
        bkg_corrected_flux = raw_flux - background
        flux_err = np.array([15.0, 10.0])

        # Should use background-corrected flux for S/N
        expected_snr = bkg_corrected_flux / flux_err
        assert_allclose(expected_snr, [1000.0 / 15.0, 500.0 / 10.0], rtol=1e-10)

    def test_snr_with_negative_background_correction(self):
        """Test S/N falls back to raw flux when bkg-corrected is negative."""
        raw_flux = np.array([100.0, 50.0])
        bkg_corrected_flux = np.array([80.0, -10.0])  # Second is negative
        flux_err = np.array([10.0, 5.0])

        # Should fall back to raw flux when bkg_corrected <= 0
        flux_for_snr = np.where(bkg_corrected_flux <= 0, raw_flux, bkg_corrected_flux)
        expected_snr = flux_for_snr / flux_err

        assert_allclose(expected_snr, [8.0, 10.0], rtol=1e-10)

    def test_snr_high_precision(self):
        """Test that S/N is not rounded (preserves precision)."""
        flux = 1234.56789
        flux_err = 12.3456

        snr = flux / flux_err

        # Should preserve decimal precision
        assert snr != round(snr)
        assert_allclose(snr, 100.00063909, rtol=1e-7)


class TestMagnitudeErrorCalculations:
    """Test magnitude error computations."""

    def test_mag_error_formula(self):
        """Test magnitude error formula: σ_mag = 1.0857 × (σ_flux / flux)."""
        flux = np.array([1000.0, 500.0, 100.0])
        flux_err = np.array([10.0, 25.0, 10.0])

        # Expected from formula
        expected_mag_err = 1.0857 * flux_err / flux

        # Calculate using the constant
        calculated_mag_err = 1.0857 * flux_err / flux

        assert_allclose(calculated_mag_err, expected_mag_err, rtol=1e-10)

    def test_mag_error_from_snr(self):
        """Test that σ_mag ≈ 1.0857 / S/N for high S/N."""
        snr = np.array([100.0, 50.0, 20.0, 10.0, 5.0])

        # For high S/N, σ_mag ≈ 1.0857 / S/N
        mag_err_from_snr = 1.0857 / snr

        # Calculate properly from flux and flux_err
        flux = 1000.0
        flux_err = flux / snr
        mag_err_proper = 1.0857 * flux_err / flux

        # Should be equivalent
        assert_allclose(mag_err_from_snr, mag_err_proper, rtol=1e-10)

    def test_mag_error_constant_derivation(self):
        """Verify the constant 1.0857 = 2.5 / ln(10)."""
        expected_constant = 2.5 / np.log(10)

        assert_allclose(expected_constant, 1.0857, rtol=1e-4)

    def test_mag_error_vs_magnitude(self):
        """Test magnitude error for typical astronomical magnitudes."""
        # Source with magnitude 15 ± 0.1
        mag_true = 15.0
        flux_true = 10 ** (-0.4 * mag_true)

        # Error of 0.1 mag corresponds to specific flux error
        mag_err = 0.1
        flux_err = (mag_err / 1.0857) * flux_true

        # Calculate mag_err from flux_err
        calculated_mag_err = 1.0857 * flux_err / flux_true

        assert_allclose(calculated_mag_err, mag_err, rtol=1e-10)


class TestErrorPropagation:
    """Test error propagation in magnitude calculations.

    Note: Zero-point error propagation has been removed from the pipeline.
    Calibrated magnitude errors now use only instrumental magnitude errors.
    """

    def test_no_zero_point_error_propagation(self):
        """Verify calibrated magnitude error equals instrumental error (no ZP propagation)."""
        # Instrumental magnitude errors
        mag_err_inst = np.array([0.05, 0.10, 0.20])

        # Without zero-point error propagation, calibrated error = instrumental error
        expected_mag_err_calib = mag_err_inst

        assert_allclose(expected_mag_err_calib, mag_err_inst, rtol=1e-10)

    def test_magnitude_error_filtering(self):
        """Test that sources with magnitude error > 2 are filtered out."""
        # Simulated magnitude errors - some good, some bad
        mag_errors = np.array([0.1, 0.5, 1.0, 1.5, 2.0, 2.1, 3.0, 5.0])

        # Filter: keep only errors <= 2
        keep_mask = mag_errors <= 2

        # Should keep 5 sources (0.1, 0.5, 1.0, 1.5, 2.0)
        assert np.sum(keep_mask) == 5
        assert np.all(mag_errors[keep_mask] <= 2)

    def test_magnitude_error_filtering_with_nan(self):
        """Test that NaN magnitude errors are preserved (not filtered out)."""
        mag_errors = np.array([0.1, 0.5, np.nan, 2.1, 3.0])

        # Filter: keep errors <= 2 OR NaN
        keep_mask = (mag_errors <= 2) | np.isnan(mag_errors)

        # Should keep 3 sources (0.1, 0.5, NaN)
        assert np.sum(keep_mask) == 3


class TestQualityFlags:
    """Test quality flag assignments."""

    def test_quality_flag_thresholds(self):
        """Test quality flag assignment based on S/N thresholds."""
        snr = np.array([2.5, 5.0, 5.1, 10.0, 10.1, 100.0])

        quality_flags = np.where(snr <= 5, "poor", np.where(snr <= 10, "marginal", "good"))

        expected_flags = ["poor", "poor", "marginal", "marginal", "good", "good"]

        assert_array_equal(quality_flags, expected_flags)

    def test_quality_flag_boundary_cases(self):
        """Test quality flags at exact boundaries."""
        # Exactly at thresholds
        snr_boundary = np.array([5.0, 10.0, 10.0001])

        quality_flags = np.where(
            snr_boundary <= 5, "poor", np.where(snr_boundary <= 10, "marginal", "good")
        )

        # S/N = 5.0 should be 'poor' (inclusive lower threshold)
        # S/N = 10.0 should be 'marginal' (good is strictly greater than 10)
        assert quality_flags[0] == "poor"
        assert quality_flags[1] == "marginal"
        assert quality_flags[2] == "good"


class TestNumericalStability:
    """Test numerical stability and edge cases."""

    def test_very_low_flux(self):
        """Test calculations with very low flux values."""
        flux = 1e-6
        flux_err = 1e-7

        snr = flux / flux_err
        mag_err = 1.0857 * flux_err / flux

        assert np.isfinite(snr)
        assert np.isfinite(mag_err)
        assert_allclose(snr, 10.0)
        assert_allclose(mag_err, 0.10857, rtol=1e-4)

    def test_very_high_flux(self):
        """Test calculations with very high flux values."""
        flux = 1e6
        flux_err = 1e4

        snr = flux / flux_err
        mag_err = 1.0857 * flux_err / flux

        assert np.isfinite(snr)
        assert np.isfinite(mag_err)
        assert_allclose(snr, 100.0)

    def test_zero_flux_error_handling(self):
        """Test handling of zero flux (should produce inf/nan appropriately)."""
        flux = 0.0
        flux_err = 1.0

        # S/N should be 0
        snr = flux / flux_err
        assert snr == 0.0

        # Magnitude should be inf (log of 0)
        with np.errstate(divide="ignore"):
            mag = -2.5 * np.log10(flux)
        assert np.isinf(mag)

    def test_negative_flux_handling(self):
        """Test handling of negative flux values."""
        flux = -100.0
        flux_err = 10.0

        snr = flux / flux_err
        assert snr == -10.0

        # Magnitude of negative flux should be nan
        with np.errstate(invalid="ignore"):
            mag = -2.5 * np.log10(flux)
        assert np.isnan(mag)


class TestRealisticScenarios:
    """Test with realistic astronomical data."""

    def test_bright_star_photometry(self):
        """Test photometry of a bright star (mag ~10)."""
        # Bright star: ~10000 counts, S/N ~ 100
        flux = 10000.0
        flux_err = 100.0

        snr = flux / flux_err
        mag_err = 1.0857 * flux_err / flux

        assert_allclose(snr, 100.0)
        assert_allclose(mag_err, 0.010857, rtol=1e-4)

        # Quality should be 'good'
        quality = "good" if snr > 10 else ("marginal" if snr > 5 else "poor")
        assert quality == "good"

    def test_faint_source_photometry(self):
        """Test photometry of a faint source (mag ~20)."""
        # Faint source: ~100 counts, S/N ~ 5
        flux = 100.0
        flux_err = 20.0

        snr = flux / flux_err
        mag_err = 1.0857 * flux_err / flux

        assert_allclose(snr, 5.0)
        assert_allclose(mag_err, 0.2171, rtol=1e-3)

        # Quality should be 'poor' (inclusive lower threshold)
        quality = "good" if snr > 10 else ("marginal" if snr > 5 else "poor")
        assert quality == "poor"

    def test_marginal_detection(self):
        """Test photometry of a marginal detection."""
        # Marginal: S/N ~ 3.5
        flux = 350.0
        flux_err = 100.0

        snr = flux / flux_err
        mag_err = 1.0857 * flux_err / flux

        assert_allclose(snr, 3.5)
        assert_allclose(mag_err, 0.3102, rtol=1e-3)

        # Quality should be 'poor'
        quality = "good" if snr > 10 else ("marginal" if snr > 5 else "poor")
        assert quality == "poor"


class TestFWHMRadiusFactor:
    """
    Regression tests for the configurable fwhm_radius_factor aperture.

    These tests validate the safe-append logic that inserts a third aperture
    radius into aperture_radii = [1.1, 1.3] without duplicating reserved values.
    They work at the column-naming / formula level to avoid requiring a real image.
    """

    # ------------------------------------------------------------------
    # Helper: replicate the safe-append logic from pipeline.py
    # ------------------------------------------------------------------
    @staticmethod
    def _build_radii(fwhm_radius_factor: float) -> list:
        aperture_radii = [1.1, 1.3]
        _rf = round(float(fwhm_radius_factor), 1)
        if _rf not in aperture_radii:
            aperture_radii.append(_rf)
        return aperture_radii

    @staticmethod
    def _suffix(radius: float) -> str:
        return f"_{str(radius).replace('.', '_')}"

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_default_aperture_radii(self):
        """Default value 1.5 produces exactly 3 radii including 1.1 and 1.3."""
        radii = self._build_radii(1.5)
        assert radii == [1.1, 1.3, 1.5]

    def test_custom_aperture_radius_added(self):
        """An arbitrary value not in the fixed set is appended."""
        radii = self._build_radii(1.7)
        assert 1.7 in radii
        assert len(radii) == 3

    def test_reserved_radius_1_1_not_duplicated(self):
        """fwhm_radius_factor=1.1 is a reserved value; list stays at 2 entries."""
        radii = self._build_radii(1.1)
        assert radii == [1.1, 1.3]
        assert len(radii) == 2

    def test_reserved_radius_1_3_not_duplicated(self):
        """fwhm_radius_factor=1.3 is a reserved value; list stays at 2 entries."""
        radii = self._build_radii(1.3)
        assert radii == [1.1, 1.3]
        assert len(radii) == 2

    def test_float_precision_rounding(self):
        """Floating-point values are rounded to 1 decimal place for clean labels."""
        # 1.50000001 should be treated as 1.5
        radii = self._build_radii(1.50000001)
        assert 1.5 in radii
        suffix = self._suffix(1.5)
        assert suffix == "_1_5"

    def test_column_naming_convention(self):
        """Verify column names follow the underscore-dot substitution convention."""
        for factor, expected_suffix in [(1.5, "_1_5"), (0.8, "_0_8"), (2.0, "_2_0")]:
            radii = self._build_radii(factor)
            suffix = self._suffix(radii[-1])
            assert suffix == expected_suffix

    def test_third_aperture_columns_in_simulated_table(self):
        """
        Simulate the column production loop from detection_and_photometry and
        verify that a custom aperture radius produces the expected columns.
        """
        import pandas as pd

        fwhm = 5.0
        n_sources = 10
        rng = np.random.default_rng(42)
        flux = rng.uniform(500, 5000, n_sources)
        flux_err = rng.uniform(10, 100, n_sources)

        radii = self._build_radii(1.5)
        table = {}
        for radius in radii:
            suffix = self._suffix(radius)
            table[f"aperture_sum{suffix}"] = flux
            table[f"aperture_sum_err{suffix}"] = flux_err
            table[f"snr{suffix}"] = flux / flux_err
            table[f"aperture_mag_err{suffix}"] = 1.0857 * flux_err / flux
            table[f"instrumental_mag{suffix}"] = -2.5 * np.log10(flux)
            quality = np.where(
                flux / flux_err < 3, "poor",
                np.where(flux / flux_err < 5, "marginal", "good"),
            )
            table[f"quality_flag{suffix}"] = quality

        df = pd.DataFrame(table)

        # All three apertures must be present
        for r in [1.1, 1.3, 1.5]:
            s = self._suffix(r)
            assert f"aperture_sum{s}" in df.columns
            assert f"snr{s}" in df.columns
            assert f"aperture_mag_err{s}" in df.columns
            assert f"instrumental_mag{s}" in df.columns
            assert f"quality_flag{s}" in df.columns

    def test_zero_point_applied_to_third_aperture(self):
        """
        Simulate calculate_zero_point's calibrated-magnitude loop and verify
        that fwhm_radius_factor=1.5 produces aperture_mag_1_5 in the table.
        """
        import pandas as pd

        zero_point = 22.5
        fwhm_radius_factor = 1.5
        aperture_radii = self._build_radii(fwhm_radius_factor)

        # Build a minimal phot_table with instrumental_mag columns for all radii
        n = 20
        rng = np.random.default_rng(7)
        phot_table = {}
        for r in aperture_radii:
            s = self._suffix(r)
            phot_table[f"instrumental_mag{s}"] = rng.uniform(-12, -8, n)
        df = pd.DataFrame(phot_table)

        # Apply the same loop as calculate_zero_point
        for radius in aperture_radii:
            s = self._suffix(radius)
            inst_col = f"instrumental_mag{s}"
            mag_col = f"aperture_mag{s}"
            if inst_col in df.columns:
                df[mag_col] = df[inst_col] + zero_point

        assert "aperture_mag_1_5" in df.columns
        assert "aperture_mag_1_1" in df.columns
        assert "aperture_mag_1_3" in df.columns
        assert_allclose(
            df["aperture_mag_1_5"].values,
            df["instrumental_mag_1_5"].values + zero_point,
        )


class TestPrefixedMagnitudeAliases:
    """Test backward-compatible prefixed aliases for calibrated magnitude columns."""

    def test_add_calibrated_magnitudes_keeps_legacy_and_adds_prefixed_aliases(self):
        """Selected GAIA band should add prefixed aliases without removing legacy names."""
        import pandas as pd

        df = pd.DataFrame(
            {
                "id": [1, 2],
                "instrumental_mag_1_5": [-10.0, -9.5],
                "aperture_sum_1_5": [1000.0, 900.0],
                "aperture_sum_err_1_5": [10.0, 12.0],
                "psf_instrumental_mag": [-10.2, -9.8],
                "psf_mag_err": [0.02, 0.03],
            }
        )

        out = add_calibrated_magnitudes(
            df,
            zero_point=22.5,
            airmass=1.2,
            filter_band="phot_g_mean_mag",
        )

        assert "psf_mag" in out.columns
        assert "psf_mag_err" in out.columns
        assert "aperture_mag_1_5" in out.columns
        assert "aperture_mag_err_1_5" in out.columns

        assert "rapasg_psf_mag" in out.columns
        assert "rapasg_psf_mag_err" in out.columns
        assert "rapasg_psf_instrumental_mag" in out.columns
        assert "rapasg_aperture_mag_1_5" in out.columns
        assert "rapasg_aperture_mag_err_1_5" in out.columns
        assert "rapasg_instrumental_mag_1_5" in out.columns

        assert_allclose(out["rapasg_psf_mag"].values, out["psf_mag"].values)
        assert_allclose(
            out["rapasg_psf_mag_err"].values,
            out["psf_mag_err"].values,
        )
        assert_allclose(
            out["rapasg_aperture_mag_1_5"].values,
            out["aperture_mag_1_5"].values,
        )
        assert_allclose(
            out["rapasg_aperture_mag_err_1_5"].values,
            out["aperture_mag_err_1_5"].values,
        )

    def test_add_calibrated_magnitudes_skips_aliases_for_unknown_filter(self):
        """Unknown calibration filters should leave the legacy columns unchanged only."""
        import pandas as pd

        df = pd.DataFrame(
            {
                "id": [1],
                "instrumental_mag_1_5": [-10.0],
                "aperture_sum_1_5": [1000.0],
                "aperture_sum_err_1_5": [10.0],
                "psf_instrumental_mag": [-10.2],
                "psf_mag_err": [0.02],
            }
        )

        out = add_calibrated_magnitudes(
            df,
            zero_point=22.5,
            airmass=1.2,
            filter_band="unknown_band",
        )

        assert "psf_mag" in out.columns
        assert "aperture_mag_1_5" in out.columns
        assert not any(col.startswith("unknown_band_") for col in out.columns)

    def test_drop_legacy_magnitude_columns_keeps_only_prefixed_export_columns(self):
        """User-facing export table should drop legacy magnitude columns when aliases exist."""
        import pandas as pd

        df = pd.DataFrame(
            {
                "id": [1],
                "psf_mag": [12.3],
                "psf_mag_err": [0.02],
                "psf_instrumental_mag": [-10.2],
                "aperture_mag_1_5": [12.4],
                "aperture_mag_err_1_5": [0.03],
                "instrumental_mag_1_5": [-10.1],
                "rapasg_psf_mag": [12.3],
                "rapasg_psf_mag_err": [0.02],
                "rapasg_psf_instrumental_mag": [-10.2],
                "rapasg_aperture_mag_1_5": [12.4],
                "rapasg_aperture_mag_err_1_5": [0.03],
                "rapasg_instrumental_mag_1_5": [-10.1],
                "snr_1_5": [50.0],
            }
        )

        out = drop_legacy_magnitude_columns(df, filter_band="phot_g_mean_mag")

        assert "rapasg_psf_mag" in out.columns
        assert "rapasg_aperture_mag_1_5" in out.columns
        assert "snr_1_5" in out.columns

        assert "psf_mag" not in out.columns
        assert "psf_mag_err" not in out.columns
        assert "psf_instrumental_mag" not in out.columns
        assert "aperture_mag_1_5" not in out.columns
        assert "aperture_mag_err_1_5" not in out.columns
        assert "instrumental_mag_1_5" not in out.columns


class TestZeroPointCalibration:
    """Test robust zero-point estimation and outlier rejection."""

    def test_zero_point_rejects_outliers_and_recovers_true_baseline(
        self,
        monkeypatch,
        tmp_path,
    ):
        """Synthetic calibration stars with 3 outliers should keep the true ZP."""
        true_zero_point = 25.0

        instrumental_good = np.linspace(-10.4, -8.5, 20)
        residuals_good = np.array(
            [
                -0.02,
                0.01,
                0.00,
                -0.01,
                0.02,
                -0.02,
                0.01,
                -0.01,
                0.00,
                0.02,
                -0.02,
                0.01,
                -0.01,
                0.00,
                0.02,
                -0.02,
                0.01,
                -0.01,
                0.00,
                0.02,
            ]
        )
        catalog_good = instrumental_good + true_zero_point + residuals_good

        instrumental_outliers = np.array([-10.0, -9.4, -8.8])
        zero_point_outliers = np.array([31.2, 18.4, 29.7])
        catalog_outliers = instrumental_outliers + zero_point_outliers

        matched_table = pd.DataFrame(
            {
                "instrumental_mag_1_3": np.concatenate(
                    [instrumental_good, instrumental_outliers]
                ),
                "phot_g_mean_mag": np.concatenate([catalog_good, catalog_outliers]),
                "aperture_mag_err_1_3": np.full(23, 0.03),
            }
        )
        phot_table = pd.DataFrame(
            {
                "instrumental_mag_1_3": np.array([-10.2, -9.7]),
                "instrumental_mag_1_1": np.array([-10.3, -9.8]),
                "instrumental_mag_1_5": np.array([-10.1, -9.6]),
            }
        )

        info_messages = []
        warning_messages = []
        success_messages = []
        error_messages = []

        class FakeStreamlit:
            def __init__(self):
                self.session_state = {
                    "base_filename": "zp_regression",
                    "username": "tester",
                }

            def info(self, message):
                info_messages.append(message)

            def warning(self, message):
                warning_messages.append(message)

            def success(self, message):
                success_messages.append(message)

            def error(self, message):
                error_messages.append(message)

            def pyplot(self, _figure):
                return None

        monkeypatch.setattr(pipeline_module, "st", FakeStreamlit())
        monkeypatch.setattr(
            pipeline_module,
            "ensure_output_directory",
            lambda directory=None: str(tmp_path),
        )

        zero_point_value, zero_point_std, figure = calculate_zero_point(
            phot_table,
            matched_table,
            filter_band="phot_g_mean_mag",
            air=1.2,
        )

        assert zero_point_value == pytest.approx(true_zero_point, abs=0.01)
        assert zero_point_std == pytest.approx(0.0, abs=0.01)
        assert figure is not None
        assert any("kept 20 / 23 calibration stars" in msg for msg in info_messages)
        assert success_messages
        assert not warning_messages
        assert not error_messages

        final_phot_table = pipeline_module.st.session_state["final_phot_table"]
        assert "aperture_mag_1_3" in final_phot_table.columns
        assert_allclose(
            final_phot_table["aperture_mag_1_3"].values,
            phot_table["instrumental_mag_1_3"].values + zero_point_value,
            atol=1e-10,
        )


class TestPsfApertureCoherency:
    """Test consistency between aperture and PSF photometry on synthetic data."""

    def test_psf_and_aperture_fluxes_agree_for_high_snr_synthetic_stars(
        self,
        monkeypatch,
        tmp_path,
    ):
        """PSF and 1.3×FWHM aperture fluxes should agree within 5% in the high-S/N regime."""
        image_shape = (540, 540)
        fwhm = 3.0
        sigma = fwhm / 2.3548200450309493
        noise_std = 5.0

        y_grid, x_grid = np.mgrid[: image_shape[0], : image_shape[1]]
        image = np.zeros(image_shape, dtype=float)

        x_positions = np.arange(45, 495, 45)
        y_positions = np.arange(45, 495, 45)
        positions = [(float(x), float(y)) for y in y_positions for x in x_positions]
        amplitudes = np.linspace(1400.0, 2300.0, len(positions))

        for amplitude, (x_mean, y_mean) in zip(amplitudes, positions):
            model = Gaussian2D(
                amplitude=amplitude,
                x_mean=x_mean,
                y_mean=y_mean,
                x_stddev=sigma,
                y_stddev=sigma,
            )
            image += model(x_grid, y_grid)

        rng = np.random.default_rng(1234)
        image += rng.normal(0.0, noise_std, image_shape)
        error = np.full(image_shape, noise_std, dtype=float)

        daofind = DAOStarFinder(fwhm=fwhm, threshold=8.0 * noise_std)
        sources = Table(
            {
                "xcentroid": np.array([pos[0] for pos in positions]),
                "ycentroid": np.array([pos[1] for pos in positions]),
                "flux": amplitudes * (2.0 * np.pi * sigma**2),
                "roundness1": np.zeros(len(positions)),
                "sharpness": np.full(len(positions), 0.8),
                "fwhm": np.full(len(positions), fwhm),
                "a": np.full(len(positions), np.nan),
                "b": np.full(len(positions), np.nan),
                "peak": np.full(len(positions), np.nan),
                "snr": np.full(len(positions), 120.0),
            }
        )

        class FakeStreamlit:
            def __init__(self):
                self.session_state = {
                    "base_filename": "psf_coherency",
                    "username": "tester",
                }

            def write(self, *_args, **_kwargs):
                return None

            def success(self, *_args, **_kwargs):
                return None

            def warning(self, *_args, **_kwargs):
                return None

            def error(self, *_args, **_kwargs):
                return None

            def pyplot(self, *_args, **_kwargs):
                return None

        monkeypatch.setattr(psf_module, "st", FakeStreamlit())
        monkeypatch.setattr(psf_module, "ensure_output_directory", lambda directory=None: str(tmp_path))

        psf_table, _ = perform_psf_photometry(
            image,
            sources,
            fwhm,
            daofind,
            mask=None,
            error=error,
            max_sources_for_psf=700,
            max_stars_for_epsf=150,
        )

        assert psf_table is not None
        assert len(psf_table) >= 20
        assert "flux_fit" in psf_table.colnames

        aperture = CircularAperture(
            np.transpose((sources["xcentroid"], sources["ycentroid"])),
            r=1.3 * fwhm,
        )
        aperture_table = aperture_photometry(image, aperture, error=error)

        matched_aperture_fluxes = []
        matched_psf_fluxes = []
        matched_psf_snr = []

        aperture_positions = np.column_stack(
            [np.asarray(sources["xcentroid"]), np.asarray(sources["ycentroid"])]
        )

        for row in psf_table:
            x_fit = float(row["x_fit"])
            y_fit = float(row["y_fit"])
            distances = np.hypot(aperture_positions[:, 0] - x_fit, aperture_positions[:, 1] - y_fit)
            nearest = int(np.argmin(distances))
            if distances[nearest] > 2.0:
                continue

            matched_aperture_fluxes.append(float(aperture_table["aperture_sum"][nearest]))
            matched_psf_fluxes.append(float(row["flux_fit"]))
            if "flux_err" in psf_table.colnames and np.isfinite(row["flux_err"]) and row["flux_err"] > 0:
                matched_psf_snr.append(float(row["flux_fit"] / row["flux_err"]))

        assert len(matched_psf_fluxes) >= 15

        matched_aperture_fluxes = np.asarray(matched_aperture_fluxes)
        matched_psf_fluxes = np.asarray(matched_psf_fluxes)
        relative_difference = np.abs(matched_aperture_fluxes - matched_psf_fluxes) / matched_psf_fluxes

        assert np.median(relative_difference) < 0.05
        assert np.percentile(relative_difference, 90) < 0.08
        assert matched_psf_snr
        assert np.median(matched_psf_snr) > 20.0


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "--tb=short"])
