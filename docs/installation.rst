
Installation Guide
==================

This page lists quick, copyable steps to set up the project locally on Windows
(PowerShell) and Unix (bash). It covers both FastAPI (recommended) and legacy
backend options. External tools such as Astrometry.net and SCAMP remain
optional for advanced features.

Minimum Requirements
--------------------

- Python 3.12, recommended for verified dependency compatibility
- pip (latest version recommended)
- At least 5 GB free disk space, with more recommended for large image sets and
  temporary processing files

Quick Install (PowerShell)
--------------------------

.. code-block:: powershell

   git clone https://github.com/your-repo/rpp.git
   cd rpp
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e .

Quick Install (bash)
--------------------

.. code-block:: bash

   git clone https://github.com/your-repo/rpp.git
   cd rpp
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .

Optional External Tools
-----------------------

- Astrometry.net: required for blind plate solving. Ensure ``solve-field`` is
  installed and available on ``PATH``.
- SCAMP: optional, for astrometric refinement workflows.
- ASTRiDE: optional, only needed if you want to experiment with the standalone
  satellite trail masking helper in ``scripts/satellite_trail_detector.py``.

Backend Dependencies
--------------------

``pip install -e .`` installs the dependencies for both backends:

- **FastAPI (recommended):** ``fastapi``, ``uvicorn``, ``sqlalchemy``,
  ``werkzeug`` and friends.
- **Legacy Flask compatibility:** ``flask`` and ``flask-cors``, used by
  ``backend.py``.

The legacy backend creates its SQLite database (``users.db``, or
``users_dev.db`` when ``APP_ENV=development``) on first run and shares the same
SMTP configuration as the FastAPI backend.

Running the Tests
-----------------

Install the development extras to get ``pytest``, then run the suite from the
project root:

.. code-block:: bash

   pip install -e ".[dev]"
   pytest

Environment Variables
---------------------

Set SMTP credentials if you want the app to send password recovery emails.
Frontend/backend URLs may also be overridden when needed.

.. code-block:: powershell

   $env:SMTP_SERVER = 'smtp.gmail.com'
   $env:SMTP_PORT = '587'
   $env:SMTP_USER = 'you@example.com'
   $env:SMTP_PASS_ENCODED = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('your-app-password'))
   $env:RPP_API_URL = 'http://localhost:8000'
   $env:RPP_LEGACY_URL = 'http://localhost:5000'

Verification
------------

After installation, verify Python dependencies and start the app:

.. code-block:: powershell

   python -c "import streamlit, astropy; print('OK')"

   # Start backend (choose one):
   python -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
   # or legacy:
   python backend.py

   # Start frontend:
   streamlit run frontend.py

If you plan to use plate solving, verify ``solve-field --help`` works and
download index files appropriate for your typical field scale.

Once the frontend is open:

- upload a FITS file,
- review the observatory and analysis parameters,
- then click **Start Analysis**.

Uploading the file alone does not start the scientific processing phase.

Support
-------

- See :doc:`usage` and :doc:`examples` for simple run instructions and a
  minimal Python example.
- For Astrometry.net and SCAMP installation follow their project pages.
- For schema upgrades or legacy database migration, see the scripts in
  ``scripts/``.