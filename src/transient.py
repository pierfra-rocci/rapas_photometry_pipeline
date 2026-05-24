from stdpipe import pipeline, cutouts, templates, catalogs, photometry, plots
from stdpipe import astrometry as stdpipe_astrometry
import sep
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
from src.tools_pipeline import get_prefixed_photometry_column

from src.tools_pipeline import fix_header


# Conversion to AB mags, from https://www.astronomy.ohio-state.edu/martini.10/usefuldata.html
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

        # Query SkyBoT for Solar System objects in the field.
        # Direct HTTP request to the current IMCCE endpoint: astroquery 0.4.10
        # hardcodes the stale vo.imcce.fr URL which is no longer reachable;
        # the service has migrated to ssp.imcce.fr.
        import requests as _requests
        _SKYBOT_URL = "https://ssp.imcce.fr/webservices/skybot/api/conesearch.php"
        _skybot_rad = min(float(sr0 + 2.0 * sr), 10.0)
        _skybot_payload = {
            "-ra": ra0,
            "-dec": dec0,
            "-rd": _skybot_rad,
            "-ep": str(obs_time.jd),
            "-loc": "500",
            "-filter": 120,
            "-objFilter": "111",
            "-refsys": "EQJ2000",
            "-output": "all",
            "-mime": "text",
        }
        _skybot_resp = _requests.get(_SKYBOT_URL, params=_skybot_payload, timeout=120)
        if _skybot_resp.status_code == 204 or not _skybot_resp.text.strip():
            skybot_results = None
        else:
            Skybot._get_raw_response = False
            skybot_results = Skybot._parse_result(_skybot_resp)

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


