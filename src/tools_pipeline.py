# Third-Party Imports
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from astropy.wcs import WCS
from astropy.coordinates import SkyCoord
from astropy import units as u

import streamlit as st

from astropy.stats import SigmaClip
from astropy.visualization import ZScaleInterval
from src.utils import get_header_value

from photutils.background import Background2D, SExtractorBackground

# Constants
FIGURE_SIZES = {
    "small": (6, 5),  # For small plots
    "medium": (8, 6),  # For medium plots
    "large": (10, 8),  # For large plots
    "wide": (12, 6),  # For wide plots
    "stars_grid": (10, 8),  # For grid of stars
}

URL = "https://astro-colibri.science/"

GAIA_BANDS = [
    ("Rapas G", "phot_g_mean_mag"),
    ("Rapas Bp", "phot_bp_mean_mag"),
    ("Rapas Rp", "phot_rp_mean_mag"),
    ("U", "u_jkc_mag"),
    ("V", "v_jkc_mag"),
    ("B", "b_jkc_mag"),
    ("R", "r_jkc_mag"),
    ("I", "i_jkc_mag"),
    ("u", "u_sdss_mag"),
    ("g", "gmag"),
    ("r", "rmag"),
    ("i", "imag"),
    ("z", "zmag"),
]


def get_filter_prefix(filter_band: str | None) -> str | None:
    """Return a normalized column prefix for the selected calibration filter.

    Parameters
    ----------
    filter_band : str or None
        Internal calibration filter name, e.g. ``phot_g_mean_mag``.

    Returns
    -------
    str or None
        Lowercase prefix such as ``rapasg`` or ``rapasbp``. Returns None when
        the filter is missing or unknown.
    """
    if not filter_band:
        return None

    for label, field in GAIA_BANDS:
        if field == filter_band:
            return label.strip().lower().replace(" ", "")

    return None


def get_prefixed_photometry_column(column_name: str, filter_band: str | None) -> str | None:
    """Return the prefixed photometry column name for the selected filter.

    Parameters
    ----------
    column_name : str
        Legacy photometry column name such as ``psf_mag``.
    filter_band : str or None
        Selected calibration filter band.

    Returns
    -------
    str or None
        Prefixed column name such as ``rapasg_psf_mag`` or None when no
        matching filter prefix exists.
    """
    filter_prefix = get_filter_prefix(filter_band)
    if not filter_prefix:
        return None
    return f"{filter_prefix}_{column_name}"


def drop_legacy_magnitude_columns(final_table, filter_band=None):
    """Remove legacy magnitude columns when prefixed aliases are available.

    This keeps internal processing free to use the legacy names while allowing
    the exported and user-facing table to expose only the filter-prefixed
    magnitude columns.
    """
    if final_table is None or len(final_table) == 0:
        return final_table

    filter_prefix = get_filter_prefix(filter_band)
    if not filter_prefix:
        return final_table

    cols_to_drop = []
    for col in list(final_table.columns):
        is_legacy_mag_col = (
            col == "psf_mag"
            or col == "psf_mag_err"
            or col == "psf_instrumental_mag"
            or col.startswith("aperture_mag_")
            or col.startswith("aperture_mag_err_")
            or col.startswith("instrumental_mag_bkg_corr_")
            or col.startswith("instrumental_mag_")
        )
        if not is_legacy_mag_col:
            continue

        prefixed_col = f"{filter_prefix}_{col}"
        if prefixed_col in final_table.columns:
            cols_to_drop.append(col)

    if cols_to_drop:
        final_table = final_table.drop(columns=cols_to_drop)

    return final_table

# Expanded filter dictionary to handle many common filter name variations
FILTER_DICT = {
    # Johnson-Cousins system
    "u": "u_jkc_mag",
    "b": "b_jkc_mag",
    "v": "v_jkc_mag",
    "r": "r_jkc_mag",
    "i": "i_jkc_mag",
    "johnson_u": "u_jkc_mag",
    "johnson_b": "b_jkc_mag",
    "johnson_v": "v_jkc_mag",
    "cousins_r": "r_jkc_mag",
    "cousins_i": "i_jkc_mag",

    # SDSS system
    "u_sdss": "u_sdss_mag",
    "g_sdss": "gmag",
    "r_sdss": "rmag",
    "i_sdss": "imag",
    "z_sdss": "zmag",
    "sdss_u": "u_sdss_mag",
    "sdss_g": "gmag",
    "sdss_r": "rmag",
    "sdss_i": "imag",
    "sdss_z": "zmag",
    "g": "gmag",
    "z": "zmag",

    # Gaia system
    "gaia_g": "phot_g_mean_mag",
    "gaia_bp": "phot_bp_mean_mag",
    "gaia_rp": "phot_rp_mean_mag",
    "bp": "phot_bp_mean_mag",
    "rp": "phot_rp_mean_mag",
    "g_gaia": "phot_g_mean_mag",
    "bp_gaia": "phot_bp_mean_mag",
    "rp_gaia": "phot_rp_mean_mag",

    # Common clear/luminance filters
    "clear": "phot_g_mean_mag",
    "lum": "phot_g_mean_mag",
    "l": "phot_g_mean_mag",
    "luminance": "phot_g_mean_mag",
    "a": "phot_g_mean_mag",  # Common in amateur setups
    "c": "phot_rp_mean_mag",
    "open": "phot_g_mean_mag",
    "w": "phot_g_mean_mag",  # Wide/White

    # Color filters (RGB)
    "red": "rmag",
    "green": "gmag",
    "blue": "b_jkc_mag",
    "rgb_r": "rmag",
    "rgb_g": "gmag",
    "rgb_b": "b_jkc_mag",

    # Sloan variations with apostrophes
    "u'": "u_sdss_mag",
    "g'": "gmag",
    "r'": "rmag",
    "i'": "imag",
    "z'": "zmag",
    "sloan_u": "u_sdss_mag",
    "sloan_g": "gmag",
    "sloan_r": "rmag",
    "sloan_i": "imag",
    "sloan_z": "zmag",

    # Pan-STARRS (map to closest SDSS equivalents)
    "ps_g": "gmag",
    "ps_r": "rmag",
    "ps_i": "imag",
    "ps_z": "zmag",
    "ps1_g": "gmag",
    "ps1_r": "rmag",
    "ps1_i": "imag",
    "ps1_z": "zmag",

    # Common typos and variations
    "clr": "phot_g_mean_mag",
    "lumn": "phot_g_mean_mag",
    "vis": "v_jkc_mag",
    "visual": "v_jkc_mag",
    "ha": "rmag",  # H-alpha, map to r
    "oiii": "gmag",  # OIII, map to g

    # Telescope-specific common names
    "astrodon_r": "rmag",
    "astrodon_g": "gmag",
    "astrodon_b": "b_jkc_mag",
    "baader_r": "rmag",
    "baader_g": "gmag",
    "baader_b": "b_jkc_mag",

    # Default/unknown
    "unknown": "phot_g_mean_mag",
    "none": "phot_g_mean_mag",
    "": "phot_g_mean_mag",
}


