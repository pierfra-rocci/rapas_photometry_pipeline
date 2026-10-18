# Error Budget and Error Propagation Review

**Scope:** `src/pipeline.py` (`detection_and_photometry`, `calculate_zero_point`,
`estimate_background` in `src/tools_pipeline.py`), `src/psf.py`
(`perform_psf_photometry`) and the calibration helpers in `src/tools_pipeline.py`
(`add_calibrated_magnitudes`, `apply_color_term_correction`).

**Method:** read the implementation end to end, cross-checked against the
published `photutils` formulas/source and against the current
`docs/PHOTOMETRY_FORMULAS.md`, `docs/features.rst` and the README changelog.
Line references are approximate and intended as signposts, not exact anchors.

---

## 1. Executive summary

The error budget is, overall, well constructed and internally consistent. The
core chain

```
per-pixel error -> aperture flux error -> local-background term -> sigma_flux
                -> S/N, sigma_mag -> sigma_mag_calib (+ zero point)
```

uses a **single flux** for the magnitude, its error and the S/N, and each
propagated term is dimensionally correct. The most important known failure modes
(raw aperture sum used for the error while the magnitude used the
background-corrected flux, missing local-background variance, rounded zero
point, no zero-point error) have all been addressed and are covered by tests.

The remaining gaps are of two kinds:

