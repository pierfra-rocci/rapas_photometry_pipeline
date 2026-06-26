import os
import streamlit as st

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import astroscrappy
from astropy.wcs import WCS
from astropy.stats import sigma_clipped_stats, sigma_clip
from astropy.coordinates import SkyCoord, EarthLocation, AltAz, get_sun
from astropy.time import Time
from photutils.psf import fit_fwhm
from astropy.visualization import ZScaleInterval
import astropy.units as u

from photutils.utils import calc_total_error
from photutils.detection import DAOStarFinder
from photutils.aperture import (CircularAperture, CircularAnnulus,
                                aperture_photometry)

from src.tools_pipeline import (
    safe_wcs_create,
    estimate_background,
    extract_photometric_color_series,
)
from src.utils import ensure_output_directory

from typing import Union, Optional, Dict, Tuple
from src.psf import perform_psf_photometry


def fit_color_term_correction(
    matched_table,
    filter_band: str,
    zero_point: float,
    fwhm_radius_factor: float = 1.5,
    min_points: int = 5,
    sigma_threshold: float = 3.0,
    max_iterations: int = 5,
):
    """Fit a simple linear color term on the matched calibration stars.

    The fit is performed on residuals relative to the existing scalar zero point,
    preserving the current calibration workflow as the baseline path.
    """
    result = {
        "supported": False,
        "fitted": False,
        "reason": None,
        "filter_band": filter_band,
        "instrumental_mag_col": None,
        "color_label": None,
        "color_ref": None,
        "color_term": None,
        "intercept": None,
        "effective_zero_point": None,
        "n_used": 0,
        "scatter": None,
        "diagnostic_figure": None,
    }

    if matched_table is None or len(matched_table) == 0:
        result["reason"] = "No matched calibration stars available."
        return result

    color_series, color_metadata = extract_photometric_color_series(matched_table, filter_band)
    result["supported"] = color_metadata.get("supported", False)
    result["color_label"] = color_metadata.get("color_label")
    if color_series is None:
        result["reason"] = "Catalog colors are unavailable for the selected filter."
        return result

    preferred_radius = 1.3
    radius_suffix = f"_{str(preferred_radius).replace('.', '_')}"
    instrumental_mag_col = f"instrumental_mag{radius_suffix}"
    if instrumental_mag_col not in matched_table.columns:
        candidate_cols = sorted(
            [col for col in matched_table.columns if col.startswith("instrumental_mag_")]
        )
        if not candidate_cols:
            result["reason"] = "No instrumental magnitude column is available for color fitting."
            return result
        instrumental_mag_col = candidate_cols[0]

    result["instrumental_mag_col"] = instrumental_mag_col

    fit_table = pd.DataFrame(
        {
            "instrumental_mag": pd.to_numeric(
                matched_table[instrumental_mag_col], errors="coerce"
            ),
            "catalog_mag": pd.to_numeric(matched_table[filter_band], errors="coerce"),
            "source_color": pd.to_numeric(color_series, errors="coerce"),
        }
    )
    fit_table = fit_table.replace([np.inf, -np.inf], np.nan).dropna().copy()

    if len(fit_table) < min_points:
        result["reason"] = (
            f"Need at least {min_points} matched stars with valid colors; found {len(fit_table)}."
        )
        return result

    color_ref = float(np.median(fit_table["source_color"]))
    fit_table["color_centered"] = fit_table["source_color"] - color_ref
    fit_table["residual"] = fit_table["catalog_mag"] - (
        fit_table["instrumental_mag"] + float(zero_point)
    )

    for _ in range(max_iterations):
        x_values = fit_table["color_centered"].to_numpy(dtype=float)
        y_values = fit_table["residual"].to_numpy(dtype=float)
        design_matrix = np.column_stack([np.ones(len(x_values)), x_values])
        intercept, color_term = np.linalg.lstsq(design_matrix, y_values, rcond=None)[0]
        model = intercept + color_term * x_values
        residuals = y_values - model

        if len(fit_table) <= min_points:
            break

        median_residual = float(np.median(residuals))
        mad = float(np.median(np.abs(residuals - median_residual)))
        scale = 1.4826 * mad if mad > 0 else float(np.std(residuals, ddof=1))
        if not np.isfinite(scale) or scale <= 0:
            break

        keep_mask = np.abs(residuals - median_residual) <= sigma_threshold * scale
        if keep_mask.all() or keep_mask.sum() < min_points:
            break
        fit_table = fit_table.iloc[keep_mask].copy()

    x_values = fit_table["color_centered"].to_numpy(dtype=float)
    y_values = fit_table["residual"].to_numpy(dtype=float)
    design_matrix = np.column_stack([np.ones(len(x_values)), x_values])
    intercept, color_term = np.linalg.lstsq(design_matrix, y_values, rcond=None)[0]
    model = intercept + color_term * x_values
    residuals = y_values - model
    scatter = float(np.std(residuals, ddof=1)) if len(residuals) > 1 else 0.0

    fig, ax = plt.subplots(figsize=(7, 5), dpi=100)
    ax.scatter(
        fit_table["source_color"],
        fit_table["residual"],
        alpha=0.7,
        color="tab:blue",
        label="Calibration stars",
    )
    color_grid = np.linspace(
        fit_table["source_color"].min(),
        fit_table["source_color"].max(),
        100,
    )
    model_grid = intercept + color_term * (color_grid - color_ref)
    ax.plot(color_grid, model_grid, color="tab:red", label="Linear color term")
    ax.axhline(0.0, color="gray", linestyle="--", linewidth=1)
    ax.set_xlabel(result["color_label"] or "Color")
    ax.set_ylabel("Catalog - (instrumental + zero point)")
    ax.set_title("Color-Term Fit")
    ax.grid(True, alpha=0.4)
    ax.legend()
    fig.tight_layout()

    result.update(
        {
            "fitted": True,
            "reason": None,
            "color_ref": color_ref,
            "color_term": float(color_term),
            "intercept": float(intercept),
            "effective_zero_point": float(zero_point) + float(intercept),
            "n_used": int(len(fit_table)),
            "scatter": scatter,
            "diagnostic_figure": fig,
        }
    )
    return result


def mask_and_remove_cosmic_rays(image_data, header):
    """
    Create a mask for saturated pixels and cosmic rays using L.A.Cosmic.
    """
    # Safe parse of header SATURATE -> float, fallback to robust max
    sat_hdr = header.get("SATURATE", None)
    try:
        saturation = float(sat_hdr) if sat_hdr is not None else None
    except (ValueError, TypeError):
        saturation = None

    if saturation is None:
        saturation = 0.90 * np.nanmax(image_data)

    mask = np.isnan(image_data)  # mask NaNs in the input image
    mask |= image_data > saturation  # mask saturated pixels

    gain_hdr = header.get("GAIN", None)
    try:
        gain = float(gain_hdr) if gain_hdr is not None else 1.0
    except (ValueError, TypeError):
        gain = 1.0

    # Run L.A.Cosmic (pass inmask explicitly)
    try:
        res = astroscrappy.detect_cosmics(
            image_data, inmask=mask, gain=gain, verbose=False
        )
        # detect_cosmics often returns a tuple; find the boolean CR mask
        if isinstance(res, tuple):
            crmask = None
            for el in res:
                if (
                    isinstance(el, np.ndarray)
                    and el.shape == image_data.shape
                    and el.dtype == bool
                ):
                    crmask = el
                    break
            if crmask is None:
                crmask = res[0].astype(bool)
        else:
            crmask = res.astype(bool)
        st.success("Saturated pixels and Cosmic ray detection complete.")
    except Exception:
        st.warning("Cosmic ray detection failed, proceeding with saturation only mask.")
        crmask = np.zeros_like(mask, dtype=bool)

    mask |= crmask
    return mask


