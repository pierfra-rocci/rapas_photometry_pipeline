API Reference
=============

This section summarizes the main Python modules and HTTP endpoints that make up
RAPAS Photometry Pipeline. The project is primarily used through the Streamlit
frontend, but several modules and backend routes are also useful for
programmatic access.

Application Modules
-------------------

Frontend and UI
^^^^^^^^^^^^^^^

``pages/app.py``
   Main Streamlit workflow for FITS upload, parameter selection, astrometry,
   photometry, cross-matching, result export, and transient finding.

``pages/login.py``
   Login, registration, password recovery, and backend selection workflow. Every
   successful sign-in is written to the Streamlit log as
   ``[YYYY-MM-DD HH:MM:SS] RPP vX.Y.Z - User '<username>' logged in
   successfully (frontend, <backend> backend).``

``pages/api_client.py``
   Thin HTTP client used by the Streamlit frontend to talk to the FastAPI
   backend. Includes backend auto-detection logic.

Science and Processing Modules
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``src/pipeline.py``
   Core photometry helpers including masking, cosmic-ray handling, and other
   lower-level processing helpers. It also owns the effective-gain resolution
   (``resolve_effective_gain`` / ``instrument_uses_driver_gain``) shared by the
   error model, the cosmic-ray mask and SEP detection: explicit ``EGAIN``,
   ``CCDGAIN`` and ``CVF`` (``gain = 1/CVF``) keywords take precedence over the
   conventional ``GAIN``, which is ignored on recognised driver-gain cameras
   (QHY/ZWO) because it is an abstract driver setting rather than e-/ADU.

``src/astrometry.py``
   Plate solving and WCS-related utilities using ``stdpipe`` and local
   Astrometry.net tooling.

``src/psf.py``
   PSF photometry helpers and PSF model construction, including the empirical
   PSF (ePSF) builder and ``compute_encircled_energy`` for the aperture
   correction.

``src/xmatch_catalogs.py``
   Catalog cross-matching and catalog enhancement, including GAIA, SIMBAD,
   SkyBoT, and related services.

``src/transient.py``
   Transient candidate search, reference-survey/catalog comparison, and extra
   filtering such as Solar System object rejection.

``src/tools_pipeline.py``
   Shared photometry and WCS utilities such as filter mapping, pixel scale
   extraction, background estimation, and calibrated magnitude helpers.

``src/pipeline.py`` also exposes ``check_psf_aperture_consistency()``, which
matches PSF and aperture sources by position and reports the median
``psf_mag - aperture_mag`` per aperture, warning when the offset exceeds
``PSF_APERTURE_CONSISTENCY_THRESHOLD`` (0.15 mag). Diagnostics are stored in
``st.session_state["psf_aperture_consistency"]``, and ``add_calibrated_magnitudes``
exports the per-source difference as ``psf_minus_aperture_mag_X_X`` (with
filter-prefixed aliases, NaN where only one method measured the source).

``src/tools_app.py``
   Streamlit-side helper functions including FITS loading, cache reset, and UI
   support routines. ``plot_photometry_offsets()`` renders the per-source
   ``psf_mag - aperture_mag`` offset (scatter against calibrated PSF magnitude
   with per-aperture median/zero lines, plus the histogram) shown in the
   results view.

Database and API Modules
^^^^^^^^^^^^^^^^^^^^^^^^

``api/main.py``
   FastAPI application with health, authentication, config persistence, and
   FITS upload/listing endpoints. A successful ``POST /api/login`` writes
   ``[YYYY-MM-DD HH:MM:SS] RPP vX.Y.Z - User '<username>' logged in
   successfully (FastAPI backend).`` to the service log; the legacy backend
   (``backend.py``) emits the same line tagged ``(legacy backend)``.

``api/models.py``
   SQLAlchemy ORM models for users, FITS storage, recovery codes, and tracking
   tables.

``api/schemas.py``
   Pydantic request and response models for the FastAPI service.

``api/storage.py``
   Storage path generation for uploaded FITS files and related backend storage
   conventions.

Useful Functions
----------------

``src.tools_app.load_fits_data(file)``
   Load image data and headers from a FITS file with robust handling for empty,
   invalid, multi-extension, or multi-dimensional FITS content.

``src.tools_app.update_observatory_from_fits_header(header)``
   Extract observatory metadata (name, latitude, longitude, elevation) from a
   FITS header and update the Streamlit session state observatory fields.

