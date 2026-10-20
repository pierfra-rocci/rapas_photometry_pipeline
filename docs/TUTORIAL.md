
# Step-by-Step Photometry Tutorial

This tutorial guides you through a typical analysis session.

Use a science FITS image with numeric image data. The loader accepts supported
FITS numeric types and extracts the first 2D plane from a data cube; inspect the
selected plane before starting analysis when processing a cube.

## 1. Upload a FITS File

In the main area, use the file uploader to select your FITS image. Supported extensions: `.fits`, `.fit`, `.fts`, `.fits.gz`, `.fts.gz`.

Uploading the file stages it in the interface and immediately shows a raw
header preview so you can verify metadata. Full FITS loading and analysis start
only after you click the Start Analysis button.

> **Note**: Each browser session is isolated from the others. If a wrong file or header is shown, press **Reset** once: it clears only your own session and drops the previously loaded file immediately.

## 2. Configure Observatory

In the sidebar, set your observatory details:

- **Name**: e.g., "Backyard Observatory"
- **Latitude, Longitude, Elevation**: Decimal degrees/meters. These may be auto-filled from the FITS header, but always verify.

## 3. Set Analysis Parameters

In the sidebar, adjust:

- **Estimated FWHM**: Initial guess in arcseconds
- **Detection Threshold**: Sigma threshold for source detection
- **FWHM Radius Factor**: Multiplier for the user-defined aperture radius (0.5 – 2.0). Values 1.1 and 1.3 are reserved for the two fixed apertures and cannot be selected without a warning.
- **Pixel scale override (arcsec/pixel)**: Leave at `0` (automatic) when the FITS header or WCS provides the pixel scale. If the scale cannot be determined, the run stops with a message; enter the value here (e.g. `0.75`) and start the analysis again. No default scale is assumed.
- **Border Mask**: Pixels to exclude at the image edge
- **Max Magnitude Error**: Last-resort cut on the calibrated magnitude error. A source is dropped only when *every* photometry method reports an error at or above this value; set it to 0 to disable the cut entirely
- **Detector Gain**: Effective gain in e-/ADU used for the Poisson term. A valid FITS `EGAIN`/`CCDGAIN` value, or the `CVF` conversion factor (`gain = 1/CVF`), takes precedence; a bare `GAIN` is used only when it looks like a genuine conversion factor (0.02–100 e-/ADU and not a QHY/ZWO driver-gain camera, where `GAIN` is an abstract setting). When none is available, a nonzero configured gain is used. At 0, the pipeline estimates the gain from the frame and falls back to 1 e-/ADU if it cannot make a reliable estimate. The Poisson term is only added when the resolved gain matches the units of the data: if it would predict a sky noise far above the measured background RMS (for example on a normalised float stack whose sky level is ~0.01), the gain is re-derived from the sky level and that RMS (`gain = sky level / RMS²`) and an informational message reports the rescaling. A frame that is already sky-subtracted cannot be checked this way, so supply the gain in the data's units (or a frame in counts) in that case.
- **Calibration Filter Band**: Choose the photometric band for calibration (e.g. g, r, i)
- **Astrometry Check**: Enable to force plate solving/WCS refinement

For an optional online fallback, configure `ASTROMETRY_API_KEY` in the
environment before starting Streamlit, or add `api_key` under
`[astrometry_net]` in `~/.astropy/config/astroquery.cfg`. In WSL, you can
enter the key without adding it to shell history:

```bash
read -s -p "Astrometry.net API key: " ASTROMETRY_API_KEY
echo
export ASTROMETRY_API_KEY
```

The app tries the local solver first. The online fallback runs only after a
local solve fails and submits the detected source list, not the FITS image.
Without a configured key, the online fallback is skipped.

*Cosmic ray removal is always performed automatically using the L.A.Cosmic algorithm (astroscrappy).*

## 4. (Optional) Astro-Colibri API Key

