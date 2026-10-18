from stdpipe import pipeline, cutouts, templates, catalogs, photometry, plots
from stdpipe import astrometry as stdpipe_astrometry
from astropy.coordinates import SkyCoord
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
import astropy.units as u
from astropy.wcs import WCS
import numpy as np
import streamlit as st
from astropy.table import Table

# NumPy 2.0 removed np.in1d; restore it as an alias for np.isin for stdpipe compatibility
if not hasattr(np, "in1d"):
    np.in1d = np.isin  # type: ignore[attr-defined]
from astropy.time import Time
from astroquery.imcce import Skybot
from src.tools_pipeline import (
    format_psf_fallback_message,
    get_prefixed_photometry_column,
)
from src.utils import prepare_export_table, skybot_cone_search

# Shared singleton: configure once at import, never per request.
Skybot._get_raw_response = False

from src.tools_pipeline import fix_header

from src.pipeline import resolve_effective_gain


# Conversion to AB mags, from https://www.astronomy.ohio-state.edu/martini.10/usefuldata.html
# SEP detection flags that make a detection unreliable as a transient
# candidate: object/aperture truncated at the image boundary, deblending
# overflow, a degenerate (fully correlated) centroid, or an all-masked /
# non-positive aperture.
#
# NOTE: these are the *SEP* flag bits (low byte), documented at
# https://sep.readthedocs.io/en/stable/reference.html and in the STDPipe
# "Object flags" guide.  stdpipe's default flag mask (0x7F00) only covers
# its own SExtractor/stdpipe conventions, so passing it to a raw SEP table
# silently disables flag filtering (the AND is always zero).
SEP_REJECT_FLAGS = (
    0x2  # sep.OBJ_TRUNC      - object truncated at image boundary
    | 0x4  # sep.OBJ_DOVERFLOW  - deblending memory overflow
    | 0x8  # sep.OBJ_SINGU      - x,y fully correlated
    | 0x10  # sep.APER_TRUNC     - aperture truncated at image boundary
    | 0x40  # sep.APER_ALLMASKED - aperture contains only masked pixels
    | 0x80  # sep.APER_NONPOSITIVE - negative aperture sum
)

# Detection threshold (sigma) used by the transient finder: low on purpose,
# to be more sensitive to faint sources.
TRANSIENT_DETECT_THRESH = 1.5

# Metadata attached to the candidate table to report which photometry supplied
# ``mag_calib``: the pipeline PSF magnitudes or the 1.1 x FWHM aperture fallback.
# The Streamlit app reads it to warn the user when the fallback is used.
TRANSIENT_MAGNITUDE_SOURCE_META = "transient_magnitude_source"
TRANSIENT_PSF_SOURCE = "psf"
TRANSIENT_APERTURE_FALLBACK_SOURCE = "aperture_1_1"

# Same PSF-fallback wording as the main pipeline, with the transient-specific
# aperture detail appended.
TRANSIENT_PSF_FALLBACK_MESSAGE = format_psf_fallback_message(
    "Transient candidates use the calibrated 1.1 x FWHM aperture magnitude."
)

# Magnitude excess (detected - catalogue) above which a source positionally
# coincident with a reference catalogue entry is still kept as a transient
# candidate.  Defaults to the long-standing 2.0 mag threshold.
DEFAULT_TRANSIENT_MAG_THRESHOLD = 1.5

filter_ab_offset = {
    "U": 0.79,
    "B": -0.09,
    "V": 0.02,
    "R": 0.21,
    "I": 0.45,
    "u": 0,
    "g": 0,
    "r": 0,
    "i": 0,
    "z": 0,
    "y": 0,
    "G": 0,
    "BP": 0,
    "RP": 0,
}


def filter_skybot_candidates(
    candidates, obs_time, sr=10 / 3600, col_ra="ra", col_dec="dec"
):
    """Filter out Solar System objects from candidates using SkyBoT service.

    This is a workaround for stdpipe's xmatch_skybot which has column naming issues
    with newer versions of astroquery.

    Parameters
    ----------
    candidates : astropy.table.Table
        Table of candidates with 'ra' and 'dec' columns
    obs_time : str or astropy.time.Time
        Observation time
    sr : float
        Cross-matching radius in degrees (default: 10 arcsec)
    col_ra : str
        Name of RA column in candidates table
    col_dec : str
        Name of Dec column in candidates table

    Returns
    -------
    astropy.table.Table
        Candidates with Solar System objects removed
    """
    if candidates is None or len(candidates) == 0:
        return candidates

    try:
        # Get center and radius of the candidates field
        ra0, dec0, sr0 = stdpipe_astrometry.get_objects_center(
            candidates, col_ra=col_ra, col_dec=col_dec
        )

        # Convert time if needed
        if not isinstance(obs_time, Time):
            obs_time = Time(obs_time)

        # Query SkyBoT for Solar System objects in the field (shared helper:
        # current ssp.imcce.fr endpoint, retries and response checks).
        skybot_results, skybot_problem = skybot_cone_search(
            ra0,
            dec0,
            float(sr0 + 2.0 * sr),
            obs_time.jd,
            Skybot._parse_result,
        )
        if skybot_problem is not None:
            st.warning(
                f"{skybot_problem}. Skipping Solar System object filtering."
            )
            return candidates

        if skybot_results is None or len(skybot_results) == 0:
            st.warning("✓ SkyBoT: No Solar System objects found in field")
            return candidates

        # Astroquery's SkyBoT returns 'RA' and 'DEC' columns (uppercase)
        # Check what columns we actually have
        skybot_ra_col = None
        skybot_dec_col = None
        for col in ["RA", "ra"]:
            if col in skybot_results.colnames:
                skybot_ra_col = col
                break
        for col in ["DEC", "dec", "Dec"]:
            if col in skybot_results.colnames:
                skybot_dec_col = col
                break

        if skybot_ra_col is None or skybot_dec_col is None:
            st.warning(
                f"SkyBoT: Could not find RA/DEC columns. Available: {skybot_results.colnames}"
            )
            return candidates

        # Cross-match candidates with SkyBoT results
        oidx, cidx, _ = stdpipe_astrometry.spherical_match(
            candidates[col_ra],
            candidates[col_dec],
            skybot_results[skybot_ra_col],
            skybot_results[skybot_dec_col],
            sr,
        )

        if len(oidx) > 0:
            # Create mask of candidates that are NOT matched to SkyBoT objects
            keep_mask = np.ones(len(candidates), dtype=bool)
            keep_mask[oidx] = False

            n_removed = len(oidx)
            st.write(
                f"✓ SkyBoT: Removed {n_removed} candidate(s) matching Solar System objects"
            )

            # Show which objects were removed
            for i, idx in enumerate(oidx[:5]):  # Show first 5 matches
                sso_name = (
                    skybot_results["Name"][cidx[i]]
                    if "Name" in skybot_results.colnames
                    else "unknown"
                )
                st.write(
                    f"  - Candidate at RA={candidates[col_ra][idx]:.4f}, Dec={candidates[col_dec][idx]:.4f} → {sso_name}"
                )
            if len(oidx) > 5:
                st.write(f"  ... and {len(oidx) - 5} more")

            return candidates[keep_mask]
        else:
            st.write("✓ SkyBoT: No candidates match known Solar System objects")
            return candidates

    except Exception as e:
        st.warning(f"SkyBoT query failed: {e}. Skipping Solar System object filtering.")
        return candidates


