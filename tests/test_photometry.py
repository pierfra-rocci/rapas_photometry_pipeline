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
   σ_mag_calib = √(σ_mag_inst² + σ_zero_point² + σ_sys²)

Author: Automated Testing Suite
Date: 2026-01-07
"""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest
from matplotlib.figure import Figure
from numpy.testing import assert_allclose, assert_array_equal
from astropy.modeling.models import Gaussian2D
from astropy.table import Table
from photutils.aperture import CircularAperture, aperture_photometry
from photutils.detection import DAOStarFinder

import src.pipeline as pipeline_module
import src.psf as psf_module
from src.pipeline import (
    calculate_zero_point,
    estimate_effective_gain,
    fit_color_term_correction,
    instrument_uses_driver_gain,
    resolve_effective_gain,
)
from src.psf import compute_encircled_energy, perform_psf_photometry
from src.tools_app import plot_photometry_offsets
from src.tools_pipeline import (
    add_calibrated_magnitudes,
    drop_legacy_magnitude_columns,
    get_filter_prefix,
    resolve_photometric_color_columns,
    apply_color_term_correction,
)


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

    Calibrated magnitude errors combine the instrumental error with the
    zero-point uncertainty: sigma_calib = sqrt(sigma_inst^2 + sigma_zp^2).
    When no zero-point uncertainty is supplied the instrumental error is used
    unchanged (backward-compatible default).
    """

    def test_zero_point_error_propagation_formula(self):
        """Calibrated error = quadrature sum of instrumental and ZP errors."""
        mag_err_inst = np.array([0.05, 0.10, 0.20])
        zero_point_std = 0.03

        expected_mag_err_calib = np.sqrt(mag_err_inst**2 + zero_point_std**2)

        assert_allclose(
            expected_mag_err_calib,
            np.sqrt(mag_err_inst**2 + zero_point_std**2),
            rtol=1e-10,
        )

    def test_add_calibrated_magnitudes_propagates_zero_point_error(self):
        """add_calibrated_magnitudes must add sigma_zp in quadrature."""
        df = pd.DataFrame(
            {
                "id": [1, 2],
                "instrumental_mag_1_3": [-10.0, -9.5],
                "aperture_mag_err_1_3": [0.05, 0.10],
                "psf_instrumental_mag": [-10.1, -9.6],
                "psf_mag_err": [0.04, 0.08],
            }
        )
        zero_point_std = 0.03

        out = add_calibrated_magnitudes(
            df,
            zero_point=22.5,
            airmass=1.2,
            filter_band="phot_g_mean_mag",
            zero_point_std=zero_point_std,
        )

        assert_allclose(
            out["aperture_mag_err_1_3"].values,
            np.sqrt(np.array([0.05, 0.10]) ** 2 + zero_point_std**2),
        )
        assert_allclose(
            out["psf_mag_err"].values,
            np.sqrt(np.array([0.04, 0.08]) ** 2 + zero_point_std**2),
        )
        # Prefixed aliases must carry the propagated error too
        assert_allclose(
            out["rapasg_aperture_mag_err_1_3"].values,
            out["aperture_mag_err_1_3"].values,
        )
        # The random (per-source) and correlated (field-common) parts must be
        # exposed separately, and their quadrature sum must reproduce the total.
        assert_allclose(
            out["aperture_mag_err_1_3_random"].values, [0.05, 0.10]
        )
        assert_allclose(
            out["aperture_mag_err_1_3_correlated"].values,
            [zero_point_std, zero_point_std],
        )
        assert_allclose(
            out["aperture_mag_err_1_3"].values,
            np.hypot(
                out["aperture_mag_err_1_3_random"].values,
                out["aperture_mag_err_1_3_correlated"].values,
            ),
        )
        assert_allclose(out["psf_mag_err_random"].values, [0.04, 0.08])
        assert_allclose(
            out["psf_mag_err_correlated"].values, [zero_point_std, zero_point_std]
        )
        assert_allclose(
            out["rapasg_psf_mag_err_correlated"].values,
            out["psf_mag_err_correlated"].values,
        )
        assert_allclose(
            out["rapasg_aperture_mag_err_1_3_random"].values,
            out["aperture_mag_err_1_3_random"].values,
        )

    def test_add_calibrated_magnitudes_default_keeps_instrumental_error(self):
        """Without a zero-point uncertainty the instrumental error is unchanged."""
        df = pd.DataFrame(
            {
                "id": [1],
                "instrumental_mag_1_3": [-10.0],
                "aperture_mag_err_1_3": [0.05],
            }
        )
        out = add_calibrated_magnitudes(df, zero_point=22.5, airmass=1.2)
        assert_allclose(out["aperture_mag_err_1_3"].values, [0.05])

    def test_add_calibrated_magnitudes_propagates_systematic_floor(self):
        """The systematic floor is added in quadrature with the ZP term."""
        df = pd.DataFrame(
            {
                "id": [1],
                "instrumental_mag_1_3": [-10.0],
                "aperture_mag_err_1_3": [0.05],
                "psf_instrumental_mag": [-10.1],
                "psf_mag_err": [0.04],
            }
        )
        out = add_calibrated_magnitudes(
            df,
            zero_point=22.5,
            airmass=1.2,
            zero_point_std=0.03,
            systematic_floor=0.04,
        )
        assert_allclose(
            out["aperture_mag_err_1_3"].values,
            [np.sqrt(0.05**2 + 0.03**2 + 0.04**2)],
        )
        assert_allclose(
            out["psf_mag_err"].values,
            [np.sqrt(0.04**2 + 0.03**2 + 0.04**2)],
        )

    def test_resolve_systematic_floor_reads_environment(self, monkeypatch):
        """Headless runs can set the floor through RPP_SYSTEMATIC_MAG_ERR_FLOOR."""
        monkeypatch.setenv("RPP_SYSTEMATIC_MAG_ERR_FLOOR", "0.05")
        assert pipeline_module.resolve_systematic_floor() == pytest.approx(0.05)

    def test_resolve_systematic_floor_defaults_to_zero(self, monkeypatch):
        monkeypatch.delenv("RPP_SYSTEMATIC_MAG_ERR_FLOOR", raising=False)
        assert pipeline_module.resolve_systematic_floor() == 0.0

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


class TestApertureCorrection:
    """Test the PSF encircled-energy aperture correction."""

    def test_encircled_energy_of_gaussian_psf(self):
        """Encircled energy should match the analytic Gaussian result."""
        y_grid, x_grid = np.mgrid[-60:61, -60:61]
        sigma = 5.0
        psf = np.exp(-(x_grid**2 + y_grid**2) / (2.0 * sigma**2))

        radii = [sigma, 2.0 * sigma, 3.0 * sigma]
        ee = compute_encircled_energy(psf, oversampling=1, radii_pixels=radii)
        expected = np.array(
            [1.0 - np.exp(-(r**2) / (2.0 * sigma**2)) for r in radii]
        )

        # Whole-pixel inclusion in the PSF sum introduces a small pixelisation
        # error, so the comparison uses a modest tolerance.
        assert_allclose(ee, expected, atol=0.02)
        assert ee[0] < ee[1] < ee[2] < 1.0

    def test_encircled_energy_oversampling_scaling(self):
        """Distances must be divided by the oversampling factor."""
        y_grid, x_grid = np.mgrid[-25:26, -25:26]
        sigma = 5.0
        coarse = np.exp(-(x_grid**2 + y_grid**2) / (2.0 * sigma**2))
        fine = np.repeat(np.repeat(coarse, 2, axis=0), 2, axis=1)

        radius = 2.0 * sigma
        ee_coarse = compute_encircled_energy(coarse, 1, [radius])
        ee_fine = compute_encircled_energy(fine, 2, [radius])

        assert_allclose(ee_coarse, ee_fine, atol=0.02)

    def test_encircled_energy_invalid_input_disables_correction(self):
        """Empty/zero PSFs must not produce a spurious correction."""
        assert_array_equal(
            compute_encircled_energy(np.zeros((5, 5)), 1, [2.0]), [1.0]
        )
        assert_array_equal(compute_encircled_energy(None, 1, [2.0]), [1.0])


