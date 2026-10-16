# RAPAS Photometry Pipeline

A comprehensive astronomical photometry pipeline built with Streamlit, featuring local astrometric plate solving with an optional online fallback, multi-catalog cross-matching, and advanced photometric analysis capabilities.

## Features

### 🔭 Core Photometry

- **Multi-aperture photometry**: Automatic aperture photometry with two fixed radii (1.1×, 1.3× FWHM) plus a fully-computed user-defined third aperture (configurable via FWHM Radius Factor); a robust sigma-clipped annulus background is subtracted per source and an empirical-PSF aperture correction puts every aperture on a total-flux scale
- **PSF photometry**: Effective Point Spread Function (ePSF) modeling.
- **Background estimation**: Advanced 2D background modeling with SExtractor algorithm
- **Source detection**: DAOStarFinder with configurable detection thresholds
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
- **Multiple filter bands**: Support for GAIA, synthetic, PanStarrs and SkyMapper photometry bands

### 🛰️ Multi-Catalog Cross-Matching

- **GAIA DR3**: Stellar parameters, proper motions, and photometry
- **SIMBAD**: Object identifications and classifications
- **Astro-Colibri**: Transient and variable source alerts (match status shown in the app, with cutouts showing the source and its magnitude)
- **SkyBoT**: Solar system object identification
- **AAVSO VSX**: Variable star catalog
- **Milliquas**: Quasar and AGN catalog
- **10 Parsec Catalog**: Nearby stars (within 10 pc)

### 🔧 Advanced Processing

- **Cosmic ray removal**: Automatic L.A.Cosmic algorithm implementation via astroscrappy
- **Image enhancement**: Multiple visualization modes (ZScale, histogram equalization)
- **Quality filtering**: Automated source quality assessment with S/N-based flags
- **Error propagation**: Comprehensive per-pixel error model (background RMS plus Poisson source noise), robust local-background uncertainty, and zero-point error propagation into every calibrated magnitude error
- **Transient Detection**: Identification of transient candidates using survey templates (PanSTARRS north, SkyMapper south)

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
- Sources are detected with SExtractor (via `stdpipe`) and matched to the pipeline photometry catalog
  within a seeing-based radius (FWHM, clamped to 1-4 arcsec); unreliable
  detections (edge-truncated, deblended, bad aperture) are discarded
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
If too few reference matches are found, a second attempt widens the SCAMP
tolerances. The refined solution is validated against the catalogue before it
is accepted; refinement failure keeps the best available WCS.

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

## Changelog

> The project currently targets version 1.8.9. Only the three latest releases are listed here; the full history is in `docs/changelog.rst`.

### Current Release (1.8.9)

- **Astro-Colibri cross-match**: events are matched to the nearest catalog source within 10 arcsec (events without coordinates are ignored). The outcome (number of matches, none, or service unavailable) is now displayed in the app instead of being buried in the log.
- **Astro-Colibri cutouts**: postage stamps show the science frame and the HiPS template with the source circled and its calibrated magnitude in the title. The survey follows the field declination (PanSTARRS north, SkyMapper south) and automatically falls back to the other survey when the field is not covered.
- **Transient finder** now detects sources with SExtractor (via `stdpipe`) and accepts `uint16` images.
- **SCAMP refinement** retries with wider tolerances when too few reference matches are found.
- **PSF star minimum lowered to 10** (was 15); a quality warning is shown when fewer than 25 PSF stars are available.
- **Session isolation**: each browser session now has its own `work_<id>` folder for temporary uploads and intermediate results, so concurrent users (or several tabs of the same account) can no longer mix FITS files, headers or outputs. Final ZIP archives remain in the user-level results folder. (Version 1.8.8 was skipped.)
- **Reset**: the *Reset* button now works in a single press and only clears the current session; it no longer clears the cache of every connected user.
- **Stale-file cleanup**: leftover work folders and temporary uploads older than 24 hours are removed once per session after login; archived ZIPs are never deleted.
- **Safer shared state**: the cached FITS parser is pure (no UI side effects), Matplotlib figures are closed after use, result-file matching respects name delimiters, and shared `astroquery` clients are configured once at import.
- **Tests**: new session-isolation tests and UI regression checks.


### Version 1.8.7

- **Pixel scale**: the silent 1 arcsec/pixel fallback is removed. When the scale cannot be derived from the header (or the solved WCS), the pipeline stops and asks for the *Pixel scale override* sidebar value.
- **Effective gain**: a single resolver (`resolve_effective_gain()`) is used for photometry errors, cosmic-ray masking and source detection. The `CVF` keyword is supported (`gain = 1/CVF`), explicit conversion keywords take precedence over `GAIN`, and the bare `GAIN` of QHY/ZWO cameras (a driver setting) is ignored. The plausible range is 0.02-100 e-/ADU.
- **Robustness**: PSF S/N guards for non-positive fluxes, safer cross-match and Astro-Colibri handling, scoped ZIP/temp-file cleanup, HTML-escaped Aladin popups.
- **Login**: a hint reminds Firefox users to press Enter after typing the password.

### Version 1.8.6

- **Transient candidate selection**: unreliable detections are now actually discarded through the detection-flag mask; detections are matched to the photometry catalog within a seeing-based radius (FWHM clamped to 1-4 arcsec, nearest counterpart only); the catalog radius in `filter_transient_candidates` is now half the seeing FWHM; the brightness-excess threshold is configurable (`mag_threshold`, default 2.0 mag).

## Contributing

- Please open issues or pull requests for bugs, feature requests or improvements.
- Tests and clear reproducible examples are appreciated for complex changes.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) for details.