def safe_wcs_create(header):
    """
    Safely create an Astropy WCS object from a FITS header.

    Validates the presence of required WCS keywords, handles potential errors
    during WCS object creation, and attempts to simplify higher-dimensional WCS
    objects to celestial coordinates. Also attempts to remove potentially
    problematic non-standard keywords before WCS creation.

    Parameters
    ----------
    header : astropy.io.fits.Header or dict-like
        The FITS header containing WCS information.

    Returns
    -------
    tuple (astropy.wcs.WCS | None, str | None, list[str])
        - (wcs_object, None, log_messages): If successful, returns the created WCS object
          and None for the error message.
        - (None, error_message, log_messages): If failed, returns None for the WCS object
          and a string describing the error (e.g., missing keywords,
          creation error, invalid object).

    Notes
    -----
    - Required WCS keywords checked: CTYPE1, CTYPE2, CRVAL1, CRVAL2,
      CRPIX1, CRPIX2.
    - Attempts to remove 'XPIXELSZ', 'YPIXELSZ', 'CDELTM1', 'CDELTM2' if
      present.
    - If the initial WCS object has more than 2 pixel dimensions, it attempts
      to extract the celestial WCS using `wcs_obj.celestial`.
    - Validates that the final WCS object has transformation attributes
      (`.wcs`).
    """
    log_messages = []
    if not header:
        return None, "No header provided", log_messages

    # Create a copy of the header to avoid modifying the original
    try:
        working_header = header.copy()
    except AttributeError:
        # Handle case where header is a regular dict
        working_header = dict(header)

    # Remove problematic keywords that can interfere with WCS
    problematic_keywords = [
        "XPIXELSZ",
        "YPIXELSZ",
        "CDELTM1",
        "CDELTM2",
        "PIXSCALE",
        "SCALE",
        "XORGSUBF",
        "YORGSUBF",
    ]

    removed_keywords = []
    for keyword in problematic_keywords:
        if keyword in working_header:
            try:
                del working_header[keyword]
                removed_keywords.append(keyword)
            except KeyError:
                pass

    if removed_keywords:
        log_messages.append("INFO: Removed problematic WCS keywords")

    required_keys = ["CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2"]
    missing_keys = [key for key in required_keys if key not in working_header]

    if missing_keys:
        return (
            None,
            f"Missing required WCS keywords: {', '.join(missing_keys)}",
            log_messages,
        )

    try:
        wcs_obj = WCS(working_header)

        if wcs_obj is None:
            return None, "WCS creation returned None", log_messages

        if wcs_obj.pixel_n_dim > 2:
            wcs_obj = wcs_obj.celestial

        if not hasattr(wcs_obj, "wcs"):
            return (
                None,
                "Created WCS object has no transformation attributes",
                log_messages,
            )

        return wcs_obj, None, log_messages
    except Exception as e:
        return None, f"WCS creation error: {str(e)}", log_messages


