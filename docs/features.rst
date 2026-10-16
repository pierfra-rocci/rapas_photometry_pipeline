Features
========

The sections below summarize the currently implemented user-facing and
developer-facing capabilities of RAPAS Photometry Pipeline.

User Authentication & Multi-User Support
----------------------------------------

**Secure User Management**:
   - User registration and login system via FastAPI (recommended) or legacy Flask backend
   - Password hashing with secure storage in SQLite or SQL database
   - Password recovery via email (requires SMTP configuration)
   - Automatic frontend fallback from FastAPI to the legacy backend when needed
   - Individual user workspaces with isolated data storage
   - Session management with automatic logout options

**Configuration Persistence**:
   - User-specific settings stored in backend database
   - Observatory parameters, analysis settings, and API keys
   - Automatic loading of saved configurations on login
   - Configuration persistence when the API backend is active

**Analysis History Tracking**:
   - Database tracking of all WCS-solved FITS files per user
   - Complete archive of result ZIP files with timestamps
   - Many-to-many linking between FITS files and result archives
   - Query functions to retrieve analysis history
   - Safe migration scripts for database schema updates

**Backend Modes**:
   - FastAPI backend available through ``api/main.py``
   - Legacy backend remains supported for compatibility
   - Frontend probing logic implemented in ``pages/api_client.py``
   - Backend URLs can be overridden with ``RPP_API_URL`` and ``RPP_LEGACY_URL``

Advanced Image Processing
-------------------------

**FITS File Support**:
   - Standard FITS formats (.fits, .fit, .fts, .fits.gz)
   - Multi-extension FITS (MEF) file handling
   - Data cubes with automatic 2D plane extraction
   - RGB astronomical images (uses first color channel)
   - Automatic header validation and fixing
   - Upload staging in the UI before the scientific processing phase starts

**Header Processing**:
   - Automatic WCS keyword validation and correction
   - Removal of problematic or conflicting keywords
   - Observatory information extraction from headers
   - Coordinate system standardization and validation
   - Missing keyword interpolation and defaults

**Cosmic Ray Removal**:
   - L.A.Cosmic algorithm implementation via `astroscrappy` package
   - Automatic cosmic ray detection and removal enabled by default
   - Noise model built from the *resolved* effective gain (explicit header
     `EGAIN`/`CCDGAIN`/`1/CVF` inside a plausible 0.02-100 e-/ADU range, then the
     user gain, then an empirical estimate; a bare `GAIN` is ignored on
     driver-gain cameras such as QHY/ZWO) plus `RDNOISE`/`ENOISE` when present
   - A mask covering an unphysical fraction of the frame (> 20%) is rejected
     rather than deleting a large part of the science image
   - Before/after comparison for quality assessment
   - Seamless integration into photometry pipeline preprocessing

**Background Estimation**:
   - photutils.Background2D with SExtractor algorithm
   - 2D background modeling with sigma clipping
   - Automatic box size adjustment for image dimensions
   - MAD-based per-box RMS, so the noise map is not inflated by undetected
     sources in crowded fields (the plain std over-estimated it by up to ~2x)
   - Smoothing filter capped for small background meshes to avoid over-smoothing
     the model and leaving large-scale residuals on sky gradients
   - Graceful degradation instead of a hard failure: the box-exclusion threshold
     is relaxed and the box size coarsened if ``Background2D`` would reject every
     box (crowded fields, large masked areas), with a single sigma-clipped
     background level as the last resort
   - Background and RMS map visualization
   - FITS output for background and noise models

Astrometric Solutions
---------------------

**Local Plate Solving**:
   - Astrometry.net integration via stdpipe wrapper
   - Blind astrometric solving for images without WCS
   - Automatic source detection using photutils
   - Scale estimation from focal length and pixel size
   - Solution validation and coordinate range checking
   - Forced re-solve option when the user enables Astrometry Check

**WCS Refinement**:
   - SCAMP integration for high-precision astrometry
   - GAIA DR3 catalog matching for reference stars
   - High-order polynomial distortion correction
   - Residual analysis and quality assessment
   - Optional refinement for existing WCS solutions

**Coordinate Systems**:
   - Support for multiple coordinate reference frames
   - SIP distortion correction handling
   - Coordinate transformation validation
   - Edge case handling for problematic coordinates
   - Fallback to original WCS when forced solving fails and a valid WCS already exists

