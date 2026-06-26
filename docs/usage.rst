
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
     you click **Start Analysis Pipeline**.
   - If **Astrometry Check** is enabled, the app forces a new plate-solving
     attempt even when a valid WCS already exists.

7. **Review outputs**

   - Download the ZIP archive with catalogs, plots, logs, and optional WCS
     products.
   - When astrometry succeeds, the updated FITS file is also stored using the
     backend data layout.

Production Deployment
---------------------

For production, you can use an ASGI/WSGI server to run the backend. The
frontend is always launched with Streamlit.

Useful environment variables:

- ``RPP_API_URL``: override the FastAPI base URL used by the frontend.
- ``RPP_LEGACY_URL``: override the legacy backend URL.
- SMTP settings are required if you want password recovery emails to work.

Logging and Troubleshooting
---------------------------

- Backend logs are printed to the console. Check for database initialization
  messages about the database schema and startup.
- If email-based features fail, confirm SMTP settings are configured,
  especially ``SMTP_SERVER``, ``SMTP_PORT``, ``SMTP_USER``, and
  ``SMTP_PASS_ENCODED``.
- For astrometry or plate solving, ensure external dependencies such as
  Astrometry.net indices and ``solve-field`` are installed and available on
  ``PATH``.
- External catalog services such as GAIA, SIMBAD, SkyBoT, and Astro-Colibri
  may time out or return partial results. In many cases the pipeline continues
  and records warnings in the log instead of stopping completely.

Next Steps
----------

- See :doc:`examples` for a minimal Python example.
- See :doc:`installation` for more details about optional dependencies and
  advanced setup.