def build_transient_table(
    candidates, photometry_table, filter_band=None, match_radius_arcsec=4.0
):
    """Build the transient candidates table with the same columns as the final catalog.

    Each candidate is matched by sky position to its nearest source of the
    photometry table, so the exported rows carry the catalog ``id`` and the
    Astro-Colibri / SIMBAD / SkyBoT / AAVSO / photometry columns of the main
    CSV/VOT files. Candidates without a counterpart keep their own
    coordinates, pixel position and calibrated PSF magnitude.
    """
    import pandas as pd

    if not candidates:
        return pd.DataFrame()

    cand_ra = np.array([c["ra"] for c in candidates], dtype=float)
    cand_dec = np.array([c["dec"] for c in candidates], dtype=float)
    export = prepare_export_table(photometry_table, filter_band)

    if export is None or len(export) == 0 or "ra" not in export.columns:
        return pd.DataFrame(
            {
                "ra": cand_ra,
                "dec": cand_dec,
                "x_center": [c.get("x") for c in candidates],
                "y_center": [c.get("y") for c in candidates],
                "psf_mag": [c.get("mag_calib") for c in candidates],
                "psf_mag_err": [c.get("mag_calib_err") for c in candidates],
            }
        )

    cand_coords = SkyCoord(cand_ra * u.deg, cand_dec * u.deg)
    phot_coords = SkyCoord(
        np.asarray(export["ra"], dtype=float) * u.deg,
        np.asarray(export["dec"], dtype=float) * u.deg,
    )
    idx, sep, _ = cand_coords.match_to_catalog_sky(phot_coords)
    matched = sep.arcsec <= match_radius_arcsec

    result = export.iloc[idx].reset_index(drop=True)
    result.loc[~matched, :] = np.nan
    result["ra"] = np.where(matched, result["ra"], cand_ra)
    result["dec"] = np.where(matched, result["dec"], cand_dec)

    fallbacks = {
        "x_center": [c.get("x") for c in candidates],
        "y_center": [c.get("y") for c in candidates],
    }
    psf_mag_col = get_prefixed_photometry_column("psf_mag", filter_band)
    psf_err_col = get_prefixed_photometry_column("psf_mag_err", filter_band)
    for col, legacy in ((psf_mag_col, "psf_mag"), (psf_err_col, "psf_mag_err")):
        fallbacks[col if col in result.columns else legacy] = [
            c.get("mag_calib" if legacy == "psf_mag" else "mag_calib_err")
            for c in candidates
        ]
    for col, values in fallbacks.items():
        values = pd.Series(values, index=result.index, dtype="float64")
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce").where(
                matched, values
            )
        else:
            result[col] = values

    return result


def _resolve_photometry_columns(table, mag_column, err_column, filter_band=None):
    """Return the magnitude and error column names present in *table*.

    The filter-prefixed form (e.g. ``rapasg_psf_mag``) is preferred because
    exported tables drop the legacy names, with a fallback to the unprefixed
    column. ``None`` is returned for a missing column so the caller can try
    another photometry method.
    """
    resolved = []
    for name in (mag_column, err_column):
        prefixed = get_prefixed_photometry_column(name, filter_band)
        if prefixed and prefixed in table.columns:
            resolved.append(prefixed)
        elif name in table.columns:
            resolved.append(name)
        else:
            resolved.append(None)
    return resolved[0], resolved[1]


