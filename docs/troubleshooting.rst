Troubleshooting
===============

This section addresses common issues and provides solutions for the current
RAPAS Photometry Pipeline workflow.

Installation & Setup Issues
---------------------------

**Error: Python version mismatch (Expected Python 3.12)**

*   **Problem**: The project is tested against Python 3.12, but a different
    Python version is active.
*   **Solution**:
    1.  Check your Python version: ``python --version``
    2.  Install Python 3.12 from python.org or your package manager.
    3.  Create a new virtual environment with Python 3.12:
        ``python3.12 -m venv .venv``
    4.  Activate the environment and reinstall dependencies:
        ``pip install -e .``

**Error: ImportError: No module named '...'**

*   **Problem**: Required Python packages are not installed or are not found in
    the current environment.
*   **Solution**:
    1.  Ensure your virtual environment is activated.
    2.  Verify you are using Python 3.12.
    3.  Run ``pip install -e .`` from the project root.
    4.  If issues persist, try ``pip install --upgrade --force-reinstall -e .``.

**Error: Could not connect to the backend**

*   **Problem**: The selected backend is not running or is inaccessible.
*   **Solution**:
    1.  Open a separate terminal in the project root.
    2.  Activate the virtual environment.
    3.  Start either ``python -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000``
        or ``python backend.py``.
    4.  Check the terminal output for errors and confirm the expected port is
        available.
    5.  If needed, set ``RPP_API_URL`` or ``RPP_LEGACY_URL`` to match your
        environment.
    6.  If the FastAPI server is running but ``GET /health`` returns HTTP 500
        after a Python or library upgrade, treat that as a backend dependency
        problem rather than a missing legacy server. Reinstall the environment
        from the project root with ``pip install --upgrade --force-reinstall -e .``
        and verify that ``anyio`` was reinstalled cleanly.

**Error: ``FastAPI.__call__() missing 1 required positional argument: 'send'``**

*   **Problem**: The FastAPI app was started with plain Gunicorn WSGI workers,
    for example ``gunicorn api.main:app``, so requests such as ``GET /health``
    fail before they reach FastAPI.
*   **Solution**:
    1.  Start the API with Uvicorn directly: ``python -m uvicorn api.main:app --host 127.0.0.1 --port 8000``.
    2.  Or start it with Gunicorn using the ASGI worker:
        ``gunicorn -k uvicorn.workers.UvicornWorker api.main:app``.
    3.  If you launch Gunicorn from the project root, the repository's
        ``gunicorn.conf.py`` now sets ``uvicorn.workers.UvicornWorker`` by
        default.

**Error: DLL load failed while importing...** (Windows)

*   **Problem**: Missing C++ runtime libraries required by some Python packages.
*   **Solution**: Install the latest Microsoft Visual C++ Redistributable
    packages for Visual Studio from the official Microsoft website.

Login & Registration Issues
---------------------------

**Error: "Username or email is already taken."**

*   **Problem**: The chosen username or email address already exists in the
    database.
*   **Solution**: Choose a different username and/or email address.

**Error: "Invalid username or password."**

*   **Problem**: Incorrect username or password entered.
*   **Solution**: Verify your credentials. Use the password recovery option if
    you forgot your password.

**Password Recovery Fails / No Email Received**

*   **Problem**: Email sending is not configured correctly on the backend.
*   **Solution**:
    1.  Ensure the active backend is running.
    2.  Verify that SMTP settings are correctly configured, especially
        ``SMTP_SERVER``, ``SMTP_PORT``, ``SMTP_USER``, and
        ``SMTP_PASS_ENCODED``.
    3.  Check the backend terminal output for email sending errors.
    4.  Check your email spam folder.
    5.  Ensure your email provider allows sending via SMTP.

Image Loading & Processing Issues
---------------------------------

**Error: Could not read FITS file / No image data found**

*   **Problem**: The file is corrupted, not a valid FITS file, or uses an
    unsupported format or compression mode.
*   **Solution**:
    *   Verify the file opens correctly in other FITS viewers.
    *   Ensure the file has a standard extension such as ``.fits`` or
        ``.fits.gz``.
    *   If it is a multi-extension FITS, ensure image data exists in at least
        one HDU.