def fix_header(header):
    """
    Fix common issues in FITS headers based on stdweb processing approach.

    Parameters
    ----------
    header : astropy.io.fits.Header
        FITS header object to fix

    Returns
    -------
    tuple (astropy.io.fits.Header, list[str])
        - (fixed_header, log_messages): Returns the fixed header object and a list of log messages.
    """
    log_messages = []
    try:
        # Create a copy to avoid modifying the original
        fixed_header = header.copy()

        # Define problematic keywords to remove
        problematic_keywords = [
            "XPIXELSZ",
            "YPIXELSZ",  # Pixel size keywords that can conflict with WCS
            "CDELTM1",
            "CDELTM2",  # Alternative delta keywords
            "PIXSCALE",  # Non-standard pixel scale
            "SCALE",  # Generic scale keyword
            "XORGSUBF",
            "YORGSUBF",  # Origin subframe keywords
        ]

        # Remove problematic keywords
        removed_keywords = []
        for keyword in problematic_keywords:
            if keyword in fixed_header:
                try:
                    del fixed_header[keyword]
                    removed_keywords.append(keyword)
                except KeyError:
                    pass

        if removed_keywords:
            log_messages.append(
                f"INFO: Removed problematic keywords: {', '.join(removed_keywords)}"
            )

        # Define WCS keywords to check for problems
        wcs_keywords = {
            "CD": ["CD1_1", "CD1_2", "CD2_1", "CD2_2"],
            "PC": ["PC1_1", "PC1_2", "PC2_1", "PC2_2"],
            "CDELT": ["CDELT1", "CDELT2"],
            "CRPIX": ["CRPIX1", "CRPIX2"],
            "CRVAL": ["CRVAL1", "CRVAL2"],
            "CTYPE": ["CTYPE1", "CTYPE2"],
        }

        # Check for problematic values and remove entire WCS if found
        remove_all_wcs = False

        # Check CD matrix for singularity
        cd_keys = wcs_keywords["CD"]
        if all(key in fixed_header for key in cd_keys):
            try:
                cd11 = float(fixed_header["CD1_1"])
                cd12 = float(fixed_header["CD1_2"])
                cd21 = float(fixed_header["CD2_1"])
                cd22 = float(fixed_header["CD2_2"])

                # Calculate determinant to check for singularity
                determinant = cd11 * cd22 - cd12 * cd21

                # Check for singular matrix or invalid values
                if (
                    abs(determinant) < 1e-15
                    or any(not np.isfinite(val) for val in [cd11, cd12, cd21, cd22])
                    or any(val == 0 for val in [cd11, cd22])
                ):
                    log_messages.append(
                        "WARNING: Detected problematic CD matrix - removing all WCS keywords"
                    )
                    remove_all_wcs = True

            except (ValueError, TypeError):
                log_messages.append(
                    "WARNING: Invalid CD matrix values - removing all WCS keywords"
                )
                remove_all_wcs = True

        # Check for obviously fake coordinate values
        if "CRVAL1" in fixed_header and "CRVAL2" in fixed_header:
            try:
                ra = float(fixed_header["CRVAL1"])
                dec = float(fixed_header["CRVAL2"])

                # Check for clearly fake values (exactly 0,0 or out of range)
                if (
                    (ra == 0.0 and dec == 0.0)
                    or not (0 <= ra < 360)
                    or not (-90 <= dec <= 90)
                    or not np.isfinite(ra)
                    or not np.isfinite(dec)
                ):
                    log_messages.append(
                        f"WARNING: Detected fake coordinates (RA={ra}, DEC={dec}) - removing all WCS keywords"
                    )
                    remove_all_wcs = True

            except (ValueError, TypeError):
                log_messages.append(
                    "WARNING: Invalid coordinate values - removing all WCS keywords"
                )
                remove_all_wcs = True

        # Check for invalid pixel reference points
        if "CRPIX1" in fixed_header and "CRPIX2" in fixed_header:
            try:
                crpix1 = float(fixed_header["CRPIX1"])
                crpix2 = float(fixed_header["CRPIX2"])

                # Check for obviously wrong values
                if (
                    not np.isfinite(crpix1)
                    or not np.isfinite(crpix2)
                    or crpix1 <= 0
                    or crpix2 <= 0
                ):
                    log_messages.append(
                        "WARNING: Detected invalid CRPIX values - removing all WCS keywords"
                    )
                    remove_all_wcs = True

            except (ValueError, TypeError):
                remove_all_wcs = True

        # Check for invalid CTYPE values
        for ctype_key in ["CTYPE1", "CTYPE2"]:
            if ctype_key in fixed_header:
                ctype_val = str(fixed_header[ctype_key]).strip()
                # If CTYPE is empty or contains obvious placeholder text
                if (
                    not ctype_val
                    or ctype_val.lower() in ["", "none", "null", "undefined"]
                    or len(ctype_val) < 3
                ):
                    log_messages.append(
                        f"WARNING: Detected invalid {ctype_key} value - removing all WCS keywords"
                    )
                    remove_all_wcs = True

        # Remove all WCS keywords if problems detected
        if remove_all_wcs:
            log_messages.append(
                "INFO: Removing all WCS keywords due to detected problems"
            )

            # Remove all WCS-related keywords
            keys_to_remove = []
            for key in list(fixed_header.keys()):
                if (
                    key.startswith("CD")
                    or key.startswith("PC")
                    or key.startswith("CDELT")
                    or key.startswith("CRPIX")
                    or key.startswith("CRVAL")
                    or key.startswith("CTYPE")
                    or key.startswith("CROTA")
                    or key.startswith("PV")
                    or key.startswith("PROJP")
                    or key in ["EQUINOX", "EPOCH", "RADESYS", "LONPOLE", "LATPOLE"]
                ):
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                try:
                    del fixed_header[key]
                except KeyError:
                    pass

            log_messages.append(
                f"INFO: Removed {len(keys_to_remove)} WCS keywords: {', '.join(keys_to_remove[:10])}{'...' if len(keys_to_remove) > 10 else ''}"
            )

            # Add minimal default WCS if image dimensions are available
            if "NAXIS1" in fixed_header and "NAXIS2" in fixed_header:
                try:
                    naxis1 = int(fixed_header["NAXIS1"])
                    naxis2 = int(fixed_header["NAXIS2"])

                    fixed_header["CTYPE1"] = "RA---TAN"
                    fixed_header["CTYPE2"] = "DEC--TAN"
                    fixed_header["CRPIX1"] = naxis1 / 2.0
                    fixed_header["CRPIX2"] = naxis2 / 2.0
                    fixed_header["CRVAL1"] = 0.0  # Will need manual input
                    fixed_header["CRVAL2"] = 0.0  # Will need manual input
                    fixed_header["EQUINOX"] = 2000.0
                    fixed_header["RADESYS"] = "ICRS"

                    log_messages.append(
                        "INFO: Added minimal default WCS (1 arcsec/pixel) - coordinates will need manual input"
                    )

                except (ValueError, TypeError):
                    log_messages.append(
                        "WARNING: Could not add default WCS due to invalid NAXIS values"
                    )

        else:
            # If WCS seems valid, apply minor fixes

            # Fix CTYPE formatting
            if "CTYPE1" in fixed_header:
                ctype1 = str(fixed_header["CTYPE1"]).strip()
                if ctype1 and not ctype1.endswith("---"):
                    if "RA" in ctype1.upper():
                        fixed_header["CTYPE1"] = "RA---TAN"
                    elif "GLON" in ctype1.upper():
                        fixed_header["CTYPE1"] = "GLON-TAN"

            if "CTYPE2" in fixed_header:
                ctype2 = str(fixed_header["CTYPE2"]).strip()
                if ctype2 and not ctype2.endswith("---"):
                    if "DEC" in ctype2.upper():
                        fixed_header["CTYPE2"] = "DEC--TAN"
                    elif "GLAT" in ctype2.upper():
                        fixed_header["CTYPE2"] = "GLAT-TAN"

            # Fix missing EQUINOX/EPOCH
            if "EQUINOX" not in fixed_header and "EPOCH" in fixed_header:
                fixed_header["EQUINOX"] = fixed_header["EPOCH"]
            elif "EQUINOX" not in fixed_header:
                fixed_header["EQUINOX"] = 2000.0

            # Fix RADESYS if missing
            if "RADESYS" not in fixed_header:
                if fixed_header.get("EQUINOX", 2000.0) == 2000.0:
                    fixed_header["RADESYS"] = "ICRS"
                else:
                    fixed_header["RADESYS"] = "FK5"

            # Remove PC matrix if CD matrix is present (they conflict)
            if all(key in fixed_header for key in ["CD1_1", "CD2_2"]):
                pc_keys = ["PC1_1", "PC1_2", "PC2_1", "PC2_2"]
                removed_pc = []
                for key in pc_keys:
                    if key in fixed_header:
                        del fixed_header[key]
                        removed_pc.append(key)
                if removed_pc:
                    log_messages.append(
                        f"INFO: Removed conflicting PC matrix keywords: {', '.join(removed_pc)}"
                    )

        return fixed_header, log_messages

    except Exception as e:
        # If fixing fails completely, return original header
        log_messages.append(f"ERROR: Header fixing failed: {str(e)}")
        return header, log_messages