Enter your Astro-Colibri API key in the sidebar to enable transient and variable source cross-matching. After the cross-match, the app reports how many pipeline sources matched an Astro-Colibri event (progressive radius 5, 10, 15 then 20 arcsec; a warning is displayed when a match is only found at 15 or 20 arcsec), that none matched, or that the service was unavailable. The sources returned by Astro-Colibri for the field are listed in a table (name, RA/Dec, type, classification, observatory, trigger ID, position error, date, redshift, matched or not, separation), and the event coordinates of matched sources are exported as `astrocolibri_ra` and `astrocolibri_dec`. In the Aladin Lite viewer, cross-matched sources are drawn as larger red squares (layer "Astro-Colibri matches") so they stand out immediately. For each match, postage-stamp cutouts (science frame and HiPS template, PanSTARRS in the north, SkyMapper in the south with automatic fallback to the other survey) show the source with its calibrated magnitude.

## 5. (Optional) Transient Candidates

Expand the "Transient Candidates" section in the sidebar:

- **Enable Transient Finder**: Activates the transient search, which compares detected sources against reference surveys (PanSTARRS1 for north, SkyMapper for south) and rejects known catalog and Solar System objects. Sources are detected directly at 1.5 sigma to favour faint objects, and the candidates are shown as yellow stars in the Aladin viewer for visual checking.
- **Reference Filter**: Select the filter band for the reference comparison and the HiPS template cutouts.
- Detections are cross-matched with the reference catalogs within half the seeing FWHM, and a source coincident with a catalog entry is kept only if it is more than 2 magnitudes brighter (a genuine brightness excess). Sources are detected with SExtractor, and unreliable detections (edge-truncated, deblended, bad aperture) are discarded. If the photometry table has no usable PSF magnitudes, the finder falls back to the calibrated 1.1 x FWHM aperture magnitude instead of returning no candidates.
- The transient finder applies no minimum S/N cutoff. Low-S/N detections remain
  eligible if they pass the other catalog, known-source, and Solar System object
  filters.

## 6. Run the Analysis

Click **Start Analysis** to begin processing. At that point the app loads
the FITS file, checks the header and WCS, and then runs the pipeline.

The following steps are performed:

1. **Background & Noise Estimation**
2. **Source Detection & Cosmic Ray Removal**
3. **Photometry**: Multi-aperture (up to 3 apertures) and PSF photometry with a robust local background, aperture correction, S/N and error calculation, and quality-flag assignment
4. **Astrometric Refinement** (if enabled)
5. **Photometric Calibration**: Cross-match with catalogs for zero-point and, when possible, a simple color-term correction
6. **Multi-Catalog Cross-Matching**: GAIA DR3, SIMBAD, SkyBoT, AAVSO VSX, Milliquas, 10 Parsec, Astro-Colibri. Available 10 Parsec proper motions are propagated to the observation epoch.
7. **Transient Detection** (if enabled)

If **Astrometry Check** is enabled, the app forces a new plate-solving attempt
even when a valid WCS is already present. If local solving fails and an online
key is configured, the app tries Astrometry.net online. If both attempts fail,
it tries to restore and continue with the original WCS when possible.

The plate solver aims for at least 20 detected stars. When fewer are found (small
or sparse field), the detection threshold is lowered from 3 to 2 and then 1 sigma;
if the solve still fails, it is retried with the deeper detection before falling
back to the online solver.

## 7. Download and Interpret Results

After processing, download the ZIP archive containing the catalog, text headers,
processing log, and generated PNG diagnostics. The archive excludes all FITS
files.

- `*_catalog.csv` / `.vot`: Source catalog restricted to a curated set of columns, in this order: `id`, `ra`, `dec`, `astrocolibri_name`, `astrocolibri_type`, `astrocolibri_classification`, `astrocolibri_ra`, `astrocolibri_dec`, `simbad_main_id`, `simbad_otype`, `simbad_B`, `simbad_V`, `skybot_NAME`, `skybot_OBJECT_TYPE`, `skybot_MAGV`, `aavso_Name`, `aavso_Type`, `aavso_Period`, `catalog_matches`, `x_center`, `y_center`, then `snr_X_X`, the aperture magnitudes `aperture_mag_X_X` (each followed by its `quality_flag_X_X`), the PSF magnitude `psf_mag`, the errors `aperture_mag_err_X_X` and `psf_mag_err`, and the offsets `psf_minus_aperture_mag_X_X`. The apertures are the fixed `_1_1` and `_1_3` plus the user-defined one (for example `_1_5` at FWHM Radius Factor 1.5), so the last suffix follows your choice. Columns that do not exist for a run (for example without an Astro-Colibri key) are omitted.

  `id` is a unique integer source number; it is the same ID displayed in the Aladin Lite popups, so you can find any source of the viewer in the files. If Astro-Colibri sources are detected, their rows are sorted by `astrocolibri_name` and appear at the very top of the CSV and VOT files.

  When a filter prefix applies, magnitudes and errors use prefixed names such as `rapasg_psf_mag` and `rapasg_aperture_mag_err_1_5` (the legacy unprefixed names are used otherwise). Magnitude errors combine the per-source random part and the field-common correlated part (zero-point uncertainty and systematic floor). Colour-corrected magnitudes (`*_colorcorr`), aperture-correction factors, background RMS and the other internal diagnostics are used during the run but are not written to these files.
