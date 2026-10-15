# RAPAS Photometry Pipeline

A comprehensive astronomical photometry pipeline built with Streamlit, featuring local astrometric plate solving with an optional online fallback, multi-catalog cross-matching, and advanced photometric analysis capabilities.

## Features

### 🔭 Core Photometry

- **Multi-aperture photometry**: Automatic aperture photometry with two fixed radii (1.1×, 1.3× FWHM) plus a fully-computed user-defined third aperture (configurable via FWHM Radius Factor); a robust sigma-clipped annulus background is subtracted per source and an empirical-PSF aperture correction puts every aperture on a total-flux scale
- **PSF photometry**: Effective Point Spread Function (ePSF) modeling.
- **Background estimation**: Advanced 2D background modeling with SExtractor algorithm
- **Source detection**: DAOStarFinder and Sep with configurable detection thresholds
- **FWHM estimation**: Automatic seeing measurement with Gaussian profile fitting

### 🌌 Astrometric Solutions

- **Local-first plate solving**: Integration with local Astrometry.net via `stdpipe`, with an optional Astrometry.net online fallback
- **Private online fallback**: If local solving fails and an API key is configured, submit the detected source list (not the FITS image) to Astrometry.net
- **WCS refinement**: Advanced astrometric refinement using standard pipe tools
- **Header validation**: Automatic WCS header fixing and validation
- **Coordinate systems**: Support for multiple coordinate reference frames

### 📊 Photometric Calibration

- **Catalogs integration**: Automatic cross-matching with standard star catalogs
- **Zero-point calculation**: Robust photometric calibration with outlier rejection
- **Color-term correction**: Optional first-order color correction fitted from matched calibration stars when catalog colors are available
- **Multiple filter bands**: Support for GAIA, synthetic, PanStarrs et SkyMapper photometry bands

### 🛰️ Multi-Catalog Cross-Matching

- **GAIA DR3**: Stellar parameters, proper motions, and photometry
- **SIMBAD**: Object identifications and classifications
- **Astro-Colibri**: Transient and variable source alerts
- **SkyBoT**: Solar system object identification
- **AAVSO VSX**: Variable star catalog
- **Milliquas**: Quasar and AGN catalog
- **10 Parsec Catalog**: Nearby stars (within 10 pc)

### 🔧 Advanced Processing

- **Cosmic ray removal**: Automatic L.A.Cosmic algorithm implementation via astroscrappy
- **Image enhancement**: Multiple visualization modes (ZScale, histogram equalization)
- **Quality filtering**: Automated source quality assessment with S/N-based flags
- **Error propagation**: Comprehensive per-pixel error model (background RMS plus Poisson source noise), robust local-background uncertainty, and zero-point error propagation into every calibrated magnitude error
- **Transient Detection**: Identification of transient candidates using survey templates

### 🖥️ User Interface

- **Interactive web interface**: Streamlit-based GUI with real-time processing
- **Parameter configuration**: Adjustable detection and photometry parameters
- **Progress tracking**: Real-time status updates and logging
- **Results visualization**: Interactive plots and Aladin Lite v3 sky viewer integration

## Installation

### Prerequisites

- Python 3.12+
- For local blind solving: Astrometry.net's `solve-field` binary and index files appropriate for your field of view
- For online fallback when local solving fails: an Astrometry.net API key and internet access

### Required Python Packages

The required packages are listed in the `pyproject.toml` file. You can install them using pip:

```bash
pip install -e .
```

This will install the project in editable mode and all the dependencies.

### System Dependencies

- **Astrometry.net**: Optional for local plate solving (accessed via `stdpipe`); online solving can be used as a fallback when an API key is configured

```bash
# Ubuntu/Debian
sudo apt-get install astrometry.net

# macOS with Homebrew
brew install astrometry-net
```

## Quick Start

### 1. Start the Backend

You must start the backend server before launching the frontend. Two modes are supported:

- **FastAPI (recommended, modern):**

  ```bash
  # Windows PowerShell
  .venv\Scripts\Activate.ps1
  python -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
  # Or use run_all_cmd.bat for one-click launch
  run_all_cmd.bat
  ```

- **Legacy Flask backend:**

  ```bash
  python backend.py
  ```

### 2. Start the Frontend

In a new terminal (with the virtual environment activated):

```bash
streamlit run frontend.py
# OR
streamlit run pages/app.py
```