def extract_coordinates(header):
    """
    Extract celestial coordinates (RA, Dec) from a FITS header.

    Attempts to find Right Ascension (RA) and Declination (Dec) values by
    checking a predefined list of common FITS keywords. Validates that the
    extracted values are numeric and fall within reasonable astronomical
    ranges.

    Parameters
    ----------
    header : astropy.io.fits.Header or dict-like or None
        The FITS header object or dictionary to search for coordinate keywords.
        If None, returns (None, None, "No header available").

    Returns
    -------
    tuple (float | None, float | None, str)
        - (ra, dec, source_description): If coordinates are found and valid,
          returns RA (degrees), Dec (degrees), and a string indicating the
          keywords used (e.g., "RA/DEC", "OBJRA/OBJDEC").
        - (None, None, error_message): If coordinates are not found, are
          non-numeric, or fall outside valid ranges (RA: -360 to 360,
          Dec: -90 to 90), returns None for RA and Dec, and a string
          describing the issue (e.g., "Coordinates not found in header",
          "Invalid RA value: ...", "Non-numeric coordinates: ...").

    Notes
    -----
    - RA keywords checked (in order): 'RA', 'OBJRA', 'RA---', 'CRVAL1'.
    - Dec keywords checked (in order): 'DEC', 'OBJDEC', 'DEC---', 'CRVAL2'.
    - Validation ranges: RA [-360, 360], Dec [-90, 90].
    """
    if header is None:
        return None, None, "No header available"

    ra_keys = ["RA", "OBJRA", "RA---", "CRVAL1"]
    dec_keys = ["DEC", "OBJDEC", "DEC---", "CRVAL2"]

    ra = get_header_value(header, ra_keys)
    dec = get_header_value(header, dec_keys)

    if ra is not None and dec is not None:
        ra_source = next((k for k in ra_keys if k in header), "unknown")
        dec_source = next((k for k in dec_keys if k in header), "unknown")
        source = f"{ra_source}/{dec_source}"

        try:
            ra_val = float(ra)
            dec_val = float(dec)

            if not (-360 <= ra_val <= 360):
                return None, None, f"Invalid RA value: {ra_val}"
            if not (-90 <= dec_val <= 90):
                return None, None, f"Invalid DEC value: {dec_val}"

            return ra_val, dec_val, source
        except (ValueError, TypeError):
            return None, None, f"Non-numeric coordinates: RA={ra}, DEC={dec}"

    return None, None, "Coordinates not found in header"


