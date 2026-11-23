"""Redundant endpoints (mirrors) for the catalogue services.

CDS documents alternative servers while its Strasbourg site is degraded
(https://cds.unistra.fr/): SIMBAD and VizieR are mirrored at CfA Harvard and
IDIA (South Africa). The Gaia DR3 catalogue is also served by the Heidelberg
TAP mirror. Each helper below tries the primary endpoint first, then the
mirrors, so one unreachable service no longer fails a catalogue cross-match.

The CDS page also lists NAOJ and China-VO VizieR mirrors, but publishes them
over HTTP while astroquery hardcodes HTTPS, so they are only reachable through
the ASU-TSV helper at the end of this module.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np
import requests
from astropy.table import Table

# Shared limit for each request to SIMBAD, VizieR, and Gaia TAP.
CATALOG_HTTP_TIMEOUT_SECONDS = 30

# VizieR mirrors, primary first. The Harvard mirror is the first fallback
# because it is the one astroquery has historically used.
VIZIER_PRIMARY_SERVER = "vizier.cds.unistra.fr"
VIZIER_FALLBACK_SERVER = "vizier.cfa.harvard.edu"
VIZIER_SERVERS = (
    VIZIER_PRIMARY_SERVER,
    VIZIER_FALLBACK_SERVER,
    "vizier.idia.ac.za",
)

# VizieR mirrors reachable through the ASU-TSV interface. astroquery can only
# talk to HTTPS servers, so NAOJ and China-VO (HTTP-only, and listed by CDS as
# official mirrors) are reachable exclusively through this path.
VIZIER_TSV_SERVERS = (
    "https://vizier.cfa.harvard.edu",
    "https://vizier.idia.ac.za",
    "http://vizier.nao.ac.jp",
    "http://vizier.china-vo.org",
)
# Backwards-compatible name; all requests use the shared catalogue timeout.
VIZIER_TSV_TIMEOUT_SECONDS = CATALOG_HTTP_TIMEOUT_SECONDS

# SIMBAD mirrors, primary first (CDS, CfA Harvard, IDIA).
SIMBAD_PRIMARY_SERVER = "simbad.cds.unistra.fr"
SIMBAD_FALLBACK_SERVER = "simbad.cfa.harvard.edu"
SIMBAD_SERVERS = (
    SIMBAD_PRIMARY_SERVER,
    SIMBAD_FALLBACK_SERVER,
    "simbad.idia.ac.za",
)

# Gaia DR3 TAP endpoints. The ESA archive is primary; the Heidelberg mirror
# serves the same gaiadr3 tables, including the synthetic photometry table used
# by the cross-match.
GAIA_PRIMARY_TAP_SERVER = "https://gea.esac.esa.int/"
GAIA_FALLBACK_TAP_SERVER = "https://gaia.ari.uni-heidelberg.de/"
GAIA_TAP_SERVERS = (GAIA_PRIMARY_TAP_SERVER, GAIA_FALLBACK_TAP_SERVER)
# Backwards-compatible name; all requests use the shared catalogue timeout.
GAIA_HTTP_TIMEOUT_SECONDS = CATALOG_HTTP_TIMEOUT_SECONDS

_VIZIER_FALLBACK_LOCK = threading.Lock()
_GAIA_MIRROR_CLIENT = None
_GAIA_MIRROR_CLIENT_LOCK = threading.Lock()


def _server_order(primary: str, servers: tuple[str, ...]) -> tuple[str, ...]:
    """Return ``primary`` followed by the other known servers, without repeats."""
    ordered: list[str] = []
    for server in (primary, *servers):
        if isinstance(server, str) and server and server not in ordered:
            ordered.append(server)
    return tuple(ordered)


def query_vizier_with_fallback(
    vizier_client: object, method_name: str, *args: object, **kwargs: object
) -> tuple[object, bool]:
    """Query VizieR, retrying on the next mirror when one is unreachable.

    The client's ``VIZIER_SERVER`` is restored afterwards so a fallback is
    never silently kept for the following queries of the same client.
    """
    method = getattr(vizier_client, method_name)
    timeout = getattr(vizier_client, "TIMEOUT", CATALOG_HTTP_TIMEOUT_SECONDS)
    if timeout is None:
        timeout = CATALOG_HTTP_TIMEOUT_SECONDS
    vizier_client.TIMEOUT = min(timeout, CATALOG_HTTP_TIMEOUT_SECONDS)
    primary = getattr(vizier_client, "VIZIER_SERVER", VIZIER_PRIMARY_SERVER)
    if not isinstance(primary, str) or not primary:
        primary = VIZIER_PRIMARY_SERVER

    errors: list[str] = []
    try:
        for index, server in enumerate(_server_order(primary, VIZIER_SERVERS)):
            vizier_client.VIZIER_SERVER = server
            try:
                return method(*args, **kwargs), index > 0
            except Exception as error:  # noqa: BLE001 - external service failures
                errors.append(f"{server}: {error}")
    finally:
        vizier_client.VIZIER_SERVER = primary

    raise RuntimeError(
        "VizieR query failed on every mirror "
        f"({', '.join(_server_order(primary, VIZIER_SERVERS))}): " + "; ".join(errors)
    )


def get_vizier_catalog_with_fallback(
    catalogs_module: object, *args: object, **kwargs: object
) -> tuple[object, bool]:
    """Call stdpipe's VizieR helper with the shared timeout and mirror fallback."""
    errors: list[str] = []
    servers = VIZIER_SERVERS
    for index, server in enumerate(servers):
        with _VIZIER_FALLBACK_LOCK:
            original_vizier = catalogs_module.Vizier

            def configured_vizier(
                *factory_args: object,
                _server: str | None = server if index > 0 else None,
                _factory: Any = original_vizier,
                **factory_kwargs: object,
            ) -> object:
                if _server is not None:
                    factory_kwargs.setdefault("vizier_server", _server)
                client = _factory(*factory_args, **factory_kwargs)
                timeout = getattr(client, "TIMEOUT", CATALOG_HTTP_TIMEOUT_SECONDS)
                if timeout is None:
                    timeout = CATALOG_HTTP_TIMEOUT_SECONDS
                client.TIMEOUT = min(timeout, CATALOG_HTTP_TIMEOUT_SECONDS)
                return client

            try:
                catalogs_module.Vizier = configured_vizier
                return catalogs_module.get_cat_vizier(*args, **kwargs), index > 0
            except Exception as error:  # noqa: BLE001 - external service failures
                errors.append(f"{server}: {error}")
            finally:
                catalogs_module.Vizier = original_vizier

    raise RuntimeError(
        f"VizieR query failed on every mirror ({', '.join(VIZIER_SERVERS)}): "
        + "; ".join(errors)
    )