def find_candidates(
    photometry_table,
    image,
    header,
    fwhm,
    pixel_scale,
    ra_center,
    dec_center,
    sr,
    mask=None,
    filter_cat=None,
    filter_name=None,
    mag_limit="<20.0",
    detect_thresh=TRANSIENT_DETECT_THRESH,
    mag_threshold=DEFAULT_TRANSIENT_MAG_THRESHOLD,
):
    """Find transient candidates in the given image around the specified object.

    The algorithm detects sources with SExtractor, attaches the calibrated PSF
    magnitudes measured by the main pipeline, and then asks
    :func:`stdpipe.pipeline.filter_transient_candidates` to reject objects that
    are explained by the reference survey or by any of the Vizier catalogues.
    A positional match is only allowed to reject a source when it is *not*
    significantly brighter than its catalogue counterpart (see
    :func:`checker_fn`); genuine brightness excesses are kept as candidates.

    Parameters
    ----------
    photometry_table : pandas.DataFrame
        Calibrated photometry catalog produced by the main pipeline. Must
        contain ``ra``/``dec`` and magnitude columns: the PSF magnitudes are
        used when available, otherwise the calibrated 1.1 x FWHM aperture
        magnitudes (``aperture_mag_1_1`` / ``aperture_mag_err_1_1``), including
        their filter-prefixed variants.
    image : 2D array
        The image data where to search for transients.
    header : astropy.io.fits.Header
        Header of the science image (must contain WCS information).
    fwhm : float
        Seeing FWHM in pixels.
    pixel_scale : float
        Pixel scale in arcsec/pixel.
    ra_center, dec_center : float
        Field centre in degrees; used to pick the reference survey.
    sr : float
        Search radius in degrees.
    mask : 2D array, optional
        Mask (True = masked) corresponding to the image.
    filter_cat : str, optional
        Calibration filter band (used to locate the prefixed PSF columns).
    filter_name : str, optional
        Reference-survey filter for the transient search template.
    mag_limit : str, optional
        Reference-survey magnitude limit expression (default ``"<20.0"``).
    detect_thresh : float, optional
        SExtractor detection threshold in sigma (default 1.5, deep search
        for faint sources). The minimum detection S/N is relaxed from 3 to 2
        when the threshold is below 2 sigma so that faint sources are not
        cut right after detection.
    mag_threshold : float, optional
        Minimum brightness excess, in magnitudes, required for a source that
        coincides with a reference-catalogue entry to be kept as a candidate.
    """
    # Resolve the effective gain (e-/ADU) exactly like the photometry error
    # model: explicit EGAIN/CCDGAIN/CVF keywords first, driver-gain cameras
    # (QHY/ZWO) recognised so their abstract GAIN setting is not misused.
    # Replaces the old 65535 / max(image) heuristic.
    gain = resolve_effective_gain(header, image)

    header, _ = fix_header(header)
    wcs = WCS(header)

    # Check hemisphere using astropy
    _ = SkyCoord(ra=ra_center * u.degree, dec=dec_center * u.degree, frame="icrs")
    is_southern = dec_center < 0

    # Auto-select catalog based on hemisphere if not specified or if PanSTARRS
    if is_southern:
        catalog = "skymapper"
        cat_cutout = "SkyMapper/DR4/"
        st.info(
            f"🌏 Southern hemisphere detected (Dec={dec_center:.2f}°). Using SkyMapper catalog."
        )
    else:
        catalog = "ps1"
        cat_cutout = "PanSTARRS/DR1/"
        st.info(
            f"🌍 Northern hemisphere detected (Dec={dec_center:.2f}°). Using PanSTARRS catalog."
        )
    image = np.ascontiguousarray(image, dtype=np.float32)
    if mask is not None:
        mask = np.asarray(mask, dtype=bool)

    # Detect objects with SExtractor through stdpipe, as done at the start of
    # the pipeline for astrometry. SExtractor handles the background itself.
    try:
        obj = photometry.get_objects_sextractor(
            image,
            mask=mask,
            thresh=detect_thresh,
            aper=max(1.5 * fwhm, 2.0),
            gain=gain,
            edge=10,
            bg_size=64,
            wcs=wcs,
            sn=3.0 if detect_thresh >= 2.0 else 2.0,
            verbose=False,
        )
    except Exception as e:
        st.error(f"SExtractor source detection failed: {e}")
        return []

    if obj is None or len(obj) == 0:
        st.warning("No objects found in the image.")
        return []

    st.success(f"Found {len(obj)} objects in the image.")

    # Make sure sky coordinates are available (normally set via wcs above)
    if "ra" not in obj.colnames or "dec" not in obj.colnames:
        ra_obj, dec_obj = wcs.all_pix2world(obj["x"], obj["y"], 0)
        obj["ra"] = ra_obj
        obj["dec"] = dec_obj

    # Cross-match detections with photometry_table to get PSF magnitudes
    if photometry_table is not None and len(photometry_table) > 0:

        # Get coordinates from photometry_table
        phot_ra = photometry_table["ra"]
        phot_dec = photometry_table["dec"]

        # Cross-match detections with the photometry table. Both lists come
        # from the same image and WCS, so a seeing-sized radius is enough.
        # The previous fixed 10 arcsec radius is several FWHM in typical
        # seeing and - because spherical_match() returns *all* pairs inside
        # the radius - produced many-to-many matches in crowded fields, which
        # duplicated candidates and could attach the wrong PSF magnitude.
        match_radius_arcsec = float(np.clip(fwhm * pixel_scale, 1.0, 4.0))
        match_radius = match_radius_arcsec / 3600.0  # arcsec -> degrees
        oidx, pidx, match_dist = stdpipe_astrometry.spherical_match(
            obj["ra"], obj["dec"], phot_ra, phot_dec, match_radius
        )

        if len(oidx) > 0:
            # Keep only the nearest photometry counterpart for each detection
            # so that every candidate gets exactly one PSF magnitude.
            nearest = np.argsort(match_dist)
            oidx, pidx = oidx[nearest], pidx[nearest]
            _, first = np.unique(oidx, return_index=True)
            oidx, pidx = oidx[first], pidx[first]

            # Keep only matched objects
            obj = obj[oidx]

            # PSF photometry is the reference for transient candidates, but
            # the pipeline may have run without PSF fitting. In that case fall
            # back to the 1.1 x FWHM aperture magnitude, which is already
            # aperture-corrected to the total flux scale.
            magnitude_source = TRANSIENT_PSF_SOURCE
            mag_col, mag_err_col = _resolve_photometry_columns(
                photometry_table, "psf_mag", "psf_mag_err", filter_cat
            )
            if mag_col is None or mag_err_col is None:
                magnitude_source = TRANSIENT_APERTURE_FALLBACK_SOURCE
                mag_col, mag_err_col = _resolve_photometry_columns(
                    photometry_table,
                    "aperture_mag_1_1",
                    "aperture_mag_err_1_1",
                    filter_cat,
                )
            if mag_col is None or mag_err_col is None:
                st.error(
                    "Neither PSF nor 1.1 x FWHM aperture magnitude columns are "
                    "available in the photometry table for transient filtering."
                )
                return []

            # Get magnitudes from photometry_table for the matched sources
            mag_calib = np.asarray(photometry_table[mag_col], dtype=float)[pidx]
            mag_calib_err = np.asarray(photometry_table[mag_err_col], dtype=float)[pidx]

            # The PSF columns may exist without a usable measurement for the
            # matched detections (failed fit -> NaN); fall back in that case too.
            if not (np.isfinite(mag_calib).any() and np.isfinite(mag_calib_err).any()):
                alt_mag_col, alt_mag_err_col = _resolve_photometry_columns(
                    photometry_table,
                    "aperture_mag_1_1",
                    "aperture_mag_err_1_1",
                    filter_cat,
                )
                if alt_mag_col is not None and alt_mag_err_col is not None:
                    magnitude_source = TRANSIENT_APERTURE_FALLBACK_SOURCE
                    mag_col, mag_err_col = alt_mag_col, alt_mag_err_col
                    mag_calib = np.asarray(photometry_table[mag_col], dtype=float)[pidx]
                    mag_calib_err = np.asarray(
                        photometry_table[mag_err_col], dtype=float
                    )[pidx]

            # Add photometry as mag_calib (reference magnitude for transients)
            obj["mag_calib"] = mag_calib
            obj["mag_calib_err"] = mag_calib_err
        else:
            st.warning(
                "No matches found between detections and PSF photometry table"
            )
            return []
    else:
        st.warning("No photometry_table provided - cannot get PSF magnitudes")
        return []

    # Query the appropriate catalog
    try:
        cat = catalogs.get_cat_vizier(
            ra_center, dec_center, sr, catalog, filters={filter_name + "mag": mag_limit}
        )

        # Debug: show what columns we got
        if cat is not None and len(cat) > 0:
            st.write(f"Catalog query returned {len(cat)} sources.")

            # Normalize coordinate column names for stdpipe compatibility
            # Different catalogs use different column names:
            # - SkyMapper: RAICRS/DEICRS
            # - PanSTARRS: RAJ2000/DEJ2000 or raMean/decMean or _RAJ2000/_DEJ2000
            # - Vizier internal: _RAJ2000/_DEJ2000
            # stdpipe expects 'ra' and 'dec' columns (lowercase)
            # IMPORTANT: We ADD columns instead of renaming, because stdpipe may
            # internally access the original column names (e.g., 'RAJ2000')

            # Find the RA column and create 'ra' alias
            ra_col = None
            dec_col = None
            for col in ["raMean", "_RAJ2000", "RAICRS", "RAJ2000", "RA", "ra"]:
                if col in cat.colnames:
                    ra_col = col
                    break
            for col in ["decMean", "_DEJ2000", "DEICRS", "DEJ2000", "DEC", "dec"]:
                if col in cat.colnames:
                    dec_col = col
                    break

            if ra_col and dec_col:
                # Add lowercase 'ra' and 'dec' columns (stdpipe standard)
                if "ra" not in cat.colnames:
                    cat["ra"] = cat[ra_col]
                if "dec" not in cat.colnames:
                    cat["dec"] = cat[dec_col]
                # Also add uppercase 'RA' and 'DEC' for compatibility
                if "RA" not in cat.colnames:
                    cat["RA"] = cat[ra_col]
                if "DEC" not in cat.colnames:
                    cat["DEC"] = cat[dec_col]
                st.write(f"✓ Added ra/dec columns from '{ra_col}'/'{dec_col}'")
            else:
                st.error(
                    f"ERROR: Could not find RA/DEC columns. Available: {cat.colnames}"
                )
                return []
        else:
            st.warning("Catalog query returned no sources")
            return []

    except Exception as e:
        st.error(f"Failed to query {catalog} catalog: {e}")
        import traceback

        st.error(traceback.format_exc())
        return []

    obj = Table(obj)
    if "flag" in obj.colnames and "flags" not in obj.colnames:
        obj["flag"].name = "flags"  # Rename to 'flags' for compatibility
    elif "flags" not in obj.colnames:
        obj["flags"] = np.zeros(len(obj), dtype=int)

    # Build vizier catalog list - include all useful catalogs
    # Note: 'apass' and 'gsc' excluded due to RA/DEC column naming issues in stdpipe
    vizier_catalogs = ["gaiaedr3", "ps1", "skymapper", "sdss", "atlas", "apass", "gsc"]

    # First, run filter_transient_candidates WITHOUT SkyBoT (it has column naming issues)
    try:
        candidates = pipeline.filter_transient_candidates(
            obj,
            cat=cat,
            # 'fwhm' is in pixels and 'pixscale' must be in deg/pixel (our
            # 'pixel_scale' is arcsec/pixel), so stdpipe derives the catalogue
            # matching radius as half the FWHM.  Previously an already
            # arcsec-scaled FWHM was passed with no pixscale, and the radius
            # silently fell back to a fixed 1 arcsec.
            fwhm=fwhm,
            pixscale=pixel_scale / 3600.0,
            time=header.get("DATE-OBS", None),
            skybot=False,  # Disable stdpipe's SkyBoT - we'll do our own
            vizier=vizier_catalogs,
            vizier_checker_fn=lambda xobj, xcat, catname: checker_fn(
                xobj,
                xcat,
                catname,
                filter_mag=filter_cat,
                mag_threshold=mag_threshold,
            ),
            ned=False,
            verbose=True,
            flagged=True,
            # SEP flag bits, not stdpipe/SExtractor ones (see SEP_REJECT_FLAGS):
            # the stdpipe default (0x7F00) never matches raw SEP flags.
            flagmask=SEP_REJECT_FLAGS,
        )
    except Exception as e:
        st.error(f"Error during transient filtering: {e}")
        import traceback

        st.error(traceback.format_exc())
        return []

    # Now apply our own SkyBoT filtering to remove Solar System objects
    obs_time = header.get("DATE-OBS", None)
    if candidates is not None and len(candidates) > 0 and obs_time is not None:
        candidates = filter_skybot_candidates(candidates, obs_time)

    if candidates is None:
        st.warning("No candidates returned from filtering.")
        return []

    # Remove candidates with invalid magnitude errors (inf or nan)
    valid_errors = np.isfinite(candidates["mag_calib_err"])
    n_invalid = len(candidates) - np.sum(valid_errors)
    if n_invalid > 0:
        candidates = candidates[valid_errors]

    st.success(
        f"Candidate filtering complete. Found {len(candidates)} potential transients."
    )

    if len(candidates) > 10:
        if len(candidates) > 50:
            st.warning(
                "⚠️ Too many candidates found (>50). Please refine your search criteria."
            )
            st.warning("(⚠️ Possibly due to a crowded field, filter band or bad S/N)")

    st.info(
        "Generating cutouts and retrieving template images for the first 20 candidates (best S/N first)..."
    )
    # Sort by best S/N: smallest mag_calib_err = highest S/N (S/N ≈ 1.0857 / mag_err)
    sorted_indices = np.argsort(candidates["mag_calib_err"])
    sorted_candidates = candidates[sorted_indices]
    for _, cand in list(enumerate(sorted_candidates))[:20]:
        # Convert row to dict for stdpipe compatibility
        cand_dict = {col: cand[col] for col in cand.colnames}
        # Create the cutout from image based on the candidate
        cutout = cutouts.get_cutout(image, cand_dict, 25, header=header)
        try:
            # Use the cutout's header with WCS for template retrieval
            # This ensures the HiPS image matches the cutout's field of view
            cutout["template"] = templates.get_hips_image(
                cat_cutout + filter_name, header=cutout.get("header"), get_header=False
            )
        except Exception as e:
            st.error(f"Failed to retrieve HiPS template image for candidate: {e}")
            st.error(f"  Attempted URL: {cat_cutout}{filter_name}")
            st.error(
                f"  Header info: RA={header.get('CRVAL1')}, Dec={header.get('CRVAL2')}"
            )
            cutout["template"] = None

        # Now we have two image planes in the cutout - let's display them
        fig = plot_cutout(
            cutout,
            # Image planes to display
            planes=["image", "template"],
            # Percentile-based scaling and asinh stretching for better visualization
            qq=[1, 99],
            stretch="linear",
        )
        st.pyplot(fig)
        plt.close(fig)

    # Let the Streamlit app warn the user when candidates rely on the aperture
    # fallback instead of PSF photometry.
    candidates.meta[TRANSIENT_MAGNITUDE_SOURCE_META] = magnitude_source
    return candidates


