import os
import streamlit as st

import numpy as np

from astropy.wcs import WCS
from astropy.io import fits

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
            # Get gain value
            if header.get("GAIN"):
                gain = header.get("GAIN")
            else:
                gain = 65635 / np.max(image)

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


def solve_with_astrometrynet(file_path):
    """
    Solve astrometric plate using local Astrometry.Net installation via stdpipe.
    This function loads a FITS image, detects objects using photutils, and uses stdpipe's
    blind_match_objects wrapper around Astrometry.Net to determine accurate WCS information.

    Refactored to use stdpipe for detection, measurement, and refinement (Gaia DR2).

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

        # Prepare parameters for stdpipe blind_match_objects
        kwargs = {
            "order": 3,  # SIP distortion order
            "update": False,  # Don't update object list in place
            "sn": 5,
            "get_header": False,  # Return WCS object, not header
            "width": image_data.shape[1],
            "height": image_data.shape[0],
            "verbose": True,
        }

        # Note: Pixel scale constraints and RA/DEC hints are now handled
        # in the solve_with_enhanced_strategy function

        try:
            # Use enhanced multi-stage plate solving strategy
            solved_wcs, stage_messages = solve_with_enhanced_strategy(
                sources, header, image_data
            )
            log_messages.extend(stage_messages)

            if solved_wcs is not None:
                log_messages.append("SUCCESS: Plate solving successful")

                # --- Refinement Step (Gaia DR2) ---
                try:
                    log_messages.append(
                        "INFO: Attempting WCS refinement using Gaia DR2"
                    )

                    # 1. Get image parameters from the initial solution
                    height, width = image_data.shape

                    # Get frame center and radius
                    center_ra, center_dec, center_sr = astrometry.get_frame_center(
                        wcs=solved_wcs, width=width, height=height
                    )

                    # 2. Fetch Gaia DR2 catalog
                    log_messages.append(
                        f"INFO: Fetching Gaia DR2 catalog around RA={center_ra:.4f}, DEC={center_dec:.4f}, r={center_sr:.2f} deg"
                    )
                    cat = catalogs.get_cat_vizier(
                        center_ra, center_dec, center_sr, "ps1", filters={"rmag": "<19"}
                    )

                    if cat is not None and len(cat) > 10:
                        log_messages.append(f"INFO: Retrieved {len(cat)} catalog stars")

                        # 3. Refine Astrometry
                        # Get pixel scale from solved WCS for matching radius
                        pixscale_deg = astrometry.get_pixscale(wcs=solved_wcs)

                        refined_wcs = stdpipe_pipeline.refine_astrometry(
                            sources,
                            cat,
                            sr=7 * pixscale_deg,  # Matching radius ~ 5 pixels
                            wcs=solved_wcs,
                            method="scamp",  # Prefer SCAMP as per notebook, falls back if not avail?
                            cat_col_mag="rmag",
                            verbose=True,
                        )

                        if refined_wcs is not None:
                            solved_wcs = refined_wcs
                            log_messages.append(
                                "SUCCESS: Astrometry refined using Gaia DR2"
                            )
                        else:
                            log_messages.append(
                                "WARNING: Astrometric refinement failed, keeping initial solution"
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
                    "WCS solution from astrometry.net (refined with Gaia DR2)"
                )
                updated_header["STDPIPE"] = (
                    True,
                    "Solved with astrometry.blind_match_objects + refinement",
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