**The FITS file uploads but nothing starts immediately**

*   **Problem**: This is expected behavior in the current interface.
*   **Solution**:
    *   Uploading stages the file only.
    *   Click **Start Analysis Pipeline** to trigger FITS loading, WCS checks,
        astrometry, and photometry.

**Error: Missing required WCS keywords / WCS creation error**

*   **Problem**: The FITS header lacks the necessary keywords for
    ``astropy.wcs`` to build a coordinate system.
*   **Solution**:
    *   Enable plate solving using Astrometry.net.
    *   Alternatively, use external software to add WCS information to the FITS
        header before uploading.

**Astrometry Check / WCS Refinement Fails**

*   **Problem**: The ``stdpipe`` refinement or blind-solving process could not
    find enough matches, or the initial WCS was too inaccurate.
*   **Solution**:
    *   Ensure the initial WCS is roughly correct.
    *   Check internet connectivity when catalog access is needed.
    *   Try adjusting the seeing parameter, as it affects detection.
    *   If the field is very sparse or very crowded, refinement may struggle.
    *   If **Astrometry Check** was enabled on an image that already had valid
        WCS, the app may fall back to the original WCS when the forced solve
        fails.

**No Sources Found / Few Sources Found**

*   **Problem**: Detection parameters may be unsuitable for the image.
*   **Solution**:
    *   Lower the detection threshold to detect fainter sources.
    *   Ensure the seeing estimate is reasonable.
    *   Check if the border mask is too large.
    *   Verify the image is not blank or excessively noisy.

**Zero Point Calculation Fails / No Gaia Matches**

*   **Problem**: The app could not match detected sources to the GAIA catalog
    or calculate a reliable zero point.
*   **Solution**:
    *   Verify WCS accuracy.
    *   Check internet connectivity for GAIA queries.
    *   Adjust the filter magnitude limit.
    *   Ensure the selected filter band is appropriate for the image filter.
    *   The field may genuinely lack suitable GAIA stars in the specified
        magnitude range.

Catalog Query Issues (SIMBAD, SkyBoT, etc.)
-------------------------------------------

**Warning: "Query failed: Network error / Timeout"**

*   **Problem**: A temporary network issue occurred or the external catalog
    service is down or slow.
*   **Solution**:
    *   Wait and try processing the image again later.
    *   Check your internet connection.
    *   Review the generated log file to see which catalog step timed out or was
        skipped.
    *   Note that the pipeline may continue with partial results rather than
        failing completely.

**Warning: "No sources found..." for a specific catalog**

*   **Problem**: The catalog genuinely contains no known objects matching the
    criteria within your image field.
*   **Solution**: This is expected for many fields. The message is informational.

**SkyBoT parse error: "not well-formed (invalid token)"**

*   **Problem**: SkyBoT returned a malformed or non-XML payload for the query,
    so parsing failed.
*   **Solution**:
    *   The pipeline now treats this as a warning and continues without SkyBoT
        matches.
    *   Re-run later to retry the external service response.
    *   Check the log preview text to verify the response was malformed rather
        than a local configuration issue.

**Astro-Colibri: No matches or Error**

*   **Problem**: Invalid or missing API key, network issue, or no recent events
    in the field.
*   **Solution**:
    *   Ensure you have entered the correct Astro-Colibri UID key in the
        sidebar.
    *   Check internet connectivity.
    *   Check the Astro-Colibri service status.

Database and Migration Issues
-----------------------------

**Need to add 1.6.0 tracking tables to an existing database**

*   **Problem**: Older databases may not contain the WCS FITS and ZIP archive
    tracking tables.
*   **Solution**:
    *   Run ``python scripts/migrate_add_wcs_zip_tables.py``.
    *   Keep the generated backup before applying changes to production data.

**Need to convert a legacy Streamlit SQLite database to the API schema**

*   **Problem**: A legacy database layout may not match the SQLAlchemy-backed
    FastAPI schema.
*   **Solution**:
    *   Run ``python scripts/migrate_legacy_db.py`` with the appropriate
        arguments.
    *   Prefer a dry run and backup before migrating live data.
