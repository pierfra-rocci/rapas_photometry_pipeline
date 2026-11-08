"""Cross-match detected sources with the public TNS (Transient Name Server).

The TNS stages the whole list of public transients as a CSV archive
(``tns_public_objects.csv.zip``) under
``https://www.wis-tns.org/sites/default/files/tns_public_objects/``. That file
is public: it is fetched with a plain ``GET`` carrying only the ``tns_marker``
user agent the TNS asks for, so no API key is needed. The TNS asks users to
cross-match against that staged file locally instead of running cone searches
through the Search API, and so does this module.

One copy of the catalogue is cached on disk and shared by every user of the
deployment: the first run of the day downloads it, the later ones read it, and
a copy older than 24 hours is downloaded again and overwritten in place. A lock
file serialises concurrent downloads, so only one session fetches the archive
while the others wait for it and reuse the result. A catalogue already
downloaded from another source can be pointed to with
``RPP_TNS_CATALOG_PATH`` (CSV or ZIP), and the cache directory (the shared
cache drive) can be changed with ``RPP_TNS_CACHE_DIR``. The name sent in the
guest marker can be set with ``RPP_TNS_MARKER_NAME``.

When a shared mirror of the TNS catalogue is available (the optional HTTP API
shipped with ``tns-mirror``, ``GET /v1/nearest``), setting
``RPP_TNS_MIRROR_URL`` makes the cross-match query it once per detected source
instead of reading the catalogue. The mirror is authoritative when it answers;
any missing URL, oversized field or failed query falls back to the daily cached
catalogue.

As a last resort, when the staged catalogue cannot be obtained (download
refused, network error) and TNS credentials are available, the TNS Search API
(``/api/get/search`` then ``/api/get/object``) is queried once with a cone
search covering the field. The sandbox server is used when
``RPP_TNS_SANDBOX`` is set to ``1``/``true``.

Transient positions are not always astrometrically accurate, so the match is
progressive: the tightest radius is tried first and the association is kept up
to 30 arcsec, always pairing a source with its closest TNS object.
"""

import io
import json
import os
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import astropy.units as u
import numpy as np
import pandas as pd
import requests
from astropy.coordinates import SkyCoord

try:
    from scipy.spatial import cKDTree
except ImportError:  # pragma: no cover - scipy ships with the pipeline deps
    cKDTree = None

# Staged archive holding the entire catalogue of public TNS objects. The file
# is public: no API key is needed, only the ``tns_marker`` user agent.
TNS_PUBLIC_OBJECTS_URL = (
    "https://www.wis-tns.org/sites/default/files/tns_public_objects/"
    "tns_public_objects.csv.zip"
)
# Older staging path, still announced by part of the TNS documentation.
TNS_PUBLIC_OBJECTS_FALLBACK_URL = (
    "https://www.wis-tns.org/system/files/tns_public_objects/"
    "tns_public_objects.csv.zip"
)
TNS_PUBLIC_OBJECTS_URLS = (
    TNS_PUBLIC_OBJECTS_URL,
    TNS_PUBLIC_OBJECTS_FALLBACK_URL,
)
# The archive is a few MB; a slow TNS day must not hang a run.
TNS_TIMEOUT_SECONDS = 120

# The staged files need no account, only a marker; this name is the contact
# sent in the guest marker.
TNS_MARKER_NAME_ENV = "RPP_TNS_MARKER_NAME"
TNS_GUEST_MARKER_NAME = "RPP pipeline"

# The TNS stages the catalogue once a day: one file is shared by every user and
# is downloaded again (and overwritten) once it is older than this.
TNS_CACHE_MAX_AGE_SECONDS = 24 * 3600
TNS_CACHE_FILENAME = "tns_public_objects.csv"
TNS_LOCK_FILENAME = "tns_public_objects.download.lock"
# Longest wait for another session's download, and the age after which a lock
# left behind by a crashed run is considered stale and stolen.
TNS_LOCK_WAIT_SECONDS = 300
TNS_LOCK_STALE_SECONDS = 600

# Progressive cross-match radii (arcsec) between a TNS object and a detected
# source: the tightest radius is tried first, then the next ones. TNS positions
# can be several arcsec off, so the loosest tier is wider than Astro-Colibri's.
TNS_MATCH_RADII_ARCSEC = (5.0, 10.0, 15.0, 20.0, 30.0)
TNS_MATCH_RADIUS_ARCSEC = max(TNS_MATCH_RADII_ARCSEC)
# Matches at or beyond this radius are loose and flagged with a WARNING
TNS_WARN_RADIUS_ARCSEC = 20.0
# Margin added to the field radius when selecting the TNS objects to consider
TNS_FIELD_MARGIN_DEG = 0.05

# Optional shared mirror: base URL of a ``tns-mirror`` HTTP API, queried once
# per detected source through ``GET {base}/v1/nearest``. When it answers, no
# catalogue is downloaded; any failure falls back to the daily catalogue.
TNS_MIRROR_URL_ENV = "RPP_TNS_MIRROR_URL"
TNS_MIRROR_TIMEOUT_SECONDS = 15
TNS_MIRROR_MAX_WORKERS = 8
# One request per source: above this count the daily catalogue is cheaper.
TNS_MIRROR_MAX_SOURCES = 500

# Search API fallback (``/api/get/search`` cone search over the field, then
# ``/api/get/object`` for the position and type of each object found).
TNS_API_URL = "https://www.wis-tns.org/api"
TNS_API_SANDBOX_URL = "https://sandbox.wis-tns.org/api"
TNS_API_SANDBOX_ENV = "RPP_TNS_SANDBOX"
TNS_API_TIMEOUT_SECONDS = 30
# The search API returns at most this many objects per page.
TNS_API_PAGE_SIZE = 50
# Each object costs one ``get/object`` call: larger fields are truncated.
TNS_API_MAX_OBJECTS = 100
# Longest wait (seconds) accepted to honour a rate-limit reset.
TNS_API_MAX_WAIT_SECONDS = 60

