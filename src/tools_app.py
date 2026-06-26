import os
import zipfile
import base64
import json
import warnings
from datetime import datetime
from io import BytesIO

# Third-Party Imports
import streamlit as st
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from astropy.io import fits

# Local Application Imports
from src.tools_pipeline import fix_header, get_filter_prefix
from src.utils import write_to_log, ensure_output_directory

warnings.filterwarnings("ignore")


@st.cache_data(ttl=300, show_spinner=False)
def _gaia_server_available() -> bool:
    """Check Gaia service availability with a short TTL cache."""
    from astroquery.gaia import Gaia

    Gaia.load_tables(only_names=True)
    return True


def try_gaia_server():
    """Check if Gaia server is reachable."""
    try:
        # Try a simple query to check if the server is responding.
        return _gaia_server_available()
    except Exception:
        st.warning(
            "⚠️ Unable to reach GAIA Server through Astroquery. \n"
            "The Server may be down or under maintenance. Please try again later !"
        )
        return False


@st.cache_data(show_spinner=False)
def load_tutorial_text(tutorial_file: str) -> str:
    """Load and cache tutorial markdown from the docs directory."""
    with open(os.path.join("docs", tutorial_file), "r", encoding="utf-8") as handle:
        return handle.read()


def _safe_unlink_session_path(path_key: str) -> None:
    """Delete a session file if present and remove the session key."""
    if path_key in st.session_state:
        try:
            path = st.session_state[path_key]
            if path and os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass
        del st.session_state[path_key]


def clear_all_caches():
    """Clear all Streamlit caches and reset file upload state"""
    try:
        st.cache_data.clear()
        if hasattr(st.cache_resource, "clear"):
            st.cache_resource.clear()

        # Clear file upload related session state
        upload_keys = [
            key for key in st.session_state.keys() if "uploader" in key.lower()
        ]
        for key in upload_keys:
            del st.session_state[key]

        # Also clear persisted uploaded file (if present)
        if "uploaded" in st.session_state:
            try:
                del st.session_state["uploaded"]
            except Exception:
                pass

        if "uploaded_bytes" in st.session_state:
            try:
                del st.session_state["uploaded_bytes"]
            except Exception:
                pass
        if "uploaded_name" in st.session_state:
            try:
                del st.session_state["uploaded_name"]
            except Exception:
                pass

        _safe_unlink_session_path("uploaded_temp_path")
        _safe_unlink_session_path("science_file_path")

        if "files_loaded" in st.session_state:
            st.session_state["files_loaded"] = {"science_file": None}

        # Clear analysis results to hide download buttons
        result_keys = [
            "calibrated_header",
            "final_phot_table",
            "epsf_model",
            "epsf_photometry_result",
            "log_buffer",
            "log_summary_written",
            "last_saved_log_content",
        ]
        for key in result_keys:
            if key in st.session_state:
                del st.session_state[key]

        # Reset base filename to default
        st.session_state["base_filename"] = "photometry"

        st.success("All caches cleared successfully!")
        st.rerun()
    except Exception as e:
        st.error(f"Error clearing caches: {e}")


