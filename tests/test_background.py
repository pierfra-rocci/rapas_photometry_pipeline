"""Tests for the sky-background model used by the photometry pipeline.

The audit that motivated these tests found three failure modes of the previous
``estimate_background`` configuration:

* ``Background2D``'s default ``exclude_percentile=10`` rejects every box when the
  whole frame is crowded or masked, raising a ``ValueError`` that aborted the
  run and produced no photometry at all;
* the plain (std) per-box RMS is inflated by undetected sources: measured
  inflation was x1.11 (15 px spacing), x1.38 (12 px) and x2.03 (10 px) on
  synthetic frames, and that factor propagates straight into the flux errors;
* a 5x5 median filter over a small background mesh over-smooths the model and
  leaves large-scale residuals (the subtracted frame was x2.24 noisier than the
  raw frame on a sky gradient).

The tests below pin the behaviour that fixes them.
"""

from functools import lru_cache

import numpy as np
import pytest

from src.tools_pipeline import (
    BACKGROUND_SMALL_MESH_THRESHOLD,
    _constant_background,
    estimate_background,
)

SKY = 200.0  # ADU
GAIN = 1.5  # e-/ADU
READ_NOISE = 3.0  # ADU
TRUE_RMS = float(np.sqrt(SKY / GAIN + READ_NOISE**2))
FWHM = 3.0
SIGMA = FWHM / (2.0 * np.sqrt(2.0 * np.log(2.0)))


@lru_cache(maxsize=16)
def _true_sky(n=512, gradient=0.0):
    """The exact sky (plus star flux) of a synthetic frame."""
    return _true_sky_cached(n, gradient)


def _true_sky_cached(n=512, gradient=0.0):
    x_grid = np.arange(n, dtype=float)[None, :]
    return SKY + gradient * (x_grid / (n - 1.0)) * np.ones((n, 1))


@lru_cache(maxsize=16)
def _cached_star_field(n=512, spacing=None, amplitude=900.0, gradient=0.0, seed=1):
    """Physically consistent frame: Poisson sky + stars, Gaussian read noise."""
    from astropy.modeling.models import Gaussian2D

    rng = np.random.default_rng(seed)
    y_grid, x_grid = np.mgrid[:n, :n]
    sky = SKY + gradient * (x_grid / (n - 1.0))
    sources = np.zeros((n, n))
    if spacing:
        # Inject each star on a small stamp instead of evaluating the model on
        # the whole frame: a densely spaced field has thousands of stars.
        half = int(np.ceil(4.0 * SIGMA))
        for iy, y0 in enumerate(range(spacing, n - spacing, spacing)):
            for ix, x0 in enumerate(range(spacing, n - spacing, spacing)):
                y_lo, y_hi = y0 - half, y0 + half + 1
                x_lo, x_hi = x0 - half, x0 + half + 1
                if y_lo < 0 or x_lo < 0 or y_hi > n or x_hi > n:
                    continue
                yy, xx = np.mgrid[y_lo:y_hi, x_lo:x_hi]
                sources[y_lo:y_hi, x_lo:x_hi] += Gaussian2D(
                    amplitude=amplitude,
                    x_mean=float(x0),
                    y_mean=float(y0),
                    x_stddev=SIGMA,
                    y_stddev=SIGMA,
                )(xx, yy)
    image = rng.poisson(np.maximum(sky + sources, 0.0) * GAIN) / GAIN
    image += rng.normal(0.0, READ_NOISE, image.shape)
    return image


def _star_field(n=512, spacing=None, amplitude=900.0, gradient=0.0, seed=1):
    """A writable copy of the cached synthetic frame."""
    return _cached_star_field(n, spacing, amplitude, gradient, seed).copy()