# Columns read from the staged CSV (the staged header has many more).
TNS_WANTED_COLUMNS = {
    "objid",
    "name_prefix",
    "name",
    "ra",
    "declination",
    "dec",
    "type",
    "redshift",
    "discoverydate",
    "discoverymag",
    "internal_names",
}

# Parsed catalogue reused across Streamlit reruns: {path: (mtime, frame)}
_TNS_FRAME_CACHE = {}

# ``frame.attrs`` key holding the KD-tree over the catalogue positions.
TNS_INDEX_ATTR = "tns_spatial_index"


def tns_cache_dir(cache_dir=None):
    """Return the directory holding the daily TNS catalogue."""
    if cache_dir is None:
        cache_dir = os.environ.get("RPP_TNS_CACHE_DIR") or os.path.join(
            tempfile.gettempdir(), "rpp_tns"
        )
    return str(cache_dir)


def tns_cache_dir_and_path(cache_dir=None):
    """Return the ``(directory, catalogue path)`` of the shared TNS cache."""
    directory = tns_cache_dir(cache_dir)
    return directory, os.path.join(directory, TNS_CACHE_FILENAME)


def tns_cache_path(day=None, cache_dir=None):
    """Return the shared cache file of the public TNS catalogue.

    A single file is kept and overwritten in place once it is more than 24
    hours old, so every user of the deployment reads the same catalogue.
    ``day`` is accepted for backward compatibility and ignored.
    """
    del day
    return tns_cache_dir_and_path(cache_dir)[1]


def tns_cache_age_seconds(path):
    """Return the age of a cached catalogue in seconds, or ``None`` if missing."""
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None


def tns_cache_is_fresh(path, max_age_seconds=TNS_CACHE_MAX_AGE_SECONDS):
    """Return True when ``path`` was downloaded less than ``max_age_seconds`` ago."""
    age = tns_cache_age_seconds(path)
    if age is None or age >= max_age_seconds:
        return False
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def tns_credentials():
    """Return the TNS credentials from the environment, or ``None``.

    The public catalogue is downloaded without them; they are only used by the
    Search API fallback (and to sign the download when a marker is configured).
    """
    api_key = (os.environ.get("TNS_API_KEY") or "").strip()
    if not api_key:
        return None
    bot_id = (os.environ.get("TNS_BOT_ID") or "").strip()
    bot_name = (os.environ.get("TNS_BOT_NAME") or "").strip()
    if bot_id and bot_name:
        return {"api_key": api_key, "tns_id": bot_id, "type": "bot", "name": bot_name}
    user_id = (os.environ.get("TNS_USER_ID") or "").strip()
    user_name = (os.environ.get("TNS_USER_NAME") or "").strip()
    if user_id and user_name:
        return {
            "api_key": api_key,
            "tns_id": user_id,
            "type": "user",
            "name": user_name,
        }
    return None


def tns_user_agent(credentials):
    """Build the ``tns_marker`` user agent required by the TNS download."""
    return 'tns_marker{"tns_id":%s,"type":"%s","name":"%s"}' % (
        credentials["tns_id"],
        credentials["type"],
        credentials["name"],
    )


def tns_download_marker(credentials=None):
    """Return the ``tns_marker`` user agent used to fetch the public catalogue.

    The staged files are public, so no API key is sent: a configured bot/user
    marker is reused when available, otherwise a guest marker names the
    deployment (``RPP_TNS_MARKER_NAME``).
    """
    credentials = credentials or tns_credentials()
    if credentials is not None:
        return tns_user_agent(credentials)
    name = (os.environ.get(TNS_MARKER_NAME_ENV) or "").strip()
    return 'tns_marker{"tns_id":0,"type":"guest","name":"%s"}' % (
        name or TNS_GUEST_MARKER_NAME
    )


def _tns_csv_text(content):
    """Return the CSV text stored in a TNS download (ZIP archive or plain CSV)."""
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [
                name for name in archive.namelist() if name.lower().endswith(".csv")
            ]
            if not members:
                raise ValueError("the archive contains no CSV file")
            with archive.open(members[0]) as handle:
                return handle.read().decode("utf-8", errors="replace")
    text = content.decode("utf-8", errors="replace")
    if "<html" in text[:500].lower():
        raise ValueError(
            "the server returned an HTML page instead of the catalogue "
            "(check the staging URL and the tns_marker user agent)"
        )
    return text


def _looks_like_tns_header(line):
    lowered = str(line).lower()
    return "objid" in lowered and "name_prefix" in lowered


def parse_tns_public_objects(text):
    """Parse the staged TNS CSV text into a DataFrame with numeric RA/Dec.

    The staged files start with a line holding the creation date, followed by
    the column header line. Files that start directly with the header are
    accepted too. Rows without usable coordinates are dropped.
    """
    text = str(text).lstrip("\ufeff")
    lines = text.splitlines()
    if len(lines) < 2:
        raise ValueError("the file has fewer than two lines")

    if _looks_like_tns_header(lines[1]):
        skiprows = 1
    elif _looks_like_tns_header(lines[0]):
        skiprows = 0
    else:
        raise ValueError("no TNS column header found in the first two lines")

    frame = pd.read_csv(
        io.StringIO(text),
        skiprows=skiprows,
        usecols=lambda column: str(column).strip().lower() in TNS_WANTED_COLUMNS,
        dtype=str,
        on_bad_lines="skip",
    )
    frame.columns = [str(column).strip().lower() for column in frame.columns]

    if "declination" in frame.columns and "dec" not in frame.columns:
        frame = frame.rename(columns={"declination": "dec"})
    for column in ("ra", "dec"):
        if column not in frame.columns:
            raise ValueError(f"the TNS catalogue has no '{column}' column")
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    valid = np.isfinite(frame["ra"]) & np.isfinite(frame["dec"])
    return _attach_spatial_index(frame[valid].reset_index(drop=True))