@st.cache_data(show_spinner=False)
def load_fits_data(_file):
    """
    Load image data and header from a FITS file with robust error handling.

    Handles multiple FITS formats including multi-extension files, data cubes,
    and RGB images. For multi-dimensional data, extracts a 2D plane.

    Parameters
    ----------
    file : Streamlit UploadedFile
        FITS file object from Streamlit file uploader

    Returns
    -------
    tuple
        (image_data, header) where:
        - image_data: 2D numpy array of pixel values, or None if loading failed
        - header: FITS header dictionary, or None if loading failed

    Notes
    -----
    - For multi-extension FITS files, uses the primary HDU if it contains data,
    otherwise uses the first HDU with valid data.
    - For 3D data (RGB or data cube), extracts the first 2D plane with appropriate warnings.
    - For higher dimensional data, takes the first slice along all extra dimensions.
    """
    if _file is not None:
        # Read bytes and guard against empty uploads
        try:
            file_content = _file.read()
        except Exception as e:
            st.error(f"Failed to read uploaded file: {e}")
            return None, None

        if not file_content:
            st.error("Uploaded FITS file is empty.")
            return None, None

        # Try opening the FITS file and handle corrupt/invalid FITS gracefully
        try:
            hdul = fits.open(BytesIO(file_content), mode="readonly")
        except OSError as e:
            st.error(f"Invalid or corrupt FITS file: {e}")
            return None, None
        except Exception as e:
            st.error(f"Error opening FITS file: {e}")
            return None, None

        try:
            data = hdul[0].data
            hdul.verify("fix")
            header = hdul[0].header

            if data is None:
                for i, hdu in enumerate(hdul[1:], 1):
                    if hasattr(hdu, "data") and hdu.data is not None:
                        data = hdu.data
                        header = hdu.header
                        if st.session_state.get("log_buffer"):
                            write_to_log(
                                st.session_state["log_buffer"],
                                f"Primary HDU has no data. Using data from HDU #{i}.",
                            )
                        break

            if data is None:
                st.warning("No image data found in the FITS file.")
                return None, None

            if len(data.shape) == 3:
                if data.shape[0] == 3 or data.shape[0] == 4:
                    st.info("Using first color channel.")
                    data = data[0]
                elif data.shape[2] == 3 or data.shape[2] == 4:
                    st.info("Using first color channel.")
                    data = data[:, :, 0]
                else:
                    data = data[0]
            elif len(data.shape) > 3:
                st.warning(
                    f"Data has {len(data.shape)} dimensions. Using first slice only."
                )
                sliced_data = data
                while len(sliced_data.shape) > 2:
                    sliced_data = sliced_data[0]
                data = sliced_data

            # Apply header fixes before returning
            try:
                fixed_header, log_messages = fix_header(header)
                header = fixed_header
                if st.session_state.get("log_buffer"):
                    for msg in log_messages:
                        level, message = msg.split(":", 1)
                        write_to_log(
                            st.session_state["log_buffer"],
                            message.strip(),
                            level=level.strip(),
                        )
                        if level.strip() == "WARNING":
                            st.warning(message.strip())

            except Exception as fix_error:
                st.warning(f"Header fixing encountered an issue: {str(fix_error)}")
                if st.session_state.get("log_buffer"):
                    write_to_log(
                        st.session_state["log_buffer"],
                        f"Header fixing failed: {str(fix_error)}",
                        level="WARNING",
                    )

            return data, header

        except Exception as e:
            st.error(f"Error loading FITS file: {str(e)}")
            return None, None
        finally:
            try:
                hdul.close()
            except Exception:
                pass

    return None, None


