Changelog
=========

This document records all notable changes to RAPAS Photometry Pipeline.

Version 1.7.5
-------------

**Released: May 19, 2026**

**Photometric Calibration**

*   **Gaia VizieR fallback**: When the Gaia TAP service is unavailable or
    returns an error, the pipeline now automatically retries the query against
    the VizieR mirror with a reduced column set, keeping photometric calibration
    available during TAP outages.
*   **Zero-point calibration improvements**:

    *   Sigma clipping is applied before the final zero-point fit.
    *   Sources with residuals beyond ±0.5 mag from the median are excluded
        before the fit.
    *   Aperture 1.3× FWHM is used as the primary reference aperture for
        zero-point determination.

**New Functions**

*   **``plot_astrocolibri_cutouts()``**: Generates postage-stamp cutout images
    for Astro-Colibri transient candidates directly from the science frame,
    enabling rapid visual inspection alongside catalog alerts.
*   **``drop_legacy_magnitude_columns()``**: Utility to strip the legacy
    (non-prefixed) magnitude columns from the output catalog when only
    filter-prefixed aliases are needed.

**Robustness & Testing**

*   **Backend detection improvements**: Enhanced probing and fallback logic for
    FastAPI / legacy backend auto-detection; new ``tests/test_backend_detection.py``
    covers probe and fallback scenarios.
*   **Graceful stdpipe import handling**: ``ImportError`` from optional stdpipe
    components is caught and reported cleanly rather than crashing the module load.

**Infrastructure**

*   **Gunicorn UvicornWorker by default**: ``gunicorn.conf.py`` now sets
    ``uvicorn.workers.UvicornWorker``, preventing WSGI/ASGI mismatches.
*   **Dependency cleanup**: Removed unused ``mpmath`` dependency; tightened
    ``setuptools`` version constraints for reproducible builds.

**UI & Logging**

*   **Consistent Streamlit message levels**: Messages use success / warning /
    info / error severity throughout the pipeline.
*   **Reduced log noise**: Verbose debug messages removed from the hot path.
*   **FITS excluded from download list**: ``.fits`` files are no longer shown
    among the per-analysis download buttons.
*   **FWHM sampling**: Source sample size increased to 1 250 for better seeing
    measurement stability.

**Documentation**

*   New FastAPI + Gunicorn troubleshooting section added to
    ``docs/troubleshooting.rst``.

Version 1.7.3
-------------

**Released: April 21, 2026**

*   **Photometry catalog aliases**: Exported calibrated magnitude columns now
    also include filter-prefixed aliases derived from the selected calibration
    band in ``GAIA_BANDS`` (for example ``rapasg_psf_mag``), while preserving
    the legacy column names for compatibility.

Version 1.7.2
-------------

**Released: April 20, 2026**

**User Interface Improvements**

*   **Sexagesimal Coordinate Display**: Target RA and DEC in the Statistics
    section are now shown in both decimal degrees and sexagesimal format
    (HH:MM:SS / ±DD:MM:SS). The same dual format is recorded in the processing
    log.
*   **Magnitude Error Plot — Logarithmic Scale**: The Y-axis of the "Magnitude
    Error vs Magnitude" scatter plot is now displayed on a logarithmic scale,
    improving readability across a wide dynamic range of photometric precision.

Version 1.7.0
-------------

**Released: 2026**

**Application Workflow & Stability**

*   **Explicit Start Gate**: Uploading a FITS file now stages it in the Streamlit UI without immediately triggering FITS loading, WCS checks, or photometry. Scientific processing starts only after clicking **Start Analysis**.
*   **Astrometry Toggle Fix**: The frontend now reads the astrometry option from the persisted analysis settings, so the sidebar toggle consistently controls forced astrometry checks.
*   **Safer Astrometry Fallback**: The upload/start workflow no longer passes an unset temporary file path into the astrometry step during the pre-start phase.

**Catalog & Network Handling**

*   **SkyBoT Timeout Adjustment**: SkyBoT queries in both catalog enhancement and transient filtering now use a 120-second timeout instead of the previous 300-second default.
*   **Graceful Remote-Service Behavior**: User-facing documentation and workflow descriptions were aligned with the existing behavior of continuing with partial results when optional remote services are slow, empty, or unavailable.

**Packaging & Environment**

