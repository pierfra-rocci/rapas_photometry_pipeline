Advanced Features
=================

This section collects the current advanced and developer-oriented workflows in
RAPAS Photometry Pipeline.

Database Tracking System
------------------------

RPP stores analysis-history metadata in SQLite so uploaded FITS files and
generated ZIP archives can be tracked per user.

The tracking layer uses three linked tables:

.. code-block:: text

    wcs_fits_files
        Uploaded or generated FITS records tied to a user

    zip_archives
        Result ZIP archives tied to a user

    wcs_fits_zip_assoc
        Association table linking one FITS record to one or more ZIP results

This supports:

- One FITS file associated with multiple processing runs
- Per-user isolation of stored files and metadata
- Automatic recording after successful result creation
- Safe re-use of existing records when the same stored filename or archive is encountered again

Migrating Existing Databases
----------------------------

If you are upgrading an older deployment, use the migration scripts in
``scripts/``.

.. code-block:: bash

    python scripts/migrate_add_wcs_zip_tables.py --db-path /path/to/users.db
    python scripts/migrate_legacy_db.py

The tracking-table migration creates a backup unless told otherwise and is
designed to be rerunnable.

Inspecting Analysis History
---------------------------

The helper functions in ``src/db_tracking.py`` expose the recorded history:

.. code-block:: python

    from src.db_tracking import (
        get_fits_files_for_user,
        get_zip_archives_for_user,
        get_zips_for_fits,
    )

    fits_files = get_fits_files_for_user("alice")
    archives = get_zip_archives_for_user("alice")
    related_zips = get_zips_for_fits(wcs_fits_id=1)

Configuration Persistence
-------------------------

The frontend saves user settings as a single JSON blob and reloads it on the
next login. The blob carries ``analysis_parameters`` (every sidebar control,
including *Max Magnitude Error* ``mag_err_max`` and *Detector Gain*
``effective_gain``), ``observatory_data`` and ``colibri_api_key``.

- **Save**: the **Save Settings** button in ``pages/app.py`` serializes the
  current dictionaries into the blob.
- **Load**: ``pages/login.py`` restores the dictionaries into session state on
  login; any missing keys are filled from ``default_analysis_params`` in
  ``src/tools_app.py`` (both new controls are seeded there).
- **Backends**: FastAPI (``GET``/``POST /api/config``) or the legacy Flask
  backend (``GET /get_config`` / ``POST /save_config``). See :doc:`api` for the
  full comparison, payload shapes and storage columns.

Because the whole ``analysis_parameters`` dictionary is persisted verbatim, new
sidebar controls round-trip automatically and require no database migration.

Development and Testing
-----------------------

Tests live in ``tests/`` and are run with ``pytest`` from the project root
(install the dev extras first with ``pip install -e ".[dev]"``):

.. code-block:: bash

   pytest

The configuration-persistence behaviour is covered by
``tests/test_config_persistence.py``:

- the FastAPI ``/api/config`` save/reload round-trip, including the ``0``
  "disable the cut" / "automatic gain" values;
- the legacy Flask ``/save_config`` / ``/get_config`` round-trip (skipped when
  Flask is unavailable, and run with a stub ``flask-cors`` when only the CORS
  extension is missing);
- seeding of ``mag_err_max`` / ``effective_gain`` in
  ``default_analysis_params``; and
- structural checks that ``pages/app.py`` writes both controls into the saved
  dictionary and that ``pages/login.py`` restores ``analysis_parameters``.

Both backend suites use an isolated SQLite database in a temporary directory, so
running the tests never touches the developer's ``users.db``.

Transient Detection
-------------------

The transient workflow is optional and runs after the main photometry and
catalog-enhancement steps.

Candidate finding (``src.transient.find_candidates``) works as follows:

- The user enables it from the **Transient Candidates** sidebar section and
  selects the reference filter.
- The reference survey is chosen from the field declination: PanSTARRS in the
  north, SkyMapper in the south.
- Sources are detected with SEP and cross-matched against the pipeline
  photometry catalog, taking the *nearest* counterpart within a seeing-based
  radius (the FWHM in arcsec, clamped to 1-4 arcsec).  Matched detections
  receive the calibrated PSF magnitudes already measured by the pipeline.
