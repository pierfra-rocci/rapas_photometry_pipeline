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

*   **Problem**: Incorrect password, or an identifier that does not match any
    account.
*   **Solution**:

    1.  The *Login* field accepts the account username **or** the email address
        used at registration. Try the email if you no longer remember the
        username.
    2.  Verify the password, and use the password recovery option if you forgot
        it.

**Where can I see who logged in?**

*   **Solution**: every successful login writes a timestamped line with the
    pipeline version to the service log, for example
    ``[2026-10-18 18:46:22] RPP v1.9.0 - User 'intern1' logged in successfully (FastAPI backend).``
    The FastAPI backend, the legacy backend and the frontend each emit their
    own line, so a login appears in both the backend and frontend logs.
    Administrators can also open the in-app *Pipeline Logs* page
    (``pages/showlogs.py``) to read the last 200 lines of
    ``fastapi_backend.log`` and ``streamlit_frontend.log``.

**Error: "Email already registered." while the account cannot be reached**

*   **Problem**: The address is already attached to an account, but the login
    identifier does not match it.
*   **Solution**:

    1.  Log in with the email address itself instead of a username.
    2.  Reset the password through *Password Recovery*, then log in with the
        email address and the new password.

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

.. note::

    For privacy, the recovery request always reports success, even when no
    account uses the submitted address. The address must therefore be exactly
    the one attached to an account. Matching ignores capitalisation and
    surrounding spaces, so any spelling of the address that resolves to the
    registered mailbox works.

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

**A wrong image or header appears, or a previous file seems to persist**

*   **Problem**: The preview shows a header or file that is not the one you
    just uploaded.
*   **Solution**:

    *   Click **Reset** once. The uploader is re-created on every reset, so
        the previous file is dropped immediately; a second press is no longer
        needed.
    *   Each browser session now works in its own ``work_<id>`` folder, so
        files of other users or tabs cannot leak into your analysis. If the
        problem persists, reload the page and report the file name and time.
    *   Leftover temporary files from closed browsers or abandoned analyses
        are removed automatically after 24 hours; archived ZIP results are
        never deleted by this cleanup.

**The FITS file uploads but nothing starts immediately**

*   **Problem**: This is expected behavior in the current interface.
*   **Solution**:

    *   Uploading stages the file only.
    *   Click **Start Analysis** to trigger FITS loading, WCS checks,
        astrometry, and photometry.

**Error: Missing required WCS keywords / WCS creation error**

*   **Problem**: The FITS header lacks the necessary keywords for
    ``astropy.wcs`` to build a coordinate system.
*   **Solution**:

    *   Enable plate solving using Astrometry.net.
    *   Alternatively, use external software to add WCS information to the FITS
        header before uploading.

**Astrometry Check / WCS Refinement Fails**

*   **Problem**: The SCAMP refinement or blind-solving process could not
    find enough matches, or the initial WCS was too inaccurate.
*   **Solution**:

    *   Ensure the initial WCS is roughly correct.
    *   Ensure the ``scamp`` binary is installed and on ``PATH``, or point
        ``RPP_SCAMP_EXECUTABLE`` at it. The refinement runs SCAMP directly.
    *   Check internet connectivity when catalog access is needed.
    *   Try adjusting the seeing parameter, as it affects detection.
    *   The solver already lowers its detection threshold (3, 2, then 1 sigma)
        to reach about 20 stars (it also tries 2 sigma when 3 sigma gives
        fewer than 100 sources); fewer than 20 stars (or fewer than 5, which
        is fatal) means the field is too sparse, too short an exposure, or
        the image is saturated or out of focus.
    *   If the field is very sparse or very crowded, refinement may struggle.
    *   If **Astrometry Check** was enabled on an image that already had valid
        WCS, the app may fall back to the original WCS when the forced solve
        fails.

When the local Astrometry.net solve fails, RPP can optionally try the online
Astrometry.net service using the detected source list (not the FITS image). Set
``ASTROMETRY_API_KEY`` in the environment of the Streamlit process, or configure
``api_key`` under ``[astrometry_net]`` in Astroquery's
``~/.astropy/config/astroquery.cfg``. Online solving is skipped when no key is
available; the original local-solve failure and WCS fallback behavior remain
unchanged. Submitted source lists are marked private where supported by the
service.

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

**Unrealistic S/N or Magnitude Errors / Gain Reported as Ignored**

