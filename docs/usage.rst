
Usage Guide
===========

This page shows the minimal steps to run the project locally and use the main
features, matching the current Streamlit interface and the dual-backend
workflow used by the application.

Quick Start (Development)
-------------------------

1. **Create and activate a virtual environment**

   .. code-block:: powershell

      python -m venv .venv
      .\.venv\Scripts\Activate.ps1

   .. code-block:: bash

      python3 -m venv .venv
      source .venv/bin/activate

2. **Install the project**

   .. code-block:: powershell

      pip install -e .

3. **Start the backend**

   - *FastAPI (recommended):*

     .. code-block:: powershell

        python -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

     Or use the provided batch file (Windows):

     .. code-block:: powershell

        run_all_cmd.bat

   - *Legacy Flask backend:*

     .. code-block:: powershell

        python backend.py

   The frontend probes the FastAPI backend first and falls back to the legacy
   backend if the API is not reachable.

4. **Start the frontend (Streamlit)**

   .. code-block:: powershell

      streamlit run frontend.py

   or

   .. code-block:: powershell

      streamlit run pages/app.py

   Visit the URL printed by Streamlit, usually ``http://localhost:8501``.

5. **Login and configure the session**

   - Register or log in.
   - On the FastAPI backend, the app supports password recovery and per-user
     saved configuration.
   - Set observatory values and analysis parameters in the sidebar before the
     scientific run.

6. **Upload the FITS file and start processing**

   - Uploading a FITS file stages it in the interface and immediately shows a
     raw header preview.
   - Full FITS loading, header/WCS checks, and astrometry are run only after
     you click **Start Analysis**.
   - In a multi-extension file, the first nonempty numeric image HDU is used,
     preferring the primary HDU; tables and one-dimensional arrays are
     skipped. Loading and plate solving use the same selected image.
   - If **Astrometry Check** is enabled, the app forces a new plate-solving
     attempt even when a valid WCS already exists.
   - A failed forced solve retains the original valid WCS. If no valid WCS can
     be obtained, sky-dependent matching, transient searches and viewers are
     skipped; pixel positions and uncalibrated instrumental photometry are
     exported when a usable pixel scale is available.
   - If the WCS is usable but the calibration catalog or zero point is
     unavailable, the ZIP contains instrumental measurements with available
     sky coordinates. Transient finding is skipped without calibration.
   - Structural WCS validation does not establish astrometric accuracy. Check
     the solution against reference stars.

7. **Review outputs**

   - Download the ZIP archive with catalogs, plots, logs, and optional WCS
     headers. FITS products are stored separately and excluded from the ZIP.
   - When astrometry succeeds, the updated FITS copy retains the other HDUs,
     leaves the uploaded file unchanged, and is also stored using the backend
     data layout.

Production Deployment
---------------------

For production, you can use an ASGI/WSGI server to run the backend. The
frontend is always launched with Streamlit.

Useful environment variables:

- ``RPP_API_URL``: override the FastAPI base URL used by the frontend.
- ``RPP_LEGACY_URL``: override the legacy backend URL.
- ``RPP_DB_PATH``: select the shared SQLite database; set the same value for
  both backends and the frontend.
- ``RPP_LOG_ADMIN_USER_IDS``: comma-separated trusted account IDs allowed to
  view global pipeline logs in Streamlit. The default is no access. Verify IDs
  against the shared database, set this in the Streamlit environment, and
  update it whenever accounts or the database change.
- SMTP settings are required if you want password recovery emails to work.

New archives and WCS FITS copies are stored under
``private_v2/user_<id>/YYYY/MM/`` within their respective storage roots.
Older username-based files remain untouched and are not shown in the archive
browser. An operator must verify ownership independently before manually
migrating them; colliding usernames make old paths unreliable evidence.
Back up both files and tracking records before making changes.
Upgrade and restart both backends together with the frontend. Startup creates
the ``rpp_user_id_reservations`` table without changing existing user IDs,
usernames, or password hashes; subsequent registrations do not reuse reserved
IDs. Keep database and storage backups consistent: replacing or restoring a
database can roll back its reservation history.
Do not reassign an account ID while its private files or log permissions remain;
review storage and revoke or update configured log IDs before deleting accounts
or replacing/restoring the user database.

Logging and Troubleshooting
---------------------------

- Backend logs are printed to the console. Check for database initialization
  messages about the database schema and startup.
- Every successful login writes a timestamped line with the pipeline version to
  the service log, for example
  ``[2026-10-18 18:46:22] RPP v1.9.0 - User 'intern1' logged in successfully (FastAPI backend).``
  The FastAPI backend, the legacy backend and the frontend each emit their own
  line. With ``run_all_linux.sh`` the lines go to ``fastapi_backend.log`` and
  ``streamlit_frontend.log``; started by hand, they go to the terminal.
- If email-based features fail, confirm SMTP settings are configured,
  especially ``SMTP_SERVER``, ``SMTP_PORT``, ``SMTP_USER``, and
  ``SMTP_PASS_ENCODED``.
- For astrometry or plate solving, ensure external dependencies such as
  Astrometry.net indices and ``solve-field`` are installed and available on
  ``PATH``.
- External catalog services such as GAIA, SIMBAD, SkyBoT, Astro-Colibri and
  the TNS may time out or return partial results. In many cases the pipeline
  continues and records warnings in the log instead of stopping completely.
- The TNS cross-match needs ``TNS_API_KEY`` plus ``TNS_BOT_ID``/
  ``TNS_BOT_NAME`` (bot) or ``TNS_USER_ID``/``TNS_USER_NAME`` (user). Its
  catalogue is downloaded once per UTC day and cached (``RPP_TNS_CACHE_DIR``),
  or read from a local file with ``RPP_TNS_CATALOG_PATH``. Setting
  ``RPP_TNS_MIRROR_URL`` queries a shared ``tns-mirror`` API instead of reading
  the catalogue; if the mirror fails, the daily catalogue is used, and if that
  cannot be downloaded the TNS Search API is queried (``RPP_TNS_SANDBOX=1``
  selects the TNS sandbox).

Next Steps
----------

- See :doc:`examples` for a minimal Python example.
- See :doc:`installation` for more details about optional dependencies and
  advanced setup.