def find_candidates(
    photometry_table,
    zero_point_value,
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
    detect_thresh=3.0,
):
    """Find transient candidates in the given image around the specified object.
    Parameters
    ----------
    obj : dict
        Dictionary with object information (e.g., coordinates).
    image : 2D array
        The image data where to search for transients.
    mask : 2D array
        The mask data corresponding to the image."""
    if header.get("GAIN"):
        gain = header.get("GAIN")
    else:
        gain = 65635 / np.max(image)

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
    image = image.astype(image.dtype.newbyteorder("="))
    bkg = sep.Background(image, mask=mask, bw=64, bh=64, fw=5, fh=5)

    # Subtract spatially-varying background from image
    image_sub = image - bkg.back()

    # Detect objects using background-subtracted image
    obj = sep.extract(image_sub, detect_thresh, gain=gain, mask=mask, err=bkg.globalrms)

    if obj is None:
        st.warning("No objects found in the image.")
        return []

    st.success(f"Found {len(obj)} objects in the image.")

    # Add RA, Dec coordinates to the obj table using WCS
    ra_obj, dec_obj = wcs.all_pix2world(obj["x"], obj["y"], 0)
    obj = np.lib.recfunctions.append_fields(
        obj, ["ra", "dec"], [ra_obj, dec_obj], usemask=False
    )

    # Cross-match SEP detections with photometry_table to get PSF magnitudes
    if photometry_table is not None and len(photometry_table) > 0:

        # Get coordinates from photometry_table
        phot_ra = photometry_table["ra"]
        phot_dec = photometry_table["dec"]

        # Cross-match using spherical matching (10 arcsec radius)
        match_radius = 10 / 3600  # 10 arcsec in degrees
        oidx, pidx, _ = stdpipe_astrometry.spherical_match(
            obj["ra"], obj["dec"], phot_ra, phot_dec, match_radius
        )

        if len(oidx) > 0:
            # Keep only matched objects
            obj = obj[oidx]

            psf_mag_col = get_prefixed_photometry_column("psf_mag", filter_cat)
            if psf_mag_col not in photometry_table.columns:
                psf_mag_col = "psf_mag"

            psf_mag_err_col = get_prefixed_photometry_column(
                "psf_mag_err", filter_cat
            )
            if psf_mag_err_col not in photometry_table.columns:
                psf_mag_err_col = "psf_mag_err"

            if (
                psf_mag_col not in photometry_table.columns
                or psf_mag_err_col not in photometry_table.columns
            ):
                st.warning(
                    "PSF magnitude columns not found in photometry table for transient filtering"
                )
                return []

            # Get PSF magnitudes from photometry_table for matched sources
            psf_mag = np.array(photometry_table[psf_mag_col][pidx])
            psf_mag_err = np.array(photometry_table[psf_mag_err_col][pidx])

            # Add PSF photometry as mag_calib (reference magnitude for transients)
            obj = np.lib.recfunctions.append_fields(
                obj,
                ["mag_calib", "mag_calib_err"],
                [psf_mag, psf_mag_err],
                usemask=False,
            )
        else:
            st.warning(
                "No matches found between SEP detections and PSF photometry table"
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
    obj["flag"].name = "flags"  # Rename to 'flags' for compatibility

    # Build vizier catalog list - include all useful catalogs
    # Note: 'apass' and 'gsc' excluded due to RA/DEC column naming issues in stdpipe
    vizier_catalogs = ["gaiaedr3", "ps1", "skymapper", "sdss", "atlas", "apass", "gsc"]

    # First, run filter_transient_candidates WITHOUT SkyBoT (it has column naming issues)
    try:
        candidates = pipeline.filter_transient_candidates(
            obj,
            cat=cat,
            fwhm=1.0 * fwhm * pixel_scale,
            time=header.get("DATE-OBS", None),
            skybot=False,  # Disable stdpipe's SkyBoT - we'll do our own
            vizier=vizier_catalogs,
            vizier_checker_fn=lambda xobj, xcat, catname: checker_fn(
                xobj, xcat, catname, filter_mag=filter_cat
            ),
            ned=False,
            verbose=True,
            flagged=True,
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

    # Remove candidates with low S/N (S/N < 5, i.e., mag_err > 0.217)
    # S/N ≈ 1.0857 / mag_err for Poisson noise
    min_snr = 20.0
    max_mag_err = 1.0857 / min_snr  # ~0.217 mag
    good_snr = candidates["mag_calib_err"] < max_mag_err
    n_low_snr = len(candidates) - np.sum(good_snr)
    if n_low_snr > 0:
        st.warning(f"Removed {n_low_snr} candidates with low S/N (<{min_snr})")
        candidates = candidates[good_snr]

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

    return candidates


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

    # Pick survey based on hemisphere
    if dec_center < 0:
        cat_cutout = "SkyMapper/DR4/"
    else:
        cat_cutout = "PanSTARRS/DR1/"

    header, _ = fix_header(header)

    st.subheader("Astro-Colibri Match Cutouts")

    saved_paths = []
    for i, (_, row) in enumerate(ac_rows.iterrows()):
        # Build a minimal dict compatible with stdpipe's get_cutout
        ra = row.get("ra")
        dec = row.get("dec")
        if ra is None or dec is None or not (np.isfinite(ra) and np.isfinite(dec)):
            continue

        # Retrieve the best available magnitude / error for the title
        mag_val = None
        mag_err_val = None
        for mag_col in ["psf_mag", "aperture_mag_1_3", "aperture_mag_1_1"]:
            if mag_col in row and row[mag_col] is not None:
                try:
                    v = float(row[mag_col])
                    if np.isfinite(v):
                        mag_val = v
                        break
                except (TypeError, ValueError):
                    pass
        for err_col in ["psf_mag_err", "aperture_mag_err_1_3", "aperture_mag_err_1_1"]:
            if err_col in row and row[err_col] is not None:
                try:
                    v = float(row[err_col])
                    if np.isfinite(v):
                        mag_err_val = v
                        break
                except (TypeError, ValueError):
                    pass

        name = row.get("astrocolibri_name", f"AC_{i+1}")
        ac_type = row.get("astrocolibri_type", "")
        ac_class = row.get("astrocolibri_classification", "")

        cand_dict = {
            "ra": ra,
            "dec": dec,
            # Handle photutils 3.0 column rename: xcenter/ycenter -> x_center/y_center
            "x": row.get("xcenter") if "xcenter" in row.index else row.get("x_center", 0.0),
            "y": row.get("ycenter") if "ycenter" in row.index else row.get("y_center", 0.0),
        }

        try:
            cutout = cutouts.get_cutout(image, cand_dict, 25, header=header)
        except Exception as e:
            st.warning(f"Could not create cutout for {name}: {e}")
            continue

        try:
            cutout["template"] = templates.get_hips_image(
                cat_cutout + filter_name,
                header=cutout.get("header"),
                get_header=False,
            )
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
        title = " | ".join(title_parts)

        fig = plot_cutout(
            cutout,
            planes=["image", "template"],
            qq=[1, 99],
            stretch="linear",
            title=title,
        )
        st.pyplot(fig)

        # Save PNG to output_dir so it ends up in the ZIP
        try:
            safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(name))
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
    """Routine for displaying various image planes from the cutout structure returned by :func:`stdpipe.cutouts.get_cutout`.

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


def checker_fn(xobj, xcat, catname, filter_mag="r"):
    """Filter candidates based on magnitude consistency with reference catalog.

    Rejects objects that are significantly fainter than their catalog counterparts,
    keeping only sources that are brighter or within the tolerance threshold.
    This filters out most catalog matches while keeping potential transients/variables.

    Parameters
    ----------
    xobj : astropy.table.Table or numpy.ndarray
        Detected objects with 'mag_calib' column
    xcat : astropy.table.Table or numpy.ndarray
        Reference catalog for comparison
    catname : str
        Name of the catalog (for informational purposes)
    filter_mag : str, default 'r'
        Filter name (e.g., 'r', 'rmag', 'V', 'Vmag', 'phot_g_mean_mag')

    Returns
    -------
    numpy.ndarray
        Boolean mask where True keeps sources that are:
        - Brighter than catalog (diff < 0)
        - Within 2.0 mag fainter than catalog (-2.0 < diff < 0)
        False rejects sources much fainter than catalog (diff <= -2.0)
    """
    # Initialize: all objects pass by default if no catalog match found
    xidx = np.ones_like(xobj, dtype=bool)

    # Normalize filter name: extract core filter from various formats
    fname = filter_mag
    if fname.endswith("mag"):
        fname = fname[:-3]  # Remove 'mag' suffix

    # Extract single-letter filter from composite names (e.g., 'phot_g_mean_' -> 'g')
    # Look for single letter filters: u, g, r, i, z, U, B, V, R, I, G, BP, RP
    single_filters = ["u", "g", "r", "i", "z", "U", "B", "V", "R", "I", "G", "BP", "RP"]
    extracted_filter = None
    for filt in single_filters:
        if filt in fname:
            extracted_filter = filt
            break

    if extracted_filter:
        fname = extracted_filter

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

        # Select objects that are BRIGHTER than catalog (potential transients/variables)
        # Negative diff means detected source is brighter than catalog
        # Threshold: > -2.0 means reject only if fainter by more than 2.0 mags
        # This keeps: all brighter sources + sources within 2.0 mag fainter
        xidx = diff > -2.0

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
        "RP": ["i", "imag", "RPmag"],
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
        if fname in ["U", "B", "V", "R", "u", "g", "r", "BP"]:
            cat_col_mag = "BPmag"
        elif fname in ["I", "i", "z", "RP"]:
            cat_col_mag = "RPmag"
        else:
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
