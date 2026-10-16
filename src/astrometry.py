import os
import shutil
import subprocess
import tempfile

import streamlit as st

import numpy as np

import astropy.units as u
from astropy.table import Table
from astropy.wcs import WCS
from astropy.io import fits
from astropy.coordinates import SkyCoord
from scipy.stats import chi2

from src.pipeline import resolve_effective_gain

# stdpipe imports
STDPIPE_IMPORT_ERROR = None

try:
    from stdpipe import astrometry
    from stdpipe import photometry
    from stdpipe import catalogs
    from stdpipe import pipeline as stdpipe_pipeline
except ModuleNotFoundError as exc:
    astrometry = None
    photometry = None
    catalogs = None
    stdpipe_pipeline = None
    STDPIPE_IMPORT_ERROR = exc

try:
    # Only used to convert an initial SIP solution to the TPV convention that
    # SCAMP is able to read; the WCS refinement itself runs the SCAMP binary.
    import sip_tpv
except Exception:  # noqa: BLE001 - pkg_resources may raise DistributionNotFound
    sip_tpv = None


def _get_online_astrometry_api_key():
    """Return the configured Astrometry.net key without exposing it in logs."""
    api_key = os.environ.get("ASTROMETRY_API_KEY")
    if api_key:
        return api_key

    try:
        from astroquery.astrometry_net import conf

        return conf.api_key or None
    except Exception:
        return None


def get_adaptive_min_sources(image_shape):
    """Calculate minimum sources based on image size
    
    Larger images can have fewer sources per area and still provide
    good plate solving constraints. This function adapts the minimum
    source requirement based on image dimensions.
    """
    height, width = image_shape
    image_area = height * width

    if image_area > 10_000_000:  # Large images (>10MP)
        return max(30, min(50, int(image_area / 200_000)))
    elif image_area > 1_000_000:  # Medium images (1-10MP)
        return max(20, min(30, int(image_area / 50_000)))
    else:  # Small images (<1MP)
        return max(10, min(25, int(image_area / 20_000)))


def estimate_pixel_scale_robust(header, image_shape):
    """Robust pixel scale estimation with multiple methods and validation
    
    This function tries multiple methods to estimate pixel scale and returns
    the most reliable estimate found. Methods are tried in order of preference.
    """
    methods = []

    # Method 1: Direct header keywords
    for key in ["PIXSCALE", "PIXSIZE", "SECPIX"]:
        if key in header:
            try:
                scale = float(header[key])
                if 0.1 <= scale <= 30.0:  # Reasonable range
                    methods.append(("header_" + key, scale))
            except Exception:
                pass

    # Method 2: Focal length + pixel size
    try:
        focal_length = None
        pixel_size = None
        for f_key in ["FOCALLEN", "FOCAL", "FOCLEN", "FL"]:
            if f_key in header:
                focal_length = float(header[f_key])
                break
        for p_key in ["PIXELSIZE", "PIXSIZE", "XPIXSZ", "YPIXSZ"]:
            if p_key in header:
                pixel_size = float(header[p_key])
                break
        if focal_length and pixel_size:
            scale = 206 * pixel_size / focal_length
            if 0.1 <= scale <= 30.0:
                methods.append(("focal_length", scale))
    except Exception:
        pass

    # Method 3: CD matrix
    try:
        cd_values = [header.get(f"CD{i}_{j}", 0) for i in [1,2] for j in [1,2]]
        if any(x != 0 for x in cd_values):
            cd11, cd12, cd21, cd22 = cd_values
            det = abs(cd11 * cd22 - cd12 * cd21)
            scale = 3600 * np.sqrt(det)
            if 0.1 <= scale <= 30.0:
                methods.append(("cd_matrix", scale))
    except Exception:
        pass

    # Method 4: Image size heuristic
    try:
        height, width = image_shape
        # Typical pixel scales for different image sizes
        if max(height, width) > 5000:  # Large images
            methods.append(("size_heuristic", 1.5))
        elif max(height, width) > 2000:  # Medium images
            methods.append(("size_heuristic", 2.5))
        else:  # Small images
            methods.append(("size_heuristic", 4.0))
    except Exception:
        pass

    # Select best estimate
    if methods:
        # Prefer header direct values, then calculated, then heuristics
        preferred_order = ["header_", "focal_length", "cd_matrix", "size_heuristic"]
        for pref in preferred_order:
            for method, scale in methods:
                if method.startswith(pref):
                    return scale

        # Fallback to first valid method
        return methods[0][1]

    return None  # No estimate found