def _gaia_mirror_client() -> object:
    """Return the lazily created Gaia client for the Heidelberg TAP mirror."""
    global _GAIA_MIRROR_CLIENT
    if _GAIA_MIRROR_CLIENT is None:
        with _GAIA_MIRROR_CLIENT_LOCK:
            if _GAIA_MIRROR_CLIENT is None:
                from astroquery.gaia import GaiaClass

                _GAIA_MIRROR_CLIENT = GaiaClass(
                    gaia_tap_server=GAIA_FALLBACK_TAP_SERVER,
                    gaia_data_server=GAIA_FALLBACK_TAP_SERVER,
                    tap_server_context="",
                    data_server_context="",
                    # The mirror does not implement the ESA status-message
                    # endpoint queried by the default constructor.
                    show_server_messages=False,
                )
    return _GAIA_MIRROR_CLIENT


def _apply_gaia_http_timeout(gaia_client: object) -> None:
    """Apply a socket timeout to astroquery's TAP HTTP connections."""
    tap_connection = getattr(gaia_client, "_Tap__connHandler", None)
    connection_handler = getattr(
        tap_connection, "_TapConn__connectionHandler", None
    )
    if connection_handler is None:
        raise RuntimeError("Could not configure the Gaia TAP connection timeout.")
    if getattr(connection_handler, "_rpp_timeout_configured", False):
        return

    # astroquery's TAP connection handler creates http.client connections without
    # exposing a timeout argument.
    original_get_connection = connection_handler.get_connection

    def get_connection(
        *, ishttps: bool = False, cookie: Any = None, verbose: bool = False
    ) -> Any:
        connection = original_get_connection(
            ishttps=ishttps, cookie=cookie, verbose=verbose
        )
        connection.timeout = CATALOG_HTTP_TIMEOUT_SECONDS
        return connection

    connection_handler.get_connection = get_connection
    connection_handler._rpp_timeout_configured = True


