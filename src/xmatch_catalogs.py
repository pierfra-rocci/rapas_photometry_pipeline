import os
import json
from datetime import datetime, timedelta
import requests
from xml.parsers.expat import ExpatError

import numpy as np
import pandas as pd
from astropy.table import join
from astropy.wcs import WCS
from astropy.coordinates import SkyCoord
from astropy.time import Time

import astropy.units as u
from astroquery.gaia import Gaia
from astroquery.simbad import Simbad
from astroquery.vizier import Vizier
from astroquery.imcce import Skybot

from src.tools_pipeline import URL
from src.utils import safe_catalog_query


def cross_match_with_gaia(
    _phot_table,
    science_header,
    pixel_size_arcsec,
    mean_fwhm_pixel,
    filter_band,
    filter_max_mag,
    refined_wcs=None,
):
    """
    Cross-match detected sources with the GAIA DR3 star catalog or PANSTARRS DR1/SkyMapper.
    This function queries the appropriate catalog for a region matching the image field of view:
    - For g, r, i, z Sloan filters: queries PANSTARRS DR1 (north) or SkyMapper (south, dec < 0)
    - For other bands: queries GAIA DR3 with synthetic photometry
    Applies filtering based on magnitude range and matches catalog sources to the detected sources.
    It also applies quality filters including variability, color index, and astrometric quality
    to ensure reliable photometric calibration stars.

    Parameters
    ----------
    _phot_table : astropy.table.Table
        Table containing detected source positions (underscore prevents caching issues)
    science_header : dict or astropy.io.fits.Header
        FITS header with WCS information (underscore prevents caching issues)
    pixel_size_arcsec : float
        Pixel scale in arcseconds per pixel
    mean_fwhm_pixel : float
        FWHM in pixels, used to determine matching radius
    filter_band : str
        Magnitude band to use for filtering. For Sloan filters (gmag, rmag, imag, zmag),
        uses PANSTARRS DR1 or SkyMapper. For GAIA bands (phot_g_mean_mag, phot_bp_mean_mag,
        phot_rp_mean_mag) or synthetic photometry bands, uses GAIA DR3.
    filter_max_mag : float
        Maximum magnitude for source filtering
    refined_wcs : astropy.wcs.WCS, optional
        A refined WCS object to use instead of the one from the header.

    Returns
    -------
    tuple (pandas.DataFrame | None, list[str])
        - (matched_table, log_messages): DataFrame containing matched sources with both measured and catalog data,
        or None if the cross-match failed or found no matches.
        - log_messages: List of log messages.
    """
    log_messages = []
    if science_header is None:
        return None, [
            "WARNING: No header information available. Cannot cross-match with catalog."
        ]

    try:
        if refined_wcs is not None:
            w = refined_wcs
            log_messages.append("INFO: Using refined WCS for cross-matching.")
        else:
            w = WCS(science_header)
            log_messages.append("INFO: Using header WCS for cross-matching.")
    except Exception as e:
        return None, [f"ERROR: Error creating WCS: {e}"]

    try:
        source_positions_pixel = np.transpose(
            (_phot_table["xcenter"], _phot_table["ycenter"])
        )
        source_positions_sky = w.pixel_to_world(
            source_positions_pixel[:, 0], source_positions_pixel[:, 1]
        )
    except Exception as e:
        return None, [
            f"ERROR: Error converting pixel positions to sky coordinates: {e}"
        ]

    try:
        # Validate RA/DEC coordinates before using them
        if "RA" not in science_header or "DEC" not in science_header:
            return None, [
                "ERROR: Missing RA/DEC coordinates in header. Header must contain RA and DEC keywords for field center determination."
            ]

        image_center_ra_dec = [science_header["RA"], science_header["DEC"]]

        # Validate coordinate values
        if not (0 <= image_center_ra_dec[0] <= 360) or not (
            -90 <= image_center_ra_dec[1] <= 90
        ):
            return None, [
                f"ERROR: Invalid coordinates: RA={image_center_ra_dec[0]}, DEC={image_center_ra_dec[1]}"
            ]

        # Calculate search radius (divided by 1.5 to avoid field edge effects)
        catalog_search_radius_arcsec = (
            max(science_header["NAXIS1"], science_header["NAXIS2"])
            * pixel_size_arcsec
            / 1.5
        )
        radius_query = catalog_search_radius_arcsec * u.arcsec
        radius_query_deg = catalog_search_radius_arcsec / 3600.0

        log_messages.append(
            f"INFO: Query radius: {round(radius_query.value / 60.0, 2)} arcmin."
        )

        # Determine if we should use PANSTARRS or GAIA based on filter_band
        sloan_bands = ["gmag", "rmag", "imag", "zmag"]
        is_sloan_filter = filter_band in sloan_bands

        # Mapping from Sloan/PANSTARRS band names to SkyMapper column names
        skymapper_band_mapping = {
            "gmag": "gPSF",
            "rmag": "rPSF",
            "imag": "iPSF",
            "zmag": "zPSF",
        }

        # Create a SkyCoord object for coordinate handling
        center_coord = SkyCoord(
            ra=image_center_ra_dec[0], dec=image_center_ra_dec[1], unit="deg"
        )

        if is_sloan_filter:
            # For Sloan filters (g, r, i, z), use PANSTARRS DR1 or SkyMapper
            # Check if we're in southern hemisphere
            is_southern = image_center_ra_dec[1] < 0

            if is_southern:
                catalog_name = "SkyMapper"
                log_messages.append(
                    f"INFO: Using SkyMapper catalog (southern hemisphere, dec={image_center_ra_dec[1]:.2f})."
                )
            else:
                catalog_name = "Panstarrs"
                log_messages.append(
                    f"INFO: Using PANSTARRS DR1 catalog (northern hemisphere, dec={image_center_ra_dec[1]:.2f})."
                )

            try:
                if is_southern:
                    # Query SkyMapper via Vizier (V/161)
                    # SkyMapper DR4 catalog in Vizier
                    try:
                        # Use '+all' to get all available columns including coordinates
                        v = Vizier(
                            columns=["**", "+_r"]
                        )  # Get all columns plus distance
                        v.ROW_LIMIT = -1  # No row limit
                        catalog_table = v.query_region(
                            center_coord,
                            radius=radius_query_deg * u.deg,
                            catalog="II/379/smssdr4",  # SkyMapper DR4
                        )
                        if len(catalog_table) > 0:
                            catalog_table = catalog_table[0]
                            log_messages.append(
                                f"DEBUG: SkyMapper columns returned: {catalog_table.colnames}"
                            )
                    except Exception as vizier_error:
                        log_messages.append(
                            f"INFO: Vizier SkyMapper query failed, trying alternative: {vizier_error}"
                        )
                        catalog_table = None
                else:
                    # Query PANSTARRS DR1 via Vizier (II/349)
                    # PANSTARRS DR1 catalog in Vizier
                    try:
                        # Use '+all' to get all available columns including coordinates
                        v = Vizier(
                            columns=["**", "+_r"]
                        )  # Get all columns plus distance
                        v.ROW_LIMIT = -1  # No row limit
                        catalog_table = v.query_region(
                            center_coord,
                            radius=radius_query_deg * u.deg,
                            catalog="II/349/ps1",  # PANSTARRS DR1
                        )
                        if len(catalog_table) > 0:
                            catalog_table = catalog_table[0]
                            log_messages.append(
                                f"DEBUG: PANSTARRS columns returned: {catalog_table.colnames}"
                            )
                    except Exception as vizier_error:
                        log_messages.append(
                            f"INFO: Vizier PANSTARRS query failed, trying alternative: {vizier_error}"
                        )
                        catalog_table = None

                if catalog_table is None or len(catalog_table) == 0:
                    log_messages.append(
                        f"WARNING: No {catalog_name} sources found within search radius."
                    )
                    return None, log_messages

                log_messages.append(
                    f"INFO: Retrieved {len(catalog_table)} sources from {catalog_name} via Vizier"
                )

                # Ensure ra/dec columns exist - different catalogs use different column names
                # PANSTARRS uses RAJ2000/DEJ2000, SkyMapper uses RAICRS/DEICRS
                if "ra" not in catalog_table.colnames:
                    if "RAJ2000" in catalog_table.colnames:
                        catalog_table["ra"] = catalog_table["RAJ2000"]
                    elif "RAICRS" in catalog_table.colnames:
                        catalog_table["ra"] = catalog_table["RAICRS"]
                if "dec" not in catalog_table.colnames:
                    if "DEJ2000" in catalog_table.colnames:
                        catalog_table["dec"] = catalog_table["DEJ2000"]
                    elif "DEICRS" in catalog_table.colnames:
                        catalog_table["dec"] = catalog_table["DEICRS"]

            except Exception as catalog_error:
                log_messages.append(
                    f"WARNING: {catalog_name} query failed: {catalog_error}"
                )
                return None, log_messages

        else:
            # For GAIA bands, use GAIA DR3 with synthetic photometry
            # Try Vizier first, then fall back to direct GAIA query
            Gaia.MAIN_GAIA_TABLE = "gaiadr3.gaia_source"

            gaia_radius_query_deg = min(radius_query_deg, 0.3)
            if gaia_radius_query_deg < radius_query_deg:
                log_messages.append(
                    "INFO: Gaia query radius reduced from "
                    f"{radius_query_deg:.3f} deg to {gaia_radius_query_deg:.3f} deg "
                    "to keep TAP requests manageable."
                )

            catalog_table = None
            
            # First, try VizieR
            try:
                log_messages.append(
                    "INFO: Attempting Gaia DR3 query via VizieR with a reduced column set."
                )
                gaia_vizier = Vizier(
                    columns=[
                        "Source",
                        "DR3Name",
                        "RA_ICRS",
                        "DE_ICRS",
                        "Gmag",
                        "BPmag",
                        "RPmag",
                        "BP-RP",
                        "VarFlag",
                        "+_r",
                    ]
                )
                gaia_vizier.ROW_LIMIT = -1
                vizier_result = gaia_vizier.query_region(
                    center_coord,
                    radius=gaia_radius_query_deg * u.deg,
                    catalog="I/355/gaiadr3",
                )

                if vizier_result and len(vizier_result) > 0:
                    catalog_table = vizier_result[0]
                    rename_map = {
                        "Source": "source_id",
                        "DR3Name": "designation",
                        "RA_ICRS": "ra",
                        "DE_ICRS": "dec",
                        "Gmag": "phot_g_mean_mag",
                        "BPmag": "phot_bp_mean_mag",
                        "RPmag": "phot_rp_mean_mag",
                        "BP-RP": "bp_rp",
                        "VarFlag": "phot_variable_flag",
                    }
                    for old_name, new_name in rename_map.items():
                        if (
                            old_name in catalog_table.colnames
                            and new_name not in catalog_table.colnames
                        ):
                            catalog_table.rename_column(old_name, new_name)

                    log_messages.append(
                        f"INFO: Retrieved {len(catalog_table)} Gaia sources from VizieR"
                    )

                    # Query synthetic photometry if needed
                    if (
                        filter_band
                        not in [
                            "phot_g_mean_mag",
                            "phot_bp_mean_mag",
                            "phot_rp_mean_mag",
                        ]
                        and catalog_table is not None
                        and len(catalog_table) > 0
                        and "source_id" in catalog_table.colnames
                    ):
                        max_sources = min(len(catalog_table), 1000)
                        source_ids = list(catalog_table["source_id"][:max_sources])
                        source_ids_str = ",".join(str(id) for id in source_ids)
                        synth_query = f"""
                        SELECT source_id, c_star, u_jkc_mag, v_jkc_mag, b_jkc_mag,
                        r_jkc_mag, i_jkc_mag, u_sdss_mag, g_sdss_mag, r_sdss_mag,
                        i_sdss_mag, z_sdss_mag
                        FROM gaiadr3.synthetic_photometry_gspc
                        WHERE source_id IN ({source_ids_str})
                        """
                        synth_job = Gaia.launch_job(query=synth_query)
                        synth_table = synth_job.get_results()

                        if synth_table is not None and len(synth_table) > 0:
                            log_messages.append(
                                f"INFO: Retrieved {len(synth_table)} synthetic photometry entries from VizieR path"
                            )
                            catalog_table = join(
                                catalog_table,
                                synth_table,
                                keys="source_id",
                                join_type="right",
                            )
                else:
                    log_messages.append(
                        "INFO: Gaia VizieR query returned no results."
                    )
            except Exception as vizier_error:
                log_messages.append(f"WARNING: Gaia VizieR query failed: {vizier_error}")
                log_messages.append(
                    "INFO: Will try direct GAIA TAP query as fallback."
                )

            # If VizieR failed or returned no results, try direct GAIA query
            if catalog_table is None or len(catalog_table) == 0:
                try:
                    log_messages.append(
                        "INFO: Attempting direct GAIA DR3 query via TAP."
                    )
                    gaia_query = f"""
                    SELECT
                        source_id,
                        designation,
                        ra,
                        dec,
                        phot_variable_flag,
                        bp_rp,
                        phot_g_mean_mag,
                        phot_bp_mean_mag,
                        phot_rp_mean_mag
                    FROM gaiadr3.gaia_source
                    WHERE 1 = CONTAINS(
                        POINT('ICRS', ra, dec),
                        CIRCLE(
                            'ICRS',
                            {center_coord.ra.deg},
                            {center_coord.dec.deg},
                            {gaia_radius_query_deg}
                        )
                    )
                    ORDER BY DISTANCE(
                        POINT('ICRS', ra, dec),
                        POINT('ICRS', {center_coord.ra.deg}, {center_coord.dec.deg})
                    ) ASC
                    """
                    job = Gaia.launch_job(query=gaia_query)
                    catalog_table = job.get_results()

                    log_messages.append(
                        "INFO: Retrieved "
                        f"{len(catalog_table) if catalog_table is not None else 0} "
                        "sources from direct GAIA TAP query"
                    )

                    # Different query strategies based on filter band
                    if (
                        filter_band
                        not in ["phot_g_mean_mag", "phot_bp_mean_mag", "phot_rp_mean_mag"]
                        and catalog_table is not None
                        and len(catalog_table) > 0
                    ):
                        # Create a comma-separated list of source_ids (limit to 1000)
                        max_sources = min(len(catalog_table), 1000)
                        source_ids = list(catalog_table["source_id"][:max_sources])
                        source_ids_str = ",".join(str(id) for id in source_ids)
                        # Query synthetic photometry just for these specific sources
                        synth_query = f"""
                        SELECT source_id, c_star, u_jkc_mag, v_jkc_mag, b_jkc_mag,
                        r_jkc_mag, i_jkc_mag, u_sdss_mag, g_sdss_mag, r_sdss_mag,
                        i_sdss_mag, z_sdss_mag
                        FROM gaiadr3.synthetic_photometry_gspc
                        WHERE source_id IN ({source_ids_str})
                        """
                        synth_job = Gaia.launch_job(query=synth_query)
                        synth_table = synth_job.get_results()

                        # Join the two tables
                        if synth_table is not None and len(synth_table) > 0:
                            log_messages.append(
                                f"INFO: Retrieved {len(synth_table)} synthetic photometry entries from direct GAIA path"
                            )
                            catalog_table = join(
                                catalog_table,
                                synth_table,
                                keys="source_id",
                                join_type="right",
                            )

                except Exception as cone_error:
                    log_messages.append(f"WARNING: Direct GAIA TAP query failed: {cone_error}")
                    log_messages.append(
                        "ERROR: Both VizieR and direct GAIA queries failed."
                    )
                    return None, log_messages
    except KeyError as ke:
        return None, [f"ERROR: Missing header keyword: {ke}"]
    except Exception as e:
        return None, [f"ERROR: Error querying catalog: {e}"]

    if catalog_table is None or len(catalog_table) == 0:
        return None, ["WARNING: No catalog sources found within search radius."]

    try:
        # Use the appropriate column name for magnitude filtering
        # For SkyMapper, we need to translate band names
        mag_column = filter_band
        if is_sloan_filter and catalog_name == "SkyMapper":
            mag_column = skymapper_band_mapping.get(filter_band, filter_band)

        # Debug: log available columns before filtering
        log_messages.append(
            f"DEBUG: Catalog columns before filtering: {catalog_table.colnames}"
        )

        # Apply magnitude filter
        mag_filter = catalog_table[mag_column] < filter_max_mag
        catalog_table_filtered = catalog_table[mag_filter]

        # Debug: log available columns after filtering
        log_messages.append(
            f"DEBUG: Catalog columns after filtering: {catalog_table_filtered.colnames}"
        )

        # Apply quality filters based on catalog type
        if is_sloan_filter:
            # For PANSTARRS/SkyMapper, apply basic filters
            # Note: Column names from Vizier:
            # - PANSTARRS: Nd = number of detections
            # - SkyMapper: Ngood = number of good observations
            try:
                # Basic quality filter: sources must have multiple detections
                detection_col = None
                if "Nd" in catalog_table_filtered.colnames:
                    detection_col = "Nd"
                elif "Ngood" in catalog_table_filtered.colnames:
                    detection_col = "Ngood"
                elif "nDetections" in catalog_table_filtered.colnames:
                    detection_col = "nDetections"

                if detection_col is not None:
                    catalog_table_filtered = catalog_table_filtered[
                        catalog_table_filtered[detection_col] > 1
                    ]
                log_messages.append(
                    f"INFO: Applied detection quality filter using column '{detection_col}'."
                )
            except Exception as quality_error:
                log_messages.append(
                    f"WARNING: Could not apply quality filter: {quality_error}"
                )
        else:
            # For GAIA, apply the original quality filters
            try:
                var_filter = catalog_table_filtered["phot_variable_flag"] != "VARIABLE"
                color_index_filter = (catalog_table_filtered["bp_rp"] > -3) & (
                    catalog_table_filtered["bp_rp"] < 3
                )

                combined_filter = var_filter & color_index_filter

                catalog_table_filtered = catalog_table_filtered[combined_filter]
            except Exception as e:
                log_messages.append(
                    f"WARNING: Could not apply GAIA quality filters: {e}"
                )

        if len(catalog_table_filtered) == 0:
            return None, [
                f"WARNING: No catalog sources found within magnitude range {filter_band} < {filter_max_mag}."
            ]

    except Exception as e:
        return None, [f"ERROR: Error filtering catalog: {e}"]

    try:
        # Prepare catalog coordinates for matching
        if is_sloan_filter:
            # For PANSTARRS/SkyMapper via Vizier, use Vizier standard column names
            # PANSTARRS uses RAJ2000/DEJ2000, SkyMapper uses RAICRS/DEICRS
            ra_col = None
            dec_col = None

            if (
                "RAJ2000" in catalog_table_filtered.colnames
                and "DEJ2000" in catalog_table_filtered.colnames
            ):
                ra_col, dec_col = "RAJ2000", "DEJ2000"
            elif (
                "RAICRS" in catalog_table_filtered.colnames
                and "DEICRS" in catalog_table_filtered.colnames
            ):
                ra_col, dec_col = "RAICRS", "DEICRS"
            elif (
                "raMean" in catalog_table_filtered.colnames
                and "decMean" in catalog_table_filtered.colnames
            ):
                ra_col, dec_col = "raMean", "decMean"
            elif (
                "ra" in catalog_table_filtered.colnames
                and "dec" in catalog_table_filtered.colnames
            ):
                ra_col, dec_col = "ra", "dec"
            else:
                # Try to find available columns
                available_cols = catalog_table_filtered.colnames
                ra_candidates = [c for c in available_cols if "ra" in c.lower()]
                dec_candidates = [c for c in available_cols if "dec" in c.lower()]
                if ra_candidates and dec_candidates:
                    ra_col, dec_col = ra_candidates[0], dec_candidates[0]
                else:
                    return None, ["ERROR: Cannot find RA/Dec columns in catalog table"]

            catalog_skycoords = SkyCoord(
                ra=catalog_table_filtered[ra_col],
                dec=catalog_table_filtered[dec_col],
                unit="deg",
            )
        else:
            # For GAIA, use standard ra/dec
            catalog_skycoords = SkyCoord(
                ra=catalog_table_filtered["ra"],
                dec=catalog_table_filtered["dec"],
                unit="deg",
            )

        idx, d2d, _ = source_positions_sky.match_to_catalog_sky(catalog_skycoords)

        max_sep_constraint = 2.5 * mean_fwhm_pixel * pixel_size_arcsec * u.arcsec
        catalog_matches = d2d < max_sep_constraint

        matched_indices_catalog = idx[catalog_matches]
        matched_indices_phot = np.where(catalog_matches)[0]

        if len(matched_indices_catalog) == 0:
            return None, [
                "WARNING: No catalog matches found within the separation constraint."
            ]

        matched_table_qtable = _phot_table[matched_indices_phot]
        matched_table = matched_table_qtable.to_pandas()
        matched_table["catalog_separation_arcsec"] = d2d[catalog_matches].arcsec

        # Add catalog identifiers and magnitude based on catalog type
        if is_sloan_filter:
            # For PANSTARRS/SkyMapper (via Vizier)
            if catalog_name == "SkyMapper":
                # SkyMapper - try different ID column names
                id_col = None
                for col in ["objID", "object_id", "ID"]:
                    if col in catalog_table_filtered.colnames:
                        id_col = col
                        break
                if id_col is None:
                    id_col = catalog_table_filtered.colnames[0]
                matched_table["catalog_source_id"] = np.asarray(
                    catalog_table_filtered[id_col][matched_indices_catalog]
                )
            else:
                # PANSTARRS - use objID from Vizier
                id_col = (
                    "objID"
                    if "objID" in catalog_table_filtered.colnames
                    else catalog_table_filtered.colnames[0]
                )
                matched_table["catalog_source_id"] = np.asarray(
                    catalog_table_filtered[id_col][matched_indices_catalog]
                )
        else:
            # For GAIA
            matched_table["catalog_source_id"] = np.asarray(
                catalog_table_filtered["designation"][matched_indices_catalog]
            )

        # Add the filter_band magnitude (use the correct column name)
        mag_column_for_output = filter_band
        if is_sloan_filter and catalog_name == "SkyMapper":
            mag_column_for_output = skymapper_band_mapping.get(filter_band, filter_band)

        matched_table[filter_band] = np.asarray(
            catalog_table_filtered[mag_column_for_output][matched_indices_catalog]
        )

        if is_sloan_filter:
            standardized_sloan_columns = {
                "gmag": ["gmag", "gPSF"],
                "rmag": ["rmag", "rPSF"],
                "imag": ["imag", "iPSF"],
                "zmag": ["zmag", "zPSF"],
            }
            for standard_name, candidates in standardized_sloan_columns.items():
                source_column = next(
                    (col for col in candidates if col in catalog_table_filtered.colnames),
                    None,
                )
                if source_column is not None:
                    matched_table[standard_name] = np.asarray(
                        catalog_table_filtered[source_column][matched_indices_catalog]
                    )
        else:
            for extra_column in [
                "phot_g_mean_mag",
                "phot_bp_mean_mag",
                "phot_rp_mean_mag",
                "bp_rp",
            ]:
                if extra_column in catalog_table_filtered.colnames:
                    matched_table[extra_column] = np.asarray(
                        catalog_table_filtered[extra_column][matched_indices_catalog]
                    )

        valid_mags = np.isfinite(matched_table[filter_band])
        matched_table = matched_table[valid_mags]

        # Remove sources with SNR < 1
        if "snr" in matched_table.columns:
            matched_table = matched_table[matched_table["snr"] > 1]

        catalog_type = catalog_name if is_sloan_filter else "Gaia"
        log_messages.append(
            f"SUCCESS: Found {len(matched_table)} {catalog_type} matches after filtering."
        )
        return matched_table, log_messages

    except Exception as e:
        return None, [f"ERROR: Error during cross-matching: {e}"]