*   **Missing Dependency Fix**: Added ``email-validator`` to project dependencies so FastAPI schema imports using ``EmailStr`` work in a clean environment.
*   **Local Editable Install**: Updated ``requirements.txt`` to install the local checkout with ``-e .`` instead of pointing back to a remote repository URL.
*   **Frontend Entry Point**: Added ``run_frontend.py`` so the configured ``pfr`` script entry point resolves correctly.
*   **Tooling Target Update**: Updated Black configuration to target Python 3.12.

**Documentation Refresh**

*   Updated ``README.md`` and ``docs/TUTORIAL.md`` to reflect the staged upload workflow, backend auto-detection, account features, maintenance scripts, astrometry fallback behavior, and partial-result handling.
*   Updated the main Sphinx pages including ``installation.rst``, ``usage.rst``, ``troubleshooting.rst``, ``features.rst``, ``examples.rst``, ``api_reference.rst``, ``user_guide.rst``, ``advanced_features.rst``, ``api.rst``, and ``index.rst``.
*   Replaced outdated terminology such as old start-button labels and stale API/module references with the current code paths and workflow names.

Version 1.6.0 (Current)
-----------------------

**Released: January 9, 2026**

**Storage & Data Organization**

*   **New FITS Storage Location**: Moved FITS file storage from ``data/fits/`` (inside project) to ``rpp_data/fits/`` (at parent level, same as ``rpp_results/``)
*   **WCS-Solved FITS Export**: New utility function ``save_fits_with_wcs()`` to save original FITS images with updated WCS headers after astrometric solving
*   **Consistent Directory Structure**: Both results (``rpp_results/``) and data files (``rpp_data/fits/``) are now stored at the same directory level outside the project folder

**Database Tracking System**

*   **New Database Tables**: Added three new tables for tracking analysis results:
    
    - ``wcs_fits_files``: Tracks WCS-solved FITS files stored in ``rpp_data/fits/``
    - ``zip_archives``: Tracks result ZIP archives stored in ``rpp_results/``
    - ``wcs_fits_zip_assoc``: Junction table linking FITS files to their associated ZIP archives (many-to-many relationship)

*   **Analysis History**: Each user can now view their complete analysis history including which FITS files generated which result archives
*   **Migration Script**: New script ``scripts/migrate_add_wcs_zip_tables.py`` safely adds new tables to existing databases without data loss
*   **Database Helper Module**: New ``src/db_tracking.py`` module provides functions for recording and querying analysis results

**API Improvements**

*   Updated ``FITS_STORAGE_ROOT`` configuration to use the new storage path
*   Added ``PARENT_DIR`` export for consistent path management across the application
*   New Pydantic schemas for WCS FITS files and ZIP archive API responses
*   New ORM models with proper relationships and constraints

**Quality Improvements**

*   Magnitude error threshold reduced from 2.0 to 1.5 for stricter quality filtering
*   Added absolute value handling for occasional negative magnitude error values

**Testing**

*   New comprehensive test suite ``tests/test_database.py`` with 35 tests covering:
    
    - ORM model creation and relationships
    - Pydantic schema validation
    - Database tracking functions
    - Migration script functionality
    - Integration workflows

Version 1.5.3
-------------

**Released: January 7, 2026**

**Photometry Calculation Improvements**

*   **Critical Bug Fix**: Fixed PSF S/N calculation that was incorrectly using ``sqrt(flux_err)`` instead of ``flux_err``. This was causing incorrect S/N values and magnitude errors for all PSF photometry.
*   **Precision Enhancement**: Removed S/N rounding to preserve full floating-point precision and avoid divide-by-zero errors in magnitude error calculations.
*   **Improved S/N Accuracy**: S/N now uses background-corrected flux (``aperture_sum_bkg_corr``) when available, providing more accurate estimates especially for faint sources.
*   **Error Propagation**: Added proper zero-point error propagation in calibrated magnitudes using quadrature: ``σ_mag_calib = √(σ_mag_inst² + σ_zp²)``
*   **Quality Flags**: Added photometric quality flags for both aperture and PSF photometry:
    
    - ``'good'``: S/N > 10 (reliable photometry)
    - ``'marginal'``: 5 < S/N ≤ 10 (use with caution)
    - ``'poor'``: S/N ≤ 5 (unreliable)
    - ``'unknown'``: missing data

**Documentation & Testing**

*   Comprehensive mathematical documentation of S/N and magnitude error formulas
*   New documentation file: ``docs/PHOTOMETRY_FORMULAS.md``
*   Complete unit test suite: ``tests/test_photometry.py``
*   Inline formula documentation in source code

