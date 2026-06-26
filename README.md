# RAPAS Photometry Pipeline

A comprehensive astronomical photometry pipeline built with Streamlit, featuring local astrometric plate solving, multi-catalog cross-matching, and advanced photometric analysis capabilities.

## Features

### 🔭 Core Photometry

- **Multi-aperture photometry**: Automatic aperture photometry with two fixed radii (1.1×, 1.3× FWHM) plus a fully-computed user-defined third aperture (configurable via FWHM Radius Factor)
- **PSF photometry**: Effective Point Spread Function (ePSF) modeling.
- **Background estimation**: Advanced 2D background modeling with SExtractor algorithm
- **Source detection**: DAOStarFinder and Sep with configurable detection thresholds
- **FWHM estimation**: Automatic seeing measurement with Gaussian profile fitting

### 🌌 Astrometric Solutions

- **Local plate solving**: Integration with Astrometry.net via `stdpipe` for blind astrometric solving
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
- **Error propagation**: Comprehensive photometric error calculation with zero-point uncertainty
- **Transient Detection**: Identification of transient candidates using survey templates

### 🖥️ User Interface

- **Interactive web interface**: Streamlit-based GUI with real-time processing
- **Parameter configuration**: Adjustable detection and photometry parameters
- **Progress tracking**: Real-time status updates and logging
- **Results visualization**: Interactive plots and Aladin Lite v3 sky viewer integration

## Installation

### Prerequisites

- Python 3.12+
- Astrometry.net local installation (`solve-field` binary must be in PATH)
- Index files for Astrometry.net appropriate for your field of view

### Required Python Packages

The required packages are listed in the `pyproject.toml` file. You can install them using pip:

```bash
pip install -e .
```

This will install the project in editable mode and all the dependencies.

### System Dependencies

- **Astrometry.net**: Required for local plate solving (accessed via `stdpipe`)

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

Create an account or login with existing credentials. The frontend probes the
FastAPI service first and falls back to the legacy backend only when the API is
unreachable and a legacy backend is detected. If the API responds with an
internal error, the login page now reports that backend state instead of
silently retrying ``localhost:5000``. You can also run in anonymous mode if the
backend is not configured.

### 4. Configure Observatory

Set your observatory location and parameters in the sidebar (name, latitude, longitude, elevation).

### 5. Upload FITS File

Upload your astronomical image to the main area. Supported extensions: `.fits`, `.fit`, `.fts`, `.fits.gz`, `.fts.gz`.
The upload step stages the file in the UI and shows an immediate raw header
preview so you can inspect metadata before processing. Full loading, WCS
validation, and any astrometry re-solve begin only after you click
**Start Analysis Pipeline**.

### 6. Run Analysis

Adjust analysis parameters as needed (seeing, detection threshold, border mask,
filter band, etc.) and click **Start Analysis Pipeline**. Results and logs will
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
- Login using HTTP Basic credentials
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
- **Border Mask**: Pixel border exclusion size
- **Filter Band**: GAIA or synthetic magnitude band for calibration (maximum calibration magnitude is fixed at 21, the GAIA limit)
- **Astrometry Check**: Toggle to force a plate solve/WCS refinement

If **Astrometry Check** is enabled, the app forces a fresh plate-solving attempt
even when a valid WCS is already present. If that attempt fails, the app tries
to fall back to the original WCS solution when available.

### Output Files

- **Photometry Catalog**: CSV and VOTable with multi-aperture and PSF photometry
  including the legacy column names (for example `psf_mag`,
  `aperture_mag_1_5`) plus filter-prefixed aliases derived from the selected
  calibration band (for example `rapasg_psf_mag`,
  `rapasg_aperture_mag_1_5`). When catalog colors are available, the export may
  also include parallel color-corrected columns such as
  `psf_mag_colorcorr` and `aperture_mag_1_5_colorcorr`, plus trace fields like
  `source_color`, `color_term_label`, `color_term_value`, and
  `color_term_offset`
- **Background Model**: 2D background and RMS maps (FITS)
- **PSF Model**: Empirical PSF (FITS)
- **WCS Header**: Astrometric solution (TXT)
- **WCS-Solved FITS**: Original image with updated WCS header (when astrometry is performed)
- **Plots**: FWHM, magnitude distributions, zero-point calibration, and color-term diagnostics when available
- **Log File**: Detailed processing log

---

### Transient Candidates (Beta)

- **Enable Transient Finder**: Activates the transient search pipeline
- **Reference Survey**: Survey to use for template comparison (e.g., PanSTARRS)

## Astrometric Pipeline

### Local Plate Solving (stdpipe + Astrometry.net)

The pipeline uses `stdpipe` as a Python wrapper around a local Astrometry.net installation for robust astrometric solutions:

1. **Source Detection**: Automated star detection using photutils
2. **Blind Matching**: `stdpipe.astrometry.blind_match_objects` for initial WCS
3. **Parameter Optimization**: Automatic scale and position hint extraction from header if available
4. **Solution Validation**: Coordinate range and transformation validation

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

## Recent changes / Changelog (last update: 2026-05-19)

### Current Release (1.7.5)

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
- Added **Transient Finder** (Beta) utilizing `stdpipe` image subtraction and catalog filtering
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