def make_border_mask(
    image: np.ndarray,
    border: Union[int, Tuple[int, int], Tuple[int, int, int, int]] = 50,
    invert: bool = True,
    dtype: np.dtype = bool,
) -> np.ndarray:
    """
    Create a binary mask for an image excluding one or more border regions.

    This function creates a mask that can be used to exclude border pixels
    from analysis, which is useful for operations that might be affected
    by edge artifacts.

    Parameters
    ----------
    image : numpy.ndarray
        The input image as a NumPy array
    border : int or tuple of int, default=50
        Border size(s) to exclude from the mask:
        - If int: same border size on all sides
        - If tuple of 2 elements: (vertical, horizontal) borders
        - If tuple of 4 elements: (top, bottom, left, right) borders
    invert : bool, default=True
        If True, the mask will be inverted (False for border regions)
        If False, the mask will be True for non-border regions
    dtype : numpy.dtype, default=bool
        Data type of the output mask

    Returns
    -------
    numpy.ndarray
        Binary mask with the same height and width as the input image

    Raises
    ------
    TypeError
        If image is not a NumPy array or border is not of the correct type
    ValueError
        If image is empty, borders are negative, borders are larger than the image,
        or border tuple has an invalid length

    Examples
    --------
    >>> img = np.ones((100, 100))
    >>> # Create a mask with 10px border on all sides
    >>> mask = make_border_mask(img, 10)
    >>> # Create a mask with different vertical/horizontal borders
    >>> mask = make_border_mask(img, (20, 30))
    >>> # Create a mask with custom borders for each side
    >>> mask = make_border_mask(img, (10, 20, 30, 40), invert=False)
    """
    import numbers

    if image is None:
        raise ValueError("Image cannot be None")
    if not isinstance(image, np.ndarray):
        raise TypeError("Image must be a numpy.ndarray")
    if image.size == 0:
        raise ValueError("Image cannot be empty")
    if image.ndim < 2:
        raise ValueError("Image must have at least 2 dimensions (height, width)")

    height, width = image.shape[:2]

    # Normalize border into four integer values: top, bottom, left, right
    if isinstance(border, numbers.Real):
        b = int(border)
        top = bottom = left = right = b
    elif isinstance(border, (tuple, list, np.ndarray)):
        if len(border) == 2:
            vert, horiz = border
            top = bottom = int(vert)
            left = right = int(horiz)
        elif len(border) == 4:
            top, bottom, left, right = (int(x) for x in border)
        else:
            raise ValueError("If 'border' is a sequence it must have length 2 or 4")
    else:
        raise TypeError(
            "border must be an int/float or a tuple/list/ndarray of length 2 or 4"
        )

    if any(b < 0 for b in (top, bottom, left, right)):
        raise ValueError("Borders cannot be negative")

    # require the inner region to have positive size
    if top + bottom >= height or left + right >= width:
        raise ValueError(
            "Sum of top+bottom must be < image height and left+right must be < image width"
        )

    # build bool mask first, then cast to requested dtype
    inner = np.zeros((height, width), dtype=bool)
    inner[top : height - bottom, left : width - right] = True

    mask = ~inner if invert else inner

    return mask.astype(dtype)


def detect_and_mask_satellite_trails(image_data, header, temp_fits_path=None):
    """
    Detect and mask satellite trails or airplane trails using ASTRiDE library.
    
    This function uses the ASTRiDE library to detect linear streaks (satellite/airplane trails)
    in astronomical images and creates a boolean mask to exclude these regions from analysis.
    
    Parameters
    ----------
    image_data : numpy.ndarray
        2D array of the image data
    header : dict or astropy.io.fits.Header
        FITS header containing image metadata
    temp_fits_path : str, optional
        Temporary path to save the image as FITS file for ASTRiDE processing.
        If None, a temporary file will be created and cleaned up.
        
    Returns
    -------
    numpy.ndarray
        Boolean mask where True indicates satellite trail pixels that should be masked
    
    Notes
    -----
    - Uses ASTRiDE library for streak detection
    - Creates a temporary FITS file for processing
    - The mask includes a buffer around detected streaks to ensure complete exclusion
    - Returns an empty mask (all False) if ASTRiDE detection fails
    """
    try:
        import tempfile
        import os
        from astropy.io import fits
        import astride.detect
        
        # Create temporary FITS file if not provided
        if temp_fits_path is None:
            temp_dir = tempfile.mkdtemp()
            temp_fits_path = os.path.join(temp_dir, "temp_image.fits")
            cleanup_temp = True
        else:
            cleanup_temp = False
            
        try:
            # Save image data to temporary FITS file
            hdu = fits.PrimaryHDU(image_data)
            # Copy relevant header information
            for key, value in header.items():
                try:
                    hdu.header[key] = value
                except Exception:
                    pass
            hdu.writeto(temp_fits_path, overwrite=True)

            # Create ASTRiDE streak detector
            # Use conservative parameters to avoid false positives
            streak_detector = astride.detect.Streak(
                temp_fits_path,
                remove_bkg='constant',  # Use constant background for simplicity
                bkg_box_size=50,
                contour_threshold=3.0,  # Higher threshold to reduce false positives
                min_points=15,  # Require more points for a valid streak
                shape_cut=0.2,
                area_cut=25.0,  # Larger area cut
                radius_dev_cut=0.5,
                connectivity_angle=3.0,
                fully_connected='high'
            )
            
            # Run streak detection
            streak_detector.detect()
            
            # Create mask from detected streaks
            mask = np.zeros_like(image_data, dtype=bool)
            
            if streak_detector.streaks and len(streak_detector.streaks) > 0:
                # Create mask by drawing lines along the detected streaks
                # Use a buffer around each streak to ensure complete masking
                buffer_size = 5  # pixels buffer around each streak
                
                for streak in streak_detector.streaks:
                    x_coords = streak['x'].astype(int)
                    y_coords = streak['y'].astype(int)
                    
                    # Draw the streak line on the mask
                    for i in range(len(x_coords) - 1):
                        x1, y1 = x_coords[i], y_coords[i]
                        x2, y2 = x_coords[i + 1], y_coords[i + 1]
                        
                        # Draw line between points (Bresenham's line algorithm)
                        dx = abs(x2 - x1)
                        dy = abs(y2 - y1)
                        sx = 1 if x1 < x2 else -1
                        sy = 1 if y1 < y2 else -1
                        err = dx - dy
                        
                        while True:
                            # Mark the pixel and surrounding buffer
                            for bx in range(-buffer_size, buffer_size + 1):
                                for by in range(-buffer_size, buffer_size + 1):
                                    nx, ny = x1 + bx, y1 + by
                                    if (0 <= nx < mask.shape[1] and 
                                        0 <= ny < mask.shape[0]):
                                        mask[ny, nx] = True
                            
                            if x1 == x2 and y1 == y2:
                                break
                            
                            e2 = 2 * err
                            if e2 > -dy:
                                err -= dy
                                x1 += sx
                            if e2 < dx:
                                err += dx
                                y1 += sy
            else:
                st.warning("No satellite trails detected")
                
            return mask
            
        except Exception as e:
            st.warning(f"Error in satellite trail detection: {e}")
            return np.zeros_like(image_data, dtype=bool)
        finally:
            # Clean up temporary file if we created it
            if cleanup_temp and os.path.exists(temp_fits_path):
                try:
                    os.remove(temp_fits_path)
                    if os.path.exists(os.path.dirname(temp_fits_path)):
                        os.rmdir(os.path.dirname(temp_fits_path))
                except Exception:
                    pass
                    
    except ImportError:
        st.warning("ASTRiDE library not available. Satellite trail detection disabled.")
        return np.zeros_like(image_data, dtype=bool)
    except Exception as e:
        st.warning(f"Unexpected error in satellite trail detection: {e}")
        return np.zeros_like(image_data, dtype=bool)