Visit the URL printed by Streamlit (usually <http://localhost:8501>).

### 3. Login or Register

Create an account or login with existing credentials. The login field accepts
either the account username or the email address used at registration, so an
account stays reachable when the username has been forgotten. The frontend
probes the FastAPI service first and falls back to the legacy backend only when
the API is unreachable and a legacy backend is detected. If the API responds
with an internal error, the login page now reports that backend state instead
of silently retrying ``localhost:5000``. A backend must be available to log in;
anonymous access is not currently supported.

### 4. Configure Observatory

Set your observatory location and parameters in the sidebar (name, latitude, longitude, elevation).

### 5. Upload FITS File

Upload your astronomical image to the main area. Supported extensions: `.fits`, `.fit`, `.fts`, `.fits.gz`, `.fts.gz`.
The upload step stages the file in the UI and shows an immediate raw header
preview so you can inspect metadata before processing. Full loading, WCS
validation, and any astrometry re-solve begin only after you click
**Start Analysis**.
Each browser session is isolated from the others, and the **Reset** button clears only your own session (one press is enough).

### 6. Run Analysis

Adjust analysis parameters as needed (seeing, detection threshold, border mask,
filter band, etc.) and click **Start Analysis**. Results and logs will
be available for download as a ZIP archive.

## Backend Modes

The Streamlit frontend supports two backend modes:

- **FastAPI mode**: preferred when `api/main.py` is running on `http://localhost:8000`
- **Legacy mode**: fallback when the API is unavailable and `backend.py` is detected instead

The frontend auto-detects the backend at startup. You can override endpoints with:

- `RPP_API_URL`
- `RPP_LEGACY_URL`

This detection and fallback behavior is implemented in `pages/api_client.py` and
used by `pages/login.py` and `pages/app.py`.

## Account and API Features

When running with the FastAPI backend, the project supports:

- User registration with password complexity checks
- Login with the username or the registration email, using HTTP Basic credentials
- Password recovery via email with a 6-digit recovery code
- Per-user configuration persistence
- Authenticated FITS upload and listing endpoints

Main API routes currently exposed by `api/main.py` include:

- `/health`
- `/api/register`
- `/api/login`
- `/api/recovery/request`
- `/api/recovery/confirm`
- `/api/config`
- `/api/upload/fits`
- `/api/fits`

---

## Configuration

### Observatory Parameters

- **Name**: Observatory identifier
- **Latitude/Longitude**: Geographic coordinates (decimal degrees)
- **Elevation**: Height above sea level (meters)

### Analysis Parameters

- **Estimated Seeing (FWHM)**: Initial estimate in arcseconds
- **Detection Threshold**: Source detection sigma threshold
- **FWHM Radius Factor**: Multiplier applied to the measured FWHM for the user-defined aperture radius (0.5 – 2.0; values 1.1 and 1.3 are reserved for the two fixed apertures)
- **Pixel scale override (arcsec/pixel)**: Only used when the pixel scale cannot be read from the FITS header (`PIXSCALE`, CD matrix, pixel size + focal length...). `0` means automatic. If the scale is unknown and this field is `0`, the run stops and asks for a value; no default scale is assumed
- **Border Mask**: Pixel border exclusion size
- **Filter Band**: GAIA or synthetic magnitude band for calibration (maximum calibration magnitude is fixed at 21, the GAIA limit)
- **Astrometry Check**: Toggle to force a plate solve/WCS refinement

If **Astrometry Check** is enabled, the app forces a fresh plate-solving attempt
even when a valid WCS is already present. If that attempt fails, the app tries
the online Astrometry.net fallback when a key is configured. If online solving
is unavailable or also fails, the app tries to continue with the original WCS
when available.

### Output Files

The downloadable ZIP contains files matching the input image's base name, except
for FITS files. FITS products, when generated, remain in the results directory
and are not included in the ZIP.

- **Photometry Catalog**: CSV and VOTable with multi-aperture and PSF photometry.
  When the selected calibration band defines a prefix, the calibrated magnitude
  and error columns are exported under the filter-prefixed names (for example
  `rapasg_psf_mag`, `rapasg_aperture_mag_1_5`, `rapasg_aperture_mag_err_1_5`);
  otherwise the legacy unprefixed names (for example `psf_mag`,
  `aperture_mag_1_5`) are exported. When catalog colors are available, the export may
  also include parallel color-corrected columns such as
  `psf_mag_colorcorr` and `aperture_mag_1_5_colorcorr`, plus trace fields like
  `source_color`, `color_term_label`, `color_term_value`, and
  `color_term_offset`. The catalog also stores the per-aperture correction
  factors (`apcorr_X_X`) and their systematics (`apcorr_mag_err_X_X`) used to
  place aperture and PSF photometry on the same total-flux scale (the total
  background-subtracted flux error is computed internally and is not exported)
- **Headers**: Input header (`*_header.txt`) and, when a WCS is available, the
  astrometric header (`*_wcs_header.txt`)
- **Plots and log**: Diagnostic PNG files and the detailed processing log
- **PSF Model**: Empirical PSF FITS, when generated; saved in the results
  directory but not included in the ZIP
- **WCS-Solved FITS**: Original image with the updated WCS, when astrometry
  succeeds; saved in the results directory and `rpp_data/fits/`, but not in the
  ZIP

---

### Transient Candidates (Beta)

- **Enable Transient Finder**: Activates the transient search pipeline
- **Reference Survey**: Survey to use for comparison and template cutouts
  (PanSTARRS in the north, SkyMapper in the south, chosen from the field
  declination)
- Sources are detected with SEP and matched to the pipeline photometry catalog
  within a seeing-based radius (FWHM, clamped to 1-4 arcsec); detections flagged
  as unreliable by SEP (edge-truncated, deblended, bad aperture) are discarded
- Detections coincident with a reference-survey or Vizier source are rejected
  within half the seeing FWHM, unless they are a genuine brightness excess
  (`mag_threshold`, default 2.0 mag brighter than the catalog counterpart)
- No minimum S/N cutoff is applied to transient candidates. Low-S/N detections
  remain eligible if they pass the other catalog, known-source, and Solar
  System-object filters.
- Cutouts (science + HiPS template) are shown for the top 20 candidates ranked
  by signal-to-noise ratio, and Solar System objects are rejected through
  SkyBoT.

## Astrometric Pipeline

### Local-First Plate Solving (stdpipe + Astrometry.net)

The pipeline first uses `stdpipe` with a local Astrometry.net installation:

1. **Source Detection**: Automated star detection using photutils
2. **Blind Matching**: `stdpipe.astrometry.blind_match_objects` for initial WCS
3. **Parameter Optimization**: Automatic scale and position hint extraction from header if available
4. **Solution Validation**: Coordinate range and transformation validation

If local plate solving fails, RPP optionally tries the Astrometry.net online
service using the detected source list. The FITS image itself is not uploaded;
the source-list submission is marked private. The online fallback is skipped
when no API key is available. Configure `ASTROMETRY_API_KEY` in the
environment inherited by the Streamlit process, or add a key to
`~/.astropy/config/astroquery.cfg`:

```ini
[astrometry_net]
api_key = YOUR_ASTROMETRY_NET_API_KEY
```

For WSL, set the variable in the same shell before starting Streamlit. The
following prompts for the key without placing it in shell history:

```bash
read -s -p "Astrometry.net API key: " ASTROMETRY_API_KEY
echo
export ASTROMETRY_API_KEY
streamlit run frontend.py
```

When a WCS is available, RPP queries Pan-STARRS DR1 and refines it by running
the SCAMP binary directly (located on `PATH`, or via `RPP_SCAMP_EXECUTABLE`).
The refined solution is validated against the catalogue before it is accepted;
refinement failure keeps the best available WCS.

## Generated Files

The pipeline generates comprehensive output files, available as a ZIP download:

- **Photometry Catalog** (CSV and VOTable): Complete source catalog with multi-aperture and PSF photometry
- **Color-Corrected Magnitudes**: Additional `*_colorcorr` columns written in parallel with the standard zero-point-calibrated magnitudes when a usable catalog color is available
- **Log File**: Detailed processing log with timestamps
- **Background Model** (FITS): 2D background and RMS maps
- **PSF Model** (FITS): Empirical PSF (or Gaussian fallback) for the field
- **Plots**: FWHM analysis, magnitude distributions, zero-point calibration
- **WCS Header** (TXT): Updated astrometric solution
- **WCS-Solved FITS**: Original image with refined WCS embedded in header (saved to `rpp_data/fits/`)

## Data and Tracking

- Uploaded and generated FITS data are stored under `rpp_data/fits/`
- Result archives are stored under `rpp_results/`
- Each browser session works in its own `work_<id>` folder (temporary uploads under `/tmp/<user>/` and intermediate results under `rpp_results/<user>_results/`), so concurrent sessions never share files; stale folders older than 24 h are cleaned automatically after login
- User-scoped storage paths follow `user_{id}/YYYY/MM/...`
- WCS-solved FITS files and ZIP archives can be linked in the database for
  per-user analysis history

The storage and tracking logic lives in `api/storage.py` and `src/db_tracking.py`.

## Technical Details

### Algorithms

- **Background**: `photutils.Background2D` with `SExtractorBackground`
- **Detection**: `photutils.DAOStarFinder` with configurable parameters
- **PSF Modeling**: `photutils.EPSFBuilder` with Gaussian fallback
- **Photometry**: Circular apertures and PSF fitting
- **Astrometry**: `stdpipe` / Astrometry.net
- **Cosmic Rays**: `astroscrappy` (L.A.Cosmic)

### Performance

- **Caching**: Streamlit caching for improved performance
- **Memory Management**: Efficient handling of large FITS files

## Maintenance Scripts

The `scripts/` directory contains utility and migration tools that are not part
of the normal end-user pipeline:

- `scripts/migrate_add_wcs_zip_tables.py`: adds the WCS FITS and ZIP archive
  tracking tables for the 1.6.0 storage model
- `scripts/migrate_legacy_db.py`: migrates a legacy SQLite user database into
  the SQLAlchemy-backed schema used by the API backend
- `scripts/satellite_trail_detector.py`: standalone satellite trail masking tool
  using ASTRiDE; useful for experimentation, but not currently integrated into
  the main Streamlit pipeline

## Troubleshooting

### Browser Compatibility Issues

**Firefox Aladin Lite Issues**:

Firefox may have compatibility issues with Aladin Lite v3 due to WebAssembly loading.

- **Refresh the page**: Sometimes resolves the issue.
- **Clear Firefox cache**: Clear cookies and cached web content.
- **Alternative browsers**: Use Chrome, Edge, or Safari for best Aladin Lite compatibility.

### Common Issues

**Plate Solving Fails**:

- Ensure `solve-field` is in your system PATH.
- Check that astrometry.net index files are installed for your image scale.
- Verify sufficient stars in the field (>5-10 recommended).
- If **Astrometry Check** was enabled, a failed forced solve may still allow the
  app to continue with the original WCS when one was already valid.

**No Catalog Matches**:

- Check internet connectivity for catalog queries (GAIA, SIMBAD, etc.).
- Verify coordinate system and field center are correct.

**External Catalog Timeout or Network Failure**:

- SkyBoT, SIMBAD, GAIA, and Astro-Colibri depend on external services.
- Temporary network failures or empty responses may reduce the available
  cross-match results without stopping the whole pipeline.
- Check the generated log file to see which catalog steps completed, timed out,
  or were skipped.

## Recent changes / Changelog

> The project currently targets version 1.8.9. The notes below summarize the most recent documented improvements in the project history and are retained for reference alongside the current release baseline.

### Current Release (1.8.9)

- **PSF star minimum lowered to 10** (was 15); a quality warning is shown when fewer than 25 PSF stars are available. Transient finder no longer fails on `uint16` images (converted to float32 for SEP). Removed the ASTRiDE diagnostic messages and the verbose zero-point wording.
- **Session isolation**: each browser session now has its own `work_<id>` folder for temporary uploads and intermediate results, so concurrent users (or several tabs of the same account) can no longer mix FITS files, headers or outputs. Final ZIP archives remain in the user-level results folder. (Version 1.8.8 was skipped.)
- **Reset**: the *Reset* button now works in a single press and only clears the current session; it no longer clears the cache of every connected user.
- **Stale-file cleanup**: leftover work folders and temporary uploads older than 24 hours are removed once per session after login; archived ZIPs are never deleted.
- **Safer shared state**: the cached FITS parser is pure (no UI side effects), Matplotlib figures are closed after use, result-file matching respects name delimiters, and shared `astroquery` clients are configured once at import.
- **Tests**: new session-isolation tests and UI regression checks.

### Version 1.8.7

- **Pixel scale**: the silent 1 arcsec/pixel fallback is removed. When the scale cannot be derived from the header (or the solved WCS), the pipeline stops and asks for the new *Pixel scale override* sidebar value. The CD-matrix scale now uses column norms (valid for rotated matrices) and tolerates non-numeric keywords.
- **Login**: a hint reminds Firefox users to press Enter after typing the password.
- **Robustness review**: PSF S/N guards for non-positive fluxes, safer cross-match and Astro-Colibri handling, scoped ZIP/temp-file cleanup, HTML-escaped Aladin popups, and DB connections always closed.
- **Effective-gain resolution** (photometry error budget, cosmic-ray masking and source detection):
  - **`CVF` conversion factor supported.** The FITS `CVF` keyword is now read and converted with `gain = 1/CVF`. Explicit conversion keywords (`EGAIN`/`CCDGAIN` in e-/ADU and `CVF`) are tried before the conventional `GAIN`, so a driver setting written to `GAIN` can no longer shadow a genuine conversion keyword.
  - **Driver-gain cameras recognised.** On QHY (abstract 0-100 scale; a QHY268M reaches ≈0.77 e-/ADU at `GAIN=56`) and ZWO ASI (0.1 dB scale) cameras the bare `GAIN` value is a driver setting, not e-/ADU, and is now ignored: the pipeline falls back to `EGAIN`/`CVF`, the user-supplied gain, or the frame's own photon-transfer estimate. The recognisable cameras are listed in the editable `DRIVER_GAIN_INSTRUMENT_PATTERNS`.
  - **Plausible gain range widened 0.02-20 → 0.02-100 e-/ADU**, so cameras with a genuinely large conversion factor are no longer rejected; the driver-setting guard, not the range, is what rejects QHY/ZWO values. The *Detector Gain* control accepts up to 1000 e-/ADU.
  - **One resolver everywhere.** `src/astrometry.py` and `src/transient.py` now call `resolve_effective_gain()` instead of the old `65535 / max(image)` heuristic, so `EGAIN`/`CVF`/driver handling is consistent across photometry, cosmic-ray masking and SEP detection.
  - New tests cover the `CVF` inversion, keyword precedence, driver-camera detection (including a false-positive guard) and the widened range.
- **Documentation**: the photometry-formulas, features and tutorial pages (English and French) and the README describe the new gain resolution.

### Version 1.8.6

- **Transient candidate selection:**
  - **SEP detection flags are now actually applied.** The candidate filter was called with stdpipe's default flag mask (`0x7F00`), which only matches stdpipe/SExtractor flag bits; raw SEP flags use the low byte, so no detection was ever flag-filtered. `find_candidates` now passes `SEP_REJECT_FLAGS` (`0xDE`: boundary truncation, deblending overflow, degenerate centroid, all-masked/non-positive aperture), discarding unreliable detections.
  - **Seeing-based cross-match radius.** SEP detections are matched to the pipeline photometry catalog within `clip(FWHM, 1, 4)` arcsec instead of a fixed 10 arcsec. Since `spherical_match` returns *all* pairs inside the radius, the old value produced many-to-many matches in crowded fields (duplicated candidates, wrong PSF magnitude); only the nearest counterpart is kept now.
  - **Correct catalogue radius in `filter_transient_candidates`.** An already arcsec-scaled FWHM was passed with no `pixscale`, so the radius silently fell back to a fixed 1 arcsec. The function now receives the FWHM in pixels and `pixscale` in degrees/pixel, giving the intended half-FWHM radius.
  - **Brightness-excess test documented and configurable.** `checker_fn` follows stdpipe's contract (a `True` entry is a confirmed catalogue match that removes the detection); the docstring states this explicitly and the threshold is exposed as `mag_threshold` (default 2.0 mag). Detections with an unusable magnitude difference are kept instead of being silently dropped.
  - New regression tests cover the SEP flag mask and the candidate-checker semantics.
- **Documentation**: the user guide, advanced-features and API reference pages, the English and French tutorials, and the README now describe the current transient candidate algorithm.

### Version 1.8.5

- **PSF photometry reliability and ePSF geometry**:
  - **Star selection no longer silently skips PSF photometry.** The adaptive S/N cut no longer discards half the candidates when no real per-source S/N is available (it was cutting on `sqrt(flux)`), and the saturation cut no longer rejects the brightest unsaturated stars (it previously removed everything above the 85th percentile or 85% of the frame maximum). On a clean 121-star field that formerly yielded only 14 candidates — below the 15-star minimum, so the whole PSF step was dropped — 49 stars are now selected and PSF photometry runs on **all** 121 sources.
  - **ePSF shape/dimension corrected.** The fitting region is now rounded instead of truncated (`int(3·FWHM)` shifted the effective radius by up to half a pixel), and the empirical PSF is extracted from a larger **5×FWHM** cutout so its wings are not truncated/normalised away — the encircled-energy aperture correction and the fitted fluxes now reflect the real PSF profile.
  - **PSF-centre convention fixed.** The model centre is `(N−1)/2`, not `N/2`; the old convention reported a spurious half/oversampled-pixel peak offset and could trigger an unnecessary re-centering. The returned/`session_state` ePSF is now the exact model used for the fits, so the aperture correction cannot be computed from a stale (non-re-centred) PSF.
  - **New PSF-vs-aperture consistency diagnostic.** After photometry the pipeline matches PSF and aperture sources and reports the median `psf_mag − aperture_mag` per aperture. Both methods share the total-flux scale, so a median offset larger than `PSF_APERTURE_CONSISTENCY_THRESHOLD` (0.15 mag) raises a warning and the full per-aperture diagnostics are stored in `st.session_state["psf_aperture_consistency"]`.
  - **Per-source offset columns.** The same difference is exported for every aperture as `psf_minus_aperture_mag_X_X` (with filter-prefixed aliases such as `rapasg_psf_minus_aperture_mag_1_3`), left NaN where only one method measured the source.
  - **Offset diagnostic plot.** The results view renders the offset per source (`plot_photometry_offsets`): a scatter of the offset against calibrated PSF magnitude with the per-aperture median and zero lines, plus the offset histogram, saved as `<base>_psf_aperture_offset.png` in the user's results folder.
  - **Catalog cross-match robustness.** SkyBoT requests VOTable output and skips malformed service-error responses without treating them as empty fields. The 10 Parsec catalog's proper motions are propagated to the observation epoch when available, and Aladin popups include SkyBoT and nearby-star identifications.
  - **Gaia DR3 fallback reliability.** The VizieR fallback now uses the Gaia DR3 cone-search catalog and its returned results; the direct TAP fallback avoids unsupported distance-function ordering in ADQL.
  - **Crowded-field local background left unchanged by design**: an empirical check showed that zeroing the per-source local background for sources with overlapping `fit_shape` regions biases crowded fluxes ~5–7% **high**, while keeping it recovers the true flux, so no guard was applied.
  - **Documentation**: the user and developer docs (PSF sections, photometry formulas, API reference and tutorials) now describe the selection, ePSF-extraction and consistency-diagnostic changes.

### Version 1.8.4

- **Configuration persistence**:
  - The *Max Magnitude Error* (`mag_err_max`) and *Detector Gain* (`effective_gain`) sidebar controls are now written into `analysis_parameters`, so they are saved with **Save Settings** and restored at the next login on both the FastAPI and legacy Flask backends.
  - Both keys are seeded in `default_analysis_params`, and the save/reload round-trip is covered by the new `tests/test_config_persistence.py` (FastAPI `/api/config`, legacy Flask `/save_config` + `/get_config`, and the save/load wiring in `pages/app.py` / `pages/login.py`).
  - The legacy backend dependencies `flask` and `flask-cors` are now declared in `pyproject.toml` and `requirements.txt`.
- **Documentation**:
  - The English and French tutorials now describe the *Max Magnitude Error* and *Detector Gain* parameters.
  - `docs/api.rst`, `docs/advanced_features.rst`, `docs/installation.rst`, and `docs/features.rst` document the configuration-persistence paths, the backend dependencies, and the test suite.

### Version 1.8.3

- **Error-budget review fixes** (S/N and error propagation):
  - The local-background (annulus) uncertainty now uses the standard error of the **median** (`ApertureStats.median_err`, `√(π/2)·s/√A_ann`). The previous mean-based `std/√A_ann` under-estimated the local term by ~25% (about 8% of `σ_flux` in the background-limited regime).
  - The **aperture-correction systematic** is now propagated into every aperture magnitude error: `σ_apcorr = 1.0857 · f_apcorr · |apcorr − 1| / apcorr` (`APCORR_SYSTEMATIC_FRACTION`, default 0.20), exported as `apcorr_mag_err_X_X`. The global `SYSTEMATIC_MAG_ERR_FLOOR` is now actually wired through `resolve_systematic_floor()` (session state / the `RPP_SYSTEMATIC_MAG_ERR_FLOOR` environment variable).
  - **Color-term uncertainties** are propagated: `fit_color_term_correction` returns the fit parameter covariance, and `apply_color_term_correction` writes `color_term_offset_err` plus a `*_colorcorr_err` column for every corrected magnitude, combining the baseline calibrated error with the color-term fit uncertainty.
  - Calibrated magnitude errors now publish their **random vs correlated components**: `*_mag_err_*_random` (per-source) and `*_mag_err_*_correlated` (the field-common zero-point/systematic term), alongside the total error.
  - The last-resort **constant background** model and the **source-detection threshold** now use the same MAD-based noise estimator as the error model, instead of a source-inflated plain std.
  - **PSF photometry** subtracts a per-source local background (`LocalBackground`, 2×–3× FWHM) and builds the empirical PSF from **de-contaminated star cutouts**. On a synthetic frame with a sky pedestal the recovered PSF flux bias drops from ×2.5 to ×1.001.
  - The aperture flux error now includes a **centroid-position term** (`centroid_flux_err_X_X`), measured by re-evaluating the aperture shifted by the centroid uncertainty `σ_PSF/S/N`.
  - The **zero-point uncertainty** is computed from the sigma-clipped **untrimmed** residual scatter, removing the low bias caused by the `|residual| ≤ 0.5 mag` cut.
  - New **audit columns**: `ee_fraction_X_X` (the encircled-energy fraction behind `apcorr_X_X`); the misleading `zero_point_error` column was renamed `zero_point_scatter`; the formal `zero_point_std` is exported as a catalog column, while `zero_point_scatter` and `zero_point_n` are recorded on the matched calibration table.
  - The exported catalog is now **consistent under a filter prefix**: `drop_legacy_magnitude_columns` also drops the legacy `psf_mag_err_random` / `psf_mag_err_correlated` columns, so the PSF error components are replaced by their prefixed aliases like the aperture ones (`aperture_mag_err_*_random` / `_correlated`) instead of surviving unprefixed alongside them.
  - **Colour-corrected columns preserved consistently**: `drop_legacy_magnitude_columns` no longer drops the legacy `*_colorcorr_err` columns (the aperture form was mistakenly treated as a legacy magnitude), so both the legacy and prefixed colour-corrected columns are exported in parallel.

### Version 1.8.1

- **Photometry error-budget overhaul** (S/N and error estimation):
  - A single, consistent flux (background-corrected **and** aperture-corrected) now drives the S/N, magnitude and magnitude error. Previously the magnitude used the background-corrected flux while its error used the raw aperture sum, so the two were inconsistent whenever the local background was not ≈0.
  - The local-background (annulus) uncertainty is now propagated into the flux error: `σ_flux² = σ_aperture² + σ_total_bkg²`. Omitting this term underestimated the error and overestimated the S/N by ~20% in the background-limited regime.
  - The local background is now a **sigma-clipped median** (`ApertureStats`, `ΣClip(3σ)`) instead of a plain annulus mean, so neighbours and cosmic rays no longer bias it.
  - The pipeline mask (border + cosmic rays + satellite trails + NaNs) is now applied to aperture and annulus photometry, and the exact masked-pixel area (`area_overlap`) is used.
  - **Aperture correction** derived from the empirical-PSF encircled-energy curve (analytic Gaussian fallback) puts aperture and PSF photometry on the same total-flux scale; the correction is stored in the new `apcorr_X_X` column.
  - The zero-point uncertainty is now propagated into calibrated magnitude errors: `σ_mag_calib = √(σ_mag_inst² + σ_zp²)`; the zero point is no longer rounded to two decimals. Since the zero point is a median, its uncertainty now carries the `√(π/2)` median factor (`1.2533 × scatter / √N`).
  - The effective gain is read from the FITS `GAIN`/`EGAIN` keyword; when absent the pipeline asks the *Detector Gain* parameter (`analysis_parameters["effective_gain"]` / `RPP_EFFECTIVE_GAIN`), then estimates it empirically from the frame's own variance-vs-signal statistics, and only then treats the data as electrons (`effective_gain = 1`) instead of the previous arbitrary `65535 / max(image)` heuristic.
  - The unreliable-photometry cut now drops a source only when **every** method reports a magnitude error ≥ 2 mag (`MAG_ERR_MAX`, tunable in the sidebar via *Max Magnitude Error*, 0 disables it); a source with a usable aperture *or* PSF measurement is kept.
  - `validate_cross_match_results()` (column-name bug fixed, now actually called) flags cross-matches that are clustered or show an unusually large median separation.
  - **Cosmic-ray masking fixed**: the mask now uses the resolved effective gain (a QHY-style `GAIN=56` had made L.A.Cosmic flag 49% of a real 900 s frame; it now flags 0.01%, recovering +76 detections and cutting the calibrator scatter from 0.235 to 0.050 mag), reads `RDNOISE`/`ENOISE` when available, and refuses to apply a mask covering more than 20% of the frame.
  - **Gain-resolution fixes**: a zero `GAIN` no longer hides a valid `EGAIN`, and implausible values (outside 0.02-20 e-/ADU, e.g. detector driver settings) are reported and ignored.
  - **Sky background model hardened**: the per-box noise now uses a MAD-based RMS instead of the plain std (undetected sources in the boxes previously inflated the noise map by up to ~2x in crowded fields), the background-mesh smoothing filter is capped for small meshes to avoid over-smoothing on gradients, and frames that made `Background2D` reject every box (crowded fields, large bad areas) no longer abort the run: the estimation degrades to more permissive settings and finally to a single sigma-clipped background level.
  - **PSF star selection**: the local-background-flatness check now compares the local background scatter to the global background scatter instead of dividing by the (near-zero) local median. This fixes a case where every candidate star could be rejected — silently skipping PSF photometry — once the sky background had been subtracted.

### Version 1.8.0

- **Gaia VizieR fallback**: When the Gaia TAP service is unavailable or returns an error, the pipeline automatically retries the query against the VizieR mirror with a reduced column set, maintaining photometric calibration availability during TAP outages.
- **Zero-point calibration improvements**:
  - Sigma clipping is now applied before the final zero-point fit for more robust outlier rejection
  - Sources with residuals beyond ±0.5 mag from the median are excluded before the fit
  - Aperture 1.3× FWHM is used as the primary reference aperture for zero-point determination
- **Color-term calibration**:
  - A simple first-order color term can now be fitted from matched calibration stars when the selected filter has a supported catalog color
  - The pipeline writes parallel `*_colorcorr` magnitude columns instead of replacing the baseline zero-point-calibrated outputs
- **New `plot_astrocolibri_cutouts()` function**: Generates postage-stamp cutout images for Astro-Colibri transient candidates directly from the science frame, enabling rapid visual inspection.
- **New `drop_legacy_magnitude_columns()` helper**: Utility function to strip the legacy (non-prefixed) magnitude columns from the output catalog when only filter-prefixed aliases are needed.
- **Backend detection improvements**: Enhanced error handling and detection logic for the FastAPI/legacy backend auto-detection; new `tests/test_backend_detection.py` test suite covers probe and fallback scenarios.
- **Gunicorn uses UvicornWorker by default**: `gunicorn.conf.py` now sets `uvicorn.workers.UvicornWorker`, preventing WSGI/ASGI mismatches when the API is deployed with Gunicorn.
- **Graceful stdpipe import handling**: `ImportError` from optional `stdpipe` components is caught and reported cleanly rather than crashing the module load.
- **Dependency cleanup**: Removed the unused `mpmath` dependency; tightened `setuptools` version constraints for reproducible builds.
- **FWHM sampling tuned**: Source sample size for FWHM estimation increased to 1 250 for better seeing measurement stability.
- **UI and logging improvements**:
  - Streamlit messages throughout the pipeline use consistent severity levels (success / warning / info / error)
  - Verbose debug logging removed from the hot path to reduce log noise
  - `.fits` files are excluded from the per-analysis download buttons to keep the archive list clean
  - Archived ZIP files in the sidebar are listed without preloading their contents; archive bytes are loaded only when a specific archived download is requested
  - SkyBoT parsing now degrades gracefully on malformed service payloads by logging a warning and continuing without SkyBoT matches
- **Troubleshooting docs**: New FastAPI + Gunicorn troubleshooting section added to `docs/troubleshooting.rst`.

### Version 1.7.3

- **Photometry catalog aliases**: exported calibrated magnitude columns now also include filter-prefixed aliases derived from the selected calibration band in `GAIA_BANDS` (for example `rapasg_psf_mag`, `rapasbp_aperture_mag_1_5`) while keeping the legacy column names for compatibility.

### Version 1.7.2

- **Sexagesimal Coordinate Display**:
  - Target RA and DEC are now shown in both decimal degrees and sexagesimal format (HH:MM:SS / ±DD:MM:SS) in the Statistics section
  - The same dual format is recorded in the processing log
- **Magnitude Error Plot — Logarithmic Scale**:
  - The Y-axis of the "Magnitude Error vs Magnitude" scatter plot is now displayed on a logarithmic scale for better readability over a wide dynamic range

### Version 1.7.0

- **Staged Start-Analysis Workflow**:
  - FITS upload now stages the file in the UI without immediately starting FITS loading, WCS checks, or photometry
  - Scientific processing begins only after clicking **Start Analysis Pipeline**
- **Astrometry Workflow Fixes**:
  - The Astrometry Check toggle now consistently controls forced astrometry solving from the saved analysis settings
  - The frontend no longer reaches the astrometry step with an unset temporary file path during the pre-start phase
  - When a forced solve fails but the original WCS is still valid, the app can continue with that valid WCS
- **Catalog Query Robustness**:
  - SkyBoT timeout was reduced from 300 seconds to 120 seconds in both catalog enhancement and transient filtering
  - Remote catalog failures and timeouts are documented as partial-result conditions rather than full-pipeline failures where possible
- **New FWHM Radius Factor Parameter**:
  - A configurable aperture radius multiplier (0.5 – 2.0 × FWHM) is now fully implemented as a third photometric aperture
  - The pipeline computes flux, S/N, magnitude, magnitude error, and quality flag for this aperture alongside the two fixed ones
  - Output catalog columns follow the same naming convention: `aperture_mag_X_X`, `aperture_mag_err_X_X`, `snr_X_X`, `quality_flag_X_X` (e.g. `_1_5` for a factor of 1.5)
  - The zero point is applied to the third aperture in the same calibration pass as the fixed apertures
  - When calibration-star colors are available, matching `*_colorcorr` magnitude columns are also exported in parallel with the baseline calibrated magnitudes
  - Values 1.1 and 1.3 are reserved for the existing fixed apertures; selecting them collapses back to two apertures without duplication
  - The value is saved in the per-user configuration and persists across sessions
- **Packaging and Runtime Fixes**:
  - Added `email-validator` so FastAPI schemas using `EmailStr` work in a clean environment
  - Replaced the self-referential remote editable install in `requirements.txt` with `-e .`
  - Added `run_frontend.py` so the configured `pfr` entry point resolves correctly
  - Updated Black configuration to target Python 3.12
- **Documentation Refresh**:
  - Updated the README, tutorial, user guide, API docs, and core Sphinx pages to match the current backend behavior, staged upload flow, storage model, and troubleshooting guidance

### Version 1.6.0

- **New Storage Structure**:
  - FITS file storage moved from `data/fits/` to `rpp_data/fits/` (same level as `rpp_results/`)
  - WCS-solved FITS files are now saved both in the ZIP archive and to `rpp_data/fits/`
  - Permanent copy in `rpp_data/fits/` is overwritten when the same file is reprocessed
- **WCS-Solved FITS Export**:
  - New `save_fits_with_wcs()` utility function saves original image with updated WCS header
  - Automatically triggered after successful astrometry solving
- **Database Tracking System**:
  - New tables track WCS-solved FITS files and result ZIP archives per user
  - Many-to-many relationship: one FITS file can be linked to multiple analysis runs
  - Migration script `scripts/migrate_add_wcs_zip_tables.py` for existing databases
  - Query functions to retrieve user's analysis history
- **Improved Quality Filtering**:
  - Magnitude error threshold reduced from 2.0 to 1.5 for stricter quality control
  - Added absolute value handling for occasional negative error values
- **Testing**:
  - New test suite `tests/test_database.py` with 35 tests for database functionality

### Version 1.5.3

- **Photometry Calculation Improvements**:
  - Fixed critical PSF S/N calculation (was using `sqrt(flux_err)` instead of `flux_err`)
  - Removed S/N rounding to preserve precision and avoid divide-by-zero errors
  - Added proper error propagation for calibrated magnitudes: `σ_mag_calib = √(σ_mag_inst² + σ_zp²)`
  - S/N now uses background-corrected flux for more accurate estimation
  - Added quality flags for photometry: 'good' (S/N≥5), 'marginal' (3≤S/N<5), 'poor' (S/N<3)
- **Documentation**: Comprehensive mathematical documentation of S/N and magnitude error formulas
- **Testing**: New unit test suite for photometric calculations (`tests/test_photometry.py`)

### Version 1.5.1

- **Python 3.12 Compatibility**: Project upgraded to require Python 3.12 with verified dependency resolution
- **Enhanced Dependency Management**: Updated to latest stable versions:
  - astropy 7.1.1
  - astropy-iers-data 0.2025.11.3.0.38.37
  - aiohttp 3.11.18
  - certifi 2025.1.31
- **Improved astrometry support**: Via `stdpipe` integration for robust local plate solving
- **Transient Detection (Beta)**: Identification of transient candidates using template comparison and catalog filtering
- **Multi-catalog cross-matching**: GAIA DR3, SIMBAD, SkyBoT, AAVSO VSX, Milliquas, 10 Parsec Catalog, and Astro-Colibri integration

### Version History

#### Version 1.4.7

- Python 3.12 Compatibility: Project upgraded to require Python 3.12 with verified dependency resolution
- Enhanced Dependency Management: Updated to latest stable versions (astropy 7.1.1, aiohttp 3.11.18, certifi 2025.1.31)

#### Version 1.4.5

- Integrated `stdpipe` for robust local astrometric solving via Astrometry.net
- Added **Transient Finder** (Beta) utilizing `stdpipe` reference-survey comparison and catalog filtering
- Added **10 Parsec Catalog** to multi-catalog cross-matching pipeline
- Improved PSF Photometry with empirical ePSF and Gaussian fallback mechanisms
- Automatic Cosmic Ray rejection enabled by default using `astroscrappy` (L.A.Cosmic algorithm)
- UI/UX enhancements: Astrometry check toggle, Aladin Lite v3 integration for interactive sky viewing
- Refactored header handling and WCS creation for improved stability and accuracy

## Contributing

- Please open issues or pull requests for bugs, feature requests or improvements.
- Tests and clear reproducible examples are appreciated for complex changes.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) for details.