- Detections flagged by SEP as unreliable (object/aperture truncated at the
  frame boundary, deblending overflow, degenerate centroid, or an all-masked /
  non-positive aperture) are discarded.  These are SEP flag bits, so the mask
  passed to stdpipe is ``SEP_REJECT_FLAGS`` rather than stdpipe's
  SExtractor-style default (``0x7F00``), which never matches raw SEP flags.
- ``stdpipe.pipeline.filter_transient_candidates`` rejects detections that
  coincide with reference-survey or Vizier sources.  The positional matching
  radius is half the seeing FWHM (set from ``fwhm`` and ``pixscale``), and a
  coincident source is kept only when it is brighter than its catalog
  counterpart by more than ``mag_threshold`` (default 2.0 mag, after removing
  the median zero-point offset), i.e. when there is a genuine brightness
  excess.
- Solar System objects are rejected separately through the SkyBoT service
  (120-second timeout), and candidates whose magnitude error is not finite are
  dropped.
- No minimum S/N cutoff is applied to transient candidates. Low-S/N detections
  remain eligible if they pass the other catalog, known-source, and Solar
  System-object filters.
- Cutouts (science and HiPS template) are displayed and saved for the top 20
  candidates, ranked by signal-to-noise ratio.

This workflow should be treated as best-effort. Reference data, remote catalog
services, and transient filtering may degrade gracefully when an external
dependency is unavailable.

Astrometric Workflows
---------------------

RPP supports several WCS-related paths:

- Validation and reuse of an existing WCS already present in the header
- Forced astrometry checks from the sidebar when the user wants a fresh solve attempt
- Local blind solving through Astrometry.net and ``stdpipe``
- Optional refinement workflows when the local toolchain is available

If a new forced solve fails but the file already had a valid WCS, the frontend
falls back to that valid solution so the rest of the pipeline can continue.

PSF Photometry
--------------

PSF photometry complements the aperture measurements and is useful when:

- sources are crowded,
- aperture measurements become unstable, or
- a PSF-based comparison is desirable.

The pipeline builds an empirical PSF model from suitable stars and then reports
PSF-derived fluxes and magnitudes alongside the aperture results when the fit is
successful. Candidate stars are filtered on flux, S/N, shape, isolation and
local-background flatness; the PSF is fitted in a compact region around each
source while the ePSF itself is extracted from a larger cutout so its wings are
preserved, and the aperture correction is derived from the exact model used for
the fits.

After photometry the pipeline cross-checks the two methods: PSF and aperture
sources are matched by position and the median ``psf_mag − aperture_mag`` is
reported for each aperture. Because the aperture fluxes are placed on the same
total-flux scale as the PSF (see ``docs/PHOTOMETRY_FORMULAS.md``), the offset
should be near zero; a median larger than
``PSF_APERTURE_CONSISTENCY_THRESHOLD`` (0.15 mag) raises a warning and the
per-aperture diagnostics are stored in
``st.session_state["psf_aperture_consistency"]``.

The results view plots these offsets with ``plot_photometry_offsets``: one
panel shows the offset against the calibrated PSF magnitude with per-aperture
median and zero lines, the other the offset histogram, and the figure is saved
as ``<base>_psf_aperture_offset.png``.

Custom Post-Processing
----------------------

The generated CSV catalogs and ZIP archives can be reused in external scripts or
notebooks for tasks such as:

- light-curve construction,
- differential photometry,
- cross-run source aggregation,
- custom filtering, or
- publication-ready plotting.

For most programmatic entry points, prefer the modules summarized in
``api_reference.rst`` and the concrete pipeline helpers under ``src/``.

Standalone Utilities
--------------------

The ``scripts/`` directory contains maintenance or utility scripts that are not
part of the normal Streamlit button flow:

- ``migrate_add_wcs_zip_tables.py``
- ``migrate_legacy_db.py``
- ``satellite_trail_detector.py``

These are intended for administration, migration, or offline workflows rather
than day-to-day frontend use.