class TestGainResolution:
    """Test effective-gain resolution for the per-pixel error map."""

    @staticmethod
    def _patch_streamlit(monkeypatch):
        class FakeStreamlit:
            def warning(self, *_args, **_kwargs):
                return None

            def info(self, *_args, **_kwargs):
                return None

            def success(self, *_args, **_kwargs):
                return None

            def error(self, *_args, **_kwargs):
                return None

        monkeypatch.setattr(pipeline_module, "st", FakeStreamlit())

    def test_gain_taken_from_header(self, monkeypatch):
        self._patch_streamlit(monkeypatch)
        image = np.ones((8, 8))
        assert resolve_effective_gain({"GAIN": 2.5}, image) == 2.5
        assert resolve_effective_gain({"GAIN": "1.8"}, image) == 1.8
        # Explicit conversion keywords are honoured too.
        assert resolve_effective_gain({"CCDGAIN": 4.2}, image) == 4.2

    def test_cvf_conversion_factor_is_inverted(self, monkeypatch):
        """CVF is ADU/e-, so the effective gain must be 1/CVF."""
        self._patch_streamlit(monkeypatch)
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        image = np.ones((8, 8))
        assert resolve_effective_gain({"CVF": 2.5}, image) == pytest.approx(0.4)
        # A present-but-useless CVF must not shadow a valid EGAIN.
        assert resolve_effective_gain({"CVF": 0, "EGAIN": 1.7}, image) == (
            pytest.approx(1.7)
        )

    def test_explicit_gain_keyword_precedes_bare_gain(self, monkeypatch):
        """A driver GAIN must not shadow an explicit e-/ADU keyword."""
        self._patch_streamlit(monkeypatch)
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        image = np.ones((8, 8))
        assert resolve_effective_gain({"GAIN": 56, "EGAIN": 0.77}, image) == (
            pytest.approx(0.77)
        )

    def test_large_but_plausible_gain_is_accepted(self, monkeypatch):
        """The plausible range reaches 100 e-/ADU for big-well cameras."""
        self._patch_streamlit(monkeypatch)
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        image = np.ones((8, 8))
        assert resolve_effective_gain({"GAIN": 45.0}, image) == pytest.approx(45.0)

    def test_out_of_range_gain_is_rejected(self, monkeypatch):
        self._patch_streamlit(monkeypatch)
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        image = np.full((8, 8), 5000.0)
        assert resolve_effective_gain({"GAIN": 500.0}, image) == 1.0
        # CVF is inverted before the range check (1/0.0005 = 2000 e-/ADU).
        assert resolve_effective_gain({"CVF": 0.0005}, image) == 1.0

    def test_gain_fallback_is_not_the_old_heuristic(self, monkeypatch):
        self._patch_streamlit(monkeypatch)
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        # The old fallback (65535 / max(image)) would return ~13 here; the sane
        # fallback treats the data as electrons (gain = 1).
        image = np.full((8, 8), 5000.0)
        assert resolve_effective_gain({}, image) == 1.0
        assert resolve_effective_gain({"GAIN": 0}, image) == 1.0
        assert resolve_effective_gain({"GAIN": -3.0}, image) == 1.0


class TestEffectiveGainEstimation:
    """Photon-transfer gain estimation and the gain-resolution precedence.

    When the FITS ``GAIN`` keyword is missing the pipeline must not silently
    treat the data as electrons: it first asks the user, then the frame itself
    (variance vs signal of source-free sky boxes), and only then falls back to
    ``effective_gain = 1``.
    """

    @staticmethod
    def _photon_transfer_frame(
        gain=2.0, sky_low=300.0, sky_high=1500.0, read_noise=4.0, n=384, seed=11
    ):
        """Frame with a sky gradient and physically consistent noise."""
        rng = np.random.default_rng(seed)
        sky = np.tile(np.linspace(sky_low, sky_high, n), (n, 1))
        image = rng.poisson(sky * gain) / gain
        image += rng.normal(0.0, read_noise, image.shape)
        return image

    @staticmethod
    def _fake_streamlit(gain=None):
        class FakeStreamlit:
            def __init__(self):
                self.session_state = {"analysis_parameters": {}}
                if gain is not None:
                    self.session_state["analysis_parameters"][
                        "effective_gain"
                    ] = gain

            def __getattr__(self, _name):
                return lambda *_args, **_kwargs: None

        return FakeStreamlit()

    def test_gain_recovered_from_a_sky_gradient(self, monkeypatch):
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        image = self._photon_transfer_frame(gain=2.0)
        estimate = estimate_effective_gain(image)
        assert estimate is not None
        assert estimate == pytest.approx(2.0, rel=0.35)

    def test_flat_sky_carries_no_gain_information(self, monkeypatch):
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        rng = np.random.default_rng(3)
        gain = 2.0
        image = rng.poisson(np.full((256, 256), 1000.0 * gain)) / gain
        image += rng.normal(0.0, 4.0, image.shape)
        assert estimate_effective_gain(image) is None

    def test_degenerate_images_are_rejected(self):
        assert estimate_effective_gain(np.ones((8, 8))) is None
        assert estimate_effective_gain(np.ones(100)) is None
        assert estimate_effective_gain(None) is None

    def test_header_gain_wins_over_user_and_frame(self, monkeypatch):
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        monkeypatch.setattr(pipeline_module, "st", self._fake_streamlit(7.5))
        image = self._photon_transfer_frame()
        assert resolve_effective_gain({"GAIN": 1.2}, image) == pytest.approx(1.2)

    def test_valid_egain_is_used_when_gain_is_zero(self, monkeypatch):
        """Real frames write GAIN=0 next to a valid EGAIN (Moravian cameras)."""
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        monkeypatch.setattr(pipeline_module, "st", self._fake_streamlit())
        header = {"GAIN": 0, "EGAIN": 3.12}
        assert resolve_effective_gain(header, self._photon_transfer_frame()) == (
            pytest.approx(3.12)
        )

    def test_driver_camera_gain_is_ignored(self, monkeypatch):
        """A QHY-style GAIN=56 (driver units, not e-/ADU) must be ignored."""
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        monkeypatch.setattr(pipeline_module, "st", self._fake_streamlit())
        flat = np.full((256, 256), 500.0)
        header = {"INSTRUME": "QHY268M", "GAIN": 56}
        assert resolve_effective_gain(header, flat) == 1.0
        # ...but an explicit user request still wins over the ignored GAIN.
        monkeypatch.setattr(pipeline_module, "st", self._fake_streamlit(0.77))
        assert resolve_effective_gain(header, flat) == pytest.approx(0.77)

    def test_driver_camera_explicit_conversion_keyword_wins(self, monkeypatch):
        """EGAIN is authoritative even on a recognised driver-gain camera."""
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        monkeypatch.setattr(pipeline_module, "st", self._fake_streamlit())
        header = {"INSTRUME": "ZWO ASI2600MM Pro", "GAIN": 100, "EGAIN": 0.75}
        assert resolve_effective_gain(header, np.full((64, 64), 500.0)) == (
            pytest.approx(0.75)
        )

    def test_driver_gain_instrument_detection(self):
        """Only driver-gain cameras are recognised (no false positives)."""
        assert instrument_uses_driver_gain({"INSTRUME": "QHY268M"})
        ascom_qhy = {"INSTRUME": "ASCOM_QHYCCD-Cameras-Capture"}
        assert instrument_uses_driver_gain(ascom_qhy)
        assert instrument_uses_driver_gain({"CAMERA": "ZWO ASI2600MM Pro"})
        assert instrument_uses_driver_gain({"DETECTOR": "ASI 174MM"})
        # A CCD and a name that merely contains "ASI" must not match.
        assert not instrument_uses_driver_gain({"INSTRUME": "Moravian C3-61000"})
        assert not instrument_uses_driver_gain({"INSTRUME": "BASIL"})
        assert not instrument_uses_driver_gain({})

    def test_user_gain_used_when_header_is_missing(self, monkeypatch):
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        monkeypatch.setattr(pipeline_module, "st", self._fake_streamlit(3.5))
        image = self._photon_transfer_frame()
        assert resolve_effective_gain({}, image) == pytest.approx(3.5)

    def test_empirical_gain_used_when_user_gain_is_automatic(self, monkeypatch):
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        monkeypatch.setattr(pipeline_module, "st", self._fake_streamlit(0.0))
        image = self._photon_transfer_frame(gain=1.6)
        resolved = resolve_effective_gain({}, image)
        assert resolved == pytest.approx(1.6, rel=0.35)

    def test_environment_gain_is_honoured_for_headless_runs(self, monkeypatch):
        monkeypatch.setenv("RPP_EFFECTIVE_GAIN", "4.0")
        monkeypatch.setattr(pipeline_module, "st", self._fake_streamlit())
        image = self._photon_transfer_frame()
        assert resolve_effective_gain({}, image) == pytest.approx(4.0)

    def test_electrons_are_the_last_resort(self, monkeypatch):
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)
        monkeypatch.setattr(pipeline_module, "st", self._fake_streamlit())
        # A constant frame has no sky gradient, so nothing can be estimated.
        flat = np.full((256, 256), 500.0)
        assert resolve_effective_gain({}, flat) == 1.0
        assert resolve_effective_gain({"GAIN": 0}, flat) == 1.0
        assert resolve_effective_gain({"GAIN": -3.0}, flat) == 1.0


