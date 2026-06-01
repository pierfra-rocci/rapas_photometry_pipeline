
# Step-by-Step Photometry Tutorial

This tutorial guides you through a typical analysis session.

> **Important**: The pipeline requires FITS images with **16-bit or 32-bit** pixel depth. 8-bit images are not supported and may produce incorrect results.

## 1. Upload a FITS File

In the main area, use the file uploader to select your FITS image. Supported extensions: `.fits`, `.fit`, `.fts`, `.fits.gz`, `.fts.gz`.

Uploading the file only stages it in the interface. The FITS file is loaded and
checked only after you click the Start Analysis button.

## 2. Configure Observatory

In the sidebar, set your observatory details:

- **Name**: e.g., "Backyard Observatory"
- **Latitude, Longitude, Elevation**: Decimal degrees/meters. These may be auto-filled from the FITS header, but always verify.

## 3. Set Analysis Parameters

In the sidebar, adjust:

- **Estimated FWHM**: Initial guess in arcseconds
- **Detection Threshold**: Sigma threshold for source detection
- **FWHM Radius Factor**: Multiplier for the user-defined aperture radius (0.5 – 2.0). Values 1.1 and 1.3 are reserved for the two fixed apertures and cannot be selected without a warning.
- **Border Mask**: Pixels to exclude at the image edge
- **Calibration Filter Band**: Choose the photometric band for calibration (e.g. g, r, i)
- **Astrometry Check**: Enable to force plate solving/WCS refinement

*Cosmic ray removal is always performed automatically using the L.A.Cosmic algorithm (astroscrappy).*

## 4. (Optional) Astro-Colibri API Key

Enter your Astro-Colibri API key in the sidebar to enable transient and variable source cross-matching.

## 5. (Optional) Transient Candidates

Expand the "Transient Candidates" section in the sidebar:

- **Enable Transient Finder**: Activates transient detection using image subtraction against reference surveys (PanSTARRS1 for north, SkyMapper for south).
- **Reference Filter**: Select the filter band for template comparison.

## 6. Run the Analysis

Click **Start Analysis Pipeline** to begin processing. At that point the app loads
the FITS file, checks the header and WCS, and then runs the pipeline.

The following steps are performed:

1. **Background & Noise Estimation**
2. **Source Detection & Cosmic Ray Removal**
3. **Photometry**: Multi-aperture (up to 3 apertures) and PSF photometry, S/N and error calculation, quality flag assignment
4. **Astrometric Refinement** (if enabled)
5. **Photometric Calibration**: Cross-match with catalogs for zero-point
6. **Multi-Catalog Cross-Matching**: GAIA DR3, SIMBAD, SkyBoT, AAVSO VSX, Milliquas, 10 Parsec, Astro-Colibri
7. **Transient Detection** (if enabled)

If **Astrometry Check** is enabled, the app forces a new plate-solving attempt
even when a valid WCS is already present. If that forced solve fails, the app
tries to restore and continue with the original WCS when possible.

## 7. Download and Interpret Results

After processing, download the ZIP archive containing:

- `*_catalog.csv` / `.vot`: Source catalog with photometry, errors, flags, and cross-matches. Each aperture produces its own set of columns: `aperture_mag_X_X`, `aperture_mag_err_X_X`, `snr_X_X`, `quality_flag_X_X` (fixed apertures: `_1_1`, `_1_3`; user-defined aperture: e.g. `_1_5` for FWHM Radius Factor = 1.5). The catalog also keeps the legacy names and adds filter-prefixed aliases derived from the selected calibration band, for example `rapasg_psf_mag` or `rapasg_aperture_mag_1_5`.
- `*_background.fits`: 2D background and RMS maps
- `*_psf.fits`: Empirical PSF model
- `*_wcs_header.txt`: Astrometric solution header
- `*_wcs.fits`: Original image with refined WCS header (when astrometry is performed)
- `*.log`: Processing log
- `*.png`: Diagnostic plots

**Note on WCS-Solved FITS Files**: When astrometry is performed, the pipeline saves your original image with the updated WCS solution in two locations:

1. **In the ZIP archive** (`*_wcs.fits`) - included with your download
2. **In `rpp_data/fits/`** - a permanent copy that gets overwritten if you reprocess the same file

**Analysis Tracking**: All analysis results are automatically tracked in the database:

- Each WCS-solved FITS file is recorded with your username
- Each ZIP archive is linked to its source FITS file(s)
- You can have multiple analysis runs from the same FITS file (different parameters)
- Query your analysis history programmatically using `src/db_tracking.py` functions

### Photometric Quality Flags

| Quality Flag | S/N Range   | Reliability | Recommended Use  |
| :----------- | :---------- | :---------- | :--------------- |
| `good`       | S/N > 10    | High        | Science-ready    |
| `marginal`   | 5 < S/N ≤ 10 | Moderate   | Use with caution |
| `poor`       | S/N ≤ 5     | Low         | Exclude          |

Magnitude errors are propagated as:

- **Instrumental error**: σ_mag = 1.0857 × (σ_flux / flux)
- **Calibrated error**: σ_mag_calib = √(σ_mag_inst² + σ_zp²)

**Interactive Analysis**: Use the embedded Aladin Lite viewer for real-time exploration, or export coordinates to ESA SkyView.

### External Services and Partial Results

Some cross-match steps depend on external services such as GAIA, SIMBAD,
SkyBoT, and Astro-Colibri. Network errors, timeouts, or empty catalog results do
not necessarily stop the full analysis. In many cases the pipeline continues with
partial results and records warnings in the log.

## Support

If you encounter issues, check the log file. For bugs or feedback, contact `rpp_support@saf-astronomie.fr`.