**Technical Details**

*   S/N Formula: ``S/N = flux / flux_error``
*   Magnitude Error Formula: ``σ_mag = 1.0857 × (σ_flux / flux)`` where 1.0857 = 2.5/ln(10)
*   Calibrated Error: ``σ_mag_calib = √(σ_mag_inst² + σ_zp²)``

Version 1.5.1
-------------

**Released: January 3, 2026**

**Current Status**
*   All features from version 1.4.7 maintained and stabilized
*   Python 3.12 compatibility confirmed
*   All dependencies verified and up-to-date
*   Production-ready release with comprehensive testing

Version 1.4.7
-------------

**Released: December 30, 2025**

**Python 3.12 Upgrade & Dependency Management**
*   Upgraded to Python 3.12 with verified dependency resolution
*   Updated astropy to 7.1.1 with latest IERS data (0.2025.11.3.0.38.37)
*   Updated aiohttp to 3.11.18 with latest security patches
*   Updated certifi to 2025.1.31 for current CA certificate bundle
*   All dependencies verified compatible with Python 3.12

**Maintenance & Stability**
*   Comprehensive dependency audit and updates
*   Improved compatibility with latest Python ecosystem
*   Enhanced system library integration

Version 1.4.5
-------------

**Major Features**

*   **Advanced Astrometric Pipeline**: 
    *   Integrated `stdpipe` for robust local astrometric solving
    *   Added WCS refinement using SCAMP (via stdpipe) and GAIA DR3 catalog
    *   Automatic header validation and fixing for problematic WCS keywords

*   **Transient Detection (Beta)**:
    *   New **Transient Finder** module utilizing image subtraction and catalog filtering
    *   Integration with PanSTARRS and SkyMapper surveys for template comparison
    *   Automatic candidate filtering against cataloged sources
    *   Hemisphere-based automatic survey selection (PanSTARRS north, SkyMapper south)

*   **Enhanced Cross-Matching**:
    *   Added **10 Parsec Catalog** for nearby star identification
    *   Improved filtering for existing catalogs
    *   Multi-band photometric support (GAIA G, BP, RP)

*   **Photometry Improvements**:
    *   Improved PSF Photometry with Gaussian fallback if EPSF building fails
    *   Automatic Cosmic Ray rejection enabled by default using `astroscrappy` (L.A.Cosmic algorithm)
    *   Multi-aperture photometry with 1.5×, 2.0× FWHM radii

*   **User Interface**:
    *   Astrometry check toggle for automatic/manual plate solving
    *   Aladin Lite v3 integration for improved interactive visualization
    *   Enhanced progress tracking and status updates

Version 0.9.8
-----------------------

**Major Features & Enhancements**

*   **Advanced Astrometric Pipeline**: 
    *   Integrated stdpipe for robust local plate solving with Astrometry.net
    *   Added WCS refinement using SCAMP and GAIA DR3 catalog
    *   Automatic header validation and fixing for problematic WCS keywords
    *   Force re-solve option for overriding existing WCS solutions
    *   Comprehensive coordinate validation and transformation checking

*   **Multi-Aperture Photometry**:
    *   Implemented four aperture radii (1.5×, 2.0×, 2.5×, 3.0× FWHM)
    *   Local background estimation with annular apertures for each radius
    *   Background-corrected and raw flux measurements
    *   Signal-to-noise ratio calculation for all apertures
    *   Backward compatibility with legacy single-aperture columns

*   **Enhanced PSF Photometry**:
    *   Empirical PSF model construction using EPSFBuilder
    *   Advanced star selection with quality filtering criteria
    *   Iterative PSF fitting with IterativePSFPhotometry
    *   PSF model visualization and FITS output
    *   Integration with multi-aperture photometry workflow

*   **Comprehensive Catalog Cross-Matching**:
    *   GAIA DR3 with quality filtering (variability, color, astrometry)
    *   SIMBAD for object identification and classification
    *   Astro-Colibri API for transient event correlation
    *   SkyBoT for solar system object identification
    *   AAVSO VSX for variable star catalog matching
    *   VizieR Milliquas for quasar and AGN identification

*   **Interactive Visualization**:
    *   Aladin Lite integration for interactive sky viewing
    *   Source overlay with detailed popup information
    *   Multiple survey options and coordinate display
    *   ESA Sky external viewer integration
    *   Real-time catalog cross-match results display