class TestCosmicRayMasking:
    """L.A.Cosmic must be fed a plausible effective gain (real-frame regression).

    A real QHY frame carries ``GAIN=56`` (the 0-100 driver scale, not e-/ADU).
    Fed to ``astroscrappy.detect_cosmics`` as a gain, it predicts a sky noise far
    below the real one and flagged 49% of that frame as cosmic rays; resolving
    the gain first (rejecting the driver value) brought that to 0.01%.
    """

    class FakeStreamlit:
        def __init__(self):
            self.session_state = {"analysis_parameters": {}}
            self.messages = []

        def _log(self, level):
            def _record(message, *args, **kwargs):
                self.messages.append((level, str(message)))

            return _record

        def __getattr__(self, name):
            if name in ("warning", "info", "success", "error"):
                return self._log(name)
            return lambda *a, **k: None

    @staticmethod
    def _noisy_frame(n=512, sky=190.0, gain=1.0, read=4.0, seed=3):
        rng = np.random.default_rng(seed)
        image = rng.poisson(np.full((n, n), sky * gain)) / gain
        return image + rng.normal(0.0, read, (n, n))

    def test_driver_gain_does_not_flag_the_whole_frame(self, monkeypatch):
        import astroscrappy

        fake_st = self.FakeStreamlit()
        monkeypatch.setattr(pipeline_module, "st", fake_st)
        monkeypatch.delenv("RPP_EFFECTIVE_GAIN", raising=False)

        image = self._noisy_frame()
        driver_gain_mask, _ = astroscrappy.detect_cosmics(
            image, gain=56.0, readnoise=6.5, verbose=False
        )
        resolved_mask = pipeline_module.mask_and_remove_cosmic_rays(
            image, {"GAIN": 56.0}
        )

        # The noise model is what breaks it: the driver value flags most of a
        # clean frame, the resolved gain keeps it essentially empty.
        assert driver_gain_mask.mean() > 0.2
        assert resolved_mask.mean() < 0.02

    def test_pre_resolved_gain_is_reused(self, monkeypatch):
        import astroscrappy

        fake_st = self.FakeStreamlit()
        monkeypatch.setattr(pipeline_module, "st", fake_st)
        monkeypatch.setattr(
            pipeline_module,
            "resolve_effective_gain",
            lambda *_args, **_kwargs: pytest.fail("gain should not be resolved twice"),
        )
        detected_gain = {}

        def detect_cosmics(_image, **kwargs):
            detected_gain["value"] = kwargs["gain"]
            return np.zeros((8, 8), dtype=bool), np.zeros((8, 8))

        monkeypatch.setattr(astroscrappy, "detect_cosmics", detect_cosmics)

        # A SATURATE keyword is required: without one the function falls back to
        # 0.90 x frame maximum, which would mask every pixel of a flat frame and
        # is unrelated to what this test checks (gain reuse).
        mask = pipeline_module.mask_and_remove_cosmic_rays(
            np.ones((8, 8)), {"SATURATE": 100.0}, effective_gain=2.5
        )

        assert detected_gain["value"] == 2.5
        assert not mask.any()
        assert not any("GAIN keyword missing" in message for _, message in fake_st.messages)

    def test_unphysical_cr_mask_is_discarded(self, monkeypatch):
        import astroscrappy

        fake_st = self.FakeStreamlit()
        monkeypatch.setattr(pipeline_module, "st", fake_st)
        monkeypatch.setattr(
            astroscrappy,
            "detect_cosmics",
            lambda *a, **k: (np.ones((16, 16), dtype=bool), np.zeros((16, 16))),
        )

        mask = pipeline_module.mask_and_remove_cosmic_rays(np.zeros((16, 16)), {})
        assert not mask.any()
        assert any("not physical" in message for _level, message in fake_st.messages)


class TestUnreliablePhotometryCut:
    """The magnitude-error cut is a last resort, not a quality filter.

    Default cut is ``src.tools_pipeline.MAG_ERR_MAX`` (2 mag): a source is
    dropped only when every method fails, and the cut can be disabled entirely
    with ``mag_err_max=0``.
    """

    @staticmethod
    def _table(aperture_err, psf_err):
        return pd.DataFrame(
            {
                "id": [1, 2, 3],
                "instrumental_mag_1_3": [-10.0, -10.0, -10.0],
                "aperture_mag_err_1_3": aperture_err,
                "psf_instrumental_mag": [-10.0, -10.0, -10.0],
                "psf_mag_err": psf_err,
            }
        )

    def test_source_survives_when_one_method_is_usable(self):
        out = add_calibrated_magnitudes(
            self._table([0.4, 3.0, 0.5], [2.5, 4.0, 0.6]),
            zero_point=22.0,
            airmass=1.0,
        )
        # Source 1 has a good aperture error only, source 3 has both, source 2
        # is unusable in both methods.
        assert list(out["id"]) == [1, 3]

    def test_faint_source_with_1_5_mag_error_is_kept(self):
        """The old 1 mag cut deleted these; 1.5-2 mag is still usable."""
        from src.tools_pipeline import MAG_ERR_MAX

        assert MAG_ERR_MAX == 2.0
        out = add_calibrated_magnitudes(
            self._table([1.5, 2.0, 1.9], [1.6, 2.1, 2.0]),
            zero_point=22.0,
            airmass=1.0,
        )
        assert list(out["id"]) == [1, 3]

    def test_cut_can_be_disabled(self):
        out = add_calibrated_magnitudes(
            self._table([1.5, 8.0, 30.0], [4.0, 9.0, 40.0]),
            zero_point=22.0,
            airmass=1.0,
            mag_err_max=0,
        )
        assert list(out["id"]) == [1, 2, 3]

    def test_custom_threshold_is_honoured(self):
        out = add_calibrated_magnitudes(
            self._table([1.5, 1.5, 0.5], [1.6, 1.6, 0.6]),
            zero_point=22.0,
            airmass=1.0,
            mag_err_max=1.5,
        )
        # 1.5 is not < 1.5, so the first two rows are dropped.
        assert list(out["id"]) == [3]

    def test_rows_without_any_error_information_are_kept(self):
        out = add_calibrated_magnitudes(
            self._table([np.nan] * 3, [np.nan] * 3),
            zero_point=22.0,
            airmass=1.0,
        )
        assert list(out["id"]) == [1, 2, 3]

    def test_error_components_do_not_disable_the_cut(self):
        """*_random / *_correlated columns are components, not methods."""
        table = self._table([3.0, 3.0, 0.5], [4.0, 4.0, 0.6])
        table["aperture_mag_err_1_3_random"] = 0.02
        table["aperture_mag_err_1_3_correlated"] = 0.03
        table["psf_mag_err_random"] = 0.02
        table["psf_mag_err_correlated"] = 0.03
        out = add_calibrated_magnitudes(table, zero_point=22.0, airmass=1.0)
        assert list(out["id"]) == [3]

    def test_apcorr_systematic_does_not_disable_the_cut(self):
        """apcorr_mag_err_* is a systematic, not a photometry method."""
        table = self._table([3.0, 3.0, 0.5], [4.0, 4.0, 0.6])
        table["apcorr_mag_err_1_3"] = 0.02
        out = add_calibrated_magnitudes(table, zero_point=22.0, airmass=1.0)
        # Rows 1 and 2 are unusable in every real method; the small aperture
        # systematic must not keep them.
        assert list(out["id"]) == [3]


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

    def test_psf_minus_aperture_magnitude_columns(self):
        """Per-source PSF-minus-aperture magnitudes are exported per radius."""
        df = pd.DataFrame(
            {
                "id": [1, 2, 3],
                "instrumental_mag_1_1": [-10.0, -10.0, -10.0],
                "instrumental_mag_1_3": [-10.5, -10.5, -10.5],
                "aperture_sum_1_1": [1000.0, 900.0, 800.0],
                "aperture_sum_err_1_1": [10.0, 12.0, 14.0],
                "aperture_sum_1_3": [1200.0, 1100.0, 1000.0],
                "aperture_sum_err_1_3": [11.0, 13.0, 15.0],
                "psf_instrumental_mag": [-10.2, -10.4, np.nan],
                "psf_mag_err": [0.02, 0.03, 0.04],
            }
        )
        out = add_calibrated_magnitudes(
            df, zero_point=22.0, airmass=1.0, filter_band="phot_g_mean_mag"
        )

        for radius_label in ("1_1", "1_3"):
            col = f"psf_minus_aperture_mag_{radius_label}"
            assert col in out.columns
            expected = out["psf_mag"] - out[f"aperture_mag_{radius_label}"]
            assert_allclose(out[col].values[:2], expected.values[:2])
            # Prefixed aliases must carry the same values.
            prefixed = f"rapasg_{col}"
            assert prefixed in out.columns
            assert_allclose(out[prefixed].values, out[col].values)

        # The PSF-only source (NaN PSF) has no difference.
        assert np.isnan(out["psf_minus_aperture_mag_1_3"].values[2])

    def test_drop_legacy_magnitude_columns_drops_unprefixed_difference(self):
        df = pd.DataFrame(
            {
                "id": [1],
                "psf_mag": [12.0],
                "psf_mag_err": [0.02],
                "psf_instrumental_mag": [-10.0],
                "aperture_mag_1_3": [12.1],
                "aperture_mag_err_1_3": [0.03],
                "instrumental_mag_1_3": [-10.1],
                "psf_minus_aperture_mag_1_3": [-0.1],
                "rapasg_psf_mag": [12.0],
                "rapasg_psf_mag_err": [0.02],
                "rapasg_psf_instrumental_mag": [-10.0],
                "rapasg_aperture_mag_1_3": [12.1],
                "rapasg_aperture_mag_err_1_3": [0.03],
                "rapasg_instrumental_mag_1_3": [-10.1],
                "rapasg_psf_minus_aperture_mag_1_3": [-0.1],
            }
        )
        out = drop_legacy_magnitude_columns(df, filter_band="phot_g_mean_mag")

        assert "rapasg_psf_minus_aperture_mag_1_3" in out.columns
        assert "psf_minus_aperture_mag_1_3" not in out.columns