def extract_pixel_scale(header):
    """
    Extract or calculate the pixel scale (in arcseconds per pixel) from a
    FITS header.

    Tries multiple methods in order of preference:
    1. Looks for direct pixel scale keywords ('PIXSIZE', 'PIXSCALE',
       'PIXELSCAL').
    2. Uses CD matrix elements (CD1_1, CD2_2) converted from deg to arcsec.
    3. Calculates from pixel size ('XPIXSZ') and focal length ('FOCALLEN'),
       using the simple formula: 206 × pixel_size_microns / focal_length_mm.
    4. If none of the above succeed, returns a default value.

    Parameters
    ----------
    header : astropy.io.fits.Header or dict-like or None
        The FITS header object or dictionary containing metadata. If None,
        returns (1.0, "default (no header)").

    Returns
    -------
    tuple (float, str)
        - (pixel_scale_value, source_description): Returns the determined pixel
          scale in arcseconds per pixel and a string describing how it was
          obtained.
        - The default pixel scale value returned if no information is found is
          1.0 arcsec/pixel.

    Notes
    -----
    - Uses the simple formula: pixel_scale = 206 × pixel_size_microns / focal_length_mm
    - Assumes XPIXSZ is in micrometers unless XPIXSZU specifies otherwise.
    """
    if header is None:
        return 1.0, "default (no header)"

    # Method 1: Direct pixel scale keywords (these should be in arcsec/pixel)
    for key in ["PIXSIZE", "PIXSCALE", "PIXELSCAL", "SECPIX"]:
        if key in header:
            value = float(header[key])
            # Sanity check: pixel scale should be reasonable (0.01 to 100 arcsec/pixel)
            if 0.01 <= value <= 100:
                return value, f"from {key}"

    # Method 2: CD matrix elements (most common after plate solving)
    if "CD1_1" in header and "CD2_2" in header:
        cd1_1 = abs(float(header["CD1_1"]))
        cd2_2 = abs(float(header["CD2_2"]))

        # Take average of both diagonal elements and convert from degrees to arcsec
        avg_cd = (cd1_1 + cd2_2) / 2.0
        scale = avg_cd * 3600.0

        # Sanity check: CD matrix values should result in reasonable pixel scales
        if 0.01 <= scale <= 100:
            return scale, f"from CD matrix (CD1_1={cd1_1:.2e}, CD2_2={cd2_2:.2e})"

    # Method 3: Calculate from pixel size and focal length using simple formula
    focal_length = None
    pixel_size = None

    # Check for focal length keywords
    for focal_key in ["FOCALLEN", "FOCAL", "FOCLEN", "FL"]:
        if focal_key in header:
            focal_length = float(header[focal_key])
            break

    # Check for pixel size keywords
    for pixel_key in ["PIXELSIZE", "PIXSIZE", "XPIXSZ", "YPIXSZ", "PIXELMICRONS"]:
        if pixel_key in header:
            pixel_size = float(header[pixel_key])
            break

    if focal_length is not None and pixel_size is not None and focal_length > 0:
        xpixsz_unit = header.get("XPIXSZU", "").strip().lower()

        # Handle different units
        if xpixsz_unit == "mm":
            # Convert mm to microns
            pixel_size_microns = pixel_size * 1000.0
            unit_desc = "mm"
        elif xpixsz_unit in ["um", "μm", "micron", "microns"]:
            pixel_size_microns = pixel_size
            unit_desc = "μm"
        else:
            # Default assumption: micrometers
            pixel_size_microns = pixel_size
            unit_desc = "μm (assumed)"

        # Use the simple formula: pixel_scale = 206 × pixel_size_microns / focal_length_mm
        scale = 206.0 * pixel_size_microns / focal_length

        if 0.01 <= scale <= 100:
            return (
                scale,
                f"calculated: 206 × {pixel_size_microns} {unit_desc} / {focal_length} mm",
            )

    # Method 4: Default fallback
    return 0.0, "default fallback value"


def validate_cross_match_results(phot_table, matched_table, header):
    """
    Validate that cross-matching results make sense
    """
    if len(matched_table) == 0:
        return False

    # Check if matched sources are distributed across the field
    ra_range = matched_table["ra"].max() - matched_table["ra"].min()
    dec_range = matched_table["dec"].max() - matched_table["dec"].min()

    # Expect some reasonable spread for a real field
    if ra_range < 0.001 or dec_range < 0.001:  # Less than ~4 arcsec
        st.warning("Matched sources seem too clustered - possible coordinate issue")
        return False

    # Check separation distribution
    separations = matched_table.get("gaia_separation_arcsec", [])
    if len(separations) > 0:
        median_sep = np.median(separations)
        if median_sep > 10:  # More than 10 arcsec median separation
            st.warning(
                f"Large median separation ({median_sep:.1f}) suggests coordinate problems"
            )
            return False

    return True


def get_field_center_coordinates(header):
    """
    Consistently extract field center coordinates with priority order
    """
    # Priority order for coordinate keywords
    coord_keywords = [
        ("CRVAL1", "CRVAL2"),  # Standard WCS
        ("RA", "DEC"),  # Common telescope keywords
        ("OBJRA", "OBJDEC"),  # Object coordinates
    ]

    for ra_key, dec_key in coord_keywords:
        if ra_key in header and dec_key in header:
            try:
                ra = float(header[ra_key])
                dec = float(header[dec_key])
                if 0 <= ra <= 360 and -90 <= dec <= 90:
                    return ra, dec
            except (ValueError, TypeError):
                continue

    return None, None


def validate_wcs_orientation(original_header, solved_header, test_pixel_coords):
    """
    Validate that WCS transformation preserves expected orientation
    """
    try:
        orig_wcs = WCS(original_header)
        solved_wcs = WCS(solved_header)

        # Test a few pixel positions
        orig_sky = orig_wcs.pixel_to_world_values(
            test_pixel_coords[:, 0], test_pixel_coords[:, 1]
        )
        solved_sky = solved_wcs.pixel_to_world_values(
            test_pixel_coords[:, 0], test_pixel_coords[:, 1]
        )

        # Check if coordinates are consistent (within reasonable tolerance)
        ra_diff = np.abs(orig_sky[0] - solved_sky[0])
        dec_diff = np.abs(orig_sky[1] - solved_sky[1])

        if np.any(ra_diff > 0.1) or np.any(dec_diff > 0.1):  # 0.1 degree tolerance
            st.warning("WCS orientation may have changed during plate solving")
            return False

        return True
    except Exception as e:
        st.warning(f"Could not validate WCS orientation: {e}")
        return True  # Assume OK if validation fails


# For matching based on RA/Dec
def match_catalogs_by_position(table1, table2, tolerance_arcsec=1.0):
    """Match two catalogs using sky coordinates"""
    coords1 = SkyCoord(ra=table1["ra"] * u.deg, dec=table1["dec"] * u.deg)
    coords2 = SkyCoord(ra=table2["ra"] * u.deg, dec=table2["dec"] * u.deg)

    idx, sep, _ = coords1.match_to_catalog_sky(coords2)
    # Only keep matches within tolerance
    matched = sep < tolerance_arcsec * u.arcsec

    return idx, matched


# Or for pixel coordinates (simpler, what you're currently trying to do)
def match_by_pixels(
    table1,
    table2,
    x_col1="xcenter",
    y_col1="ycenter",
    x_col2="x_init",
    y_col2="y_init",
    tolerance_pixels=0.5,
):
    """Match catalogs by pixel distance"""
    from scipy.spatial import cKDTree

    coords1 = np.column_stack([table1[x_col1], table1[y_col1]])
    coords2 = np.column_stack([table2[x_col2], table2[y_col2]])

    tree = cKDTree(coords2)
    distances, indices = tree.query(coords1)
    matched = distances < tolerance_pixels

    return indices, matched, distances


