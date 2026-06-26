User Guide
==========

This guide describes the current end-user workflow for RAPAS Photometry
Pipeline. The application uses a staged interface: uploading a FITS file only
prepares it for analysis, and the scientific processing starts only after you
click **Start Analysis**.

Getting Started
---------------

Before opening the Streamlit app, start at least one backend:

- FastAPI backend (recommended): ``python -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000``
- Legacy backend (compatibility mode): ``python backend.py``

Then launch the frontend:

- ``streamlit run frontend.py``
- or ``streamlit run pages/app.py``

The login page probes the available backends automatically. If the FastAPI
backend is reachable it is preferred; otherwise the frontend falls back to the
legacy backend.

Application Layout
------------------

The interface is divided into two main areas:

- Sidebar for account actions, observatory settings, analysis parameters, transient options, API key input, settings save, and archived-result browsing
- Main panel for FITS upload, image preview, processing progress, tables, plots, logs, and downloads

Step 1: Sign In
---------------

1. Open the login page.
2. Register a new account or log in with an existing one.
3. Use password recovery if needed. Recovery codes are emailed when SMTP is configured.

Step 2: Upload a FITS File
--------------------------

1. Select a science FITS file in the main upload area.
2. After upload, the file is staged and marked as ready.
3. At this stage the app does not yet run FITS loading, astrometry checks, or photometry.

Step 3: Configure Parameters
----------------------------

Use the sidebar to review or change:

- Observatory name, latitude, longitude, and elevation
- Estimated seeing, detection threshold, FWHM radius factor, and border mask size
- Calibration filter band (the maximum calibration magnitude is fixed at 21, the GAIA catalogue limit)
- **Astrometry check** to force solving or refining the WCS workflow
- **Transient Candidates** options to enable the transient finder and choose a reference filter
- Optional Astro-Colibri UID key

If observatory metadata are present in the FITS header, the application may use
them to prefill the observatory fields.

Step 4: Start Analysis
----------------------

1. Click **Start Analysis**.
2. The pipeline then performs the full scientific workflow.

Depending on the file and selected options, this can include:

- FITS loading and header validation
- WCS validation and optional local plate solving
- Source detection and FWHM estimation
- Aperture photometry and PSF photometry
- Catalog-based zero-point calibration and, when colors are available, a simple color-term correction
- Catalog enhancement through external services
- Optional transient-candidate search
- Packaging of outputs into downloadable files

Astrometry Behavior
-------------------

- If a valid WCS is already present, the application can continue with that solution.
- If WCS is missing or the user enables **Astrometry check**, the app attempts local plate solving.
- If forced solving fails but an existing WCS is still valid, the pipeline falls back to the existing WCS rather than stopping the whole run.

Catalog Enhancement
-------------------

After photometry, the catalog can be enriched with matches from services such
as:

- GAIA DR3
- SIMBAD
- SkyBoT
- AAVSO VSX
- Milliquas
- Astro-Colibri, when an API key is provided

These services are best-effort integrations. Timeouts, empty responses, or
temporary network failures are reported in the logs and warnings, and the
pipeline continues whenever partial results are still usable.

Transient Candidates
--------------------

If enabled, the transient workflow runs after the main photometry and catalog
steps. It uses reference-survey data and then applies additional filtering,
including Solar System object rejection through SkyBoT.

Survey selection is based on the field declination in the current code path:

- Northern fields use PanSTARRS references
- Southern fields use SkyMapper references

Results and Downloads
---------------------

The main panel can show:

- Image previews and diagnostic plots
- Source and calibration tables
- Processing logs
- Interactive Aladin views
- Download buttons for result archives

**Statistics section**: the target field center RA and DEC are shown in both
decimal degrees and sexagesimal format (HH:MM:SS / ±DD:MM:SS). The same dual
format is written into the processing log file.

**Magnitude Error vs Magnitude plot**: the Y-axis uses a logarithmic scale so
that photometric precision is readable across the full magnitude range of the
catalog.

Result ZIP archives typically contain the generated catalogs, logs, plots, and
other analysis products created during the run.

When the matched calibration stars include a usable catalog color, the exported
catalog may also contain additional color-corrected magnitude columns such as
``psf_mag_colorcorr`` or ``aperture_mag_1_3_colorcorr``. These are written in
parallel with the standard zero-point-calibrated magnitudes, not in place of
them. The catalog may also include trace columns such as ``source_color``,
``color_term_label``, ``color_term_value``, and ``color_term_offset``.

Saved Settings and Archives
---------------------------

- Use **Save Settings** to persist observatory and analysis parameters when the API backend is active.
- Use the archived-results area to review previously stored FITS uploads and ZIP outputs tied to your account.
- In the archived-results sidebar browser, ZIP content is loaded only when you click to download a specific archive, reducing rerun overhead while keeping the full archive list visible.

Practical Tips
--------------

- Check the logs first when a catalog or network-backed enrichment step is missing.
- Install Astrometry.net locally if you want reliable blind solving for files without WCS.
- Treat remote-service enrichments and transient screening as optional enhancements, not guaranteed steps.
- If a field is crowded or sparse, adjust the seeing and detection threshold before rerunning the pipeline.