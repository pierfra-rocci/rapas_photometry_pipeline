"""Redundant endpoints (mirrors) for the catalogue services.

CDS documents alternative servers while its Strasbourg site is degraded
(https://cds.unistra.fr/): SIMBAD and VizieR are mirrored at CfA Harvard and
IDIA (South Africa). The Gaia DR3 catalogue is also served by the Heidelberg
TAP mirror. Each helper below tries the primary endpoint first, then the
mirrors, so one unreachable service no longer fails a catalogue cross-match.

The CDS page also lists NAOJ and China-VO VizieR mirrors, but publishes them
over HTTP while astroquery hardcodes HTTPS, so they are not used here.
"""

from __future__ import annotations

import threading

# VizieR mirrors, primary first. The Harvard mirror is the first fallback
# because it is the one astroquery has historically used.
VIZIER_PRIMARY_SERVER = "vizier.cds.unistra.fr"
VIZIER_FALLBACK_SERVER = "vizier.cfa.harvard.edu"
VIZIER_SERVERS = (
    VIZIER_PRIMARY_SERVER,
    VIZIER_FALLBACK_SERVER,
    "vizier.idia.ac.za",
)

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
        f"({', '.join(_server_order(primary, VIZIER_SERVERS))}): "
        + "; ".join(errors)
    )


def get_vizier_catalog_with_fallback(
    catalogs_module: object, *args: object, **kwargs: object
) -> tuple[object, bool]:
    """Call stdpipe's VizieR helper, retrying it against the next mirror."""
    try:
        return catalogs_module.get_cat_vizier(*args, **kwargs), False
    except Exception as primary_error:  # noqa: BLE001 - external service failures
        errors = [f"{VIZIER_PRIMARY_SERVER}: {primary_error}"]

    # Only the mirror attempts swap the module's Vizier factory, so the lock is
    # taken per attempt and the primary query above runs concurrently.
    for server in VIZIER_SERVERS[1:]:
        with _VIZIER_FALLBACK_LOCK:
            original_vizier = catalogs_module.Vizier

            def fallback_vizier(
                *factory_args: object,
                _server: str = server,
                **factory_kwargs: object,
            ) -> object:
                factory_kwargs.setdefault("vizier_server", _server)
                return original_vizier(*factory_args, **factory_kwargs)

            try:
                catalogs_module.Vizier = fallback_vizier
                return catalogs_module.get_cat_vizier(*args, **kwargs), True
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


def query_gaia_with_fallback(
    gaia_client: object, query: str, **kwargs: object
) -> tuple[object, bool]:
    """Launch a Gaia TAP job, retrying on the Heidelberg mirror."""
    try:
        return gaia_client.launch_job(query=query, **kwargs), False
    except Exception as primary_error:  # noqa: BLE001 - external service failures
        fallback_kwargs = dict(kwargs)
        # The mirror does not serve the gzip-compressed VOTable the ESA archive
        # returns by default.
        fallback_kwargs.setdefault("output_format", "votable")
        try:
            return (
                _gaia_mirror_client().launch_job(query=query, **fallback_kwargs),
                True,
            )
        except Exception as fallback_error:  # noqa: BLE001 - external service failures
            raise RuntimeError(
                f"Gaia TAP query failed on {GAIA_PRIMARY_TAP_SERVER}: "
                f"{primary_error}; the fallback mirror "
                f"{GAIA_FALLBACK_TAP_SERVER} also failed: {fallback_error}"
            ) from fallback_error