def airmass(
    _header: Dict, observatory: Optional[Dict] = None, return_details: bool = False
) -> Union[float, Tuple[float, Dict]]:
    """
    Calculate the airmass for a celestial object from observation parameters.

    Airmass is a measure of the optical path length through Earth's atmosphere.
    This function calculates it from header information and observatory location,
    handling multiple coordinate formats and performing physical validity checks.

    Parameters
    ----------
    _header : Dict
        FITS header or dictionary containing observation information.
        Must include:
        - Coordinates (RA/DEC or OBJRA/OBJDEC)
        - Observation date (DATE-OBS)
    observatory : Dict, optional
        Information about the observatory. If not provided, uses the default.
        mat: {
            'name': str,           # Observatory name
            'latitude': float,     # Latitude in degrees
            'longitude': float,    # Longitude in degrees
            'elevation': float     # Elevation in meters
        }
    return_details : bool, optional
        If True, returns additional information about the observation.

    Returns
    -------
    Union[float, Tuple[float, Dict]]
        - If return_details=False: airmass (float)
        - If return_details=True: (airmass, observation_details)
          where observation_details is a dictionary with:
          - observatory name
          - datetime
          - target coordinates
          - altitude/azimuth
          - sun altitude
          - observation type (night/twilight/day)

    Notes
    -----
    Airmass is approximately sec(z) where z is the zenith angle.
    The function enforces physical constraints (airmass ≥ 1.0) and
    displays warnings for extreme values. It also determines whether
    the observation was taken during night, twilight or day.
    """

    # Check if airmass already exists in header
    airmass_keywords = ["AIRMASS", "SECZ", "AIRMASS_START", "AIRMASS_END"]
    for keyword in airmass_keywords:
        if keyword in _header and _header[keyword] is not None:
            try:
                existing_airmass = float(_header[keyword])
                # Validate the existing airmass value
                if 1.0 <= existing_airmass <= 30.0:
                    st.write("Using existing airmass from header...")
                    if return_details:
                        return existing_airmass, {"airmass_source": f"header_{keyword}"}
                    return existing_airmass
                else:
                    st.warning(
                        f"Invalid airmass value in header ({existing_airmass}), calculating from coordinates"
                    )
                    break
            except (ValueError, TypeError):
                st.warning(
                    f"Could not parse airmass value from header keyword {keyword}"
                )
                continue

    def get_observation_type(sun_alt):
        if sun_alt < -18:
            return "night"
        if sun_alt < 0:
            return "twilight"
        return "day"

    obs_data = observatory

    try:
        ra = _header.get("RA") or _header.get("OBJRA") or _header.get("CRVAL1")
        dec = _header.get("DEC") or _header.get("OBJDEC") or _header.get("CRVAL2")

        # Check for various date keywords in order of preference
        date_keywords = ["DATE-OBS", "DATE", "DATE_OBS", "DATEOBS", "MJD-OBS"]
        obstime_str = None

        for keyword in date_keywords:
            if keyword in _header and _header[keyword] is not None:
                obstime_str = _header[keyword]
                break

        if any(v is None for v in [ra, dec, obstime_str]):
            missing = []
            if ra is None:
                missing.append("RA/OBJRA/CRVAL1")
            if dec is None:
                missing.append("DEC/OBJDEC/CRVAL2")
            if obstime_str is None:
                missing.append("DATE-OBS/DATE")
            raise KeyError(f"Missing required header keywords: {', '.join(missing)}")

        coord = SkyCoord(ra=ra, dec=dec, unit=u.deg, frame="icrs")

        # Try to parse the observation time with different formats
        try:
            # First, try direct parsing
            obstime = Time(obstime_str)

        except Exception:
            # If that fails, try different parsing strategies
            try:
                # Handle common FITS date formats
                if isinstance(obstime_str, str):
                    # Remove any timezone info and try to parse
                    clean_date = obstime_str.replace("Z", "").replace("T", " ")
                    # Try ISO format
                    obstime = Time(clean_date, format="iso")
                else:
                    # If it's not a string, convert it
                    obstime = Time(str(obstime_str))
            except Exception:
                # Last resort: try as MJD if it's a number
                try:
                    mjd_value = float(obstime_str)
                    obstime = Time(mjd_value, format="mjd")
                except (ValueError, TypeError):
                    raise ValueError(
                        f"Could not parse observation time '{obstime_str}' in any recognized format"
                    )

        location = EarthLocation(
            lat=obs_data["latitude"] * u.deg,
            lon=obs_data["longitude"] * u.deg,
            height=obs_data["elevation"] * u.m,
        )

        altaz_frame = AltAz(obstime=obstime, location=location)
        altaz = coord.transform_to(altaz_frame)
        airmass_value = float(altaz.secz)

        if airmass_value < 1.0:
            st.warning("Calculated airmass is less than 1 (impossible)")
            airmass_value = 1.0
        elif airmass_value > 30.0:
            st.warning("Extremely high airmass (>30), object near horizon")

        sun_altaz = get_sun(obstime).transform_to(altaz_frame)
        sun_alt = float(sun_altaz.alt.deg)

        details = {
            "observatory": obs_data["name"],
            "datetime": obstime.iso,
            "target_coords": {
                "ra": coord.ra.to_string(unit=u.hour),
                "observation_type": get_observation_type(sun_alt),
            },
            "sun_altitude": round(sun_alt, 2),
            "observation_type": get_observation_type(sun_alt),
            "altaz": {
                "altitude": round(float(altaz.alt.deg), 2),
                "azimuth": round(float(altaz.az.deg), 2),
            },
        }

        st.write(f"Date and time of observation (UTC): {obstime.iso}")
        st.write(
            f"Altitude: {details['altaz']['altitude']}° \n"
            f"Azimuth: {details['altaz']['azimuth']}°"
        )

        if return_details:
            return round(airmass_value, 2), details
        return round(airmass_value, 2)

    except Exception as e:
        st.warning(f"Error calculating airmass: {str(e)}")
        if return_details:
            return 0.0, {}
        return 0.0


