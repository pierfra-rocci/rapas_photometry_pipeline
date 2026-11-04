"""Fallback helpers for catalogue-service mirrors."""

from __future__ import annotations

import threading

VIZIER_PRIMARY_SERVER = "vizier.cds.unistra.fr"
VIZIER_FALLBACK_SERVER = "vizier.cfa.harvard.edu"
_VIZIER_FALLBACK_LOCK = threading.Lock()


def query_vizier_with_fallback(
    vizier_client: object, method_name: str, *args: object, **kwargs: object
) -> tuple[object, bool]:
    """Query VizieR's primary mirror, then retry on the Harvard mirror."""
    try:
        return getattr(vizier_client, method_name)(*args, **kwargs), False
    except Exception as primary_error:  # noqa: BLE001 - external service failures
        try:
            vizier_client.VIZIER_SERVER = VIZIER_FALLBACK_SERVER
            return getattr(vizier_client, method_name)(*args, **kwargs), True
        except Exception as fallback_error:
            raise RuntimeError(
                f"VizieR query failed on {VIZIER_PRIMARY_SERVER}: {primary_error}; "
                f"the fallback mirror {VIZIER_FALLBACK_SERVER} also failed: "
                f"{fallback_error}"
            ) from fallback_error


def get_vizier_catalog_with_fallback(
    catalogs_module: object, *args: object, **kwargs: object
) -> tuple[object, bool]:
    """Call stdpipe's VizieR helper, retrying it against the Harvard mirror."""
    try:
        return catalogs_module.get_cat_vizier(*args, **kwargs), False
    except Exception as primary_error:  # noqa: BLE001 - external service failures
        with _VIZIER_FALLBACK_LOCK:
            original_vizier = catalogs_module.Vizier

            def fallback_vizier(*factory_args: object, **factory_kwargs: object) -> object:
                factory_kwargs["vizier_server"] = VIZIER_FALLBACK_SERVER
                return original_vizier(*factory_args, **factory_kwargs)

            try:
                catalogs_module.Vizier = fallback_vizier
                return catalogs_module.get_cat_vizier(*args, **kwargs), True
            except Exception as fallback_error:
                raise RuntimeError(
                    f"VizieR query failed on {VIZIER_PRIMARY_SERVER}: {primary_error}; "
                    f"the fallback mirror {VIZIER_FALLBACK_SERVER} also failed: "
                    f"{fallback_error}"
                ) from fallback_error
            finally:
                catalogs_module.Vizier = original_vizier