def clean_final_table(df):
    """Remove rows with NaN, Inf, or null values in critical columns"""

    # Replace inf with NaN for easier handling
    df = df.replace([np.inf, -np.inf], np.nan)

    # Define critical columns that must not have NaN
    critical_cols = ["ra", "dec", "xcenter", "ycenter"]

    # Optional: Also check magnitude columns
    mag_cols = [col for col in df.columns if "mag" in col.lower()]
    critical_cols.extend(mag_cols)

    # Keep only columns that exist
    critical_cols = [col for col in critical_cols if col in df.columns]

    initial_count = len(df)

    # Remove rows with NaN in critical columns
    df_clean = df.dropna(subset=critical_cols)

    removed = initial_count - len(df_clean)
    if removed > 0:
        st.warning(f"Removed {removed} rows with NaN/Inf values in critical columns")

    return df_clean


def merge_photometry_catalogs(aperture_table, psf_table, tolerance_pixels=1.0):
    """
    Merge aperture and PSF photometry, keeping ALL sources from both.

    Returns:
    - Sources matched in both: have both aperture_mag and psf_mag
    - PSF-only sources: have psf_mag but aperture_mag is NaN
    - Aperture-only sources: have aperture_mag but psf_mag is NaN
    """
    from scipy.spatial import cKDTree

    log_messages = []

    # Determine coordinate columns
    # photutils 3.0 renamed xcenter/ycenter -> x_center/y_center (canonical in colnames)
    aper_x = next(
        (c for c in ["xcenter", "x_center", "x_0"] if c in aperture_table.columns),
        "xcenter",
    )
    aper_y = next(
        (c for c in ["ycenter", "y_center", "y_0"] if c in aperture_table.columns),
        "ycenter",
    )
    psf_x = next(
        (c for c in ["x_init", "xcenter", "x_center"] if c in psf_table.columns),
        "x_init",
    )
    psf_y = next(
        (c for c in ["y_init", "ycenter", "y_center"] if c in psf_table.columns),
        "y_init",
    )

    # Convert to DataFrames if needed
    if not isinstance(aperture_table, pd.DataFrame):
        aperture_table = aperture_table.to_pandas()
    if not isinstance(psf_table, pd.DataFrame):
        psf_table = psf_table.to_pandas()

    # Build coordinate arrays
    aper_coords = np.column_stack([aperture_table[aper_x], aperture_table[aper_y]])
    psf_coords = np.column_stack([psf_table[psf_x], psf_table[psf_y]])

    # Match PSF sources to aperture sources
    tree = cKDTree(aper_coords)
    distances, aper_indices = tree.query(psf_coords)
    matched = distances < tolerance_pixels

    # Create three groups:
    # 1. PSF sources matched to aperture sources
    psf_matched_idx = np.where(matched)[0]
    aper_matched_idx = aper_indices[matched]

    # 2. PSF sources with NO aperture match
    psf_unmatched_idx = np.where(~matched)[0]

    # 3. Aperture sources with NO PSF match
    aper_has_match = np.zeros(len(aperture_table), dtype=bool)
    aper_has_match[aper_matched_idx] = True
    aper_unmatched_idx = np.where(~aper_has_match)[0]

    # Build final table
    final_rows = []

    # Add matched sources (have both aperture and PSF)
    for psf_idx, aper_idx in zip(psf_matched_idx, aper_matched_idx):
        row = aperture_table.iloc[aper_idx].to_dict()
        # Add PSF measurements with clear prefixes
        psf_row = psf_table.iloc[psf_idx]
        if "flux_fit" in psf_row:
            row["psf_flux"] = psf_row["flux_fit"]
        if "flux_err" in psf_row:
            row["psf_flux_err"] = psf_row["flux_err"]
        if "instrumental_mag" in psf_row:
            row["psf_instrumental_mag"] = psf_row["instrumental_mag"]
        if "psf_mag_err" in psf_row:
            row["psf_mag_err"] = psf_row["psf_mag_err"]

        # Mark as having both
        row["phot_method"] = "both"

        final_rows.append(row)

    # Add PSF-only sources
    for psf_idx in psf_unmatched_idx:
        psf_row = psf_table.iloc[psf_idx].to_dict()

        row = {
            aper_x: psf_row.get(psf_x),
            aper_y: psf_row.get(psf_y),
            "ra": psf_row.get("ra"),
            "dec": psf_row.get("dec"),
            "psf_flux": psf_row.get("flux_fit"),
            "psf_flux_err": psf_row.get("flux_err"),
            "psf_mag_err": psf_row.get("psf_mag_err"),
            "psf_instrumental_mag": psf_row.get("instrumental_mag"),
            "phot_method": "psf_only",
        }

        final_rows.append(row)

    # Add aperture-only sources
    for aper_idx in aper_unmatched_idx:
        row = aperture_table.iloc[aper_idx].to_dict()
        row["phot_method"] = "aperture_only"
        final_rows.append(row)

    final_table = pd.DataFrame(final_rows)

    log_messages.append(f"""INFO: Photometry merge results:
    - Matched sources (both methods): {len(psf_matched_idx)}
    - PSF-only sources: {len(psf_unmatched_idx)}
    - Aperture-only sources: {len(aper_unmatched_idx)}
    - Total sources: {len(final_table)}
    """)

    return final_table, log_messages