def query_gaia_with_fallback(
    gaia_client: object, query: str, **kwargs: object
) -> tuple[object, bool]:
    """Launch a Gaia TAP job, retrying on the Heidelberg mirror."""
    try:
        _apply_gaia_http_timeout(gaia_client)
        return gaia_client.launch_job(query=query, **kwargs), False
    except Exception as primary_error:  # noqa: BLE001 - external service failures
        fallback_kwargs = dict(kwargs)
        # The mirror does not serve the gzip-compressed VOTable the ESA archive
        # returns by default.
        fallback_kwargs.setdefault("output_format", "votable")
        try:
            fallback_client = _gaia_mirror_client()
            _apply_gaia_http_timeout(fallback_client)
            return (
                fallback_client.launch_job(query=query, **fallback_kwargs),
                True,
            )
        except Exception as fallback_error:  # noqa: BLE001 - external service failures
            raise RuntimeError(
                f"Gaia TAP query failed on {GAIA_PRIMARY_TAP_SERVER}: "
                f"{primary_error}; the fallback mirror "
                f"{GAIA_FALLBACK_TAP_SERVER} also failed: {fallback_error}"
            ) from fallback_error


def _parse_vizier_asu_tsv(text: str) -> Table | None:
    """Parse a VizieR ASU-TSV answer into an astropy Table.

    The format is a ``#`` comment block, a tab-separated header, a units line,
    a line of dashes and the data rows. ``None`` is returned when the answer
    carries no table at all, which is how a degraded server replies; a header
    with zero data rows yields an empty table instead, so the caller can tell
    an unavailable catalogue from an uncovered field.
    """
    lines = [
        line for line in text.splitlines() if line.strip() and not line.startswith("#")
    ]
    if len(lines) < 2:
        return None

    header = [name.strip() for name in lines[0].split("\t")]
    first_data_line = None
    for index, line in enumerate(lines[1:], start=1):
        cells = [cell.strip() for cell in line.split("\t")]
        if cells and all(set(cell) <= {"-"} for cell in cells if cell):
            first_data_line = index + 1
            break
    if first_data_line is None:
        return None

    rows = [line.split("\t") for line in lines[first_data_line:] if line.strip()]
    columns = {}
    for position, name in enumerate(header):
        values = [row[position].strip() if position < len(row) else "" for row in rows]
        try:
            data = np.array(
                [float(value) if value else np.nan for value in values], dtype=float
            )
        except ValueError:
            data = np.array(values, dtype=str)
        columns[name or f"column{position}"] = data
    return Table(columns)


def query_vizier_asu_tsv(
    catalog: str,
    ra0: float,
    dec0: float,
    sr0: float,
    filters: dict[str, str] | None = None,
    servers: tuple[str, ...] = VIZIER_TSV_SERVERS,
    timeout: float = CATALOG_HTTP_TIMEOUT_SECONDS,
) -> tuple[Table, str]:
    """Query VizieR through the ASU-TSV interface of the mirrors.

    Unlike :func:`get_vizier_catalog_with_fallback`, which goes through
    astroquery's VOTable client, this uses the plain TSV interface and also
    reaches the HTTP-only NAOJ and China-VO mirrors. ``-out.all`` is requested
    because the explicit column selection astroquery sends (``-out=*``) is not
    answered with data by the current servers.

    Returns the catalogue table and the mirror that served it. The table is
    empty when the field is simply not covered; ``RuntimeError`` is raised when
    no mirror returns a table, so callers can report an unavailable service
    instead of treating it as an empty field.
    """
    payload = [
        f"-source={catalog}",
        "-out.all",
        "-out.max=unlimited",
        f"-c={ra0:.8f} {dec0:.8f}",
        f"-c.rd={sr0:.8f}",
    ]
    for column, expression in (filters or {}).items():
        payload.append(f"{column}={expression}")
    body = "\n".join(payload)

    errors: list[str] = []
    timeout = min(timeout, CATALOG_HTTP_TIMEOUT_SECONDS)
    for server in servers:
        url = server.rstrip("/") + "/viz-bin/asu-tsv"
        try:
            response = requests.post(url, data=body, timeout=timeout)
        except requests.exceptions.RequestException as error:
            errors.append(f"{server}: {error}")
            continue
        if response.status_code != 200:
            errors.append(f"{server}: HTTP {response.status_code}")
            continue
        table = _parse_vizier_asu_tsv(response.text)
        if table is None:
            errors.append(f"{server}: no catalogue table in the TSV answer")
            continue
        return table, server

    raise RuntimeError(
        "VizieR ASU-TSV query failed on every mirror ("
        + ", ".join(servers)
        + "): "
        + "; ".join(errors)
    )
