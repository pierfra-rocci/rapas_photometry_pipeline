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
- `flux`: Source flux (preferably background-corrected)
- `flux_error`: Uncertainty in flux measurement (σ_flux)

### Implementation Details

**Aperture Photometry:**
- Uses **background-corrected flux** when available: `aperture_sum_bkg_corr`
- Falls back to raw `aperture_sum` if background correction yields negative/zero values
- No rounding applied to preserve precision

**PSF Photometry:**
- Uses `flux_fit` from PSF model fitting
- `flux_err` from PSFPhotometry is already the standard deviation (not variance)

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

### Relationship to S/N

For high signal-to-noise sources (S/N > 5):
```
σ_mag ≈ 1.0857 / S/N
```

This is valid because:
```
σ_mag = 1.0857 × (σ_flux / flux)
      = 1.0857 × (1 / (flux / σ_flux))
      = 1.0857 / S/N
```

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

### Calibrated Magnitude Error

When converting instrumental magnitudes to calibrated magnitudes using a zero point:

```
mag_calib = mag_inst + zero_point
```

The error propagates as:
```
σ_mag_calib = √(σ_mag_inst² + σ_zero_point²)
```

This is quadrature addition because the errors are independent.

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
σ_mag_calib = √(σ_mag_inst² + σ_zero_point² + (airmass × σ_k)²)
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
| Magnitude Error | `1.0857 × (flux_err / flux)` | 0.01–0.3 mag |
| Calibrated Mag Error | `√(σ_inst² + σ_zp²)` | 0.02–0.35 mag |
| Color-Corrected Mag | `mag_calib + intercept + C × (color - color_ref)` | calibration-dependent |
| Instrumental Magnitude | `-2.5 × log₁₀(flux)` | 10–25 mag |

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

1. **Negative background-corrected flux:** Falls back to raw aperture flux
2. **Zero flux:** Produces `inf` magnitude and `nan` magnitude error
3. **Very low/high flux:** Maintains numerical stability across dynamic range
4. **Missing data:** Assigns `'unknown'` quality flag

### Testing

Run the test suite to verify calculations:
```bash
pytest tests/test_photometry.py -v
```

---

*Last Updated: 2026-01-07*
*Version: 1.0*