- `*_transients.csv`: Transient candidates (when the transient finder is enabled), with the same columns as the catalog, including `id`, so each candidate can be traced back to its catalog row. Candidates without a catalog counterpart keep their own coordinates, pixel position and calibrated PSF magnitude.
- `*_header.txt`: Input FITS header
- `*_wcs_header.txt`: Astrometric solution header, when a WCS is available
- `*.log`: Processing log
- `*.png`: Diagnostic plots

**FITS products (not included in the ZIP)**: The empirical PSF model, when
generated, is saved as `*_psf.fits` in the per-user results directory. When
astrometry succeeds, the pipeline saves the original image with the updated WCS
as `*_wcs.fits` in the results directory and also in `rpp_data/fits/`:

1. **Results directory**: The per-user results folder; the file is excluded from the downloadable ZIP.
2. **`rpp_data/fits/`**: A persistent copy that is overwritten if the same file is processed again.

**Analysis Tracking**: The database tracks selected analysis artifacts and their links; it does not store the photometry rows themselves:

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

The flux error used for the S/N combines the aperture error, the uncertainty of
the local background measured in the annulus (``σ_aperture`` and ``σ_total_bkg``),
and the flux change induced by the uncertainty in the source centroid
(``σ_centroid``):

- **Flux error**: σ_flux² = σ_aperture² + σ_total_bkg² + σ_centroid², where the
  local-background term uses the large-sample median standard error of the
  annulus (``ApertureStats.median_err``) and the centroid term propagates the
  position uncertainty of the source
- **Instrumental error**: σ_mag = 1.0857 × (σ_flux / flux), computed from the same
  background-subtracted, aperture-corrected flux as the magnitude itself
- **Calibrated error**: σ_mag_calib = √(σ_mag_inst² + σ_zp² + σ_sys²), where σ_zp is
  the formal uncertainty of the median zero point, calculated as 1.2533 × the
  untrimmed calibration scatter / √N, and σ_sys is the systematic error floor.
  The formal zero-point uncertainty is written to the `zero_point_std` column; the
  untrimmed and retained calibrator scatters (`zero_point_scatter`,
  `zero_point_scatter_kept`) and the calibrator count (`zero_point_n`) are
  recorded on the matched calibration table rather than the exported source
  catalog

Aperture magnitudes are corrected for the fraction of the PSF that falls outside
the aperture using the empirical-PSF encircled-energy curve (analytic Gaussian
fallback), so the aperture and PSF magnitudes share the same total-flux scale. The
background subtraction and the least-squares PSF fit both use a robust local
background estimated on an annulus around each source.

After photometry the pipeline also reports the median `psf_mag - aperture_mag`
offset per aperture and warns when it exceeds 0.15 mag, so a disagreement
between the two methods (for example an ePSF shape or normalisation problem) is
visible directly in the run. The per-source offset is exported as
`psf_minus_aperture_mag_X_X` columns (NaN for sources measured by only one
method), and the results view plots it against the calibrated PSF magnitude
(with per-aperture median and zero lines) alongside its histogram.

**Interactive Analysis**: Use the embedded Aladin Lite viewer for real-time exploration, or export coordinates to ESA SkyView.

### External Services and Partial Results

Some cross-match steps depend on external services such as GAIA, SIMBAD,
SkyBoT, and Astro-Colibri. Network errors, timeouts, or empty catalog results do
not necessarily stop the full analysis. In many cases the pipeline continues with
partial results and records warnings in the log. The same applies when SkyBoT
returns a malformed or unreadable response payload.

## Support

If you encounter issues, check the log file. For bugs or feedback, contact `rpp_support@saf-astronomie.fr`.