class TestColorTermCalibration:
    """Test the standalone color-term calibration helpers."""

    def test_resolve_photometric_color_columns_for_supported_filters(self):
        gaia_config = resolve_photometric_color_columns("phot_g_mean_mag")
        assert gaia_config["supported"] is True
        assert gaia_config["color_label"] == "BP-RP"

        sloan_config = resolve_photometric_color_columns("imag")
        assert sloan_config["supported"] is True
        assert sloan_config["color_label"] == "r-i"

        unsupported_config = resolve_photometric_color_columns("v_jkc_mag")
        assert unsupported_config["supported"] is False

    def test_fit_color_term_recovers_known_linear_coefficient(self):
        n_sources = 24
        instrumental_mag = np.linspace(-10.2, -8.1, n_sources)
        rp_mag = np.linspace(13.2, 14.0, n_sources)
        color = np.linspace(0.2, 1.4, n_sources)
        bp_mag = rp_mag + color
        zero_point = 25.0
        true_color_term = 0.18
        color_ref = np.median(color)
        catalog_mag = instrumental_mag + zero_point + true_color_term * (color - color_ref)

        matched_table = pd.DataFrame(
            {
                "instrumental_mag_1_3": instrumental_mag,
                "phot_g_mean_mag": catalog_mag,
                "phot_bp_mean_mag": bp_mag,
                "phot_rp_mean_mag": rp_mag,
            }
        )

        result = fit_color_term_correction(
            matched_table,
            filter_band="phot_g_mean_mag",
            zero_point=zero_point,
        )

        assert result["supported"] is True
        assert result["fitted"] is True
        assert result["n_used"] == n_sources
        assert result["color_term"] == pytest.approx(true_color_term, abs=1e-6)
        assert result["intercept"] == pytest.approx(0.0, abs=1e-6)
        assert result["diagnostic_figure"] is not None

    def test_apply_color_term_correction_appends_new_columns_and_keeps_legacy(self):
        final_table = pd.DataFrame(
            {
                "id": [1, 2],
                "psf_mag": [14.1, 15.2],
                "psf_mag_err": [0.05, 0.10],
                "aperture_mag_1_3": [14.0, 15.1],
                "aperture_mag_err_1_3": [0.06, 0.11],
                "calib_gmag": [14.6, 15.7],
                "calib_rmag": [14.2, 15.1],
            }
        )

        covariance = [[0.0004, 0.0001], [0.0001, 0.0009]]
        color_term_result = {
            "fitted": True,
            "color_label": "g-r",
            "color_term": 0.2,
            "color_ref": 0.4,
            "intercept": 0.01,
            "parameter_covariance": covariance,
        }

        corrected = apply_color_term_correction(
            final_table,
            color_term_result,
            filter_band="rmag",
        )

        expected_color = final_table["calib_gmag"] - final_table["calib_rmag"]
        expected_offset = 0.01 + 0.2 * (expected_color - 0.4)
        color_centered = expected_color.values - 0.4
        cov = np.asarray(covariance)
        expected_offset_err = np.sqrt(
            cov[0, 0]
            + 2.0 * color_centered * cov[0, 1]
            + color_centered**2 * cov[1, 1]
        )

        assert "psf_mag" in corrected.columns
        assert "aperture_mag_1_3" in corrected.columns
        assert "psf_mag_colorcorr" in corrected.columns
        assert "aperture_mag_1_3_colorcorr" in corrected.columns
        assert "r_psf_mag_colorcorr" in corrected.columns
        assert "r_aperture_mag_1_3_colorcorr" in corrected.columns
        assert_allclose(corrected["source_color"].values, expected_color.values)
        assert_allclose(corrected["color_term_offset"].values, expected_offset.values)
        assert_allclose(
            corrected["psf_mag_colorcorr"].values,
            (final_table["psf_mag"] + expected_offset).values,
        )
        assert_allclose(
            corrected["r_psf_mag_colorcorr"].values,
            corrected["psf_mag_colorcorr"].values,
        )

        # The color-term fit uncertainty is propagated into every corrected error.
        assert_allclose(
            corrected["color_term_offset_err"].values, expected_offset_err
        )
        assert_allclose(
            corrected["psf_mag_colorcorr_err"].values,
            np.sqrt(np.array([0.05, 0.10]) ** 2 + expected_offset_err**2),
        )
        assert_allclose(
            corrected["aperture_mag_1_3_colorcorr_err"].values,
            np.sqrt(np.array([0.06, 0.11]) ** 2 + expected_offset_err**2),
        )
        assert_allclose(
            corrected["r_psf_mag_colorcorr_err"].values,
            corrected["psf_mag_colorcorr_err"].values,
        )

    def test_colorcorr_err_without_baseline_error_is_fit_only(self):
        """Without a baseline error column the corrected error is the fit term."""
        final_table = pd.DataFrame(
            {
                "id": [1],
                "psf_mag": [14.1],
                "calib_gmag": [14.6],
                "calib_rmag": [14.2],
            }
        )
        covariance = [[0.0004, 0.0], [0.0, 0.0009]]
        corrected = apply_color_term_correction(
            final_table,
            {
                "fitted": True,
                "color_label": "g-r",
                "color_term": 0.2,
                "color_ref": 0.4,
                "intercept": 0.01,
                "parameter_covariance": covariance,
            },
            filter_band="rmag",
        )
        assert_allclose(
            corrected["psf_mag_colorcorr_err"].values,
            corrected["color_term_offset_err"].values,
        )

    def test_fit_color_term_reports_parameter_uncertainties(self):
        n_sources = 30
        instrumental_mag = np.linspace(-10.5, -8.0, n_sources)
        rp_mag = np.linspace(13.0, 14.2, n_sources)
        color = np.linspace(0.1, 1.5, n_sources)
        bp_mag = rp_mag + color
        zero_point = 25.0
        color_ref = np.median(color)
        rng = np.random.default_rng(11)
        catalog_mag = (
            instrumental_mag
            + zero_point
            + 0.18 * (color - color_ref)
            + rng.normal(0.0, 0.01, n_sources)
        )

        matched_table = pd.DataFrame(
            {
                "instrumental_mag_1_3": instrumental_mag,
                "phot_g_mean_mag": catalog_mag,
                "phot_bp_mean_mag": bp_mag,
                "phot_rp_mean_mag": rp_mag,
            }
        )
        result = fit_color_term_correction(
            matched_table, filter_band="phot_g_mean_mag", zero_point=zero_point
        )

        assert result["parameter_covariance"] is not None
        covariance = np.asarray(result["parameter_covariance"])
        assert covariance.shape == (2, 2)
        assert result["intercept_std"] == pytest.approx(np.sqrt(covariance[0, 0]))
        assert result["color_term_std"] == pytest.approx(np.sqrt(covariance[1, 1]))
        assert result["color_term_std"] > 0.0

    def test_drop_legacy_magnitude_columns_keeps_unprefixed_colorcorr_when_alias_exists(self):
        df = pd.DataFrame(
            {
                "id": [1],
                "psf_mag": [12.3],
                "psf_mag_colorcorr": [12.25],
                "psf_mag_colorcorr_err": [0.05],
                "aperture_mag_1_5": [12.4],
                "aperture_mag_1_5_colorcorr": [12.35],
                "aperture_mag_1_5_colorcorr_err": [0.06],
                "rapasg_psf_mag": [12.3],
                "rapasg_psf_mag_colorcorr": [12.25],
                "rapasg_psf_mag_colorcorr_err": [0.05],
                "rapasg_aperture_mag_1_5": [12.4],
                "rapasg_aperture_mag_1_5_colorcorr": [12.35],
                "rapasg_aperture_mag_1_5_colorcorr_err": [0.06],
            }
        )

        out = drop_legacy_magnitude_columns(df, filter_band="phot_g_mean_mag")

        # Colour-corrected columns (magnitude and error) are written in parallel,
        # so both the legacy and the prefixed forms are kept.
        for column in (
            "psf_mag_colorcorr",
            "psf_mag_colorcorr_err",
            "aperture_mag_1_5_colorcorr",
            "aperture_mag_1_5_colorcorr_err",
            "rapasg_psf_mag_colorcorr",
            "rapasg_psf_mag_colorcorr_err",
            "rapasg_aperture_mag_1_5_colorcorr",
            "rapasg_aperture_mag_1_5_colorcorr_err",
        ):
            assert column in out.columns, f"{column} was unexpectedly dropped"

        # The baseline (non-colour) legacy magnitudes are still replaced.
        assert "psf_mag" not in out.columns
        assert "aperture_mag_1_5" not in out.columns

    def test_apply_color_term_correction_skips_when_color_is_missing(self):
        final_table = pd.DataFrame(
            {
                "id": [1],
                "psf_mag": [14.1],
            }
        )

        corrected = apply_color_term_correction(
            final_table,
            {
                "fitted": True,
                "color_label": "BP-RP",
                "color_term": 0.1,
                "color_ref": 0.8,
                "intercept": 0.0,
            },
            filter_band="phot_g_mean_mag",
        )

        assert list(corrected.columns) == list(final_table.columns)


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

    def test_zero_point_uncertainty_uses_untrimmed_scatter(self, monkeypatch, tmp_path):
        """The |residual| <= 0.5 mag cut truncates the scatter; sigma_zp must use
        the sigma-clipped (untrimmed) scatter instead."""
        true_zero_point = 25.0
        n = 80
        rng = np.random.default_rng(17)
        instrumental = np.linspace(-10.5, -8.0, n)
        residuals = rng.normal(0.0, 0.35, n)
        catalog = instrumental + true_zero_point + residuals

        matched_table = pd.DataFrame(
            {
                "instrumental_mag_1_3": instrumental,
                "phot_g_mean_mag": catalog,
                "aperture_mag_err_1_3": np.full(n, 0.03),
            }
        )
        phot_table = pd.DataFrame({"instrumental_mag_1_3": instrumental[:2]})

        class FakeStreamlit:
            def __init__(self):
                self.session_state = {
                    "base_filename": "zp_untrimmed",
                    "username": "tester",
                }

            def __getattr__(self, _name):
                return lambda *_args, **_kwargs: None

        monkeypatch.setattr(pipeline_module, "st", FakeStreamlit())
        monkeypatch.setattr(
            pipeline_module,
            "ensure_output_directory",
            lambda directory=None: str(tmp_path),
        )

        _, zero_point_std, _ = calculate_zero_point(
            phot_table, matched_table, filter_band="phot_g_mean_mag", air=1.0
        )

        diagnostics = pipeline_module.st.session_state["zero_point_diagnostics"]
        # The hard cut must actually truncate the distribution on this sample.
        assert diagnostics["scatter"] > diagnostics["scatter_kept"]
        expected = (
            pipeline_module.MEDIAN_STD_ERROR_FACTOR
            * diagnostics["scatter"]
            / np.sqrt(diagnostics["n_cal"])
        )
        assert zero_point_std == pytest.approx(expected, rel=1e-9)


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

    def test_psf_local_background_recovers_flux_with_constant_sky(
        self, monkeypatch, tmp_path
    ):
        """A constant local sky must not bias the fitted PSF flux."""
        image_shape = (500, 500)
        fwhm = 3.0
        sigma = fwhm / 2.3548200450309493
        noise_std = 4.0
        sky = 300.0
        amplitude = 1600.0
        true_flux = amplitude * 2.0 * np.pi * sigma**2

        rng = np.random.default_rng(2025)
        y_grid, x_grid = np.mgrid[: image_shape[0], : image_shape[1]]
        image = np.full(image_shape, sky, dtype=float)
        x_positions = np.arange(40, 460, 40)
        y_positions = np.arange(40, 460, 40)
        positions = [(float(x), float(y)) for y in y_positions for x in x_positions]
        for x_mean, y_mean in positions:
            image += Gaussian2D(
                amplitude=amplitude,
                x_mean=x_mean,
                y_mean=y_mean,
                x_stddev=sigma,
                y_stddev=sigma,
            )(x_grid, y_grid)
        image += rng.normal(0.0, noise_std, image_shape)
        error = np.full(image_shape, noise_std, dtype=float)

        daofind = DAOStarFinder(fwhm=fwhm, threshold=8.0 * noise_std)
        sources = Table(
            {
                "xcentroid": np.array([pos[0] for pos in positions]),
                "ycentroid": np.array([pos[1] for pos in positions]),
                "flux": np.full(len(positions), true_flux),
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
                    "base_filename": "psf_local_bkg",
                    "username": "tester",
                }

            def __getattr__(self, _name):
                return lambda *_args, **_kwargs: None

        monkeypatch.setattr(psf_module, "st", FakeStreamlit())
        monkeypatch.setattr(
            psf_module,
            "ensure_output_directory",
            lambda directory=None: str(tmp_path),
        )

        enabled, _ = perform_psf_photometry(
            image,
            sources,
            fwhm,
            daofind,
            mask=None,
            error=error,
            max_sources_for_psf=700,
            max_stars_for_epsf=150,
        )
        disabled, _ = perform_psf_photometry(
            image,
            sources,
            fwhm,
            daofind,
            mask=None,
            error=error,
            max_sources_for_psf=700,
            max_stars_for_epsf=150,
            local_bkg_inner_factor=0.0,
        )

        assert enabled is not None and disabled is not None

        def _median_flux_ratio(table):
            ratios = [
                float(row["flux_fit"]) / true_flux
                for row in table
                if np.isfinite(row["flux_fit"]) and row["flux_fit"] > 0
            ]
            return float(np.median(ratios)), len(ratios)

        ratio_enabled, n_enabled = _median_flux_ratio(enabled)
        ratio_disabled, n_disabled = _median_flux_ratio(disabled)
        assert n_enabled > 20 and n_disabled > 20
        # Without a local-background term the sky pedestal biases the fitted flux
        # high by >30%. With both the ePSF cutouts de-contaminated and the
        # per-source local background subtracted, the recovered flux must match
        # the true total flux to a few percent.
        assert ratio_disabled > 1.3, ratio_disabled
        assert ratio_enabled == pytest.approx(1.0, abs=0.05), ratio_enabled

    def test_psf_photometry_runs_on_a_moderate_bright_field(
        self, monkeypatch, tmp_path
    ):
        """A clean field with a narrow luminosity range must not abort PSF photometry.

        Regression: the star-selection filters (adaptive S/N derived from
        ``sqrt(flux)``, the 85th-percentile saturation cut and the median..90th
        flux window) used to leave fewer than the minimum 15 candidate stars on
        such a field, silently skipping the whole PSF step and dropping every PSF
        magnitude. This checks that PSF photometry runs on all detected sources
        and that the fitted ePSF is centred.
        """
        image_shape = (540, 540)
        fwhm = 3.0
        sigma = fwhm / 2.3548200450309493
        noise_std = 4.0

        y_grid, x_grid = np.mgrid[: image_shape[0], : image_shape[1]]
        image = np.zeros(image_shape, dtype=float)
        x_positions = np.arange(45, 495, 45)
        y_positions = np.arange(45, 495, 45)
        positions = [(float(x), float(y)) for y in y_positions for x in x_positions]
        amplitudes = np.linspace(1600.0, 2600.0, len(positions))
        for amplitude, (x_mean, y_mean) in zip(amplitudes, positions):
            image += Gaussian2D(
                amplitude=amplitude,
                x_mean=x_mean,
                y_mean=y_mean,
                x_stddev=sigma,
                y_stddev=sigma,
            )(x_grid, y_grid)

        rng = np.random.default_rng(99)
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
                # No 'snr'/'flux_err' column: the pipeline must fall back to the
                # sqrt(flux) approximation, which is exactly the path that used
                # to trigger the over-aggressive percentile cut.
            }
        )

        class FakeStreamlit:
            def __init__(self):
                self.session_state = {
                    "base_filename": "psf_moderate",
                    "username": "tester",
                }

            def __getattr__(self, _name):
                return lambda *_args, **_kwargs: None

        monkeypatch.setattr(psf_module, "st", FakeStreamlit())
        monkeypatch.setattr(
            psf_module,
            "ensure_output_directory",
            lambda directory=None: str(tmp_path),
        )

        psf_table, epsf = perform_psf_photometry(
            image, sources, fwhm, daofind, mask=None, error=error
        )

        assert psf_table is not None, "PSF photometry must not be skipped"
        assert epsf is not None
        # PSF photometry is performed on ALL detected sources, not only the
        # subset used to build the ePSF.
        assert len(psf_table) == len(positions)
        flux = np.asarray(psf_table["flux_fit"], dtype=float)
        assert np.isfinite(flux).sum() == len(positions)

        # The fitted ePSF must be centred on its grid: (N - 1) / 2, not N / 2.
        data = np.asarray(epsf.data)
        peak = np.unravel_index(int(np.argmax(data)), data.shape)
        center = (np.asarray(data.shape, dtype=float) - 1.0) / 2.0
        assert np.allclose(peak, center, atol=1.0)