def solve_with_enhanced_strategy(sources, header, image_data):
    """Multi-stage plate solving with progressive constraints
    
    This function implements a multi-stage approach to plate solving:
    1. Broad search with minimal constraints
    2. Narrow search with better pixel scale constraints
    3. Targeted search with RA/DEC hints if available
    """
    log_messages = []
    
    # Get pixel scale estimate for constraints
    pixel_scale = estimate_pixel_scale_robust(header, image_data.shape)
    
    # Stage 1: Broad search with minimal constraints
    kwargs_broad = {
        "order": 3,
        "scale_lower": 0.5,
        "scale_upper": 15.0,
        "scale_units": "arcsecperpix",
        "radius": 10.0,  # 10 degree radius
        "verbose": True
    }
    log_messages.append("INFO: Trying broad plate solving (wide constraints)")
    
    # Stage 2: Narrow search with better constraints
    kwargs_narrow = kwargs_broad.copy()
    if pixel_scale:
        kwargs_narrow.update({
            "scale_lower": pixel_scale * 0.7,
            "scale_upper": pixel_scale * 1.3,
            "radius": 5.0  # 5 degree radius
        })
        log_messages.append(f"INFO: Using pixel scale estimate: {pixel_scale:.2f} arcsec/pixel")
    else:
        log_messages.append("INFO: No pixel scale estimate available, using broad range")

    # Stage 3: Targeted search with RA/DEC hint
    kwargs_targeted = kwargs_narrow.copy()
    if header and "RA" in header and "DEC" in header:
        try:
            ra_hint = float(header["RA"])
            dec_hint = float(header["DEC"])
            if 0 <= ra_hint <= 360 and -90 <= dec_hint <= 90:
                kwargs_targeted.update({
                    "center_ra": ra_hint,
                    "center_dec": dec_hint,
                    "radius": 2.0  # 2 degree radius
                })
                log_messages.append(
                    f"INFO: Using RA/DEC hint: {ra_hint:.3f}, {dec_hint:.3f}"
                )
        except Exception:
            log_messages.append("WARNING: Could not parse RA/DEC from header")

    # Try stages in order
    for stage_name, kwargs in [
        ("broad", kwargs_broad),
        ("narrow", kwargs_narrow),
        ("targeted", kwargs_targeted)
    ]:
        try:
            log_messages.append(f"INFO: Trying {stage_name} plate solving")
            solved_wcs = astrometry.blind_match_objects(sources, **kwargs)
            if solved_wcs is not None:
                log_messages.append(f"SUCCESS: {stage_name} solving successful")
                return solved_wcs, log_messages
        except Exception as e:
            log_messages.append(f"WARNING: {stage_name} solving failed: {e}")

    log_messages.append("ERROR: All plate solving stages failed")
    return None, log_messages


def _try_source_detection_improved(image, header, min_sources=10):
    """Progressive parameter grid with adaptive thresholds
    This improved version tries a more strategic sequence of parameters
    that starts with the most likely values first, then progressively
    tries more aggressive parameters.
    """
    # Start with most likely parameters first
    parameter_grid = [
        # (FWHM, threshold, description)
        (3.5, 3.0, "Standard parameters"),
        (3.5, 2.0, "Slightly more sensitive"),
        (3.0, 2.0, "Tighter FWHM, lower threshold"),
        (2.5, 1.5, "More aggressive"),
        (2.0, 1.0, "Very aggressive"),
        (4.0, 2.0, "Large FWHM"),
        (4.0, 1.5, "Large FWHM, lower threshold"),
        (2.8, 1.3, "Medium aggressive"),
    ]

    for fwhm_est, thresh, desc in parameter_grid:
        try:
            # Resolve the effective gain (e-/ADU) exactly like the photometry
            # error model: explicit EGAIN/CCDGAIN/CVF keywords first, driver-
            # gain cameras (QHY/ZWO) recognised so their abstract GAIN setting
            # is not misused. Replaces the old 65535 / max(image) heuristic.
            gain = resolve_effective_gain(header, image, quiet=True)

            sources = photometry.get_objects_sextractor(
                image,
                thresh=thresh,
                aper=1.5 * fwhm_est,
                gain=gain,
                edge=10,
                bg_size=64,
            )

            if sources is not None and len(sources) >= min_sources:
                # Measure objects
                sources = photometry.measure_objects(
                    sources,
                    image,
                    fwhm=fwhm_est,
                    aper=1,
                    bkgann=[2.0, 3.0],
                    verbose=False,
                    sn=5.0,
                )

                if sources is not None and len(sources) >= min_sources:
                    st.success(
                        f"Found {len(sources)} sources with "
                        f"thresh={thresh}, FWHM={fwhm_est}"
                    )
                    return sources

            if sources is not None:
                st.write(
                    f"Found {len(sources)} sources (need at least {min_sources})"
                )

        except Exception as e:
            st.error(f"Detection failed with FWHM={fwhm_est} : {e}")
            continue

    return None


# ---------------------------------------------------------------------------
# Direct SCAMP-based WCS refinement
# ---------------------------------------------------------------------------
#
# The initial blind solution is refined against a local reference catalogue by
# running the SCAMP *executable* directly, instead of routing through
# ``stdpipe.pipeline.refine_astrometry`` (which only wraps the same binary).
# Driving the binary ourselves lets us inspect its XML report and the header it
# produces, and to independently re-measure the match residuals before the
# refined solution is trusted by the rest of the pipeline.

# Explicit path to the SCAMP binary; when unset the executable is looked up on
# PATH and in the usual Astromatic locations.
SCAMP_EXECUTABLE_ENV = "RPP_SCAMP_EXECUTABLE"
SCAMP_COMMON_PATHS = (
    "/usr/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    "/usr/local/astrometry/bin",
    "/opt/local/astrometry/bin",
)

# Validation criteria for a trustworthy SCAMP solution (see
# :func:`_check_scamp_solution`).
SCAMP_MIN_MATCHES = 10
SCAMP_MAX_CHI2_PVALUE = 1e-3  # reject when p(chi2, dof) falls below this
SCAMP_RMS_TOLERANCE = 1.05  # refined match RMS may not exceed initial by more
SCAMP_MAX_CENTER_SHIFT_DEG = 0.5  # rejection threshold for a gross mis-solve
SCAMP_PIXSCALE_TOLERANCE = 0.2  # allowed fractional change of the pixel scale
SCAMP_TIMEOUT_SECONDS = 300
# Option sets tried in turn; the first is SCAMP's default behaviour.
SCAMP_RETRY_OPTIONS = (
    {},
    {
        "POSITION_MAXERR": 10,
        "PIXSCALE_MAXERR": 1.5,
        "POSANGLE_MAXERR": 10,
        "SN_THRESHOLDS": "5.0,50.0",
    },
)