def _unit_vectors(ra_deg, dec_deg):
    """Return unit vectors for positions given in degrees."""
    ra = np.radians(np.asarray(ra_deg, dtype=float))
    dec = np.radians(np.asarray(dec_deg, dtype=float))
    cos_dec = np.cos(dec)
    return np.column_stack((cos_dec * np.cos(ra), cos_dec * np.sin(ra), np.sin(dec)))


def _attach_spatial_index(frame):
    """Attach a KD-tree over the catalogue positions to ``frame.attrs``.

    Selecting the objects of a field then walks the tree instead of comparing
    every row of the ~170k-object catalogue with the field centre. Without
    scipy, or for a frame the index cannot be built from, the frame is returned
    unchanged and :func:`tns_field_mask` falls back to the angular separation.
    """
    if cKDTree is None or len(frame) == 0:
        return frame
    try:
        frame.attrs[TNS_INDEX_ATTR] = cKDTree(_unit_vectors(frame["ra"], frame["dec"]))
    except Exception:  # pragma: no cover - defensive, the frame is validated
        frame.attrs.pop(TNS_INDEX_ATTR, None)
    return frame


def tns_spatial_index(frame):
    """Return the KD-tree attached to a catalogue frame, or None."""
    attrs = getattr(frame, "attrs", None)
    return attrs.get(TNS_INDEX_ATTR) if attrs is not None else None


def tns_field_mask(catalog, center, radius_deg):
    """Return a boolean mask of the catalogue rows within ``radius_deg`` of ``center``.

    The `spatial index<https://docs.scipy.org/doc/scipy/reference/generated/
    scipy.spatial.cKDTree.html>`_ attached at parse time is used when present;
    otherwise the angular separation of every row is computed, which yields the
    same mask.
    """
    radius_deg = float(radius_deg)
    tree = tns_spatial_index(catalog)
    if tree is not None:
        # A point at angular distance r is a chord 2 sin(r / 2) away from the
        # centre on the unit sphere; the tiny factor keeps the boundary
        # inclusive despite floating-point rounding.
        chord = 2.0 * np.sin(np.radians(radius_deg) / 2.0) * (1.0 + 1e-12)
        vector = _unit_vectors([center.ra.deg], [center.dec.deg])[0]
        mask = np.zeros(len(catalog), dtype=bool)
        hits = tree.query_ball_point(vector, chord)
        if hits:
            mask[np.asarray(hits, dtype=int)] = True
        return mask

    coords = SkyCoord(
        ra=catalog["ra"].to_numpy(dtype=float),
        dec=catalog["dec"].to_numpy(dtype=float),
        unit="deg",
    )
    return center.separation(coords).deg <= radius_deg


def download_tns_public_objects(
    credentials=None, url=None, timeout=TNS_TIMEOUT_SECONDS, urls=None
):
    """Download the staged TNS catalogue with a plain ``GET`` (no API key).

    Parameters
    ----------
    credentials : dict, optional
        Configured bot/user marker, used only to sign the request; the public
        catalogue downloads without it.
    url, urls : str or sequence of str, optional
        Override the staging URL(s); the known ones are tried in order.

    Returns
    -------
    tuple (str | None, list[str])
        ``(csv_text, messages)`` on success, ``(None, messages)`` when the
        download is not possible (network, HTML page or HTTP error).
    """
    candidates = [url] if url else list(urls or TNS_PUBLIC_OBJECTS_URLS)
    headers = {"User-Agent": tns_download_marker(credentials)}

    messages = []
    for candidate in candidates:
        try:
            response = requests.get(candidate, headers=headers, timeout=timeout)
        except Exception as error:
            messages.append(f"WARNING: TNS: download of {candidate} failed: {error}")
            continue

        status = getattr(response, "status_code", 200)
        if status != 200:
            messages.append(
                f"WARNING: TNS: download of {candidate} failed with HTTP {status}."
            )
            continue

        try:
            text = _tns_csv_text(getattr(response, "content", b"") or b"")
        except Exception as error:
            messages.append(
                f"WARNING: TNS: unreadable catalogue archive from {candidate}: {error}"
            )
            continue

        if not text.strip():
            messages.append(f"WARNING: TNS: {candidate} returned an empty catalogue.")
            continue

        return text, messages
    return None, messages


def tns_mirror_url():
    """Return the shared TNS mirror base URL from the environment, or None."""
    url = (os.environ.get(TNS_MIRROR_URL_ENV) or "").strip()
    return url or None


def tns_mirror_nearest_url(base_url):
    """Return the ``/v1/nearest`` endpoint of a mirror base URL."""
    return f"{str(base_url).strip().rstrip('/')}/v1/nearest"