class TestPsfApertureDiagnostic:
    """Unit tests for the PSF-vs-aperture consistency diagnostic."""

    class FakeStreamlit:
        def __init__(self):
            self.warnings = []
            self.infos = []

        def warning(self, message, *_args, **_kwargs):
            self.warnings.append(str(message))

        def info(self, message, *_args, **_kwargs):
            self.infos.append(str(message))

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

    @staticmethod
    def _tables(n=40, offset=0.0, seed=0):
        rng = np.random.default_rng(seed)
        x = np.linspace(50.0, 450.0, n)
        y = np.linspace(50.0, 450.0, n)
        aperture_mag = rng.normal(-10.0, 0.01, n)
        phot_table = Table(
            {
                "xcenter": x,
                "ycenter": y,
                "instrumental_mag_1_3": aperture_mag,
            }
        )
        epsf_table = Table(
            {
                "x_init": x,
                "y_init": y,
                "instrumental_mag": aperture_mag + offset,
            }
        )
        return phot_table, epsf_table

    def test_large_offset_is_flagged_as_outlier(self, monkeypatch):
        fake = self.FakeStreamlit()
        monkeypatch.setattr(pipeline_module, "st", fake)

        phot_table, epsf_table = self._tables(offset=0.4)
        diag = pipeline_module.check_psf_aperture_consistency(
            phot_table, epsf_table, [1.3], warn_threshold=0.15
        )

        assert diag["outliers"] == ["_1_3"]
        entry = diag["per_aperture"]["_1_3"]
        assert entry["n"] == len(phot_table)
        assert entry["median_diff"] == pytest.approx(0.4, abs=0.02)
        assert fake.warnings and "disagree" in fake.warnings[0]

    def test_agreement_does_not_warn(self, monkeypatch):
        fake = self.FakeStreamlit()
        monkeypatch.setattr(pipeline_module, "st", fake)

        phot_table, epsf_table = self._tables(offset=0.0)
        diag = pipeline_module.check_psf_aperture_consistency(
            phot_table, epsf_table, [1.3], warn_threshold=0.15
        )

        assert diag["outliers"] == []
        assert diag["per_aperture"]["_1_3"]["median_diff"] == pytest.approx(
            0.0, abs=0.02
        )
        assert not fake.warnings
        assert fake.infos

    def test_unmatched_and_missing_inputs_are_tolerated(self, monkeypatch):
        fake = self.FakeStreamlit()
        monkeypatch.setattr(pipeline_module, "st", fake)

        # No PSF table at all
        diag = pipeline_module.check_psf_aperture_consistency(
            Table({"xcenter": [1.0]}), None, [1.3]
        )
        assert diag["per_aperture"] == {}
        assert diag["outliers"] == []

        # No position column on the PSF side -> graceful no-op
        phot_table, _ = self._tables(n=12)
        epsf_table = Table({"instrumental_mag": np.zeros(12)})
        diag = pipeline_module.check_psf_aperture_consistency(
            phot_table, epsf_table, [1.3]
        )
        assert diag["per_aperture"] == {}

        # Fewer than min_matches valid pairs -> not reported
        phot_table, epsf_table = self._tables(n=5)
        diag = pipeline_module.check_psf_aperture_consistency(
            phot_table, epsf_table, [1.3], min_matches=10
        )
        assert diag["per_aperture"]["_1_3"]["median_diff"] is None