def enhance_catalog(
    api_key,
    final_table,
    matched_table,
    header,
    pixel_scale_arcsec,
    search_radius_arcsec=60,
):
    """
    Enhance a photometric catalog with cross-matches from multiple
    astronomical databases.

    This function queries several catalogs and databases including:
    - GAIA DR3 (using previously matched sources)
    - Astro-Colibri (for known astronomical sources)
    - SIMBAD (for object identifications and classifications)
    - SkyBoT (for solar system objects)
    - AAVSO VSX (for variable stars)
    - VizieR VII/294 (for quasars)

    Parameters
    ----------
    api_key : str
        API key for Astro-Colibri.
    final_table : pandas.DataFrame
        Final photometry catalog with RA/Dec coordinates
    matched_table : pandas.DataFrame
        Table of already matched GAIA sources (used for calibration)
    header : dict or astropy.io.fits.Header
        FITS header with observation information
    pixel_scale_arcsec : float
        Pixel scale in arcseconds per pixel
    search_radius_arcsec : float, optional
        Search radius for cross-matching in arcseconds, default=60

    Returns
    -------
    tuple (pandas.DataFrame, list[str])
        - enhanced_table: Input dataframe with added catalog information.
        - log_messages: List of log messages.
    """
    log_messages = []
    # Add input validation at the beginning
    if final_table is None:
        return None, ["ERROR: final_table is None - cannot enhance catalog"]

    if len(final_table) == 0:
        log_messages.append("WARNING: No sources to cross-match with catalogs.")
        return final_table, log_messages

    # Ensure we have valid RA/Dec coordinates in final_table
    if "ra" not in final_table.columns or "dec" not in final_table.columns:
        return final_table, [
            "ERROR: final_table must contain 'ra' and 'dec' columns for cross-matching"
        ]

    # Make a copy to avoid modifying the original table
    enhanced_table = final_table.copy()

    # Filter out sources with NaN coordinates at the beginning
    valid_coords_mask = (
        pd.notna(enhanced_table["ra"])
        & pd.notna(enhanced_table["dec"])
        & np.isfinite(enhanced_table["ra"])
        & np.isfinite(enhanced_table["dec"])
    )

    if not valid_coords_mask.any():
        return enhanced_table, [
            "ERROR: No sources with valid RA/Dec coordinates found for cross-matching"
        ]

    num_invalid = len(enhanced_table) - valid_coords_mask.sum()
    if num_invalid > 0:
        log_messages.append(
            f"WARNING: Excluding {num_invalid} sources with invalid coordinates from cross-matching"
        )

    # Compute field of view (arcmin) ONCE and use everywhere
    field_center_ra = None
    field_center_dec = None
    field_width_arcmin = 35.0  # fallback default
    if header is not None:
        if "CRVAL1" in header and "CRVAL2" in header:
            field_center_ra = float(header["CRVAL1"])
            field_center_dec = float(header["CRVAL2"])
        elif "RA" in header and "DEC" in header:
            field_center_ra = float(header["RA"])
            field_center_dec = float(header["DEC"])
        elif "OBJRA" in header and "OBJDEC" in header:
            field_center_ra = float(header["OBJRA"])
            field_center_dec = float(header["OBJDEC"])
        # Compute field width if possible
        if "NAXIS1" in header and "NAXIS2" in header and pixel_scale_arcsec:
            field_width_arcmin = (
                max(header.get("NAXIS1", 1000), header.get("NAXIS2", 1000))
                * pixel_scale_arcsec
                / 60.0
            )

    log_messages.append("INFO: Starting cross-match process...")

    if matched_table is not None and len(matched_table) > 0:
        log_messages.append("INFO: Adding calibration matches...")

        # Use iloc-based matching on valid coordinates only
        valid_indices_list = np.where(valid_coords_mask)[0]

        if len(valid_indices_list) > 0 and len(matched_table) > 0:
            # Filter enhanced_table to only valid coordinates
            valid_enhanced = enhanced_table.iloc[valid_indices_list].copy()

            # Create match_id on valid subset
            if (
                "xcenter" in valid_enhanced.columns
                and "ycenter" in valid_enhanced.columns
            ):
                valid_enhanced["match_id"] = (
                    valid_enhanced["xcenter"].round(2).astype(str)
                    + "_"
                    + valid_enhanced["ycenter"].round(2).astype(str)
                )

            # Create match_id on matched_table
            if (
                "xcenter" in matched_table.columns
                and "ycenter" in matched_table.columns
            ):
                matched_table["match_id"] = (
                    matched_table["xcenter"].round(2).astype(str)
                    + "_"
                    + matched_table["ycenter"].round(2).astype(str)
                )

                calib_cols = [
                    col
                    for col in matched_table.columns
                    if any(x in col for x in ["gaia", "phot_"])
                    or col in {"bp_rp", "gmag", "rmag", "imag", "zmag"}
                ]
                calib_cols.append("match_id")
                calib_cols = list(dict.fromkeys(calib_cols))
                gaia_subset = matched_table[calib_cols].copy()

                rename_dict = {
                    col: (
                        f"gaia_{col}"
                        if (col.startswith("phot_") or col == "bp_rp" or "gaia" in col)
                        else f"calib_{col}"
                    )
                    for col in gaia_subset.columns
                    if col != "match_id"
                    and not col.startswith("gaia_")
                    and not col.startswith("calib_")
                }
                if rename_dict:
                    gaia_subset = gaia_subset.rename(columns=rename_dict)

                # Merge on valid subset
                valid_enhanced = pd.merge(
                    valid_enhanced, gaia_subset, on="match_id", how="left"
                )

                # Map back to full table using iloc
                enhanced_table.iloc[valid_indices_list] = valid_enhanced

                # Add gaia_calib_star column
                enhanced_table["calib_star"] = False
                enhanced_table.iloc[
                    valid_indices_list,
                    enhanced_table.columns.get_loc("calib_star"),
                ] = valid_enhanced["match_id"].isin(matched_table["match_id"]).values

                log_messages.append(
                    f"SUCCESS: Added {len(matched_table)} calibration stars to catalog"
                )

    if field_center_ra is not None and field_center_dec is not None:
        if not (-360 <= field_center_ra <= 360) or not (-90 <= field_center_dec <= 90):
            log_messages.append(
                f"WARNING: Invalid coordinates: RA={field_center_ra}, DEC={field_center_dec}"
            )
        else:
            pass
    else:
        log_messages.append(
            "WARNING: Could not extract field center coordinates from header"
        )

    log_messages.append("INFO: Querying Astro-Colibri API...")

    if api_key is None:
        api_key = os.environ.get("ASTROCOLIBRI_API")
        if api_key is None:
            log_messages.append(
                "WARNING: No API key for ASTRO-COLIBRI provided or found"
            )
            pass

    try:
        try:
            # Base URL of the API
            url = URL + "cone_search"

            # Request parameters (headers, body)
            headers = {"Content-Type": "application/json"}

            # Define date range for the query
            observation_date = None
            if header is not None:
                if "DATE-OBS" in header:
                    observation_date = header["DATE-OBS"]
                elif "DATE" in header:
                    observation_date = header["DATE"]

            # Set time range to ±60 days from observation date or current date
            if observation_date:
                try:
                    base_date = datetime.fromisoformat(
                        observation_date.replace("T", " ").split(".")[0]
                    )
                except (ValueError, TypeError):
                    base_date = datetime.now()
            else:
                base_date = datetime.now()

            date_min = (base_date - timedelta(days=60)).isoformat()
            date_max = (base_date + timedelta(days=60)).isoformat()

            body = {
                "uid": api_key,
                "filter": None,
                "time_range": {
                    "max": date_max,
                    "min": date_min,
                },
                "properties": {
                    "type": "cone",
                    "position": {"ra": field_center_ra, "dec": field_center_dec},
                    "radius": field_width_arcmin / 2.0,
                },
            }

            # Perform the POST request
            response = requests.post(url, headers=headers, data=json.dumps(body))

            # Process the response
            try:
                if response.status_code == 200:
                    events = response.json()["voevents"]
                else:
                    log_messages.append(f"WARNING: url: {url}")
                    log_messages.append(
                        f"WARNING: Request failed with status code: {response.status_code}"
                    )
            except json.JSONDecodeError:
                log_messages.append(
                    f"ERROR: Request did NOT succeed : {response.status_code}"
                )
                log_messages.append(
                    f"ERROR: Error message : {response.content.decode('UTF-8')}"
                )

        except Exception as e:
            log_messages.append(f"ERROR: Error querying Astro-Colibri API: {str(e)}")
            # Continue with function instead of returning None

        if response is not None and response.status_code == 200:
            sources = {
                "ra": [],
                "dec": [],
                "source_name": [],
                "type": [],
                "classification": [],
            }

            enhanced_table["astrocolibri_name"] = None
            enhanced_table["astrocolibri_type"] = None
            enhanced_table["astrocolibri_classification"] = None
            for event in events:
                if "ra" in event and "dec" in event:
                    sources["ra"].append(event["ra"])
                    sources["dec"].append(event["dec"])
                    sources["source_name"].append(event["source_name"])
                    sources["type"].append(event["type"])
                    sources["classification"].append(event["classification"])
            astrostars = pd.DataFrame(sources)
            log_messages.append(
                f"INFO: Found {len(astrostars)} Astro-Colibri sources in field."
            )

            # Filter valid coordinates for astro-colibri matching
            valid_final_coords = enhanced_table[valid_coords_mask]

            if len(valid_final_coords) > 0 and len(astrostars) > 0:
                source_coords = SkyCoord(
                    ra=valid_final_coords["ra"].values,
                    dec=valid_final_coords["dec"].values,
                    unit="deg",
                )

                astro_colibri_coords = SkyCoord(
                    ra=astrostars["ra"],
                    dec=astrostars["dec"],
                    unit=(u.deg, u.deg),
                )

                if not isinstance(search_radius_arcsec, (int, float)):
                    raise ValueError("Search radius must be a number")

                idx, d2d, _ = source_coords.match_to_catalog_sky(astro_colibri_coords)
                matches = d2d < (10 * u.arcsec)

                # Map matches back to the original table indices
                valid_indices = list(valid_final_coords.index)

                # Use enumerate to iterate indices cleanly and avoid unpacking errors
                for i, match_idx in enumerate(idx):
                    try:
                        if not bool(matches[i]):
                            continue
                    except Exception:
                        # fallback if matches is a masked/quantity array
                        if getattr(matches[i], "value", False) is False:
                            continue

                    original_idx = valid_indices[i]
                    # Use iloc for safe pandas indexing
                    try:
                        enhanced_table.loc[original_idx, "astrocolibri_name"] = (
                            astrostars.iloc[int(match_idx)]["source_name"]
                        )
                    except Exception:
                        enhanced_table.loc[original_idx, "astrocolibri_name"] = (
                            astrostars["source_name"].iloc[int(match_idx)]
                        )

                    try:
                        enhanced_table.loc[original_idx, "astrocolibri_type"] = (
                            astrostars.iloc[int(match_idx)]["type"]
                        )
                    except Exception:
                        enhanced_table.loc[original_idx, "astrocolibri_type"] = (
                            astrostars["type"].iloc[int(match_idx)]
                        )

                    try:
                        enhanced_table.loc[
                            original_idx, "astrocolibri_classification"
                        ] = astrostars.iloc[int(match_idx)]["classification"]
                    except Exception:
                        enhanced_table.loc[
                            original_idx, "astrocolibri_classification"
                        ] = astrostars["classification"].iloc[int(match_idx)]

                log_messages.append("INFO: Astro-Colibri matched objects in field.")
            else:
                log_messages.append(
                    "INFO: No valid coordinates available for Astro-Colibri matching"
                )
        else:
            log_messages.append("INFO: No Astro-Colibri sources found in the field.")
    except Exception as e:
        log_messages.append(f"ERROR: Error querying Astro-Colibri: {str(e)}")
        log_messages.append("INFO: No Astro-Colibri sources found.")

    log_messages.append("INFO: Querying SIMBAD for object identifications...")

    custom_simbad = Simbad()
    custom_simbad.add_votable_fields("otype", "main_id", "ids", "B", "V")

    log_messages.append("INFO: Querying SIMBAD")

    try:
        center_coord = SkyCoord(ra=field_center_ra, dec=field_center_dec, unit="deg")
        simbad_result, error = safe_catalog_query(
            custom_simbad.query_region,
            "SIMBAD query failed",
            center_coord,
            radius=field_width_arcmin * u.arcmin,
        )
        if error:
            log_messages.append(f"WARNING: {error}")
        else:
            if simbad_result is not None and len(simbad_result) > 0:
                enhanced_table["simbad_main_id"] = None
                enhanced_table["simbad_otype"] = None
                enhanced_table["simbad_ids"] = None
                enhanced_table["simbad_B"] = None
                enhanced_table["simbad_V"] = None

                # Filter valid coordinates for SIMBAD matching
                valid_final_coords = enhanced_table[valid_coords_mask]

                if len(valid_final_coords) > 0:
                    source_coords = SkyCoord(
                        ra=valid_final_coords["ra"].values,
                        dec=valid_final_coords["dec"].values,
                        unit="deg",
                    )

                    if all(col in simbad_result.colnames for col in ["ra", "dec"]):
                        try:
                            # Filter out NaN coordinates in SIMBAD result
                            simbad_valid_mask = (
                                pd.notna(simbad_result["ra"])
                                & pd.notna(simbad_result["dec"])
                                & np.isfinite(simbad_result["ra"])
                                & np.isfinite(simbad_result["dec"])
                            )

                            if not simbad_valid_mask.any():
                                log_messages.append(
                                    "WARNING: No SIMBAD sources with valid coordinates found"
                                )
                            else:
                                simbad_filtered = simbad_result[simbad_valid_mask]

                                simbad_coords = SkyCoord(
                                    ra=simbad_filtered["ra"],
                                    dec=simbad_filtered["dec"],
                                    unit=(u.hourangle, u.deg),
                                )

                                idx, d2d, _ = source_coords.match_to_catalog_sky(
                                    simbad_coords
                                )
                                matches = d2d <= (10.0 * u.arcsec)

                                # Map matches back to the original table indices
                                valid_indices = valid_final_coords.index

                                for i, (match, match_idx) in enumerate(
                                    zip(matches, idx)
                                ):
                                    if match:
                                        original_idx = valid_indices[i]
                                        enhanced_table.loc[
                                            original_idx, "simbad_main_id"
                                        ] = simbad_filtered["main_id"][match_idx]
                                        enhanced_table.loc[
                                            original_idx, "simbad_otype"
                                        ] = simbad_filtered["otype"][match_idx]
                                        enhanced_table.loc[original_idx, "simbad_B"] = (
                                            simbad_filtered["B"][match_idx]
                                        )
                                        enhanced_table.loc[original_idx, "simbad_V"] = (
                                            simbad_filtered["V"][match_idx]
                                        )
                                        if "ids" in simbad_filtered.colnames:
                                            enhanced_table.loc[
                                                original_idx, "simbad_ids"
                                            ] = simbad_filtered["ids"][match_idx]

                                log_messages.append(
                                    f"INFO: Found {sum(matches)} SIMBAD objects in field."
                                )
                        except Exception as e:
                            log_messages.append(
                                f"ERROR: Error creating SkyCoord objects from SIMBAD data: {str(e)}"
                            )
                            log_messages.append(
                                f"INFO: Available SIMBAD columns: {simbad_result.colnames}"
                            )
                    else:
                        available_cols = ", ".join(simbad_result.colnames)
                        log_messages.append(
                            f"ERROR: SIMBAD result missing required columns. Available columns: {available_cols}"
                        )
                else:
                    log_messages.append(
                        "INFO: No valid coordinates available for SIMBAD matching"
                    )
            else:
                log_messages.append("INFO: No SIMBAD objects found in the field.")
    except Exception as e:
        log_messages.append(f"ERROR: SIMBAD query execution failed: {str(e)}")

    try:
        if field_center_ra is not None and field_center_dec is not None:
            if "DATE-OBS" in header:
                obs_date = header["DATE-OBS"]
            elif "DATE" in header:
                obs_date = header["DATE"]
            else:
                obs_date = Time.now().isot

            obs_time = Time(obs_date)

            sr_value = min(
                field_width_arcmin / 60.0, 1.0
            )  # degrees, limit to 1 deg max

            log_messages.append("INFO: Querying SkyBoT for solar system objects...")

            try:
                # Prepare output columns
                enhanced_table["skybot_NAME"] = None
                enhanced_table["skybot_OBJECT_TYPE"] = None
                enhanced_table["skybot_MAGV"] = None

                # Use Skybot cone search via direct HTTP request.
                # astroquery 0.4.10 hardcodes the stale vo.imcce.fr URL which
                # is no longer reachable; the service moved to ssp.imcce.fr.
                field_coord = SkyCoord(
                    ra=field_center_ra, dec=field_center_dec, unit=u.deg
                )

                _SKYBOT_URL = "https://ssp.imcce.fr/webservices/skybot/api/conesearch.php"
                _skybot_payload = {
                    "-ra": field_coord.ra.deg,
                    "-dec": field_coord.dec.deg,
                    "-rd": min(float(sr_value), 10.0),
                    "-ep": str(obs_time.jd),
                    "-loc": "500",
                    "-filter": 120,
                    "-objFilter": "111",
                    "-refsys": "EQJ2000",
                    "-output": "all",
                    "-mime": "text",
                }
                _skybot_resp = requests.get(
                    _SKYBOT_URL, params=_skybot_payload, timeout=120
                )
                if _skybot_resp.status_code == 204 or not _skybot_resp.text.strip():
                    skybot_result = None
                else:
                    try:
                        Skybot._get_raw_response = False
                        skybot_result = Skybot._parse_result(_skybot_resp)
                    except (ExpatError, ValueError) as parse_error:
                        preview = _skybot_resp.text.strip().replace("\n", " ")[:120]
                        log_messages.append(
                            "WARNING: SkyBoT response could not be parsed "
                            f"({parse_error}). Skipping SkyBoT match. Response preview: {preview}"
                        )
                        skybot_result = None

                if skybot_result is None or len(skybot_result) == 0:
                    log_messages.append(
                        "INFO: No solar system objects found in the field."
                    )
                else:
                    log_messages.append(
                        f"INFO: Found {len(skybot_result)} solar system objects."
                    )

                    # Convert astropy table to list of dicts for easier access
                    data = [
                        dict(zip(skybot_result.colnames, row)) for row in skybot_result
                    ]

                    # Build SkyCoord from returned objects
                    ra_list = []
                    dec_list = []
                    good_indices = []

                    for i_obj, obj in enumerate(data):
                        try:
                            ra_val = obj.get("RA")
                            dec_val = obj.get("DEC")

                            if ra_val is None or dec_val is None:
                                continue

                            # Extract numeric values from Quantity objects
                            ra_f = float(
                                ra_val.value if hasattr(ra_val, "value") else ra_val
                            )
                            dec_f = float(
                                dec_val.value if hasattr(dec_val, "value") else dec_val
                            )

                            ra_list.append(ra_f)
                            dec_list.append(dec_f)
                            good_indices.append(i_obj)
                        except Exception:
                            continue

                    log_messages.append(
                        f"INFO: Extracted {len(ra_list)} valid coordinates from SkyBoT results"
                    )

                    if len(ra_list) == 0:
                        log_messages.append(
                            "INFO: SkyBoT returned entries but no usable coordinates."
                        )
                    else:
                        skybot_coords = SkyCoord(ra=ra_list, dec=dec_list, unit=u.deg)

                        # Filter valid coordinates for SkyBoT matching
                        valid_final_coords = enhanced_table[valid_coords_mask]

                        if len(valid_final_coords) == 0:
                            log_messages.append(
                                "INFO: No valid coordinates available for SkyBoT matching"
                            )
                        else:
                            source_coords = SkyCoord(
                                ra=valid_final_coords["ra"].values,
                                dec=valid_final_coords["dec"].values,
                                unit=u.deg,
                            )

                            # Use 2D sky matching
                            idx, d2d, _ = source_coords.match_to_catalog_sky(
                                skybot_coords
                            )
                            max_sep = 10.0 * u.arcsec
                            matches = d2d <= max_sep

                            # Map matches back to the original table indices
                            valid_indices = valid_final_coords.index
                            matched_sources = np.where(matches)[0]

                            # Initialize catalog_matches column if missing
                            if "catalog_matches" not in enhanced_table.columns:
                                enhanced_table["catalog_matches"] = ""

                            for src_i in matched_sources:
                                original_idx = valid_indices[src_i]
                                skybot_idx = good_indices[int(idx[src_i])]
                                rec = data[skybot_idx]

                                name = rec.get("Name")
                                objtype = rec.get("Type")
                                magv = rec.get("V")
                                epoch = rec.get("epoch")

                                # Extract values from Quantity objects
                                if magv is not None and hasattr(magv, "value"):
                                    magv = float(magv.value)
                                if epoch is not None and hasattr(epoch, "value"):
                                    epoch = float(epoch.value)

                                enhanced_table.loc[original_idx, "skybot_NAME"] = name
                                enhanced_table.loc[
                                    original_idx, "skybot_OBJECT_TYPE"
                                ] = objtype
                                enhanced_table.loc[original_idx, "skybot_MAGV"] = magv
                                enhanced_table.loc[original_idx, "skybot_epoch"] = epoch

                            has_skybot = enhanced_table["skybot_NAME"].notna()
                            enhanced_table.loc[has_skybot, "catalog_matches"] += (
                                "SkyBoT; "
                            )
                            log_messages.append(
                                f"INFO: Found {int(has_skybot.sum())} solar system objects matched in field."
                            )
            except Exception as e:
                log_messages.append(
                    f"ERROR: Unexpected error during SkyBoT processing: {e}"
                )
        else:
            log_messages.append(
                "WARNING: Could not determine field center for SkyBoT query"
            )
    except Exception as e:
        log_messages.append(f"ERROR: Error in SkyBoT processing: {str(e)}")

    log_messages.append("INFO: Querying AAVSO VSX for variable stars...")
    try:
        if field_center_ra is not None and field_center_dec is not None:
            Vizier.ROW_LIMIT = -1
            vizier_result = Vizier.query_region(
                SkyCoord(ra=field_center_ra, dec=field_center_dec, unit=u.deg),
                radius=field_width_arcmin * u.arcmin,
                catalog=["B/vsx/vsx"],
            )

            if (
                vizier_result
                and "B/vsx/vsx" in vizier_result.keys()
                and len(vizier_result["B/vsx/vsx"]) > 0
            ):
                vsx_table = vizier_result["B/vsx/vsx"]

                vsx_coords = SkyCoord(
                    ra=vsx_table["RAJ2000"], dec=vsx_table["DEJ2000"], unit=u.deg
                )

                # Filter valid coordinates for AAVSO matching
                valid_final_coords = enhanced_table[valid_coords_mask]

                if len(valid_final_coords) > 0:
                    source_coords = SkyCoord(
                        ra=valid_final_coords["ra"].values,
                        dec=valid_final_coords["dec"].values,
                        unit="deg",
                    )

                    idx, d2d, _ = source_coords.match_to_catalog_sky(vsx_coords)
                    matches = d2d <= (10 * u.arcsec)

                    enhanced_table["aavso_Name"] = None
                    enhanced_table["aavso_Type"] = None
                    enhanced_table["aavso_Period"] = None

                    # Map matches back to the original table indices
                    valid_indices = valid_final_coords.index

                    for i, (match, match_idx) in enumerate(zip(matches, idx)):
                        if match:
                            original_idx = valid_indices[i]
                            enhanced_table.loc[original_idx, "aavso_Name"] = vsx_table[
                                "Name"
                            ][match_idx]
                            enhanced_table.loc[original_idx, "aavso_Type"] = vsx_table[
                                "Type"
                            ][match_idx]
                            enhanced_table.loc[original_idx, "aavso_Period"] = (
                                vsx_table["Period"][match_idx]
                            )

                    log_messages.append(
                        f"INFO: Found {sum(matches)} variable stars in field."
                    )
                else:
                    log_messages.append(
                        "INFO: No valid coordinates available for AAVSO matching"
                    )
            else:
                log_messages.append("INFO: No variable stars found in the field.")
    except Exception as e:
        log_messages.append(f"ERROR: Error querying AAVSO VSX: {e}")

    log_messages.append("INFO: Querying Milliquas Catalog for quasars...")
    try:
        if field_center_ra is not None and field_center_dec is not None:
            # Set columns to retrieve from the quasar catalog
            v = Vizier(columns=["RAJ2000", "DEJ2000", "Name", "z", "Rmag"])
            v.ROW_LIMIT = -1  # No row limit

            # Query the VII/294 catalog around the field center
            result = v.query_region(
                SkyCoord(ra=field_center_ra, dec=field_center_dec, unit=(u.deg, u.deg)),
                width=field_width_arcmin * u.arcmin,
                catalog="VII/294",
            )

            if result and len(result) > 0:
                qso_table = result[0]

                # Convert to pandas DataFrame for easier matching
                qso_df = qso_table.to_pandas()
                qso_coords = SkyCoord(
                    ra=qso_df["RAJ2000"], dec=qso_df["DEJ2000"], unit=u.deg
                )

                # Filter valid coordinates for QSO matching
                valid_final_coords = enhanced_table[valid_coords_mask]

                if len(valid_final_coords) > 0:
                    # Create source coordinates for matching
                    source_coords = SkyCoord(
                        ra=valid_final_coords["ra"],
                        dec=valid_final_coords["dec"],
                        unit=u.deg,
                    )

                    # Perform cross-matching
                    idx, d2d, _ = source_coords.match_to_catalog_3d(qso_coords)
                    matches = d2d.arcsec < 10.0

                    # Add matched quasar information to the final table
                    enhanced_table["qso_name"] = None
                    enhanced_table["qso_redshift"] = None
                    enhanced_table["qso_Rmag"] = None

                    # Initialize catalog_matches column if it doesn't exist
                    if "catalog_matches" not in enhanced_table.columns:
                        enhanced_table["catalog_matches"] = ""

                    # Map matches back to the original table indices
                    valid_indices = valid_final_coords.index
                    matched_sources = np.where(matches)[0]
                    matched_qsos = idx[matches]

                    for i, qso_idx in zip(matched_sources, matched_qsos):
                        original_idx = valid_indices[i]
                        enhanced_table.loc[original_idx, "qso_name"] = qso_df.iloc[
                            qso_idx
                        ]["Name"]
                        enhanced_table.loc[original_idx, "qso_redshift"] = qso_df.iloc[
                            qso_idx
                        ]["z"]
                        enhanced_table.loc[original_idx, "qso_Rmag"] = qso_df.iloc[
                            qso_idx
                        ]["Rmag"]

                    # Update the catalog_matches column for matched quasars
                    has_qso = enhanced_table["qso_name"].notna()
                    enhanced_table.loc[has_qso, "catalog_matches"] += "QSO; "

                    log_messages.append(
                        f"INFO: Found {sum(has_qso)} quasars in field from Milliquas catalog."
                    )
                else:
                    log_messages.append(
                        "INFO: No valid coordinates available for QSO matching"
                    )
            else:
                log_messages.append(
                    "WARNING: No quasars found in field from Milliquas catalog."
                )
    except Exception as e:
        log_messages.append(f"ERROR: Error querying VizieR Milliquas: {str(e)}")

    log_messages.append("INFO: Querying 10 Parsec Catalog...")

    try:
        if field_center_ra is not None and field_center_dec is not None:
            # Prepare VizieR query
            v = Vizier(
                columns=[
                    "RAICRS",
                    "DEICRS",
                    "Name",
                    "ObjType",
                    "Gmag",
                    "GBPmag",
                    "GRPmag",
                ]
            )
            v.ROW_LIMIT = -1

            # Query the 10 Parsec catalog (J/A+A/650/A201/tablea1)
            result = v.query_region(
                SkyCoord(ra=field_center_ra, dec=field_center_dec, unit=(u.deg, u.deg)),
                width=field_width_arcmin * u.arcmin,
                catalog="J/A+A/650/A201/tablea1",
            )

            if result and len(result) > 0:
                pc10_table = result[0]

                # Convert to DataFrame
                pc10_df = pc10_table.to_pandas()

                # Catalog coordinates
                pc10_coords = SkyCoord(
                    ra=pc10_df["RAICRS"].astype(float),
                    dec=pc10_df["DEICRS"].astype(float),
                    unit=u.deg,
                )

                # Filter valid coordinates from enhanced_table
                valid_final_coords = enhanced_table[valid_coords_mask]

                if len(valid_final_coords) > 0:
                    # Field source coordinates
                    source_coords = SkyCoord(
                        ra=valid_final_coords["ra"].astype(float),
                        dec=valid_final_coords["dec"].astype(float),
                        unit=u.deg,
                    )

                    # Cross-match (sky distance)
                    idx, d2d, _ = source_coords.match_to_catalog_sky(pc10_coords)
                    matches = d2d.arcsec < 10.0

                    # Ensure destination columns exist
                    for col in [
                        "pc10_name",
                        "pc10_type",
                        "pc10_Gmag",
                        "pc10_GBPmag",
                        "pc10_GRPmag",
                    ]:
                        if col not in enhanced_table.columns:
                            enhanced_table[col] = None

                    if "catalog_matches" not in enhanced_table.columns:
                        enhanced_table["catalog_matches"] = ""

                    # Index mapping back to original table
                    valid_indices = valid_final_coords.index
                    matched_sources = np.where(matches)[0]
                    matched_objects = idx[matches]

                    # Fill matched rows
                    for i, cat_idx in zip(matched_sources, matched_objects):
                        original_idx = valid_indices[i]

                        enhanced_table.loc[original_idx, "pc10_name"] = pc10_df.iloc[
                            cat_idx
                        ]["Name"]
                        enhanced_table.loc[original_idx, "pc10_type"] = pc10_df.iloc[
                            cat_idx
                        ]["ObjType"]
                        enhanced_table.loc[original_idx, "pc10_Gmag"] = pc10_df.iloc[
                            cat_idx
                        ]["Gmag"]
                        enhanced_table.loc[original_idx, "pc10_GBPmag"] = pc10_df.iloc[
                            cat_idx
                        ]["GBPmag"]
                        enhanced_table.loc[original_idx, "pc10_GRPmag"] = pc10_df.iloc[
                            cat_idx
                        ]["GRPmag"]

                    # Update catalog match annotation
                    has_match = enhanced_table["pc10_name"].notna()
                    enhanced_table.loc[has_match, "catalog_matches"] = (
                        enhanced_table.loc[has_match, "catalog_matches"].astype(str)
                        + "pc10; "
                    )

                    log_messages.append(
                        f"INFO: Found {sum(has_match)} sources in field from 10 Parsec catalog."
                    )

                else:
                    log_messages.append(
                        "INFO: No valid coordinates available for 10 Parsec matching"
                    )

            else:
                log_messages.append("WARNING: No sources found from 10 Parsec catalog.")

    except Exception as e:
        log_messages.append(f"ERROR: Error querying VizieR 10 Parsec: {str(e)}")

    # Remove rows with snr_2.0 equal to 0, -1, or -2
    if "snr_2.0" in enhanced_table.columns:
        # coerce to numeric so None/invalid values become NaN
        snr_vals = pd.to_numeric(enhanced_table["snr_2.0"], errors="coerce")
        invalid_mask = snr_vals.isin([0, -1, -2])
        if invalid_mask.any():
            removed = int(invalid_mask.sum())
            enhanced_table = enhanced_table.loc[~invalid_mask].copy()
            log_messages.append(
                f"INFO: Removed {removed} sources with snr_2.0 in [0, -1, -2]"
            )

    # Reorganize columns to group related photometry columns together
    def get_column_order():
        """Generate the desired column order with photometry columns grouped together."""
        column_order = []
        
        # Start with basic identification and position columns
        basic_cols = ["xcenter", "ycenter", "ra", "dec", "catalog_source_id"]
        column_order.extend([col for col in basic_cols if col in enhanced_table.columns])
        
        # Add aperture photometry columns (magnitudes, errors, SNR) grouped together
        aperture_cols = []
        # Only select aperture_mag columns that don't contain "err" to avoid including error columns
        aperture_mag_cols = [col for col in enhanced_table.columns if col.startswith("aperture_mag_") and "err" not in col]
        
        # Sort aperture columns by radius for consistent ordering
        aperture_mag_cols.sort()
        
        # Group aperture magnitudes with their corresponding errors and SNR
        for mag_col in aperture_mag_cols:
            aperture_cols.append(mag_col)
            # Find corresponding error column
            radius_suffix = mag_col.replace("aperture_mag_", "")
            err_col = f"aperture_mag_err_{radius_suffix}"
            if err_col in enhanced_table.columns:
                aperture_cols.append(err_col)
            # Find corresponding SNR column - handle both underscore and dot formats
            snr_col_underscore = f"snr{radius_suffix}"
            snr_col_dot = f"snr{radius_suffix.replace('_', '.')}"
            snr_col = None
            if snr_col_underscore in enhanced_table.columns:
                snr_col = snr_col_underscore
            elif snr_col_dot in enhanced_table.columns:
                snr_col = snr_col_dot
            if snr_col is not None:
                aperture_cols.append(snr_col)
        
        column_order.extend(aperture_cols)
        
        # Add PSF photometry columns (magnitudes, errors, SNR) grouped together
        psf_cols = []
        if "psf_mag" in enhanced_table.columns:
            psf_cols.append("psf_mag")
        if "psf_mag_err" in enhanced_table.columns:
            psf_cols.append("psf_mag_err")
        if "snr" in enhanced_table.columns:
            psf_cols.append("snr")
        
        column_order.extend(psf_cols)
        
        # Add calibration-related columns
        calib_cols = ["calib_star", "zero_point", "zero_point_std", "airmass"]
        column_order.extend([col for col in calib_cols if col in enhanced_table.columns])
        
        # Add catalog cross-match columns
        catalog_cols = [
            "astrocolibri_name", "astrocolibri_type", "astrocolibri_classification",
            "simbad_main_id", "simbad_otype", "simbad_B", "simbad_V",
            "skybot_NAME", "skybot_OBJECT_TYPE", "skybot_MAGV", "skybot_epoch",
            "aavso_Name", "aavso_Type", "aavso_Period",
            "qso_name", "qso_redshift", "qso_Rmag",
            "pc10_name", "pc10_type", "pc10_Gmag", "pc10_GBPmag", "pc10_GRPmag",
            "catalog_matches"
        ]
        column_order.extend([col for col in catalog_cols if col in enhanced_table.columns])
        
        # Add GAIA-related columns
        gaia_cols = [col for col in enhanced_table.columns if col.startswith("gaia_")]
        column_order.extend(gaia_cols)
        
        # Add any remaining columns not explicitly ordered
        remaining_cols = [col for col in enhanced_table.columns if col not in column_order]
        column_order.extend(remaining_cols)
        
        return column_order
    
    # Apply the new column order
    try:
        column_order = get_column_order()
        enhanced_table = enhanced_table[column_order]
        log_messages.append("INFO: Reorganized table columns for better readability")
    except Exception as e:
        log_messages.append(f"WARNING: Could not reorganize columns: {e}")

    # Round numeric columns to 3 decimal places for better readability
    def round_numeric_columns():
        """Round magnitude, error, and SNR columns to 3 decimal places."""
        columns_rounded = 0
        
        # Round all aperture magnitude columns
        aperture_mag_cols = [col for col in enhanced_table.columns if col.startswith("aperture_mag_") and "err" not in col]
        for col in aperture_mag_cols:
            if col in enhanced_table.columns and pd.api.types.is_numeric_dtype(enhanced_table[col]):
                enhanced_table[col] = enhanced_table[col].round(3)
                columns_rounded += 1
        
        # Round all aperture magnitude error columns
        aperture_err_cols = [col for col in enhanced_table.columns if col.startswith("aperture_mag_err_")]
        for col in aperture_err_cols:
            if col in enhanced_table.columns and pd.api.types.is_numeric_dtype(enhanced_table[col]):
                enhanced_table[col] = enhanced_table[col].round(3)
                columns_rounded += 1
        
        # Round PSF magnitude and error columns
        psf_mag_cols = ["psf_mag", "psf_mag_err"]
        for col in psf_mag_cols:
            if col in enhanced_table.columns and pd.api.types.is_numeric_dtype(enhanced_table[col]):
                enhanced_table[col] = enhanced_table[col].round(3)
                columns_rounded += 1
        
        # Round all SNR columns
        snr_cols = [col for col in enhanced_table.columns if col.startswith("snr")]
        for col in snr_cols:
            if col in enhanced_table.columns and pd.api.types.is_numeric_dtype(enhanced_table[col]):
                enhanced_table[col] = enhanced_table[col].round(3)
                columns_rounded += 1
        
        return columns_rounded
    
    try:
        rounded_count = round_numeric_columns()
        if rounded_count > 0:
            log_messages.append(f"INFO: Rounded {rounded_count} numeric columns to 3 decimal places")
    except Exception as e:
        log_messages.append(f"WARNING: Could not round numeric columns: {e}")

    return enhanced_table, log_messages