def add_calibrated_magnitudes(final_table, zero_point, airmass, filter_band=None):
    """
    Add calibrated magnitudes for both aperture and PSF.
    Handle cases where only one method is available.
    Dynamically handles different aperture radius suffixes.
    Filters out sources with magnitude errors > 1.5.

    Parameters
    ----------
    final_table : pandas.DataFrame
        Table with instrumental photometry
    zero_point : float
        Photometric zero point
    airmass : float
        Airmass value
    filter_band : str, optional
        Selected calibration filter band used to create prefixed alias columns
        such as ``rapasg_psf_mag`` while preserving the existing column names.

    Returns
    -------
    pandas.DataFrame
        Table with calibrated magnitudes, filtered to remove poor photometry

    Mathematical Formulas
    ---------------------
    Calibrated Magnitude:
        mag_calib = mag_inst + zero_point

    Magnitude Error:
        σ_mag = 1.0857 × (σ_flux / flux)

        Derived from error propagation of mag = -2.5 × log10(flux)
        where 1.0857 = 2.5 / ln(10)

    Notes
    -----
    - If instrumental magnitude error doesn't exist, it's computed from flux
    - Handles multiple aperture radii dynamically
    - Removes sources with magnitude errors > 1.5 (unreliable photometry)
    """
    if final_table is None or len(final_table) == 0:
        return final_table

    filter_prefix = get_filter_prefix(filter_band)

    # Helper to compute aperture magnitude for a given radius label
    def _compute_aperture_mag_for_radius(tbl, radius_label):
        # candidate instrumental mag column names in preference order
        candidates = [
            f"instrumental_mag_bkg_corr_{radius_label}",
            f"instrumental_mag_{radius_label}",
        ]
        inst_col = next((c for c in candidates if c in tbl.columns), None)
        mag_col = f"aperture_mag_{radius_label}"
        mag_err_col = f"aperture_mag_err_{radius_label}"
        flux_col = f"aperture_sum_{radius_label}"
        flux_err_col = f"aperture_sum_err_{radius_label}"

        if inst_col:
            tbl[mag_col] = tbl[inst_col] + zero_point  # - 0.09 * airmass

        # Compute magnitude error if not present
        if mag_err_col not in tbl.columns:
            if flux_col in tbl.columns and flux_err_col in tbl.columns:
                with np.errstate(divide="ignore", invalid="ignore"):
                    mag_err = 1.0857 * tbl[flux_err_col] / tbl[flux_col]
                    mag_err = mag_err.replace([np.inf, -np.inf], np.nan)
                tbl[mag_err_col] = mag_err

        return tbl

    # Dynamically find available aperture radii from column names
    aperture_radii = set()
    for col in final_table.columns:
        # Extract radius suffix by removing known prefixes
        radius_label = None
        if col.startswith("instrumental_mag_bkg_corr_"):
            radius_label = col.replace("instrumental_mag_bkg_corr_", "", 1)
        elif col.startswith("instrumental_mag_"):
            radius_label = col.replace("instrumental_mag_", "", 1)

        if radius_label:
            aperture_radii.add(radius_label)

    # Compute calibrated magnitudes for each found radius
    for radius_label in aperture_radii:
        final_table = _compute_aperture_mag_for_radius(final_table, radius_label)

    # PSF magnitude (if available)
    if "psf_instrumental_mag" in final_table.columns:
        final_table["psf_mag"] = (
            final_table["psf_instrumental_mag"] + zero_point  # - 0.09 * airmass
        )

    if filter_prefix:
        alias_map = {}
        for col in list(final_table.columns):
            alias_name = None
            if col == "psf_mag":
                alias_name = f"{filter_prefix}_psf_mag"
            elif col == "psf_mag_err":
                alias_name = f"{filter_prefix}_psf_mag_err"
            elif col == "psf_instrumental_mag":
                alias_name = f"{filter_prefix}_psf_instrumental_mag"
            elif col.startswith("aperture_mag_err_"):
                alias_name = f"{filter_prefix}_{col}"
            elif col.startswith("aperture_mag_"):
                alias_name = f"{filter_prefix}_{col}"
            elif col.startswith("instrumental_mag_bkg_corr_"):
                alias_name = f"{filter_prefix}_{col}"
            elif col.startswith("instrumental_mag_"):
                alias_name = f"{filter_prefix}_{col}"

            if alias_name and alias_name not in final_table.columns:
                alias_map[alias_name] = final_table[col]

        if alias_map:
            final_table = final_table.assign(**alias_map)

    # remove columns from a list (these are intermediate columns that may exist)
    cols_to_remove = [
        "sky_center.ra",
        "sky_center.dec",
        "match_id",
        "simbad_ids",
        "catalog_matches",
        "calib_mag",
    ]

    # Also remove aperture_sum and instrumental_mag columns for cleanup
    for col in list(final_table.columns):
        if col.startswith("aperture_sum_") or col.startswith("background_per_pixel_"):
            cols_to_remove.append(col)
        # Keep instrumental_mag columns but can remove them if desired
        # if col.startswith("instrumental_mag_"):
        #     cols_to_remove.append(col)

    final_table = final_table.drop(
        columns=[col for col in cols_to_remove if col in final_table.columns]
    )

    final_table["id"] = final_table["id"].astype("Int64")

    # Filter out sources with magnitude errors > 1. (unreliable photometry)
    # Use abs() to handle occasional negative errors from numerical issues
    initial_count = len(final_table)
    mag_err_cols = [col for col in final_table.columns if "mag_err" in col]

    if mag_err_cols:
        # Create mask: keep rows where ALL magnitude errors are < 1. (or NaN)
        keep_mask = np.ones(len(final_table), dtype=bool)
        for col in mag_err_cols:
            col_values = np.abs(final_table[col])  # Use absolute values
            # Keep if error < 1. or is NaN (missing data is ok)
            col_mask = (col_values < 1.) | pd.isna(col_values)
            keep_mask &= col_mask

        final_table = final_table[keep_mask].reset_index(drop=True)
        removed_count = initial_count - len(final_table)

        if removed_count > 0:
            import streamlit as st

            st.warning(f"Removed {removed_count} sources with magnitude error >= 1.")

    return final_table