class TestPhotometryOffsetPlot:
    """Unit tests for the PSF-minus-aperture offset diagnostic figure."""

    @staticmethod
    def _table(offset=0.0, n=80, seed=1):
        rng = np.random.default_rng(seed)
        offsets = rng.normal(offset, 0.05, n)
        return pd.DataFrame(
            {
                "psf_mag": rng.normal(15.0, 1.0, n),
                "psf_minus_aperture_mag_1_3": offsets,
                "psf_minus_aperture_mag_2_0": offsets + 0.01,
            }
        )

    def test_returns_two_panel_figure(self):
        fig = plot_photometry_offsets(self._table())
        try:
            assert isinstance(fig, Figure)
            assert len(fig.axes) == 2
        finally:
            plt.close(fig)

    def test_median_line_tracks_known_offset(self):
        fig = plot_photometry_offsets(self._table(offset=0.35))
        try:
            labels = fig.axes[0].get_legend_handles_labels()[1]
            assert any("+0.35" in label for label in labels)
        finally:
            plt.close(fig)

    def test_legacy_and_prefixed_columns_are_deduplicated(self):
        table = self._table()
        table["rapasg_psf_minus_aperture_mag_1_3"] = table[
            "psf_minus_aperture_mag_1_3"
        ]
        fig = plot_photometry_offsets(table)
        try:
            hist_labels = fig.axes[1].get_legend_handles_labels()[1]
            # One entry per radius, not per column name.
            assert sum("\u00d7FWHM" in label for label in hist_labels) == 2
        finally:
            plt.close(fig)

    def test_missing_columns_render_placeholder(self):
        fig = plot_photometry_offsets(pd.DataFrame({"psf_mag": [15.0, 16.0]}))
        try:
            texts = [t.get_text() for ax in fig.axes for t in ax.texts]
            assert any("No PSF/aperture offset data available" in t for t in texts)
        finally:
            plt.close(fig)

    def test_all_nan_offsets_render_placeholder(self):
        table = pd.DataFrame({"psf_minus_aperture_mag_1_3": [np.nan] * 3})
        fig = plot_photometry_offsets(table)
        try:
            texts = [t.get_text() for ax in fig.axes for t in ax.texts]
            assert any("No finite PSF/aperture offsets" in t for t in texts)
        finally:
            plt.close(fig)