# Magnitude / error columns used for the Astro-Colibri cutout titles, in
# descending order of preference.
ASTROCOLIBRI_MAG_COLUMNS = ("psf_mag", "aperture_mag_1_3", "aperture_mag_1_1")
ASTROCOLIBRI_MAG_ERR_COLUMNS = (
    "psf_mag_err",
    "aperture_mag_err_1_3",
    "aperture_mag_err_1_1",
)


def _first_finite_value(row, columns):
    """Return the first finite float found in *row* for *columns*, else None."""
    for column in columns:
        if column in row and row[column] is not None:
            try:
                value = float(row[column])
            except (TypeError, ValueError):
                continue
            if np.isfinite(value):
                return value
    return None


def _with_prefixed_columns(row, legacy_columns):
    """Expand legacy names with filter-prefixed variants present in *row*.

    The exported table drops ``psf_mag`` & co. in favour of e.g.
    ``rapasg_psf_mag``, so both forms must be searched.
    """
    available = list(getattr(row, "index", row))
    expanded = []
    for legacy in legacy_columns:
        expanded.append(legacy)
        expanded.extend(
            c for c in available if c.endswith("_" + legacy) and c not in expanded
        )
    return expanded


def select_astrocolibri_magnitude(row):
    """Return the best available finite magnitude for an Astro-Colibri row."""
    return _first_finite_value(
        row, _with_prefixed_columns(row, ASTROCOLIBRI_MAG_COLUMNS)
    )