def fwhm_fit(
    _img: np.ndarray,
    fwhm: float,
    mask: Optional[np.ndarray] = None,
    std_lo: float = 0.5,
    std_hi: float = 0.5,
) -> Optional[float]:
    """
    Estimate the Full Width at Half Maximum (FWHM) of stars in an astronomical image.

    This function detects sources in the image using DAOStarFinder, filters them based on
    flux values, and fits 1D Gaussian models to the marginal distributions of each source
    to calculate FWHM values.

    Parameters
    ----------
    img : numpy.ndarray
        The 2D image array containing astronomical sources
    fwhm : float
        Initial FWHM estimate in pixels, used for source detection
    pixel_scale : float
        The pixel scale of the image in arcseconds per pixel
    mask : numpy.ndarray, optional
        Boolean mask array where True indicates pixels to ignore during source detection
    std_lo : float, default=0.5
        Lower bound for flux filtering, in standard deviations below median flux
    std_hi : float, default=0.5
        Upper bound for flux filtering, in standard deviations above median flux

    Returns
    -------
    float or None
        The median FWHM value in pixels (rounded to nearest integer) if successful,
        or None if no valid sources could be measured

    Notes
    -----
    The function uses marginal sums along the x and y dimensions and fits 1D Gaussian
    profiles to estimate FWHM. For each source, a box of size ~6×FWHM is extracted,
    and profiles are fit independently in both dimensions, with the results averaged.

    If more than 1000 sources are detected after filtering, a random sample of 1000
    sources is selected for FWHM calculation to improve performance.

    Progress updates and error messages are displayed using Streamlit.
    """
    try:
        _, _, clipped_std = sigma_clipped_stats(_img, sigma=3.0)

        daofind = DAOStarFinder(fwhm=1.5 * fwhm, threshold=7 * clipped_std)
        sources = daofind(_img, mask=mask)
        if sources is None:
            st.warning("No sources found !")
            return None, clipped_std

        flux = sources["flux"]
        median_flux = np.mean(flux)
        std_flux = np.std(flux)
        mask_flux = (flux > median_flux - std_lo * std_flux) & (
            flux < median_flux + std_hi * std_flux
        )
        filtered_sources = sources[mask_flux]

        filtered_sources = filtered_sources[~np.isnan(filtered_sources["flux"])]

        st.write(f"Sources after filtering : {len(filtered_sources)}")

        if len(filtered_sources) == 0:
            msg = "No valid sources for fitting found after filtering."
            st.error(msg)
            raise ValueError(msg)

        # Randomly sample 1250 sources if more than 1250 are available
        if len(filtered_sources) > 1000:
            indices = np.random.choice(len(filtered_sources), size=1000, replace=False)
            filtered_sources = filtered_sources[indices]
            st.warning("Too many sources, sampled 1000 sources for FWHM calculation")

        box_size = int(6 * round(fwhm))
        if box_size % 2 == 0:
            box_size += 1

        xypos = list(zip(filtered_sources["xcentroid"], filtered_sources["ycentroid"]))
        fwhms = fit_fwhm(_img, xypos=xypos, fit_shape=box_size)

        # fit_fwhm returns [fwhm_x, fwhm_y] for each source
        # Average x and y FWHM for each source, then take median
        if fwhms.ndim == 2 and fwhms.shape[1] == 2:
            # Average x and y for each source
            mean_fwhms_per_source = np.mean(fwhms, axis=1)
            mean_fwhm = np.median(mean_fwhms_per_source)
        else:
            # Fallback if already 1D
            mean_fwhm = np.median(fwhms)

        st.success(f"FWHM using gaussian model : {round(mean_fwhm, 2)} pixels")

        return round(mean_fwhm, 2), clipped_std
    except ValueError as e:
        raise e
    except Exception as e:
        st.error(f"Unexpected error in fwhm_fit: {e}")
        raise ValueError(f"Unexpected error in fwhm_fit: {e}")


