Backend API
===========

RPP exposes a FastAPI backend in ``api/main.py``. The Streamlit frontend uses
this backend for account management, configuration persistence, and FITS file
storage. A legacy backend still exists for compatibility, but the FastAPI
service is the primary documented interface.

Authentication Model
--------------------

- Registration and login are initiated through JSON requests.
- Authenticated routes use the current backend security layer from ``api/security.py``.
- The Streamlit frontend handles credential storage and request formatting through ``pages/api_client.py``.

Core Endpoints
--------------

Health and Authentication
^^^^^^^^^^^^^^^^^^^^^^^^^

- ``GET /health``
- ``POST /api/register``
- ``POST /api/login``
- ``POST /api/recovery/request``
- ``POST /api/recovery/confirm``

Configuration
^^^^^^^^^^^^^

- ``GET /api/config``
- ``POST /api/config``

FITS Storage
^^^^^^^^^^^^

- ``POST /api/upload/fits``
- ``GET /api/fits``

Behavior Notes
--------------

- FITS uploads are written to user-scoped storage paths under ``rpp_data/fits/``.
- Configuration blobs are stored per user in the database.
- Duplicate FITS uploads are rejected when the backend detects the same stored hash.
- The backend returns schema-driven JSON responses defined in ``api/schemas.py``.

Configuration Persistence
-------------------------

The **Save Settings** button in ``pages/app.py`` serializes the whole
``analysis_parameters`` dictionary (every sidebar control, including
*Max Magnitude Error* ``mag_err_max`` and *Detector Gain* ``effective_gain``)
together with ``observatory_data`` and ``colibri_api_key`` into a single JSON
blob::

    {
      "analysis_parameters": { ... },
      "observatory_data": { ... },
      "colibri_api_key": "..."
    }

The frontend picks a persistence path from ``st.session_state.backend_mode``.
On the next login, ``pages/login.py`` fetches the blob and assigns its
``analysis_parameters`` / ``observatory_data`` entries back into session state
(``initialize_session_state`` in ``src/tools_app.py`` then fills in any missing
defaults). Because the entire parameters dictionary is stored verbatim, every
sidebar control round-trips without the backend needing to know its individual
fields.

FastAPI backend (recommended)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Selected when ``backend_mode == "api"``.
- ``POST /api/config`` with body ``{"config": { ... }}`` writes the blob to the
  ``users.config_json`` column of the SQLAlchemy-managed database.
- ``GET /api/config`` returns ``{"config": { ... }}``, or ``{"config": null}``
  when the user has no stored configuration yet.
- Authenticated with HTTP Basic; the client helpers are ``save_config`` /
  ``get_config`` in ``pages/api_client.py``.
- Implementation: ``api/main.py`` (``save_config`` / ``get_config``),
  model in ``api/models.py``, schemas in ``api/schemas.py``.

Legacy Flask backend
^^^^^^^^^^^^^^^^^^^^

- Selected when ``backend_mode == "legacy"``.
- ``POST /save_config`` accepts ``{"username": ..., "config_json": "<json string>"}``
  and stores the string in the ``users.config_json`` column.
- ``GET /get_config`` returns the stored JSON string, or ``{}`` when none is set.
- Authenticated with HTTP Basic. The server derives the username from the
  credentials via ``authenticate_request()``; the ``username`` body field is
  ignored.
- Storage: SQLite file ``users.db`` (``users_dev.db`` when
  ``APP_ENV=development``), created and migrated by ``backend.py``.
- Note: importing ``backend.py`` runs ``init_db()`` at module scope, and the
  module depends on ``flask`` / ``flask-cors``; these are not part of the
  FastAPI install, so the legacy backend should be treated as optional.

Both backends store the blob as text in a ``config_json`` column, so no schema
change is required when new sidebar parameters are added to
``analysis_parameters``. Round-trip coverage for both paths lives in
``tests/test_config_persistence.py``.

Main Backend Modules
--------------------

``api/main.py``
   FastAPI application, route definitions, upload handling, and response wiring.

``api/models.py``
   SQLAlchemy ORM models for users, recovery codes, uploaded FITS files, and
   related tracking tables.

``api/schemas.py``
   Pydantic request and response models used by the API.

``api/storage.py``
   User-scoped storage-path generation for uploaded FITS files.

``api/database.py``
   Database engine, session creation, and SQLAlchemy base setup.

``api/security.py``
   Authentication helpers and dependency wiring for protected routes.

``backend.py``
   Legacy Flask backend providing equivalent registration, login, recovery and
   configuration endpoints for compatibility with older deployments. Stores its
   data in SQLite (``users.db``) and requires ``flask`` / ``flask-cors``.

Programmatic Use
----------------

For frontend-integrated usage, the simplest client surface is the helper layer
in ``pages/api_client.py``. For broader codebase entry points, see
``api_reference.rst`` for the surrounding module map.