def select_astrocolibri_magnitude_error(row):
    """Return the best available finite magnitude error for a row."""
    return _first_finite_value(
        row, _with_prefixed_columns(row, ASTROCOLIBRI_MAG_ERR_COLUMNS)
    )


def sanitize_astrocolibri_name(name) -> str:
    """Return a filesystem-safe token derived from an Astro-Colibri name."""
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in str(name))


def _hips_survey_candidates(dec_center):
    """Return HiPS survey prefixes ordered by preference for a declination.

    SkyMapper (south) and PanSTARRS (north) overlap only around the equator,
    so the other survey is kept as a fallback when the first returns nothing.
    """
    if dec_center < 0:
        return ["SkyMapper/DR4/", "PanSTARRS/DR1/"]
    return ["PanSTARRS/DR1/", "SkyMapper/DR4/"]


def _fetch_hips_template(cutout_header, filter_name, dec_center):
    """Fetch a HiPS template matching *cutout_header*, trying both surveys.

    Returns ``(template, survey_prefix)``; ``template`` is None when no survey
    returned usable (finite, non-empty) data.
    """
    last_error = None
    for prefix in _hips_survey_candidates(dec_center):
        try:
            template = templates.get_hips_image(
                prefix + filter_name, header=cutout_header, get_header=False
            )
        except Exception as e:
            last_error = e
            continue
        if template is not None and np.isfinite(template).any():
            return template, prefix
    if last_error is not None:
        raise last_error
    return None, None


def plot_astrocolibri_cutouts(
    final_table,
    image,
    header,
    output_dir,
    base_filename,
    filter_name="r",
    dec_center=0.0,
):
    """Display and save cutout images for all Astro-Colibri cross-matches.

    Iterates over rows in *final_table* where ``astrocolibri_name`` is set,
    retrieves a HiPS survey template for the position, displays the image /
    template pair in the Streamlit app and saves a PNG to *output_dir* so it
    is included in the final ZIP archive.

    Parameters
    ----------
    final_table : pandas.DataFrame
        Enhanced photometry catalog (output of ``enhance_catalog``).
    image : numpy.ndarray
        2-D science image array.
    header : astropy FITS header
        Header of the science image (must contain WCS).
    output_dir : str
        Directory where PNG files are saved.
    base_filename : str
        Prefix for output file names.
    filter_name : str, optional
        Survey filter to use for the HiPS template (default: ``"r"``).
    dec_center : float, optional
        Declination of the field centre in degrees. Used to pick the
        hemisphere-appropriate survey (PanSTARRS north / SkyMapper south).
    """
    import os

    if final_table is None or len(final_table) == 0:
        return

    # Select rows with an Astro-Colibri match
    if "astrocolibri_name" not in final_table.columns:
        return

    ac_rows = final_table[final_table["astrocolibri_name"].notna()].copy()
    if len(ac_rows) == 0:
        return

    header, _ = fix_header(header)
    wcs = WCS(header)

    st.subheader("Astro-Colibri Match Cutouts")

    saved_paths = []
    for i, (_, row) in enumerate(ac_rows.iterrows()):
        # Build a minimal dict compatible with stdpipe's get_cutout
        ra = row.get("ra")
        dec = row.get("dec")
        if ra is None or dec is None or not (np.isfinite(ra) and np.isfinite(dec)):
            continue

        # Retrieve the best available magnitude / error for the title
        mag_val = select_astrocolibri_magnitude(row)
        mag_err_val = select_astrocolibri_magnitude_error(row)

        name = row.get("astrocolibri_name", f"AC_{i+1}")
        ac_type = row.get("astrocolibri_type", "")
        ac_class = row.get("astrocolibri_classification", "")

        # Derive pixel position from the WCS so the cutout is always centred
        # on the matched source, whatever the photometry column naming is.
        try:
            x, y = wcs.all_world2pix(float(ra), float(dec), 0)
        except Exception as e:
            st.warning(f"Could not compute pixel position for {name}: {e}")
            continue
        if not (np.isfinite(x) and np.isfinite(y)):
            continue
        cand_dict = {"ra": ra, "dec": dec, "x": float(x), "y": float(y)}

        try:
            cutout = cutouts.get_cutout(image, cand_dict, 25, header=header)
        except Exception as e:
            st.warning(f"Could not create cutout for {name}: {e}")
            continue

        survey = None
        try:
            cutout["template"], survey = _fetch_hips_template(
                cutout.get("header"), filter_name, float(dec)
            )
            if cutout["template"] is None:
                st.warning(f"No HiPS template coverage for {name}")
        except Exception as e:
            st.warning(f"Could not retrieve HiPS template for {name}: {e}")
            cutout["template"] = None

        # Build a descriptive title
        title_parts = [str(name)]
        if ac_type:
            title_parts.append(str(ac_type))
        if ac_class:
            title_parts.append(str(ac_class))
        if mag_val is not None:
            mag_str = f"mag={mag_val:.2f}"
            if mag_err_val is not None:
                mag_str += f" ± {mag_err_val:.3f}"
            title_parts.append(mag_str)
        if survey:
            title_parts.append(f"ref: {survey.rstrip('/')}/{filter_name}")
        title = " | ".join(title_parts)

        fig = plot_cutout(
            cutout,
            planes=["image", "template"],
            qq=[1, 99],
            stretch="linear",
            title=title,
            mark_ra=ra,
            mark_dec=dec,
            mark_r=8,
        )
        st.pyplot(fig)

        # Save PNG to output_dir so it ends up in the ZIP
        try:
            safe_name = sanitize_astrocolibri_name(name)
            png_filename = f"{base_filename}_astrocolibri_{i+1:02d}_{safe_name}.png"
            png_path = os.path.join(output_dir, png_filename)
            fig.savefig(png_path, dpi=100, bbox_inches="tight")
            saved_paths.append(png_path)
        except Exception as e:
            st.warning(f"Could not save Astro-Colibri cutout PNG: {e}")

        plt.close(fig)

    if saved_paths:
        st.success(
            f"Saved {len(saved_paths)} Astro-Colibri cutout image(s) to the results archive."
        )

    return saved_paths


