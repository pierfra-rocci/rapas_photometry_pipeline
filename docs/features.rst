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
------------------------

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
   - Configurable parameters (gain, read noise, sigma clipping thresholds)
   - Before/after comparison for quality assessment
   - Seamless integration into photometry pipeline preprocessing

**Background Estimation**:
   - photutils.Background2D with SExtractor algorithm
   - 2D background modeling with sigma clipping
   - Automatic box size adjustment for image dimensions
   - Background and RMS map visualization
   - FITS output for background and noise models

Astrometric Solutions
--------------------

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
    - Exported calibrated magnitude columns also include filter-prefixed aliases
       derived from the selected calibration band in ``GAIA_BANDS`` (for example
       ``rapasg_psf_mag`` or ``rapasg_aperture_mag_1_5``) while preserving the
       legacy column names
   - Per-aperture background-corrected and raw flux measurements
   - Signal-to-noise ratio (S/N) calculation using background-corrected flux
   - Magnitude error calculation: ``σ_mag = 1.0857 × (σ_flux / flux)``
   - Quality flags: 'good' (S/N>10), 'marginal' (5<S/N≤10), 'poor' (S/N≤5)
    - Magnitude calibration against the fitted catalog zero-point
    - Optional parallel ``*_colorcorr`` magnitude columns when a usable catalog
       color is available for the selected calibration band
   - Support for aperture correction and PSF comparison

**PSF Photometry**:
   - Empirical PSF model construction using EPSFBuilder
   - Star selection with quality filtering criteria
   - Iterative PSF fitting with IterativePSFPhotometry
   - Correct S/N calculation: ``S/N = flux_fit / flux_err``
   - PSF-specific quality flags for reliability assessment
   - PSF model visualization and FITS output
   - Comparison metrics with aperture photometry

**Error Propagation**:
   - Comprehensive photometric error calculation
   - Poisson noise plus read noise modeling
   - Background uncertainty propagation
   - Total error estimation with calc_total_error
   - Zero-point error propagation: ``σ_mag_calib = √(σ_mag_inst² + σ_zp²)``
   - Full precision S/N (no rounding) to avoid divide-by-zero

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
       baseline calibrated magnitudes
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
     candidates extracted from the science frame for rapid visual inspection

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
   - Cross-matching with field sources

Transient Detection (Beta)
-------------------------

**Transient Finder**:
   - Automated search for transient candidates
   - Image subtraction and catalog filtering
   - Integration with PanSTARRS (Northern) and SkyMapper (Southern) surveys
   - Template image retrieval and masking
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
   - Persistent parameter storage across sessions
   - Save/load configuration with single click
   - Default parameter sets for common use cases
   - Parameter validation with helpful error messages
   - Reset options for troubleshooting

**File Management**:
   - Drag-and-drop file upload interface
   - Automatic file format detection and validation
   - Temporary file handling with automatic cleanup
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
   - Isolated workspaces for data security
   - Shared configuration options when appropriate
   - Load balancing for multiple simultaneous analyses
   - Scalable backend architecture

**Quality Assurance**:
   - Comprehensive input validation
   - Automatic quality checks throughout pipeline
   - Robust error recovery mechanisms
   - Extensive testing with real astronomical data
   - Performance benchmarking and optimization