class TestPipelineErrorBudget:
    """End-to-end check of the S/N and error budget in detection_and_photometry."""

    @staticmethod
    def _make_image(
        n=320,
        background=200.0,
        noise=5.0,
        fwhm=3.0,
        spacing=40,
        amplitude=900.0,
        amplitude_span=0.15,
        amplitude_range=None,
    ):
        sigma = fwhm / (2.0 * np.sqrt(2.0 * np.log(2.0)))
        image = np.full((n, n), float(background))
        y_grid, x_grid = np.mgrid[:n, :n]
        rng = np.random.default_rng(2024)

        margin = spacing
        for iy, y0 in enumerate(range(margin, n - margin, spacing)):
            for ix, x0 in enumerate(range(margin, n - margin, spacing)):
                if amplitude_range is not None:
                    star_amplitude = float(
                        rng.uniform(amplitude_range[0], amplitude_range[1])
                    )
                else:
                    star_amplitude = amplitude * (
                        1.0 + amplitude_span * ((ix + iy) % 3)
                    )
                model = Gaussian2D(
                    amplitude=star_amplitude,
                    x_mean=float(x0),
                    y_mean=float(y0),
                    x_stddev=sigma,
                    y_stddev=sigma,
                )
                image += model(x_grid, y_grid)

        image += rng.normal(0.0, noise, image.shape)
        return image

    @staticmethod
    def _make_header(image_shape):
        from astropy.wcs import WCS

        wcs = WCS(naxis=2)
        wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        wcs.wcs.crval = [150.0, 2.0]
        wcs.wcs.crpix = [image_shape[1] / 2.0, image_shape[0] / 2.0]
        wcs.wcs.cdelt = [-1.8 / 3600.0, 1.8 / 3600.0]
        header = wcs.to_header()
        header["GAIN"] = (1.5, "e-/ADU")
        header["NAXIS"] = 2
        header["NAXIS1"] = image_shape[1]
        header["NAXIS2"] = image_shape[0]
        return header

    def test_error_budget_and_aperture_correction(self, monkeypatch):
        image = self._make_image()
        header = self._make_header(image.shape)

        class FakeStreamlit:
            def __init__(self):
                self.session_state = {"base_filename": "budget", "username": "tester"}

            def __getattr__(self, _name):
                return lambda *_args, **_kwargs: None

        monkeypatch.setattr(pipeline_module, "st", FakeStreamlit())
        # Keep the test fast and deterministic: skip CR/satellite masking and the
        # ePSF build so the Gaussian aperture-correction fallback is exercised.
        monkeypatch.setattr(
            pipeline_module,
            "mask_and_remove_cosmic_rays",
            lambda *args, **kwargs: np.zeros(image.shape, dtype=bool),
        )
        monkeypatch.setattr(
            pipeline_module,
            "detect_and_mask_satellite_trails",
            lambda *args, **kwargs: np.zeros(image.shape, dtype=bool),
        )
        monkeypatch.setattr(
            pipeline_module,
            "perform_psf_photometry",
            lambda *args, **kwargs: (None, None),
        )

        phot_table, *_ = pipeline_module.detection_and_photometry(
            image,
            header,
            mean_fwhm_pixel=3.0,
            threshold_sigma=5.0,
            detection_mask=15,
            fwhm_radius_factor=1.5,
        )

        assert phot_table is not None
        assert len(phot_table) > 5

        for suffix in ("_1_1", "_1_3", "_1_5"):
            for column in (
                f"aperture_sum_bkg_corr{suffix}",
                f"aperture_sum_bkg_corr_err{suffix}",
                f"aperture_mag_err{suffix}",
                f"snr{suffix}",
                f"apcorr{suffix}",
                f"apcorr_mag_err{suffix}",
                f"ee_fraction{suffix}",
                f"centroid_flux_err{suffix}",
                f"instrumental_mag{suffix}",
            ):
                assert column in phot_table.colnames, column

        flux = np.asarray(phot_table["aperture_sum_bkg_corr_1_3"], dtype=float)
        err = np.asarray(phot_table["aperture_sum_bkg_corr_err_1_3"], dtype=float)
        ap_err = np.asarray(phot_table["aperture_sum_err_1_3"], dtype=float)
        mag_err = np.asarray(phot_table["aperture_mag_err_1_3"], dtype=float)
        snr = np.asarray(phot_table["snr_1_3"], dtype=float)
        apcorr = np.asarray(phot_table["apcorr_1_3"], dtype=float)
        inst = np.asarray(phot_table["instrumental_mag_1_3"], dtype=float)

        good = np.isfinite(flux) & (flux > 0) & np.isfinite(err) & (err > 0)
        assert good.sum() > 5

        # S/N uses the same background-corrected flux and its full error
        assert_allclose(snr[good], flux[good] / err[good], rtol=1e-9)
        # The statistical part of the magnitude error uses the same flux as the
        # magnitude; the aperture-correction systematic is added in quadrature.
        apcorr_sys = np.asarray(phot_table["apcorr_mag_err_1_3"], dtype=float)
        expected_sys = (
            pipeline_module.MAG_ERR_COEFFICIENT
            * pipeline_module.APCORR_SYSTEMATIC_FRACTION
            * np.abs(apcorr[good] - 1.0)
            / apcorr[good]
        )
        assert_allclose(apcorr_sys[good], expected_sys, rtol=1e-9)
        assert_allclose(
            mag_err[good],
            np.sqrt(
                (pipeline_module.MAG_ERR_COEFFICIENT * err[good] / flux[good]) ** 2
                + apcorr_sys[good] ** 2
            ),
            rtol=1e-9,
        )
        # The instrumental magnitude reflects the aperture-corrected flux
        assert_allclose(inst[good], -2.5 * np.log10(flux[good] * apcorr[good]), rtol=1e-9)
        # The local-background and centroid terms are folded into the flux error
        assert np.all(err[good] > ap_err[good])
        bkg_pixel_err = np.asarray(
            phot_table["background_rms_per_pixel_1_3"], dtype=float
        )
        aperture_area = np.asarray(phot_table["aperture_area_1_3"], dtype=float)
        centroid_err = np.asarray(
            phot_table["centroid_flux_err_1_3"], dtype=float
        )
        assert_allclose(
            err[good],
            np.sqrt(
                ap_err[good] ** 2
                + (bkg_pixel_err[good] * aperture_area[good]) ** 2
                + centroid_err[good] ** 2
            ),
            rtol=1e-9,
        )
        assert np.all(centroid_err[good] >= 0.0)
        # The term must not be a silent no-op on real (noisy) data.
        assert np.any(centroid_err[good] > 0.0)
        # Aperture correction is >= 1 and larger for the smaller aperture
        assert np.all(apcorr[good] >= 1.0)
        assert np.all(apcorr[good] < 1.2)
        # The encircled-energy fraction is exported for auditing: apcorr = 1/ee
        ee_fraction = np.asarray(phot_table["ee_fraction_1_3"], dtype=float)
        assert_allclose(ee_fraction[good], 1.0 / apcorr[good], rtol=1e-9)
        assert np.all(
            np.asarray(phot_table["apcorr_1_1"], dtype=float) >= apcorr - 1e-12
        )

    def test_local_background_error_uses_median_error(self, monkeypatch):
        """The local-background uncertainty must use the standard error of the
        median (``ApertureStats.median_err``), not the mean-based
        ``std / sqrt(area)``."""
        import astropy.units as u

        image = self._make_image()
        header = self._make_header(image.shape)

        class FakeStreamlit:
            def __init__(self):
                self.session_state = {"base_filename": "bkg", "username": "tester"}

            def __getattr__(self, _name):
                return lambda *_args, **_kwargs: None

        monkeypatch.setattr(pipeline_module, "st", FakeStreamlit())
        monkeypatch.setattr(
            pipeline_module,
            "mask_and_remove_cosmic_rays",
            lambda *args, **kwargs: np.zeros(image.shape, dtype=bool),
        )
        monkeypatch.setattr(
            pipeline_module,
            "detect_and_mask_satellite_trails",
            lambda *args, **kwargs: np.zeros(image.shape, dtype=bool),
        )
        monkeypatch.setattr(
            pipeline_module,
            "perform_psf_photometry",
            lambda *args, **kwargs: (None, None),
        )

        # Deliberately make the mean-based and median-based standard errors
        # differ, so the test fails if the pipeline uses std/sqrt(area).
        class FakeApertureStats:
            def __init__(self, data, aperture, *args, **kwargs):
                self._n = np.atleast_2d(aperture.positions).shape[0]

            @property
            def median(self):
                return np.zeros(self._n, dtype=float)

            @property
            def std(self):
                return np.full(self._n, 2.0)

            @property
            def median_err(self):
                return np.full(self._n, 5.0)

            @property
            def sum_aper_area(self):
                return np.full(self._n, 4.0) * u.pix

        monkeypatch.setattr(pipeline_module, "ApertureStats", FakeApertureStats)

        phot_table, *_ = pipeline_module.detection_and_photometry(
            image,
            header,
            mean_fwhm_pixel=3.0,
            threshold_sigma=5.0,
            detection_mask=15,
            fwhm_radius_factor=1.5,
        )

        assert phot_table is not None
        bkg_pixel_err = np.asarray(
            phot_table["background_rms_per_pixel_1_3"], dtype=float
        )
        # 5.0 is median_err; the mean-based 2.0/sqrt(4.0) = 1.0 would be wrong.
        assert_allclose(bkg_pixel_err, 5.0, rtol=1e-9)

        # And it must actually enter the propagated flux error.
        aperture_area = np.asarray(phot_table["aperture_area_1_3"], dtype=float)
        ap_err = np.asarray(phot_table["aperture_sum_err_1_3"], dtype=float)
        centroid_err = np.asarray(
            phot_table["centroid_flux_err_1_3"], dtype=float
        )
        flux_err = np.asarray(
            phot_table["aperture_sum_bkg_corr_err_1_3"], dtype=float
        )
        assert_allclose(
            flux_err,
            np.sqrt(
                ap_err**2
                + (5.0 * aperture_area) ** 2
                + centroid_err**2
            ),
            rtol=1e-9,
        )

    def test_epsf_aperture_correction_end_to_end(self, monkeypatch, tmp_path):
        """The pipeline must derive the aperture correction from the real ePSF."""
        # A constant sky level is included on purpose: once Background2D subtracts
        # it, the local mid-background is ~0, which used to make the local
        # background-flatness filter reject every candidate star. A continuous
        # amplitude distribution keeps enough stars inside the flux-percentile
        # window that survives the saturation cut.
        image = self._make_image(
            n=700,
            background=150.0,
            noise=3.5,
            fwhm=3.0,
            spacing=50,
            amplitude_range=(1500.0, 3600.0),
        )
        header = self._make_header(image.shape)

        info_messages = []

        class FakeStreamlit:
            def __init__(self):
                self.session_state = {"base_filename": "apcorr", "username": "tester"}

            def info(self, message):
                info_messages.append(str(message))

            def __getattr__(self, _name):
                return lambda *_args, **_kwargs: None

        fake_st = FakeStreamlit()
        monkeypatch.setattr(pipeline_module, "st", fake_st)
        monkeypatch.setattr(psf_module, "st", fake_st)
        monkeypatch.setattr(
            psf_module, "ensure_output_directory", lambda directory=None: str(tmp_path)
        )
        # Skip CR/satellite masking, but let the real ePSF builder run.
        monkeypatch.setattr(
            pipeline_module,
            "mask_and_remove_cosmic_rays",
            lambda *args, **kwargs: np.zeros(image.shape, dtype=bool),
        )
        monkeypatch.setattr(
            pipeline_module,
            "detect_and_mask_satellite_trails",
            lambda *args, **kwargs: np.zeros(image.shape, dtype=bool),
        )

        (
            phot_table,
            epsf_table,
            _daofind,
            _bkg,
            _wcs,
            _fig,
            fwhm_estimate,
            _mask,
        ) = pipeline_module.detection_and_photometry(
            image,
            header,
            mean_fwhm_pixel=3.0,
            threshold_sigma=5.0,
            detection_mask=15,
            fwhm_radius_factor=1.5,
        )

        assert epsf_table is not None, "the ePSF should be built on this field"

        # The PSF-vs-aperture consistency diagnostic must have run and, on this
        # well-behaved synthetic field, must report agreement rather than flag an
        # outlier.
        consistency = fake_st.session_state.get("psf_aperture_consistency")
        assert consistency is not None
        assert consistency["outliers"] == []
        for suffix in ("_1_1", "_1_3", "_1_5"):
            entry = consistency["per_aperture"].get(suffix)
            assert entry is not None
            assert entry["n"] > 5
            assert abs(entry["median_diff"]) < 0.1
        epsf_model = fake_st.session_state.get("epsf_model")
        assert epsf_model is not None
        assert any(
            "empirical PSF" in message for message in info_messages
        ), "the pipeline must report the empirical-PSF aperture correction"

        # The stored correction must equal the encircled energy of the returned ePSF.
        radii_pixels = [
            1.1 * fwhm_estimate,
            1.3 * fwhm_estimate,
            1.5 * fwhm_estimate,
        ]
        ee = compute_encircled_energy(
            epsf_model.data, epsf_model.oversampling, radii_pixels
        )
        expected_corrections = [
            min(max(1.0 / value if 0.0 < value <= 1.0 else 1.0, 1.0), 2.0)
            for value in ee
        ]

        for suffix, expected in zip(("_1_1", "_1_3", "_1_5"), expected_corrections):
            apcorr = np.asarray(phot_table[f"apcorr{suffix}"], dtype=float)
            assert_allclose(apcorr, expected, rtol=1e-9)

        # ...and the magnitude must reflect that correction.
        flux = np.asarray(phot_table["aperture_sum_bkg_corr_1_3"], dtype=float)
        inst = np.asarray(phot_table["instrumental_mag_1_3"], dtype=float)
        apcorr = np.asarray(phot_table["apcorr_1_3"], dtype=float)
        good = np.isfinite(flux) & (flux > 0) & np.isfinite(inst)
        assert good.sum() > 5
        assert np.all(apcorr[good] > 1.0)
        assert_allclose(
            inst[good], -2.5 * np.log10(flux[good] * apcorr[good]), rtol=1e-9
        )


