import os
import re
import streamlit as st

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import astroscrappy
from astropy.wcs import WCS
from astropy.stats import sigma_clipped_stats, sigma_clip, SigmaClip
from astropy.coordinates import SkyCoord, EarthLocation, AltAz, get_sun
from astropy.time import Time
from photutils.psf import fit_fwhm
from astropy.visualization import ZScaleInterval
import astropy.units as u

from photutils.utils import calc_total_error
from photutils.detection import DAOStarFinder
from photutils.aperture import (CircularAperture, CircularAnnulus,
                                ApertureStats, aperture_photometry)

from src.tools_pipeline import (
    safe_wcs_create,
    estimate_background,
    extract_photometric_color_series,
)
from src.utils import ensure_output_directory, session_results_path

from typing import Union, Optional, Dict, Tuple
from src.psf import perform_psf_photometry, compute_encircled_energy
MAG_ERR_COEFFICIENT = 2.5 / np.log(10.0)  # 1.0857...
GAUSSIAN_FWHM_TO_SIGMA = 2.0 * np.sqrt(2.0 * np.log(2.0))  # 2.3548...
# Optional additive systematic floor (mag) applied to calibrated magnitude errors.
# It is combined in quadrature with the statistical and zero-point terms by
# ``add_calibrated_magnitudes`` and can be overridden per run through
# ``analysis_parameters["systematic_mag_err_floor"]`` or the
# ``RPP_SYSTEMATIC_MAG_ERR_FLOOR`` environment variable (see
# ``resolve_systematic_floor``).
SYSTEMATIC_MAG_ERR_FLOOR = 0.0
# Fractional uncertainty of the aperture correction derived from the PSF
# encircled-energy curve. The correction changes the flux by a factor
# ``apcorr`` (a relative shift of ``(apcorr - 1)``); its uncertainty is taken
# as this fraction of that shift and converted to the magnitude scale with
# ``MAG_ERR_COEFFICIENT``. This term does not cancel in the flux ratio, so it is
# a genuine systematic that would otherwise be absent from every aperture
# magnitude error (it dominates at high S/N). 0 disables it.
APCORR_SYSTEMATIC_FRACTION = 0.20
# PSF/aperture consistency diagnostic. PSF fluxes and encircled-energy-corrected
# aperture fluxes should both estimate the same total flux, so their
# instrumental magnitudes should agree source by source. A median offset larger
# than this value (mag) is reported as a warning: it points to an ePSF
# shape/normalisation or local-background problem rather than to noise.
PSF_APERTURE_CONSISTENCY_THRESHOLD = 0.15
# Centroid-position contribution to the aperture flux error. ``sigma_pos`` is the
# standard high-S/N approximation sigma_PSF / S/N, floored and capped at
# CENTROID_MAX_SIGMA_FRACTION x FWHM; the flux change for a one-sigma
# displacement is measured directly by shifting the aperture (this captures the
# real PSF asymmetry/ellipticity that a symmetric-Gaussian estimate would miss).
CENTROID_MIN_SIGMA_PIX = 0.01
CENTROID_MAX_SIGMA_FRACTION = 0.5
# Standard error of the sample median of a Gaussian sample, in units of
# sigma / sqrt(N): sqrt(pi / 2) = 1.2533. The zero point is a median, so this
# factor (and not 1.0) is the correct one for its formal uncertainty.
MEDIAN_STD_ERROR_FACTOR = float(np.sqrt(np.pi / 2.0))
# Plausible range for a true FITS gain in e-/ADU. Genuine conversion factors
# span roughly 0.02-100 e-/ADU (large-well CCDs reach tens of e-/ADU), hence the
# 100 upper bound. Detector *driver* settings are sometimes written to GAIN
# instead (the QHY 0-100 scale writes GAIN=56, ZWO writes a 0.1 dB value); those
# cameras are recognised from the instrument keyword and their GAIN is ignored
# (see ``DRIVER_GAIN_INSTRUMENT_PATTERNS``) rather than relying on a range that
# would also reject legitimate large gains.
GAIN_PLAUSIBLE_RANGE = (0.02, 100.0)
# A cosmic-ray mask covering more than this fraction of a frame means the noise
# model does not describe the data (real CR rates are < 1%), so the mask is
# rejected instead of removing a large part of the science image.
MAX_CR_MASK_FRACTION = 0.20

# FITS keywords that may carry the effective gain, in priority order, with how
# to turn the header value into e-/ADU: "identity" for a value that is already
# e-/ADU, "inverse" for a conversion factor in ADU/e- whose reciprocal is the
# gain. Explicit conversion keywords are tried before the ambiguous GAIN so that
# a driver setting written to GAIN cannot shadow a genuine EGAIN/CVF in the same
# header.
GAIN_KEYWORDS = (
    ("EGAIN", "identity"),
    ("CCDGAIN", "identity"),
    ("CVF", "inverse"),
    ("GAIN", "identity"),
)

# Cameras whose ``GAIN`` keyword is an abstract driver setting rather than an
# e-/ADU conversion factor. QHY writes a 0-100 scale (a QHY268M reaches
# ~0.77 e-/ADU at GAIN=56, with an HGC/LGC switch at 55->56), ZWO ASI writes a
# 0.1 dB value (unity gain is a model-specific number, e.g. ~100 for the
# ASI2600). The instrument is matched case-insensitively against the usual
# metadata keywords (INSTRUME/INSTRID/INSTRUMENT/CAMERA/DETECTOR/CCDNAME) with
# these regular expressions; extend the tuple for other driver-gain cameras.
DRIVER_GAIN_INSTRUMENT_PATTERNS = (
    r"QHY",
    r"ZWO",
    r"(?<![A-Z])ASI[- ]?\d",  # ZWO ASI model strings, e.g. "ASI2600MM"
)
# Header keywords scanned to identify the instrument for driver-gain detection.
INSTRUMENT_HEADER_KEYS = (
    "INSTRUME",
    "INSTRID",
    "INSTRUMENT",
    "CAMERA",
    "DETECTOR",
    "CCDNAME",
)

# Photon-transfer (variance vs signal) gain estimation settings.
PHOTON_TRANSFER_BOX = 32
PHOTON_TRANSFER_MIN_BOXES = 12
PHOTON_TRANSFER_MAX_BOXES = 1500
PHOTON_TRANSFER_MIN_SIGNIFICANCE = 3.0
PHOTON_TRANSFER_GAIN_RANGE = (0.2, 100.0)


def _requested_effective_gain():
    """Return the user-supplied effective gain, or None when not provided.

    The value can come from the sidebar (``analysis_parameters["effective_gain"]``
    or a session-state ``effective_gain`` entry) or from the
    ``RPP_EFFECTIVE_GAIN`` environment variable, which makes it usable for
    headless/batch runs. Non-positive or unparsable values mean "auto".
    """
    candidates = []
    try:
        parameters = st.session_state.get("analysis_parameters", {}) or {}
        candidates.append(parameters.get("effective_gain"))
        candidates.append(st.session_state.get("effective_gain"))
    except Exception:
        # A partially initialised or stubbed session state must not break
        # photometry; the gain simply stays on the automatic path.
        pass
    candidates.append(os.environ.get("RPP_EFFECTIVE_GAIN"))

    for value in candidates:
        try:
            if isinstance(value, str):
                value = value.strip().replace(",", ".")
            gain = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(gain) and gain > 0:
            return gain
    return None


def resolve_systematic_floor(default=None):
    """Resolve the optional systematic magnitude-error floor (mag).

    The floor is a single additive term, added in quadrature to the statistical
    and zero-point uncertainties of every calibrated magnitude error. It is
    resolved in this order:

    1. the sidebar / session state (``analysis_parameters["systematic_mag_err_floor"]``
       or a session-state ``systematic_mag_err_floor`` entry),
    2. the ``RPP_SYSTEMATIC_MAG_ERR_FLOOR`` environment variable (headless runs),
    3. the module default ``SYSTEMATIC_MAG_ERR_FLOOR`` (0, i.e. no extra term).

    A value of 0 disables the floor. Negative or unparsable values are ignored.
    """
    if default is None:
        default = SYSTEMATIC_MAG_ERR_FLOOR
    candidates = []
    try:
        parameters = st.session_state.get("analysis_parameters", {}) or {}
        candidates.append(parameters.get("systematic_mag_err_floor"))
        candidates.append(st.session_state.get("systematic_mag_err_floor"))
    except Exception:
        pass
    candidates.append(os.environ.get("RPP_SYSTEMATIC_MAG_ERR_FLOOR"))

    for value in candidates:
        try:
            if isinstance(value, str):
                value = value.strip().replace(",", ".")
            floor = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(floor) and floor >= 0:
            return floor
    return float(default)