def _mirror_lookup(mapping, keys):
    """Return the first present key of ``keys`` in a lower-cased mapping."""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _mirror_number(value):
    """Return ``value`` as a finite float, or None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def parse_tns_mirror_objects(payload):
    """Normalise a mirror JSON answer into catalogue-like object dicts.

    ``/v1/nearest`` returns the single closest object (or ``null``), but lists,
    ``{"objects": [...]}`` wrappers and the staged TNS column names are
    accepted too, so other mirror implementations can be used unchanged.
    Objects without a name or usable coordinates are dropped.
    """
    if payload is None:
        return []
    if isinstance(payload, dict):
        for key in ("objects", "results", "data", "matches", "nearest", "object"):
            if key in payload:
                return parse_tns_mirror_objects(payload[key])
        items = [payload]
    elif isinstance(payload, (list, tuple)):
        items = list(payload)
    else:
        return []

    objects = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fields = {str(key).strip().lower(): value for key, value in item.items()}
        ra = _mirror_number(_mirror_lookup(fields, ("ra", "right_ascension", "ra_deg")))
        dec = _mirror_number(_mirror_lookup(fields, ("dec", "declination", "dec_deg")))
        name = _clean_text(
            _mirror_lookup(
                fields, ("name", "designation", "objname", "object_name", "full_name")
            )
        )
        prefix = _clean_text(_mirror_lookup(fields, ("name_prefix", "prefix")))
        if prefix and name and not name.lower().startswith(prefix.lower()):
            designation = f"{prefix} {name}"
        else:
            designation = name or prefix
        if ra is None or dec is None or designation is None:
            continue
        objects.append(
            {
                "designation": designation,
                "ra": ra,
                "dec": dec,
                "type": _clean_text(
                    _mirror_lookup(fields, ("type", "object_type", "objtype"))
                ),
                "redshift": _clean_text(_mirror_lookup(fields, ("redshift", "z"))),
                "discoverydate": _clean_text(
                    _mirror_lookup(fields, ("discoverydate", "discovery_date"))
                ),
                "discoverymag": _clean_text(
                    _mirror_lookup(fields, ("discoverymag", "discovery_mag"))
                ),
            }
        )
    return objects


def query_tns_mirror(
    ra,
    dec,
    radius_arcsec=TNS_MATCH_RADIUS_ARCSEC,
    url=None,
    timeout=TNS_MIRROR_TIMEOUT_SECONDS,
):
    """Return the mirror objects within ``radius_arcsec`` of one position.

    Returns
    -------
    tuple (list[dict] | None, str)
        ``(objects, reason)``. An empty list is a valid answer meaning "no
        object"; ``None`` means the query failed and the caller should fall
        back to the daily catalogue.
    """
    base = (url or tns_mirror_url() or "").strip()
    if not base:
        return None, "no mirror URL configured"

    params = {
        "ra": round(float(ra), 7),
        "dec": round(float(dec), 7),
        "radius_arcsec": float(radius_arcsec),
    }
    try:
        response = requests.get(
            tns_mirror_nearest_url(base), params=params, timeout=timeout
        )
    except Exception as error:
        return None, f"mirror query failed: {error}"

    status = getattr(response, "status_code", 200)
    if status == 404:
        return [], ""
    if status != 200:
        return None, f"mirror query failed with HTTP {status}"

    try:
        payload = response.json()
    except Exception as error:
        return None, f"mirror returned an unreadable answer: {error}"
    return parse_tns_mirror_objects(payload), ""


def mirror_tns_objects(
    ra_values,
    dec_values,
    url=None,
    radius_arcsec=TNS_MATCH_RADIUS_ARCSEC,
    timeout=TNS_MIRROR_TIMEOUT_SECONDS,
    max_workers=TNS_MIRROR_MAX_WORKERS,
    max_sources=TNS_MIRROR_MAX_SOURCES,
):
    """Query a shared TNS mirror once per detected source.

    Returns
    -------
    tuple (pandas.DataFrame | None, list[str])
        A catalogue-like frame holding every object returned by the mirror (one
        entry per object, duplicates removed), or ``None`` when the mirror must
        not or cannot be used (no URL, too many sources, a failed query); the
        caller then falls back to the daily catalogue.
    """
    base = (url or tns_mirror_url() or "").strip()
    if not base:
        return None, []

    positions = []
    for ra_value, dec_value in zip(ra_values, dec_values):
        try:
            ra = float(ra_value)
            dec = float(dec_value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(ra) and np.isfinite(dec):
            positions.append((ra, dec))

    if not positions:
        return _mirror_frame([]), [
            "INFO: TNS: no source with valid coordinates to cross-match."
        ]

    if len(positions) > max_sources:
        return None, [
            f"INFO: TNS: {len(positions)} sources exceed the shared-mirror "
            f"limit of {max_sources}; using the daily catalogue instead."
        ]

    def _query(position):
        return query_tns_mirror(
            position[0],
            position[1],
            radius_arcsec=radius_arcsec,
            url=base,
            timeout=timeout,
        )

    workers = max(1, min(int(max_workers), len(positions)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(_query, positions))

    failures = [reason for objects, reason in results if objects is None]
    if failures:
        return None, [
            f"WARNING: TNS: shared mirror at {base} could not answer "
            f"{len(failures)} of {len(positions)} queries ({failures[0]}); "
            "using the daily catalogue instead."
        ]

    unique = {}
    for objects, _reason in results:
        for obj in objects:
            key = (obj["designation"], round(obj["ra"], 6), round(obj["dec"], 6))
            unique.setdefault(key, obj)
    rows = list(unique.values())

    answered = sum(1 for objects, _reason in results if objects)
    if rows:
        message = (
            f"INFO: TNS: shared mirror at {base}: {len(rows)} object(s) within "
            f"{radius_arcsec:g} arcsec of {answered} source(s) "
            f"({len(positions)} queried)."
        )
    else:
        message = (
            f"INFO: TNS: shared mirror at {base}: no object within "
            f"{radius_arcsec:g} arcsec of the {len(positions)} source(s)."
        )
    return _mirror_frame(rows), [message]


def _mirror_frame(rows):
    """Build a catalogue-like frame from normalised mirror objects."""
    return pd.DataFrame(
        {
            "name_prefix": [None for _ in rows],
            "name": [row["designation"] for row in rows],
            "ra": [row["ra"] for row in rows],
            "dec": [row["dec"] for row in rows],
            "type": [row["type"] for row in rows],
            "redshift": [row["redshift"] for row in rows],
            "discoverydate": [row["discoverydate"] for row in rows],
            "discoverymag": [row["discoverymag"] for row in rows],
        }
    )


def tns_api_base_url():
    """Return the TNS API base URL (sandbox when ``RPP_TNS_SANDBOX`` is set)."""
    flag = (os.environ.get(TNS_API_SANDBOX_ENV) or "").strip().lower()
    return TNS_API_SANDBOX_URL if flag in ("1", "true", "yes", "on") else TNS_API_URL


def _tns_api_post(endpoint, credentials, payload, timeout=TNS_API_TIMEOUT_SECONDS):
    """POST ``payload`` to a TNS API endpoint, honouring one rate-limit wait.

    Returns
    -------
    tuple (dict | None, str)
        ``(reply, reason)``; ``reply`` is the ``data.reply`` part of the JSON
        answer, or ``None`` when the call failed (``reason`` says why).
    """
    url = f"{tns_api_base_url()}/{endpoint}"
    request = {
        "headers": {"User-Agent": tns_user_agent(credentials)},
        "data": {"api_key": credentials["api_key"], "data": json.dumps(payload)},
        "timeout": timeout,
    }
    response = None
    for attempt in range(2):
        try:
            response = requests.post(url, **request)
        except Exception as error:
            return None, f"request failed: {error}"
        if getattr(response, "status_code", 200) != 429 or attempt:
            break
        # Rate limit reached: wait for the reset announced by the server.
        headers = getattr(response, "headers", None) or {}
        try:
            wait = int(headers.get("x-rate-limit-reset", headers.get("Retry-After")))
        except (TypeError, ValueError):
            return None, "rate limit reached"
        if wait > TNS_API_MAX_WAIT_SECONDS:
            return None, f"rate limit reached (reset in {wait} s)"
        time.sleep(wait + 1)

    status = getattr(response, "status_code", 200)
    if status != 200:
        return None, f"HTTP {status}"
    try:
        answer = response.json()
        reply = answer["data"]["reply"]
    except Exception:
        return None, "unreadable answer"
    return reply, ""


def search_tns_api(
    credentials, ra, dec, radius_arcsec, max_objects=TNS_API_MAX_OBJECTS
):
    """Cone-search the TNS Search API and list the object names found.

    Returns
    -------
    tuple (list[tuple[str, str]] | None, list[str])
        ``([(prefix, name), ...], messages)``, or ``(None, messages)`` when the
        search failed. At most ``max_objects`` names are returned.
    """
    names = []
    page = 0
    while len(names) < max_objects:
        payload = {
            "ra": f"{float(ra):.6f}",
            "dec": f"{float(dec):.6f}",
            "radius": f"{float(radius_arcsec):.1f}",
            "units": "arcsec",
            "num_page": TNS_API_PAGE_SIZE,
            "page": page,
        }
        reply, reason = _tns_api_post("get/search", credentials, payload)
        if reply is None:
            return None, [f"WARNING: TNS: Search API failed: {reason}."]
        if not isinstance(reply, list):
            break
        for item in reply:
            if not isinstance(item, dict):
                continue
            name = _clean_text(item.get("objname"))
            if name:
                names.append((_clean_text(item.get("prefix")), name))
        if len(reply) < TNS_API_PAGE_SIZE:
            break
        page += 1

    messages = []
    if len(names) >= max_objects:
        names = names[:max_objects]
        messages.append(
            f"WARNING: TNS: the Search API list reached the limit of "
            f"{max_objects} objects; further transients of the field may be "
            "missing."
        )
    return names, messages


def get_tns_api_object(credentials, name, prefix=None):
    """Return the normalised TNS object ``name`` from the API, or None."""
    reply, _reason = _tns_api_post(
        "get/object",
        credentials,
        {"objname": name, "photometry": "0", "spectra": "0"},
    )
    if not isinstance(reply, dict):
        return None

    object_type = reply.get("object_type")
    if isinstance(object_type, dict):
        object_type = object_type.get("name")
    payload = {
        "name": reply.get("objname") or name,
        "name_prefix": reply.get("name_prefix") or prefix,
        "ra": reply.get("radeg", reply.get("ra")),
        "dec": reply.get("decdeg", reply.get("dec")),
        "type": object_type,
        "redshift": reply.get("redshift"),
        "discoverydate": reply.get("discoverydate"),
        "discoverymag": reply.get("discoverymag"),
    }
    objects = parse_tns_mirror_objects(payload)
    return objects[0] if objects else None


def api_tns_objects(
    credentials, ra, dec, radius_deg, max_objects=TNS_API_MAX_OBJECTS
):
    """Query the TNS Search API for the objects of a field.

    One cone search lists the objects, then each is fetched for its position
    and type.

    Returns
    -------
    tuple (pandas.DataFrame | None, list[str])
        A catalogue-like frame of the objects found (empty when the field holds
        none), or ``None`` when the API cannot be used.
    """
    radius_arcsec = float(radius_deg) * 3600.0
    names, messages = search_tns_api(
        credentials, ra, dec, radius_arcsec, max_objects=max_objects
    )
    if names is None:
        return None, messages

    rows = []
    failed = 0
    for prefix, name in names:
        obj = get_tns_api_object(credentials, name, prefix=prefix)
        if obj is None:
            failed += 1
        else:
            rows.append(obj)
    if failed:
        messages.append(
            f"WARNING: TNS: could not fetch {failed} of {len(names)} objects "
            "listed by the Search API."
        )
    if names and not rows:
        return None, messages

    messages.append(
        f"INFO: TNS: Search API: {len(rows)} object(s) within "
        f"{radius_arcsec / 60.0:.1f} arcmin of the field centre."
    )
    return _mirror_frame(rows), messages


def _read_tns_file(path):
    with open(path, "rb") as handle:
        return _tns_csv_text(handle.read())


def _load_tns_frame(path):
    """Parse a cached catalogue file, reusing the last parse of that file."""
    try:
        mtime = os.path.getmtime(path)
    except OSError as error:
        return None, [f"WARNING: TNS: could not read the cached catalogue: {error}"]

    cached = _TNS_FRAME_CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1], []

    try:
        frame = parse_tns_public_objects(_read_tns_file(path))
    except Exception as error:
        return None, [f"WARNING: TNS: could not read the cached catalogue: {error}"]

    _TNS_FRAME_CACHE[path] = (mtime, frame)
    return frame, []


def _acquire_tns_download_lock(lock_path, wait_seconds=TNS_LOCK_WAIT_SECONDS):
    """Create ``lock_path`` exclusively, waiting for another session to finish.

    Returns True when the lock is held, False when it could not be obtained in
    time (the caller then downloads anyway rather than failing the run).
    """
    deadline = time.time() + wait_seconds
    while True:
        try:
            handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pass
        except OSError:
            # Unwritable directory: proceed without serialising the download.
            return False
        else:
            os.close(handle)
            return True

        # A crashed download can leave the lock behind: ignore it once stale.
        try:
            if time.time() - os.path.getmtime(lock_path) > TNS_LOCK_STALE_SECONDS:
                os.unlink(lock_path)
                continue
        except OSError:
            continue
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def _release_tns_download_lock(lock_path):
    """Remove a download lock, ignoring a lock already gone."""
    try:
        os.unlink(lock_path)
    except OSError:
        pass


def _clean_stale_tns_cache_files(directory, path):
    """Remove the lock and temp files a killed download left in the cache.

    A download abandoned mid-way (for instance a warm-up thread killed with the
    process) leaves ``tns_public_objects.csv.<pid>.tmp`` behind, and, if it died
    while holding it, the download lock too. Both are only removed once they are
    older than :data:`TNS_LOCK_STALE_SECONDS`, so a live download is never
    disturbed, and the catalogue itself is left untouched. Best-effort: a
    failure here must not cost the caller the cached catalogue.

    Returns
    -------
    list[str]
        The names of the files that were removed, so the caller can report them.
    """
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    temp_prefix = TNS_CACHE_FILENAME + "."
    now = time.time()
    removed = []
    for name in names:
        if name == TNS_LOCK_FILENAME:
            candidate = os.path.join(directory, name)
        elif name.startswith(temp_prefix) and name.endswith(".tmp"):
            candidate = os.path.join(directory, name)
        else:
            continue
        try:
            if now - os.path.getmtime(candidate) < TNS_LOCK_STALE_SECONDS:
                continue
            os.unlink(candidate)
        except OSError:
            continue
        removed.append(name)
    return removed


def load_tns_public_objects(
    cache_dir=None, catalog_path=None, credentials=None, download=True
):
    """Load the public TNS catalogue, refreshing the shared cache once a day.

    A single file is shared by every user of the deployment: the first run of
    the day downloads it, the later ones read it, and a copy older than 24
    hours is downloaded again and overwritten. The download is serialised by a
    lock file, so concurrent sessions wait for it instead of all fetching the
    archive.

    Returns
    -------
    tuple (pandas.DataFrame | None, list[str])
        The catalogue and the log messages describing which source was used
        (cache, local file or fresh download) or why it is unavailable.
    """
    log_messages = []
    explicit_path = catalog_path or os.environ.get("RPP_TNS_CATALOG_PATH")
    if explicit_path:
        if not os.path.exists(explicit_path):
            return None, [f"WARNING: TNS: catalogue file not found: {explicit_path}"]
        frame, messages = _load_tns_frame(str(explicit_path))
        if frame is not None:
            log_messages.append(
                f"INFO: TNS: loaded {len(frame)} public objects from "
                f"{explicit_path}."
            )
        return frame, log_messages + messages

    directory, path = tns_cache_dir_and_path(cache_dir)
    # Clear what a killed download left behind, even when the cached catalogue
    # is still fresh and no download happens in this run.
    removed = _clean_stale_tns_cache_files(directory, path)
    if removed:
        log_messages.append(
            f"INFO: TNS: removed stale cache file(s) from an interrupted "
            f"download: {', '.join(sorted(removed))}."
        )

    def _use_cached_catalogue(reason):
        """Load the shared copy and log it, or return ``(None, [])``."""
        frame, messages = _load_tns_frame(path)
        if frame is None:
            return None, messages
        return frame, [
            f"INFO: TNS: {reason} ({len(frame)} objects)."
        ]

    if tns_cache_is_fresh(path):
        frame, messages = _use_cached_catalogue("using the shared cached public catalogue")
        if frame is not None:
            return frame, log_messages + messages
        log_messages += messages

    if not download:
        if os.path.exists(path):
            frame, messages = _use_cached_catalogue("using the cached public catalogue")
            if frame is not None:
                return frame, log_messages + messages
            log_messages += messages
        return None, log_messages + [
            "WARNING: TNS: no cached catalogue available; skipping the TNS "
            "cross-match."
        ]

    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as error:
        log_messages.append(
            f"WARNING: TNS: could not create the catalogue cache directory: {error}"
        )

    lock_path = os.path.join(directory, TNS_LOCK_FILENAME)
    locked = _acquire_tns_download_lock(lock_path)
    if not locked:
        log_messages.append(
            "WARNING: TNS: could not lock the catalogue cache; downloading "
            "without waiting for the other sessions."
        )
    try:
        # Another session may have refreshed the file while we waited.
        if tns_cache_is_fresh(path):
            frame, messages = _use_cached_catalogue(
                "another session downloaded the public catalogue; using it"
            )
            if frame is not None:
                return frame, log_messages + messages
            log_messages += messages

        text, messages = download_tns_public_objects(credentials=credentials)
        log_messages += messages
        if text is None:
            # Rather than losing the cross-match for the rest of the day, keep
            # using the previous copy until the TNS can be reached again.
            if os.path.exists(path):
                frame, messages = _use_cached_catalogue(
                    "the catalogue could not be refreshed; using the cached "
                    "public catalogue"
                )
                if frame is not None:
                    age = tns_cache_age_seconds(path) or 0.0
                    log_messages += messages
                    log_messages.append(
                        f"WARNING: TNS: the cached catalogue is "
                        f"{age / 3600.0:.1f} h old; it will be refreshed on the "
                        "next run."
                    )
                    return frame, log_messages
                log_messages += messages
            return None, log_messages + [
                "WARNING: TNS: the public catalogue could not be downloaded; "
                "skipping the TNS cross-match."
            ]

        try:
            frame = parse_tns_public_objects(text)
        except Exception as error:
            return None, log_messages + [
                f"WARNING: TNS: could not parse the public catalogue: {error}"
            ]

        try:
            # Write next to the cache file, then replace it: readers never see a
            # half-written catalogue and yesterday's copy is overwritten.
            temp_path = f"{path}.{os.getpid()}.tmp"
            with open(temp_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            os.replace(temp_path, path)
            # Reuse the frame just parsed instead of re-reading the new file.
            _TNS_FRAME_CACHE[path] = (os.path.getmtime(path), frame)
        except OSError as error:
            log_messages.append(
                f"WARNING: TNS: could not cache the catalogue: {error}"
            )

        log_messages.append(
            f"INFO: TNS: downloaded the public catalogue ({len(frame)} objects)."
        )
        return frame, log_messages
    finally:
        if locked:
            _release_tns_download_lock(lock_path)


def tns_match_radius(separation_arcsec, radii=TNS_MATCH_RADII_ARCSEC):
    """Return the smallest radius in ``radii`` containing the separation."""
    for radius in sorted(radii):
        if separation_arcsec < radius:
            return radius
    return None


def _clean_text(value):
    """Return ``value`` as stripped text, or None when missing/invalid."""
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    text = str(value).strip()
    if text.lower() in ("", "nan", "none"):
        return None
    return text


def _tns_designation(row):
    """Return the full TNS designation (``"SN 2026abc"``) of a catalogue row."""
    name = _clean_text(row.get("name"))
    prefix = _clean_text(row.get("name_prefix"))
    if prefix and name:
        return f"{prefix} {name}"
    return name or prefix


def _column_or_none(frame, column):
    if column in frame.columns:
        return frame[column].tolist()
    return [None] * len(frame)


def cross_match_tns(
    table,
    valid_coords_mask,
    ra_center=None,
    dec_center=None,
    field_width_arcmin=None,
    cache_dir=None,
    catalog_path=None,
    credentials=None,
):
    """Cross-match a photometry table with the public TNS objects in the field.

    Each TNS object is paired with its closest detected source; several objects
    landing on the same source keep the closest one. The match is progressive
    between 5 and 30 arcsec, and associations found only at 20 arcsec or more
    are reported with a WARNING.

    Parameters
    ----------
    table : pandas.DataFrame
        Photometry catalog with ``ra``/``dec`` columns.
    valid_coords_mask : pandas.Series
        Boolean mask selecting the rows with usable coordinates.
    ra_center, dec_center : float
        Field centre in degrees.
    field_width_arcmin : float, optional
        Field size, used to discard TNS objects outside the image.
    cache_dir : str, optional
        Override the TNS catalogue cache directory.
    catalog_path : str, optional
        Use an already downloaded CSV/ZIP catalogue instead of the daily cache.
    credentials : dict, optional
        TNS credentials for the Search API fallback; read from the environment
        when omitted. The public catalogue needs none.

    A shared mirror configured through ``RPP_TNS_MIRROR_URL`` is queried first
    and replaces the catalogue whenever it answers; otherwise the daily cached
    catalogue is used, as before.

    Returns
    -------
    tuple (pandas.DataFrame, list[str])
        The table with ``tns_*`` columns filled (they are only added once the
        catalogue is available, so an unavailable TNS leaves the exported
        columns untouched) and the log messages.
    """
    log_messages = []
    if table is None or len(table) == 0:
        return table, ["WARNING: TNS: no sources to cross-match."]
    if ra_center is None or dec_center is None:
        return table, [
            "WARNING: TNS: no field centre available; skipping the TNS " "cross-match."
        ]

    try:
        center = SkyCoord(ra=float(ra_center), dec=float(dec_center), unit="deg")
    except (TypeError, ValueError) as error:
        return table, [
            f"WARNING: TNS: invalid field centre ({error}); skipping the TNS "
            "cross-match."
        ]

    if "ra" not in table.columns or "dec" not in table.columns:
        return table, [
            "WARNING: TNS: the photometry table has no RA/Dec columns; "
            "skipping the TNS cross-match."
        ]

    try:
        valid_table = table[valid_coords_mask]
    except (KeyError, IndexError):
        valid_table = table

    # A configured shared mirror answers for the detected sources directly, so
    # the catalogue download is skipped; any failure falls back to it.
    catalog = None
    from_mirror = False
    mirror_url = tns_mirror_url() if len(valid_table) else None
    if mirror_url:
        try:
            source_ra = valid_table["ra"].to_numpy(dtype=float)
            source_dec = valid_table["dec"].to_numpy(dtype=float)
        except (TypeError, ValueError):
            source_ra = np.array([], dtype=float)
            source_dec = np.array([], dtype=float)
        catalog, messages = mirror_tns_objects(source_ra, source_dec, url=mirror_url)
        log_messages += messages
        from_mirror = catalog is not None

    if catalog is None:
        catalog, messages = load_tns_public_objects(
            cache_dir=cache_dir,
            catalog_path=catalog_path,
            credentials=credentials,
        )
        log_messages += messages
        if catalog is None:
            # The staged catalogue is unavailable: try the Search API, unless a
            # local catalogue was explicitly requested.
            credentials = credentials or tns_credentials()
            explicit = catalog_path or os.environ.get("RPP_TNS_CATALOG_PATH")
            if credentials is None or explicit:
                return table, log_messages
            try:
                field_radius_deg = (
                    max(0.0, float(field_width_arcmin or 35.0) / 2.0) / 60.0
                ) + TNS_FIELD_MARGIN_DEG
            except (TypeError, ValueError):
                return table, log_messages
            log_messages.append("INFO: TNS: querying the Search API instead.")
            catalog, messages = api_tns_objects(
                credentials, center.ra.deg, center.dec.deg, field_radius_deg
            )
            log_messages += messages
            if catalog is None:
                return table, log_messages
            from_mirror = True
        elif len(catalog) == 0:
            return table, log_messages + [
                "WARNING: TNS: the public catalogue is empty."
            ]

    # Objects from the mirror or the Search API are already restricted to the
    # sources or the field, so no field selection is needed.
    if from_mirror:
        if len(catalog) == 0:
            return table, log_messages + [
                f"INFO: TNS: no source matched within "
                f"{TNS_MATCH_RADIUS_ARCSEC:g} arcsec."
            ]
        objects = catalog.reset_index(drop=True)
        try:
            objects_coords = SkyCoord(
                ra=objects["ra"].to_numpy(dtype=float),
                dec=objects["dec"].to_numpy(dtype=float),
                unit="deg",
            )
        except Exception as error:
            return table, log_messages + [
                f"WARNING: TNS: could not read the mirror answer: {error}"
            ]
    else:
        try:
            field_radius_deg = (
                max(0.0, float(field_width_arcmin or 35.0) / 2.0) / 60.0
            ) + TNS_FIELD_MARGIN_DEG
            in_field = tns_field_mask(catalog, center, field_radius_deg)
            objects = catalog[in_field].reset_index(drop=True)
            objects_coords = SkyCoord(
                ra=objects["ra"].to_numpy(dtype=float),
                dec=objects["dec"].to_numpy(dtype=float),
                unit="deg",
            )
        except Exception as error:
            return table, log_messages + [
                f"WARNING: TNS: could not select the objects in the field: {error}"
            ]

        log_messages.append(
            f"INFO: TNS: {len(objects)} public transient(s) within "
            f"{field_radius_deg * 60.0:.1f} arcmin of the field centre."
        )
        if len(objects) == 0:
            return table, log_messages

    table = table.copy()
    for column, default in (
        ("tns_name", None),
        ("tns_type", None),
        ("tns_ra", np.nan),
        ("tns_dec", np.nan),
        ("tns_separation_arcsec", np.nan),
    ):
        table[column] = default

    objects_view = pd.DataFrame(
        {
            "designation": [_tns_designation(row) for _, row in objects.iterrows()],
            "ra": objects["ra"].to_numpy(),
            "dec": objects["dec"].to_numpy(),
            "type": _column_or_none(objects, "type"),
            "redshift": _column_or_none(objects, "redshift"),
            "discoverydate": _column_or_none(objects, "discoverydate"),
            "discoverymag": _column_or_none(objects, "discoverymag"),
            "matched": False,
            "separation_arcsec": np.nan,
        }
    )

    if len(valid_table) == 0:
        table.attrs["tns_objects"] = objects_view
        log_messages.append(
            "INFO: TNS: no source with valid coordinates to cross-match."
        )
        return table, log_messages

    try:
        source_coords = SkyCoord(
            ra=valid_table["ra"].to_numpy(dtype=float),
            dec=valid_table["dec"].to_numpy(dtype=float),
            unit="deg",
        )
        indices, separations, _ = objects_coords.match_to_catalog_sky(source_coords)
    except Exception as error:
        table.attrs["tns_objects"] = objects_view
        return table, log_messages + [f"WARNING: TNS: cross-match failed: {error}"]

    within = separations < TNS_MATCH_RADIUS_ARCSEC * u.arcsec

    # Several objects may land on the same source: keep the closest one.
    valid_indices = list(valid_table.index)
    best = {}
    for object_i, source_i in enumerate(indices):
        if not bool(within[object_i]):
            continue
        separation = float(separations[object_i].arcsec)
        original_idx = valid_indices[int(source_i)]
        if original_idx in best and best[original_idx][0] <= separation:
            continue
        best[original_idx] = (separation, object_i)

    for original_idx, (separation, object_i) in best.items():
        row = objects_view.iloc[object_i]
        table.loc[original_idx, "tns_name"] = row["designation"]
        table.loc[original_idx, "tns_type"] = row["type"]
        table.loc[original_idx, "tns_ra"] = float(row["ra"])
        table.loc[original_idx, "tns_dec"] = float(row["dec"])
        table.loc[original_idx, "tns_separation_arcsec"] = separation
        objects_view.at[object_i, "matched"] = True
        objects_view.at[object_i, "separation_arcsec"] = separation

    if best:
        if "catalog_matches" not in table.columns:
            table["catalog_matches"] = ""
        has_tns = table["tns_name"].notna()
        table.loc[has_tns, "catalog_matches"] = (
            table.loc[has_tns, "catalog_matches"].fillna("").astype(str) + "TNS; "
        )

        radius_counts = {radius: 0 for radius in TNS_MATCH_RADII_ARCSEC}
        for separation, _ in best.values():
            tier = tns_match_radius(separation)
            if tier is not None:
                radius_counts[tier] += 1
        tiers = ", ".join(
            f'{radius:g}" x{count}' for radius, count in radius_counts.items() if count
        )
        log_messages.append(
            f"INFO: TNS: {len(best)} source(s) matched within "
            f"{TNS_MATCH_RADIUS_ARCSEC:g} arcsec"
            + (f" (radius reached: {tiers})." if tiers else ".")
        )
        for radius, count in radius_counts.items():
            if count and radius >= TNS_WARN_RADIUS_ARCSEC:
                log_messages.append(
                    f"WARNING: TNS: {count} source(s) matched only at the "
                    f"{radius:g} arcsec tier; the association is loose, please "
                    "check it visually."
                )
    else:
        log_messages.append(
            f"INFO: TNS: no source matched within "
            f"{TNS_MATCH_RADIUS_ARCSEC:g} arcsec."
        )

    table.attrs["tns_objects"] = objects_view
    return table, log_messages
