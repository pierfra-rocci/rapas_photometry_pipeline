RAPAS Photometry Pipeline (RPP)
===============================

**Version |release|**

RAPAS Photometry Pipeline (RPP) is a Streamlit-based astronomical photometry
application with a FastAPI backend and a legacy compatibility backend. It is
designed for FITS-based image analysis, photometric calibration, catalog
cross-matching, transient screening, and result archiving.

The documentation in this section reflects the current application workflow:
upload a FITS file, review settings, click **Start Analysis**, and then inspect
or download the generated results.

Highlights
----------

- Streamlit frontend with backend auto-detection and fallback support
- FITS upload staging before any scientific processing starts
- Local WCS validation and optional Astrometry.net solving/refinement
- Aperture and PSF photometry with propagated uncertainties
- Catalog enhancement with GAIA, SIMBAD, SkyBoT, VSX, Milliquas, and optional Astro-Colibri
- Optional transient candidate workflow with PanSTARRS or SkyMapper references
- SQLite-backed user settings, FITS tracking, and result archive tracking
- ZIP export of catalogs, logs, plots, and generated intermediate products

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   installation
   usage
   user_guide
   features
   advanced_features
   api
   api_reference
   examples
   troubleshooting
   changelog


Quick Start
-----------

1. Install dependencies with ``pip install -e .``.
2. Start the FastAPI backend with ``python -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000``.
3. If needed, start the legacy backend with ``python backend.py``.
4. Start the frontend with ``streamlit run frontend.py`` or ``streamlit run pages/app.py``.
5. Log in, upload a FITS file, configure parameters in the sidebar, and click **Start Analysis**.
6. Review the plots, tables, and logs, then download the ZIP archive or revisit archived results later.


System Architecture
-------------------

**Frontend (Streamlit):**
   - Main UI in ``pages/app.py`` with login flow in ``pages/login.py``;
     every successful login is written to the service log with a local
     timestamp and the pipeline version
   - Staged upload flow with explicit processing start
   - Result display, archive browsing, and settings persistence

**Backend:**
   - *FastAPI (recommended):* Authentication, config persistence, and FITS storage routes
   - *Legacy backend:* Kept for compatibility with older workflows
   - SQLite-backed user, configuration, and file metadata storage

**Processing Pipeline:**
   - FITS loading, header checks, WCS validation, photometry, and catalog enrichment
   - Optional plate solving, refinement, and transient candidate search
   - Graceful handling of remote-service timeouts and partial-result returns

**External Integration:**
   - Astrometry.net and optional SCAMP for astrometric workflows
   - GAIA, SIMBAD, SkyBoT, VSX, Milliquas, and Astro-Colibri services
   - Aladin Lite for interactive sky visualization



Supported Data Formats
----------------------

- FITS (``.fits``, ``.fit``, ``.fts``, ``.fits.gz``)
- Multi-extension FITS (MEF)
- Data cubes, using the first science plane when needed
- RGB-like FITS content, using the first channel when needed


Use Cases
---------

**Research**:
   - Variable-star photometry and monitoring
   - Calibrated field-source catalog generation
   - WCS validation and improvement for uploaded images
   - Transient screening and Solar System object rejection

**Survey Work**:
   - Reproducible source extraction and catalog enrichment
   - Batch inspection of archived analysis products
   - Cross-identification against multiple public catalogs

**Educational Use**:
   - Demonstrating practical photometry workflows
   - Teaching FITS handling, calibration, and WCS concepts
   - Exploring catalog cross-matching and result validation


Operational Notes
-----------------

- Performance depends strongly on image size, WCS quality, and whether local astrometry tools are available.
- Remote catalog queries can be slow or unavailable; when possible, the application continues with partial results and reports the degraded step in the logs.
- Plate solving and transient workflows depend on optional local and remote services and should be treated as best-effort enhancements rather than hard requirements.



Indices and tables

==================



* :ref:`genindex`

* :ref:`modindex`

* :ref:`search`