1. **a small statistical inconsistency** in the local-background term (a median
   is used for the value but the mean's standard error for its uncertainty);
2. **unmodeled systematics** that are not represented in any exported error at
   all: the aperture correction, the color-term correction, and the correlated
   nature of the zero-point error.

None of these invalidates the pipeline. They are worth fixing because they
affect the same background-limited regime the project already invested in, and
because two of them (aperture and color-term corrections) are the largest
remaining contributions to the total photometric error budget at high S/N.

---

## 2. The error budget as implemented

### 2.1 Per-pixel error map

`detection_and_photometry` builds the map with `photutils.utils.calc_total_error`:

```
sigma_pix^2 = sigma_bkg,pix^2 + max(I_pix, 0) / g_eff
```

where `sigma_bkg,pix = Background2D.background_rms` (MAD-based estimator,
`MADStdBackgroundRMS`), `I_pix` is the **background-subtracted** image
(`image_sub`) and `g_eff` is the resolved effective gain.

This matches the photutils reference formula
`sigma_tot = sqrt(sigma_bkg^2 + I / g_eff)` exactly. Verified against the
photutils 3.0 source (`photutils/utils/errors.py`), including the documented
behaviour that **the Poisson term is dropped wherever `data` is negative**
(`source_variance = np.maximum(source_variance, 0)`), so background-subtracted
sources with negative sky pixels get only the background term there. This is
correct and is not a bug, but it is worth knowing.

### 2.2 Aperture flux and its error

`aperture_photometry(image_sub, aperture, error=total_error, mask=mask, wcs=...)`
returns

```
aperture_sum_err = sqrt( sum_{aperture} total_error^2 )
```

with the pipeline mask (border + cosmic rays + satellite trails + NaNs) applied
and the exact masked area returned through `area_overlap`.

### 2.3 Local background and its error

For aperture area `A_ap` and annulus area `A_ann`:

```
bkg_per_pixel   = sigma-clipped median of the annulus   (ApertureStats.median)
total_bkg       = bkg_per_pixel * A_ap
sigma_bkg_pixel = annulus_std / sqrt(A_ann)             <-- see finding F1
sigma_total_bkg = sigma_bkg_pixel * A_ap
flux            = aperture_sum - total_bkg
sigma_flux^2    = aperture_sum_err^2 + sigma_total_bkg^2
```

### 2.4 S/N, magnitude and magnitude error

```
S/N       = flux / sigma_flux          (flux errors > 0, else NaN)
mag_inst  = -2.5 * log10(flux * apcorr)
sigma_mag = 1.0857 * (sigma_flux * apcorr) / (flux * apcorr)
          = 1.0857 * sigma_flux / flux
```

The aperture correction cancels in the ratio, so S/N and `sigma_mag` are
invariant under `apcorr` while the magnitude itself is not. This is consistent.

### 2.5 Calibrated magnitude error

```
sigma_mag_calib = sqrt( sigma_mag^2 + sigma_zp^2 + sigma_sys^2 )
sigma_zp        = 1.2533 * scatter / sqrt(N)      (median standard error)
```

`sigma_zp` is applied to every `aperture_mag_err_*` and `psf_mag_err` column in
`add_calibrated_magnitudes`, at full precision.

### 2.6 PSF photometry

`PSFPhotometry(...)(img, init_params=..., mask=..., error=error)` receives the
same `total_error` map, so `flux_err` is derived from the same per-pixel noise
model as the aperture errors.

### 2.7 Numerical sanity check of the local-background term

For the default annulus `r_in = 2r`, `r_out = 2.5r`:
`A_ap / A_ann = 1 / (2.5^2 - 2.0^2) = 1/2.25 = 0.444`, so the local-background
term is `sqrt(A_ap/A_ann) = 0.667` times the aperture sky-noise term. In
quadrature this raises `sigma_flux` by `sqrt(1 + 0.444) = 1.202`, i.e. ~20% —
matching the figure quoted in `docs/PHOTOMETRY_FORMULAS.md`. The documentation
is correct.

---

## 3. What is correct (and should be preserved)

| Item | Status |
|------|--------|
| `calc_total_error` formula and negative-pixel handling | Matches photutils source |
| Single consistent flux for S/N, magnitude and its error | Correct |
| `sigma_flux^2 = sigma_ap^2 + sigma_total_bkg^2` | Correct, ~20% effect verified |
| `sigma_mag = 1.0857 * sigma_flux / flux` with `1.0857 = 2.5/ln 10` | Correct |
| `apcorr` cancels in S/N and `sigma_mag` | Correct |
| Median standard error factor `sqrt(pi/2) = 1.2533` on the zero point | Correct |
| Zero point kept at full precision | Correct |
| Zero-point error propagated into every calibrated error column | Correct |
| Mask applied to both aperture and annulus photometry | Correct |
| `MADStdBackgroundRMS` for the noise map | Correct, avoids source-inflated noise |

---

## 4. Findings

Ranked by expected impact on the total error budget. No finding breaks the
pipeline; all are incremental accuracy/consistency improvements.

### F1 — Local-background error uses the *mean* standard error for a *median* (medium) — **FIXED**

**Status:** fixed. The annulus uncertainty now uses
`ApertureStats.median_err` (with `std / sqrt(A_ann)` kept only as a fallback),
and a regression test
(`TestPipelineErrorBudget::test_local_background_error_uses_median_error`) guards
it. `docs/PHOTOMETRY_FORMULAS.md` was updated to match.

**Where:** `src/pipeline.py`, annulus block (`bkg_per_pixel_err = bkg_std /
np.sqrt(annulus_area)`), ~lines 1320–1340.

**Issue:** the value is `ApertureStats.median` (a sigma-clipped median) but the
uncertainty uses the standard error of the **mean**, `std / sqrt(A_ann)`. The
standard error of a median is larger by `sqrt(pi/2) = 1.2533`. photutils exposes
exactly the right quantity: `ApertureStats.median_err` is documented as the
large-sample approximation `sqrt(pi/2) * s / sqrt(n)`.

**Impact:** the local-background term is underestimated by ~25%. In the
background-limited regime the total `sigma_flux` is then low by ~8% (the local
term contributes a factor ~1.20 to the total; raising it by 1.2533 gives ~1.30
instead). This is the same class of bias that was fixed for the zero point, and
it is the only remaining term in the budget that uses the wrong estimator.

**Resolution:** replaced `bkg_std / sqrt(annulus_area)` with
`np.asarray(annulus_stats.median_err, dtype=float)`, keeping the existing
`annulus_area` fallback only when `median_err` is unavailable or non-finite.

### F2 — Aperture-correction uncertainty is never propagated (medium) — **FIXED**

**Status:** fixed. A per-aperture systematic
`sigma_apcorr = 1.0857 · f_apcorr · |apcorr−1| / apcorr` (with
`APCORR_SYSTEMATIC_FRACTION`, default 0.20) is now added in quadrature to every
`aperture_mag_err_*` and stored in `apcorr_mag_err_*`. The global
`SYSTEMATIC_MAG_ERR_FLOOR` is now actually passed to
`add_calibrated_magnitudes` via `resolve_systematic_floor()` (session state /
`RPP_SYSTEMATIC_MAG_ERR_FLOOR` / module default). Tests:
`TestPipelineErrorBudget::test_error_budget_and_aperture_correction`,
`test_add_calibrated_magnitudes_propagates_systematic_floor`,
`test_resolve_systematic_floor_reads_environment`.

**Where:** `src/pipeline.py`, `apcorr` block ~lines 1420–1440; the
`SYSTEMATIC_MAG_ERR_FLOOR` constant at line 34.

**Issue:** `apcorr = 1 / EE(r)` is derived from the empirical ePSF (or a Gaussian
fallback) and clamped to `[1, 2]`. The *random* part cancels in `sigma_mag`, but
the *systematic* part does not: an error in `EE(r)` produces a magnitude offset
that is completely absent from every exported error. The clamp at 2.0 also
silently truncates a pathological correction, again with no error attached.

Additionally, `SYSTEMATIC_MAG_ERR_FLOOR = 0.0` is defined in `src/pipeline.py`
but is never passed to `add_calibrated_magnitudes` (which does accept a
`systematic_floor` argument). The plumbing exists but is disconnected — dead
code today.

**Impact:** at high S/N (where `sigma_mag` is small) the aperture-correction
systematic dominates the realistic error budget but is invisible to the user.

**Recommendation:** either (a) estimate `delta(EE)` from the ePSF and propagate
`1.0857 * delta(EE)/EE` into `sigma_mag` or into the systematic floor, or
(b) at minimum wire `SYSTEMATIC_MAG_ERR_FLOOR` through to
`add_calibrated_magnitudes` and document the assumed floor. Note the clamp in
the documentation either way.

### F3 — Color-term-corrected magnitudes carry no error (medium) — **FIXED**

**Status:** fixed. `fit_color_term_correction` now returns the fit parameter
covariance and standard errors, and `apply_color_term_correction` writes
`color_term_offset_err` plus a `*_colorcorr_err` column
`√(σ_mag_calib² + σ_color_term_offset²)` for every corrected magnitude (with
filter-prefixed aliases). Tests:
`test_apply_color_term_correction_appends_new_columns_and_keeps_legacy`,
`test_colorcorr_err_without_baseline_error_is_fit_only`,
`test_fit_color_term_reports_parameter_uncertainties`.

**Where:** `src/tools_pipeline.py`, `apply_color_term_correction`, ~lines 226–290.

**Issue:** the function appends `*_colorcorr` magnitudes and trace fields
(`color_term_offset`, `color_term_value`, ...) but never writes a
`*_colorcorr_err` column, and the color-term coefficient/intercept uncertainty
from `fit_color_term_correction` is not propagated. The `*_colorcorr` loop also
deliberately skips existing error columns.

**Impact:** users who select `*_colorcorr` magnitudes for science get no error
for them; the parallel baseline errors do not apply because the offset shifts
the magnitude but does not change `sigma_mag` in the current model.

**Recommendation:** export `*_colorcorr_err` derived from the baseline error
plus the color-term fit uncertainty (coefficient standard error times the color
offset from `color_ref`), and document that the color term is not a random
per-source error.

### F4 — Zero-point scatter is biased low by the outlier cuts (low–medium) — **FIXED**

**Status:** fully fixed. `calculate_zero_point` now takes the scatter for
`σ_zp` from the **sigma-clipped sample before the ±0.5 mag hard cut** (which
truncates the tails), keeping the truncated value only as a reported diagnostic
(`zero_point_scatter_kept`). Both scatter estimates and `N` are stored in
`st.session_state["zero_point_diagnostics"]`. Separately,
`add_calibrated_magnitudes` exports every magnitude error as a total plus
explicit `*_mag_err_*_random` and `*_mag_err_*_correlated` components with
filter-prefixed aliases. Test:
`TestZeroPointCalibration::test_zero_point_uncertainty_uses_untrimmed_scatter`.

**Where:** `src/pipeline.py`, `calculate_zero_point`, sigma-clip + `|residual| <=
0.5 mag` block, ~lines 1700–1760.

**Issue:** `sigma_zp = 1.2533 * scatter / sqrt(N)` uses the scatter of the
*trimmed* residual distribution. The explicit `|residual| <= 0.5 mag` hard cut
removes the wings, so `scatter` — and therefore `sigma_zp` — is biased low
(the code comment already acknowledges this). In addition, the zero-point error
is a **common systematic** shared by every source in the field, yet it is added
in quadrature per source as if it were independent.

**Impact:** `sigma_mag_calib` is slightly optimistic; for ensemble/relative work
the correlated component behaves differently from the random component.

**Recommendation:** document the bias explicitly (it already is, in part) and
consider reporting the untrimmed RMS as a diagnostic column. If absolute
calibration matters, expose a flag distinguishing the random (`sigma_mag`) from
the systematic (`sigma_zp`) part rather than only their quadrature sum.

### F5 — Constant-background fallback uses a non-robust RMS (low) — **FIXED**

**Status:** fixed. `_constant_background` now uses `stdfunc="mad_std"`, matching
the normal `Background2D` path. Test:
`TestBackgroundRobustness::test_constant_fallback_rms_is_mad_based`.

**Where:** `src/tools_pipeline.py`, `_constant_background`, ~line 1592.

**Issue:** the last-resort model uses `sigma_clipped_stats(...)[2]`, the plain
sigma-clipped standard deviation, whereas the normal path uses the MAD estimator.
On the crowded frames that trigger this fallback, the plain std is inflated by
undetected sources (the documented reason `MADStdBackgroundRMS` was adopted).

**Impact:** in exactly the fallback regime the per-pixel `sigma_bkg` — and hence
every flux error — can be inflated by up to ~2x.

**Recommendation:** use the MAD-based RMS in `_constant_background` too, for
consistency.

### F6 — Detection threshold uses a non-robust global std (low) — **FIXED**

**Status:** fixed. Both the `fwhm_fit` noise estimate and the
`detection_and_photometry` fallback now use `stdfunc="mad_std"`, so the
detection threshold and the error model use the same robust noise estimate.

**Where:** `src/pipeline.py`, `sigma_clipped_stats(image_sub, sigma=3.0)` feeding
`DAOStarFinder(threshold=(threshold_sigma + 0.5) * clipped_std)`, ~lines 1215–1230.

**Issue:** the detection threshold is set from the global sigma-clipped std,
which is inflated by sources in crowded fields, rather than from the MAD-based
noise that the error budget uses.

**Impact:** the effective detection threshold is higher than the requested
`threshold_sigma` in crowded fields; faint sources are lost. It is a
sensitivity/bias issue rather than a propagated-error issue.

**Recommendation:** derive the threshold from the MAD-based global RMS (or the
median of the `background_rms` map) so detection and the error model agree.

### F7 — PSF photometry has no PSF-mismatch or local-background term (low) — **FIXED**

**Status:** fixed. `perform_psf_photometry` builds one `LocalBackground`
(sigma-clipped median annulus, 2×–3× FWHM) and uses it in two places:

* **ePSF build** — the local background is subtracted from a copy of the image
  around each PSF star before `extract_stars`, so the empirical PSF is not built
  from sky-contaminated cutouts;
* **per-source fits** — the background is evaluated with the pipeline mask and
  passed as the `local_bkg` column of the PSF-fitting `init_params` (with the
  estimator as a fallback). Sources with a fully masked/off-frame annulus fall
  back to no subtraction instead of a NaN fit.

Test:
`TestPsfApertureCoherency::test_psf_local_background_recovers_flux_with_constant_sky`
— the sky-induced flux bias drops from ×2.49 (no local background) to ×1.001
across the ensemble. A PSF-model mismatch can still remain on real, PSF-varying
frames, but the background pedestal no longer drives it.

**Where:** `src/psf.py`, `PSFPhotometry(...)` construction ~line 1012; the
`local_bkg` argument is not passed.

**Issue:** PSF fluxes are fitted on `image_sub` (global model already
subtracted) with no explicit local background fit and no allowance for PSF model
mismatch. `flux_err` faithfully reflects the per-pixel noise model but not these
systematics, so PSF errors can be optimistic in nebulous/crowded fields.

**Recommendation:** consider `local_bkg` (e.g. the annulus median already
computed for the aperture) and/or a PSF-mismatch term; at minimum document that
the PSF error is a purely statistical estimate.

### F8 — Centroiding error is not propagated (low) — **FIXED**

**Status:** fixed. The aperture flux error now includes a centroid-position term.
`sigma_pos = sigma_PSF / S/N` (floored at 0.01 px, capped at 0.5×FWHM) and the
flux change for a one-sigma displacement is measured directly by re-measuring
the aperture shifted `±sigma_pos` along x and y (so real PSF
asymmetry/ellipticity is captured, unlike a symmetric-Gaussian derivative),
`sigma_centroid = 0.5·√(ΔF_x² + ΔF_y²)`. It is folded into
`aperture_sum_bkg_corr_err_*` and exported as `centroid_flux_err_X_X`. Tests:
every end-to-end budget test now verifies the full decomposition
`σ_flux² = σ_ap² + σ_bkg² + σ_centroid²`.

**Issue:** the aperture flux error accounts for per-pixel noise, the local
background and (via `area_overlap`) the masked area, but not the uncertainty in
the source position. For small apertures and undersampled PSFs, centroid error
is a real contribution.

**Recommendation:** optional secondary term, or document it as a known omission.

### F9 — Effective-gain fallback of 1 is conservative but not neutral (info)

As documented, with data in ADU and an unknown true gain `g`, setting
`g_eff = 1` overestimates the source Poisson variance by `g`, i.e. bright-source
magnitude errors by up to `sqrt(g)`. The ordering (header -> user -> empirical
-> 1) and the `BUNIT` check are sound. No change needed; keep the warning.

### F10 — Naming / observability nits (info) — **FIXED**

* The misleading `zero_point_error` column was renamed to `zero_point_scatter`
  (calibrator scatter), and the formal `zero_point_std` is now stored alongside
  it in the matched table.
* The documentation now states explicitly that the zero point is anchored to the
  aperture-corrected `instrumental_mag_*`, while
  `instrumental_mag_bkg_corr_*` is the uncorrected diagnostic reference.
* `ee_fraction_X_X` is now exported next to `apcorr_X_X`, so the correction is
  auditable (a large `apcorr` is traceable to a poorly-enclosed aperture).

---

## 5. Test coverage assessment

`tests/test_photometry.py` already contains a dedicated
`TestPipelineErrorBudget` class with end-to-end checks that:

* S/N equals `flux / sigma_flux` for the background-corrected flux;
* `sigma_mag` uses the same flux as the magnitude;
* the local-background term is folded in (`sigma_flux > aperture_sum_err`);
* the aperture correction is `>= 1`, bounded, and larger for the smaller
  aperture;
* the real ePSF path produces the stored `apcorr`.

`TestErrorPropagation` covers `sigma_mag_calib = sqrt(sigma_inst^2 + sigma_zp^2)`
and the default (instrumental-only) case. `TestGainResolution`,
`TestEffectiveGainEstimation`, `TestCosmicRayMasking`, `TestUnreliablePhotometryCut`
and `TestBright/FaintSource` cover the adjacent budget terms.

**Gaps worth adding tests for:**

1. `median_err`-based local-background term (F1) — a regression test asserting
   `sigma_flux` uses the median standard error.
2. `systematic_floor` actually affecting exported errors (F2).
3. `*_colorcorr_err` existence and value (F3).
4. `_constant_background` RMS is MAD-based (F5).
5. Zero-point `scatter` vs formal uncertainty distinguishable in the output (F10).

---

## 6. Suggested follow-ups (in priority order)

1. **F1** — switch the local-background uncertainty to `ApertureStats.median_err`.
2. **F2** — propagate/report the aperture-correction systematic; wire or remove
   `SYSTEMATIC_MAG_ERR_FLOOR`.
3. **F3** — export `*_colorcorr_err` with color-term fit uncertainty.
4. **F5 / F6** — use MAD-based noise for the constant fallback and the detection
   threshold, for consistency with the error model.
5. **F4 / F7 / F8** — document the correlated/systematic components and the
   PSF/centroid omissions, or model them.
6. Update `docs/PHOTOMETRY_FORMULAS.md` to match whatever is changed.

---

## 7. References

* photutils `calc_total_error` (formula and negative-pixel behaviour):
  <https://photutils.readthedocs.io/en/3.0.0/_modules/photutils/utils/errors.html>
* photutils `ApertureStats` (`median`, `median_err`, `std`, `sum_aper_area`):
  <https://photutils.readthedocs.io/en/latest/api/photutils.aperture.ApertureStats.html>
* photutils aperture photometry (masking, `area_overlap`, exact method):
  <https://photutils.readthedocs.io/en/stable/aperture.html>
* photutils PSF photometry (`PSFPhotometry`, `error`, `local_bkg`):
  <https://photutils.readthedocs.io/en/stable/psf.html>
* Howell, S. B. (2006), *Handbook of CCD Astronomy*, 2nd ed., ch. 4
* Merline, W. J., & Howell, S. B. (1995), *Experimental Techniques in CCD Photometry*
* Bevington, P. R., & Robinson, D. K. (2003), *Data Reduction and Error Analysis
  for the Physical Sciences*, 3rd ed.
* Standard error of the median: `sqrt(pi/2) * sigma / sqrt(N) ~= 1.2533 * sigma / sqrt(N)`

---

## 8. Post-review updates (v1.8.5)

This section records changes made after the review above. They concern PSF
photometry reliability and the ePSF geometry rather than the calibrated error
budget, which is unchanged.

* **PSF star selection** no longer uses the `sqrt(flux)` approximation for a
  hard 50% S/N cut, and the saturation filter no longer rejects the brightest
  unsaturated stars. On a clean 121-star field this goes from 14 selected
  stars (below the 15-star minimum, so the whole PSF step was skipped) to 49,
  with photometry on all 121 sources.
* **ePSF geometry**: the fitting region is rounded (`int(3·FWHM)` was biasing
  the effective radius by up to half a pixel), the ePSF is extracted from a
  larger ≈5× FWHM cutout so its wings are not truncated/normalised away, the
  model centre is `(N − 1)/2`, and the aperture-correction model is the exact
  object used for the fits (including any re-centering).
* **PSF-vs-aperture diagnostic**: `check_psf_aperture_consistency()` reports
  the median `psf_mag − aperture_mag` per aperture and warns above
  `PSF_APERTURE_CONSISTENCY_THRESHOLD` (0.15 mag). This makes the class of
  ePSF shape/normalisation offset discussed for PSF photometry directly
  observable instead of having to be inferred from the catalog.
* **Crowded local background** was investigated as a possible "double
  subtraction" of the background. It is not a real flux bias in photutils
  3.0 (per-source `local_bkg` is subtracted once from each source's own
  cutout); zeroing it for overlapping sources would bias crowded fluxes
  ~5-7% high, so no guard was applied. See
  `docs/PHOTOMETRY_FORMULAS.md` for the numbers.

---

*Prepared as a code review of the RAPAS Photometry Pipeline error budget.*