# Columns a detection table must carry to be fed to SCAMP.
SCAMP_SOURCE_COLUMNS = (
    "x",
    "y",
    "xerr",
    "yerr",
    "flux",
    "fluxerr",
    "mag",
    "magerr",
    "flags",
)


def find_scamp_executable(explicit_path=None):
    """Locate the SCAMP executable, or return ``None`` when unavailable.

    Lookup order: an explicit path, the ``RPP_SCAMP_EXECUTABLE`` environment
    variable, :func:`shutil.which`, then the standard Astromatic install
    directories.
    """
    candidates = [explicit_path, os.environ.get(SCAMP_EXECUTABLE_ENV)]
    located = shutil.which("scamp")
    if located:
        candidates.append(located)
    candidates.extend(os.path.join(path, "scamp") for path in SCAMP_COMMON_PATHS)

    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _build_scamp_header(wcs, shape):
    """Return a SCAMP-readable FITS header for the initial WCS solution.

    SCAMP cannot read SIP distortions, so a SIP solution is rewritten in the
    equivalent TPV convention (via ``sip_tpv``) using an explicit CD matrix.
    """
    header = wcs.to_header(relax=True)

    height, width = shape
    header.setdefault("NAXIS", 2)
    header.setdefault("NAXIS1", int(width))
    header.setdefault("NAXIS2", int(height))
    header.setdefault("BITPIX", -64)
    header.setdefault("EQUINOX", 2000.0)

    if wcs.sip is not None and sip_tpv is not None:
        # sip_to_pv needs the linear term as a CD matrix; astropy writes a CD
        # matrix directly when one was used, otherwise PC + CDELT (and omits an
        # identity PC), so build CD from whichever representation is present.
        if "CD1_1" not in header:
            pc11 = header.pop("PC1_1", 1.0)
            pc12 = header.pop("PC1_2", 0.0)
            pc21 = header.pop("PC2_1", 0.0)
            pc22 = header.pop("PC2_2", 1.0)
            cdelt1 = header.pop("CDELT1", 1.0)
            cdelt2 = header.pop("CDELT2", 1.0)
            header["CD1_1"] = cdelt1 * pc11
            header["CD1_2"] = cdelt2 * pc12
            header["CD2_1"] = cdelt1 * pc21
            header["CD2_2"] = cdelt2 * pc22
        sip_tpv.sip_to_pv(header)

    return header


def _write_scamp_ldac(table, header, filename):
    """Write ``table`` and ``header`` in the FITS-LDAC format SCAMP reads."""
    header_string = header.tostring(endcard=True)
    # astropy strips the trailing blanks of the final END card; SCAMP needs it.
    header_string += fits.Header().tostring(endcard=True)

    header_column = fits.Column(
        name="Field Header Card",
        format=f"{len(header_string)}A",
        array=[header_string],
    )
    header_hdu = fits.BinTableHDU.from_columns(fits.ColDefs([header_column]))
    header_hdu.header["EXTNAME"] = "LDAC_IMHEAD"

    data_hdu = fits.table_to_hdu(table)
    data_hdu.header["EXTNAME"] = "LDAC_OBJECTS"

    fits.HDUList([fits.PrimaryHDU(), header_hdu, data_hdu]).writeto(
        filename, overwrite=True
    )


def _scamp_xml_diagnostics(xml_path):
    """Parse the SCAMP XML report into a small ``{key: value}`` dict."""
    try:
        row = Table.read(xml_path, table_id=0)[0]
    except (OSError, ValueError, KeyError, IndexError):
        return {}

    diagnostics = {}
    for key in ("NDeg_Reference", "Chi2_Reference"):
        if key not in row.colnames:
            continue
        try:
            diagnostics[key] = float(row[key])
        except (TypeError, ValueError):
            continue
    return diagnostics


def _match_rms_arcsec(wcs, sources, cat, sr_deg, cat_col_ra, cat_col_dec):
    """Return (RMS nearest-neighbour match separation, number of matches).

    Each detection is matched to its closest catalogue counterpart, so the
    metric measures the actual astrometric residual rather than the density of
    neighbouring stars inside the matching radius.
    """
    ra, dec = wcs.all_pix2world(
        np.asarray(sources["x"], dtype=float),
        np.asarray(sources["y"], dtype=float),
        0,
    )
    source_coords = SkyCoord(ra, dec, unit="deg")
    catalog_coords = SkyCoord(
        np.asarray(cat[cat_col_ra], dtype=float),
        np.asarray(cat[cat_col_dec], dtype=float),
        unit="deg",
    )

    _, separation, _ = source_coords.match_to_catalog_sky(catalog_coords)
    within = separation <= sr_deg * u.deg
    if not np.any(within):
        return None, 0
    matched = separation[within].arcsec
    return float(np.sqrt(np.mean(matched**2))), int(matched.size)


def _wcs_pixel_scale_arcsec(wcs):
    """Return the pixel scale of a WCS in arcsec/pixel."""
    return float(
        np.hypot(
            wcs.pixel_scale_matrix[0, 0], wcs.pixel_scale_matrix[0, 1]
        )
        * 3600.0
    )


def _frame_center(wcs, shape):
    """Return the (RA, Dec) of the frame center in degrees."""
    height, width = shape
    ra, dec = wcs.all_pix2world(width / 2.0, height / 2.0, 0)
    return float(np.asarray(ra)), float(np.asarray(dec))


