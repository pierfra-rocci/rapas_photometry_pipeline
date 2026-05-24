import os
import streamlit as st

import numpy as np
import matplotlib.pyplot as plt

from astropy.table import Table
from astropy.nddata import NDData
from astropy.io import fits
from astropy.visualization import simple_norm

from photutils.psf import EPSFBuilder, extract_stars, PSFPhotometry
from photutils.psf import SourceGrouper, EPSFStars

from src.tools_pipeline import FIGURE_SIZES
from src.utils import ensure_output_directory

from typing import Any, Optional, Tuple


def perform_psf_photometry(
    img: np.ndarray,
    photo_table: Table,
    fwhm: float,
    daostarfind: Any,
    mask: Optional[np.ndarray] = None,
    error=None,
    max_sources_for_psf: int = 700,
    max_stars_for_epsf: int = 350,
) -> Tuple[Optional[Table], Optional[Any]]:
    """
    Perform PSF (Point Spread Function) photometry using an empirically-constructed PSF model.

    This function builds an empirical PSF (EPSF) from bright stars in the image
    and then uses this model to perform PSF photometry on all detected sources.
    If EPSF building fails, returns None.

    Parameters
    ----------
    img : numpy.ndarray
        Image with sky background subtracted
    photo_table : astropy.table.Table
        Table containing source positions
    fwhm : float
        Full Width at Half Maximum in pixels, used to define extraction and fitting sizes
    daostarfind : callable
        Star detection function used as "finder" in PSF photometry
    mask : numpy.ndarray, optional
        Mask to exclude certain image areas from analysis
    error : numpy.ndarray, optional
        Error array for the image
    max_sources_for_psf : int, optional
        Maximum number of sources to extract for PSF model construction (default: 500)
        This limits the initial extraction before quality filtering
    max_stars_for_epsf : int, optional
        Maximum number of stars to use for final EPSF building (default: 200)
        Only the brightest stars are selected if more are available

    Returns
    -------
    Tuple[Optional[astropy.table.Table], Optional[photutils.psf.EPSFModel]]
        - phot_epsf_result : Table containing PSF photometry results, or None if PSF building failed
        - epsf : The fitted EPSF model, or None if PSF building failed
    """
    # Initial validation
    try:
        if img is None or not isinstance(img, np.ndarray) or img.size == 0:
            raise ValueError(
                "Invalid image data provided. Ensure the image is a non-empty numpy array."
            )

        if mask is not None and (
            not isinstance(mask, np.ndarray) or mask.shape != img.shape
        ):
            raise ValueError(
                "Invalid mask provided. Ensure the mask is a numpy array with the same shape as the image."
            )

        if not callable(daostarfind):
            raise ValueError(
                "The 'finder' parameter must be a callable star finder, such as DAOStarFinder."
            )

        # build NDData including mask/uncertainty when available (improves extract_stars)
        try:
            from astropy.nddata import StdDevUncertainty

            nd_unc = StdDevUncertainty(error) if (error is not None) else None
        except Exception:
            nd_unc = None

        nddata = NDData(
            data=img, mask=mask if mask is not None else None, uncertainty=nd_unc
        )
    except Exception as e:
        st.error(f"Error in initial validation: {e}")
        raise

    # Filter photo_table to select only the best stars for PSF model
    # IMPORTANT: Keep original photo_table for final photometry on ALL sources
    photo_table_all_sources = photo_table  # Store original table

    try:
        st.write("Filtering stars for PSF model construction...")
        st.write(f"Starting with {len(photo_table)} sources")

        # Required columns (raise helpful error if missing)
        if "flux" not in photo_table.colnames:
            raise ValueError("photo_table missing required column 'flux'")
        # Handle photutils 3.0 column rename: canonical name is 'x_centroid' (with
        # underscore), but old code expects 'xcentroid'. Use whichever is present.
        _xcol = "x_centroid" if "x_centroid" in photo_table.colnames else "xcentroid"
        _ycol = "y_centroid" if "y_centroid" in photo_table.colnames else "ycentroid"
        if _xcol not in photo_table.colnames or _ycol not in photo_table.colnames:
            raise ValueError(
                "photo_table must contain 'xcentroid'/'x_centroid' and "
                "'ycentroid'/'y_centroid' columns"
            )

        # ========== EARLY FILTERING STEP ==========
        # Pre-select top brightest sources to avoid extracting thousands of stars
        # This is ONLY for PSF model construction, not for final photometry

        # Get initial arrays from full table
        flux_initial = np.asarray(photo_table["flux"])
        xcentroid_initial = np.asarray(photo_table[_xcol])
        ycentroid_initial = np.asarray(photo_table[_ycol])

        # If we have too many sources, keep only the brightest ones before detailed filtering
        photo_table_for_psf = photo_table  # Start with full table
        if len(photo_table) > max_sources_for_psf:
            st.write(
                f"Too many sources ({len(photo_table)}). Pre-selecting top {max_sources_for_psf} brightest stars for PSF model construction..."
            )

            # Create a simple score based on flux (higher is better)
            # Only consider sources with finite flux and valid positions
            valid_for_preselect = (
                np.isfinite(flux_initial)
                & np.isfinite(xcentroid_initial)
                & np.isfinite(ycentroid_initial)
            )

            if np.sum(valid_for_preselect) == 0:
                raise ValueError("No sources with valid flux and positions found")

            # Rank by flux (brightest first)
            flux_ranks = np.argsort(flux_initial[valid_for_preselect])[::-1]

            # Create index mapping back to original table
            valid_indices = np.where(valid_for_preselect)[0]
            top_indices = valid_indices[flux_ranks[:max_sources_for_psf]]

            # Pre-filter ONLY for PSF construction (not for final photometry)
            photo_table_for_psf = photo_table[top_indices]

            st.write(
                f"Pre-selected {len(photo_table_for_psf)} brightest sources for PSF model construction"
            )
            st.write(
                f"   (Final photometry will be performed on all {len(photo_table_all_sources)} original sources)"
            )

        # ---------- START REPLACEMENT BLOCK ----------
        # NOW work with photo_table_for_psf for all subsequent operations
        # Ensure we always have an 'orig_index' mapping so we can map back to the original full table.

        if len(photo_table) > max_sources_for_psf:
            # top_indices was computed above during pre-selection
            # make a copy so adding columns does not mutate the original user's table
            photo_table_for_psf = photo_table_for_psf.copy()
            if "orig_index" not in photo_table_for_psf.colnames:
                photo_table_for_psf["orig_index"] = top_indices
        else:
            # no preselection: create an explicit mapping 0..N-1
            photo_table_for_psf = photo_table_for_psf.copy()
            if "orig_index" not in photo_table_for_psf.colnames:
                photo_table_for_psf["orig_index"] = np.arange(len(photo_table_for_psf))

        n_sources = len(photo_table_for_psf)

        def _col_arr(tbl, name, default=None):
            if name in tbl.colnames:
                return np.asarray(tbl[name])
            if default is not None:
                return (
                    np.asarray(default)
                    if np.shape(default) == (len(tbl),)
                    else np.full(len(tbl), default)
                )
            return np.full(len(tbl), np.nan)

        # Get arrays safely from photo_table_for_psf
        flux = _col_arr(photo_table_for_psf, "flux")
        roundness1 = _col_arr(photo_table_for_psf, "roundness1", np.nan)
        sharpness = _col_arr(photo_table_for_psf, "sharpness", np.nan)
        xcentroid = _col_arr(photo_table_for_psf, _xcol)
        ycentroid = _col_arr(photo_table_for_psf, _ycol)

        # Additional attributes for improved filtering
        # Try to get source properties that indicate size and shape
        fwhm_sources = _col_arr(photo_table_for_psf, "fwhm", np.nan)  # if available
        # Ellipticity can be inferred from roundness or derived from semi-major/minor axes if available
        semimajor = _col_arr(
            photo_table_for_psf, "a", np.nan
        )  # semi-major axis if available
        semiminor = _col_arr(
            photo_table_for_psf, "b", np.nan
        )  # semi-minor axis if available
        # Peak value for saturation detection
        peak = _col_arr(photo_table_for_psf, "peak", np.nan)  # peak flux if available

        # Get flux statistics with NaN handling (on potentially pre-filtered data)
        flux_finite = flux[np.isfinite(flux)]
        if len(flux_finite) == 0:
            raise ValueError("No sources with finite flux values found")

        flux_median = np.median(flux_finite)

        # ========== S/N FILTERING (NEW - CRITICAL FOR PSF QUALITY) ==========
        # Compute S/N for each source if error data is available
        # S/N = flux / flux_error or approximate from flux and background noise
        snr = _col_arr(photo_table_for_psf, "snr", np.nan)  # if available directly
        flux_err = _col_arr(photo_table_for_psf, "flux_err", np.nan)  # flux uncertainty

        # If S/N not directly available, compute from flux and error
        valid_snr = np.isfinite(snr)
        snr_is_approximated = False  # Track if we're using Poisson approximation
        if not np.any(valid_snr):
            # Try to compute S/N from flux and flux_err
            valid_flux_err = np.isfinite(flux_err) & (flux_err > 0)
            if np.any(valid_flux_err):
                snr = np.full(n_sources, np.nan)
                snr[valid_flux_err] = flux[valid_flux_err] / flux_err[valid_flux_err]
                valid_snr = np.isfinite(snr)
                st.write(
                    f"  ⓘ S/N computed from flux/flux_err for {np.sum(valid_snr)} sources"
                )
            else:
                # Approximate S/N from flux assuming Poisson noise: S/N ≈ sqrt(flux)
                # This is a rough approximation for photon-limited regime
                valid_positive_flux = flux > 0
                snr = np.full(n_sources, np.nan)
                snr[valid_positive_flux] = np.sqrt(flux[valid_positive_flux])
                valid_snr = np.isfinite(snr)
                snr_is_approximated = True
                st.write(
                    f"  ⓘ S/N approximated as √flux for {np.sum(valid_snr)} sources (Poisson assumption)"
                )

        # S/N threshold: ADAPTIVE based on actual S/N distribution
        # If S/N is approximated (Poisson), use percentile-based threshold instead of fixed value
        snr_finite = snr[valid_snr]
        if len(snr_finite) > 0:
            snr_median = np.median(snr_finite)

            if snr_is_approximated or snr_median < 10:
                # Use adaptive threshold: select top 50% of S/N values
                snr_threshold = max(snr_median, np.percentile(snr_finite, 50))
                st.write(
                    f"  ⓘ Using adaptive S/N threshold (median S/N={snr_median:.1f})"
                )
            else:
                # Use fixed threshold for proper S/N values
                snr_threshold = 20.0
        else:
            snr_threshold = 0.0  # No S/N filtering if no valid values

        snr_criteria = np.zeros(n_sources, dtype=bool)
        if snr_threshold > 0:
            snr_criteria[valid_snr] = snr[valid_snr] >= snr_threshold
        else:
            snr_criteria = np.ones(n_sources, dtype=bool)  # Skip S/N filtering
        # n_snr_pass = np.sum(snr_criteria)
        # st.write(f"  ✓ S/N ≥ {snr_threshold:.1f}: {n_snr_pass} high-S/N sources")

        # Define flux filtering criteria: keep BRIGHT, unsaturated stars
        # Focus on top percentile of flux distribution (bright stars have better S/N)
        # Use percentiles instead of median ± σ to ensure we get the brightest stars
        flux_25 = np.percentile(flux_finite, 25)  # Don't go below 25th percentile
        flux_90 = np.percentile(flux_finite, 90)  # Upper limit to avoid saturation
        flux_min = max(flux_25, flux_median)  # At least median brightness
        flux_max = flux_90  # Stay below 90th percentile (likely saturated)

        # st.write(
        #     f"Target flux range: {flux_min:.1f} "
        #     f"→ {flux_max:.1f} (25th-90th percentile, bright stars)"
        # )

        # Create individual boolean masks with explicit NaN handling
        valid_flux = np.isfinite(flux)
        valid_roundness = np.isfinite(roundness1)
        valid_sharpness = np.isfinite(sharpness)
        valid_xcentroid = np.isfinite(xcentroid)
        valid_ycentroid = np.isfinite(ycentroid)

        # Combine validity checks
        valid_all = (
            valid_flux
            & valid_roundness
            & valid_sharpness
            & valid_xcentroid
            & valid_ycentroid
        )

        if not np.any(valid_all):
            # relax: accept stars where position and flux are valid at least
            valid_all = valid_flux & valid_xcentroid & valid_ycentroid
            if not np.any(valid_all):
                raise ValueError("No sources with required valid parameters found")

        # ========== FLUX FILTERING ==========
        flux_criteria = np.zeros(n_sources, dtype=bool)
        flux_criteria[valid_flux] = (flux[valid_flux] >= flux_min) & (
            flux[valid_flux] <= flux_max
        )
        # n_flux_pass = np.sum(flux_criteria)
        # st.write(f"  ✓ Flux range: {n_flux_pass} sources")

        # ========== SATURATION FILTERING (STRICTER) ==========
        # Reject sources with peak values close to saturation
        # Use 85th percentile for stricter saturation rejection
        saturation_criteria = np.ones(n_sources, dtype=bool)
        valid_peak = np.isfinite(peak)
        if np.any(valid_peak):
            peak_values = peak[valid_peak]
            # Use 85th percentile as a STRICT saturation limit (was 95th)
            peak_85 = np.percentile(peak_values, 85)
            # Also check against image maximum (typical CCD saturation ~65535 for 16-bit)
            img_max = np.nanmax(img)
            saturation_limit = min(
                peak_85 * 0.95, img_max * 0.85
            )  # 5% below 85th or 85% of max
            saturation_criteria[valid_peak] = peak[valid_peak] < saturation_limit
            # n_sat_pass = np.sum(saturation_criteria)
            # st.write(
            #     f"  ✓ Saturation check (strict): {n_sat_pass} "
            #     f"unsaturated sources (limit: {saturation_limit:.0f}, img_max: {img_max:.0f})"
            # )
        else:
            # No peak data available; use image maximum as fallback
            img_max = np.nanmax(img)
            saturation_limit = img_max * 0.80  # 80% of image max
            st.write(
                f"  ⓘ No peak flux in catalog; using 80% of image max ({saturation_limit:.0f}) as limit"
            )

        # ========== SIZE/FWHM FILTERING ==========
        # Select stars with FWHM close to the expected FWHM (point-like, not extended)
        # Allow a reasonable range around the measured FWHM
        size_criteria = np.ones(n_sources, dtype=bool)
        valid_fwhm = np.isfinite(fwhm_sources)
        if np.any(valid_fwhm):
            # Use the fwhm parameter passed to the function
            # Accept sources with FWHM within 0.8–1.4× expected FWHM
            # (rejects extended objects and under-sampled stars)
            size_min = fwhm * 0.75
            size_max = fwhm * 1.5
            size_criteria[valid_fwhm] = (fwhm_sources[valid_fwhm] >= size_min) & (
                fwhm_sources[valid_fwhm] <= size_max
            )
            # n_size_pass = np.sum(size_criteria)
            # st.write(
            #     f"  ✓ Size/FWHM filter: {n_size_pass} "
            #     f"point-like sources ({size_min:.2f}–{size_max:.2f} px)"
            # )
        else:
            st.write("  ⓘ size filtering skipped")

        # ========== SHAPE/ELLIPTICITY FILTERING ==========
        # Reject blends and optical defects using roundness and axis ratio
        roundness_criteria = np.zeros(n_sources, dtype=bool)
        roundness_criteria[valid_roundness] = (
            np.abs(roundness1[valid_roundness]) < 0.4
        )  # Relaxed from 0.25
        # n_roundness_pass = np.sum(roundness_criteria)
        # st.write(f"  ✓ Roundness (|r₁| < 0.4): {n_roundness_pass} sources")

        # Add axis-ratio check if semi-major/minor axes available
        ellipticity_criteria = np.ones(n_sources, dtype=bool)
        valid_axes = np.isfinite(semimajor) & np.isfinite(semiminor)
        if np.any(valid_axes):
            # Axis ratio b/a; keep near-circular (ratio ≈ 1)
            axis_ratio = semiminor[valid_axes] / semimajor[valid_axes]
            # Ellipticity: 0 = circular, ~1 = very elongated
            ellipticity = 1.0 - axis_ratio
            ellipticity_criteria[valid_axes] = (
                ellipticity < 0.3
            )  # Reject highly elongated objects
            # n_ellipticity_pass = np.sum(ellipticity_criteria)
            # st.write(f"  ✓ Ellipticity (ε < 0.3): {n_ellipticity_pass} sources")

        # Sharpness filtering DISABLED - too restrictive
        # sharpness_criteria = np.zeros(n_sources, dtype=bool)
        # sharpness_criteria[valid_sharpness] = sharpness[valid_sharpness] > 0.45
        # n_sharpness_pass = np.sum(sharpness_criteria)
        # st.write(f"  ✓ Sharpness (sharp > 0.45): {n_sharpness_pass} sources")
        sharpness_criteria = np.ones(n_sources, dtype=bool)  # Accept all (disabled)

        # ========== CROWDING/ISOLATION FILTERING ==========
        # Reject stars with bright neighbors within isolation radius
        crowding_criteria = np.ones(n_sources, dtype=bool)
        neighbor_radius = max(4.0 * fwhm, 40.0)  # At least 4×FWHM or 40 pixels
        # mag; reject if bright neighbor within this limit
        magnitude_threshold = 3.0

        # Convert flux to magnitude for neighbor comparison
        # (simple: -2.5 log10(flux))
        # Avoid log of non-positive values
        flux_safe = np.where(flux > 0, flux, 1.0)
        magnitude = -2.5 * np.log10(flux_safe)

        for i in range(n_sources):
            if not (valid_xcentroid[i] and valid_ycentroid[i] and valid_flux[i]):
                crowding_criteria[i] = False
                continue

            # Find all neighbors within the search radius
            dx = xcentroid - xcentroid[i]
            dy = ycentroid - ycentroid[i]
            dist = np.sqrt(dx**2 + dy**2)

            # Exclude self (dist=0) and find neighbors
            neighbor_mask = (dist > 0.5) & (dist < neighbor_radius)

            if np.any(neighbor_mask):
                neighbor_mags = magnitude[neighbor_mask]

                # Count bright neighbors (within magnitude_threshold)
                bright_neighbors = np.sum(
                    neighbor_mags < magnitude[i] + magnitude_threshold
                )

                if bright_neighbors > 0:
                    crowding_criteria[i] = False
                    # Optionally log severely crowded sources
                    if bright_neighbors > 2:
                        pass  # Too crowded, reject

        # n_crowding_pass = np.sum(crowding_criteria)
        # st.write(
        #     f"  ✓ Isolation (no bright neighbors within "
        #     f"{neighbor_radius}px, <{magnitude_threshold:.1f}Δmag): "
        #     f"{n_crowding_pass} sources"
        # )

        # ========== EDGE CRITERIA ==========
        # Keep away from edges where PSF is affected
        edge_criteria = np.zeros(n_sources, dtype=bool)
        valid_coords = valid_xcentroid & valid_ycentroid
        edge_buffer = max(3 * fwhm, 20)  # At least 3×FWHM or 20 px
        edge_criteria[valid_coords] = (
            (xcentroid[valid_coords] > edge_buffer)
            & (xcentroid[valid_coords] < img.shape[1] - edge_buffer)
            & (ycentroid[valid_coords] > edge_buffer)
            & (ycentroid[valid_coords] < img.shape[0] - edge_buffer)
        )
        # n_edge_pass = np.sum(edge_criteria)
        # st.write(f"  ✓ Not near edges (>{edge_buffer:.0f}px): {n_edge_pass} sources")

        # ========== LOCAL BACKGROUND FLATNESS CHECK ==========
        # Ensure stars have flat local background (no gradients)
        # This helps avoid PSF contamination from nebulosity or gradients
        background_criteria = np.ones(n_sources, dtype=bool)
        local_box_size = int(max(5 * fwhm, 25))  # Box size for local background check
        half_box = local_box_size // 2

        n_background_checked = 0
        for i in range(n_sources):
            if not (valid_xcentroid[i] and valid_ycentroid[i]):
                continue

            xi, yi = int(xcentroid[i]), int(ycentroid[i])

            # Define annulus around the star (exclude the star itself)
            inner_radius = int(2.5 * fwhm)  # Inside this: the star
            outer_radius = half_box

            # Check bounds
            if (
                xi - outer_radius < 0
                or xi + outer_radius >= img.shape[1]
                or yi - outer_radius < 0
                or yi + outer_radius >= img.shape[0]
            ):
                continue

            # Extract local region
            local_region = img[
                yi - outer_radius : yi + outer_radius + 1,
                xi - outer_radius : xi + outer_radius + 1,
            ]

            # Create annular mask (background region)
            y_local, x_local = np.ogrid[
                -outer_radius : outer_radius + 1, -outer_radius : outer_radius + 1
            ]
            dist_from_center = np.sqrt(x_local**2 + y_local**2)
            annular_mask = (dist_from_center >= inner_radius) & (
                dist_from_center <= outer_radius
            )

            if mask is not None:
                # Also exclude masked pixels
                local_mask = mask[
                    yi - outer_radius : yi + outer_radius + 1,
                    xi - outer_radius : xi + outer_radius + 1,
                ]
                annular_mask = annular_mask & ~local_mask

            if np.sum(annular_mask) < 20:  # Need enough pixels for statistics
                continue

            background_values = local_region[annular_mask]
            background_values = background_values[np.isfinite(background_values)]

            if len(background_values) < 10:
                continue

            n_background_checked += 1

            # Check background flatness: std should be small relative to median
            bkg_median = np.median(background_values)
            bkg_std = np.std(background_values)

            # Reject if background is too variable (> 10% of median or > 3σ above typical)
            # Also check for gradient by comparing quadrants
            if bkg_median > 0:
                bkg_variation = bkg_std / bkg_median
                if bkg_variation > 0.15:  # More than 15% variation
                    background_criteria[i] = False

        # n_bkg_pass = np.sum(background_criteria)
        # st.write(
        #     f"  ✓ Flat local background: {n_bkg_pass}/{n_background_checked} "
        #     f"sources with uniform surroundings"
        # )

        # Combine all criteria with validity checks (including S/N and background)
        good_stars_mask = (
            valid_all
            & snr_criteria  # NEW: S/N filtering
            & flux_criteria
            & saturation_criteria
            & size_criteria
            & roundness_criteria
            & background_criteria  # NEW: local background flatness
            & ellipticity_criteria
            & sharpness_criteria
            & crowding_criteria
            & edge_criteria
        )

        # ===== APPLY THE MASK =====
        filtered_photo_table = photo_table_for_psf[good_stars_mask]
        # st.write(f"Flux range: {flux_min:.1f} -> {flux_max:.1f}")
        # st.write(f"Stars after quality filtering: {len(filtered_photo_table)}")

        if len(filtered_photo_table) >= 5:
            try:
                x_vals = np.asarray(filtered_photo_table[_xcol])
                y_vals = np.asarray(filtered_photo_table[_ycol])
                x_span = float(np.nanmax(x_vals) - np.nanmin(x_vals))
                y_span = float(np.nanmax(y_vals) - np.nanmin(y_vals))
                field_width = float(img.shape[1])
                field_height = float(img.shape[0])
                coverage_x = x_span / field_width if field_width > 0 else 0.0
                coverage_y = y_span / field_height if field_height > 0 else 0.0
                # st.write(
                #     "Spatial coverage (normalized widths): "
                #     f"X={coverage_x:.2f}, Y={coverage_y:.2f}"
                # )
                coverage_threshold = 0.35
                if coverage_x < coverage_threshold or coverage_y < coverage_threshold:
                    st.warning(
                        (
                            "Les étoiles sélectionnées couvrent une zone "
                            "restreinte du champ. Envisagez d'ajouter des "
                            "étoiles dans d'autres régions."
                        )
                    )
            except Exception as coverage_error:
                st.warning(
                    f"Impossible d'évaluer la couverture spatiale: {coverage_error}"
                )

        # Save indices mapping to original full table
        orig_indices_preselection = np.asarray(photo_table_for_psf["orig_index"])
        orig_indices_filtered = orig_indices_preselection[good_stars_mask]

        st.session_state["psf_preselected_indices"] = orig_indices_preselection
        st.session_state["psf_filtered_indices"] = orig_indices_filtered

        # Check if we have enough stars for HIGH-QUALITY PSF (target: 100+)
        min_stars_optimal = 100
        min_stars_acceptable = 15  # Minimum required - below this, PSF is not computed

        if len(filtered_photo_table) >= min_stars_optimal:
            st.success(
                f"✓ {len(filtered_photo_table)} high-quality PSF stars selected "
                f"(target: ≥{min_stars_optimal})"
            )
        elif len(filtered_photo_table) >= min_stars_acceptable:
            st.warning(
                f"⚠ {len(filtered_photo_table)} PSF stars (acceptable but <{min_stars_optimal}). "
                f"PSF model may have reduced accuracy."
            )
        else:
            # Less than 15 stars: do not compute PSF
            st.error(
                f"❌ Only {len(filtered_photo_table)} PSF stars available (minimum: {min_stars_acceptable}). "
                f"PSF photometry will NOT be performed. Consider using aperture photometry only."
            )
            return None, None
        # ---------- END REPLACEMENT BLOCK ----------

    except Exception as e:
        st.error(f"Error filtering stars for PSF model: {e}")
        raise

    try:
        stars_table = Table()
        # extract_stars expects 'x' and 'y' column names
        stars_table["x"] = filtered_photo_table[_xcol]
        stars_table["y"] = filtered_photo_table[_ycol]
    except Exception as e:
        st.error(f"Error preparing star positions table: {e}")
        raise

    try:
        # Ensure fit_shape accommodates 1.3*FWHM aperture radius
        fit_shape = int(3 * fwhm)
        if fit_shape % 2 == 0:
            fit_shape += 1
        aperture_radius = fit_shape / 2.0
        st.write(
            f"Fitting shape: {fit_shape} pixels "
            f"(aperture radius: {aperture_radius:.2f} px = "
            f"{aperture_radius / fwhm:.2f} × FWHM)."
        )
    except Exception as e:
        st.error(f"Error calculating fitting shape: {e}")
        raise

    try:
        stars = extract_stars(nddata, stars_table, size=fit_shape)

        if isinstance(stars, list):
            if len(stars) == 0:
                raise ValueError(
                    "No stars extracted for PSF model. Check your selection criteria."
                )

        n_stars = len(stars)
        st.success(f"{n_stars} stars extracted for PSF model.")

        # basic inspection of cutouts for NaN/all-zero
        if hasattr(stars, "data") and stars.data is not None:
            if isinstance(stars.data, list) and len(stars.data) > 0:
                has_nan = any(np.isnan(star_data).any() for star_data in stars.data)
            else:
                st.write("Stars data is empty or not a list")
        else:
            st.write("Stars object has no data attribute")

        if n_stars == 0:
            raise ValueError(
                "No stars extracted for PSF model. Check your selection criteria."
            )
    except Exception as e:
        st.error(f"Error extracting stars: {e}")
        raise

    try:
        # Remove stars with NaN or all-zero data
        mask_valid = []
        if hasattr(stars, "data") and isinstance(stars.data, list):
            for star_data in stars.data:
                if np.isnan(star_data).any():
                    mask_valid.append(False)
                elif np.all(star_data == 0):
                    mask_valid.append(False)
                else:
                    mask_valid.append(True)
        else:
            try:
                for i in range(len(stars)):
                    star = stars[i]
                    if np.isnan(star.data).any():
                        mask_valid.append(False)
                    elif np.all(star.data == 0):
                        mask_valid.append(False)
                    else:
                        mask_valid.append(True)
            except Exception as iter_error:
                st.warning(
                    f"Could not iterate through stars for validation: {iter_error}"
                )
                mask_valid = [True] * len(stars)

        mask_valid = np.array(mask_valid)

        # Only filter if we have any invalid stars
        if not np.all(mask_valid):
            filtered_stars = stars[mask_valid]
            if not isinstance(filtered_stars, EPSFStars):
                filtered_stars = EPSFStars(list(filtered_stars))
            stars = filtered_stars
            n_stars = len(stars)
            st.write(
                f"{n_stars} valid stars remain for PSF model after filtering invalid data."
            )
        else:
            st.write(f"All {len(stars)} stars are valid for PSF model.")

        if len(stars) == 0:
            raise ValueError("No valid stars for PSF model after filtering.")
    except Exception as e:
        st.error(f"Error filtering stars for PSF model: {e}")
        raise

    # Build EPSF model with Gaussian fallback
    try:
        # Filter out invalid Star objects and select brightest for EPSFBuilder
        valid_stars = []
        try:
            for star in stars:  # stars is already an EPSFStars object
                if hasattr(star, "data") and star.data is not None:
                    arr = np.asarray(star.data)
                    if arr.size == 0 or np.isnan(arr).any() or np.all(arr == 0):
                        continue
                    valid_stars.append(star)
        except Exception as e:
            st.warning(f"Error during initial filtering of stars for ePSF: {e}")
            valid_stars = []

        n_valid = len(valid_stars)
        st.write(f"Using {n_valid} valid Star objects for ePSF construction")

        if n_valid < 5:
            raise ValueError("Too few valid Star objects for ePSF building")

        # If there are a lot of stars, pick the brightest (by peak)
        if n_valid > max_stars_for_epsf:
            st.write(
                f"Selecting top {max_stars_for_epsf} brightest stars from {n_valid} valid stars..."
            )
            peaks = np.array([s.data.max() for s in valid_stars])
            top_idx = np.argsort(peaks)[-max_stars_for_epsf:][::-1]
            stars_for_builder = EPSFStars([valid_stars[i] for i in top_idx])
            st.write(
                f"✓ Selected {len(stars_for_builder)} brightest stars for ePSF construction."
            )
        else:
            stars_for_builder = EPSFStars(valid_stars)

        # Try EPSFBuilder with retries using tuned parameters
        epsf = None
        epsf_data = None

        recentering_box = max(5, int(np.ceil(2.5 * fwhm)))
        if recentering_box % 2 == 0:
            recentering_box += 1
        center_accuracy = max(0.02, min(0.1, 0.25 / max(float(fwhm), 1.0)))

        builder_attempts = [dict(oversampling=2, maxiters=5)]
        if n_valid >= 150:
            builder_attempts.append(dict(oversampling=4, maxiters=5))
        else:
            st.write("Essai oversampling 4 ignoré (échantillon < 150 étoiles).")

        for params in builder_attempts:
            try:
                oversamp = int(params["oversampling"])
                maxiters = int(params["maxiters"])
                st.write(
                    f"Essai EPSFBuilder: oversampling={oversamp}, maxiters={maxiters}"
                )
                epsf_builder = EPSFBuilder(
                    oversampling=oversamp,
                    maxiters=maxiters,
                    recentering_boxsize=recentering_box,
                    center_accuracy=center_accuracy,
                    progress_bar=False,
                )
                epsf, _ = epsf_builder(stars_for_builder)
                if epsf is not None:
                    st.write(f"EPSFBuilder réussi (oversampling={oversamp})")
                    epsf_data = np.asarray(epsf.data)
                    break
            except Exception as build_error:
                st.warning(f"Échec EPSFBuilder oversampling={oversamp}: {build_error}")
                epsf = None

        # Check if EPSFBuilder succeeded
        if epsf is None or (
            hasattr(epsf, "data")
            and (epsf.data is None or np.asarray(epsf.data).size == 0)
        ):
            st.warning(
                "⚠️ EPSFBuilder failed to create a valid PSF model. Continuing without PSF photometry."
            )
            return None, None

        epsf_data = np.asarray(epsf.data)

        # Validate epsf_data
        if epsf_data is None or epsf_data.size == 0:
            st.warning(
                "⚠️ ePSF data is invalid or empty. Continuing without PSF photometry."
            )
            return None, None

        if np.isnan(epsf_data).any():
            st.warning(
                "⚠️ ePSF data contains NaN values. Continuing without PSF photometry."
            )
            return None, None

        st.write(f"Shape of PSF data: {epsf_data.shape}")
        st.session_state["epsf_model"] = epsf
        st.session_state["used_gaussian_fallback"] = False

    except Exception as e:
        st.warning(f"Error fitting PSF model: {e}. Continuing without PSF photometry.")
        return None, None

    # Ensure we have a usable PSF model for PSFPhotometry
    try:
        # Get oversampling from the EPSF model
        oversamp = getattr(epsf, "oversampling", 2)
        if isinstance(oversamp, (list, tuple, np.ndarray)):
            oversamp = int(np.asarray(oversamp).ravel()[0])
        else:
            oversamp = int(oversamp)

        st.write(f"PSF oversampling factor: {oversamp}")

        try:
            from photutils.psf import ImagePSF

            # CRITICAL: Pass oversampling to ImagePSF to preserve centering!
            # Without this, the PSF model center is shifted causing magnitude offset
            psf_for_phot = ImagePSF(epsf_data, oversampling=oversamp)
            st.write(f"Created ImagePSF with oversampling={oversamp}")
        except Exception as ipsf_err:
            st.warning(f"ImagePSF creation failed: {ipsf_err}")
            # fallback to epsf object (works on newer photutils versions)
            psf_for_phot = epsf
            st.write("Using EPSF model directly for photometry")

        # Verify PSF model center
        psf_center_y, psf_center_x = np.array(epsf_data.shape) / 2.0
        st.write(
            f"PSF model center: ({psf_center_x:.2f}, {psf_center_y:.2f}) in oversampled pixels"
        )

        # Check if PSF peak is at center
        peak_y, peak_x = np.unravel_index(np.argmax(epsf_data), epsf_data.shape)
        offset_x = (peak_x - psf_center_x) / oversamp
        offset_y = (peak_y - psf_center_y) / oversamp
        if abs(offset_x) > 0.5 or abs(offset_y) > 0.5:
            st.warning(
                f"⚠ PSF peak is offset from center by ({offset_x:.2f}, {offset_y:.2f}) pixels. "
                f"Attempting sub-pixel re-centering..."
            )
            # Non-destructive re-centering: shift a copy of epsf_data so the peak
            # aligns with the array center. Uses scipy.ndimage.shift (cubic spline,
            # order=3) which is already available through the astropy dependency.
            # Shift is computed in oversampled pixels. We only adopt the result if
            # it actually reduces the offset; otherwise the original is kept.
            try:
                from scipy.ndimage import shift as _ndimage_shift

                # Shift needed in oversampled-pixel coordinates
                shift_y_os = psf_center_y - peak_y
                shift_x_os = psf_center_x - peak_x
                epsf_data_centered = _ndimage_shift(
                    epsf_data,
                    [shift_y_os, shift_x_os],
                    order=3,
                    mode="constant",
                    cval=0.0,
                )
                new_peak_y, new_peak_x = np.unravel_index(
                    np.argmax(epsf_data_centered), epsf_data_centered.shape
                )
                new_offset_x = (new_peak_x - psf_center_x) / oversamp
                new_offset_y = (new_peak_y - psf_center_y) / oversamp
                improved = abs(new_offset_x) <= abs(offset_x) and abs(new_offset_y) <= abs(offset_y)
                if improved:
                    epsf_data = epsf_data_centered
                    # Recreate psf_for_phot from the re-centered data
                    try:
                        psf_for_phot = ImagePSF(epsf_data, oversampling=oversamp)
                    except Exception:
                        pass  # Keep the previously created psf_for_phot
                    st.write(
                        f"✓ PSF re-centered: new peak offset "
                        f"({new_offset_x:.2f}, {new_offset_y:.2f}) px "
                        f"(was ({offset_x:.2f}, {offset_y:.2f}) px)"
                    )
                else:
                    st.write(
                        "ⓘ Re-centering did not improve alignment "
                        f"(new offset: ({new_offset_x:.2f}, {new_offset_y:.2f}) px); "
                        "keeping original PSF."
                    )
            except Exception as _recenter_err:
                st.warning(f"PSF re-centering skipped: {_recenter_err}. Keeping original PSF.")
        else:
            st.write(
                f"✓ PSF peak offset from center: ({offset_x:.2f}, {offset_y:.2f}) pixels (acceptable)"
            )

        # Save / display epsf as FITS and figure (best-effort)
        try:
            hdu = fits.PrimaryHDU(data=epsf_data)
            model_type = "EPSF"
            hdu.header["COMMENT"] = f"PSF model: {model_type}"
            hdu.header["PSFTYPE"] = (
                "EPSF",
                "Type of PSF model used",
            )

            # Ensure FWHM written as a scalar
            try:
                if np is not None and (
                    hasattr(fwhm, "__len__") and not np.isscalar(fwhm)
                ):
                    fwhm_val = float(np.asarray(fwhm).mean())
                else:
                    fwhm_val = float(fwhm)
            except Exception:
                fwhm_val = float(getattr(epsf, "fwhm", 0.0) or 0.0)
            hdu.header["FWHMPIX"] = (fwhm_val, "FWHM in pixels used for extraction")

            # OVERSAMP may be tuple/list/array; convert to safe FITS-friendly value
            oversamp = getattr(epsf, "oversampling", 2)
            try:
                if isinstance(oversamp, (list, tuple, np.ndarray)):
                    # store as comma-separated string to avoid illegal array header values
                    oversamp_val = ",".join(
                        str(int(x)) for x in np.asarray(oversamp).ravel()
                    )
                else:
                    oversamp_val = int(oversamp)
            except Exception:
                oversamp_val = 2
            hdu.header["OVERSAMP"] = (str(oversamp_val), "Oversampling factor")

            # Number of stars used - ensure scalar int
            try:
                nstars_val = int(len(filtered_photo_table))
            except Exception:
                nstars_val = 0
            hdu.header["NSTARS"] = (nstars_val, "Number of stars used for PSF model")

            psf_filename = (
                f"{st.session_state.get('base_filename', 'psf_model')}_psf.fits"
            )
            username = st.session_state.get("username", "anonymous")
            psf_filepath = os.path.join(
                ensure_output_directory(directory=f"{username}_results"), psf_filename
            )
            hdu.writeto(psf_filepath, overwrite=True)
            st.write(f"PSF model saved as FITS file ({model_type})")

            norm_epsf = simple_norm(epsf_data, "log", percent=99.0)
            fig_epsf_model, ax_epsf_model = plt.subplots(
                figsize=FIGURE_SIZES["medium"], dpi=120
            )
            ax_epsf_model.imshow(
                epsf_data,
                norm=norm_epsf,
                origin="lower",
                cmap="viridis",
                interpolation="nearest",
            )
            title = f"Fitted PSF Model ({nstars_val} stars)"
            ax_epsf_model.set_title(title)
            st.pyplot(fig_epsf_model)
        except Exception as e:
            st.warning(f"Error working with PSF model display/save: {e}")
    except Exception as e:
        st.warning(
            f"Error preparing PSF model for photometry: {e}. Continuing without PSF photometry."
        )
        return None, None

    # Create PSF photometry object and perform photometry
    try:
        if error is not None and not isinstance(error, np.ndarray):
            st.warning(
                "Invalid error array provided, proceeding without error estimation"
            )
            error = None
        elif error is not None and error.shape != img.shape:
            st.warning(
                "Error array shape mismatch, proceeding without error estimation"
            )
            error = None

        # Create a SourceGrouper
        min_separation = 1.9 * fwhm
        grouper = SourceGrouper(min_separation=min_separation)
        # sigma_clip = SigmaClip(sigma=3.0)
        # bkgstat = MMMBackground(sigma_clip=sigma_clip)
        # localbkg_estimator = LocalBackground(3.0 * fwhm, 5.0 * fwhm, bkgstat)

        psfphot = PSFPhotometry(
            psf_model=psf_for_phot,
            fit_shape=fit_shape,
            finder=daostarfind,
            aperture_radius=1.3 * aperture_radius,
            grouper=grouper,
            # localbkg_estimator=localbkg_estimator,
        )

        initial_params = Table()
        initial_params["x"] = photo_table[_xcol]
        initial_params["y"] = photo_table[_ycol]
        if "flux" in photo_table.colnames:
            initial_params["flux"] = photo_table["flux"]
        initial_params["x_0"] = initial_params["x"]
        initial_params["y_0"] = initial_params["y"]

        # Filter out sources that fall in masked regions (robust)
        mask_bool = None
        if mask is not None:
            mask_bool = np.asarray(mask, dtype=bool)
        if mask_bool is not None:
            # compute rounded indices with clipping
            x_int = np.clip(
                np.round(initial_params["x"]).astype(int), 0, img.shape[1] - 1
            )
            y_int = np.clip(
                np.round(initial_params["y"]).astype(int), 0, img.shape[0] - 1
            )

            valid_bounds = (
                (initial_params["x"] >= 0)
                & (initial_params["x"] < img.shape[1])
                & (initial_params["y"] >= 0)
                & (initial_params["y"] < img.shape[0])
            )

            valid_mask = np.ones(len(initial_params), dtype=bool)
            valid_mask[valid_bounds] = ~mask_bool[
                y_int[valid_bounds], x_int[valid_bounds]
            ]

            initial_params_filtered = initial_params[valid_mask]

            st.write(
                f"Filtered out {len(initial_params) - len(initial_params_filtered)} sources that fall in masked regions"
            )
            st.write(
                f"Proceeding with {len(initial_params_filtered)} sources for PSF photometry"
            )

            if len(initial_params_filtered) == 0:
                st.error(
                    "All sources fall in masked regions. Cannot perform PSF photometry."
                )
                return None, epsf

            initial_params = initial_params_filtered
        else:
            st.write("No mask provided, using all sources for PSF photometry")

        st.write("Performing PSF photometry on sources...")
        # call PSFPhotometry: data first
        phot_epsf_result = psfphot(
            img, init_params=initial_params, mask=mask_bool, error=error
        )
        st.session_state["epsf_photometry_result"] = phot_epsf_result
        st.write("PSF photometry completed successfully.")
        return phot_epsf_result, epsf

    except Exception as e:
        st.warning(
            f"Error executing PSF photometry: {e}. Continuing without PSF photometry."
        )
        return None, None