def clean_photometry_table(df, require_magnitude=True):
    """
    Clean table but preserve sources that have valid photometry in at least one method.

    Args:
        require_magnitude: If True, remove sources without any valid magnitude
    """
    log_messages = []
    # Replace inf with NaN
    df = df.replace([np.inf, -np.inf], np.nan)

    # Always require valid coordinates
    coord_cols = ["xcenter", "ycenter"]
    coord_cols = [col for col in coord_cols if col in df.columns]

    initial_count = len(df)

    # Remove rows with invalid coordinates
    df = df.dropna(subset=coord_cols)
    removed_coords = initial_count - len(df)

    if require_magnitude:
        # Remove sources that don't have ANY valid magnitude measurement
        mag_cols = [
            col for col in df.columns if "mag" in col.lower() and col != "mag_method"
        ]

        if mag_cols:
            # Keep row if it has at least ONE valid magnitude
            has_valid_mag = df[mag_cols].notna().any(axis=1)
            df = df[has_valid_mag]
            removed_no_mag = initial_count - removed_coords - len(df)
        else:
            removed_no_mag = 0
    else:
        removed_no_mag = 0

    # Remove rows where ALL numeric columns are NaN (completely empty rows)
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    if len(numeric_cols) > 0:
        df = df.dropna(subset=numeric_cols, how="all")

    total_removed = initial_count - len(df)

    if total_removed > 0:
        log_messages.append(f"""INFO: Cleaning results:
        - Removed {removed_coords} sources with invalid coordinates
        - Removed {removed_no_mag} sources without valid magnitudes
        - Total removed: {total_removed}
        - Remaining sources: {len(df)}
        """)

    return df, log_messages


def estimate_background(image_data, box_size=64, filter_size=5, figure=True):
    """
    Estimate the background and background RMS of an astronomical image.

    Uses photutils.Background2D to create a 2D background model with sigma-clipping
    and the SExtractor background estimation algorithm. Includes error handling
    and automatic adjustment for small images.

    Parameters
    ----------
    image_data : numpy.ndarray
        The 2D image array
    box_size : int, optional
        The box size in pixels for the local background estimation.
        Will be automatically adjusted if the image is small.
    filter_size : int, optional
        Size of the filter for smoothing the background.

    Returns
    -------
    tuple
        (background_2d_object, fig_bkg, error_message) where:
        - background_2d_object: photutils.Background2D object if successful, None if failed
        - fig_bkg: matplotlib.figure.Figure object if successful, None if failed
        - error_message: None if successful, string describing the error if failed

    Notes
    -----
    The function automatically adjusts the box_size and filter_size parameters
    if the image is too small, and handles various edge cases to ensure robust
    background estimation.
    """
    if image_data is None:
        return None, None, "No image data provided"

    if not isinstance(image_data, np.ndarray):
        return None, None, f"Image data must be a numpy array, got {type(image_data)}"

    if len(image_data.shape) != 2:
        return None, None, f"Image must be 2D, got shape {image_data.shape}"

    height, width = image_data.shape
    adjusted_box_size = max(box_size, min(height // 10, width // 10, 128))
    adjusted_filter_size = min(filter_size, adjusted_box_size // 2)

    if adjusted_box_size < 10:
        return (
            None,
            None,
            f"Image too small ({height}x{width}) for background estimation",
        )

    try:
        sigma_clip = SigmaClip(sigma=3)
        bkg_estimator = SExtractorBackground()

        bkg = Background2D(
            data=image_data,
            box_size=adjusted_box_size,
            filter_size=adjusted_filter_size,
            sigma_clip=sigma_clip,
            bkg_estimator=bkg_estimator,
        )

        fig_bkg = None
        # Plot the background model with ZScale and save as FITS
        if figure:
            try:
                # Create a figure with two subplots side by side for background/RMS
                fig_bkg, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

                # Use ZScaleInterval for better visualization
                zscale = ZScaleInterval()
                vmin, vmax = zscale.get_limits(bkg.background)

                # Plot the background model
                im1 = ax1.imshow(
                    bkg.background, origin="lower", cmap="viridis", vmin=vmin, vmax=vmax
                )
                ax1.set_title("Estimated Background")
                fig_bkg.colorbar(im1, ax=ax1, label="Flux")

                # Plot the background RMS
                vmin_rms, vmax_rms = zscale.get_limits(bkg.background_rms)
                im2 = ax2.imshow(
                    bkg.background_rms,
                    origin="lower",
                    cmap="viridis",
                    vmin=vmin_rms,
                    vmax=vmax_rms,
                )
                ax2.set_title("Background RMS")
                fig_bkg.colorbar(im2, ax=ax2, label="Flux")

                fig_bkg.tight_layout()

            except Exception as e:
                return bkg, None, f"Error creating plot: {str(e)}"

        return bkg, fig_bkg, None
    except Exception as e:
        return None, None, f"Background estimation error: {str(e)}"


def extract_filter_from_header(header):
    """
    Extract and normalize filter name from FITS header.

    Parameters
    ----------
    header : astropy.io.fits.Header or dict-like
        FITS header to extract filter from

    Returns
    -------
    tuple (str, str)
        (raw_filter_name, mapped_gaia_band)
        Returns ("Unknown", "phot_g_mean_mag") if no filter found
    """
    if header is None:
        return "Unknown", "phot_g_mean_mag"

    # Try multiple common filter keywords
    filter_keywords = ["FILTER", "FILTERS", "FLT", "FILTER1", "INSFLNAM", "FILTNAME"]

    raw_filter = None
    for key in filter_keywords:
        if key in header:
            raw_filter = str(header[key]).strip()
            break

    if not raw_filter or raw_filter.upper() in ["NONE", "NULL", ""]:
        return "Unknown", "phot_g_mean_mag"

    # Normalize the filter name
    normalized = raw_filter.lower().replace(" ", "_").replace("-", "_")

    # Map to GAIA band
    mapped_band = FILTER_DICT.get(normalized, "phot_g_mean_mag")

    return raw_filter, mapped_band