def _check_scamp_solution(initial_wcs, refined_wcs, diagnostics, sources, cat,
                          sr_deg, shape, cat_col_ra, cat_col_dec,
                          min_matches=SCAMP_MIN_MATCHES):
    """Return ``None`` when the refined solution passes, else a reason string.

    The checks combine SCAMP's own report (number of matched references and the
    chi2 of the fit) with an independent, WCS-only re-measurement of the
    source-to-catalogue match residuals before and after refinement.
    """
    if refined_wcs is None or not refined_wcs.is_celestial:
        return "SCAMP returned a non-celestial WCS"

    n_matches = diagnostics.get("n_matches")
    chi2_value = diagnostics.get("chi2")
    if n_matches is not None and n_matches < min_matches:
        return (
            f"only {n_matches:.0f} reference matches (need "
            f"{min_matches})"
        )
    if chi2_value is not None and n_matches and n_matches > 0:
        try:
            if chi2.sf(chi2_value, df=n_matches) < SCAMP_MAX_CHI2_PVALUE:
                return (
                    f"SCAMP fit rejected (chi2={chi2_value:.1f}, "
                    f"dof={n_matches:.0f})"
                )
        except (TypeError, ValueError, FloatingPointError):
            diagnostics["chi2_check_error"] = True

    # Independent residual check: the refined WCS must not make the positional
    # agreement with the catalogue worse than the initial solution did.
    initial_rms, _ = _match_rms_arcsec(
        initial_wcs, sources, cat, sr_deg, cat_col_ra, cat_col_dec
    )
    refined_rms, refined_matches = _match_rms_arcsec(
        refined_wcs, sources, cat, sr_deg, cat_col_ra, cat_col_dec
    )
    diagnostics["initial_rms_arcsec"] = initial_rms
    diagnostics["refined_rms_arcsec"] = refined_rms
    diagnostics["refined_match_count"] = refined_matches

    if refined_rms is None or refined_matches == 0:
        return "refined WCS no longer matches the catalogue at the given radius"
    if (
        initial_rms is not None
        and refined_rms > initial_rms * SCAMP_RMS_TOLERANCE + 1e-3
    ):
        return (
            f"refined match RMS {refined_rms:.3f}\" is worse than the initial "
            f"{initial_rms:.3f}\""
        )

    # Sanity checks on the geometry of the solution.
    try:
        initial_ra, initial_dec = _frame_center(initial_wcs, shape)
        refined_ra, refined_dec = _frame_center(refined_wcs, shape)
        center_shift = SkyCoord(initial_ra, initial_dec, unit="deg").separation(
            SkyCoord(refined_ra, refined_dec, unit="deg")
        ).deg
        diagnostics["center_shift_deg"] = center_shift
        if center_shift > SCAMP_MAX_CENTER_SHIFT_DEG:
            return f"frame center moved by {center_shift:.3f} deg"

        initial_scale = _wcs_pixel_scale_arcsec(initial_wcs)
        refined_scale = _wcs_pixel_scale_arcsec(refined_wcs)
        diagnostics["initial_pixel_scale_arcsec"] = initial_scale
        diagnostics["refined_pixel_scale_arcsec"] = refined_scale
        if initial_scale > 0:
            ratio = refined_scale / initial_scale
            diagnostics["pixel_scale_ratio"] = ratio
            if abs(ratio - 1.0) > SCAMP_PIXSCALE_TOLERANCE:
                return f"pixel scale changed by a factor {ratio:.3f}"
    except (TypeError, ValueError, AttributeError) as exc:
        # Geometry checks are best-effort; a failure here must not reject an
        # otherwise valid solution.
        diagnostics["geometry_check_error"] = str(exc)

    return None