def plot_cutout(
    cutout,
    planes=["image", "template"],
    fig=None,
    axs=None,
    mark_x=None,
    mark_y=None,
    mark_r=5.0,
    mark_r2=None,
    mark_r3=None,
    mark_color="red",
    mark_lw=2,
    mark_ra=None,
    mark_dec=None,
    show_title=True,
    title=None,
    additional_title=None,
    qq=None,
    stretch="linear",
    r0=None,
    **kwargs,
):
    r"""Routine for displaying various image planes from the cutout structure returned by :func:`stdpipe.cutouts.get_cutout`.

    The cutout planes are displayed in a single row, in the order defined by `planes` paremeters. Optionally, circular mark may be overlayed over the planes at the specified pixel position inside the cutout.

    :param cutout: Cutout structure as returned by :func:`stdpipe.cutouts.get_cutout`
    :param planes: List of names of cutout planes to show
    :param fig: Matplotlib figure where to plot, optional
    :param axs: Matplotlib axes same length as planes, optional
    :param mark_x: `x` coordinate of the overlay mark in cutout coordinates, optional
    :param mark_y: `y` coordinate of the overlay mark in cutout coordinates, optional
    :param mark_r: Radius of the overlay mark in cutout coordinates in pixels, optional
    :param mark_color: Color of the overlay mark, optional
    :param mark_lw: Line width of the overlay mark, optional
    :param mark_ra: Sky coordinate of the overlay mark, overrides `mark_x` and `mark_y`, optional
    :param mark_dec: Sky coordinate of the overlay mark, overrides `mark_x` and `mark_y`, optional
    :param r0: Smoothing kernel size (sigma) to be applied to the image and template planes, optional
    :param show_title: Show title over cutout. Defaults to True.
    :param title: The title to show above the cutouts, optional. If not provided, the title will be constructed from various pieces of cutout metadata, plus the contents of `additoonal_title` field, if provided
    :param additional_title: Additional text to append to automatically generated title of the cutout figure.
    :param \**kwargs: All additional parameters will be directly passed to :func:`stdpipe.plots.imshow` calls on individual images

    """
    curplot = 1
    nplots = len([_ for _ in planes if _ in cutout])

    if fig is None:
        fig = plt.figure(figsize=[nplots * 4, 4 + 1.0], dpi=75, tight_layout=True)

    if axs is not None:
        if not len(axs) == len(planes):
            raise ValueError("Number of axes must be same as number of cutouts")

    for ii, name in enumerate(planes):
        if name in cutout and cutout[name] is not None:
            if axs is not None:
                ax = axs[ii]
            else:
                ax = fig.add_subplot(1, nplots, curplot)
            curplot += 1

            # Set up parameters for stdpipe.plots.imshow
            params = {
                "stretch": "asinh" if name in ["image", "template"] else "linear",
                "r0": r0 if name in ["image", "template"] else None,
                "cmap": "Blues_r",
                "show_colorbar": False,
                "show_axis": False,
            }

            # Override with user-provided qq or stretch
            if qq is not None:
                params["qq"] = qq
            if stretch is not None and name == "image":
                params["stretch"] = stretch

            # Update with any additional user kwargs
            params.update(kwargs)

            # Use stdpipe's imshow for proper scaling
            plots.imshow(cutout[name], ax=ax, **params)
            ax.set_title(name.upper())

            # Mark overlays
            if mark_ra is not None and mark_dec is not None and cutout.get("wcs"):
                mark_x, mark_y = cutout["wcs"].all_world2pix(mark_ra, mark_dec, 0)

            if mark_x is not None and mark_y is not None:
                ax.add_artist(
                    Circle(
                        (mark_x, mark_y),
                        mark_r,
                        edgecolor=mark_color,
                        facecolor="none",
                        ls="-",
                        lw=mark_lw,
                    )
                )
                for _ in [mark_r2, mark_r3]:
                    if _ is not None:
                        ax.add_artist(
                            Circle(
                                (mark_x, mark_y),
                                _,
                                edgecolor=mark_color,
                                facecolor="none",
                                ls="--",
                                lw=mark_lw / 2,
                            )
                        )

    if show_title:
        if title is None:
            title = cutout["meta"].get("name", "unnamed")
            if "time" in cutout["meta"]:
                title += " at %s" % cutout["meta"]["time"].to_value("iso")
            if "mag_filter_name" in cutout["meta"]:
                title += " : " + cutout["meta"]["mag_filter_name"]
                if (
                    "mag_color_name" in cutout["meta"]
                    and "mag_color_term" in cutout["meta"]
                    and cutout["meta"]["mag_color_term"] is not None
                ):
                    title += " " + photometry.format_color_term(
                        cutout["meta"]["mag_color_term"],
                        color_name=cutout["meta"]["mag_color_name"],
                    )
            if "mag_limit" in cutout["meta"]:
                title += " : limit %.2f" % cutout["meta"]["mag_limit"]
            if "mag_calib" in cutout["meta"]:
                title += r" : psf_mag = %.2f $\pm$ %.2f" % (
                    cutout["meta"].get("mag_calib", np.nan),
                    cutout["meta"].get(
                        "mag_calib_err", cutout["meta"].get("magerr", np.nan)
                    ),
                )
            if additional_title:
                title += " : " + additional_title
        fig.suptitle(title)

    return fig