Source Detection & Photometry
-----------------------------

**Source Detection**:
   - DAOStarFinder implementation with configurable parameters
   - MAD-based detection threshold, consistent with the error model and not
     inflated by undetected sources in crowded fields
   - Border masking to avoid edge artifacts
   - Quality filtering based on shape parameters (roundness, sharpness)
   - Automatic threshold optimization
   - Detection validation and source counting

**FWHM Estimation**:
   - Gaussian profile fitting on stellar marginal sums
   - Multiple source averaging for robust estimates
   - FWHM vs relative flux analysis
   - Outlier rejection with sigma clipping
   - Seeing estimation from stellar profiles

**Multi-Aperture Photometry**:
   - Two fixed aperture radii: 1.1× and 1.3× FWHM (optimized for most use cases)
   - User-configurable third aperture (FWHM Radius Factor, 0.5–2.0× FWHM, default 1.5×); values 1.1 and 1.3 collapse back to two apertures without duplication
   - All three apertures produce the same set of output columns: flux, background-corrected flux, S/N, instrumental magnitude, calibrated magnitude, magnitude error, and quality flag
    - Column naming follows ``aperture_mag_X_X`` / ``snr_X_X`` / ``aperture_mag_err_X_X`` / ``quality_flag_X_X`` (e.g. ``_1_1``, ``_1_3``, ``_1_5``)
   - When the selected calibration band defines a prefix, the exported
      calibrated magnitude and error columns use the filter-prefixed names
      (for example ``rapasg_psf_mag`` or ``rapasg_aperture_mag_1_5``) in place of
      the legacy unprefixed names, which are dropped on export; when no prefix
      applies the legacy names are exported
   - Per-aperture background-subtracted flux measurements with a robust
     sigma-clipped median annulus background (``ApertureStats``)
   - The pipeline mask (border, cosmic rays, satellite trails, NaNs) is applied
     to both the source and the annulus photometry, and the exact masked-pixel
     area is used (``area_overlap``)
   - Signal-to-noise ratio (S/N) computed from the background-subtracted flux and
     its full error, including the local-background and centroid uncertainties:
     ``σ_flux² = σ_aperture² + σ_total_bkg² + σ_centroid²``
   - Magnitude error calculated from the same flux used for the magnitude:
     ``σ_mag = 1.0857 × (σ_flux / flux)``
   - Aperture correction from the empirical-PSF encircled-energy curve (analytic
     Gaussian fallback) so aperture and PSF photometry share a total-flux scale;
     the correction factor is exported in ``apcorr_X_X`` and the underlying
     encircled-energy fraction in ``ee_fraction_X_X``
   - Quality flags: 'good' (S/N>10), 'marginal' (5<S/N≤10), 'poor' (S/N≤5)
    - Magnitude calibration against the fitted catalog zero-point
    - Optional parallel ``*_colorcorr`` magnitude columns when a usable catalog
       color is available for the selected calibration band

**PSF Photometry**:
   - Empirical PSF model construction using EPSFBuilder
   - Star selection with quality filtering criteria (flux, S/N, shape, crowding
     and local-background flatness); truly saturated pixels are removed by the
     mask beforehand, so the saturation check never discards the brightest
     unsaturated stars and no field is skipped just because too few candidates
     survived an over-aggressive filter
   - The PSF is fitted in a compact ≈3× FWHM region while the ePSF is extracted
     from a larger ≈5× FWHM cutout, so its wings are not truncated and the
     aperture-correction curve stays meaningful
   - Iterative PSF fitting with IterativePSFPhotometry
   - Per-source local-background subtraction (sigma-clipped median annulus) so
     residual sky/nebulosity does not bias the fitted PSF flux, applied both to
     the ePSF-building star cutouts and to the per-source fits
   - Correct S/N calculation: ``S/N = flux_fit / flux_err``
   - PSF-specific quality flags for reliability assessment
   - PSF model visualization and FITS output
   - **PSF-vs-aperture consistency check**: the median
     ``psf_mag − aperture_mag`` offset is reported per aperture, with a warning
     when it exceeds ``PSF_APERTURE_CONSISTENCY_THRESHOLD`` (0.15 mag) — a
     direct indicator of an ePSF shape/normalisation problem
   - Per-source difference columns ``psf_minus_aperture_mag_X_X`` (with
     filter-prefixed aliases) for every aperture, NaN where only one method
     measured the source
   - **Offset diagnostic plot** in the results view: the per-source offset
     against the calibrated PSF magnitude, with per-aperture median and zero
     lines, plus the offset histogram (saved as
     ``<base>_psf_aperture_offset.png``)

