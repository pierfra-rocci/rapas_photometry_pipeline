# RAPAS Photometry Pipeline

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/pierfra-rocci/rapas_photometry_pipeline/blob/main/LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue)](https://www.python.org/)
[![GitHub stars](https://img.shields.io/github/stars/pierfra-rocci/rapas_photometry_pipeline)](https://github.com/pierfra-rocci/rapas_photometry_pipeline/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/pierfra-rocci/rapas_photometry_pipeline)](https://github.com/pierfra-rocci/rapas_photometry_pipeline/forks)
[![GitHub issues](https://img.shields.io/github/issues/pierfra-rocci/rapas_photometry_pipeline)](https://github.com/pierfra-rocci/rapas_photometry_pipeline/issues)
[![Documentation](https://img.shields.io/badge/Documentation-GitHub%20Pages-blue)](https://pierfra-rocci.github.io/rapas_photometry_pipeline/)
[![Documentation build](https://github.com/pierfra-rocci/rapas_photometry_pipeline/actions/workflows/docs.yml/badge.svg)](https://github.com/pierfra-rocci/rapas_photometry_pipeline/actions/workflows/docs.yml)

![RAPAS Logo](rapas_logo.png)

![Version](https://img.shields.io/badge/version-1.9.3-blue)

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
- **Adaptive source detection**: if 3 sigma gives fewer than 100 sources, 2 sigma is tried first; the blind solve aims for at least 20 stars; the detection threshold is lowered 3 → 2 → 1 sigma when the field is small or too few stars are found, and the solve is retried at a deeper threshold if it fails
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
- **Astro-Colibri**: Transient and variable source alerts (progressive match radius up to 30 arcsec, match status shown in the app, with cutouts showing the source and its magnitude)
- **TNS (Transient Name Server)**: Public transients (AT/SN/FRB) from the daily staged CSV, matched with a progressive radius up to 30 arcsec (the closest object wins)
- **SkyBoT**: Solar system object identification
- **AAVSO VSX**: Variable star catalog
- **Milliquas**: Quasar and AGN catalog
- **10 Parsec Catalog**: Nearby stars (within 10 pc)

### 🔧 Advanced Processing

- **Cosmic ray removal**: Automatic L.A.Cosmic algorithm implementation via astroscrappy
- **Image enhancement**: Multiple visualization modes (ZScale, histogram equalization)
- **Quality filtering**: Automated source quality assessment with S/N-based flags
- **Error propagation**: Comprehensive per-pixel error model (background RMS plus Poisson source noise, added only when the resolved gain matches the units of the frame), robust local-background uncertainty, and zero-point error propagation into every calibrated magnitude error
- **Transient Detection**: Identification of transient candidates using survey templates (PanSTARRS north, SkyMapper south)

### 🖥️ User Interface

- **Interactive web interface**: Streamlit-based GUI with real-time processing
- **RAPAS branding**: Official RAPAS logo (three circles: white, cyan, red) displayed as favicon in the browser tab and prominently featured next to the title on login and app pages
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

Every successful login is written to the service logs with a local timestamp
and the pipeline version, for example
`[2026-10-18 18:46:22] RPP v1.9.0 - User 'intern1' logged in successfully (FastAPI backend).`
The FastAPI backend, the legacy Flask backend and the Streamlit frontend each
emit their own line (see [Service Logs](#service-logs)).

### 4. Configure Observatory

Set your observatory location and parameters in the sidebar (name, latitude, longitude, elevation).

### 5. Upload FITS File

Upload your astronomical image to the main area. Supported extensions: `.fits`, `.fit`, `.fts`, `.fits.gz`, `.fts.gz`.
The upload step stages the file in the UI and shows an immediate raw header
preview so you can inspect metadata before processing. Full loading, WCS
validation, and any astrometry re-solve begin only after you click
**Start Analysis**.
For multi-extension files, the first nonempty numeric image HDU is used,
preferring the primary HDU; tables and one-dimensional arrays are skipped.
The same image is used for plate solving. A solved FITS copy retains the
other HDUs and leaves the uploaded file unchanged.
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

## Service Logs

- `run_all_linux.sh` (and `manage_streamlit.sh`) write the backend output to
  `fastapi_backend.log` and the frontend output to `streamlit_frontend.log` in
  the project directory; when the services are started manually, the same
  messages go to the terminal.
- Every successful login writes a timestamped line with the pipeline version,
  for example
  `[2026-10-18 18:46:22] RPP v1.9.0 - User 'intern1' logged in successfully (FastAPI backend).`
  The legacy backend emits the same line tagged `(legacy backend)`, and the
  frontend emits `(frontend, FastAPI backend)` or `(frontend, legacy backend)`.
  The version is read at runtime from `src/__version__.py`.
- The in-app **Pipeline Logs** page (`pages/showlogs.py`) is disabled by default.
  To grant access, set `RPP_LOG_ADMIN_USER_IDS` in the Streamlit environment
  to a comma-separated list of trusted database user IDs, such as `1,3`.
  Usernames alone do not grant access. Verify the IDs against the shared
  database and update the setting whenever accounts or the database change.

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

Local plate-solving attempts share a 180-second wall-clock budget. Set
`RPP_LOCAL_ASTROMETRY_TIMEOUT_SECONDS` to a positive number to change it.
When the budget is exhausted, the local solver is stopped and the existing
online fallback is attempted if configured. Source detection, process cleanup,
online requests, and later refinement are not hard-bounded by this budget.

Headers are preserved rather than filled with invented celestial coordinates or
pixel scales. A WCS must have an explicit, nonsingular scale and a finite,
reversible celestial transform; conflicting CD and PC matrices are rejected.
These checks do **not** prove astrometric accuracy: verify the solution against
reference stars. If no valid WCS is available after solving, the app skips sky
matching and transient searches and exports uncalibrated pixel-only photometry
when a usable pixel scale is available. If no scale is available, set the pixel
scale override before running. If a valid WCS exists but the calibration
catalog or zero point is unavailable, the ZIP instead includes uncalibrated
instrumental photometry with the available sky coordinates; transient finding
is skipped because calibrated magnitudes are unavailable.

### Output Files

The downloadable ZIP contains files matching the input image's base name, except
for FITS files. FITS products, when generated, remain in the results directory
and are not included in the ZIP.

- **Photometry Catalog**: CSV and VOTable (`<input-base>.csv` / `<input-base>.vot`)
  restricted to a curated set of columns, always in the same order:
  `id`, `ra`, `dec`, `astrocolibri_name`, `astrocolibri_type`,
  `astrocolibri_classification`, `astrocolibri_ra`, `astrocolibri_dec`, `tns_name`, `tns_type`, `tns_ra`, `tns_dec`, `simbad_main_id`, `simbad_otype`, `simbad_B`,
  `simbad_V`, `skybot_NAME`, `skybot_OBJECT_TYPE`, `skybot_MAGV`, `aavso_Name`,
  `aavso_Type`, `aavso_Period`, `catalog_matches`, `x_center`, `y_center`, then
  the `snr_*` columns, the aperture magnitudes (each followed by its
  `quality_flag_*`), the PSF magnitude, the aperture and PSF magnitude errors,
  and the `psf_minus_aperture_mag_*` offsets. The three aperture suffixes are
  `_1_1`, `_1_3` and the user-defined radius (for example `_1_5` at FWHM Radius
  Factor 1.5), so the last suffix follows the user's choice. When the selected
  calibration band defines a prefix, magnitudes are exported under the
  filter-prefixed names (for example `rapasg_psf_mag`,
  `rapasg_aperture_mag_1_5`, `rapasg_aperture_mag_err_1_5`); otherwise the legacy
  unprefixed names are used. Columns absent from a run (for example without an
  Astro-Colibri key) are simply omitted. The first column `id` is a unique
  integer source number and is the same ID shown in the Aladin Lite popups.
  When Astro-Colibri sources are detected, those rows are sorted by
  `astrocolibri_name` and appear at the very top of both files. TNS matches add
  the transient designation (`tns_name`, e.g. `SN 2026abc`), its type
  (`tns_type`) and the TNS coordinates (`tns_ra`, `tns_dec`). Colour-corrected
  columns, aperture-correction factors and other internal diagnostics stay in the
  session (cutouts, transient finder) but are not written to these files
- **Transient candidates** (`*_transients.csv`): one row per candidate with the
  same columns as the catalog (including `id`, so each candidate can be traced
  back to its catalog row); candidates without a catalog counterpart keep their
  own coordinates, pixel position and calibrated PSF magnitude
- **Instrumental catalog** (`*_instrumental_catalog.csv` and `.vot`): pixel
  positions, measured fluxes and instrumental magnitudes when no valid WCS or
  photometric calibration could be obtained. These are not calibrated
  magnitudes; sky coordinates are included only when a valid WCS is available
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
- Matched detections use the calibrated PSF magnitude. When PSF photometry is
  unavailable (missing columns or no finite value for the matched detections),
  the calibrated 1.1 x FWHM aperture magnitude is used instead, so transient
  filtering is not blocked. The app then shows the shared PSF warning
  `PSF photometry is not available; using calibrated aperture photometry instead. Transient candidates use the calibrated 1.1 x FWHM aperture magnitude.`
  The main pipeline uses only the first sentence.
- Matched detections use the calibrated PSF magnitude. When PSF photometry is
  unavailable (missing columns or no finite value for the matched detections),
  the calibrated 1.1 x FWHM aperture magnitude is used instead, so transient
  filtering is not blocked. The app then shows the shared PSF warning
  `PSF photometry is not available; using calibrated aperture photometry instead. Transient candidates use the calibrated 1.1 x FWHM aperture magnitude.`
  The main pipeline uses only the first sentence.
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

- **Photometry Catalog** (CSV and VOTable): Curated source catalog (`id`, coordinates, Astro-Colibri/SIMBAD/SkyBoT/AAVSO matches, S/N, multi-aperture and PSF photometry with errors and quality flags)
- **Log File**: Detailed processing log with timestamps
- **Background Model** (FITS): 2D background and RMS maps
- **PSF Model** (FITS): Empirical PSF (or Gaussian fallback) for the field
- **Plots**: FWHM analysis, magnitude distributions, zero-point calibration
- **WCS Header** (TXT): Updated astrometric solution
- **WCS-Solved FITS**: Original image with refined WCS embedded in header (saved to `rpp_data/fits/`)

## Data and Tracking

- Uploaded and generated FITS data are stored under `rpp_data/fits/`
- Result archives are stored under `rpp_results/`
- New archives and WCS FITS copies use `private_v2/user_{id}/YYYY/MM/` under their respective roots. Each session has a `work_<id>` folder under its private results directory and a temporary upload folder under `/tmp/rpp_private_v2/user_{id}/`; stale work folders older than 24 h are cleaned after login.
- Older username-based archives and WCS FITS copies remain untouched and are not displayed or overwritten by the app. An operator must verify ownership independently before manually copying them into the appropriate private directory; username-derived filenames alone are not reliable proof of ownership. Back up files and database tracking records before migration.
- FastAPI and Flask must use the same user database. If `RPP_DB_PATH` is set, configure it consistently for both backends and the frontend.
- Upgrade and restart both backends together with the frontend. Startup creates a persistent `rpp_user_id_reservations` table, preserving existing IDs, usernames, and password hashes while preventing new registrations from reusing reserved IDs.
- Account IDs must not be reassigned to different people while their private files or log permissions remain. Reservation history can be lost when replacing or restoring a database: before doing so, review private storage and revoke or update configured log IDs. Keep database and storage backups consistent.
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

**Aladin Lite viewer stays empty (any browser)**:

Aladin Lite v3 paints the sky with WebGL2 and fetches its HiPS tiles from CDS
(`alasky.cds.unistra.fr`), so an empty viewer is usually a WebGL2 or a network
issue, not a caching one.

- **Read the red line above the viewer**: the widget now names the cause (no
  WebGL2 context, or `aladin.js` could not be downloaded) instead of showing
  a blank box. If the CDS script cannot be reached, the widget automatically
  retries it from jsDelivr at
  `https://cdn.jsdelivr.net/npm/aladin-lite@3.8.2/dist/aladin.js` as an ES
  module; if both sources fail or time out, the widget reports the failure.
- **Offline coordinates**: expand **Source coordinates (available without
  Aladin)** above the viewer. The science-image preview is also available
  independently of Aladin.
- **Firefox**: enable hardware acceleration, or set `webgl.force-enabled` to
  `true` in `about:config` and restart the browser.
- **Tiles blocked**: check the browser console for failed requests to
  `alasky.cds.unistra.fr` (proxy, firewall, HTTPS-Only mode); the viewer then
  falls back to `CDS/P/DSS2/red` on its own.
- **Note**: the jsDelivr fallback provides the Aladin Lite JavaScript library;
  survey imagery still requires access to the HiPS tile server. Both default
  surveys use CDS, so a CDS-wide outage can affect both.
- **Alternative browsers**: use Chrome or Edge when WebGL2 cannot be enabled.
- **Full details**: `docs/troubleshooting.rst`, section *Aladin Lite Viewer
  Issues*.

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

- SkyBoT, SIMBAD, GAIA, Astro-Colibri and TNS depend on external services.
- The TNS cross-match uses the public catalogue staged once a day by the TNS
  (`tns_public_objects.csv.zip`), downloaded with a plain `GET` and no API key:
  only the `tns_marker` user agent is sent (`RPP_TNS_MARKER_NAME` names it). One
  copy is shared by every user through the cache directory (system temp
  directory by default, `RPP_TNS_CACHE_DIR` for a shared cache drive): the first
  run of the day downloads it, the others read it, and a copy older than 24
  hours is downloaded again and overwritten. If the refresh fails the previous
  copy is still used, with a warning. A catalogue already on disk can be used
  with `RPP_TNS_CATALOG_PATH`, and `RPP_TNS_MIRROR_URL` makes the step query a
  shared `tns-mirror` HTTP API (`GET /v1/nearest`) instead of reading the
  catalogue. If the staged catalogue cannot be downloaded, the TNS Search API
  (`/api/get/search` + `/api/get/object`) is queried when `TNS_API_KEY` plus
  `TNS_BOT_ID`/`TNS_BOT_NAME` (bot) or `TNS_USER_ID`/`TNS_USER_NAME` (user) are
  set (`RPP_TNS_SANDBOX=1` selects `sandbox.wis-tns.org`).
- Temporary network failures or empty responses may reduce the available
  cross-match results without stopping the whole pipeline.
- If the cross-match step itself fails, the photometry catalogue (CSV and
  VOTable) is still written and available for download, without the
  cross-match columns. The transient finder runs independently, so a failure
  there does not affect the catalogue either. If the calibration cross-match
  or zero point fails, an uncalibrated instrumental catalog is exported
  instead and transient finding is skipped.
- Check the generated log file to see which catalog steps completed, timed out,
  or were skipped.

## Changelog

> The project currently targets version 1.9.3. Recent releases are listed here; the full history is in `docs/changelog.rst`.

### Current Release (1.9.3)

- **TNS public catalogue without an API key**: the daily `tns_public_objects.csv.zip` is downloaded from `https://www.wis-tns.org/sites/default/files/tns_public_objects/` with a plain `GET` that only carries the `tns_marker` user agent (a guest marker, named with `RPP_TNS_MARKER_NAME`); the older `/system/files/` path is tried if it fails. One copy is cached and shared by every user: the first run of the day downloads it, the others read it, and a copy older than 24 hours is downloaded again and overwritten, with a lock file so concurrent sessions do not download it twice. TNS credentials are now only needed by the Search API fallback.
- **TNS catalogue warm-up**: the shared catalogue is prepared at app startup in a background daemon thread (once per process), so neither the first page load nor the first analysis of the day pays for the download; an analysis that needs it meanwhile waits on the download lock and reuses the downloaded file, and the warm-up is skipped when `RPP_TNS_MIRROR_URL` is set.
- **TNS cache cleanup**: the download lock and any half-written `tns_public_objects.csv.<pid>.tmp` left by an interrupted download are removed by the next catalogue load once they are older than 10 minutes, reported as one `INFO: TNS: removed stale cache file(s) ...` line.
- **TNS status line**: the TNS status panel reports the catalogue state — how long ago it was downloaded, when the next refresh is due, and whether a background download is running.
- **Partial results and downloads**: when calibration is unavailable, the pipeline exports instrumental CSV and VOTable catalogs instead of calibrated results and skips transient finding. A failed later cross-match or transient search does not prevent the photometry catalog from being exported. The archived ZIP remains downloadable after a Streamlit rerun.
- **Aladin Lite resilience**: if the CDS script fails, the viewer tries the pinned jsDelivr module; a source-coordinate table remains available without the viewer. The default HiPS imagery still requires access to CDS.
- **Catalog service mirrors**: failed VizieR and SIMBAD requests can retry on Harvard mirrors. This retries the same service, not a different photometric catalog.
- **Private account storage**: new archives and WCS FITS files use account-ID-based paths; global in-app logs are restricted to configured administrator IDs.
- **Local astrometry timeout**: local plate-solving attempts share a configurable 180-second budget before the existing fallback is attempted.
- **VOTable export**: duplicate catalog columns are removed before CSV and VOTable export.

### Version 1.9.2

- **TNS (Transient Name Server) cross-match**: sources are also matched against the public TNS objects from the daily staged catalogue (`tns_public_objects.csv.zip`, downloaded at most once per UTC day and cached), with a progressive radius of 5, 10, 15, 20 and 30 arcsec that keeps the closest object per source. `tns_name`, `tns_type`, `tns_ra` and `tns_dec` are added to the catalog files, `TNS; ` is added to `catalog_matches`, the objects found in the field are listed in the app, and the Aladin popups name them. A missing key or network/HTTP error is reported as a warning and the run continues. Configure `TNS_API_KEY` with `TNS_BOT_ID`/`TNS_BOT_NAME` (or `TNS_USER_ID`/`TNS_USER_NAME`); `RPP_TNS_CACHE_DIR` and `RPP_TNS_CATALOG_PATH` control the cache location and an offline catalogue.
- **TNS Search API fallback**: when the staged catalogue cannot be obtained and TNS credentials are set, one `/api/get/search` cone search over the field followed by `/api/get/object` calls (up to 100 objects) provides the name, position and type of the objects; matching and the `tns_name`/`tns_type`/`tns_ra`/`tns_dec` output columns are unchanged. `RPP_TNS_SANDBOX=1` selects the TNS sandbox.
- **TNS spatial index**: the parsed TNS catalogue now carries a `scipy` KD-tree over its object positions, so selecting the objects of a field no longer compares all ~170k rows with the field centre. The index is built once when the catalogue is parsed, kept with it in the cache, and `tns_field_mask` falls back to the angular separation when no index is available.
- **TNS shared mirror**: setting `RPP_TNS_MIRROR_URL` to a shared `tns-mirror` HTTP API makes the TNS step query `GET /v1/nearest` once per detected source instead of downloading the catalogue; the mirror is authoritative when it answers and any failure falls back to the daily cached catalogue (fields with more than 500 sources use the catalogue directly).
- **Astro-Colibri radius extended to 30 arcsec**: the progressive Astro-Colibri match now runs 5, 10, 15, 20 and 30 arcsec (was 20); loose associations found only at 15, 20 or 30 arcsec are still flagged with a warning.

### Version 1.9.1

- **Aladin Lite diagnostics**: the widget reports why the sky image is missing instead of showing an empty viewer, both on a red line above the viewer and in the browser console: no WebGL2 context (hardware acceleration off, blocklisted GPU, `webgl.disabled`), or `aladin.js` not downloadable from `aladin.cds.unistra.fr`. It also logs the Aladin Lite release actually loaded (`[RPP Aladin] version ...`).
- **Aladin fallback survey**: the `fallback_survey` argument of `display_catalog_in_aladin()` defaults to `CDS/P/DSS2/red`. The HiPS `properties` document is probed with a 12 s timeout and, when it cannot be read, the base layer is switched to the fallback survey; the swap is reported above the viewer, and an explicit message is shown when neither survey is reachable.
- **Aladin troubleshooting and tests**: new *Aladin Lite Viewer Issues* section in `docs/troubleshooting.rst`, plus tests for the diagnostics and the fallback in `tests/test_network_resilience.py`.

### Version 1.9.0

- **Login audit line**: every successful login writes a timestamped line with the pipeline version to the service logs, for example `[2026-10-18 18:46:22] RPP v1.9.0 - User 'intern1' logged in successfully (FastAPI backend).` The line is emitted by `api/main.py` (FastAPI), `backend.py` (legacy Flask) and `pages/login.py` (frontend).
- **Curated catalog export**: `*_catalog.csv` / `.vot` now contain only the essential columns in a fixed order, starting with a unique `id` that matches the ID shown in the Aladin Lite popups; Astro-Colibri detections are sorted to the top of the files, and `*_transients.csv` reuses the same columns.
- **Astrometry detection ladder**: 20 stars minimum target (Astrometry.net has no hard minimum; it solves from the brightest stars first), detection threshold 3 → 2 → 1 sigma, best-effort with at least 5 stars on sparse fields, and a retry of the solve at a lower threshold on failure.
- **Astro-Colibri list and Aladin highlight**: every Astro-Colibri source returned for the field is listed in the app (name, RA/Dec, type, classification, match status, separation), the event RA/Dec are exported as `astrocolibri_ra` / `astrocolibri_dec`, and cross-matched sources are drawn as larger red squares (separate "Astro-Colibri matches" layer) in the Aladin Lite viewer.
- **Astro-Colibri cross-match**: events are matched to the nearest catalog source with a progressive radius (5, then 10, 15 and finally 20 arcsec; events without coordinates are ignored); the log reports how many matches were found at each radius, and a warning is shown when sources were matched only at the loose 15 or 20 arcsec tiers. SkyBoT requests are now retried on network/HTTP 429/5xx errors, use the mid-exposure epoch and the ephemeris uncertainty (up to 30 arcsec) for the match. The outcome (number of matches, none, or service unavailable) is now displayed in the app instead of being buried in the log.
- **Astro-Colibri cutouts**: postage stamps show the science frame and the HiPS template with the source circled and its calibrated magnitude in the title. The survey follows the field declination (PanSTARRS north, SkyMapper south) and automatically falls back to the other survey when the field is not covered.
- **Transient finder** now detects sources with SExtractor (via `stdpipe`) and accepts `uint16` images. When the photometry table has no usable PSF magnitudes, it falls back to the calibrated 1.1 x FWHM aperture photometry instead of returning no candidates.
- **SCAMP refinement** retries with wider tolerances when too few reference matches are found.
- **Scale-aware error model**: the Poisson term of the per-pixel error is now validated against the frame itself. A normalised float stack used to get a Poisson term ~1e5 times the measured sky variance, because the resolved gain is in e-/ADU while the data are not in counts; the inflated uncertainties made 22 of 26 ePSF-to-star fits return a negative flux, which inverted the ePSF and made `EPSFBuilder` abort with *Cannot normalize ePSF*, dropping PSF photometry for the whole frame. `resolve_poisson_gain()` keeps the gain when it matches the data's scale and otherwise re-derives it from the sky level and its RMS (`gain = sky / rms²`), and `compute_error_map()` builds the error map from it (background RMS alone when no gain is usable). PSF photometry and the aperture S/N are restored on such frames.
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

## Author

**Pier-Francesco Rocci**

## Acknowledgements

Development of this project was supported by AI language models: **ChatGPT**,
**Claude**, **Mistral** and **DeepSeek**. Their assistance contributed to the
design, implementation and documentation of the pipeline.

## Contributing

- Please open issues or pull requests for bugs, feature requests or improvements.
- Tests and clear reproducible examples are appreciated for complex changes.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) for details.