*   **Robust Error Handling**:
    *   Comprehensive error recovery mechanisms
    *   Graceful degradation when optional components fail
    *   Detailed error messages with recovery suggestions
    *   Automatic fallback options for common failure modes
    *   Enhanced logging with severity levels and timestamps

**Image Processing Improvements**:
    *   Advanced FITS header validation and automatic fixing
    *   Support for multi-extension FITS and data cubes
    *   RGB astronomical image handling (first channel extraction)
    *   Cosmic ray removal integration with L.A.Cosmic algorithm
    *   Enhanced background modeling with visualization and FITS output

**User Interface Enhancements**:
    *   Streamlined sidebar organization with collapsible sections
    *   Real-time parameter validation with helpful error messages
    *   Improved file upload handling with format detection
    *   Enhanced progress indicators and status updates
    *   Mobile-friendly responsive design improvements

**Data Management**:
    *   Automatic result archiving with timestamped ZIP files
    *   User-specific workspace isolation and management
    *   Archive browser for accessing previous results
    *   Automatic cleanup of old files (30+ day retention)
    *   Comprehensive output file generation and organization

**Performance Optimizations**:
    *   Streamlit caching for improved response times
    *   Memory-efficient handling of large FITS files
    *   Optimized algorithms for typical astronomical image sizes
    *   Background processing for time-consuming operations
    *   Resource monitoring and management improvements

**Configuration Management**:
    *   Enhanced parameter persistence across user sessions
    *   Observatory information extraction from FITS headers
    *   API key management with secure storage
    *   Default parameter sets for common use cases
    *   Configuration validation and error checking

**Minor Enhancements & Bug Fixes**

*   Improved coordinate system handling with multiple keyword support
*   Enhanced pixel scale calculation with multiple methods
*   Better FWHM estimation with outlier rejection
*   Robust airmass calculation with multiple date format support
*   Enhanced zero-point calculation with atmospheric extinction correction
*   Improved catalog query error handling and retry mechanisms
*   Better temporary file management and cleanup
*   Enhanced logging with structured format and severity levels

**Documentation Updates**:
    *   Comprehensive API reference documentation
    *   Updated installation guide with current dependencies
    *   Enhanced user guide with step-by-step workflows
    *   Feature documentation with detailed descriptions
    *   Troubleshooting guide with common issues and solutions

Version 1.0.0 (2023-09-01) - Previous Release
--------------------------------------------

**Features**:
- FITS image calibration (bias, dark, flat)
- Basic plate solving with astrometry.net
- Source detection and aperture photometry
- Zero-point calibration with Gaia DR3
- CSV catalog export
- Image visualization with matplotlib
- User-friendly Streamlit interface

**Requirements**:
- Python 3.8+
- Core packages: astropy, photutils, astroquery, streamlit, numpy, pandas, matplotlib

Version 0.9.0 (2023-07-15) - Beta Release
-----------------------------------------

**Initial Beta Features**:
- Core functionality implemented
- Basic calibration and photometry workflow
- Preliminary documentation
- Single-user desktop application

**Technical Foundation**:
- photutils-based photometry pipeline
- Basic FITS loading and display
- Simple aperture photometry
- Gaia DR3 cross-matching
- Basic CSV export functionality

Development Roadmap
------------------

**Planned Features for v1.0**:
- Enhanced PSF modeling with multiple PSF types
- Advanced astrometric distortion correction
- Batch processing capabilities for image sequences
- Machine learning-based source classification
- Advanced visualization tools and interactive plots
- Integration with additional astronomical catalogs
- Performance optimizations for large surveys
- Enhanced documentation and tutorials

**Future Enhancements**:
- Multi-band photometry across different filters
- Time-series analysis for variable source monitoring
- Advanced statistical analysis tools
- Integration with observatory control systems
- Cloud-based processing and storage options
- Advanced machine learning features
- Professional data reduction pipeline integration

**Known Issues & Limitations**:
- SCAMP refinement requires proper installation and configuration
- Large images (>8K×8K) may require increased memory allocation
- Some catalog services may have temporary availability issues
- Complex WCS solutions with high-order distortions may need manual validation
- Network-dependent features require stable internet connection

**Migration Notes**:
- Configuration files from previous versions are automatically migrated
- Output file formats are backward compatible
- API changes are minimal and documented in the API reference
- Legacy single-aperture photometry columns are maintained for compatibility