class TestBackgroundRobustness:
    """The background step must never abort the run on hard frames."""

    @pytest.mark.parametrize("spacing", [20, 15, 12, 10])
    def test_crowded_fields_still_produce_a_model(self, spacing):
        image = _star_field(spacing=spacing)
        bkg, _fig, error = estimate_background(image, figure=False)
        assert bkg is not None, error
        assert bkg.background.shape == image.shape
        assert bkg.background_rms.shape == image.shape

    def test_frame_with_large_nan_areas_still_produces_a_model(self):
        image = _star_field()
        rng = np.random.default_rng(7)
        bad = rng.random(image.shape) < 0.2
        image[bad] = np.nan
        bkg, _fig, error = estimate_background(image, figure=False)
        assert bkg is not None, error
        assert np.all(np.isfinite(bkg.background))

    def test_constant_fallback_model_is_usable(self):
        """The last-resort model exposes the same arrays as Background2D."""
        image = _star_field(spacing=15)
        model = _constant_background(image)
        assert model is not None
        assert model.background.shape == image.shape
        assert model.background_rms.shape == image.shape
        assert np.allclose(model.background, np.median(model.background))
        assert model.background_rms[0, 0] > 0

    def test_constant_fallback_rejects_hopeless_frames(self):
        assert _constant_background(np.full((16, 16), np.nan)) is None

    def test_constant_fallback_rms_is_mad_based(self):
        """The last-resort RMS must track the true noise, not the inflated std."""
        from astropy.stats import sigma_clipped_stats

        image = _star_field(spacing=12)
        model = _constant_background(image)
        _, _, plain_std = sigma_clipped_stats(image, sigma=3.0, maxiters=10)
        rms = float(model.background_rms[0, 0])
        ratio = rms / TRUE_RMS
        assert ratio == pytest.approx(1.0, abs=0.20), (
            f"constant-fallback RMS should track the true pixel noise, got x{ratio:.2f}"
        )
        assert rms <= plain_std


class TestBackgroundAccuracy:
    """The model must not distort the sky level or inflate the noise."""

    def test_flat_sky_level_is_preserved(self):
        image = _star_field()
        bkg, _fig, _error = estimate_background(image, figure=False)
        level = float(np.median(bkg.background))
        assert level == pytest.approx(SKY, abs=0.01 * SKY)

    @pytest.mark.parametrize("spacing", [None, 30, 20])
    def test_noise_map_is_not_inflated_by_regime(self, spacing):
        image = _star_field(spacing=spacing)
        bkg, _fig, _error = estimate_background(image, figure=False)
        ratio = float(np.median(bkg.background_rms)) / TRUE_RMS
        assert ratio == pytest.approx(1.0, abs=0.08), (
            "background RMS should track the true pixel noise, got "
            f"x{ratio:.2f}"
        )

    def test_noise_map_stays_below_the_plain_std_estimate_when_crowded(self):
        """The MAD-based estimator must beat the std in a crowded field."""
        from astropy.stats import SigmaClip
        from photutils.background import (
            Background2D,
            SExtractorBackground,
            StdBackgroundRMS,
        )

        image = _star_field(spacing=12)
        bkg, _fig, _error = estimate_background(image, figure=False)
        mad_rms = float(np.median(bkg.background_rms))

        plain = Background2D(
            image,
            box_size=(64, 64),
            filter_size=3,
            sigma_clip=SigmaClip(sigma=3.0),
            bkg_estimator=SExtractorBackground(),
            bkg_rms_estimator=StdBackgroundRMS(),
            exclude_percentile=50.0,
        )
        plain_rms = float(np.median(plain.background_rms))
        assert mad_rms < 0.95 * plain_rms
        assert mad_rms / TRUE_RMS < 1.3

    def test_small_mesh_filter_is_capped(self, monkeypatch):
        """Small background meshes must not use a wide smoothing filter."""
        from photutils.background import Background2D as RealBackground2D

        seen = {}
        original = RealBackground2D

        class RecordingBackground2D(original):
            def __init__(self, *args, **kwargs):
                seen["filter_size"] = kwargs.get("filter_size")
                seen["exclude_percentile"] = kwargs.get("exclude_percentile")
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(
            "src.tools_pipeline.Background2D", RecordingBackground2D
        )
        image = _star_field(n=320)
        estimate_background(image, figure=False)

        mesh = 320 // max(64, min(320 // 10, 320 // 10, 128))
        assert mesh < BACKGROUND_SMALL_MESH_THRESHOLD
        assert seen["filter_size"] <= 3
        assert seen["exclude_percentile"] == pytest.approx(10.0)

    def test_gradient_sky_leaves_small_residuals(self):
        image = _star_field(gradient=300.0)
        bkg, _fig, _error = estimate_background(image, figure=False)
        true_sky = _true_sky(512, 300.0)
        residual = bkg.background - true_sky
        # The 64 px boxes cannot follow a 300 ADU ramp exactly, and a wide
        # smoothing filter made this far worse: the previous 5x5 filter over the
        # 8x8 mesh left residuals of up to ~56 ADU (5 sigma).
        assert float(np.max(np.abs(residual))) < 4.0 * TRUE_RMS