def _refine_wcs_with_scamp_once(
    sources,
    cat,
    wcs,
    sr,
    order=3,
    cat_col_ra="RAJ2000",
    cat_col_dec="DEJ2000",
    cat_col_mag="rmag",
    min_matches=SCAMP_MIN_MATCHES,
    timeout=SCAMP_TIMEOUT_SECONDS,
    scamp_executable=None,
    verbose=None,
    extra_options=None,
):
    """Refine an initial WCS by running the SCAMP executable directly.

    The frame detections and the reference catalogue are written as LDAC tables
    and handed to the ``scamp`` binary, which is invoked with the same options
    the previous stdpipe wrapper used (TPV projection, no photometric solve, XML
    report). The returned solution is validated before it is accepted.

    Parameters
    ----------
    sources : astropy.table.Table
        Detections; must contain ``SCAMP_SOURCE_COLUMNS``.
    cat : astropy.table.Table
        Reference catalogue with ``cat_col_ra``/``cat_col_dec`` positions.
    wcs : astropy.wcs.WCS
        Initial (blind) solution to refine.
    sr : float
        Matching radius in degrees.
    order : int
        Polynomial order of the PV distortion fitted by SCAMP.
    cat_col_ra, cat_col_dec : str
        Catalogue column names for Right Ascension and Declination.
    cat_col_mag : str
        Catalogue column name holding the magnitude closest to the frame band.
    min_matches : int
        Minimum number of catalogue references SCAMP must fit.
    timeout : float
        Maximum runtime of the SCAMP process, in seconds.
    scamp_executable : str, optional
        Explicit path to the SCAMP binary.
    verbose : callable or bool, optional
        ``print``-like function receiving SCAMP/log messages.

    Returns
    -------
    (astropy.wcs.WCS or None, dict)
        The refined WCS (``None`` on failure) and a diagnostics dictionary.
    """

    def log(message):
        if verbose is None or verbose is False:
            return
        if callable(verbose):
            verbose(message)
        else:
            print(message)

    diagnostics = {
        "success": False,
        "reason": None,
        "executable": None,
        "returncode": None,
        "n_matches": None,
        "chi2": None,
        "astrometric_rms_arcsec": None,
        "initial_rms_arcsec": None,
        "refined_rms_arcsec": None,
        "center_shift_deg": None,
        "pixel_scale_ratio": None,
    }

    missing_columns = [
        column for column in SCAMP_SOURCE_COLUMNS if column not in sources.colnames
    ]
    if missing_columns:
        diagnostics["reason"] = (
            f"detection table is missing columns: {', '.join(missing_columns)}"
        )
        log(diagnostics["reason"])
        return None, diagnostics

    if cat is None or len(cat) == 0:
        diagnostics["reason"] = "no reference catalogue stars available"
        return None, diagnostics

    executable = find_scamp_executable(scamp_executable)
    diagnostics["executable"] = executable
    if executable is None:
        diagnostics["reason"] = (
            "SCAMP executable not found; install SCAMP or set "
            f"{SCAMP_EXECUTABLE_ENV}"
        )
        log(diagnostics["reason"])
        return None, diagnostics

    workdir = tempfile.mkdtemp(prefix="scamp")
    keep_workdir = bool(os.environ.get("RPP_SCAMP_KEEP_WORKDIR"))
    try:
        shape = (
            int(np.max(sources["y"])) + 1,
            int(np.max(sources["x"])) + 1,
        )
        header = _build_scamp_header(wcs, shape)

        objects = Table(
            data={
                "XWIN_IMAGE": np.asarray(sources["x"], dtype=float) + 1.0,
                "YWIN_IMAGE": np.asarray(sources["y"], dtype=float) + 1.0,
                "ERRAWIN_IMAGE": np.asarray(sources["xerr"], dtype=float),
                "ERRBWIN_IMAGE": np.asarray(sources["yerr"], dtype=float),
                "FLUX_AUTO": np.asarray(sources["flux"], dtype=float),
                "FLUXERR_AUTO": np.asarray(sources["fluxerr"], dtype=float),
                "MAG_AUTO": np.asarray(sources["mag"], dtype=float),
                "MAGERR_AUTO": np.asarray(sources["magerr"], dtype=float),
                "FLAGS": np.asarray(sources["flags"], dtype=int),
            }
        )

        n_cat = len(cat)
        catalog = Table(
            data={
                "X_WORLD": np.asarray(cat[cat_col_ra], dtype=float),
                "Y_WORLD": np.asarray(cat[cat_col_dec], dtype=float),
                "ERRA_WORLD": _default_column(cat, "e_" + cat_col_ra, 1.0 / 3600.0),
                "ERRB_WORLD": _default_column(
                    cat, "e_" + cat_col_dec, 1.0 / 3600.0
                ),
                "MAG": _default_column(cat, cat_col_mag, 0.0),
                "MAGERR": _default_column(cat, "e_" + cat_col_mag, 0.01),
                "OBSDATE": np.full(n_cat, 2000.0),
                "FLAGS": np.zeros(n_cat, dtype=int),
            }
        )

        objects_name = os.path.join(workdir, "objects.cat")
        catalog_name = os.path.join(workdir, "catalogue.cat")
        header_name = os.path.join(workdir, "objects.head")
        xml_name = os.path.join(workdir, "scamp.xml")
        config_name = os.path.join(workdir, "empty.conf")

        _write_scamp_ldac(objects, header, objects_name)
        _write_scamp_ldac(catalog, header, catalog_name)
        if os.path.exists(header_name):
            os.unlink(header_name)
        # An empty config keeps SCAMP from picking up a stray scamp.conf.
        with open(config_name, "w", encoding="ascii") as handle:
            handle.write("\n")

        options = {
            "c": config_name,
            "VERBOSE_TYPE": "QUIET",
            "SOLVE_PHOTOM": "N",
            "CHECKPLOT_TYPE": "NONE",
            "WRITE_XML": "Y",
            "XML_NAME": xml_name,
            "PROJECTION_TYPE": "TPV",
            "CROSSID_RADIUS": sr * 3600.0,
            "DISTORT_DEGREES": max(1, int(order)),
            "ASTREF_CATALOG": "FILE",
            "ASTREFCAT_NAME": catalog_name,
            "HEADER_NAME": header_name,
        }
        options.update(extra_options or {})

        command = [executable, objects_name]
        for key, value in options.items():
            command.extend(["-" + key, str(value)])

        log("Will run SCAMP like that:")
        log(" ".join(command))

        try:
            completed = subprocess.run(
                command,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            diagnostics["reason"] = f"SCAMP timed out after {timeout:g} s"
            log(diagnostics["reason"])
            return None, diagnostics
        except OSError as exc:
            diagnostics["reason"] = f"could not run SCAMP: {exc}"
            log(diagnostics["reason"])
            return None, diagnostics

        diagnostics["returncode"] = completed.returncode
        output = "\n".join(
            part.strip()
            for part in (completed.stdout, completed.stderr)
            if part and part.strip()
        )
        if output:
            for line in output.splitlines():
                log(line)

        if completed.returncode != 0:
            diagnostics["reason"] = (
                f"SCAMP invocation returned code {completed.returncode}"
            )
            log(diagnostics["reason"])
            return None, diagnostics
        if not os.path.exists(header_name):
            diagnostics["reason"] = "SCAMP did not produce an output header"
            log(diagnostics["reason"])
            return None, diagnostics
        if not os.path.exists(xml_name):
            diagnostics["reason"] = "SCAMP did not produce an XML report"
            log(diagnostics["reason"])
            return None, diagnostics

        report = _scamp_xml_diagnostics(xml_name)
        diagnostics["n_matches"] = report.get("NDeg_Reference")
        diagnostics["chi2"] = report.get("Chi2_Reference")
        if diagnostics["n_matches"] is not None:
            chi2_display = (
                diagnostics["chi2"] if diagnostics["chi2"] is not None else -1.0
            )
            log(
                f"SCAMP fit {diagnostics['n_matches']:.0f} references "
                f"(chi2 {chi2_display:.1f})"
            )

        with open(header_name, "r") as handle:
            scamp_header = fits.Header.fromstring(
                handle.read().encode("ascii", "ignore"), sep="\n"
            )

        # SCAMP sometimes returns a TAN solution despite the PV keywords, or a
        # TPV CTYPE without the PV terms; normalise both cases.
        if scamp_header.get("CTYPE1") != "RA---TPV" and "PV1_0" in scamp_header:
            scamp_header["CTYPE1"] = "RA---TPV"
            scamp_header["CTYPE2"] = "DEC--TPV"
        elif scamp_header.get("CTYPE1") == "RA---TPV" and "PV1_0" not in scamp_header:
            scamp_header["CTYPE1"] = "RA---TAN"
            scamp_header["CTYPE2"] = "DEC--TAN"
            scamp_header = WCS(scamp_header).to_header(relax=True)

        refined_wcs = WCS(scamp_header)

        diagnostics["astrometric_rms_arcsec"] = tuple(
            float(scamp_header.get(key, 0.0)) * 3600.0
            for key in ("ASTRRMS1", "ASTRRMS2")
        )

        reason = _check_scamp_solution(
            wcs,
            refined_wcs,
            diagnostics,
            sources,
            cat,
            sr,
            shape,
            cat_col_ra,
            cat_col_dec,
            min_matches=min_matches,
        )
        if reason is not None:
            diagnostics["reason"] = reason
            log(f"SCAMP solution rejected: {reason}")
            return None, diagnostics

        diagnostics["success"] = True
        log("SCAMP solution accepted")
        return refined_wcs, diagnostics
    finally:
        if not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def refine_wcs_with_scamp(sources, cat, wcs, sr, **kwargs):
    """Run SCAMP, retrying with wider matching tolerances on too few matches.

    SCAMP's default pattern matching only tolerates a ~1 arcmin error on the
    initial position (``POSITION_MAXERR``); a plate solution that is slightly
    further off yields 0-few matches although the field is fine. The retry
    widens the position/scale/angle tolerances and relaxes the S/N cut.
    """
    min_matches = kwargs.get("min_matches", SCAMP_MIN_MATCHES)
    refined, diagnostics = None, {}
    for attempt, extra in enumerate(SCAMP_RETRY_OPTIONS):
        refined, diagnostics = _refine_wcs_with_scamp_once(
            sources, cat, wcs, sr, extra_options=extra, **kwargs
        )
        n_matches = diagnostics.get("n_matches")
        if refined is not None or n_matches is None or n_matches >= min_matches:
            break
        verbose = kwargs.get("verbose")
        if callable(verbose):
            verbose(
                f"Only {n_matches:.0f} matches; retrying SCAMP with wider "
                "matching tolerances"
            )
    diagnostics["attempts"] = attempt + 1
    return refined, diagnostics


def _default_column(table, column, default):
    """Return ``table[column]`` as floats, or a constant column of ``default``."""
    if column in table.colnames:
        return np.asarray(table[column], dtype=float)
    return np.full(len(table), float(default))


def solve_with_astrometrynet(file_path):
    """
    Solve astrometric plate using local Astrometry.Net installation via stdpipe.
    This function loads a FITS image, detects objects using photutils, and uses stdpipe's
    blind_match_objects wrapper around Astrometry.Net to determine accurate WCS information.

    Object detection and measurement use stdpipe; the WCS refinement runs the
    SCAMP binary directly against the Pan-STARRS DR1 catalogue (see
    :func:`refine_wcs_with_scamp`).

    Parameters
    ----------
    file_path : str
        Path to the FITS image file that needs astrometric solving

    Returns
    -------
    tuple
        (wcs_object, updated_header, log_messages, error) where:
        - wcs_object: astropy.wcs.WCS object containing the WCS solution
        - updated_header: Original header updated with WCS keywords
        - log_messages: List of log messages
        - error: Error message if solving fails

    Notes
    -----
    This function requires:
    - Local Astrometry.Net installation with solve-field binary
    - Appropriate index files for the field scale
    - The image should contain enough stars for plate solving (typically >10)

    Uses stdpipe.astrometry.blind_match_objects for cleaner interface.
    """
    log_messages = []
    if STDPIPE_IMPORT_ERROR is not None:
        error_message = (
            "Astrometry dependencies are not available: "
            f"{STDPIPE_IMPORT_ERROR}. Install setuptools==71.0.0 "
            "so pkg_resources remains available for sip_tpv/stdpipe."
        )
        log_messages.append(f"ERROR: {error_message}")
        return None, None, log_messages, error_message

    try:
        if not os.path.exists(file_path):
            return None, None, log_messages, f"File {file_path} does not exist"

        # Load the FITS file
        log_messages.append("INFO: Loading FITS file for local plate solving")
        with fits.open(file_path) as hdul:
            image_data = hdul[0].data
            header = hdul[0].header.copy()

        if image_data is None:
            return None, None, log_messages, "No image data found in FITS file"

        # Check if WCS already exists
        try:
            existing_wcs = WCS(header)
            if existing_wcs.is_celestial:
                log_messages.append("INFO: Proceeding with blind plate solve")
        except Exception:
            log_messages.append(
                "INFO: No valid WCS found in header. Proceeding with blind solve"
            )

        # Use adaptive minimum sources based on image size
        adaptive_min_sources = get_adaptive_min_sources(image_data.shape)
        log_messages.append(f"INFO: Using adaptive minimum sources: {adaptive_min_sources}")

        # Try improved detection with progressive parameter grid
        sources = _try_source_detection_improved(
            image_data,
            header,
            min_sources=adaptive_min_sources
        )

        if sources is None or len(sources) < 5:
            error_message = "Failed to detect sufficient sources for plate solving."
            log_messages.append(f"ERROR: {error_message}")
            return None, None, log_messages, error_message

        # Filter for best sources if too many
        if len(sources) > 500:
            sources.sort("flux")
            sources.reverse()
            sources = sources[:500]
            log_messages.append(
                f"INFO: Using brightest {len(sources)} sources for plate solving"
            )

        log_messages.append(
            f"SUCCESS: Ready for plate solving with {len(sources)} sources"
        )

        # Get pixel scale estimate using improved method
        pixel_scale_estimate = estimate_pixel_scale_robust(header, image_data.shape)
        
        if pixel_scale_estimate is not None:
            log_messages.append(
                f"INFO: Using pixel scale estimate: {pixel_scale_estimate:.2f} arcsec/pixel"
            )
        else:
            log_messages.append(
                "INFO: No pixel scale estimate available, using broad range"
            )

        # Note: the stdpipe blind-match parameters (SIP order, pixel-scale
        # constraints and RA/DEC hints) are handled inside
        # solve_with_enhanced_strategy.

        try:
            # Use enhanced multi-stage plate solving strategy
            solved_wcs, stage_messages = solve_with_enhanced_strategy(
                sources, header, image_data
            )
            log_messages.extend(stage_messages)

            if solved_wcs is None:
                api_key = _get_online_astrometry_api_key()
                if api_key:
                    log_messages.append(
                        "INFO: Local plate solve failed; trying "
                        "Astrometry.net "
                        "online with the detected source list"
                    )
                    height, width = image_data.shape
                    online_kwargs = {
                        "order": 3,
                        "sn": 5,
                        "width": width,
                        "height": height,
                        "solve_timeout": 600,
                        "api_key": api_key,
                        "publicly_visible": "n",
                    }

                    try:
                        hint_wcs = WCS(header)
                        if hint_wcs.is_celestial:
                            center_ra, center_dec, center_sr = (
                                astrometry.get_frame_center(
                                    wcs=hint_wcs,
                                    width=width,
                                    height=height,
                                )
                            )
                            online_kwargs.update(
                                {
                                    "center_ra": center_ra,
                                    "center_dec": center_dec,
                                    "radius": max(1.0, 2 * center_sr),
                                }
                            )
                            online_scale = (
                                astrometry.get_pixscale(wcs=hint_wcs) * 3600
                            )
                        else:
                            online_scale = pixel_scale_estimate
                    except Exception as hint_error:
                        online_scale = pixel_scale_estimate
                        log_messages.append(
                            "WARNING: Could not derive online solver hints: "
                            f"{hint_error}"
                        )

                    if online_scale is not None and online_scale > 0:
                        online_kwargs.update(
                            {
                                "scale_lower": 0.7 * online_scale,
                                "scale_upper": 1.3 * online_scale,
                                "scale_units": "arcsecperpix",
                            }
                        )

                    try:
                        solved_wcs = astrometry.blind_match_astrometrynet(
                            sources, **online_kwargs
                        )
                        if solved_wcs is not None:
                            log_messages.append(
                                "SUCCESS: Online Astrometry.net plate solving "
                                "successful"
                            )
                        else:
                            log_messages.append(
                                "WARNING: Online Astrometry.net plate solving "
                                "returned no solution"
                            )
                    except Exception as online_error:
                        log_messages.append(
                            "WARNING: Online Astrometry.net solve failed: "
                            f"{online_error}"
                        )
                else:
                    log_messages.append(
                        "INFO: Online Astrometry.net fallback skipped; no API "
                        "key found in ASTROMETRY_API_KEY or Astroquery config"
                    )

            if solved_wcs is not None:
                log_messages.append("SUCCESS: Plate solving successful")

                # --- Refinement Step (Pan-STARRS DR1) ---
                try:
                    log_messages.append(
                        "INFO: Attempting WCS refinement using Pan-STARRS DR1"
                    )

                    # 1. Get image parameters from the initial solution
                    height, width = image_data.shape

                    # Get frame center and radius
                    center_ra, center_dec, center_sr = astrometry.get_frame_center(
                        wcs=solved_wcs, width=width, height=height
                    )

                    # 2. Fetch Pan-STARRS DR1 catalog
                    log_messages.append(
                        "INFO: Fetching Pan-STARRS DR1 catalog around "
                        f"RA={center_ra:.4f}, DEC={center_dec:.4f}, "
                        f"r={center_sr:.2f} deg"
                    )
                    cat = catalogs.get_cat_vizier(
                        center_ra,
                        center_dec,
                        center_sr,
                        "ps1",
                        filters={"rmag": "<19"},
                    )

                    if cat is not None and len(cat) > 10:
                        log_messages.append(
                            f"INFO: Retrieved {len(cat)} catalog stars"
                        )

                        # 3. Refine Astrometry
                        # Get pixel scale from solved WCS for matching radius
                        pixscale_deg = astrometry.get_pixscale(wcs=solved_wcs)
                        refinement_sr = 7 * pixscale_deg

                        required_source_columns = {
                            "x", "y", "xerr", "yerr", "flux", "fluxerr",
                            "mag", "magerr", "flags",
                        }
                        required_catalog_columns = {
                            "RAJ2000", "DEJ2000", "rmag"
                        }
                        missing_source_columns = sorted(
                            required_source_columns.difference(
                                sources.colnames
                            )
                        )
                        missing_catalog_columns = sorted(
                            required_catalog_columns.difference(cat.colnames)
                        )
                        log_messages.append(
                            "INFO: Refinement inputs: "
                            f"{len(sources)} detections, "
                            f"{len(cat)} catalog stars, "
                            f"radius={refinement_sr * 3600:.2f} arcsec, "
                            "missing source columns="
                            f"{missing_source_columns or 'none'}, "
                            "missing catalog columns="
                            f"{missing_catalog_columns or 'none'}"
                        )

                        def refinement_logger(*parts, **_kwargs):
                            message = " ".join(
                                str(part) for part in parts
                            ).strip()
                            if message:
                                log_messages.append(
                                    f"INFO: SCAMP: {message}"
                                )

                        # Run the SCAMP binary directly (no stdpipe wrapper) and
                        # keep the initial solution unless the refined one passes
                        # the validation checks inside the function.
                        refined_wcs, scamp_diagnostics = refine_wcs_with_scamp(
                            sources,
                            cat,
                            wcs=solved_wcs,
                            sr=refinement_sr,
                            order=3,
                            cat_col_mag="rmag",
                            verbose=refinement_logger,
                        )

                        if scamp_diagnostics.get("executable") is None:
                            log_messages.append(
                                "WARNING: SCAMP executable not found; keeping "
                                "the initial WCS solution"
                            )
                        elif refined_wcs is not None:
                            solved_wcs = refined_wcs
                            log_messages.append(
                                "SUCCESS: Astrometry refined using "
                                "Pan-STARRS DR1 and direct SCAMP"
                            )
                            log_messages.append(
                                "INFO: SCAMP diagnostics: "
                                f"matches={scamp_diagnostics.get('n_matches')}, "
                                f"chi2={scamp_diagnostics.get('chi2')}, "
                                "astrometric RMS (arcsec)="
                                f"{scamp_diagnostics.get('astrometric_rms_arcsec')}"
                            )
                            initial_rms = scamp_diagnostics.get(
                                "initial_rms_arcsec"
                            )
                            refined_rms = scamp_diagnostics.get(
                                "refined_rms_arcsec"
                            )
                            if initial_rms is not None and refined_rms is not None:
                                log_messages.append(
                                    "INFO: SCAMP cross-check: match RMS "
                                    f"{initial_rms:.3f}\" -> {refined_rms:.3f}\""
                                )
                        else:
                            log_messages.append(
                                "WARNING: Astrometric refinement failed "
                                f"({scamp_diagnostics.get('reason')}), keeping "
                                "initial solution"
                            )
                    else:
                        log_messages.append(
                            "WARNING: Could not retrieve sufficient catalog stars for refinement"
                        )

                except Exception as refine_error:
                    log_messages.append(
                        f"WARNING: Refinement process failed: {refine_error}"
                    )

                # Update original header with WCS solution (refined or initial)
                updated_header = header.copy()

                # Clear any existing WCS keywords to avoid conflicts
                astrometry.clear_wcs(
                    updated_header,
                    remove_comments=True,
                    remove_underscored=True,
                    remove_history=True,
                )

                # Add new WCS keywords from solution
                wcs_header = solved_wcs.to_header(relax=True)
                updated_header.update(wcs_header)

                # Add solution metadata
                updated_header["COMMENT"] = (
                    "Astrometry.net WCS; PS1 refinement attempted"
                )
                updated_header["STDPIPE"] = (
                    True,
                    "Solved with blind matching and refinement",
                )

                # Display solution information
                try:
                    center_ra, center_dec = solved_wcs.pixel_to_world_values(
                        image_data.shape[1] / 2, image_data.shape[0] / 2
                    )
                    pixel_scale = astrometry.get_pixscale(wcs=solved_wcs) * 3600

                    log_messages.append(
                        f"INFO: Solution center: RA={center_ra:.6f}°, DEC={center_dec:.6f}°"
                    )
                    log_messages.append(
                        f"INFO: Pixel scale: {pixel_scale:.3f} arcsec/pixel"
                    )

                    # Validate solution makes sense
                    if 0 <= center_ra <= 360 and -90 <= center_dec <= 90:
                        log_messages.append("SUCCESS: Solution coordinates are valid")
                    else:
                        log_messages.append(
                            "WARNING: Solution coordinates seem invalid"
                        )

                except Exception as coord_error:
                    log_messages.append(
                        f"WARNING: Could not extract solution coordinates: {coord_error}"
                    )

                return solved_wcs, updated_header, log_messages, None

            else:
                error_message = "Plate solving failed"
                log_messages.append(f"ERROR: {error_message}")
                return None, None, log_messages, error_message

        except Exception as solve_error:
            error_message = f"Error during plate solving: {solve_error}"
            log_messages.append(f"ERROR: {error_message}")
            return None, None, log_messages, error_message

    except Exception as e:
        error_message = f"Error in plate solving setup: {str(e)}"
        log_messages.append(f"ERROR: {error_message}")
        return None, None, log_messages, error_message