*   **Problem**: The warning "... is outside the plausible effective-gain
    range" appears, or bright-source magnitude errors look far too small (and
    the cosmic-ray mask covers a large part of the frame). On several CMOS
    cameras the FITS ``GAIN`` keyword stores an abstract *driver* setting
    rather than an e-/ADU conversion factor: QHY uses a 0-100 scale (a QHY268M
    at ``GAIN=56`` is ≈0.77 e-/ADU) and ZWO ASI uses a 0.1 dB value.
*   **Solution**:

    *   RPP already recognises QHY/ZWO/ASI cameras (see
        ``DRIVER_GAIN_INSTRUMENT_PATTERNS``) and ignores their bare ``GAIN``,
        falling back to ``EGAIN``/``CVF``, the frame's photon-transfer estimate,
        or ``effective_gain = 1``.
    *   For the most accurate errors, set *Detector Gain* in the sidebar (or
        ``RPP_EFFECTIVE_GAIN`` for headless runs) to the true e-/ADU conversion
        factor for the gain setting in use, read from the manufacturer's
        gain-vs-e-/ADU chart.
    *   If the header carries a genuine conversion factor, provide it as
        ``EGAIN``/``CCDGAIN`` (e-/ADU) or ``CVF`` (RPP uses ``gain = 1/CVF``).

**PSF photometry is skipped, or the log says "Cannot normalize ePSF"**

*   **Problem**: ``EPSFBuilder`` stopped with ``Cannot normalize ePSF: the data
    sum (...) is not finite and positive`` and the run continued with aperture
    photometry only. This comes from the ePSF-to-star fits: photutils weights
    them by ``1 / error²``, so an error map that does not describe the frame
    down-weights the star cores, the fitted fluxes come back negative, and the
    next ePSF is built inverted. The failure is in the error model, not in the
    ePSF itself (the first iteration is usually fine). On the frame this was
    reported for, a normalised float stack (sky ~0.01, no ``BUNIT``/``GAIN``)
    had been given a genuine e-/ADU gain, which made the Poisson term ~1e5
    times the measured sky variance.
*   **Solution**:

    *   Update RPP: ``resolve_poisson_gain`` now detects that the gain cannot
        describe the data's units and re-derives it from the sky level and its
        RMS (``gain = sky level / RMS²``), reporting it with the message
        "Using the background-consistent gain ...". The same repaired map also
        restores the aperture S/N and magnitude errors of such frames.
    *   For an exact error model, supply the gain in the *units of the data*:
        a frame in counts (ADU/electrons) with ``EGAIN``/``CCDGAIN``/``CVF`` in
        the header, or the matching *Detector Gain* value.
    *   A frame that is already sky-subtracted gives no sky level to check
        against, so set the gain explicitly or analyse a frame in counts.
    *   If PSF photometry is still skipped, check the PSF star count in the
        log: 10 stars is the hard minimum and 25 the quality target.

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
    *   If the preview starts with ``# Flag: -1``, SkyBoT reports a service-side
        error. Keep the accompanying ticket number and report it to IMCCE if the
        error persists.
    *   The pipeline requests VOTable output for Astroquery's parser. A
        ``No sources found from 10 Parsec catalog`` message is a separate
        VizieR query result and is not caused by the SkyBoT response.

**Astro-Colibri: No matches or Error**

*   **Problem**: Invalid or missing API key, network issue, or no recent events
    in the field.
*   **Solution**:

    *   Ensure you have entered the correct Astro-Colibri UID key in the
        sidebar.
    *   Check internet connectivity.
    *   Check the Astro-Colibri service status.
    *   HTTP 429 means the daily quota (100 requests/day) is exhausted; the
        log message states the reason returned by the API.
    *   A warning about the 15 or 20 arcsec tier means the association is
        loose: check it visually in Aladin.

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

**Pixel scale could not be determined**

*   **Problem**: The run stops with "Pixel scale could not be determined from
    the FITS header".
*   **Solution**: The header has no usable ``PIXSCALE``/``SECPIX``, CD matrix, or
    pixel size + focal length, and the WCS could not provide a scale. Enter the
    value in the sidebar (**Parameters > Pixel scale override**, arcsec/pixel)
    and start the analysis again.

**Login password not taken into account in Firefox**

*   **Problem**: After typing the password, the value is not taken into account.
*   **Solution**: Press Enter in the password field before clicking **Login**.