**Error Propagation**:
   - Comprehensive photometric error calculation
   - Sky (global) background noise from the MAD-based ``Background2D`` RMS map,
     plus the local annulus background subtracted per source
   - Poisson noise plus read noise modeling with ``calc_total_error``; the
     effective gain is taken from the FITS ``EGAIN``/``CCDGAIN`` keyword or the
     ``CVF`` conversion factor (``gain = 1/CVF``), then from the user-supplied
     *Detector Gain* parameter (``RPP_EFFECTIVE_GAIN`` for headless runs), then
     estimated empirically from the frame's variance-vs-signal statistics
     (photon transfer), and only then defaults to electrons (``gain = 1``). A
     conventional ``GAIN`` is accepted only when it is inside 0.02-100 e-/ADU
     and the instrument is not a driver-gain camera (QHY/ZWO), whose ``GAIN``
     is an abstract driver setting rather than a conversion factor
   - Local-background uncertainty propagation using the standard error of the
     median annulus (``ApertureStats.median_err``):
     ``σ_flux² = σ_aperture² + σ_total_bkg² + σ_centroid²``
   - Centroid-position term from re-measuring the aperture shifted by the
     centroid uncertainty (``σ_centroid = σ_PSF/S/N``), capturing the real PSF
     asymmetry (``centroid_flux_err_X_X``)
   - Zero-point and systematic-floor error propagation into every calibrated
     magnitude error:
     ``σ_mag_calib = √(σ_mag_inst² + σ_zp² + σ_sys²)``, with the zero point's
     median standard error ``σ_zp = √(π/2) × scatter/√N`` using the sigma-clipped
     (untrimmed) residual scatter, so the ±0.5 mag outlier cut does not bias it,
     and the optional systematic floor ``σ_sys`` resolved by
     ``resolve_systematic_floor()``
   - Random and correlated components exposed separately: ``*_mag_err_*_random``
     (per source) and ``*_mag_err_*_correlated`` (field-common zero-point term),
     in addition to the total error
   - Unreliable-photometry cut: a source is dropped only when every photometry
     method reports a magnitude error ≥ 2 mag (``MAG_ERR_MAX``, configurable
     through the *Max Magnitude Error* parameter, 0 disables the cut)
   - Full precision S/N and zero point (no rounding) to avoid divide-by-zero and
     to preserve the propagated uncertainty

Photometric Calibration
-----------------------

**GAIA DR3 Integration**:
   - Automatic cross-matching with GAIA Data Release 3
   - Quality filtering (variability, color, astrometric quality)
   - Multiple photometric bands including synthetic photometry
   - Cone search with configurable radius
   - Proper motion and parallax data inclusion
   - **VizieR fallback**: when the primary Gaia TAP service is unavailable, the
     query is automatically retried against the VizieR mirror with a reduced
     column set so calibration can proceed during TAP outages
   - **Match validation**: clustered matches or a large median catalog
     separation are flagged, so a bad WCS cannot silently bias the zero point

**Zero-Point Calculation**:
   - Robust calibration with sigma clipping outlier rejection
   - Sources with residuals beyond ±0.5 mag from the median excluded before the final fit
   - Aperture 1.3× FWHM used as the primary reference aperture for zero-point determination
   - Multiple aperture zero-point determination
   - Atmospheric extinction correction using airmass
   - Calibration star quality assessment
   - Zero-point uncertainty estimation and propagation
   - Correct column name lookup for error columns