def display_catalog_in_aladin(
    final_table: pd.DataFrame,
    ra_center: float,
    dec_center: float,
    fov: float = 1.5,
    ra_col: str = "ra",
    dec_col: str = "dec",
    mag_col: str = "psf_mag",
    alt_mag_col: str = "aperture_mag_1_1",
    catalog_col: str = "catalog_matches",
    id_cols: list[str] = ["simbad_main_id", "aavso_Name"],
    fallback_id_prefix: str = "Source",
    survey: str = "CDS/P/DSS2/color",
    filter_band: str | None = None,
) -> None:
    """
    Display a DataFrame catalog in an embedded Aladin Lite interactive sky viewer.
    This function creates an interactive astronomical image with catalog overlay
    that allows exploring detected sources and their cross-matches.

    Parameters
    ----------
    final_table : pandas.DataFrame
        DataFrame containing catalog data with coordinates and photometry
    ra_center : float
        Right Ascension center coordinate for the Aladin view (degrees)
    dec_center : float
        Declination center coordinate for the Aladin view (degrees)
    fov : float, optional
        Initial field of view in degrees, default=1.5
    ra_col : str, optional
        Name of the column containing Right Ascension values, default='ra'
    dec_col : str, optional
        Name of the column containing Declination values, default='dec'
    mag_col : str, optional
        Name of the primary magnitude column, default='psf_mag'
    alt_mag_col : str, optional
        Name of an alternative (preferred) magnitude column, default='aperture_mag'
    catalog_col : str, optional
        Name of the column containing catalog match information, default='catalog_matches'
    id_cols : list[str], optional
        List of column names (in order of preference) to use for source identifiers
    fallback_id_prefix : str, optional
        Prefix to use for source names if no ID is found, default="Source"
    survey : str, optional
        The initial sky survey to display in Aladin Lite, default="CDS/P/DSS2/color"

    Notes
    -----
    The function creates an interactive HTML component embedded in Streamlit that
    shows a DSS image of the field with catalog sources overlaid as interactive markers.
    Each marker shows a popup with detailed source information when clicked.

    Handles errors gracefully and provides feedback in the Streamlit interface
    if any issues occur.
    """
    if not (
        isinstance(ra_center, (int, float)) and isinstance(dec_center, (int, float))
    ):
        st.error("Missing or invalid center coordinates (RA/Dec) for Aladin display.")
        return

    if not isinstance(final_table, pd.DataFrame) or final_table.empty:
        st.warning("Input table is empty or not a DataFrame. Cannot display in Aladin.")
        return

    if ra_col not in final_table.columns or dec_col not in final_table.columns:
        st.error(
            f"Required columns '{ra_col}' or '{dec_col}' not found in the DataFrame."
        )
        return

    filter_prefix = get_filter_prefix(filter_band)
    if filter_prefix:
        prefixed_mag_col = f"{filter_prefix}_{mag_col}"
        prefixed_alt_mag_col = f"{filter_prefix}_{alt_mag_col}"
        if prefixed_mag_col in final_table.columns:
            mag_col = prefixed_mag_col
        if prefixed_alt_mag_col in final_table.columns:
            alt_mag_col = prefixed_alt_mag_col

    catalog_sources = []
    required_cols = {ra_col, dec_col}
    optional_cols = {mag_col, alt_mag_col, catalog_col}.union(set(id_cols))
    available_cols = set(final_table.columns)

    present_optional_cols = optional_cols.intersection(available_cols)
    cols_to_iterate = list(required_cols.union(present_optional_cols))

    for idx, row in final_table[cols_to_iterate].iterrows():
        ra_val = row[ra_col]
        dec_val = row[dec_col]
        if pd.notna(ra_val) and pd.notna(dec_val):
            try:
                source = {"ra": float(ra_val), "dec": float(dec_val)}
            except (ValueError, TypeError):
                continue

            psf_mag = None
            aperture_mag = None

            # Get PSF magnitude
            if mag_col in present_optional_cols and pd.notna(row[mag_col]):
                try:
                    psf_mag = float(row[mag_col])
                    source["psf_mag"] = psf_mag
                except (ValueError, TypeError):
                    pass

            # Get aperture magnitude (try multiple aperture columns)
            # Exclude error columns (which contain "err" anywhere in the name)
            aperture_mag_prefix = (
                f"{filter_prefix}_aperture_mag_" if filter_prefix else "aperture_mag_"
            )
            aperture_mag_cols = [
                col
                for col in final_table.columns
                if col.startswith(aperture_mag_prefix) and "err" not in col
            ]
            if not aperture_mag_cols:
                aperture_mag_cols = [
                    col
                    for col in final_table.columns
                    if col.startswith("aperture_mag_") and "err" not in col
                ]
            for ap_col in aperture_mag_cols:
                if ap_col in final_table.columns:
                    val = final_table.loc[idx, ap_col]
                    if pd.notna(val):
                        try:
                            aperture_mag = float(val)
                            source["aperture_mag"] = aperture_mag
                            break
                        except (ValueError, TypeError):
                            continue

            # Handle catalog matches - collect individual catalog IDs
            catalog_matches = {}

            # SIMBAD matches
            if "simbad_main_id" in present_optional_cols and pd.notna(
                row["simbad_main_id"]
            ):
                simbad_id = str(row["simbad_main_id"]).strip()
                if simbad_id and simbad_id not in ["", "nan", "None"]:
                    catalog_matches["SIMBAD"] = simbad_id

            # AAVSO VSX matches (variable stars)
            if "aavso_Name" in present_optional_cols and pd.notna(row["aavso_Name"]):
                aavso_name = str(row["aavso_Name"]).strip()
                if aavso_name and aavso_name not in ["", "nan", "None"]:
                    catalog_matches["AAVSO"] = aavso_name

            # Astro-Colibri matches
            if "astrocolibri_name" in present_optional_cols and pd.notna(
                row["astrocolibri_name"]
            ):
                colibri_name = str(row["astrocolibri_name"]).strip()
                if colibri_name and colibri_name not in ["", "nan", "None"]:
                    catalog_matches["Astro-Colibri"] = colibri_name

            # Quasar matches
            if "qso_name" in present_optional_cols and pd.notna(row["qso_name"]):
                qso_name = str(row["qso_name"]).strip()
                if qso_name and qso_name not in ["", "nan", "None"]:
                    catalog_matches["QSO"] = qso_name

            # GAIA matches (if calibration star)
            if "gaia_calib_star" in present_optional_cols and row.get(
                "gaia_calib_star", False
            ):
                catalog_matches["GAIA"] = "Calibration Star"

            source["catalog_matches"] = catalog_matches
            source_id = f"{fallback_id_prefix} {idx + 1}"

            # First, check if there's an "id" column in the table
            if "id" in final_table.columns and pd.notna(row.get("id")):
                id_value = str(row["id"]).strip()
                if id_value and id_value not in ["nan", "None", ""]:
                    source_id = f"ID: {id_value}"

            elif id_cols:
                for id_col in id_cols:
                    if id_col in present_optional_cols and pd.notna(row[id_col]):
                        id_value = str(row[id_col]).strip()
                        if id_value and id_value not in ["nan", "None", ""]:
                            source_id = id_value
                            break

            source["name"] = source_id
            source["source_number"] = idx + 1

            # Add the raw ID value separately for display in popup
            if "id" in final_table.columns and pd.notna(row.get("id")):
                source["catalog_id"] = str(row["id"])

            catalog_sources.append(source)

    if not catalog_sources:
        st.warning("No valid sources with RA/Dec found in the table to display.")
        return

    with st.spinner("Loading Aladin Lite viewer..."):
        try:
            try:
                sources_json_b64 = base64.b64encode(
                    json.dumps(catalog_sources).encode("utf-8")
                ).decode("utf-8")
            except Exception as e:
                st.error(f"Failed to encode catalog data: {str(e)}")
                return

            html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, height=device-height, initial-scale=1.0, user-scalable=no">
    <title>Aladin Lite v3</title>