def detection_and_photometry(
    image_data, science_header, mean_fwhm_pixel, threshold_sigma, detection_mask,
    fwhm_radius_factor: float = 1.5,
):
    """
    Perform a complete photometry workflow on an astronomical image.

    This function executes the following steps:
      1. Estimate the sky background and its RMS.
      2. Detect sources using DAOStarFinder.
      3. Optionally refine the WCS using GAIA DR3 and stdpipe.
      4. Perform aperture photometry on detected sources.
      5. Perform PSF photometry using an empirical PSF model.

    Parameters
    ----------
    image_data : numpy.ndarray
        2D array of the image data.
    _science_header : dict or astropy.io.fits.Header
        FITS header or dictionary with image metadata.
    mean_fwhm_pixel : float
        Estimated FWHM in pixels, used for aperture and PSF sizing
    threshold_sigma : float
        Detection threshold in sigma above background.
    detection_mask : int
        Border size in pixels to mask during detection.
    filter_band : str
        Photometric band for catalog matching and calibration.
    gb : str, optional
        Gaia band name for catalog queries (default: "Gmag").

    Returns
    -------
    tuple
        (phot_table, epsf_table, daofind, bkg), where:
        phot_table : astropy.table.Table
            Results of aperture photometry.
        epsf_table : astropy.table.Table or None
            Results of PSF photometry, or None if PSF fitting failed.
        daofind : photutils.detection.DAOStarFinder
            DAOStarFinder object used for source detection.
        bkg : photutils.background.Background2D
            Background2D object containing the background model.

    Notes
    -----
    - WCS refinement uses stdpipe and GAIA DR3 if enabled in session state.
    - Adds RA/Dec coordinates to photometry tables if WCS is available.
    - Computes instrumental magnitudes for both aperture and PSF photometry.
    - Displays progress and results in the Streamlit interface.

    Photometric Formulas
    --------------------
    Signal-to-Noise Ratio (S/N):
        S/N = flux / flux_error
        where flux is background-corrected aperture sum (if available) or raw aperture sum

    Magnitude Error:
        σ_mag = (2.5 / ln(10)) × (σ_flux / flux) = 1.0857 × (σ_flux / flux)
        Derived from error propagation: mag = -2.5 × log10(flux)

    Instrumental Magnitude:
        m_inst = -2.5 × log10(flux)

    Quality Flags:
        - 'poor': S/N ≤ 5 (unreliable photometry)
        - 'marginal': 5 < S/N ≤ 10 (marginal quality)
        - 'good': S/N > 10 (reliable photometry)
        - 'unknown': missing data or calculation failed
    """
    daofind = None

    try:
        w, wcs_error, _ = safe_wcs_create(science_header)
        if w is None:
            st.error(f"Error creating WCS in detection_and_photometry: {wcs_error}")
            st.error(
                "Will attempt to proceed without WCS - coordinates will not be available"
            )
            # Don't return here - allow photometry to continue without coordinates
            # return None, None, daofind, None, None, None, None, None
        else:
            st.success("WCS object created successfully in detection_and_photometry")
    except Exception as e:
        st.error(f"Exception while creating WCS in detection_and_photometry: {e}")
        w = None
        # Don't return - allow photometry to continue
        # return None, None, daofind, None, None, None, None, None

    pixel_scale = science_header.get(
        "PIXSCALE",
        science_header.get("PIXSIZE", science_header.get("PIXELSCAL", 1.0)),
    )

    bkg, bkg_fig, bkg_error = estimate_background(image_data)

    if bkg is None:
        st.error(f"Error estimating background: {bkg_error}")
        return None, None, daofind, None, None, None, None, None

    # border mask (True = masked)
    border_mask = make_border_mask(image_data, border=detection_mask)

    # cosmic-ray mask produced by LA Cosmic / custom routine (True = masked)
    try:
        cr_mask = mask_and_remove_cosmic_rays(image_data, science_header)
    except Exception:
        cr_mask = np.zeros_like(border_mask, dtype=bool)

    # satellite trail mask using ASTRiDE (True = masked)
    try:
        satellite_mask = detect_and_mask_satellite_trails(image_data, science_header)
    except Exception:
        satellite_mask = np.zeros_like(border_mask, dtype=bool)

    # normalize to boolean and ensure compatible shape
    def _to_bool_mask(arr, ref_shape):
        if arr is None:
            return np.zeros(ref_shape, dtype=bool)
        a = np.asarray(arr)
        if a.shape != ref_shape:
            if a.size == np.prod(ref_shape):
                try:
                    a = a.reshape(ref_shape)
                except Exception:
                    return np.zeros(ref_shape, dtype=bool)
            else:
                return np.zeros(ref_shape, dtype=bool)
        return a.astype(bool)

    final_mask = np.logical_or(
        _to_bool_mask(border_mask, image_data.shape[:2]),
        _to_bool_mask(cr_mask, image_data.shape[:2]),
    )
    # Add satellite trail mask to the final mask
    final_mask = np.logical_or(
        final_mask,
        _to_bool_mask(satellite_mask, image_data.shape[:2])
    )
    mask = final_mask

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.imshow(mask, cmap="viridis", origin="lower")
    st.pyplot(fig)
    st.pyplot(bkg_fig)

    # Ensure image_sub is float64 to avoid casting errors
    image_sub = image_data.astype(np.float64) - bkg.background.astype(np.float64)

    show_subtracted_image(image_sub)

    # Ensure bkg_error is also float64
    bkg_error = np.full_like(
        image_sub, bkg.background_rms.astype(np.float64), dtype=np.float64
    )

    # Get camera gain from header, fallback to computed value
    camera_gain = science_header.get("GAIN")
    if camera_gain is None:
        camera_gain = 65635 / np.nanmax(image_data)

    # Convert to float64 to ensure compatibility with calc_total_error
    total_error = calc_total_error(
        image_sub.astype(np.float64),
        bkg_error.astype(np.float64),
        effective_gain=np.float64(camera_gain),
    )

    st.write("Estimating FWHM ...")
    fwhm_estimate, clipped_std = fwhm_fit(image_sub, mean_fwhm_pixel, mask)

    if fwhm_estimate is None:
        st.warning("Failed to estimate FWHM. Using the initial estimate.")
        fwhm_estimate = mean_fwhm_pixel
    if clipped_std is None:
        _, _, clipped_std = sigma_clipped_stats(image_sub, sigma=3.0)

    daofind = DAOStarFinder(
        fwhm=1.5 * fwhm_estimate, threshold=(threshold_sigma + 0.5) * clipped_std
    )

    sources = daofind(image_sub, mask=mask)

    if sources is None or len(sources) == 0:
        st.warning("No sources found!")
        return None, None, daofind, bkg, None, None, fwhm_estimate, mask

    positions = np.transpose((sources["xcentroid"], sources["ycentroid"]))

    # Create multiple circular apertures with different radii
    aperture_radii = [1.1, 1.3]
    _rf = round(float(fwhm_radius_factor), 1)
    if _rf not in aperture_radii:
        aperture_radii.append(_rf)
    apertures = [
        CircularAperture(positions, r=radius * fwhm_estimate)
        for radius in aperture_radii
    ]

    # Create circular annulus apertures for background estimation
    annulus_apertures = []
    for radius in aperture_radii:
        r_in = 2.0 * radius * fwhm_estimate
        r_out = 2.5 * radius * fwhm_estimate
        annulus_apertures.append(CircularAnnulus(positions, r_in=r_in, r_out=r_out))

    try:
        wcs_obj = None
        if w is not None:
            try:
                # Use the refined WCS object instead of recreating from header
                wcs_obj = w
                if wcs_obj.pixel_n_dim > 2:
                    wcs_obj = wcs_obj.celestial
            except Exception as e:
                st.warning(f"Error creating WCS object: {e}")
                wcs_obj = None
        elif "CTYPE1" in science_header:
            try:
                # Fallback to header WCS if no refined WCS available
                wcs_obj = WCS(science_header)
                if wcs_obj.pixel_n_dim > 2:
                    wcs_obj = wcs_obj.celestial
            except Exception as e:
                st.warning(f"Error creating WCS object: {e}")
                wcs_obj = None

        # Perform photometry for all apertures
        phot_tables = []

        for i, (aperture, annulus) in enumerate(zip(apertures, annulus_apertures)):
            # Aperture photometry on source
            phot_result = aperture_photometry(
                image_sub, aperture, error=total_error, wcs=wcs_obj
            )

            # Background estimation from annulus
            # According to photutils documentation, aperture_photometry returns the
            # sum within the aperture. For the annulus, this is the total flux in the annulus.
            bkg_result = aperture_photometry(
                image_sub, annulus, error=total_error, wcs=wcs_obj
            )

            # Add radius information and process results
            radius_suffix = f"_{str(aperture_radii[i]).replace('.', '_')}"

            # Rename aperture columns
            if "aperture_sum" in phot_result.colnames:
                phot_result.rename_column(
                    "aperture_sum", f"aperture_sum{radius_suffix}"
                )
            if "aperture_sum_err" in phot_result.colnames:
                phot_result.rename_column(
                    "aperture_sum_err", f"aperture_sum_err{radius_suffix}"
                )

            # Calculate background-corrected photometry following photutils best practices
            # Reference: https://photutils.readthedocs.io/en/2.3.0/user_guide/aperture.html
            if "aperture_sum" in bkg_result.colnames:
                # Get the aperture and annulus areas
                # Using .area property which gives exact analytical area
                annulus_area = annulus.area
                aperture_area = aperture.area

                # Step 1: Calculate per-pixel background from annulus measurement
                # bkg_result["aperture_sum"] is the total flux in the annulus region
                # Divide by annulus area to get average per-pixel background
                with np.errstate(divide="ignore", invalid="ignore"):
                    bkg_per_pixel = np.divide(
                        bkg_result["aperture_sum"],
                        annulus_area,
                        out=np.zeros_like(bkg_result["aperture_sum"]),
                        where=annulus_area != 0,
                    )

                # Step 2: Calculate total background within source aperture
                # Multiply per-pixel background by source aperture area
                total_bkg = bkg_per_pixel * aperture_area

                # Step 3: Compute background-corrected source flux
                aperture_sum_col = f"aperture_sum{radius_suffix}"
                if aperture_sum_col in phot_result.colnames:
                    phot_result[f"aperture_sum_bkg_corr{radius_suffix}"] = (
                        phot_result[aperture_sum_col] - total_bkg
                    )

                # Step 4: Store background information for reference
                phot_result[f"background{radius_suffix}"] = total_bkg
                phot_result[f"background_per_pixel{radius_suffix}"] = bkg_per_pixel

            phot_tables.append(phot_result)

        # Combine all photometry results
        phot_table = phot_tables[0]
        for i in range(1, len(phot_tables)):
            # Add columns from additional apertures
            for col in phot_tables[i].colnames:
                if col not in phot_table.colnames:
                    phot_table[col] = phot_tables[i][col]

        phot_table["xcenter"] = sources["xcentroid"]
        phot_table["ycenter"] = sources["ycentroid"]

        # Calculate SNR and magnitudes for each aperture
        for i, radius in enumerate(aperture_radii):
            radius_suffix = f"_{str(radius).replace('.', '_')}"
            aperture_sum_col = f"aperture_sum{radius_suffix}"
            aperture_err_col = f"aperture_sum_err{radius_suffix}"
            bkg_corr_col = f"aperture_sum_bkg_corr{radius_suffix}"

            if (
                aperture_sum_col in phot_table.colnames
                and aperture_err_col in phot_table.colnames
            ):
                # Use background-corrected flux for S/N if available (more accurate)
                if bkg_corr_col in phot_table.colnames:
                    # Use background-corrected flux for S/N calculation
                    flux_for_snr = phot_table[bkg_corr_col]
                    # For negative/zero bkg-corrected flux, fall back to raw flux
                    use_raw = flux_for_snr <= 0
                    flux_for_snr = np.where(
                        use_raw, phot_table[aperture_sum_col], flux_for_snr
                    )
                else:
                    # Fall back to raw flux if bkg-corrected not available
                    flux_for_snr = phot_table[aperture_sum_col]

                # SNR calculation (no rounding to preserve precision)
                # Formula: S/N = flux / flux_error
                # Uses background-corrected flux when available for better accuracy
                phot_table[f"snr{radius_suffix}"] = (
                    flux_for_snr / phot_table[aperture_err_col]
                )

                # Calculate magnitude error directly from flux and flux_err
                # Formula: σ_mag = 1.0857 × (σ_flux / flux)
                # Derivation: From mag = -2.5 × log10(flux), using error propagation:
                #   σ_mag = |d(mag)/d(flux)| × σ_flux = (2.5/ln(10)) × (σ_flux/flux)
                #   where 2.5/ln(10) ≈ 1.0857
                m_err = (
                    1.0857 * phot_table[aperture_err_col] / phot_table[aperture_sum_col]
                )
                phot_table[f"aperture_mag_err{radius_suffix}"] = m_err

                # Add quality flag based on S/N
                snr_values = phot_table[f"snr{radius_suffix}"]
                quality_flag = np.where(
                    snr_values <= 5,
                    "poor",
                    np.where(snr_values <= 10, "marginal", "good"),
                )
                phot_table[f"quality_flag{radius_suffix}"] = quality_flag

                # Instrumental magnitude for raw aperture sum
                instrumental_mags = -2.5 * np.log10(phot_table[aperture_sum_col])
                phot_table[f"instrumental_mag{radius_suffix}"] = instrumental_mags

                # If background-corrected flux is available, calculate its magnitude
                if bkg_corr_col in phot_table.colnames:
                    # Initialize column with NaN
                    phot_table[f"instrumental_mag_bkg_corr{radius_suffix}"] = np.nan

                    # Handle negative or zero background-corrected fluxes
                    valid_flux = phot_table[bkg_corr_col] > 0

                    phot_table[f"instrumental_mag_bkg_corr{radius_suffix}"][
                        valid_flux
                    ] = -2.5 * np.log10(phot_table[bkg_corr_col][valid_flux])
            else:
                phot_table[f"snr{radius_suffix}"] = np.nan
                phot_table[f"aperture_mag_err{radius_suffix}"] = np.nan
                phot_table[f"instrumental_mag{radius_suffix}"] = np.nan
                phot_table[f"quality_flag{radius_suffix}"] = "unknown"

        try:
            epsf_table, _ = perform_psf_photometry(
                image_sub, sources, fwhm_estimate, daofind, mask, total_error
            )

            if epsf_table is not None:
                # SNR for PSF photometry (no rounding, flux_err is already std dev not variance)
                # Formula: S/N = flux_fit / flux_err
                # Note: PSFPhotometry returns flux_err as standard deviation, not variance
                epsf_table["snr"] = epsf_table["flux_fit"] / epsf_table["flux_err"]
                # Calculate magnitude error directly from flux and flux_err
                # Formula: σ_mag = 1.0857 × (σ_flux / flux)
                # Same derivation as aperture photometry (see above)
                m_err = 1.0857 * epsf_table["flux_err"] / epsf_table["flux_fit"]
                epsf_table["psf_mag_err"] = m_err

                # Add quality flag for PSF photometry
                psf_snr = epsf_table["snr"]
                psf_quality_flag = np.where(
                    psf_snr <= 5,
                    "poor",
                    np.where(psf_snr <= 10, "marginal", "good"),
                )
                epsf_table["psf_quality_flag"] = psf_quality_flag

                epsf_instrumental_mags = -2.5 * np.log10(epsf_table["flux_fit"])
                epsf_table["instrumental_mag"] = epsf_instrumental_mags
            else:
                st.warning(
                    "PSF photometry was not performed. Continuing with aperture photometry only."
                )
        except Exception as e:
            st.warning(
                f"Error performing EPSF photometry: {e}. Continuing with aperture photometry only."
            )
            epsf_table = None

        # Use the first aperture's columns (since "aperture_sum" was renamed)
        first_aperture_suffix = f"_{str(aperture_radii[0]).replace('.', '_')}"
        first_aperture_col = f"aperture_sum{first_aperture_suffix}"
        first_mag_col = f"instrumental_mag{first_aperture_suffix}"

        valid_sources = (phot_table[first_aperture_col] > 0) & np.isfinite(
            phot_table[first_mag_col]
        )
        phot_table = phot_table[valid_sources]

        if epsf_table is not None:
            epsf_valid_sources = np.isfinite(epsf_table["instrumental_mag"])
            epsf_table = epsf_table[epsf_valid_sources]

        try:
            if wcs_obj is not None:
                ra, dec = wcs_obj.pixel_to_world_values(
                    phot_table["xcenter"], phot_table["ycenter"]
                )
                phot_table["ra"] = ra * u.deg
                phot_table["dec"] = dec * u.deg
                st.success(f"Added RA/Dec coordinates to {len(phot_table)} sources")

                if epsf_table is not None:
                    try:
                        epsf_ra, epsf_dec = wcs_obj.pixel_to_world_values(
                            epsf_table["x_fit"], epsf_table["y_fit"]
                        )
                        epsf_table["ra"] = epsf_ra * u.deg
                        epsf_table["dec"] = epsf_dec * u.deg
                        st.success(
                            f"Added RA/Dec coordinates to {len(epsf_table)} PSF sources"
                        )
                    except Exception as e:
                        st.warning(f"Could not add coordinates to EPSF table: {e}")
            else:
                st.warning("No WCS object available - coordinates will not be added")
                if all(
                    key in science_header for key in ["RA", "DEC", "NAXIS1", "NAXIS2"]
                ):
                    center_ra = science_header["RA"]
                    center_dec = science_header["DEC"]
                    width = science_header["NAXIS1"]
                    height = science_header["NAXIS2"]

                    pix_scale = pixel_scale or 1.0

                    center_x = width / 2
                    center_y = height / 2

                    for i, row in enumerate(phot_table):
                        x = row["xcenter"]
                        y = row["ycenter"]
                        dx = (x - center_x) * pix_scale / 3600.0
                        dy = (y - center_y) * pix_scale / 3600.0
                        phot_table[i]["ra"] = center_ra + dx / np.cos(
                            np.radians(center_dec)
                        )
                        phot_table[i]["dec"] = center_dec + dy
        except Exception as e:
            st.warning(
                f"WCS transformation failed: {e}. RA and Dec not added to tables."
            )

        st.write(f"Found {len(phot_table)} sources and performed photometry.")
        return (
            phot_table,
            epsf_table,
            daofind,
            bkg,
            wcs_obj,
            bkg_fig,
            fwhm_estimate,
            mask,
        )
    except Exception as e:
        st.error(f"Error performing aperture photometry: {e}")
        return None, None, daofind, bkg, wcs_obj, None, fwhm_estimate, mask