CALIBRATION_BAND_TO_FILTER = {
    "phot_g_mean_mag": "G",
    "phot_bp_mean_mag": "BP",
    "phot_rp_mean_mag": "RP",
    "u_jkc_mag": "U",
    "v_jkc_mag": "V",
    "b_jkc_mag": "B",
    "r_jkc_mag": "R",
    "i_jkc_mag": "I",
    "u_sdss_mag": "u",
    "gmag": "g",
    "rmag": "r",
    "imag": "i",
    "zmag": "z",
}

_KNOWN_FILTER_NAMES = ("u", "g", "r", "i", "z", "U", "B", "V", "R", "I", "G", "BP", "RP")


def normalize_calibration_filter(filter_mag, default="r"):
    """Return the canonical filter name (u,g,r,i,z,U,B,V,R,I,G,BP,RP).

    The calibration band is stored as a catalogue column name such as
    ``phot_g_mean_mag`` (Gaia G), ``phot_rp_mean_mag`` (Gaia RP) or
    ``rmag`` (PS1 r).  The former substring heuristic mistook Gaia RP for
    ``r`` and Gaia BP for nothing at all, and Gaia G for the PS1 ``g`` band,
    so the magnitudes were compared against the wrong catalogue column.
    """
    if not filter_mag:
        return default
    name = str(filter_mag).strip()
    if name in CALIBRATION_BAND_TO_FILTER:
        return CALIBRATION_BAND_TO_FILTER[name]
    stripped = name[:-3] if name.endswith("mag") else name
    if stripped in _KNOWN_FILTER_NAMES:
        return stripped
    lowered = name.lower()
    if "phot_bp" in lowered:
        return "BP"
    if "phot_rp" in lowered:
        return "RP"
    if "phot_g" in lowered:
        return "G"
    for filt in _KNOWN_FILTER_NAMES:
        if filt in stripped:
            return filt
    return stripped


def checker_fn(xobj, xcat, catname, filter_mag="r", mag_threshold=2.0):
    """Decide which reference-catalogue matches should *reject* a candidate.

    This is passed to :func:`stdpipe.pipeline.filter_transient_candidates` as
    its ``vizier_checker_fn``.  In stdpipe's contract the returned mask selects
    the catalogue entries that count as genuine matches - i.e. an entry marked
    ``True`` causes the corresponding detection to be **removed** from the
    candidate list, while ``False`` means "this is not really the same object,
    keep the detection as a candidate".

    The rule is therefore: a positional match is treated as the same object
    (``True``) unless the detected source is brighter than its catalogue
    counterpart by more than ``mag_threshold`` magnitudes, in which case the
    flux excess is a possible transient/variable and the detection is kept
    (``False``).  A common zero-point offset between the detection and the
    catalogue is removed first (median of the matched differences), so that
    the comparison reflects a real brightness excess rather than calibration
    differences.

    Parameters
    ----------
    xobj : astropy.table.Table or numpy.ndarray
        Detected objects with a 'mag_calib' column.
    xcat : astropy.table.Table or numpy.ndarray
        Reference catalogue for comparison.
    catname : str
        Name of the catalogue (for informational purposes).
    filter_mag : str, default 'r'
        Filter name (e.g., 'r', 'rmag', 'V', 'Vmag', 'phot_g_mean_mag').
    mag_threshold : float, default 2.0
        Minimum brightness excess (in magnitudes) required for a matched
        detection to remain a candidate.

    Returns
    -------
    numpy.ndarray
        Boolean mask over *xcat*: ``True`` = confirmed catalogue match (the
        detection is rejected), ``False`` = significant brightness excess (the
        detection is kept).  When no magnitude column can be identified, all
        entries are marked ``True`` (no brightening evidence, so nothing is
        rescued by this catalogue); entries with an unusable magnitude
        difference are kept instead.
    """
    # Default: treat every catalogue entry as a confirmed match (i.e. no
    # detection is rescued by this catalogue) unless a magnitude comparison
    # shows a genuine brightness excess below.
    xidx = np.ones_like(xobj, dtype=bool)

    fname = normalize_calibration_filter(filter_mag)

    # Find the corresponding magnitude column in the reference catalog
    cat_col_mag, _ = guess_catalogue_mag_columns(fname, xcat)

    # Log which catalog is being checked and result
    if cat_col_mag is not None:
        st.write(
            f"✓ {catname.upper()}: Found magnitude column '{cat_col_mag}' for filter '{fname}'"
        )
    else:
        st.write(f"✗ {catname.upper()}: No magnitude column found for filter '{fname}'")

    if cat_col_mag is not None:
        mag = xobj["mag_calib"].copy()

        # Apply AB magnitude corrections for Johnson-Cousins filters when using AB catalogs
        if fname in ["U", "B", "V", "R", "I"] and cat_col_mag not in [
            "Umag",
            "Bmag",
            "Vmag",
            "Rmag",
            "Imag",
        ]:
            mag += filter_ab_offset.get(fname, 0)

        # Compute magnitude difference: detected vs. catalog
        # Use np.array() to ensure we have a writable copy (astropy columns are read-only)
        diff = np.array(mag - xcat[cat_col_mag])

        # If sufficient valid measurements, remove systematic zeropoint offset
        # This accounts for photometric calibration differences
        if len(diff[np.isfinite(diff)]) > 10:
            median_diff = np.nanmedian(diff)
            diff -= median_diff

        # diff = detected - catalogue, so a source brighter than the catalogue
        # has diff < 0.  Mark a match as confirmed (True -> the detection is
        # removed) unless the excess is larger than mag_threshold.  Objects
        # whose difference cannot be evaluated (NaN magnitude) are kept as
        # candidates rather than being silently discarded.
        finite = np.isfinite(diff)
        xidx = np.where(finite, diff > -mag_threshold, False)

    return xidx