</head>
<body>
    <div id="aladin-lite-div" style="width:100%;height:550px;"></div>
    <script type="text/javascript" src="https://code.jquery.com/jquery-1.12.1.min.js" charset="utf-8"></script>
    <script type="text/javascript" src="https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js" charset="utf-8"></script>
    <script type="text/javascript">
        let aladin;
        A.init.then(() => {{
            try {{
                // Initialize Aladin v3
                aladin = A.aladin('#aladin-lite-div', {{
                    target: '{ra_center} {dec_center}',
                    fov: {fov},
                    survey: '{survey}',
                    reticleSize: 0,
                    showZoomControl: true,
                    showFullscreenControl: true,
                    showLayersControl: true,
                    showGotoControl: true,
                    showSimbadPointerControl: true
                }});

                // Create catalog overlay
                let cat = A.catalog({{
                    name: 'Photometry Results',
                    sourceSize: 12,
                    shape: 'circle',
                    color: '#00ff88',
                    onClick: 'showPopup'
                }});
                aladin.addCatalog(cat);
                
                // Decode and parse the base64 encoded JSON data
                let sourcesData = JSON.parse(atob("{sources_json_b64}"));
                let aladinSources = [];

                sourcesData.forEach(function(source) {{
                    // Build comprehensive popup content
                    let popupContent = '<div style="padding:8px; max-width:300px; font-family:Arial,sans-serif;">';

                    // Header with catalog ID if available, otherwise source number
                    if(source.catalog_id) {{
                        popupContent += '<h4 style="margin:0 0 8px 0; color:#2c5282;">Source ID: ' + source.catalog_id + '</h4>';
                    }} else if(source.source_number) {{
                        popupContent += '<h4 style="margin:0 0 8px 0; color:#2c5282;">Source #' + source.source_number + '</h4>';
                    }}

                    // Coordinates section
                    popupContent += '<div style="background:#f7fafc; padding:6px; margin:4px 0; border-radius:4px;">';
                    popupContent += '<strong>Coordinates:</strong><br/>';
                    popupContent += 'RA: ' + (typeof source.ra === 'number' ? source.ra.toFixed(6) : source.ra) + '°<br/>';
                    popupContent += 'Dec: ' + (typeof source.dec === 'number' ? source.dec.toFixed(6) : source.dec) + '°';
                    popupContent += '</div>';

                    // Photometry section - handle both PSF and aperture magnitudes
                    if(source.psf_mag || source.aperture_mag) {{
                        popupContent += '<div style="background:#edf2f7; padding:6px; margin:4px 0; border-radius:4px;">';
                        popupContent += '<strong>Photometry:</strong><br/>';

                        // Show PSF magnitude if available
                        if(source.psf_mag && source.psf_mag !== null && source.psf_mag !== undefined) {{
                            popupContent += 'PSF Mag: ' + (typeof source.psf_mag === 'number' ? source.psf_mag.toFixed(2) : source.psf_mag) + '<br/>';
                        }}

                        // Show aperture magnitude if available
                        if(source.aperture_mag && source.aperture_mag !== null && source.aperture_mag !== undefined) {{
                            popupContent += 'Aperture Mag: ' + (typeof source.aperture_mag === 'number' ? source.aperture_mag.toFixed(2) : source.aperture_mag) + '<br/>';
                        }}

                        // If only aperture_mag is available and psf_mag is not, show it prominently
                        if(!source.psf_mag && source.aperture_mag) {{
                            popupContent += '<em style="color:#2d3748;">Primary magnitude: Aperture</em><br/>';
                        }}

                        // Signal-to-noise ratio if available
                        if(source.snr) {{
                            popupContent += 'S/N: ' + (typeof source.snr === 'number' ? source.snr.toFixed(1) : source.snr);
                        }}
                        popupContent += '</div>';
                    }}

                    // Catalog matches section
                    if(source.catalog_matches && 
                       Object.keys(source.catalog_matches).length > 0) {{
                        popupContent += '<div style="background:#e6fffa; ' +
                                       'padding:6px; margin:4px 0; border-radius:4px;">';
                        popupContent += '<strong>Catalog Matches:</strong><br/>';
                        for(let catalog in source.catalog_matches) {{
                            let catalogValue = source.catalog_matches[catalog];
                            if(catalogValue && catalogValue !== '' && 
                               catalogValue !== 'nan') {{
                                let escapedCatalogValue = catalogValue.toString()
                                    .replace(/[<>&"']/g, function(m) {{
                                    return {{'<':'&lt;', '>':'&gt;', '&':'&amp;', 
                                            '"':'&quot;', "'":"&#39;"}}[m];
                                }});
                                popupContent += '<span style="display:inline-block; ' +
                                               'background:#81e6d9; color:#234e52; ' +
                                               'padding:2px 6px; margin:1px; ' +
                                               'border-radius:3px; font-size:11px;">';
                                popupContent += catalog + ': ' + escapedCatalogValue + 
                                               '</span><br/>';
                            }}
                        }}
                        popupContent += '</div>';
                    }}

                    popupContent += '</div>';

                    // Create source with v3 properties
                    let aladinSource = A.source(
                        source.ra,
                        source.dec,
                        {{ 
                            data: popupContent,
                        }}
                    );
                    aladinSources.push(aladinSource);
                }});

                // Add sources to catalog
                if (aladinSources.length > 0) {{
                    cat.addSources(aladinSources);
                    cat.updateShape();
                }}
            }} catch (error) {{
                console.error("Error initializing Aladin Lite v3 or adding sources:", error);
                document.getElementById('aladin-lite-div').innerHTML = '<p style="color:red;">Error loading Aladin v3 viewer. Try refreshing the page or use a modern browser.</p>';
            }}
        }}).catch((error) => {{
            console.error("A.init promise rejected:", error);
            document.getElementById('aladin-lite-div').innerHTML = '<p style="color:red;">Error initializing Aladin v3. Please check your browser console for details.</p>';
        }});
    </script>