``src.tools_app.display_archived_files_browser(api_client)``
   Render the archived FITS file and ZIP result browser in the Streamlit
   sidebar. Queries the backend for the current user's stored files.

``src.tools_pipeline.safe_wcs_create(header)``
   Attempt to build a WCS object from a FITS header while returning structured
   error information and log messages.

``src.tools_pipeline.extract_pixel_scale(header)``
   Extract or estimate the pixel scale used by later photometric and
   astrometric steps.

``src.pipeline.make_border_mask(image, border=50, invert=True, dtype=bool)``
   Build a border mask for detection workflows.

``src.pipeline.fwhm_fit(image, objects, pixel_scale)``
   Estimate the FWHM of the stellar PSF by fitting Gaussian profiles to
   detected sources and averaging the results with sigma clipping.

``src.pipeline.detect_and_mask_satellite_trails(image_data, header, temp_fits_path=None)``
   Detect satellite trails in an image using ASTRiDE and return a boolean mask
   with trail pixels set to True. Returns an empty mask when ASTRiDE is
   unavailable or no trails are found.

``src.pipeline.show_subtracted_image(image_sub)``
   Display the background-subtracted image inside the Streamlit interface.

``src.astrometry.solve_with_astrometrynet(file_path)``
   Run local astrometric solving with the configured Astrometry.net/stdpipe
   stack.

``src.xmatch_catalogs.cross_match_with_gaia(...)``
   Cross-match measured sources with calibration catalogs and prepare data for
   zero-point estimation and later enrichment.

``src.xmatch_catalogs.enhance_catalog(...)``
   Enrich the photometry catalog with additional catalog matches such as
   SIMBAD, SkyBoT, and Astro-Colibri.

``src.transient.find_candidates(...)``
   Run the transient candidate workflow after photometry and catalog
   preparation. Sources are detected with SExtractor, unreliable detections are
   dropped using the SEP flag mask (``SEP_REJECT_FLAGS``) and matched to the
   photometry catalog within a seeing-based radius, and reference-survey /
   Vizier matches are rejected unless the detection is a genuine brightness
   excess (``mag_threshold``, default 2.0 mag). No minimum S/N cutoff is
   applied; low-S/N detections can remain candidates if they pass the other
   catalog and known-object filters.

Photometric Formula Conventions
-------------------------------

The project documentation and code use the following formulas consistently:

- ``σ_flux² = σ_aperture² + σ_total_bkg² + σ_centroid²`` (aperture-sum error,
  the local-background error from the sigma-clipped annulus, and the
  centroid-position term)
- ``σ_bkg_pixel = ApertureStats.median_err`` = ``√(π/2) · s / √A_ann`` (standard
  error of the median annulus level)
- ``S/N = flux / flux_error`` (computed from the background-subtracted,
  aperture-corrected flux)
- ``σ_mag = 1.0857 × (σ_flux / flux)``, combined in quadrature with the
  aperture-correction systematic
  ``σ_apcorr = 1.0857 · f_apcorr · |apcorr − 1| / apcorr``
- ``flux_total = flux_aperture / EE(r)`` (encircled-energy aperture correction)
- ``σ_mag_calib = √(σ_mag_inst² + σ_zp² + σ_sys²)``, where ``σ_zp`` uses the
  sigma-clipped, untrimmed residual scatter and the ``√(π/2)`` median factor

Photometry Error Columns
^^^^^^^^^^^^^^^^^^^^^^^^^

The exported photometry catalog exposes the error budget explicitly so it can
be inspected or recombined downstream:

- ``background_rms_per_pixel_X_X`` — local-background ``median_err`` per pixel
- ``centroid_flux_err_X_X`` — centroid-position term
- ``apcorr_X_X`` / ``ee_fraction_X_X`` — aperture correction and its
  encircled-energy fraction
- ``apcorr_mag_err_X_X`` — aperture-correction magnitude systematic
- ``aperture_mag_err_X_X`` / ``psf_mag_err`` — total calibrated magnitude error
  (after zero-point propagation)
- ``aperture_mag_err_X_X_random`` / ``..._correlated`` and
  ``psf_mag_err_random`` / ``psf_mag_err_correlated`` — the per-source and
  field-common components of the calibrated magnitude error
- ``aperture_mag_X_X_colorcorr_err`` / ``psf_mag_colorcorr_err`` — errors of the
  color-term-corrected magnitudes
- ``zero_point_std`` — field-level formal zero-point uncertainty