class TestDocumentedCatalogColumns:
    """Keep the exported catalog columns in sync with docs/api_reference.rst.

    ``docs/api_reference.rst`` publishes an explicit list of the photometry
    error columns that reach the downloadable ``*_catalog.csv`` / ``.vot``, and
    notes which quantities stay internal. This test pins that contract: the
    documented columns must be produced by the export helpers, and the columns
    the docs mark as intermediate / matched-table-only must not leak into the
    exported catalog.
    """

    # Canonical error columns documented in api_reference.rst
    # ("Photometry Error Columns"). ``zero_point_std`` is broadcast onto the
    # catalog by pages/app.py, so the test mirrors that assignment.
    DOCUMENTED_EXPORTED = (
        "background_rms_per_pixel_1_3",
        "centroid_flux_err_1_3",
        "apcorr_1_3",
        "ee_fraction_1_3",
        "apcorr_mag_err_1_3",
        "aperture_mag_err_1_3",
        "aperture_mag_err_1_3_random",
        "aperture_mag_err_1_3_correlated",
        "psf_mag_err",
        "psf_mag_err_random",
        "psf_mag_err_correlated",
        "aperture_mag_1_3_colorcorr_err",
        "psf_mag_colorcorr_err",
        "zero_point_std",
    )

    # Documented as intermediate (detection_and_photometry) or stored on the
    # matched calibration table only, never in the exported source catalog.
    DOCUMENTED_INTERNAL = (
        "aperture_sum_bkg_corr_1_3",
        "aperture_sum_bkg_corr_err_1_3",
        "background_per_pixel_1_3",
        "zero_point_scatter",
        "zero_point_n",
    )

    @staticmethod
    def _catalog(filter_band):
        """Build a catalog the way the export path does, for a given band."""
        table = pd.DataFrame(
            {
                "id": [1, 2, 3],
                "xcenter": [10.0, 20.0, 30.0],
                "ycenter": [10.0, 20.0, 30.0],
                "instrumental_mag_1_3": [10.0, 11.0, 12.0],
                # Per-method error column emitted by detection_and_photometry.
                "aperture_mag_err_1_3": [0.06, 0.07, 0.08],
                # Intermediate flux columns: must be dropped on export.
                "aperture_sum_bkg_corr_1_3": [1000.0, 500.0, 200.0],
                "aperture_sum_bkg_corr_err_1_3": [10.0, 8.0, 6.0],
                "background_per_pixel_1_3": [200.0, 200.0, 200.0],
                # Documented exported columns.
                "background_rms_per_pixel_1_3": [5.0, 5.0, 5.0],
                "centroid_flux_err_1_3": [0.3, 0.3, 0.3],
                "apcorr_1_3": [1.05, 1.05, 1.05],
                "ee_fraction_1_3": [0.952, 0.952, 0.952],
                "apcorr_mag_err_1_3": [0.02, 0.02, 0.02],
                "psf_instrumental_mag": [10.05, 11.05, 12.05],
                "psf_mag_err": [0.05, 0.06, 0.07],
                # Catalog colors for the optional color-term correction.
                "calib_gmag": [10.6, 11.6, 12.6],
                "calib_rmag": [10.2, 11.2, 12.2],
            }
        )

        out = add_calibrated_magnitudes(
            table,
            zero_point=22.0,
            airmass=1.1,
            filter_band=filter_band,
            zero_point_std=0.03,
            systematic_floor=0.01,
        )
        # pages/app.py broadcasts the field-level zero-point scalar.
        out["zero_point_std"] = 0.03

        color_term_result = {
            "fitted": True,
            "color_label": "g-r",
            "color_term": 0.2,
            "color_ref": 0.4,
            "intercept": 0.01,
            "parameter_covariance": [[0.0004, 0.0001], [0.0001, 0.0009]],
        }
        return apply_color_term_correction(
            out, color_term_result, filter_band=filter_band
        )

    def test_documented_error_columns_are_exported(self):
        """Every documented error column must reach the catalog."""
        # "rmag" is a supported color definition without a column prefix, so
        # the canonical (unprefixed) documented names are the exported ones.
        catalog = self._catalog("rmag")
        missing = [c for c in self.DOCUMENTED_EXPORTED if c not in catalog.columns]
        assert not missing, f"documented exported column(s) missing: {missing}"

    def test_internal_columns_are_not_exported(self):
        """Columns documented as internal must not leak into the catalog."""
        catalog = self._catalog("rmag")
        leaked = [c for c in self.DOCUMENTED_INTERNAL if c in catalog.columns]
        assert not leaked, f"internal column(s) leaked into the catalog: {leaked}"

    def test_documented_error_columns_survive_a_filter_prefix(self):
        """With a GAIA band the prefixed error columns replace the legacy ones."""
        filter_band = "phot_g_mean_mag"
        prefix = get_filter_prefix(filter_band)
        assert prefix, "expected a filter prefix for the GAIA g band"

        catalog = drop_legacy_magnitude_columns(self._catalog(filter_band), filter_band)
        for column in (
            "aperture_mag_err_1_3",
            "aperture_mag_err_1_3_random",
            "aperture_mag_err_1_3_correlated",
            "psf_mag_err",
            "psf_mag_err_random",
            "psf_mag_err_correlated",
        ):
            assert f"{prefix}_{column}" in catalog.columns, (
                f"prefixed export is missing {prefix}_{column}"
            )

    def test_legacy_error_columns_are_dropped_with_a_prefix(self):
        """The legacy unprefixed magnitude/error columns must not be exported
        alongside their filter-prefixed replacements."""
        filter_band = "phot_g_mean_mag"
        prefix = get_filter_prefix(filter_band)
        assert prefix

        catalog = drop_legacy_magnitude_columns(self._catalog(filter_band), filter_band)

        # Legacy magnitude and error columns that must be replaced by the
        # prefixed forms when a prefix applies.
        legacy_columns = (
            "instrumental_mag_1_3",
            "aperture_mag_1_3",
            "aperture_mag_err_1_3",
            "aperture_mag_err_1_3_random",
            "aperture_mag_err_1_3_correlated",
            "psf_instrumental_mag",
            "psf_mag",
            "psf_mag_err",
            "psf_mag_err_random",
            "psf_mag_err_correlated",
        )
        still_present = [c for c in legacy_columns if c in catalog.columns]
        assert not still_present, (
            "legacy column(s) not dropped for a prefixed export: "
            f"{still_present}"
        )

        # Every dropped legacy error column must have a prefixed counterpart.
        for column in (
            "aperture_mag_err_1_3",
            "aperture_mag_err_1_3_random",
            "aperture_mag_err_1_3_correlated",
            "psf_mag_err",
            "psf_mag_err_random",
            "psf_mag_err_correlated",
        ):
            assert f"{prefix}_{column}" in catalog.columns


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "--tb=short"])