</body>
</html>
"""
            st.iframe(
                html_content,
                height=600,
            )

        except Exception as e:
            st.error(f"Failed to render Aladin HTML component: {str(e)}")
            st.subheader("Source Coordinates (Aladin viewer unavailable)")
            display_df = pd.DataFrame(catalog_sources)
            if not display_df.empty:
                st.dataframe(display_df, use_container_width=True)


def provide_download_buttons(folder_path):
    """
    Creates a single download button for a zip file containing all files in the specified folder.
    This function compresses all files in the given folder into a single zip archive
    and provides a download button for the archive.

    Args:
        folder_path (str): The path to the folder containing files to be zipped and made downloadable

    Returns:
        None: The function creates a Streamlit download button directly in the app interface
    """
    try:
        base_filename = st.session_state.get("base_filename", "")

        files = [
            f
            for f in os.listdir(folder_path)
            if os.path.isfile(os.path.join(folder_path, f))
            and f.startswith(base_filename)
            and not f.lower().endswith(".zip")
            and not f.lower().endswith(".fits")
        ]
        if not files:
            st.warning("No files found in output directory")
            return

        # Create a timestamp for the zip filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = st.session_state.get("base_filename", "rpp_results")
        zip_filename = f"{base_name}_{timestamp}.zip"

        # Create in-memory zip file
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file in files:
                file_path = os.path.join(folder_path, file)
                zip_file.write(file_path, arcname=file)

        # Reset buffer position to the beginning
        zip_buffer.seek(0)
        st.caption(f"Archive contains {len(files)} files")
        if st.download_button(
            label="📦 Download Results (ZIP)",
            data=zip_buffer,
            file_name=zip_filename,
            mime="application/zip",
            on_click="ignore",
        ):
            st.success("File downloaded !")

    except Exception as e:
        st.error(f"Error creating zip archive: {str(e)}")


def display_archived_files_browser(output_dir):
    """
    Display a file browser for archived ZIP files in the user's results directory.
    This function creates a secure file browser that only allows access to ZIP files
    within the specified output directory.

    Parameters
    ----------
    output_dir : str
        Path to the user's results directory

    Returns
    -------
    None
        Creates Streamlit interface elements directly
    """
    if not os.path.exists(output_dir):
        st.warning("No results directory found.")
        return

    try:
        # Collect only ZIP files
        zip_files = []
        for item in os.listdir(output_dir):
            item_path = os.path.join(output_dir, item)
            if os.path.isfile(item_path) and item.lower().endswith(".zip"):
                # Get file stats
                try:
                    stat = os.stat(item_path)
                    file_size = stat.st_size
                    mod_time = datetime.fromtimestamp(stat.st_mtime)

                    zip_files.append(
                        {
                            "name": item,
                            "size": file_size,
                            "modified": mod_time,
                            "path": item_path,
                        }
                    )
                except Exception:
                    # Skip files that can't be accessed
                    continue

        if not zip_files:
            return

        # Sort files by modification time (newest first)
        zip_files.sort(key=lambda x: x["modified"], reverse=True)

        # Keep lightweight UI state for deferred archive loading
        selected_archive = st.session_state.get("archived_selected_zip")

        # Compact display with per-file selection buttons
        for file_info in zip_files:
            # Format file size
            size = file_info["size"]
            if size > 1024 * 1024:
                size_str = f"{size / (1024 * 1024):.1f}MB"
            elif size > 1024:
                size_str = f"{size / 1024:.1f}KB"
            else:
                size_str = f"{size}B"

            # Format date
            date_str = file_info["modified"].strftime("%m/%d %H:%M")

            # Truncate filename if too long
            display_name = file_info["name"]
            if len(display_name) > 25:
                display_name = display_name[:21] + "..."

            # Create compact row with action button
            col1, col2 = st.columns([3, 1])

            with col1:
                st.text(f"{display_name}")
                st.caption(f"{size_str} • {date_str}")

            with col2:
                if st.button(
                    "⬇️",
                    key=f"select_zip_{file_info['name']}",
                    help=f"Prepare download for {file_info['name']}",
                    use_container_width=True,
                ):
                    st.session_state["archived_selected_zip"] = file_info
                    st.session_state.pop("archived_selected_zip_data", None)
                    selected_archive = file_info

        if selected_archive:
            st.caption(f"Selected archive: {selected_archive['name']}")

            file_data = st.session_state.get("archived_selected_zip_data")
            if file_data is None:
                try:
                    with open(selected_archive["path"], "rb") as handle:
                        file_data = handle.read()
                    st.session_state["archived_selected_zip_data"] = file_data
                except Exception as e:
                    st.error(f"Error opening archive: {str(e)}")
                    return

            downloaded = st.download_button(
                label=f"Download {selected_archive['name']}",
                data=file_data,
                file_name=selected_archive["name"],
                mime="application/zip",
                key=f"download_selected_zip_{selected_archive['name']}",
                use_container_width=True,
            )
            if downloaded:
                st.session_state.pop("archived_selected_zip_data", None)
                st.session_state.pop("archived_selected_zip", None)

    except Exception as e:
        st.error(f"Error accessing results directory: {str(e)}") 


def plot_magnitude_distribution(final_table, log_buffer=None):
    """
    Create magnitude distribution plots (histogram and error scatter plot).

    Parameters
    ----------
    final_table : pandas.DataFrame
        DataFrame containing photometry results with magnitude columns
    log_buffer : StringIO, optional
        Log buffer for writing status messages

    Returns
    -------
    matplotlib.figure.Figure
        Figure object containing the plots
    """
    fig_mag, (ax_mag, ax_err) = plt.subplots(1, 2, figsize=(14, 5), dpi=100)

    filter_band = st.session_state.get("analysis_parameters", {}).get("filter_band")
    filter_prefix = get_filter_prefix(filter_band)

    # Dynamically find available aperture magnitude columns
    # Exclude error columns (which contain "err" anywhere in the name)
    aperture_mag_prefix = (
        f"{filter_prefix}_aperture_mag_" if filter_prefix else "aperture_mag_"
    )
    aperture_mag_cols = [
        col
        for col in final_table.columns
        if col.startswith(aperture_mag_prefix) and "err" not in col
    ]
    if not aperture_mag_cols:
        aperture_mag_cols = [
            col
            for col in final_table.columns
            if col.startswith("aperture_mag_") and "err" not in col
        ]
    aperture_mag_col = aperture_mag_cols[0] if aperture_mag_cols else None

    # Construct the error column name: aperture_mag_1.1 -> aperture_mag_err_1.1
    if aperture_mag_col:
        # Extract the radius suffix (e.g., "1.1" from "aperture_mag_1.1")
        radius_suffix = aperture_mag_col.replace(aperture_mag_prefix, "", 1)
        aperture_err_col = f"{aperture_mag_prefix.replace('aperture_mag_', 'aperture_mag_err_')}{radius_suffix}"
        if aperture_err_col not in final_table.columns:
            aperture_err_col = f"aperture_mag_err_{radius_suffix}"
    else:
        aperture_err_col = None

    has_aperture = (
        aperture_mag_col is not None and aperture_mag_col in final_table.columns
    )
    psf_mag_col = (
        f"{filter_prefix}_psf_mag"
        if filter_prefix and f"{filter_prefix}_psf_mag" in final_table.columns
        else "psf_mag"
    )
    psf_mag_err_col = (
        f"{filter_prefix}_psf_mag_err"
        if filter_prefix and f"{filter_prefix}_psf_mag_err" in final_table.columns
        else "psf_mag_err"
    )
    has_psf = psf_mag_col in final_table.columns

    if not has_aperture and not has_psf:
        # Create empty plots with message
        ax_mag.text(
            0.5,
            0.5,
            "No magnitude data available",
            ha="center",
            va="center",
            transform=ax_mag.transAxes,
        )
        ax_err.text(
            0.5,
            0.5,
            "No magnitude error data available",
            ha="center",
            va="center",
            transform=ax_err.transAxes,
        )
        return fig_mag

    # Calculate bins for magnitude distribution
    mag_values = []
    if has_aperture:
        mag_values.extend(final_table[aperture_mag_col].dropna().tolist())
    if has_psf:
        mag_values.extend(final_table[psf_mag_col].dropna().tolist())

    if mag_values:
        bins = np.linspace(min(mag_values), max(mag_values), 40)
    else:
        bins = 40

    # Magnitude distribution histogram (left panel)
    if has_aperture:
        # Extract aperture radius from column name for label
        aperture_label = (
            aperture_mag_col.split("aperture_mag_", 1)[1]
            if aperture_mag_col
            else "unknown"
        )
        ax_mag.hist(
            final_table[aperture_mag_col].dropna(),
            bins=bins,
            alpha=0.6,
            label=f"Aperture Calib Mag ({aperture_label}×FWHM)",
            color="tab:blue",
        )
    if has_psf:
        ax_mag.hist(
            final_table[psf_mag_col].dropna(),
            bins=bins,
            alpha=0.6,
            label="PSF Calib Mag",
            color="tab:orange",
        )

    ax_mag.set_xlabel("Calibrated Magnitude")
    ax_mag.set_ylabel("Number of Sources")
    ax_mag.set_title("Distribution of Calibrated Magnitudes")
    ax_mag.legend()
    ax_mag.grid(True, alpha=0.3)

    # Scatter plot of magnitude vs error (right panel)
    if has_aperture and aperture_err_col and aperture_err_col in final_table.columns:
        aperture_label = (
            aperture_mag_col.split("aperture_mag_", 1)[1]
            if aperture_mag_col
            else "unknown"
        )
        ax_err.scatter(
            final_table[aperture_mag_col],
            final_table[aperture_err_col],
            alpha=0.7,
            label=f"Aperture ({aperture_label}×FWHM)",
            color="tab:blue",
            s=18,
        )
    if has_psf and psf_mag_err_col in final_table.columns:
        ax_err.scatter(
            final_table[psf_mag_col],
            final_table[psf_mag_err_col],
            alpha=0.7,
            label="PSF",
            color="tab:orange",
            s=18,
        )

    ax_err.set_xlabel("Calibrated Magnitude")
    ax_err.set_ylabel("Magnitude Error")
    ax_err.set_title("Magnitude Error vs Magnitude")
    ax_err.set_yscale("log")
    ax_err.legend()
    ax_err.grid(True, alpha=0.3)

    fig_mag.tight_layout()

    if log_buffer:
        write_to_log(log_buffer, "Created magnitude distribution plots")

    return fig_mag


def handle_log_messages(log_messages):
    """
    Process and display log messages in the Streamlit interface.

    Parameters
    ----------
    log_messages : list[str]
        A list of log messages, each formatted as "LEVEL: message"
    """
    for msg in log_messages:
        level, message = msg.split(":", 1)
        write_to_log(
            st.session_state.log_buffer,
            message.strip(),
            level=level.strip(),
        )
        if level.strip() == "INFO":
            pass
        elif level.strip() == "WARNING":
            st.warning(message.strip())
        elif level.strip() == "ERROR":
            st.error(message.strip())


def initialize_session_state():
    """
    Initialize all session state variables for the application.
    Ensures all required keys have default values.
    """
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if "username" not in st.session_state:
        st.session_state.username = None

    # Core Data/Results State
    defaults = {
        "calibrated_header": None,
        "final_phot_table": None,
        "epsf_model": None,
        "epsf_photometry_result": None,
        "log_buffer": None,
        "base_filename": "photometry",
        "science_file_path": None,
    }

    for key, default_value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default_value

    # Output directory
    if "output_dir" not in st.session_state:
        username = st.session_state.get("username", "anonymous")
        st.session_state.output_dir = ensure_output_directory(
            directory=f"{username}_results"
        )

    # Analysis Parameters
    default_analysis_params = {
        "seeing": 3.0,
        "threshold_sigma": 3.0,
        "fwhm_radius_factor": 1.5,
        "detection_mask": 10,
        "filter_band": "phot_g_mean_mag",
        "filter_max_mag": 21.0,
        "astrometry_check": True,
        "force_plate_solve": False,
        "run_transient_finder": False,
        "transient_survey": "PanSTARRS",
        "transient_filter": "r",
    }

    if "analysis_parameters" not in st.session_state:
        st.session_state.analysis_parameters = default_analysis_params.copy()
    else:
        # Ensure all keys exist
        for key, value in default_analysis_params.items():
            if key not in st.session_state.analysis_parameters:
                st.session_state.analysis_parameters[key] = value

    # Observatory Parameters (keep only dictionary, remove individual keys)
    default_observatory_data = {
        "name": "Obs",
        "latitude": 0.0,
        "longitude": 0.0,
        "elevation": 0.0,
    }

    if "observatory_data" not in st.session_state:
        st.session_state.observatory_data = default_observatory_data.copy()
    else:
        for key, value in default_observatory_data.items():
            if key not in st.session_state.observatory_data:
                st.session_state.observatory_data[key] = value

    # API Keys and File Loading
    if "colibri_api_key" not in st.session_state:
        st.session_state.colibri_api_key = None
    if "files_loaded" not in st.session_state:
        st.session_state.files_loaded = {"science_file": None}

    # --- Sync session state with loaded config ---
    if "observatory_data" in st.session_state:
        obs = st.session_state["observatory_data"]
        st.session_state["observatory_name"] = obs.get("name", "")
        st.session_state["observatory_latitude"] = obs.get("latitude", 0.0)
        st.session_state["observatory_longitude"] = obs.get("longitude", 0.0)
        st.session_state["observatory_elevation"] = obs.get("elevation", 0.0)

    if "analysis_parameters" in st.session_state:
        ap = st.session_state["analysis_parameters"]
        for key in [
            "seeing",
            "threshold_sigma",
            "detection_mask",
            "astrometry_check",
            "force_plate_solve",
            "filter_band",
            "run_transient_finder",
            "transient_survey",
            "transient_filter",
        ]:
            if key in ap:
                st.session_state[key] = ap[key]

    if "colibri_api_key" in st.session_state:
        st.session_state["colibri_api_key"] = st.session_state["colibri_api_key"]


def update_observatory_from_fits_header(header):
    """
    Update observatory data in session state from FITS header information.

    Parameters
    ----------
    header : astropy.io.fits.Header
        FITS header object to extract observatory information from

    Returns
    -------
    bool
        True if any observatory data was updated, False otherwise
    """
    updated = False

    # Check for observatory name
    for key in ["TELESCOP", "OBSERVER"]:
        if key in header and header[key]:
            observatory_name = str(header[key]).strip()
            if observatory_name and observatory_name not in ["", "UNKNOWN", "None"]:
                st.session_state.observatory_data["name"] = observatory_name
                st.session_state["observatory_name"] = observatory_name
                updated = True
                break

    # Check for site latitude - Fixed key name from SITELAN to SITELAT
    if "SITELAT" in header and header["SITELAT"] is not None:
        try:
            latitude = float(header["SITELAT"])
            if -90 <= latitude <= 90:  # Valid latitude range
                st.session_state.observatory_data["latitude"] = latitude
                st.session_state["observatory_latitude"] = latitude
                updated = True
        except (ValueError, TypeError):
            pass

    # Check for site longitude
    if "SITELONG" in header and header["SITELONG"] is not None:
        try:
            longitude = float(header["SITELONG"])
            if -180 <= longitude <= 180:  # Valid longitude range
                st.session_state.observatory_data["longitude"] = longitude
                st.session_state["observatory_longitude"] = longitude
                updated = True
        except (ValueError, TypeError):
            pass

    # Check for site elevation
    if "SITEELEV" in header and header["SITEELEV"] is not None:
        try:
            elevation = float(header["SITEELEV"])
            if elevation >= -400:  # Reasonable minimum (below sea level)
                st.session_state.observatory_data["elevation"] = elevation
                st.session_state["observatory_elevation"] = elevation
                updated = True
        except (ValueError, TypeError):
            pass

    return updated


def cleanup_temp_files():
    """
    Remove temporary FITS files potentially created during processing.

    Specifically targets files stored in the directory indicated by
    `st.session_state['science_file_path']`. It attempts to remove all files
    ending with '.fits', '.fit', or '.fts' (case-insensitive) within that
    directory.

    Notes
    -----
    - Relies on `st.session_state['science_file_path']` being set and pointing
      to a valid temporary file path whose directory contains the files to be
      cleaned.
    - Uses `st.warning` to report errors if removal fails for individual files
      or if the base directory cannot be accessed.
    - This function has side effects (deleting files) and depends on
      Streamlit's session state. It does not return any value.
    """
    if (
        "science_file_path" in st.session_state
        and st.session_state["science_file_path"]
    ):
        try:
            temp_file = st.session_state["science_file_path"]
            if os.path.exists(temp_file):
                base_dir = os.path.dirname(temp_file)
                if os.path.isdir(base_dir):
                    temp_dir_files = [
                        f
                        for f in os.listdir(base_dir)
                        if os.path.isfile(os.path.join(base_dir, f))
                        and f.lower().endswith(
                            (".fits", ".fit", ".fts", ".log", ".png")
                        )
                    ]
                    for file in temp_dir_files:
                        try:
                            os.remove(os.path.join(base_dir, file))
                        except Exception as e:
                            st.warning(f"Could not remove {file}: {str(e)}")
                else:
                    st.warning(f"Temporary path {base_dir} is not a directory.")
        except Exception as e:
            st.warning(f"Could not remove temporary files: {str(e)}")