**Color-Term Calibration**:
    - First-order color term fitted from the matched calibration stars after the
       scalar zero-point calibration
    - Uses simple catalog colors when available: Gaia ``BP-RP`` and Sloan-style
       ``g-r``, ``r-i``, or ``i-z`` depending on the selected calibration band
    - Writes parallel ``*_colorcorr`` magnitude columns instead of replacing the
       baseline calibrated magnitudes, together with ``*_colorcorr_err`` errors
       that combine the baseline calibrated error with the color-term fit
       uncertainty
    - Automatically skipped when the selected filter is unsupported or when the
       final catalog lacks usable color information for a source

**Magnitude Systems**:
   - GAIA G, BP, RP photometric bands
   - Johnson-Cousins UBVRI synthetic photometry
   - SDSS ugriz synthetic photometry
   - Instrumental to standard magnitude conversion
   - Multi-band calibration support

Multi-Catalog Cross-Matching
----------------------------

**GAIA DR3 Catalog**:
   - Stellar parameters, proper motions, and photometry
   - Quality filtering for reliable calibration sources
   - Synthetic photometry for non-standard bands
   - Astrometric quality indicators (RUWE)
   - Variability and color index filtering

**SIMBAD Database**:
   - Object identification and classification
   - Multiple identifier retrieval
   - Astronomical object types and properties
   - B and V magnitude data when available
   - Cross-reference with other catalogs

**Astro-Colibri Transient Alerts**:
   - Real-time transient and variable source alerts
   - Multi-messenger astronomy event correlation
   - Configurable time windows around observation date
   - API key authentication for full access
   - Event classification and discovery information
   - ``plot_astrocolibri_cutouts()``: postage-stamp cutouts of Astro-Colibri
     candidates extracted from the science frame for rapid visual inspection;
     each stamp shows the HiPS template (PanSTARRS north, SkyMapper south, with
     automatic fallback to the other survey), the circled source and its
     calibrated magnitude
   - Cross-match radius of 10 arcsec (nearest event per source); the match
     outcome is displayed in the app

**Solar System Objects (SkyBoT)**:
   - Moving object identification and ephemeris
   - Asteroid and comet detection
   - Magnitude predictions and orbital elements
   - Time-dependent position calculations
   - Integration with observation timestamps
   - Graceful handling of timeout or network failures during catalog access

**Variable Star Catalog (AAVSO VSX)**:
   - Variable star identification and classification
   - Period information when available
   - Variable star types and characteristics
   - Cross-matching with detected sources
   - Integration with photometric analysis

**Quasar Catalog (Milliquas)**:
   - Quasar and AGN identification
   - Redshift information when available
   - R-band magnitude data
   - Large-scale structure studies support
   - Cross-matching with point sources

**Nearby Stars (10 Parsec Catalog)**:
   - Identification of stars within 10 parsecs
   - G, BP, RP magnitude data
   - Object type classification
   - Available proper motions propagated to the observation epoch
   - Cross-matching with field sources

Transient Detection (Beta)
--------------------------

**Transient Finder**:
   - Automated search for transient candidates
   - Reference-survey and catalog filtering, with integration with PanSTARRS
     (Northern) and SkyMapper (Southern) surveys
   - SExtractor detection (via stdpipe) with flags used to discard unreliable
     detections
   - Seeing-based cross-matching with the pipeline photometry catalog (nearest
     counterpart within the FWHM, clamped to 1-4 arcsec)
   - Brightness-excess test against catalog counterparts (default 2.0 mag
     brighter, ``mag_threshold``)
   - Template (HiPS) image retrieval and masking
   - No minimum S/N cutoff is applied to transient candidates; low-S/N
     detections remain eligible if they pass the other filtering steps
   - Candidate visualization with cutouts (Science, Template) for the top 20
     candidates sorted by best signal-to-noise ratio
   - Additional SkyBoT filtering for Solar System object rejection

Data Visualization & Analysis
-----------------------------

**Image Display**:
   - Dual visualization modes (ZScale and histogram equalization)
   - Automatic contrast optimization
   - Color-mapped display with customizable scales
   - Interactive zoom and pan capabilities
   - Multi-panel layout for comparison

**Interactive Sky Viewer**:
   - Aladin Lite integration for source exploration
   - Overlay of detected sources with catalog information
   - Clickable source markers with detailed information
   - Survey selection and coordinate display
   - Export to external sky viewers (ESA Sky)

**Photometric Plots**:
   - FWHM analysis with scatter plots and statistics
   - Magnitude distribution histograms
   - Zero-point calibration plots with residuals
   - Background model visualization
   - PSF model display and analysis

