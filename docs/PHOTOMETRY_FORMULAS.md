# Photometry Formulas and Error Calculations

This document provides detailed mathematical documentation for the photometric calculations used in the pipeline.

## Table of Contents
1. [Signal-to-Noise Ratio (S/N)](#signal-to-noise-ratio-sn)
2. [Magnitude Error Calculation](#magnitude-error-calculation)
3. [Error Propagation](#error-propagation)
4. [Quality Flags](#quality-flags)
5. [References](#references)

---

## Signal-to-Noise Ratio (S/N)

### Formula

```
S/N = flux / flux_error
```

Where:
- `flux`: Source flux (background-corrected and aperture-corrected, see below)
- `flux_error`: Uncertainty in flux measurement (σ_flux), including the
  uncertainty of the local background estimate

### Implementation Details

**Aperture Photometry:**
- Computes the background-corrected flux `aperture_sum_bkg_corr` from a robust
  sigma-clipped median annulus and uses it for the magnitude, its error and the
  S/N (a single, consistent flux)
- `flux_error` is `aperture_sum_bkg_corr_err`, i.e. the quadrature sum of the
  aperture error, the local-background error and the centroid term. This column
  is produced by `detection_and_photometry` and drives the magnitude error, but
  it is not retained in the exported catalog
- No rounding applied to preserve precision

**PSF Photometry:**
- Uses `flux_fit` from PSF model fitting
- `flux_err` from PSFPhotometry is already the standard deviation (not variance)
- A **local background** is subtracted per source before fitting. A
  sigma-clipped median annulus (`LocalBackground`, radii 2× and 3× FWHM) is
  evaluated per source and the values are passed as the `local_bkg` column of
  the PSF-fitting `init_params`. This matches the aperture-photometry local
  background and removes residual sky/nebulosity that would otherwise bias the
  fitted flux and its error. Sources whose annulus is fully masked or off-frame
  fall back to no local subtraction rather than returning a NaN fit.
- The **empirical PSF itself** is built from de-contaminated cutouts: the same
  local-background annulus is subtracted from a copy of the image around each PSF
  star before `extract_stars`, so the ePSF is not built from sky-contaminated
  stars. On a synthetic frame with a constant sky pedestal this drops the PSF
  flux bias from ×2.5 (no local background) to ×1.001.
- **Shape and dimensions.** The PSF is fitted in a compact `fit_shape`
  (≈3× FWHM, rounded to an odd number of pixels) but the ePSF is *extracted*
  from a larger ≈5× FWHM cutout, so the wings are not truncated and normalised
  away; this keeps the encircled-energy correction (which reaches 1.5× FWHM)
  meaningful. The ePSF centre is `(N − 1) / 2` on its oversampled grid.
- **Star selection.** Candidate ePSF stars are filtered on flux, S/N, shape,
  crowding and a local-background flatness test. Truly saturated pixels are
  already removed by the pipeline mask, so the saturation check is only a
  last-resort guard and never rejects the brightest unsaturated stars; an
  approximate S/N derived from `sqrt(flux)` is not used as a hard cut.

**PSF-vs-aperture consistency check.** After photometry,
`check_psf_aperture_consistency()` matches PSF and aperture sources by position
and reports the median `psf_mag − aperture_mag` for each aperture. Both methods
are placed on the same total-flux scale (see the aperture correction above), so
the difference should be centred on zero. A median offset larger than
`PSF_APERTURE_CONSISTENCY_THRESHOLD` (`0.15` mag) raises a warning; the
per-aperture counts, medians and robust (MAD) scatter are stored in
`st.session_state["psf_aperture_consistency"]`. The per-source value is also
exported for every aperture as `psf_minus_aperture_mag_X_X` (with
filter-prefixed aliases), NaN where only one method measured the source; the
zero point is common to both magnitudes and cancels in the difference. The
results view plots the per-source offset against the calibrated PSF magnitude
(with per-aperture median and zero lines) and as a histogram. Note that photutils subtracts
local backgrounds per source over its own `fit_shape` cutout; for the crowded
case this does **not** bias the recovered flux in practice (verified on grouped
pairs with symmetric and strongly asymmetric pedestals), so the local background
is kept for overlapping sources rather than zeroed.

### Aperture Correction

The S/N is invariant under a multiplicative flux correction, but the
instrumental magnitude is not. Before computing magnitudes the aperture flux is
corrected for the fraction of the PSF that falls outside the aperture using the
encircled-energy curve of the empirical PSF (or an analytic Gaussian fallback):

```
flux_total = flux_aperture / EE(r)
```

The correction factor `1/EE(r)` is stored per source in the `apcorr_X_X` column,
and the encircled-energy fraction it came from in `ee_fraction_X_X` (so a large
correction is traceable to a poorly-enclosed aperture rather than a silent
magnitude offset). This puts aperture and PSF photometry on the same total-flux
scale, which matters because the zero point is anchored to one aperture.

The **zero point is fitted against the aperture-corrected**
`instrumental_mag_X_X` column. The `instrumental_mag_bkg_corr_X_X` column is the
uncorrected, background-subtracted reference (useful for diagnostics) and does
**not** anchor the calibration.

**Aperture-correction systematic.** The correction shifts the (log) flux by a
relative amount `(apcorr - 1)/apcorr`. Unlike the statistical flux error, this
shift does **not** cancel in the flux ratio, so it must be added to the
magnitude error separately:

```
sigma_apcorr = MAG_ERR_COEFFICIENT * f_apcorr * |apcorr - 1| / apcorr
```

where `f_apcorr` is the fractional uncertainty of the correction
(`APCORR_SYSTEMATIC_FRACTION` in `src.pipeline`, default 0.20, i.e. the
correction is trusted to 20% of its size). The per-aperture term is stored in
`apcorr_mag_err_X_X` and combined in quadrature with the statistical magnitude
error. It dominates the error budget at high S/N, where `sigma_mag` alone is
very small. Setting `APCORR_SYSTEMATIC_FRACTION = 0` disables it.

### Example

```python
# For a source with 1000 counts and error of 10 counts:
flux = 1000.0
flux_error = 10.0
snr = flux / flux_error  # = 100.0
```

---

## Magnitude Error Calculation

### Formula

```
σ_mag = 1.0857 × (σ_flux / flux)
```

Where:
- `σ_mag`: Magnitude error (instrumental)
- `σ_flux`: Flux uncertainty
- `flux`: Source flux
- `1.0857 = 2.5 / ln(10)`

### Mathematical Derivation

Given the magnitude-flux relation:
```
mag = -2.5 × log₁₀(flux)
```

Using error propagation:
```
σ_mag = |∂mag/∂flux| × σ_flux
      = |-2.5 / (flux × ln(10))| × σ_flux
      = (2.5 / ln(10)) × (σ_flux / flux)
      ≈ 1.0857 × (σ_flux / flux)
```

The statistical term above is combined in quadrature with the
**aperture-correction systematic** `sigma_apcorr` (see the Aperture Correction
section), which does not cancel in the flux ratio:

```
σ_mag = √( (1.0857 × σ_flux / flux)² + σ_apcorr² )
```

### Relationship to S/N

For high signal-to-noise sources (S/N > 5), the statistical term reduces to:
```
σ_mag(stat) ≈ 1.0857 / S/N
```

This is valid because:
```
σ_mag(stat) = 1.0857 × (σ_flux / flux)
            = 1.0857 × (1 / (flux / σ_flux))
            = 1.0857 / S/N
```

At high S/N the actual exported `σ_mag` is dominated by the
`σ_apcorr` systematic and the zero-point term rather than by `1.0857 / S/N`.

### Example

```python
# For a source with flux=1000 ± 10:
flux = 1000.0
flux_err = 10.0
mag_err = 1.0857 * flux_err / flux  # = 0.01087 mag

# Verify with S/N:
snr = flux / flux_err  # = 100.0
mag_err_from_snr = 1.0857 / snr  # = 0.01087 mag (same!)
```

---

## Error Propagation

### Sky Background Model

Two independent background estimates are used, and they play different roles:

* a **global 2D model** (`Background2D`, SExtractor mode estimator) defines the
  per-pixel `background` and `background_rms` arrays. The RMS map is the
  "background-only" noise term of the per-pixel error map (`σ_bkg` below);
* the **local annulus** median removes whatever sky level the global model left
  behind around each source (see the next section).

The per-box RMS uses the **MAD-based estimator** (`MADStdBackgroundRMS`). The
plain standard deviation of the sigma-clipped box is biased high by the wings of
undetected sources: measured on synthetic frames it over-estimated the true pixel
noise by x1.11 at 15 px source spacing, x1.38 at 12 px and x2.03 at 10 px, and
that factor propagates straight into every flux error. The MAD estimator is
unbiased on a clean frame (x1.02) and stays much closer when crowded (x1.17 at
12 px).

The smoothing filter over the background mesh is capped at 3x3 when the mesh is
small (< 12 boxes across). A 5x5 median filter over an 8x8 mesh over-smooths the
model: on a 300 ADU sky ramp the subtracted frame was x2.24 noisier than the raw
frame, whereas a 3x3 filter leaves x1.54.

Finally, the model never aborts the run. `Background2D` discards any box with
more than `exclude_percentile` % of masked/clipped pixels (10 % by default),
which can reject *every* box in a crowded field or a frame with large bad areas
and raise `ValueError`. The estimation is retried with 50 % and then 100 %
tolerance and a coarser box, and if no mesh is possible it falls back to a single
sigma-clipped background level for the whole frame. That last-resort
`_constant_background` model uses the same MAD-based RMS as the normal path, so
fallback frames do not get a source-inflated noise map. Aperture photometry
stays usable in that case because each source still has its local annulus
background subtracted.

For consistency with the error model, the **source-detection threshold** is
also derived from the MAD-based global scatter (`stdfunc="mad_std"`) rather than
the plain sigma-clipped standard deviation. The plain std is inflated by
undetected sources, which would silently raise the effective detection
threshold above the requested `threshold_sigma` in crowded fields.

### Local Background Subtraction and its Error

The local background is measured as the **sigma-clipped median** of the annulus
surrounding the source (`ApertureStats`), not a plain mean, so neighbouring
sources and cosmic rays do not bias it. For an aperture area `A_ap` and an
annulus area `A_ann`:

```
bkg_per_pixel     = sigma-clipped median of the annulus
total_bkg         = bkg_per_pixel × A_ap
sigma_bkg_pixel   = annulus_median_err        # sqrt(pi/2) × s / sqrt(A_ann)
sigma_total_bkg   = sigma_bkg_pixel × A_ap
flux              = aperture_sum − total_bkg
σ_flux²           = σ_aperture² + σ_total_bkg² + σ_centroid²
```

Because the local background is a **median**, its uncertainty is the standard
error of the median, `sqrt(pi/2) × s / sqrt(A_ann)` (~1.2533× larger than the
mean-based `s / sqrt(A_ann)`), which is what `ApertureStats.median_err`
returns. Using the mean's standard error for a median underestimates the local
background term by ~25% and `σ_flux` by ~8% in the background-limited regime.
When `median_err` is unavailable (older photutils, or non-finite values) the
pipeline falls back to the mean-based estimate.

### Centroid-Position Error

A fixed aperture samples a different fraction of the PSF when the source is not
perfectly centred. The corresponding flux error is

```
sigma_pos   = sigma_PSF / S/N            # per-axis centroid uncertainty
sigma_pos   = clip(sigma_pos, 0.01, 0.5 × FWHM)
sigma_flux  = 0.5 × sqrt( (F(x+σ)-F(x-σ))² + (F(y+σ)-F(y-σ))² )
σ_flux²     = σ_aperture² + σ_total_bkg² + σ_centroid²
```

`sigma_PSF = FWHM / 2.3548` and `F(...)` is the aperture sum re-measured with
the aperture shifted by `±sigma_pos` along x or y (`centroid_flux_err_X_X`).
Using the measured shifted sums rather than a symmetric-Gaussian derivative is
intentional: for a perfectly symmetric PSF the linear term vanishes by
symmetry, whereas the real (asymmetric/elliptical) PSF does contribute. The
term is small for apertures larger than the PSF (a few percent of the
statistical error) but matters for small apertures and undersampled PSFs.

The `σ_total_bkg` term is essential: omitting it underestimates `σ_flux` by
roughly 20% for the default `r_in = 2r`, `r_out = 2.5r` annulus and therefore
overestimates the S/N by the same amount in the background-limited regime.

### Calibrated Magnitude Error

When converting instrumental magnitudes to calibrated magnitudes using a zero point:

```
mag_calib = mag_inst + zero_point
```

The error propagates as:
```
σ_mag_calib = √(σ_mag_inst² + σ_zero_point² + σ_sys²)
```

This is quadrature addition because the errors are independent. The zero point is
a **median** of the calibrator residuals, so its formal uncertainty is the
standard error of the median,

```
σ_zero_point = √(π/2) × scatter / √N ≈ 1.2533 × scatter / √N
```

over the retained calibration stars (`N`), kept at full precision. The
`√(π/2)` factor (about 25% larger than the mean-based `scatter / √N`) is
exported as `MEDIAN_STD_ERROR_FACTOR` in `src.pipeline`.

The `scatter` in this expression is taken from the **sigma-clipped sample before
the `|residual| <= 0.5 mag` hard cut**. That hard cut removes the tails and
biases the truncated (“retained”) scatter low; since the sigma clip already
removes true outliers, the wider sample is the best available estimate of the
intrinsic dispersion. Both values are recorded on the matched calibration table
(`zero_point_scatter`, `zero_point_scatter_kept`) and the diagnostics are stored
in `st.session_state["zero_point_diagnostics"]`.

An optional systematic floor `σ_sys` (default 0) is supplied through the
`systematic_floor` argument of `add_calibrated_magnitudes` and is resolved per
run by `resolve_systematic_floor()`:

1. `analysis_parameters["systematic_mag_err_floor"]` / session state,
2. the `RPP_SYSTEMATIC_MAG_ERR_FLOOR` environment variable (headless runs),
3. the module default `SYSTEMATIC_MAG_ERR_FLOOR` (0, i.e. no extra term).

The zero-point uncertainty is now propagated into every exported
`aperture_mag_err_*` and `psf_mag_err` column by
`add_calibrated_magnitudes(zero_point_std=...)`.

**Random vs correlated components.** `σ_zp` (and `σ_sys`) is a **common** offset
shared by every source in the field, not an independent per-source error. Adding
it in quadrature is conservative for a single source, but it does not average
down over an ensemble. To support both uses, the total error is accompanied by
two explicit component columns for every magnitude-error column:

```
aperture_mag_err_1_3           = sqrt(random^2 + correlated^2)   (total)
aperture_mag_err_1_3_random     = per-source statistical error plus the
                                   aperture-correction systematic
_aperture_mag_err_1_3_correlated = sqrt(σ_zp^2 + σ_sys^2)          (field-common)
```
The same `_random` / `_correlated` suffixes exist for `psf_mag_err`, but the
PSF random component is the PSF-fitting statistical error; it has no
aperture-correction term. For relative photometry, quote `_random`; for absolute
photometry, use the total. The field-level `zero_point_std` is also stored as a
catalog column.

### Example

```python
import numpy as np

# Instrumental photometry with error
mag_inst_err = 0.05  # mag

# Zero point calibration with uncertainty
zero_point_err = 0.03  # mag

# Propagated error
mag_calib_err = np.sqrt(mag_inst_err**2 + zero_point_err**2)
# = √(0.05² + 0.03²) = √(0.0025 + 0.0009) = √0.0034 ≈ 0.058 mag
```

### Effective gain

The per-pixel error map is built with
`calc_total_error(background_subtracted_data, background_rms, effective_gain)`.
The effective gain (counts per data unit) is resolved in this order:

1. **FITS conversion keyword**, in the priority order of `GAIN_KEYWORDS`:
   explicit `EGAIN` and `CCDGAIN` (already e-/ADU) and `CVF` (a conversion
   factor in ADU/e-, so `gain = 1/CVF`) are tried before the conventional
   `GAIN`. The value must be positive and inside the plausible range
   0.02-100 e-/ADU (`GAIN_PLAUSIBLE_RANGE`); anything outside it is reported
   and ignored. On a recognised **driver-gain camera** a bare `GAIN` is skipped
   entirely (see below);
2. **user-supplied gain** — the *Detector Gain* parameter in the sidebar,
   `analysis_parameters["effective_gain"]`, or the `RPP_EFFECTIVE_GAIN`
   environment variable for headless runs (0 or empty means *automatic*);
3. **empirical photon-transfer estimate** from the frame itself: for boxes of
   pure sky the pixel variance grows linearly with the mean signal,
   ```
   var(box) ≈ read_noise² + mean(box) / gain
   ```
   so `1 / slope` of a robust fit to (mean, variance) is the effective gain.
   The estimate is accepted only when the frame really constrains it: enough
   source-free boxes (sigma clipping removes < 10% of the pixels), a sky level
   that varies enough across the frame (slope measured at ≥ 3σ), and a
   physically plausible result. A flat sky carries no gain information and is
   rejected;
4. **`effective_gain = 1`** (data treated as electrons) as the last resort,
   with an explicit warning.

Step 4 is the conservative choice, but it is *not* neutral: with data in ADU and
an unknown true gain `g`, the source Poisson variance `flux/gain` is
overestimated by a factor `g`, i.e. the magnitude errors of bright sources by up
to `√g`. The previous arbitrary `65535 / max(image)` heuristic remains removed.

#### Why a FITS `GAIN` is not always e-/ADU

CMOS cameras expose an adjustable analog gain whose *driver setting* is written
to `GAIN`, but that number is not a conversion factor. QHY writes an abstract
0-100 scale (a QHY268M operated in high-gain mode at `GAIN=56` measures
≈0.77 e-/ADU, and the HGC/LGC switch sits at 55→56); ZWO ASI writes a 0.1 dB
value whose unity-gain setting is model-specific (e.g. ≈100 for the ASI2600).
The mapping from the driver setting to e-/ADU is per-model and piecewise, so the
pipeline does **not** try to guess it: it recognises such a camera from the
instrument metadata (`INSTRUME`/`INSTRID`/`INSTRUMENT`/`CAMERA`/`DETECTOR`/
`CCDNAME`, matched by `DRIVER_GAIN_INSTRUMENT_PATTERNS`) and ignores its bare
`GAIN`, then uses an explicit `EGAIN`/`CVF` conversion, the user-supplied gain,
or the photon-transfer estimate measured from the frame itself. Supplying the
true conversion factor for the gain setting in use (read from the manufacturer's
gain-vs-e-/ADU chart) is the most reliable option.

The same resolved gain is handed to `astroscrappy.detect_cosmics` for the
cosmic-ray mask, together with a `RDNOISE`/`READNOIS`/`ENOISE` value when the
header provides one. A cosmic-ray mask covering more than
`MAX_CR_MASK_FRACTION` (20%) of a frame is not physical and is discarded rather
than removing a large part of the science image.

Masked pixels (border, cosmic rays, satellite trails and NaNs) are excluded from
both the source and the annulus photometry.

### Atmospheric Extinction (Optional)

If atmospheric extinction is applied:
```
mag_calib = mag_inst + zero_point - k × airmass
```

Where:
- `k`: Extinction coefficient (mag/airmass)
- `airmass`: Air mass during observation

Error propagation becomes:
```
σ_mag_calib = √(σ_mag_inst² + σ_zero_point² + σ_sys² + (airmass × σ_k)²)
```

### Color-Term Calibration (Optional)

When catalog colors are available for a matched source, the pipeline can also
apply a simple linear color-term correction after the standard zero-point
calibration.

Baseline calibrated magnitude:
```
mag_calib = mag_inst + zero_point
```

Color-corrected calibrated magnitude:
```
mag_calib_colorcorr = mag_calib + color_term_offset
```

The fitted offset is modeled as:
```
color_term_offset = intercept + C × (color - color_ref)
```

Where:
- `C`: fitted color-term coefficient
- `color`: per-source catalog color, for example `BP-RP` or `g-r`
- `color_ref`: median color of the retained calibration stars
- `intercept`: residual offset relative to the scalar zero point

This follows the same first-order idea described in STDPipe, while keeping the
existing scalar zero-point calibration as the compatibility baseline.

**Uncertainty of the color correction.** The fit parameter covariance is taken
from the final least-squares fit (`scatter² × (Dᵀ D)⁻¹`) and propagated to each
source through its color offset `x = color - color_ref`:

```
var(color_term_offset) = var(intercept) + 2x·cov(intercept, C) + x²·var(C)
```

It is stored in `color_term_offset_err` (and `color_term_std` / `intercept_std`
are returned by the fit). Every color-corrected magnitude gets a parallel error
column `*_colorcorr_err`:

```
σ_mag_colorcorr = √(σ_mag_calib² + σ_color_term_offset²)
```

where `σ_mag_calib` is the baseline calibrated error (already including the
statistical, aperture-correction and zero-point terms). Sources without a usable
catalog color keep NaN in every correction column.

### Practical Limitation

Color-corrected magnitudes are only written for sources that have the required
catalog color columns in the final table. Unmatched sources, or sources without
usable catalog colors, keep the standard zero-point-calibrated magnitudes only.

---

## Quality Flags

Sources are assigned quality flags based on their S/N:

| Quality Flag | S/N Range | Description | Recommendation |
|-------------|-----------|-------------|----------------|
| `'good'` | S/N > 10 | Reliable photometry | Use for science |
| `'marginal'` | 5 < S/N ≤ 10 | Intermediate quality | Use with caution |
| `'poor'` | S/N ≤ 5 | Unreliable photometry | Exclude from analysis |
| `'unknown'` | N/A | Missing/invalid data | Exclude |

### Rationale

- **S/N ≤ 5**: Detection may still be real, but the photometry is too uncertain to be labeled reliable
- **5 < S/N ≤ 10**: Usable for inspection or exploratory analysis, but not in the highest-confidence class
- **S/N > 10**: Conservative threshold for reliable photometry with smaller magnitude uncertainties

### Usage Example

```python
import pandas as pd

# Load photometry catalog
df = pd.read_csv('photometry.csv')

# Filter for reliable sources only
reliable_sources = df[df['quality_flag_1_1'] == 'good']

# Include marginal detections with warning
usable_sources = df[df['quality_flag_1_1'].isin(['good', 'marginal'])]
```

---

## Summary Table

| Quantity | Formula | Typical Values |
|----------|---------|----------------|
| S/N | `flux / flux_error` | 3–1000 |
| Flux Error | `√(σ_aperture² + σ_total_bkg² + σ_centroid²)` | — |
| Local background error | `median_err` = `√(π/2)·s/√A_ann` | per-pixel background |
| Centroid-position error | `0.5·√(ΔF_x² + ΔF_y²)` at `±σ_PSF/S/N` | few % of statistical |
| Sky RMS | MAD of the sigma-clipped box (`MADStdBackgroundRMS`) | per-pixel noise |
| Magnitude Error | `√((1.0857·σ_flux/flux)² + σ_apcorr²)` | 0.01–0.3 mag |
| Aperture Correction | `flux_total = flux_aperture / EE(r)` | 1.00–1.15 |
| Aperture-correction systematic | `σ_apcorr = 1.0857 · f_apcorr · |apcorr−1| / apcorr` | 0.01–0.05 mag |
| Calibrated Mag Error | `√(σ_inst² + σ_zp² + σ_sys²)` | 0.02–0.35 mag |
| Zero-point uncertainty | `√(π/2) × scatter_untrimmed / √N` | 0.01–0.2 mag |
| Effective gain | `EGAIN`/`CCDGAIN`/`1/CVF` → user → `1/slope(var vs mean)` → `1` | e-/ADU |
| Color-Corrected Mag | `mag_calib + intercept + C × (color - color_ref)` | calibration-dependent |
| Color-Corrected Mag Error | `√(σ_mag_calib² + var(intercept) + 2x·cov + x²·var(C))` | calibration-dependent |
| Instrumental Magnitude | `-2.5 × log₁₀(flux_total)` | 10–25 mag |

---

## References

1. **Photometric Error Theory:**
   - Howell, S. B. (2006). "Handbook of CCD Astronomy" (2nd ed.), Chapter 4
   - Merline, W. J., & Howell, S. B. (1995). "Experimental Techniques in CCD Photometry"

2. **Error Propagation:**
   - Bevington, P. R., & Robinson, D. K. (2003). "Data Reduction and Error Analysis for the Physical Sciences" (3rd ed.)

3. **S/N and Detection Limits:**
   - Da Costa, G. S. (1992). "Basic Photometry Techniques" in "Astronomical CCD Observing and Reduction Techniques", ASP Conference Series

4. **Photutils Documentation:**
   - https://photutils.readthedocs.io/
   - Aperture Photometry: https://photutils.readthedocs.io/en/stable/aperture.html
   - PSF Photometry: https://photutils.readthedocs.io/en/stable/psf.html

---

## Implementation Notes

### Numerical Precision

- All S/N calculations use full floating-point precision (no rounding)
- This prevents divide-by-zero errors in magnitude error calculations
- Magnitude errors are computed directly from flux ratios, not from S/N

### Edge Cases

The implementation handles several edge cases:

1. **Negative background-corrected flux:** Kept as-is in the S/N (it is a real
   non-detection rather than silently replaced by the raw flux) and excluded
   from the magnitude/error columns (`nan`)
2. **Zero flux:** Produces `nan` magnitude and `nan` magnitude error
3. **Very low/high flux:** Maintains numerical stability across dynamic range
4. **Missing data:** Assigns `'unknown'` quality flag
5. **Masked / NaN pixels:** Excluded from both the aperture and the annulus sums
   and from their errors via the shared pipeline mask
6. **Unreliable photometry:** a source is removed only when *every* photometry
   method reports a magnitude error of 2 mag or more (`MAG_ERR_MAX` in
   `src.tools_pipeline`, overridable per run through the *Max Magnitude Error*
   parameter). A source with one usable method is kept, and sources without any
   error information at all (all NaN) are kept as well. Setting the parameter to
   0 disables the cut completely, leaving the quality flags as the only
   reliability indicator.

### Testing

Run the test suite to verify calculations:
```bash
pytest tests/test_photometry.py -v
```

---

*Last Updated: 2026-10-02*
*Version: 1.0*