The total aperture flux error (aperture + local background + centroid) is
computed as the ``aperture_sum_bkg_corr_err_X_X`` column by
``detection_and_photometry``; it is an intermediate quantity that
``add_calibrated_magnitudes`` does not retain in the exported catalog. The
calibrator scatter and count diagnostics (``zero_point_scatter``,
``zero_point_scatter_kept``, ``zero_point_n``) are stored on the matched
calibration table used for zero-point diagnostics, not on the exported source
catalog.

Analysis Result Files
---------------------

The Streamlit workflow writes files to a per-user ``*_results/`` directory.
Both ZIP builders include files matching the input base name except files with
the ``.fits`` extension. FITS products therefore remain on disk and are not
part of the downloadable archive.

.. list-table:: Generated analysis artifacts
    :header-rows: 1
    :widths: 25 35 25 15

    * - File pattern
      - Contents
      - Generation / writer
      - In ZIP
    * - ``*_catalog.csv`` and ``*_catalog.vot``
      - Curated columns only (``id`` first, then coordinates, Astro-Colibri/SIMBAD/SkyBoT/AAVSO matches, S/N, aperture and PSF photometry); Astro-Colibri sources sorted first
      - After photometry and catalog enrichment; ``src.utils.save_catalog_files`` (columns chosen by ``build_export_columns``/``prepare_export_table``)
      - Yes
    * - ``*_header.txt``
      - Input FITS header
      - When a science header is available; ``src.utils.save_header_to_txt``
      - Yes
    * - ``*_wcs_header.txt``
      - Header containing the available astrometric solution
      - When a WCS solution is available; ``src.utils.save_header_to_txt``
      - Yes
    * - ``*.log``
      - Processing log
      - At the end of analysis; ``pages/app.py``
      - Yes
    * - ``*_*.png``
      - Image and diagnostic plots (for example magnitude and PSF/aperture-offset plots)
      - Depending on the plots produced; ``pages/app.py``
      - Yes
    * - ``*_transients.csv``
      - Transient candidate table with the same columns as the catalog (including ``id``)
      - When transient candidates are returned; ``src.transient.build_transient_table``
      - Yes
    * - ``*_psf.fits``
      - Empirical PSF model
      - When the ePSF model is saved; ``src/psf.py``
      - No
    * - ``*_wcs.fits``
      - Original image with the updated WCS header
      - When WCS solving succeeds; ``src.utils.save_fits_with_wcs``. Also copied to ``rpp_data/fits/``.
      - No

HTTP API Endpoints
------------------

The FastAPI backend in ``api/main.py`` currently exposes these primary routes:

- ``GET /health``
- ``POST /api/register``
- ``POST /api/login``
- ``POST /api/recovery/request``
- ``POST /api/recovery/confirm``
- ``GET /api/config``
- ``POST /api/config``
- ``POST /api/upload/fits``
- ``GET /api/fits``

The optional legacy Flask backend in ``backend.py`` exposes an equivalent
configuration surface for older deployments (see ``api.rst`` for the full
persistence comparison):

- ``POST /register``
- ``POST /login``
- ``POST /recover_request``
- ``POST /recover_confirm``
- ``POST /save_config``
- ``GET /get_config``

Maintenance and Migration Scripts
---------------------------------

The repository also contains command-line helpers in ``scripts/``:

- ``scripts/migrate_add_wcs_zip_tables.py``
- ``scripts/migrate_legacy_db.py``
- ``scripts/satellite_trail_detector.py``

These are maintenance or standalone utilities and are not all part of the main
interactive Streamlit workflow.

Test Suite
----------

Tests live in ``tests/`` and run with ``pytest`` from the project root. The
modules most relevant to backend and settings behaviour are:

- ``tests/test_config_persistence.py`` — FastAPI and legacy Flask
  configuration round-trips, default seeding of ``mag_err_max`` /
  ``effective_gain``, and the save/load wiring in ``pages/app.py`` and
  ``pages/login.py``
- ``tests/test_robustness_ingestion.py`` — FastAPI FITS ingestion and listing
- ``tests/test_database.py`` — ORM models and analysis-history tracking
- ``tests/test_photometry.py`` — photometry and error-budget calculations,
  including the PSF star-selection regression, the ePSF centring, the
  PSF-vs-aperture consistency diagnostic and the effective-gain resolution
  (``CVF`` inversion, keyword precedence, driver-camera detection and the
  plausible range)
- ``tests/test_version_consistency.py`` — release-version agreement across
  ``pyproject.toml``, ``src/__version__.py``, ``docs/conf.py``, the README and
  the changelog