def guess_catalogue_mag_columns(fname, cat, augmented_only=False):
    """Find magnitude column in catalog, with fallback to closest filter alternative.

    Parameters
    ----------
    fname : str
        Filter name (e.g., 'g', 'r', 'BP', 'RP')
    cat : astropy.table.Table or similar
        Catalog with colnames attribute
    augmented_only : bool
        If True, raise error if exact filter not found in augmented catalogs

    Returns
    -------
    tuple
        (magnitude_column_name, magnitude_error_column_name) or (None, None)
    """
    # Map of filter similarities: if exact filter not found, try alternatives in order
    filter_alternatives = {
        "u": ["u", "U"],
        "g": ["g", "G", "gmag"],
        "r": ["r", "R", "rmag"],
        "i": ["i", "I", "imag"],
        "z": ["z"],
        "U": ["U", "u", "gmag"],
        "B": ["g", "gmag", "BPmag"],
        "V": ["g", "r", "gmag", "rmag"],
        "R": ["r", "rmag", "RPmag"],
        "I": ["i", "imag", "RPmag"],
        "BP": ["g", "gmag", "BPmag"],
        "G": ["g", "r", "gmag", "rmag"],
        "RP": ["r", "rmag", "RPmag"],
    }

    cat_col_mag = None
    cat_col_mag_err = None

    # Most of augmented catalogues
    if f"{fname}mag" in cat.colnames:
        cat_col_mag = f"{fname}mag"
        if f"e_{fname}mag" in cat.colnames:
            cat_col_mag_err = f"e_{fname}mag"

    elif augmented_only:
        raise RuntimeError(f"Unsupported filter {fname} for this catalogue")

    # Gaia DR2/eDR3/DR3 from XMatch (check before PS1 as it's more specific)
    elif (
        "phot_bp_mean_mag" in cat.colnames
        and "phot_rp_mean_mag" in cat.colnames
        and "phot_g_mean_mag" in cat.colnames
    ):
        if fname in ["B", "U", "u"]:
            cat_col_mag = "phot_bp_mean_mag"  # BP is blue photometer
        elif fname in ["I", "i", "z"]:
            cat_col_mag = "phot_rp_mean_mag"  # RP is red photometer
        elif fname in ["g", "G", "V", "r", "R"]:
            cat_col_mag = "phot_g_mean_mag"  # G band (green) matches optical g,r
        elif fname == "BP":
            cat_col_mag = "phot_bp_mean_mag"
        elif fname == "RP":
            cat_col_mag = "phot_rp_mean_mag"
        else:
            cat_col_mag = "phot_g_mean_mag"
        if f"{cat_col_mag}_error" in cat.colnames:
            cat_col_mag_err = f"{cat_col_mag}_error"

    # Gaia DR2/eDR3/DR3 from Vizier
    elif "BPmag" in cat.colnames and "RPmag" in cat.colnames and "Gmag" in cat.colnames:
        if fname in ["U", "B", "u", "g", "BP"]:
            cat_col_mag = "BPmag"
        elif fname in ["I", "i", "z", "RP"]:
            cat_col_mag = "RPmag"
        else:
            # G, V, R, r: closest to the broad Gaia G band (same as XMatch)
            cat_col_mag = "Gmag"
        if f"e_{cat_col_mag}" in cat.colnames:
            cat_col_mag_err = f"e_{cat_col_mag}"

    # SDSS (ugriz system)
    elif "psfMag_u" in cat.colnames or "u" in cat.colnames:
        mag_map = {
            "u": "psfMag_u",
            "g": "psfMag_g",
            "r": "psfMag_r",
            "i": "psfMag_i",
            "z": "psfMag_z",
        }
        if fname in mag_map and mag_map[fname] in cat.colnames:
            cat_col_mag = mag_map[fname]
            err_col = mag_map[fname].replace("psfMag", "psfMagErr")
            if err_col in cat.colnames:
                cat_col_mag_err = err_col
        else:
            # Try alternatives from filter similarity map
            for alt_filter in filter_alternatives.get(fname, []):
                if alt_filter in mag_map and mag_map[alt_filter] in cat.colnames:
                    cat_col_mag = mag_map[alt_filter]
                    err_col = mag_map[alt_filter].replace("psfMag", "psfMagErr")
                    if err_col in cat.colnames:
                        cat_col_mag_err = err_col
                    break

    # APASS (also has mag errors)
    elif f"mag{fname}" in cat.colnames:
        cat_col_mag = f"mag{fname}"
        if f"e_mag{fname}" in cat.colnames:
            cat_col_mag_err = f"e_mag{fname}"
    else:
        # APASS fallback: try alternatives
        for alt_filter in filter_alternatives.get(fname, []):
            if f"mag{alt_filter}" in cat.colnames:
                cat_col_mag = f"mag{alt_filter}"
                if f"e_mag{alt_filter}" in cat.colnames:
                    cat_col_mag_err = f"e_mag{alt_filter}"
                break

    # ATLAS
    if cat_col_mag is None and "m" in cat.colnames:  # ATLAS uses 'm' for magnitude
        if fname in ["r", "i"] or fname in filter_alternatives.get(fname, []):
            cat_col_mag = "m"
            if "dm" in cat.colnames:
                cat_col_mag_err = "dm"

    # Non-augmented PS1 etc (check last as more generic)
    if cat_col_mag is None and "gmag" in cat.colnames and "rmag" in cat.colnames:
        if fname in ["U", "B", "V", "BP"]:
            cat_col_mag = "gmag"
        elif fname in ["R", "G"]:
            cat_col_mag = "rmag"
        elif fname in ["I", "RP"]:
            cat_col_mag = "imag"
        else:
            # Try filter alternatives
            for alt_filter in filter_alternatives.get(fname, []):
                if (
                    alt_filter in ["U", "B", "V", "BP", "G", "g"]
                    and "gmag" in cat.colnames
                ):
                    cat_col_mag = "gmag"
                    break
                elif alt_filter in ["R", "r"] and "rmag" in cat.colnames:
                    cat_col_mag = "rmag"
                    break
                elif alt_filter in ["I", "i"] and "imag" in cat.colnames:
                    cat_col_mag = "imag"
                    break

        if cat_col_mag and f"e_{cat_col_mag}" in cat.colnames:
            cat_col_mag_err = f"e_{cat_col_mag}"

    # SkyMapper
    if cat_col_mag is None and f"{fname}PSF" in cat.colnames:
        cat_col_mag = f"{fname}PSF"
        if f"e_{fname}PSF" in cat.colnames:
            cat_col_mag_err = f"e_{fname}PSF"
    elif cat_col_mag is None:
        # SkyMapper fallback: try alternatives
        for alt_filter in filter_alternatives.get(fname, []):
            if f"{alt_filter}PSF" in cat.colnames:
                cat_col_mag = f"{alt_filter}PSF"
                if f"e_{alt_filter}PSF" in cat.colnames:
                    cat_col_mag_err = f"e_{alt_filter}PSF"
                break

    return cat_col_mag, cat_col_mag_err