def show_subtracted_image(image_sub):
    """
    Display the background-subtracted image using Streamlit.
    Parameters
    ----------
    image_sub : numpy.ndarray
        The background-subtracted image data.
    """
    fig, ax = plt.subplots(figsize=(7, 7))
    zscale = ZScaleInterval()
    vmin, vmax = zscale.get_limits(image_sub)
    im = ax.imshow(image_sub, origin="lower", cmap="viridis", vmin=vmin, vmax=vmax)
    ax.set_title("Background-subtracted image")
    plt.colorbar(im, ax=ax, label="Flux")
    st.pyplot(fig)
    plt.close(fig)


def calculate_zero_point(_phot_table, _matched_table, filter_band, air, fwhm_radius_factor: float = 1.5):
    """
    Calculate photometric zero point from matched sources with GAIA.

    The zero point transforms instrumental magnitudes to the standard GAIA magnitude system,
    accounting for atmospheric extinction using the provided airmass.

    Parameters
    ----------
    _phot_table : astropy.table.Table or pandas.DataFrame
        Photometry results table (underscore prevents caching issues)
    _matched_table : pandas.DataFrame
        Table of GAIA cross-matched sources (used for calibration)
    filter_band : str
        GAIA magnitude band used (e.g., 'phot_g_mean_mag')
    air : float
        Airmass value for atmospheric extinction correction

    Returns
    -------
    tuple
        (zero_point_value, zero_point_std, matplotlib_figure) where:
        - zero_point_value: Float value of the calculated zero point
        - zero_point_std: Standard deviation of the zero point
        - matplotlib_figure: Figure object showing GAIA vs. calibrated magnitudes

    Notes
    -----
    - Uses sigma clipping to remove outliers from zero point calculation
    - Applies a simple atmospheric extinction correction of 0.1*airmass
    - Stores the calibrated photometry table in session state as 'final_phot_table'
    - Creates and saves a plot showing the relation between GAIA and calibrated magnitudes
    """
    if _matched_table is None or len(_matched_table) == 0:
        st.warning("No matched sources to calculate zero point.")
        return None, None, None

    try:
        # Define aperture radii (should match the ones used in detection_and_photometry)
        aperture_radii = [1.1, 1.3]
        _rf = round(float(fwhm_radius_factor), 1)
        if _rf not in aperture_radii:
            aperture_radii.append(_rf)

        # Use the 1.3 aperture as the default for zero point calculation.
        # This preserves support for computing additional aperture magnitudes
        # while anchoring the ZP fit to the requested calibration aperture.
        default_radius = 1.3
        radius_suffix = f"_{str(default_radius).replace('.', '_')}"
        instrumental_mag_col = f"instrumental_mag{radius_suffix}"

        # Check if the column exists in matched table
        if instrumental_mag_col not in _matched_table.columns:
            st.error(
                f"Column '{instrumental_mag_col}' not found in matched table. Available columns: {list(_matched_table.columns)}"
            )
            return None, None, None

        valid = np.isfinite(_matched_table[instrumental_mag_col]) & np.isfinite(
            _matched_table[filter_band]
        )
        matched_table = _matched_table.loc[valid].copy()

        if len(matched_table) == 0:
            st.warning("No valid matched sources for zero point calculation.")
            return None, None, None

        # Robust zero-point estimation using iterative sigma clipping.
        # Uses median as centre and MAD-based scale, iterating until convergence.
        # The same clip mask is applied to matched_table so the downstream plots
        # and the ZP value are always derived from the identical set of stars.
        zero_points_raw = (
            matched_table[filter_band] - matched_table[instrumental_mag_col]
        ).values
        clipped = sigma_clip(
            zero_points_raw, sigma=3.0, maxiters=10,
            cenfunc="median", stdfunc="mad_std"
        )

        # np.ma.getmaskarray returns a proper boolean array even when sigma_clip
        # clips nothing and the mask is the nomask singleton.
        keep_mask = ~np.ma.getmaskarray(clipped)
        if not keep_mask.any():
            st.warning("All matched sources removed by sigma clipping for zero point.")
            return None, None, None

        # Restrict matched_table to the accepted calibration stars so that the
        # same rows drive the ZP value, the column assignments, and the plots.
        matched_table = matched_table.iloc[keep_mask].copy()
        zero_points_clean = clipped.compressed()  # unmasked values only

        # Apply an additional strict hard-cut on absolute residuals.
        # This keeps only calibrators within +/- 0.5 mag from the ZP centre.
        residual_limit_mag = 0.5
        zp_center = float(np.median(zero_points_clean))
        strict_keep = np.abs(zero_points_clean - zp_center) <= residual_limit_mag
        if not strict_keep.any():
            st.warning(
                "All matched sources removed by strict residual filtering "
                f"(|residual| <= {residual_limit_mag:.1f} mag)."
            )
            return None, None, None

        matched_table = matched_table.iloc[strict_keep].copy()
        zero_points_clean = zero_points_clean[strict_keep]

        zero_point_value = float(np.median(zero_points_clean))
        n_cal = len(zero_points_clean)
        # Dispersion of calibrator residuals (informative, stored in the table)
        if n_cal > 1:
            zero_point_scatter = float(np.std(zero_points_clean, ddof=1))
        else:
            zero_point_scatter = 0.0
        # Formal uncertainty on the ZP estimator: scatter / sqrt(N)
        zero_point_std = zero_point_scatter / np.sqrt(n_cal)

        if np.isnan(zero_point_value):
            st.warning("Zero point calculation returned NaN.")
            return None, None, None

        st.info(
            f"Sigma-clipping + strict residual cut (|residual| <= {residual_limit_mag:.1f} mag) "
            f"kept {n_cal} / {len(zero_points_raw)} calibration stars "
            f"(scatter = {zero_point_scatter:.3f} mag)."
        )

        _matched_table = matched_table

        _matched_table["zero_point"] = (
            _matched_table[filter_band] - _matched_table[instrumental_mag_col]
        )
        # Store calibrator scatter (not the formal ZP uncertainty) in the table
        _matched_table["zero_point_error"] = zero_point_scatter

        if not isinstance(_phot_table, pd.DataFrame):
            _phot_table = _phot_table.to_pandas()

        # Remove old single-aperture columns if they exist
        old_columns = ["aperture_mag", "aperture_instrumental_mag", "aperture_mag_err"]

        for col in old_columns:
            if col in _phot_table.columns:
                _phot_table.drop(columns=[col], inplace=True)

        # Pin the default aperture suffix now, before the loops below overwrite
        # radius_suffix with the last element of aperture_radii.
        default_radius_suffix = radius_suffix

        # Add calibrated magnitudes for all aperture radii
        for radius in aperture_radii:
            radius_suffix = f"_{str(radius).replace('.', '_')}"
            instrumental_col = f"instrumental_mag{radius_suffix}"
            aperture_mag_col = f"aperture_mag{radius_suffix}"

            if instrumental_col in _phot_table.columns:
                _phot_table[aperture_mag_col] = (
                    _phot_table[instrumental_col] + zero_point_value  # - 0.09 * air
                )

        # Also apply to matched table for all aperture radii
        for radius in aperture_radii:
            radius_suffix = f"_{str(radius).replace('.', '_')}"
            instrumental_col = f"instrumental_mag{radius_suffix}"
            aperture_mag_col = f"aperture_mag{radius_suffix}"

            if instrumental_col in _matched_table.columns:
                _matched_table[aperture_mag_col] = (
                    _matched_table[instrumental_col] + zero_point_value  # - 0.09 * air
                )

        st.session_state["final_phot_table"] = _phot_table

        fig, (ax, ax_resid) = plt.subplots(1, 2, figsize=(14, 6), dpi=100)

        # Use the default aperture radius for plotting (pinned before the loops)
        aperture_mag_col = f"aperture_mag{default_radius_suffix}"

        # Check if the aperture_mag column exists before calculating residuals
        if aperture_mag_col not in _matched_table.columns:
            st.warning(
                f"Column '{aperture_mag_col}' not found. Cannot create calibration plots."
            )
            return round(zero_point_value, 2), round(zero_point_std, 2), None

        # Calculate residuals
        _matched_table["residual"] = (
            _matched_table[filter_band] - _matched_table[aperture_mag_col]
        )

        # Left plot: Zero point calibration
        ax.scatter(
            _matched_table[filter_band],
            _matched_table[aperture_mag_col],
            alpha=0.5,
            label="Matched sources",
            color="blue",
        )

        # Calculate and plot regression line with variance
        x_data = _matched_table[filter_band].values
        y_data = _matched_table[aperture_mag_col].values

        # Remove any NaN values for regression
        valid_mask = np.isfinite(x_data) & np.isfinite(y_data)
        x_clean = x_data[valid_mask]
        y_clean = y_data[valid_mask]

        if len(x_clean) > 1:
            # Calculate linear regression
            coeffs = np.polyfit(x_clean, y_clean, 1)
            slope, intercept = coeffs

            # Create regression line points
            x_reg = np.linspace(x_clean.min(), x_clean.max(), 100)
            y_reg = slope * x_reg + intercept

            # Calculate residuals and standard deviation
            y_pred = slope * x_clean + intercept
            residuals = y_clean - y_pred
            std_residuals = np.std(residuals)

            # Plot regression line
            ax.plot(
                x_reg, y_reg, "r-", linewidth=2, label=f"Regression (slope={slope:.3f})"
            )

            # Plot variance bands (±1σ and ±2σ)
            ax.fill_between(
                x_reg,
                y_reg - std_residuals,
                y_reg + std_residuals,
                alpha=0.3,
                color="red",
                label=f"±1σ ({std_residuals:.3f} mag)",
            )
            ax.fill_between(
                x_reg,
                y_reg - 2 * std_residuals,
                y_reg + 2 * std_residuals,
                alpha=0.15,
                color="red",
                label=f"±2σ ({2 * std_residuals:.3f} mag)",
            )

        # Add a diagonal line for reference
        mag_range = [
            min(
                _matched_table[filter_band].min(),
                _matched_table[aperture_mag_col].min(),
            ),
            max(
                _matched_table[filter_band].max(),
                _matched_table[aperture_mag_col].max(),
            ),
        ]
        ideal_mag = np.linspace(mag_range[0], mag_range[1], 100)
        ax.plot(ideal_mag, ideal_mag, "k--", alpha=0.7, label="y=x")

        ax.set_xlabel(f"Gaia {filter_band}")
        ax.set_ylabel("Calib mag")
        ax.set_title("Gaia magnitude vs Calibrated magnitude")
        ax.legend()
        ax.grid(True, alpha=0.5)

        # Set axis limits to match the actual data range (not auto-scaled)
        margin = 0.5  # Add small margin for readability
        ax.set_xlim(mag_range[0] - margin, mag_range[1] + margin)
        ax.set_ylim(mag_range[0] - margin, mag_range[1] + margin)

        # Right plot: Residuals
        mag_cat = _matched_table[filter_band]
        mag_inst = _matched_table[instrumental_mag_col]
        zp_mean = zero_point_value
        residuals = mag_cat - (mag_inst + zp_mean)

        # Always use the fixed 1.3× aperture error for the residuals plot,
        # regardless of the user-defined FWHM radius factor.
        aperture_err_col = "aperture_mag_err_1_3"
        if aperture_err_col in _matched_table.columns:
            aperture_mag_err = _matched_table[aperture_err_col].values
        elif "aperture_mag_err" in _matched_table.columns:
            aperture_mag_err = _matched_table["aperture_mag_err"].values
        else:
            aperture_mag_err = np.zeros_like(residuals)

        zp_err = zero_point_std if zero_point_std is not None else 0.0
        yerr = np.sqrt(aperture_mag_err**2 + zp_err**2)

        ax_resid.errorbar(
            mag_cat,
            residuals,
            yerr=yerr,
            fmt="o",
            markersize=5,
            alpha=0.7,
            label="Residuals",
        )
        ax_resid.axhline(0, color="gray", ls="--")
        ax_resid.set_xlabel("Calibrated magnitude")
        ax_resid.set_ylabel("Residual (catalog - calibrated)")
        ax_resid.set_title("Photometric Residuals")
        ax_resid.grid(True, alpha=0.5)
        ax_resid.legend()

        # Adjust layout and display
        fig.tight_layout()
        st.pyplot(fig)

        st.success(
            f"Calculated Zero Point: {zero_point_value:.2f} "
            f"± {zero_point_std:.3f} (formal uncertainty, N={n_cal}), "
            f"calibrator scatter = {zero_point_scatter:.3f} mag"
        )

        try:
            base_name = st.session_state.get("base_filename", "photometry")
            username = st.session_state.get("username", "anonymous")
            output_dir = ensure_output_directory(directory=f"{username}_results")
            zero_point_plot_path = os.path.join(
                output_dir, f"{base_name}_zero_point_plot.png"
            )
            fig.savefig(zero_point_plot_path)
        except Exception as e:
            st.warning(f"Could not save plot to file: {e}")

        return round(zero_point_value, 2), round(zero_point_std, 2), fig
    except Exception as e:
        st.error(f"Error calculating zero point: {e}")
        import traceback

        st.error(traceback.format_exc())
        return None, None, None