**Source Catalogs**:
   - Comprehensive tabular display of all measurements
   - Multi-aperture photometry results
   - Cross-match results from all catalogs
   - Coordinate information and quality indicators
   - Export capabilities in multiple formats

User Interface & Workflow
-------------------------

**Streamlit Web Interface**:
   - Modern, responsive design accessible via web browser
   - Real-time processing progress indicators
   - Interactive parameter adjustment with immediate feedback
   - Organized sidebar with collapsible sections
   - Mobile-friendly responsive layout
   - Dedicated Start Analysis action after FITS upload and parameter review

**Statistics Display**:
   - Target RA and DEC shown in both decimal degrees and sexagesimal format
     (HH:MM:SS / ±DD:MM:SS) in the Statistics section
   - Dual coordinate format also recorded in the processing log file

**Photometric Visualization**:
   - "Magnitude Error vs Magnitude" scatter plot uses a logarithmic Y-axis for
     better readability across a wide dynamic range of photometric precision

**Configuration Management**:
   - Persistent parameter storage across sessions on both the FastAPI and
     legacy Flask backends
   - Save/load configuration with a single click, including the *Max Magnitude
     Error* and *Detector Gain* controls
   - Default parameter sets for common use cases (missing keys are seeded from
     ``default_analysis_params``)
   - Parameter validation with helpful error messages
   - Reset options for troubleshooting (session-local, effective in a single press)

**File Management**:
   - Drag-and-drop file upload interface
   - Automatic file format detection and validation
   - Temporary file handling with automatic cleanup
   - Per-session ``work_<id>`` folders for temporary uploads and intermediate results
   - Archive browser for previous results
   - Batch download options for result sets
   - User-scoped storage paths under ``rpp_data/fits/`` and ``rpp_results/``

**Error Handling**:
   - Comprehensive error messages with recovery suggestions
   - Graceful degradation when optional components fail
   - Automatic fallback options for common failure modes
   - Detailed logging for troubleshooting
   - User-friendly error reporting

Developer Utilities and Scripts
-------------------------------

**Database Migration Utilities**:
   - ``scripts/migrate_add_wcs_zip_tables.py`` for 1.6.0 tracking tables
   - ``scripts/migrate_legacy_db.py`` for legacy SQLite migration to the API schema

**Standalone Processing Utilities**:
   - ``scripts/satellite_trail_detector.py`` for experimental satellite trail masking
   - Script utilities are documented as separate helpers, not as integrated GUI features

Output Generation & Export
--------------------------

**Comprehensive Result Files**:
   - CSV catalogs with complete photometric measurements
   - FITS files for background and PSF models
   - WCS-solved FITS files with refined astrometric headers
   - PNG plots for all analysis visualizations
   - Text files for headers and processing logs
   - ZIP archives for convenient result distribution

**Data Organization**:
   - User-specific result directories (``rpp_results/``)
   - Dedicated FITS storage with WCS solutions (``rpp_data/fits/``)
   - Timestamped file naming for version control
   - Automatic file categorization by type
   - Metadata preservation in output files
   - Cross-referenced file relationships

**Archive Management**:
   - Automatic result archiving with compression
   - Old file cleanup with configurable retention
   - Automatic removal of stale session folders and temporary uploads older than 24 hours (archived ZIP files are kept)
   - Browse interface for historical results
   - Bulk download options for research workflows
   - Secure multi-user data isolation

Performance & Scalability
-------------------------

**Efficient Processing**:
   - Streamlit caching for improved response times
   - Memory-efficient handling of large FITS files
   - Optimized algorithms for typical image sizes
   - Background processing for time-consuming operations
   - Resource monitoring and management

**Multi-User Support**:
   - Concurrent user session handling
   - Isolated workspaces for data security: each browser session, even of the same account, has its own work folders
   - Reset only affects the current session, never other connected users
   - Shared configuration options when appropriate
   - Load balancing for multiple simultaneous analyses
   - Scalable backend architecture

**Quality Assurance**:
   - Comprehensive input validation
   - Automatic quality checks throughout pipeline
   - Robust error recovery mechanisms
   - Extensive testing with real astronomical data
   - Performance benchmarking and optimization