def estimate_effective_gain(
    image_data,
    box_size=PHOTON_TRANSFER_BOX,
    max_boxes=PHOTON_TRANSFER_MAX_BOXES,
):
    """Estimate the effective gain (countable units per data unit) empirically.

    Uses the photon-transfer relation: for boxes of pure sky the pixel variance
    grows linearly with the mean signal,

        var(box) ~ read_noise^2 + mean(box) / gain,

    so ``1 / slope`` of a robust fit to (mean, variance) is the effective gain.
    The estimate is only returned when the frame itself constrains it:

    * enough source-free boxes are available (a source-free box is one where
      sigma clipping removes < 10% of the pixels),
    * the sky level varies enough across the frame for the slope to be measured
      at ``PHOTON_TRANSFER_MIN_SIGNIFICANCE``,
    * the resulting gain is physically plausible.

    A flat sky (no gradient, no nebulosity) carries no gain information, so
    ``None`` is returned and the caller falls back to a documented default.

    Returns
    -------
    float or None
        Estimated gain, or None when the frame does not constrain it.
    """
    try:
        data = np.asarray(image_data, dtype=float)
    except (TypeError, ValueError):
        return None
    if data.ndim != 2:
        return None

    ny, nx = data.shape
    if ny < 2 * box_size or nx < 2 * box_size:
        return None

    means = []
    variances = []
    n_y = (ny // box_size) * box_size
    n_x = (nx // box_size) * box_size
    for y0 in range(0, n_y, box_size):
        for x0 in range(0, n_x, box_size):
            if len(means) >= max_boxes:
                break
            box = data[y0 : y0 + box_size, x0 : x0 + box_size]
            if box.shape != (box_size, box_size) or not np.all(np.isfinite(box)):
                continue
            clipped = sigma_clip(box, sigma=3.0, maxiters=5, masked=True)
            n_good = int(np.count_nonzero(~np.ma.getmaskarray(clipped)))
            # Boxes flagged by the clip are contaminated by sources and would
            # bias the variance-vs-signal slope upwards (gain downwards).
            if n_good < 0.9 * box.size or n_good < 4:
                continue
            mean = float(np.ma.mean(clipped))
            variance = float(np.ma.var(clipped))
            if not (np.isfinite(mean) and np.isfinite(variance)) or variance < 0:
                continue
            means.append(mean)
            variances.append(variance)

    if len(means) < PHOTON_TRANSFER_MIN_BOXES:
        return None

    means = np.asarray(means, dtype=float)
    variances = np.asarray(variances, dtype=float)
    if np.ptp(means) <= 0 or not np.ptp(variances) > 0:
        return None

    slope, intercept = np.polyfit(means, variances, 1)
    for _ in range(2):
        residuals = variances - (slope * means + intercept)
        scale = np.std(residuals)
        if not np.isfinite(scale) or scale <= 0:
            break
        keep = np.abs(residuals) <= 3.0 * scale
        if keep.sum() < PHOTON_TRANSFER_MIN_BOXES:
            break
        slope, intercept = np.polyfit(means[keep], variances[keep], 1)

    if not np.isfinite(slope) or slope <= 0:
        return None

    # Significance of the slope: the sky level must actually drive the variance.
    residuals = variances - (slope * means + intercept)
    spread = float(np.std(means, ddof=1))
    n_points = max(len(means) - 2, 1)
    if spread <= 0 or residuals.size <= 2:
        return None
    slope_error = float(np.std(residuals, ddof=1)) / (np.sqrt(n_points) * spread)
    if not np.isfinite(slope_error) or slope_error <= 0:
        return None
    if slope / slope_error < PHOTON_TRANSFER_MIN_SIGNIFICANCE:
        return None

    gain = 1.0 / slope
    lower, upper = PHOTON_TRANSFER_GAIN_RANGE
    if not np.isfinite(gain) or not (lower <= gain <= upper):
        return None
    return float(gain)


def instrument_uses_driver_gain(header):
    """Return True when the header names a camera whose ``GAIN`` is a driver setting.

    On several CMOS cameras the FITS ``GAIN`` keyword holds an abstract detector
    driver setting, not an e-/ADU conversion factor: QHY writes a 0-100 scale (a
    QHY268M reaches ~0.77 e-/ADU at GAIN=56) and ZWO ASI writes a 0.1 dB value.
    Using such a number as e-/ADU divides the Poisson variance by the driver
    value and inflates the S/N of bright sources (and made L.A.Cosmic flag ~49%
    of a real frame as cosmic rays). The instrument is identified from the usual
    metadata keywords and the ``DRIVER_GAIN_INSTRUMENT_PATTERNS`` regexes; an
    explicit ``EGAIN``/``CCDGAIN``/``CVF`` conversion keyword still takes
    precedence over the ignored ``GAIN``.
    """
    for key in INSTRUMENT_HEADER_KEYS:
        if key not in header:
            continue
        try:
            text = str(header.get(key) or "")
        except Exception:
            continue
        if not text:
            continue
        for pattern in DRIVER_GAIN_INSTRUMENT_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
    return False


def resolve_effective_gain(header, image_data, quiet=False):
    """Resolve the effective gain (counts per data unit) used for Poisson noise.

    ``photutils.calc_total_error`` expects the ratio of countable units
    (electrons/photons) to the units of the data. The value is resolved in this
    order:

    1. a FITS conversion keyword, in the priority order of ``GAIN_KEYWORDS``:
       explicit ``EGAIN``/``CCDGAIN`` (e-/ADU) and ``CVF`` (a conversion factor
       in ADU/e-, so ``gain = 1/CVF``) are tried before the conventional
       ``GAIN``. On a recognised driver-gain camera (``QHY``/``ZWO``/``ASI``) a
       bare ``GAIN`` is skipped because it is an abstract driver setting, not
       e-/ADU. Values outside ``GAIN_PLAUSIBLE_RANGE`` are reported and ignored.
    2. a user-supplied gain (sidebar / session state / ``RPP_EFFECTIVE_GAIN``),
    3. an empirical photon-transfer estimate from the frame itself,
    4. ``1`` (data treated as electrons), which is the conservative fallback:
       with data in ADU this overestimates the source Poisson variance by the
       true gain, hence the magnitude errors of bright sources by up to
       ``sqrt(gain)``.
    """
    lower, upper = GAIN_PLAUSIBLE_RANGE
    driver_instrument = instrument_uses_driver_gain(header)
    header_gain = None
    rejected_gain = None
    skipped_driver_gain = None

    for key, conversion in GAIN_KEYWORDS:
        if key not in header:
            continue
        try:
            raw_value = float(header.get(key))
        except (TypeError, ValueError):
            continue
        if not np.isfinite(raw_value) or raw_value <= 0:
            # Keep looking: a zero/blank GAIN often sits next to a valid EGAIN.
            continue
        # CVF is a conversion factor (ADU per electron), so the effective gain
        # is its reciprocal; every other keyword already stores e-/ADU.
        value = 1.0 / raw_value if conversion == "inverse" else raw_value
        if not np.isfinite(value) or value <= 0:
            continue
        if key == "GAIN" and driver_instrument:
            # A recognised driver-gain camera: GAIN is not e-/ADU. Remember it
            # so a message can be shown if no usable keyword is found.
            skipped_driver_gain = raw_value
            continue
        if not (lower <= value <= upper):
            rejected_gain = (key, raw_value, value)
            continue
        header_gain = value
        break

    if header_gain is not None:
        return header_gain

    if skipped_driver_gain is not None:
        st.info(
            f"The instrument looks like a driver-gain camera, so FITS GAIN = "
            f"{skipped_driver_gain:g} is an abstract driver setting rather than "
            "e-/ADU and is ignored. Enter the conversion factor in the "
            "parameters (or provide an EGAIN/CVF keyword) to set it explicitly."
        )

    if rejected_gain is not None:
        key, raw_value, value = rejected_gain
        shown = (
            f"{key} = {raw_value:g} (conversion factor, i.e. "
            f"{value:g} e-/ADU)"
            if key == "CVF"
            else f"{key} = {raw_value:g} e-/ADU"
        )
        st.warning(
            f"{shown} is outside the plausible effective-gain range "
            f"({lower:g}-{upper:g} e-/ADU); it is probably a detector driver "
            "setting rather than e-/ADU, so it is ignored. Set the detector "
            "gain in the parameters or RPP_EFFECTIVE_GAIN to override."
        )

    requested = _requested_effective_gain()
    if requested is not None:
        st.info(
            "No usable header gain keyword; using the user-supplied "
            f"effective_gain = {requested:.4g} for the Poisson term."
        )
        return requested

    empirical = estimate_effective_gain(image_data)
    if empirical is not None:
        st.info(
            "No usable header gain keyword; effective_gain = "
            f"{empirical:.3g} estimated from the frame's photon-transfer "
            "statistics."
        )
        return empirical

    bunit = str(header.get("BUNIT", "") or "").strip().lower()
    bunit_is_electrons = bunit in (
        "electron",
        "electrons",
        "e-",
        "e-/pixel",
        "electron/pixel",
    )
    if bunit_is_electrons:
        st.warning(
            "No usable header gain keyword; BUNIT indicates electrons, "
            "using effective_gain = 1."
        )
    elif not quiet:
        st.warning(
            "No usable header gain keyword and the frame does not constrain "
            "it; assuming effective_gain = 1 (data treated as electrons). Set "
            "the detector gain in the parameters to improve the errors of bright sources."
        )
    return 1.0


def fit_color_term_correction(
    matched_table,
    filter_band: str,
    zero_point: float,
    fwhm_radius_factor: float = 1.5,
    min_points: int = 5,
    sigma_threshold: float = 3.0,
    max_iterations: int = 5,
):
    """Fit a simple linear color term on the matched calibration stars.

    The fit is performed on residuals relative to the existing scalar zero point,
    preserving the current calibration workflow as the baseline path.
    """
    result = {
        "supported": False,
        "fitted": False,
        "reason": None,
        "filter_band": filter_band,
        "instrumental_mag_col": None,
        "color_label": None,
        "color_ref": None,
        "color_term": None,
        "intercept": None,
        "effective_zero_point": None,
        "n_used": 0,
        "scatter": None,
        "intercept_std": None,
        "color_term_std": None,
        "parameter_covariance": None,
        "diagnostic_figure": None,
    }

    if matched_table is None or len(matched_table) == 0:
        result["reason"] = "No matched calibration stars available."
        return result

    color_series, color_metadata = extract_photometric_color_series(matched_table, filter_band)
    result["supported"] = color_metadata.get("supported", False)
    result["color_label"] = color_metadata.get("color_label")
    if color_series is None:
        result["reason"] = "Catalog colors are unavailable for the selected filter."
        return result

    preferred_radius = 1.3
    radius_suffix = f"_{str(preferred_radius).replace('.', '_')}"
    instrumental_mag_col = f"instrumental_mag{radius_suffix}"
    if instrumental_mag_col not in matched_table.columns:
        candidate_cols = sorted(
            [col for col in matched_table.columns if col.startswith("instrumental_mag_")]
        )
        if not candidate_cols:
            result["reason"] = "No instrumental magnitude column is available for color fitting."
            return result
        instrumental_mag_col = candidate_cols[0]

    result["instrumental_mag_col"] = instrumental_mag_col

    fit_table = pd.DataFrame(
        {
            "instrumental_mag": pd.to_numeric(
                matched_table[instrumental_mag_col], errors="coerce"
            ),
            "catalog_mag": pd.to_numeric(matched_table[filter_band], errors="coerce"),
            "source_color": pd.to_numeric(color_series, errors="coerce"),
        }
    )
    fit_table = fit_table.replace([np.inf, -np.inf], np.nan).dropna().copy()

    if len(fit_table) < min_points:
        result["reason"] = (
            f"Need at least {min_points} matched stars with valid colors; found {len(fit_table)}."
        )
        return result

    color_ref = float(np.median(fit_table["source_color"]))
    fit_table["color_centered"] = fit_table["source_color"] - color_ref
    fit_table["residual"] = fit_table["catalog_mag"] - (
        fit_table["instrumental_mag"] + float(zero_point)
    )

    for _ in range(max_iterations):
        x_values = fit_table["color_centered"].to_numpy(dtype=float)
        y_values = fit_table["residual"].to_numpy(dtype=float)
        design_matrix = np.column_stack([np.ones(len(x_values)), x_values])
        intercept, color_term = np.linalg.lstsq(design_matrix, y_values, rcond=None)[0]
        model = intercept + color_term * x_values
        residuals = y_values - model

        if len(fit_table) <= min_points:
            break

        median_residual = float(np.median(residuals))
        mad = float(np.median(np.abs(residuals - median_residual)))
        scale = 1.4826 * mad if mad > 0 else float(np.std(residuals, ddof=1))
        if not np.isfinite(scale) or scale <= 0:
            break

        keep_mask = np.abs(residuals - median_residual) <= sigma_threshold * scale
        if keep_mask.all() or keep_mask.sum() < min_points:
            break
        fit_table = fit_table.iloc[keep_mask].copy()

    x_values = fit_table["color_centered"].to_numpy(dtype=float)
    y_values = fit_table["residual"].to_numpy(dtype=float)
    design_matrix = np.column_stack([np.ones(len(x_values)), x_values])
    intercept, color_term = np.linalg.lstsq(design_matrix, y_values, rcond=None)[0]
    model = intercept + color_term * x_values
    residuals = y_values - model
    scatter = float(np.std(residuals, ddof=1)) if len(residuals) > 1 else 0.0

    # Parameter covariance of the final least-squares fit. It is the basis for
    # the per-source color-term uncertainty exported as ``*_colorcorr_err`` (see
    # ``apply_color_term_correction``): for a source at colour offset x,
    # var(offset) = var(intercept) + 2*x*cov(intercept, slope) + x^2*var(slope).
    n_points = len(y_values)
    dof = max(n_points - 2, 1)
    normal_matrix = design_matrix.T @ design_matrix
    try:
        normal_matrix_inv = np.linalg.inv(normal_matrix)
    except np.linalg.LinAlgError:
        normal_matrix_inv = np.linalg.pinv(normal_matrix)
    residual_variance = float(np.sum(residuals**2) / dof)
    parameter_covariance = residual_variance * normal_matrix_inv
    intercept_std = float(np.sqrt(max(parameter_covariance[0, 0], 0.0)))
    color_term_std = float(np.sqrt(max(parameter_covariance[1, 1], 0.0)))

    fig, ax = plt.subplots(figsize=(7, 5), dpi=100)
    ax.scatter(
        fit_table["source_color"],
        fit_table["residual"],
        alpha=0.7,
        color="tab:blue",
        label="Calibration stars",
    )
    color_grid = np.linspace(
        fit_table["source_color"].min(),
        fit_table["source_color"].max(),
        100,
    )
    model_grid = intercept + color_term * (color_grid - color_ref)
    ax.plot(color_grid, model_grid, color="tab:red", label="Linear color term")
    ax.axhline(0.0, color="gray", linestyle="--", linewidth=1)
    ax.set_xlabel(result["color_label"] or "Color")
    ax.set_ylabel("Catalog - (instrumental + zero point)")
    ax.set_title("Color-Term Fit")
    ax.grid(True, alpha=0.4)
    ax.legend()
    fig.tight_layout()

    result.update(
        {
            "fitted": True,
            "reason": None,
            "color_ref": color_ref,
            "color_term": float(color_term),
            "intercept": float(intercept),
            "effective_zero_point": float(zero_point) + float(intercept),
            "n_used": int(len(fit_table)),
            "scatter": scatter,
            "intercept_std": intercept_std,
            "color_term_std": color_term_std,
            "parameter_covariance": parameter_covariance.tolist(),
            "diagnostic_figure": fig,
        }
    )
    return result


def mask_and_remove_cosmic_rays(image_data, header, effective_gain=None):
    """
    Create a mask for saturated pixels and cosmic rays using L.A.Cosmic.

    L.A.Cosmic is fed the *effective* gain (e-/ADU), resolved exactly like the
    Poisson term of the error budget. Writing a detector driver setting to
    ``GAIN`` (for example the QHY 0-100 scale, which writes ``GAIN=56``) makes
    ``detect_cosmics`` predict a sky noise far below the real one: measured on a
    real 900 s frame with a 190 ADU sky and 13.3 ADU pixel noise, ``gain=56``
    flagged 49% of the frame as cosmic rays while ``gain=1`` flagged 0.01%.
    A mask larger than ``MAX_CR_MASK_FRACTION`` of the frame is therefore
    rejected as unphysical instead of silently deleting half the data.
    """
    # Safe parse of header SATURATE -> float, fallback to robust max
    sat_hdr = header.get("SATURATE", None)
    try:
        saturation = float(sat_hdr) if sat_hdr is not None else None
    except (ValueError, TypeError):
        saturation = None

    if saturation is None:
        saturation = 0.90 * np.nanmax(image_data)

    mask = np.isnan(image_data)  # mask NaNs in the input image
    mask |= image_data > saturation  # mask saturated pixels

    gain = (
        resolve_effective_gain(header, image_data)
        if effective_gain is None
        else effective_gain
    )

    # Read noise (e-) for the same noise model, when the header provides one.
    readnoise = None
    for key in ("RDNOISE", "RDNOIS", "READNOIS", "READNOISE", "ENOISE"):
        if key not in header:
            continue
        try:
            value = float(header[key])
        except (ValueError, TypeError):
            continue
        if np.isfinite(value) and 0 < value < 1000:
            readnoise = value
            break

    detect_kwargs = {"inmask": mask, "gain": float(gain), "verbose": False}
    if readnoise is not None:
        detect_kwargs["readnoise"] = readnoise

    # Run L.A.Cosmic (pass inmask explicitly)
    try:
        res = astroscrappy.detect_cosmics(image_data, **detect_kwargs)
        # detect_cosmics often returns a tuple; find the boolean CR mask
        if isinstance(res, tuple):
            crmask = None
            for el in res:
                if (
                    isinstance(el, np.ndarray)
                    and el.shape == image_data.shape
                    and el.dtype == bool
                ):
                    crmask = el
                    break
            if crmask is None:
                crmask = res[0].astype(bool)
        else:
            crmask = res.astype(bool)

        cr_fraction = float(np.mean(crmask))
        if cr_fraction > MAX_CR_MASK_FRACTION:
            st.warning(
                f"Cosmic-ray detection flagged {100 * cr_fraction:.1f}% of the "
                "frame, which is not physical; the cosmic-ray mask is discarded "
                "(saturated and NaN pixels stay masked). Check the detector "
                "gain used for the noise model."
            )
            crmask = np.zeros_like(mask, dtype=bool)
        else:
            st.info(
                f"Cosmic-ray mask covers {100 * cr_fraction:.3f}% of the frame "
                f"(effective_gain={float(gain):.4g} e-/ADU)."
            )
        st.success("Saturated pixels and Cosmic ray detection complete.")
    except Exception:
        st.warning("Cosmic ray detection failed, proceeding with saturation only mask.")
        crmask = np.zeros_like(mask, dtype=bool)

    mask |= crmask
    return mask


def make_border_mask(
    image: np.ndarray,
    border: Union[int, Tuple[int, int], Tuple[int, int, int, int]] = 50,
    invert: bool = True,
    dtype: np.dtype = bool,
) -> np.ndarray:
    """
    Create a binary mask for an image excluding one or more border regions.

    This function creates a mask that can be used to exclude border pixels
    from analysis, which is useful for operations that might be affected
    by edge artifacts.

    Parameters
    ----------
    image : numpy.ndarray
        The input image as a NumPy array
    border : int or tuple of int, default=50
        Border size(s) to exclude from the mask:
        - If int: same border size on all sides
        - If tuple of 2 elements: (vertical, horizontal) borders
        - If tuple of 4 elements: (top, bottom, left, right) borders
    invert : bool, default=True
        Selects which region the returned mask marks as True:
        - If True (default): border pixels are True (masked) and the inner
          region is False. This matches the "True = masked" convention used by
          the photometry pipeline.
        - If False: the inner (non-border) region is True and border pixels
          are False.
    dtype : numpy.dtype, default=bool
        Data type of the output mask

    Returns
    -------
    numpy.ndarray
        Binary mask with the same height and width as the input image.
        True marks the selected region: the border when ``invert=True``
        (the default), and the inner region when ``invert=False``.

    Raises
    ------
    TypeError
        If image is not a NumPy array or border is not of the correct type
    ValueError
        If image is empty, borders are negative, borders are larger than the image,
        or border tuple has an invalid length

    Examples
    --------
    >>> img = np.ones((100, 100))
    >>> # Create a mask with 10px border on all sides
    >>> mask = make_border_mask(img, 10)
    >>> # Create a mask with different vertical/horizontal borders
    >>> mask = make_border_mask(img, (20, 30))
    >>> # Custom borders per side, marking the inner region True instead:
    >>> mask = make_border_mask(img, (10, 20, 30, 40), invert=False)
    """
    import numbers

    if image is None:
        raise ValueError("Image cannot be None")
    if not isinstance(image, np.ndarray):
        raise TypeError("Image must be a numpy.ndarray")
    if image.size == 0:
        raise ValueError("Image cannot be empty")
    if image.ndim < 2:
        raise ValueError("Image must have at least 2 dimensions (height, width)")

    height, width = image.shape[:2]

    # Normalize border into four integer values: top, bottom, left, right
    if isinstance(border, numbers.Real):
        b = int(border)
        top = bottom = left = right = b
    elif isinstance(border, (tuple, list, np.ndarray)):
        if len(border) == 2:
            vert, horiz = border
            top = bottom = int(vert)
            left = right = int(horiz)
        elif len(border) == 4:
            top, bottom, left, right = (int(x) for x in border)
        else:
            raise ValueError("If 'border' is a sequence it must have length 2 or 4")
    else:
        raise TypeError(
            "border must be an int/float or a tuple/list/ndarray of length 2 or 4"
        )

    if any(b < 0 for b in (top, bottom, left, right)):
        raise ValueError("Borders cannot be negative")

    # require the inner region to have positive size
    if top + bottom >= height or left + right >= width:
        raise ValueError(
            "Sum of top+bottom must be < image height and left+right must be < image width"
        )

    # build bool mask first, then cast to requested dtype
    inner = np.zeros((height, width), dtype=bool)
    inner[top : height - bottom, left : width - right] = True

    mask = ~inner if invert else inner

    return mask.astype(dtype)


def detect_and_mask_satellite_trails(image_data, header, temp_fits_path=None):
    """
    Detect and mask satellite trails or airplane trails using ASTRiDE library.
    
    This function uses the ASTRiDE library to detect linear streaks (satellite/airplane trails)
    in astronomical images and creates a boolean mask to exclude these regions from analysis.
    
    Parameters
    ----------
    image_data : numpy.ndarray
        2D array of the image data
    header : dict or astropy.io.fits.Header
        FITS header containing image metadata
    temp_fits_path : str, optional
        Temporary path to save the image as FITS file for ASTRiDE processing.
        If None, a temporary file will be created and cleaned up.
        
    Returns
    -------
    numpy.ndarray
        Boolean mask where True indicates satellite trail pixels that should be masked
    
    Notes
    -----
    - Uses ASTRiDE library for streak detection
    - Creates a temporary FITS file for processing
    - The mask includes a buffer around detected streaks to ensure complete exclusion
    - Returns an empty mask (all False) if ASTRiDE detection fails
    """
    try:
        import tempfile
        import os
        from astropy.io import fits
        import astride.detect
        
        # Create temporary FITS file if not provided
        if temp_fits_path is None:
            temp_dir = tempfile.mkdtemp()
            temp_fits_path = os.path.join(temp_dir, "temp_image.fits")
            cleanup_temp = True
        else:
            cleanup_temp = False
            
        try:
            # ASTRiDE only needs the pixel data. Copying the science header is
            # harmful: one unparsable card makes writeto() fail and silently
            # disables the whole detection.
            fits.PrimaryHDU(np.asarray(image_data, dtype=np.float32)).writeto(
                temp_fits_path, overwrite=True
            )

            def _run_streak(remove_bkg, path=temp_fits_path):
                detector = astride.detect.Streak(
                    path,
                    remove_bkg=remove_bkg,
                    bkg_box_size=50,
                    contour_threshold=3.0,
                    min_points=15,
                    shape_cut=0.2,
                    area_cut=25.0,
                    radius_dev_cut=0.5,
                    connectivity_angle=3.0,
                    fully_connected="high",
                )
                detector.detect()
                return detector

            # A 2D background map is needed: with a constant background the
            # gradient (sky, vignetting) hides faint trails and only short
            # fragments pass the shape cuts.
            try:
                streak_detector = _run_streak("map")
            except Exception as map_error:
                # ASTRiDE's own 2D background can fail depending on the
                # installed photutils/astride versions. Subtract our own
                # background map instead of degrading to a constant one,
                # which only yields short fragments.
                st.warning(
                    f"ASTRiDE 'map' background failed ({map_error}); "
                    "using a photutils background subtraction instead."
                )
                from astropy.stats import SigmaClip
                from photutils.background import Background2D, MedianBackground

                bkg2d = Background2D(
                    np.asarray(image_data, dtype=np.float32),
                    (50, 50),
                    filter_size=(3, 3),
                    sigma_clip=SigmaClip(3.0),
                    bkg_estimator=MedianBackground(),
                )
                sub_path = os.path.splitext(temp_fits_path)[0] + "_sub.fits"
                fits.PrimaryHDU(
                    (np.asarray(image_data, dtype=np.float32) - bkg2d.background)
                    .astype(np.float32)
                ).writeto(sub_path, overwrite=True)
                try:
                    streak_detector = _run_streak("constant", sub_path)
                finally:
                    if os.path.exists(sub_path):
                        os.remove(sub_path)

            # Short candidates are elongated blobs/diffraction spikes, not
            # trails: keep only edges spanning a sizeable part of the frame.
            min_length = max(50.0, 0.05 * min(image_data.shape[:2]))
            streak_detector.streaks = [
                streak
                for streak in (streak_detector.streaks or [])
                if streak.get("length", 0.0) >= min_length
            ]

            # Create mask from detected streaks
            mask = np.zeros_like(image_data, dtype=bool)
            
            if streak_detector.streaks and len(streak_detector.streaks) > 0:
                # Create mask by drawing lines along the detected streaks
                # Use a buffer around each streak to ensure complete masking
                buffer_size = 5  # pixels buffer around each streak
                
                for streak in streak_detector.streaks:
                    x_coords = streak['x'].astype(int)
                    y_coords = streak['y'].astype(int)
                    
                    # Draw the streak line on the mask
                    for i in range(len(x_coords) - 1):
                        x1, y1 = x_coords[i], y_coords[i]
                        x2, y2 = x_coords[i + 1], y_coords[i + 1]
                        
                        # Draw line between points (Bresenham's line algorithm)
                        dx = abs(x2 - x1)
                        dy = abs(y2 - y1)
                        sx = 1 if x1 < x2 else -1
                        sy = 1 if y1 < y2 else -1
                        err = dx - dy
                        
                        while True:
                            # Mark the pixel and surrounding buffer
                            for bx in range(-buffer_size, buffer_size + 1):
                                for by in range(-buffer_size, buffer_size + 1):
                                    nx, ny = x1 + bx, y1 + by
                                    if (0 <= nx < mask.shape[1] and 
                                        0 <= ny < mask.shape[0]):
                                        mask[ny, nx] = True
                            
                            if x1 == x2 and y1 == y2:
                                break
                            
                            e2 = 2 * err
                            if e2 > -dy:
                                err -= dy
                                x1 += sx
                            if e2 < dx:
                                err += dx
                                y1 += sy
            else:
                st.warning("No satellite trails detected")
                
            return mask
            
        except Exception as e:
            st.warning(f"Error in satellite trail detection: {e}")
            return np.zeros_like(image_data, dtype=bool)
        finally:
            # Clean up temporary file if we created it
            if cleanup_temp and os.path.exists(temp_fits_path):
                try:
                    import shutil

                    shutil.rmtree(os.path.dirname(temp_fits_path), ignore_errors=True)
                except Exception:
                    pass
                    
    except ImportError:
        st.warning("ASTRiDE library not available. Satellite trail detection disabled.")
        return np.zeros_like(image_data, dtype=bool)
    except Exception as e:
        st.warning(f"Unexpected error in satellite trail detection: {e}")
        return np.zeros_like(image_data, dtype=bool)


def airmass(
    _header: Dict, observatory: Optional[Dict] = None, return_details: bool = False
) -> Union[float, Tuple[float, Dict]]:
    """
    Calculate the airmass for a celestial object from observation parameters.

    Airmass is a measure of the optical path length through Earth's atmosphere.
    This function calculates it from header information and observatory location,
    handling multiple coordinate formats and performing physical validity checks.

    Parameters
    ----------
    _header : Dict
        FITS header or dictionary containing observation information.
        Must include:
        - Coordinates (RA/DEC or OBJRA/OBJDEC)
        - Observation date (DATE-OBS)
    observatory : Dict, optional
        Information about the observatory. If not provided, uses the default.
        mat: {
            'name': str,           # Observatory name
            'latitude': float,     # Latitude in degrees
            'longitude': float,    # Longitude in degrees
            'elevation': float     # Elevation in meters
        }
    return_details : bool, optional
        If True, returns additional information about the observation.

    Returns
    -------
    Union[float, Tuple[float, Dict]]
        - If return_details=False: airmass (float)
        - If return_details=True: (airmass, observation_details)
          where observation_details is a dictionary with:
          - observatory name
          - datetime
          - target coordinates
          - altitude/azimuth
          - sun altitude
          - observation type (night/twilight/day)

    Notes
    -----
    Airmass is approximately sec(z) where z is the zenith angle.
    The function enforces physical constraints (airmass ≥ 1.0) and
    displays warnings for extreme values. It also determines whether
    the observation was taken during night, twilight or day.
    """

    # Check if airmass already exists in header
    airmass_keywords = ["AIRMASS", "SECZ", "AIRMASS_START", "AIRMASS_END"]
    for keyword in airmass_keywords:
        if keyword in _header and _header[keyword] is not None:
            try:
                existing_airmass = float(_header[keyword])
                # Validate the existing airmass value
                if 1.0 <= existing_airmass <= 30.0:
                    st.write("Using existing airmass from header...")
                    if return_details:
                        return existing_airmass, {"airmass_source": f"header_{keyword}"}
                    return existing_airmass
                else:
                    st.warning(
                        f"Invalid airmass value in header ({existing_airmass}), calculating from coordinates"
                    )
                    break
            except (ValueError, TypeError):
                st.warning(
                    f"Could not parse airmass value from header keyword {keyword}"
                )
                continue

    def get_observation_type(sun_alt):
        if sun_alt < -18:
            return "night"
        if sun_alt < 0:
            return "twilight"
        return "day"

    obs_data = observatory

    try:
        def _first_present(*keys):
            for key in keys:
                value = _header.get(key)
                if value is not None:
                    return value
            return None

        ra = _first_present("RA", "OBJRA", "CRVAL1")
        dec = _first_present("DEC", "OBJDEC", "CRVAL2")

        # Check for various date keywords in order of preference
        date_keywords = ["DATE-OBS", "DATE", "DATE_OBS", "DATEOBS", "MJD-OBS"]
        obstime_str = None

        for keyword in date_keywords:
            if keyword in _header and _header[keyword] is not None:
                obstime_str = _header[keyword]
                break

        if any(v is None for v in [ra, dec, obstime_str]):
            missing = []
            if ra is None:
                missing.append("RA/OBJRA/CRVAL1")
            if dec is None:
                missing.append("DEC/OBJDEC/CRVAL2")
            if obstime_str is None:
                missing.append("DATE-OBS/DATE")
            raise KeyError(f"Missing required header keywords: {', '.join(missing)}")

        if isinstance(ra, str) and (":" in ra or " " in ra.strip()):
            coord = SkyCoord(ra=ra, dec=dec, unit=(u.hourangle, u.deg), frame="icrs")
        else:
            coord = SkyCoord(ra=ra, dec=dec, unit=u.deg, frame="icrs")

        # Try to parse the observation time with different formats
        try:
            # First, try direct parsing
            obstime = Time(obstime_str)

        except Exception:
            # If that fails, try different parsing strategies
            try:
                # Handle common FITS date formats
                if isinstance(obstime_str, str):
                    # Remove any timezone info and try to parse
                    clean_date = obstime_str.replace("Z", "").replace("T", " ")
                    # Try ISO format
                    obstime = Time(clean_date, format="iso")
                else:
                    # If it's not a string, convert it
                    obstime = Time(str(obstime_str))
            except Exception:
                # Last resort: try as MJD if it's a number
                try:
                    mjd_value = float(obstime_str)
                    obstime = Time(mjd_value, format="mjd")
                except (ValueError, TypeError):
                    raise ValueError(
                        f"Could not parse observation time '{obstime_str}' in any recognized format"
                    )

        location = EarthLocation(
            lat=obs_data["latitude"] * u.deg,
            lon=obs_data["longitude"] * u.deg,
            height=obs_data["elevation"] * u.m,
        )

        altaz_frame = AltAz(obstime=obstime, location=location)
        altaz = coord.transform_to(altaz_frame)
        airmass_value = float(altaz.secz)

        if airmass_value < 1.0:
            st.warning("Calculated airmass is less than 1 (impossible)")
            airmass_value = 1.0
        elif airmass_value > 30.0:
            st.warning("Extremely high airmass (>30), object near horizon")

        sun_altaz = get_sun(obstime).transform_to(altaz_frame)
        sun_alt = float(sun_altaz.alt.deg)

        details = {
            "observatory": obs_data["name"],
            "datetime": obstime.iso,
            "target_coords": {
                "ra": coord.ra.to_string(unit=u.hour),
                "observation_type": get_observation_type(sun_alt),
            },
            "sun_altitude": round(sun_alt, 2),
            "observation_type": get_observation_type(sun_alt),
            "altaz": {
                "altitude": round(float(altaz.alt.deg), 2),
                "azimuth": round(float(altaz.az.deg), 2),
            },
        }

        st.write(f"Date and time of observation (UTC): {obstime.iso}")
        st.write(
            f"Altitude: {details['altaz']['altitude']}° \n"
            f"Azimuth: {details['altaz']['azimuth']}°"
        )

        if return_details:
            return round(airmass_value, 2), details
        return round(airmass_value, 2)

    except Exception as e:
        st.warning(f"Error calculating airmass: {str(e)}")
        if return_details:
            return 0.0, {}
        return 0.0


def fwhm_fit(
    _img: np.ndarray,
    fwhm: float,
    mask: Optional[np.ndarray] = None,
    std_lo: float = 0.5,
    std_hi: float = 0.5,
) -> Optional[float]:
    """
    Estimate the Full Width at Half Maximum (FWHM) of stars in an astronomical image.

    This function detects sources in the image using DAOStarFinder, filters them based on
    flux values, and fits 1D Gaussian models to the marginal distributions of each source
    to calculate FWHM values.

    Parameters
    ----------
    img : numpy.ndarray
        The 2D image array containing astronomical sources
    fwhm : float
        Initial FWHM estimate in pixels, used for source detection
    pixel_scale : float
        The pixel scale of the image in arcseconds per pixel
    mask : numpy.ndarray, optional
        Boolean mask array where True indicates pixels to ignore during source detection
    std_lo : float, default=0.5
        Lower bound for flux filtering, in standard deviations below median flux
    std_hi : float, default=0.5
        Upper bound for flux filtering, in standard deviations above median flux

    Returns
    -------
    float or None
        The median FWHM value in pixels (rounded to nearest integer) if successful,
        or None if no valid sources could be measured

    Notes
    -----
    The function uses marginal sums along the x and y dimensions and fits 1D Gaussian
    profiles to estimate FWHM. For each source, a box of size ~6×FWHM is extracted,
    and profiles are fit independently in both dimensions, with the results averaged.

    If more than 1000 sources are detected after filtering, a random sample of 1000
    sources is selected for FWHM calculation to improve performance.

    Progress updates and error messages are displayed using Streamlit.
    """
    try:
        # MAD-based noise, so the detection threshold matches the noise model
        # used by the error budget and is not inflated by undetected sources in
        # crowded fields (the plain std can over-estimate the noise by up to
        # ~2x at tight source spacing).
        _, _, clipped_std = sigma_clipped_stats(
            _img, sigma=3.0, stdfunc="mad_std"
        )

        daofind = DAOStarFinder(fwhm=1.5 * fwhm, threshold=7 * clipped_std)
        sources = daofind(_img, mask=mask)
        if sources is None:
            st.warning("No sources found !")
            return None, clipped_std

        flux = sources["flux"]
        median_flux = np.mean(flux)
        std_flux = np.std(flux)
        mask_flux = (flux > median_flux - std_lo * std_flux) & (
            flux < median_flux + std_hi * std_flux
        )
        filtered_sources = sources[mask_flux]

        filtered_sources = filtered_sources[~np.isnan(filtered_sources["flux"])]

        st.write(f"Sources after filtering : {len(filtered_sources)}")

        if len(filtered_sources) == 0:
            msg = "No valid sources for fitting found after filtering."
            st.error(msg)
            raise ValueError(msg)

        # Randomly sample 1250 sources if more than 1250 are available
        if len(filtered_sources) > 1000:
            indices = np.random.choice(len(filtered_sources), size=1000, replace=False)
            filtered_sources = filtered_sources[indices]
            st.warning("Too many sources, sampled 1000 sources for FWHM calculation")

        box_size = int(6 * round(fwhm))
        if box_size % 2 == 0:
            box_size += 1

        xypos = list(zip(filtered_sources["xcentroid"], filtered_sources["ycentroid"]))
        fwhms = fit_fwhm(_img, xypos=xypos, fit_shape=box_size)

        # fit_fwhm returns [fwhm_x, fwhm_y] for each source
        # Average x and y FWHM for each source, then take median
        if fwhms.ndim == 2 and fwhms.shape[1] == 2:
            # Average x and y for each source
            mean_fwhms_per_source = np.nanmean(fwhms, axis=1)
            mean_fwhm = np.nanmedian(mean_fwhms_per_source)
        else:
            # Fallback if already 1D
            mean_fwhm = np.median(fwhms)

        st.success(f"FWHM using gaussian model : {round(mean_fwhm, 2)} pixels")

        return round(mean_fwhm, 2), clipped_std
    except ValueError as e:
        raise e
    except Exception as e:
        st.error(f"Unexpected error in fwhm_fit: {e}")
        raise ValueError(f"Unexpected error in fwhm_fit: {e}")


def check_psf_aperture_consistency(
    phot_table,
    epsf_table,
    aperture_radii,
    max_offset_pixels: float = 1.0,
    warn_threshold: float = PSF_APERTURE_CONSISTENCY_THRESHOLD,
    min_matches: int = 10,
):
    """Compare PSF and aperture instrumental magnitudes source by source.

    Both photometry methods are placed on the same total-flux scale (aperture
    fluxes are corrected with the empirical-PSF encircled energy), so for a
    well-built ePSF the difference ``psf_mag - aperture_mag`` should be centred
    on zero. A large median offset is a diagnostic of an ePSF shape/normalisation
    problem, a corrupted aperture correction, or a local-background bias; it is
    reported with ``st.warning``.

    Parameters
    ----------
    phot_table : astropy.table.Table
        Aperture photometry table with the ``instrumental_mag*`` columns and
        centroid positions.
    epsf_table : astropy.table.Table or None
        PSF photometry table with an ``instrumental_mag`` column and positions.
    aperture_radii : sequence of float
        Aperture radii in FWHM units, used to build the ``*_X_X`` suffixes.
    max_offset_pixels : float, optional
        Positional tolerance for matching a PSF source to an aperture source.
    warn_threshold : float, optional
        Absolute median offset (mag) above which a warning is emitted.
    min_matches : int, optional
        Minimum number of matched sources required to report an aperture.

    Returns
    -------
    dict
        Diagnostics with ``per_aperture`` (matched count, median offset and
        robust scatter per radius) and the list of ``outliers``.
    """
    diagnostics = {
        "threshold": float(warn_threshold),
        "max_offset_pixels": float(max_offset_pixels),
        "n_psf": 0 if epsf_table is None else len(epsf_table),
        "n_aperture": 0 if phot_table is None else len(phot_table),
        "per_aperture": {},
        "outliers": [],
    }
    if phot_table is None or epsf_table is None or len(epsf_table) == 0:
        return diagnostics
    if "instrumental_mag" not in epsf_table.colnames:
        return diagnostics

    psf_x_col = next(
        (c for c in ("x_init", "xcentroid", "x_center", "xcenter")
         if c in epsf_table.colnames),
        None,
    )
    psf_y_col = next(
        (c for c in ("y_init", "ycentroid", "y_center", "ycenter")
         if c in epsf_table.colnames),
        None,
    )
    ap_x_col = next(
        (c for c in ("xcenter", "x_center", "x_centroid")
         if c in phot_table.colnames),
        None,
    )
    ap_y_col = next(
        (c for c in ("ycenter", "y_center", "y_centroid")
         if c in phot_table.colnames),
        None,
    )
    if None in (psf_x_col, psf_y_col, ap_x_col, ap_y_col):
        return diagnostics

    try:
        from scipy.spatial import cKDTree

        psf_xy = np.column_stack(
            [
                np.asarray(epsf_table[psf_x_col], dtype=float),
                np.asarray(epsf_table[psf_y_col], dtype=float),
            ]
        )
        ap_xy = np.column_stack(
            [
                np.asarray(phot_table[ap_x_col], dtype=float),
                np.asarray(phot_table[ap_y_col], dtype=float),
            ]
        )
        psf_mag = np.asarray(epsf_table["instrumental_mag"], dtype=float)
    except (KeyError, TypeError, ValueError):
        return diagnostics

    finite_psf = np.isfinite(psf_xy).all(axis=1) & np.isfinite(psf_mag)
    if not np.any(finite_psf) or len(ap_xy) == 0:
        return diagnostics

    tree = cKDTree(ap_xy)
    distances, indices = tree.query(psf_xy[finite_psf])
    matched = distances <= max_offset_pixels
    if not np.any(matched):
        return diagnostics
    psf_matched = psf_mag[finite_psf][matched]
    ap_indices = indices[matched]

    for radius in aperture_radii:
        suffix = f"_{str(radius).replace('.', '_')}"
        col = f"instrumental_mag{suffix}"
        if col not in phot_table.colnames:
            continue
        ap_mag = np.asarray(phot_table[col], dtype=float)[ap_indices]
        good = np.isfinite(psf_matched) & np.isfinite(ap_mag)
        n_matched = int(good.sum())
        entry = {
            "suffix": suffix,
            "n": n_matched,
            "median_diff": None,
            "scatter": None,
        }
        if n_matched >= min_matches:
            diff = psf_matched[good] - ap_mag[good]
            median_diff = float(np.median(diff))
            entry["median_diff"] = median_diff
            entry["scatter"] = float(
                1.4826 * np.median(np.abs(diff - median_diff))
            )
            if abs(median_diff) > warn_threshold:
                diagnostics["outliers"].append(suffix)
        diagnostics["per_aperture"][suffix] = entry

    if diagnostics["outliers"]:
        details = ", ".join(
            f"{suffix} {diagnostics['per_aperture'][suffix]['median_diff']:+.3f} mag"
            f" ({diagnostics['per_aperture'][suffix]['n']} sources)"
            for suffix in diagnostics["outliers"]
        )
        st.warning(
            "PSF and aperture magnitudes disagree (median PSF - aperture over "
            f"matched sources): {details}. Both methods are on the same "
            "total-flux scale, so a large offset indicates an ePSF "
            "shape/normalisation or local-background problem rather than noise."
        )
    else:
        reported = [
            entry
            for entry in diagnostics["per_aperture"].values()
            if entry["median_diff"] is not None
        ]
        if reported:
            summary = ", ".join(
                f"{entry['suffix']} {entry['median_diff']:+.3f}"
                f" (n={entry['n']})"
                for entry in reported
            )
            st.info(
                "PSF vs aperture consistency: median PSF - aperture magnitude "
                f"= {summary}."
            )
    return diagnostics


def detection_and_photometry(
    image_data, science_header, mean_fwhm_pixel, threshold_sigma, detection_mask,
    fwhm_radius_factor: float = 1.5,
):
    """
    Perform a complete photometry workflow on an astronomical image.

    This function executes the following steps:
      1. Estimate the sky background and its RMS.
      2. Detect sources using DAOStarFinder.
      3. Optionally refine the WCS using GAIA DR3 and stdpipe.
      4. Perform aperture photometry on detected sources.
      5. Perform PSF photometry using an empirical PSF model.

    Parameters
    ----------
    image_data : numpy.ndarray
        2D array of the image data.
    _science_header : dict or astropy.io.fits.Header
        FITS header or dictionary with image metadata.
    mean_fwhm_pixel : float
        Estimated FWHM in pixels, used for aperture and PSF sizing
    threshold_sigma : float
        Detection threshold in sigma above background.
    detection_mask : int
        Border size in pixels to mask during detection.
    filter_band : str
        Photometric band for catalog matching and calibration.
    gb : str, optional
        Gaia band name for catalog queries (default: "Gmag").

    Returns
    -------
    tuple
        (phot_table, epsf_table, daofind, bkg), where:
        phot_table : astropy.table.Table
            Results of aperture photometry.
        epsf_table : astropy.table.Table or None
            Results of PSF photometry, or None if PSF fitting failed.
        daofind : photutils.detection.DAOStarFinder
            DAOStarFinder object used for source detection.
        bkg : photutils.background.Background2D
            Background2D object containing the background model.

    Notes
    -----
    - WCS refinement uses stdpipe and GAIA DR3 if enabled in session state.
    - Adds RA/Dec coordinates to photometry tables if WCS is available.
    - Computes instrumental magnitudes for both aperture and PSF photometry.
    - Displays progress and results in the Streamlit interface.

    Photometric Formulas
    --------------------
    Signal-to-Noise Ratio (S/N):
        S/N = flux / flux_error
        where flux is background-corrected aperture sum (if available) or raw aperture sum

    Magnitude Error:
        σ_mag = (2.5 / ln(10)) × (σ_flux / flux) = 1.0857 × (σ_flux / flux)
        Derived from error propagation: mag = -2.5 × log10(flux)

    Instrumental Magnitude:
        m_inst = -2.5 × log10(flux)

    Quality Flags:
        - 'poor': S/N ≤ 5 (unreliable photometry)
        - 'marginal': 5 < S/N ≤ 10 (marginal quality)
        - 'good': S/N > 10 (reliable photometry)
        - 'unknown': missing data or calculation failed
    """
    daofind = None

    try:
        w, wcs_error, _ = safe_wcs_create(science_header)
        if w is None:
            st.error(f"Error creating WCS in detection_and_photometry: {wcs_error}")
            st.error(
                "Will attempt to proceed without WCS - coordinates will not be available"
            )
        else:
            st.success("WCS object created successfully in detection_and_photometry")
    except Exception as e:
        st.error(f"Exception while creating WCS in detection_and_photometry: {e}")
        w = None

    pixel_scale = science_header.get(
        "PIXSCALE",
        science_header.get("PIXSIZE", science_header.get("PIXELSCAL", 1.0)),
    )

    bkg, bkg_fig, bkg_error = estimate_background(image_data)

    if bkg is None:
        st.error(f"Error estimating background: {bkg_error}")
        return None, None, daofind, None, None, None, None, None

    # border mask (True = masked)
    border_mask = make_border_mask(image_data, border=detection_mask)

    # Resolve once so cosmic-ray detection and the photometry error model share
    # one gain and emit any user-facing fallback message only once.
    camera_gain = resolve_effective_gain(science_header, image_data)

    # cosmic-ray mask produced by LA Cosmic / custom routine (True = masked)
    try:
        cr_mask = mask_and_remove_cosmic_rays(
            image_data, science_header, effective_gain=camera_gain
        )
    except Exception:
        cr_mask = np.zeros_like(border_mask, dtype=bool)

    # satellite trail mask using ASTRiDE (True = masked)
    try:
        satellite_mask = detect_and_mask_satellite_trails(image_data, science_header)
    except Exception:
        satellite_mask = np.zeros_like(border_mask, dtype=bool)

    # normalize to boolean and ensure compatible shape
    def _to_bool_mask(arr, ref_shape):
        if arr is None:
            return np.zeros(ref_shape, dtype=bool)
        a = np.asarray(arr)
        if a.shape != ref_shape:
            if a.size == np.prod(ref_shape):
                try:
                    a = a.reshape(ref_shape)
                except Exception:
                    return np.zeros(ref_shape, dtype=bool)
            else:
                return np.zeros(ref_shape, dtype=bool)
        return a.astype(bool)

    final_mask = np.logical_or(
        _to_bool_mask(border_mask, image_data.shape[:2]),
        _to_bool_mask(cr_mask, image_data.shape[:2]),
    )
    # Add satellite trail mask to the final mask
    final_mask = np.logical_or(
        final_mask,
        _to_bool_mask(satellite_mask, image_data.shape[:2])
    )
    mask = final_mask

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.imshow(mask, cmap="viridis", origin="lower")
    st.pyplot(fig)
    plt.close(fig)
    if bkg_fig is not None:
        st.pyplot(bkg_fig)
        plt.close(bkg_fig)

    # Ensure image_sub is float64 to avoid casting errors
    image_sub = image_data.astype(np.float64) - bkg.background.astype(np.float64)

    show_subtracted_image(image_sub)

    # Ensure bkg_error is also float64
    bkg_error = np.full_like(
        image_sub, bkg.background_rms.astype(np.float64), dtype=np.float64
    )

    # Convert to float64 to ensure compatibility with calc_total_error
    total_error = calc_total_error(
        image_sub.astype(np.float64),
        bkg_error.astype(np.float64),
        effective_gain=np.float64(camera_gain),
    )

    st.write("Estimating FWHM ...")
    fwhm_estimate, clipped_std = fwhm_fit(image_sub, mean_fwhm_pixel, mask)

    if fwhm_estimate is None:
        st.warning("Failed to estimate FWHM. Using the initial estimate.")
        fwhm_estimate = mean_fwhm_pixel
    if clipped_std is None:
        _, _, clipped_std = sigma_clipped_stats(
            image_sub, sigma=3.0, stdfunc="mad_std"
        )

    daofind = DAOStarFinder(
        fwhm=1.5 * fwhm_estimate, threshold=(threshold_sigma + 0.5) * clipped_std
    )

    sources = daofind(image_sub, mask=mask)

    if sources is None or len(sources) == 0:
        st.warning("No sources found!")
        return None, None, daofind, bkg, None, None, fwhm_estimate, mask

    positions = np.transpose((sources["xcentroid"], sources["ycentroid"]))

    # Create multiple circular apertures with different radii
    aperture_radii = [1.1, 1.3]
    _rf = round(float(fwhm_radius_factor), 1)
    if _rf not in aperture_radii:
        aperture_radii.append(_rf)
    apertures = [
        CircularAperture(positions, r=radius * fwhm_estimate)
        for radius in aperture_radii
    ]

    # Create circular annulus apertures for background estimation
    annulus_apertures = []
    for radius in aperture_radii:
        r_in = 2.0 * radius * fwhm_estimate
        r_out = 2.5 * radius * fwhm_estimate
        annulus_apertures.append(CircularAnnulus(positions, r_in=r_in, r_out=r_out))

    try:
        wcs_obj = None
        if w is not None:
            try:
                # Use the refined WCS object instead of recreating from header
                wcs_obj = w
                if wcs_obj.pixel_n_dim > 2:
                    wcs_obj = wcs_obj.celestial
            except Exception as e:
                st.warning(f"Error creating WCS object: {e}")
                wcs_obj = None
        elif "CTYPE1" in science_header:
            try:
                # Fallback to header WCS if no refined WCS available
                wcs_obj = WCS(science_header)
                if wcs_obj.pixel_n_dim > 2:
                    wcs_obj = wcs_obj.celestial
            except Exception as e:
                st.warning(f"Error creating WCS object: {e}")
                wcs_obj = None

        # Perform photometry for all apertures
        phot_tables = []

        for i, (aperture, annulus) in enumerate(zip(apertures, annulus_apertures)):
            radius_suffix = f"_{str(aperture_radii[i]).replace('.', '_')}"

            # Aperture photometry on the background-subtracted image. The mask
            # (border + cosmic rays + satellite trails + NaNs) is applied so that
            # contaminated pixels are ignored by both the sum and its error.
            # Reference: https://photutils.readthedocs.io/en/3.0.0/user_guide/aperture.html
            phot_result = aperture_photometry(
                image_sub, aperture, error=total_error, mask=mask, wcs=wcs_obj
            )
            if "aperture_sum" in phot_result.colnames:
                phot_result.rename_column(
                    "aperture_sum", f"aperture_sum{radius_suffix}"
                )
            if "aperture_sum_err" in phot_result.colnames:
                phot_result.rename_column(
                    "aperture_sum_err", f"aperture_sum_err{radius_suffix}"
                )

            # Exact area actually covered by the aperture (accounts for masked
            # pixels, as recommended by the photutils documentation).
            aperture_area = np.asarray(
                aperture.area_overlap(image_sub, mask=mask), dtype=float
            )

            # Robust local background: sigma-clipped median in the annulus. A plain
            # mean is biased by neighbouring sources and cosmic rays.
            try:
                annulus_stats = ApertureStats(
                    image_sub,
                    annulus,
                    mask=mask,
                    error=total_error,
                    sigma_clip=SigmaClip(sigma=3.0, maxiters=10),
                )
                bkg_per_pixel = np.asarray(annulus_stats.median, dtype=float)
                bkg_std = np.asarray(annulus_stats.std, dtype=float)
                # Standard error of the MEDIAN estimator. photutils computes
                # median_err = sqrt(pi/2) * s / sqrt(n), i.e. the mean-based
                # std/sqrt(n) with the median factor (~1.2533) applied. Using the
                # mean's standard error for a median underestimates the local
                # background uncertainty by ~25% (and sigma_flux by ~8% in the
                # background-limited regime).
                try:
                    bkg_median_err = np.asarray(
                        annulus_stats.median_err, dtype=float
                    )
                except Exception:
                    bkg_median_err = None
                annulus_area = np.asarray(
                    annulus_stats.sum_aper_area.value, dtype=float
                )
            except Exception as annulus_error:
                st.warning(
                    f"Annulus background estimation failed{radius_suffix}: "
                    f"{annulus_error}"
                )
                bkg_per_pixel = np.zeros(len(positions), dtype=float)
                bkg_std = np.zeros(len(positions), dtype=float)
                bkg_median_err = None
                annulus_area = np.zeros(len(positions), dtype=float)

            # Total background inside the source aperture and its uncertainty.
            total_bkg = bkg_per_pixel * aperture_area
            with np.errstate(divide="ignore", invalid="ignore"):
                bkg_per_pixel_err = np.where(
                    annulus_area > 0, bkg_std / np.sqrt(annulus_area), np.nan
                )
            # Prefer the standard error of the median when photutils provides it;
            # keep the mean-based estimate as a fallback for older versions or
            # non-finite values.
            if bkg_median_err is not None:
                bkg_per_pixel_err = np.where(
                    np.isfinite(bkg_median_err), bkg_median_err, bkg_per_pixel_err
                )
            total_bkg_err = bkg_per_pixel_err * aperture_area

            ap_sum_col = f"aperture_sum{radius_suffix}"
            ap_err_col = f"aperture_sum_err{radius_suffix}"
            ap_sum = np.asarray(phot_result[ap_sum_col], dtype=float)
            if ap_err_col in phot_result.colnames:
                ap_err = np.asarray(phot_result[ap_err_col], dtype=float)
            else:
                ap_err = np.zeros(len(ap_sum), dtype=float)

            # Background-corrected source flux and its error. The variance of the
            # local background estimate (from the annulus) must be added to the
            # variance of the aperture sum; omitting it underestimates sigma_flux
            # and overestimates S/N.
            bkg_corrected = ap_sum - total_bkg
            bkg_corrected_err = np.sqrt(ap_err**2 + total_bkg_err**2)

            # Centroid-position contribution to the flux error. A fixed aperture
            # samples a different fraction of the PSF when the source is not
            # perfectly centred. sigma_pos uses the standard high-S/N
            # approximation sigma_PSF / S/N (floored, capped at half the FWHM),
            # and the flux change for a one-sigma displacement is measured
            # directly by re-measuring the aperture shifted by +/- sigma_pos
            # along x and y. This captures the real PSF asymmetry/ellipticity
            # instead of assuming a symmetric Gaussian (where the linear term
            # vanishes by symmetry).
            sigma_psf_pixels = float(fwhm_estimate) / GAUSSIAN_FWHM_TO_SIGMA
            with np.errstate(divide="ignore", invalid="ignore"):
                snr_prelim = np.where(
                    bkg_corrected_err > 0,
                    bkg_corrected / bkg_corrected_err,
                    np.nan,
                )
            sigma_pos = sigma_psf_pixels / np.maximum(np.abs(snr_prelim), 1.0)
            sigma_pos = np.clip(
                np.where(np.isfinite(sigma_pos), sigma_pos, 0.0),
                CENTROID_MIN_SIGMA_PIX,
                CENTROID_MAX_SIGMA_FRACTION * float(fwhm_estimate),
            )
            try:
                x_centers = np.asarray(positions)[:, 0]
                y_centers = np.asarray(positions)[:, 1]
                zeros = np.zeros_like(sigma_pos)
                shifted_sums = []
                for dx, dy in (
                    (sigma_pos, zeros),
                    (-sigma_pos, zeros),
                    (zeros, sigma_pos),
                    (zeros, -sigma_pos),
                ):
                    shifted_aperture = CircularAperture(
                        np.transpose((x_centers + dx, y_centers + dy)),
                        r=aperture.r,
                    )
                    shifted_result = aperture_photometry(
                        image_sub, shifted_aperture, mask=mask
                    )
                    shifted_sums.append(
                        np.asarray(shifted_result["aperture_sum"], dtype=float)
                    )
                f_x_plus, f_x_minus, f_y_plus, f_y_minus = shifted_sums
                # Half the (+/-) difference is |dF/dx| * sigma_pos; combine the
                # two orthogonal axes in quadrature.
                centroid_flux_err = 0.5 * np.sqrt(
                    (f_x_plus - f_x_minus) ** 2 + (f_y_plus - f_y_minus) ** 2
                )
            except Exception as centroid_error:
                st.warning(
                    f"Centroid flux error estimation failed{radius_suffix}: "
                    f"{centroid_error}"
                )
                centroid_flux_err = np.zeros(len(bkg_corrected), dtype=float)
            bkg_corrected_err = np.sqrt(
                bkg_corrected_err**2 + centroid_flux_err**2
            )

            phot_result[f"aperture_sum_bkg_corr{radius_suffix}"] = bkg_corrected
            phot_result[f"aperture_sum_bkg_corr_err{radius_suffix}"] = (
                bkg_corrected_err
            )
            phot_result[f"aperture_area{radius_suffix}"] = aperture_area
            phot_result[f"background{radius_suffix}"] = total_bkg
            phot_result[f"background_per_pixel{radius_suffix}"] = bkg_per_pixel
            phot_result[f"background_rms_per_pixel{radius_suffix}"] = bkg_per_pixel_err
            phot_result[f"centroid_flux_err{radius_suffix}"] = centroid_flux_err

            phot_tables.append(phot_result)

        # Combine all photometry results
        phot_table = phot_tables[0]
        for i in range(1, len(phot_tables)):
            # Add columns from additional apertures
            for col in phot_tables[i].colnames:
                if col not in phot_table.colnames:
                    phot_table[col] = phot_tables[i][col]

        phot_table["xcenter"] = sources["xcentroid"]
        phot_table["ycenter"] = sources["ycentroid"]

        # Build the empirical PSF first: its encircled-energy curve provides the
        # aperture correction that puts aperture and PSF photometry on the same
        # total-flux scale.
        try:
            epsf_table, epsf_model = perform_psf_photometry(
                image_sub, sources, fwhm_estimate, daofind, mask, total_error
            )
        except Exception as e:
            st.warning(
                f"Error performing EPSF photometry: {e}. Continuing with aperture photometry only."
            )
            epsf_table, epsf_model = None, None

        aperture_radii_pixels = [
            float(radius) * float(fwhm_estimate) for radius in aperture_radii
        ]
        if epsf_model is not None:
            ee_fractions = compute_encircled_energy(
                getattr(epsf_model, "data", None),
                getattr(epsf_model, "oversampling", 1),
                aperture_radii_pixels,
            )
            st.info("Aperture correction derived from the empirical PSF.")
        else:
            # Analytic Gaussian encircled energy as a fallback (the pipeline
            # already assumes a Gaussian profile when no ePSF can be built).
            sigma_psf = float(fwhm_estimate) / GAUSSIAN_FWHM_TO_SIGMA
            ee_fractions = np.array(
                [
                    1.0 - np.exp(-(r**2) / (2.0 * sigma_psf**2))
                    for r in aperture_radii_pixels
                ]
            )
            st.info("Aperture correction derived from a Gaussian PSF model.")

        # Single, consistent flux per aperture (background- AND aperture-corrected)
        # drives S/N, magnitude and magnitude error.
        for i, radius in enumerate(aperture_radii):
            radius_suffix = f"_{str(radius).replace('.', '_')}"
            bkg_corr_col = f"aperture_sum_bkg_corr{radius_suffix}"
            bkg_corr_err_col = f"aperture_sum_bkg_corr_err{radius_suffix}"

            if (
                bkg_corr_col in phot_table.colnames
                and bkg_corr_err_col in phot_table.colnames
            ):
                flux = np.asarray(phot_table[bkg_corr_col], dtype=float)
                flux_err = np.asarray(phot_table[bkg_corr_err_col], dtype=float)

                ee = float(ee_fractions[i]) if i < len(ee_fractions) else 1.0
                apcorr = 1.0 / ee if 0.0 < ee <= 1.0 else 1.0
                # An aperture correction only ever adds flux; clamp it so a
                # pathological PSF cannot produce a runaway magnitude shift.
                apcorr = min(max(apcorr, 1.0), 2.0)
                phot_table[f"apcorr{radius_suffix}"] = apcorr
                # Encircled-energy fraction behind the correction, for auditing:
                # apcorr = 1/ee (clamped), so a large apcorr is traceable to a
                # poorly-enclosed aperture rather than a silent magnitude offset.
                phot_table[f"ee_fraction{radius_suffix}"] = ee

                # Systematic uncertainty of the aperture correction itself. The
                # correction shifts the (log) flux by ~(apcorr - 1)/apcorr, so a
                # fractional uncertainty in the encircled energy translates to
                # that relative shift times MAG_ERR_COEFFICIENT in magnitudes.
                # Unlike the statistical error it does NOT cancel in the flux
                # ratio, and it is stored separately for auditing.
                apcorr_mag_sys = (
                    MAG_ERR_COEFFICIENT
                    * APCORR_SYSTEMATIC_FRACTION
                    * abs(apcorr - 1.0)
                    / apcorr
                )
                phot_table[f"apcorr_mag_err{radius_suffix}"] = apcorr_mag_sys

                flux_corr = flux * apcorr
                flux_corr_err = flux_err * apcorr

                # S/N is invariant under the multiplicative aperture correction.
                # Formula: S/N = flux / flux_error
                with np.errstate(divide="ignore", invalid="ignore"):
                    snr = np.where(flux_err > 0, flux / flux_err, np.nan)
                phot_table[f"snr{radius_suffix}"] = snr

                # Formula: sigma_mag = 1.0857 * (sigma_flux / flux), evaluated with
                # the SAME flux used for the magnitude itself. The aperture
                # correction adds a systematic term that does not cancel here.
                with np.errstate(divide="ignore", invalid="ignore"):
                    mag_err = np.where(
                        flux_corr > 0,
                        MAG_ERR_COEFFICIENT * flux_corr_err / flux_corr,
                        np.nan,
                    )
                mag_err = np.sqrt(mag_err**2 + apcorr_mag_sys**2)
                phot_table[f"aperture_mag_err{radius_suffix}"] = mag_err

                instrumental_mags = np.full(len(phot_table), np.nan, dtype=float)
                positive = np.isfinite(flux_corr) & (flux_corr > 0)
                instrumental_mags[positive] = -2.5 * np.log10(flux_corr[positive])
                phot_table[f"instrumental_mag{radius_suffix}"] = instrumental_mags

                # Reference (uncorrected, background-subtracted) magnitude
                uncorrected_mags = np.full(len(phot_table), np.nan, dtype=float)
                positive_raw = np.isfinite(flux) & (flux > 0)
                uncorrected_mags[positive_raw] = -2.5 * np.log10(flux[positive_raw])
                phot_table[f"instrumental_mag_bkg_corr{radius_suffix}"] = (
                    uncorrected_mags
                )

                quality_flag = np.where(
                    ~np.isfinite(snr),
                    "unknown",
                    np.where(
                        snr <= 5, "poor", np.where(snr <= 10, "marginal", "good")
                    ),
                )
                phot_table[f"quality_flag{radius_suffix}"] = quality_flag
            else:
                phot_table[f"snr{radius_suffix}"] = np.nan
                phot_table[f"apcorr_mag_err{radius_suffix}"] = np.nan
                phot_table[f"ee_fraction{radius_suffix}"] = np.nan
                phot_table[f"aperture_mag_err{radius_suffix}"] = np.nan
                phot_table[f"instrumental_mag{radius_suffix}"] = np.nan
                phot_table[f"instrumental_mag_bkg_corr{radius_suffix}"] = np.nan
                phot_table[f"quality_flag{radius_suffix}"] = "unknown"

        if epsf_table is not None:
            # SNR for PSF photometry (no rounding, flux_err is already std dev not variance)
            # Formula: S/N = flux_fit / flux_err
            # Note: PSFPhotometry returns flux_err as standard deviation, not variance
            _flux_fit = np.asarray(epsf_table["flux_fit"], dtype=float)
            _flux_err = np.asarray(epsf_table["flux_err"], dtype=float)
            _valid = (_flux_err > 0) & (_flux_fit > 0)
            with np.errstate(divide="ignore", invalid="ignore"):
                epsf_table["snr"] = np.where(_valid, _flux_fit / _flux_err, np.nan)
                # Formula: sigma_mag = 1.0857 * (sigma_flux / flux)
                m_err = np.where(
                    _valid, MAG_ERR_COEFFICIENT * _flux_err / _flux_fit, np.nan
                )
            epsf_table["psf_mag_err"] = m_err

            # Add quality flag for PSF photometry
            psf_snr = epsf_table["snr"]
            psf_quality_flag = np.where(
                ~np.isfinite(psf_snr),
                "unknown",
                np.where(
                    psf_snr <= 5,
                    "poor",
                    np.where(psf_snr <= 10, "marginal", "good"),
                ),
            )
            epsf_table["psf_quality_flag"] = psf_quality_flag

            epsf_instrumental_mags = -2.5 * np.log10(epsf_table["flux_fit"])
            epsf_table["instrumental_mag"] = epsf_instrumental_mags
        else:
            st.warning(
                "PSF photometry was not performed. Continuing with aperture photometry only."
            )

        # Cross-check the two independent photometry methods. Both are placed on
        # the same total-flux scale (aperture fluxes via the empirical-PSF
        # encircled energy), so a large median offset flags an ePSF
        # shape/normalisation or local-background problem worth investigating.
        try:
            st.session_state["psf_aperture_consistency"] = (
                check_psf_aperture_consistency(
                    phot_table, epsf_table, aperture_radii
                )
            )
        except Exception as consistency_error:
            st.warning(
                f"PSF/aperture consistency check failed: {consistency_error}"
            )

        # Use the first aperture's columns (since "aperture_sum" was renamed)
        first_aperture_suffix = f"_{str(aperture_radii[0]).replace('.', '_')}"
        first_aperture_col = f"aperture_sum{first_aperture_suffix}"
        first_bkg_corr_col = f"aperture_sum_bkg_corr{first_aperture_suffix}"
        first_mag_col = f"instrumental_mag{first_aperture_suffix}"

        flux_for_filter = (
            phot_table[first_bkg_corr_col]
            if first_bkg_corr_col in phot_table.colnames
            else phot_table[first_aperture_col]
        )
        valid_sources = (flux_for_filter > 0) & np.isfinite(phot_table[first_mag_col])
        phot_table = phot_table[valid_sources]

        if epsf_table is not None:
            epsf_valid_sources = np.isfinite(epsf_table["instrumental_mag"])
            epsf_table = epsf_table[epsf_valid_sources]

        try:
            if wcs_obj is not None:
                ra, dec = wcs_obj.pixel_to_world_values(
                    phot_table["xcenter"], phot_table["ycenter"]
                )
                phot_table["ra"] = ra * u.deg
                phot_table["dec"] = dec * u.deg
                st.success(f"Added RA/Dec coordinates to {len(phot_table)} sources")

                if epsf_table is not None:
                    try:
                        epsf_ra, epsf_dec = wcs_obj.pixel_to_world_values(
                            epsf_table["x_fit"], epsf_table["y_fit"]
                        )
                        epsf_table["ra"] = epsf_ra * u.deg
                        epsf_table["dec"] = epsf_dec * u.deg
                        st.success(
                            f"Added RA/Dec coordinates to {len(epsf_table)} PSF sources"
                        )
                    except Exception as e:
                        st.warning(f"Could not add coordinates to EPSF table: {e}")
            else:
                st.warning("No WCS object available - coordinates will not be added")
                if all(
                    key in science_header for key in ["RA", "DEC", "NAXIS1", "NAXIS2"]
                ):
                    center_ra = science_header["RA"]
                    center_dec = science_header["DEC"]
                    width = science_header["NAXIS1"]
                    height = science_header["NAXIS2"]

                    pix_scale = pixel_scale or 1.0

                    center_x = width / 2
                    center_y = height / 2

                    for i, row in enumerate(phot_table):
                        x = row["xcenter"]
                        y = row["ycenter"]
                        dx = (x - center_x) * pix_scale / 3600.0
                        dy = (y - center_y) * pix_scale / 3600.0
                        phot_table[i]["ra"] = center_ra + dx / np.cos(
                            np.radians(center_dec)
                        )
                        phot_table[i]["dec"] = center_dec + dy
        except Exception as e:
            st.warning(
                f"WCS transformation failed: {e}. RA and Dec not added to tables."
            )

        st.write(f"Found {len(phot_table)} sources and performed photometry.")
        return (
            phot_table,
            epsf_table,
            daofind,
            bkg,
            wcs_obj,
            bkg_fig,
            fwhm_estimate,
            mask,
        )
    except Exception as e:
        st.error(f"Error performing aperture photometry: {e}")
        return None, None, daofind, bkg, wcs_obj, None, fwhm_estimate, mask


def show_subtracted_image(image_sub):
    """
    Display the background-subtracted image using Streamlit.
    Parameters
    ----------
    image_sub : numpy.ndarray
        The background-subtracted image data.
    """
    fig, ax = plt.subplots(figsize=(7, 7))
    zscale = ZScaleInterval()
    vmin, vmax = zscale.get_limits(image_sub)
    im = ax.imshow(image_sub, origin="lower", cmap="viridis", vmin=vmin, vmax=vmax)
    ax.set_title("Background-subtracted image")
    plt.colorbar(im, ax=ax, label="Flux")
    st.pyplot(fig)
    plt.close(fig)


def select_residual_error_column(matched_table, residuals, radius_suffix="1_3"):
    """Return the magnitude-error array used for the residuals error bars.

    The residuals plot always uses the fixed 1.3x aperture error, regardless
    of the user-defined FWHM radius factor, falling back to the generic
    ``aperture_mag_err`` column and finally to zeros.

    Parameters
    ----------
    matched_table : pandas.DataFrame
        Cross-matched table that may hold one of the error columns.
    residuals : numpy.ndarray
        Residuals array; only its shape is used for the zero fallback.

    Returns
    -------
    numpy.ndarray
        Error values aligned with *residuals*.
    """
    aperture_err_col = f"aperture_mag_err_{radius_suffix}"
    if aperture_err_col not in matched_table.columns:
        aperture_err_col = "aperture_mag_err_1_3"
    if aperture_err_col in matched_table.columns:
        return matched_table[aperture_err_col].values
    if "aperture_mag_err" in matched_table.columns:
        return matched_table["aperture_mag_err"].values
    return np.zeros_like(residuals)


def calculate_zero_point(_phot_table, _matched_table, filter_band, air, fwhm_radius_factor: float = 1.5):
    """
    Calculate photometric zero point from matched sources with GAIA.

    The zero point transforms instrumental magnitudes to the standard GAIA magnitude system,
    accounting for atmospheric extinction using the provided airmass.

    Parameters
    ----------
    _phot_table : astropy.table.Table or pandas.DataFrame
        Photometry results table (underscore prevents caching issues)
    _matched_table : pandas.DataFrame
        Table of GAIA cross-matched sources (used for calibration)
    filter_band : str
        GAIA magnitude band used (e.g., 'phot_g_mean_mag')
    air : float
        Airmass value for atmospheric extinction correction

    Returns
    -------
    tuple
        (zero_point_value, zero_point_std, matplotlib_figure) where:
        - zero_point_value: Float value of the calculated zero point
        - zero_point_std: Standard deviation of the zero point
        - matplotlib_figure: Figure object showing GAIA vs. calibrated magnitudes

    Notes
    -----
    - Uses sigma clipping to remove outliers from zero point calculation
    - Applies a simple atmospheric extinction correction of 0.1*airmass
    - Stores the calibrated photometry table in session state as 'final_phot_table'
    - Creates and saves a plot showing the relation between GAIA and calibrated magnitudes
    """
    if _matched_table is None or len(_matched_table) == 0:
        st.warning("No matched sources to calculate zero point.")
        return None, None, None

    try:
        # Define aperture radii (should match the ones used in detection_and_photometry)
        aperture_radii = [1.1, 1.3]
        _rf = round(float(fwhm_radius_factor), 1)
        if _rf not in aperture_radii:
            aperture_radii.append(_rf)

        # Use the 1.3 aperture as the default for zero point calculation.
        # This preserves support for computing additional aperture magnitudes
        # while anchoring the ZP fit to the requested calibration aperture.
        default_radius = 1.3
        radius_suffix = f"_{str(default_radius).replace('.', '_')}"
        instrumental_mag_col = f"instrumental_mag{radius_suffix}"

        # Check if the column exists in matched table
        if instrumental_mag_col not in _matched_table.columns:
            st.error(
                f"Column '{instrumental_mag_col}' not found in matched table. Available columns: {list(_matched_table.columns)}"
            )
            return None, None, None

        valid = np.isfinite(_matched_table[instrumental_mag_col]) & np.isfinite(
            _matched_table[filter_band]
        )
        matched_table = _matched_table.loc[valid].copy()

        if len(matched_table) == 0:
            st.warning("No valid matched sources for zero point calculation.")
            return None, None, None

        # Robust zero-point estimation using iterative sigma clipping.
        # Uses median as centre and MAD-based scale, iterating until convergence.
        # The same clip mask is applied to matched_table so the downstream plots
        # and the ZP value are always derived from the identical set of stars.
        zero_points_raw = (
            matched_table[filter_band] - matched_table[instrumental_mag_col]
        ).values
        clipped = sigma_clip(
            zero_points_raw, sigma=3.0, maxiters=10,
            cenfunc="median", stdfunc="mad_std"
        )

        # np.ma.getmaskarray returns a proper boolean array even when sigma_clip
        # clips nothing and the mask is the nomask singleton.
        keep_mask = ~np.ma.getmaskarray(clipped)
        if not keep_mask.any():
            st.warning("All matched sources removed by sigma clipping for zero point.")
            return None, None, None

        # Restrict matched_table to the accepted calibration stars so that the
        # same rows drive the ZP value, the column assignments, and the plots.
        matched_table = matched_table.iloc[keep_mask].copy()
        zero_points_clean = clipped.compressed()  # unmasked values only
        # Keep the sigma-clipped sample BEFORE the strict hard cut: the hard cut
        # truncates the distribution and biases the scatter (and hence the
        # zero-point uncertainty) low.
        zero_points_clipped = zero_points_clean.copy()

        # Apply an additional strict hard-cut on absolute residuals.
        # This keeps only calibrators within +/- 0.5 mag from the ZP centre.
        residual_limit_mag = 0.5
        zp_center = float(np.median(zero_points_clean))
        strict_keep = np.abs(zero_points_clean - zp_center) <= residual_limit_mag
        if not strict_keep.any():
            st.warning(
                "All matched sources removed by strict residual filtering "
                f"(|residual| <= {residual_limit_mag:.1f} mag)."
            )
            return None, None, None

        matched_table = matched_table.iloc[strict_keep].copy()
        zero_points_clean = zero_points_clean[strict_keep]

        zero_point_value = float(np.median(zero_points_clean))
        n_cal = len(zero_points_clean)
        # Scatter of the retained calibrators (informative, stored in the table).
        # The |residual| <= 0.5 mag cut truncates the distribution, so this value
        # is biased low and is reported for reference only.
        if n_cal > 1:
            kept_scatter = float(np.std(zero_points_clean, ddof=1))
        else:
            kept_scatter = 0.0
        # Scatter used for the formal uncertainty: the sigma-CLIPPED sample
        # BEFORE the hard +/-0.5 mag cut. The hard cut removes the tails and
        # biases the scatter (and sigma_zp) low; the sigma clip already removes
        # true outliers, so this is the best available estimate of the intrinsic
        # dispersion.
        if len(zero_points_clipped) > 1:
            zero_point_scatter = float(np.std(zero_points_clipped, ddof=1))
        else:
            zero_point_scatter = kept_scatter
        # Formal uncertainty on the ZP estimator. The zero point is the MEDIAN
        # of the calibrator residuals, whose standard error is
        # sqrt(pi/2) * scatter / sqrt(N) -- about 25% larger than the
        # mean-based sigma/sqrt(N). The scatter comes from the untrimmed (but
        # sigma-clipped) sample to avoid the truncation bias.
        zero_point_std = MEDIAN_STD_ERROR_FACTOR * zero_point_scatter / np.sqrt(n_cal)

        # Expose the zero-point diagnostics (including both scatter estimates)
        # for logging and tests. ``scatter`` is the untrimmed (sigma-clipped)
        # value that drives ``std``; ``scatter_kept`` is the truncated one.
        try:
            st.session_state["zero_point_diagnostics"] = {
                "value": float(zero_point_value),
                "std": float(zero_point_std),
                "scatter": float(zero_point_scatter),
                "scatter_kept": float(kept_scatter),
                "n_cal": int(n_cal),
                "n_raw": int(len(zero_points_raw)),
            }
        except Exception:
            pass

        if np.isnan(zero_point_value):
            st.warning("Zero point calculation returned NaN.")
            return None, None, None

        st.info(
            f"Sigma-clipping + strict residual cut (|residual| <= {residual_limit_mag:.1f} mag) "
            f"kept {n_cal} / {len(zero_points_raw)} calibration stars "
            f"(scatter = {kept_scatter:.3f} mag retained, "
            f"{zero_point_scatter:.3f} mag untrimmed)."
        )

        _matched_table = matched_table

        _matched_table["zero_point"] = (
            _matched_table[filter_band] - _matched_table[instrumental_mag_col]
        )
        # Store the calibrator scatter (NOT the formal zero-point uncertainty,
        # which is zero_point_std = 1.2533 * scatter / sqrt(N)) under a name that
        # says so, to avoid it being mistaken for an error.
        _matched_table["zero_point_scatter"] = zero_point_scatter
        _matched_table["zero_point_scatter_kept"] = kept_scatter
        _matched_table["zero_point_std"] = zero_point_std
        _matched_table["zero_point_n"] = n_cal

        if not isinstance(_phot_table, pd.DataFrame):
            _phot_table = _phot_table.to_pandas()

        # Remove old single-aperture columns if they exist
        old_columns = ["aperture_mag", "aperture_instrumental_mag", "aperture_mag_err"]

        for col in old_columns:
            if col in _phot_table.columns:
                _phot_table.drop(columns=[col], inplace=True)

        # Pin the default aperture suffix now, before the loops below overwrite
        # radius_suffix with the last element of aperture_radii.
        default_radius_suffix = radius_suffix

        # Add calibrated magnitudes for all aperture radii
        for radius in aperture_radii:
            radius_suffix = f"_{str(radius).replace('.', '_')}"
            instrumental_col = f"instrumental_mag{radius_suffix}"
            aperture_mag_col = f"aperture_mag{radius_suffix}"

            if instrumental_col in _phot_table.columns:
                _phot_table[aperture_mag_col] = (
                    _phot_table[instrumental_col] + zero_point_value  # - 0.09 * air
                )

        # Also apply to matched table for all aperture radii
        for radius in aperture_radii:
            radius_suffix = f"_{str(radius).replace('.', '_')}"
            instrumental_col = f"instrumental_mag{radius_suffix}"
            aperture_mag_col = f"aperture_mag{radius_suffix}"

            if instrumental_col in _matched_table.columns:
                _matched_table[aperture_mag_col] = (
                    _matched_table[instrumental_col] + zero_point_value  # - 0.09 * air
                )

        st.session_state["final_phot_table"] = _phot_table

        fig, (ax, ax_resid) = plt.subplots(1, 2, figsize=(14, 6), dpi=100)

        # Use the default aperture radius for plotting (pinned before the loops)
        aperture_mag_col = f"aperture_mag{default_radius_suffix}"

        # Check if the aperture_mag column exists before calculating residuals
        if aperture_mag_col not in _matched_table.columns:
            st.warning(
                f"Column '{aperture_mag_col}' not found. Cannot create calibration plots."
            )
            # Keep full precision so the zero-point uncertainty can be propagated
            # into the calibrated magnitude errors downstream.
            return float(zero_point_value), float(zero_point_std), None

        # Calculate residuals
        _matched_table["residual"] = (
            _matched_table[filter_band] - _matched_table[aperture_mag_col]
        )

        # Plots use the 1.1xFWHM aperture (comparison with PSF photometry);
        # the zero point itself is still anchored on the default aperture.
        _plot_suffix = "_1_1"
        plot_mag_col = f"aperture_mag{_plot_suffix}"
        plot_inst_col = f"instrumental_mag{_plot_suffix}"
        if (
            plot_mag_col not in _matched_table.columns
            or plot_inst_col not in _matched_table.columns
        ):
            _plot_suffix = default_radius_suffix
            plot_mag_col = aperture_mag_col
            plot_inst_col = instrumental_mag_col
        _plot_label = _plot_suffix.lstrip("_").replace("_", ".")

        # Left plot: Zero point calibration
        ax.scatter(
            _matched_table[filter_band],
            _matched_table[plot_mag_col],
            alpha=0.5,
            label="Matched sources",
            color="blue",
        )

        # Calculate and plot regression line with variance
        x_data = _matched_table[filter_band].values
        y_data = _matched_table[plot_mag_col].values

        # Remove any NaN values for regression
        valid_mask = np.isfinite(x_data) & np.isfinite(y_data)
        x_clean = x_data[valid_mask]
        y_clean = y_data[valid_mask]

        if len(x_clean) > 1:
            # Calculate linear regression
            coeffs = np.polyfit(x_clean, y_clean, 1)
            slope, intercept = coeffs

            # Create regression line points
            x_reg = np.linspace(x_clean.min(), x_clean.max(), 100)
            y_reg = slope * x_reg + intercept

            # Calculate residuals and standard deviation
            y_pred = slope * x_clean + intercept
            residuals = y_clean - y_pred
            std_residuals = np.std(residuals)

            # Plot regression line
            ax.plot(
                x_reg, y_reg, "r-", linewidth=2, label=f"Regression (slope={slope:.3f})"
            )

            # Plot variance bands (±1σ and ±2σ)
            ax.fill_between(
                x_reg,
                y_reg - std_residuals,
                y_reg + std_residuals,
                alpha=0.3,
                color="red",
                label=f"±1σ ({std_residuals:.3f} mag)",
            )
            ax.fill_between(
                x_reg,
                y_reg - 2 * std_residuals,
                y_reg + 2 * std_residuals,
                alpha=0.15,
                color="red",
                label=f"±2σ ({2 * std_residuals:.3f} mag)",
            )

        # Add a diagonal line for reference
        mag_range = [
            min(
                _matched_table[filter_band].min(),
                _matched_table[plot_mag_col].min(),
            ),
            max(
                _matched_table[filter_band].max(),
                _matched_table[plot_mag_col].max(),
            ),
        ]
        ideal_mag = np.linspace(mag_range[0], mag_range[1], 100)
        ax.plot(ideal_mag, ideal_mag, "k--", alpha=0.7)

        ax.set_xlabel(f"Gaia {filter_band}")
        ax.set_ylabel(f"Calib mag ({_plot_label}×FWHM aperture)")
        ax.set_title("Gaia magnitude vs Calibrated magnitude")
        ax.legend()
        ax.grid(True, alpha=0.5)

        # Set axis limits to match the actual data range (not auto-scaled)
        margin = 0.5  # Add small margin for readability
        ax.set_xlim(mag_range[0] - margin, mag_range[1] + margin)
        ax.set_ylim(mag_range[0] - margin, mag_range[1] + margin)

        # Right plot: Residuals
        mag_cat = _matched_table[filter_band]
        mag_inst = _matched_table[plot_inst_col]
        zp_mean = zero_point_value
        residuals = mag_cat - (mag_inst + zp_mean)

        # Error bars use the plotted (1.1×) aperture error, independent of the
        # user-defined FWHM radius factor.
        aperture_mag_err = select_residual_error_column(
            _matched_table, residuals, _plot_suffix.lstrip("_")
        )

        zp_err = zero_point_std if zero_point_std is not None else 0.0
        yerr = np.sqrt(aperture_mag_err**2 + zp_err**2)

        ax_resid.errorbar(
            mag_cat,
            residuals,
            yerr=yerr,
            fmt="o",
            markersize=5,
            alpha=0.7,
            label="Residuals",
        )
        ax_resid.axhline(0, color="gray", ls="--")
        ax_resid.set_xlabel("Calibrated magnitude")
        ax_resid.set_ylabel("Residual (catalog - calibrated)")
        ax_resid.set_title(f"Photometric Residuals ({_plot_label}×FWHM aperture)")
        ax_resid.grid(True, alpha=0.5)
        ax_resid.legend()

        # Adjust layout and display
        fig.tight_layout()
        st.pyplot(fig)

        st.success(
            f"Calculated Zero Point: {zero_point_value:.2f} "
            f"± {zero_point_std:.3f} "
            f"calibrator scatter = {zero_point_scatter:.3f} mag"
        )

        try:
            base_name = st.session_state.get("base_filename", "photometry")
            username = st.session_state.get("username", "anonymous")
            output_dir = session_results_path(
                ensure_output_directory(directory=f"{username}_results"),
                st.session_state.get("session_id"),
            )
            zero_point_plot_path = os.path.join(
                output_dir, f"{base_name}_zero_point_plot.png"
            )
            fig.savefig(zero_point_plot_path)
        except Exception as e:
            st.warning(f"Could not save plot to file: {e}")

        # The figure was displayed and saved above: release it from the
        # process-wide pyplot registry shared by all sessions.
        plt.close(fig)

        # Keep full precision so the zero-point uncertainty can be propagated
        # into the calibrated magnitude errors downstream.
        return float(zero_point_value), float(zero_point_std), fig
    except Exception as e:
        st.error(f"Error calculating zero point: {e}")
        import traceback

        st.error(traceback.format_exc())
        return None, None, None
