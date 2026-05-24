# Standard Library Imports
import sys
import os
import json
import tempfile
import warnings
from datetime import datetime
from types import SimpleNamespace

# Third-Party Imports
import streamlit as st
import requests  # type: ignore[import]
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from astropy.visualization import ZScaleInterval, ImageNormalize

from src.tools_app import (
    initialize_session_state,
    load_fits_data,
    load_tutorial_text,
    plot_magnitude_distribution,
    update_observatory_from_fits_header,
    display_catalog_in_aladin,
    display_archived_files_browser,
    provide_download_buttons,
    clear_all_caches,
    handle_log_messages,
    cleanup_temp_files,
    try_gaia_server,
)
from src.header_utils import select_science_header, copy_header_or_none

# Local Application Imports
from src.tools_pipeline import (
    GAIA_BANDS,
    extract_coordinates,
    extract_pixel_scale,
    safe_wcs_create,
    add_calibrated_magnitudes,
    drop_legacy_magnitude_columns,
    get_filter_prefix,
    merge_photometry_catalogs,
    clean_photometry_table,
)
from src.utils import (
    FIGURE_SIZES,
    PIPELINE_PLOT_MIN_HEIGHT,
    get_base_filename,
    ensure_output_directory,
    initialize_log,
    get_pipeline_figure_size,
    write_to_log,
    zip_results_on_exit,
    save_header_to_txt,
    save_catalog_files,
    save_fits_with_wcs,
)
from src.db_tracking import record_analysis_result

from src.pipeline import (
    calculate_zero_point,
    detection_and_photometry,
    airmass,
)

from src.astrometry import solve_with_astrometrynet
from src.xmatch_catalogs import cross_match_with_gaia, enhance_catalog
from src.transient import find_candidates, plot_astrocolibri_cutouts

from src.__version__ import version

from pages.api_client import ApiClient, ApiError, detect_backend


# Conditional Import (already present, just noting its location)
if getattr(sys, "frozen", False):
    try:
        import importlib.metadata

        importlib.metadata.distributions = lambda **kwargs: []
    except ImportError:
        st.warning(
            "Could not modify importlib.metadata, potential issues in frozen mode."
        )

warnings.filterwarnings("ignore")


def _build_persisted_upload(temp_path: str, file_name: str) -> SimpleNamespace:
    """Return a lightweight file-like wrapper backed by a temp file."""
    tmp = SimpleNamespace()

    def _read():
        with open(temp_path, "rb") as handle:
            return handle.read()

    def _getvalue():
        with open(temp_path, "rb") as handle:
            return handle.read()

    tmp.read = _read
    tmp.getvalue = _getvalue
    tmp.name = file_name
    return tmp

if "backend_status_message" not in st.session_state:
    st.session_state.backend_status_message = "Determining backend..."
if "backend_initialized" not in st.session_state:
    st.session_state.backend_initialized = False
if "api_mode" not in st.session_state:
    st.session_state.api_mode = False
if "run_analysis_pipeline" not in st.session_state:
    st.session_state.run_analysis_pipeline = False
if "backend_mode" not in st.session_state:
    st.session_state.backend_mode = "legacy"
if "api_base_url" not in st.session_state:
    st.session_state.api_base_url = os.getenv(
        "RPP_API_URL",
        "http://localhost:8000",
    )
if "legacy_backend_url" not in st.session_state:
    st.session_state.legacy_backend_url = os.getenv(
        "RPP_LEGACY_URL",
        "http://localhost:5000",
    )

if not st.session_state.backend_initialized:
    backend_info = detect_backend()
    st.session_state.api_base_url = backend_info["api_base_url"]
    st.session_state.legacy_backend_url = backend_info["legacy_backend_url"]
    st.session_state.api_mode = backend_info["mode"] == "api"
    st.session_state.backend_mode = backend_info["mode"]
    st.session_state.backend_status_message = backend_info["message"]
    st.session_state.backend_initialized = True


# ---------------------------------------------------------------------------
# Matomo Analytics Injection
# ---------------------------------------------------------------------------
# We inject the Matomo tracking script via a zero-height hidden iframe.
# Streamlit strips <script> tags from st.markdown(), so st.iframe()
# is the supported workaround. Matomo handles cross-frame tracking
# natively, so page views and link clicks are recorded correctly.

def inject_matomo_tracking(site_id: str = "83",
                           matomo_url: str = "https://analytics.obspm.fr/"):
    """
    Inject the Matomo JS tracker into the Streamlit page.

    Parameters
    ----------
    site_id   : Your Matomo site ID (string, e.g. "83").
    matomo_url: Base URL of your Matomo instance (trailing slash required).
    """
    tracking_code = f"""
    <script>
        var _paq = window._paq = window._paq || [];
        /* Custom dimensions or goals can be pushed here before trackPageView */
        _paq.push(['trackPageView']);
        _paq.push(['enableLinkTracking']);
        (function() {{
            var u = "{matomo_url}";
            _paq.push(['setTrackerUrl', u + 'matomo.php']);
            _paq.push(['setSiteId', '{site_id}']);
            var d = document,
                g = d.createElement('script'),
                s = d.getElementsByTagName('script')[0];
            g.async = true;
            g.src = u + 'matomo.js';
            s.parentNode.insertBefore(g, s);
        }})();
    </script>
    """
    st.iframe(tracking_code, height=1)


st.set_page_config(
    page_title="RAPAS Photometry Pipeline", page_icon=":sparkles:", layout="centered"
)

initialize_session_state()

# Initialize session state for photometry pipeline button
if "run_photometry_pipeline" not in st.session_state:
    st.session_state.run_photometry_pipeline = False

# Redirect to login if not authenticated
if not st.session_state.logged_in:
    st.warning("You must log in to access this page.")
    try:
        st.switch_page("pages/login.py")
    except Exception:
        st.warning(
            "Automatic redirect is not available in this launch mode. "
            "Open the login page directly or start the app with frontend.py."
        )
    st.stop()

# Fire the tracker once per session after successful login
if "matomo_injected" not in st.session_state:
    inject_matomo_tracking(site_id="83", matomo_url="https://analytics.obspm.fr/")
    st.session_state.matomo_injected = True

# Custom CSS to control plot display size and fix sidebar width
st.markdown(
    f"""
<style>
    .stPlot > div {{
        display: flex;
        justify-content: center;
        min-height: {PIPELINE_PLOT_MIN_HEIGHT}px;
    }}
    .main .block-container {{
        max-width: 1200px;
        padding-top: 2rem;
        padding-bottom: 2rem;
    }}
    .element-container img {{
        max-width: 100% !important;
        height: auto !important;
    }}
    [data-testid="stSidebar"] {{
        min-width: 480px !important;
        max-width: 480px !important;
        width: 480px !important;
    }}
    [data-testid="stSidebarResizeHandle"] {{
        display: none !important;
    }}
</style>
""",
    unsafe_allow_html=True,
)

# Add application version to the sidebar
st.title(":sparkles: **RAPAS Photometry Pipeline**")
st.markdown(
    "| [**RAPAS Home**](https://rapas.imcce.fr/) | [<img src='https://github.githubassets.com/favicons/favicon.svg' width='16' style='vertical-align:middle'/> **GitHub**](https://github.com/pierfra-rocci/rpp) |",
    unsafe_allow_html=True,
)
st.caption(st.session_state.backend_status_message)

# Added: Quick Start Tutorial link (now displayed in an expander)
with st.expander("📘 Quick Start Tutorial"):
    tab_en, tab_fr = st.tabs(["🇬🇧 English", "🇫🇷 Français"])
    with tab_en:
        try:
            st.markdown(load_tutorial_text("TUTORIAL.md"), unsafe_allow_html=True)
        except FileNotFoundError:
            st.warning("TUTORIAL.md not found. It should be in the `docs` folder.")
    with tab_fr:
        try:
            st.markdown(
                load_tutorial_text("TUTORIAL_FR.md"),
                unsafe_allow_html=True,
            )
        except FileNotFoundError:
            st.warning("TUTORIAL_FR.md not found. It should be in the `docs` folder.")

st.sidebar.markdown(f"**Version:** _{version}_")
st.sidebar.caption(st.session_state.backend_status_message)

with st.sidebar.expander("🔭 Observatory Data", expanded=False):
    st.session_state.observatory_name = st.text_input(
        "Observatory Name",
        value=st.session_state.observatory_name,
        help="Name of the observatory (e.g., TJMS).",
    )

    # Latitude input with locale handling
    latitude_input = st.text_input(
        "Latitude (deg)",
        value=str(st.session_state.observatory_latitude),
        help="Observatory latitude in decimal degrees (North positive). Accepts both comma and dot as decimal separator.",
    )
    try:
        latitude = float(latitude_input.replace(",", "."))
        if -90 <= latitude <= 90:
            st.session_state.observatory_latitude = latitude
        else:
            st.error("Latitude must be between -90 and 90 degrees")
    except ValueError:
        if latitude_input.strip():  # Only show error if input is not empty
            st.error("Please enter a valid number for latitude.")

    # Longitude input with locale handling
    longitude_input = st.text_input(
        "Longitude (deg)",
        value=str(st.session_state.observatory_longitude),
        help="Observatory longitude in decimal degrees (East positive). Accepts both comma and dot as decimal separator.",
    )
    try:
        longitude = float(longitude_input.replace(",", "."))
        if -180 <= longitude <= 180:
            st.session_state.observatory_longitude = longitude
        else:
            st.error("Longitude must be between -180 and 180 degrees")
    except ValueError:
        if longitude_input.strip():
            st.error("Please enter a valid number for longitude.")

    # Elevation input with locale handling
    elevation_input = st.text_input(
        "Elevation (m)",
        value=str(st.session_state.observatory_elevation),
        help="Observatory elevation above sea level in meters. Accepts both comma and dot as decimal separator.",
    )
    try:
        elevation = float(elevation_input.replace(",", "."))
        st.session_state.observatory_elevation = elevation
    except ValueError:
        if elevation_input.strip():
            st.error("Please enter a valid number for elevation.")

with st.sidebar.expander("⚙️ Analysis Parameters", expanded=False):
    st.session_state.analysis_parameters["seeing"] = st.slider(
        "Estimated FWHM (arcsec)",
        min_value=1.0,
        max_value=6.0,
        value=st.session_state.analysis_parameters["seeing"],
        step=0.5,
        help=(
            "Initial guess for the Full Width at Half Maximum of stars in "
            "arcseconds. Will be refined."
        ),
    )
    _fwhm_rf = st.slider(
        "FWHM Radius Factor",
        min_value=0.5,
        max_value=2.0,
        value=float(st.session_state.analysis_parameters.get("fwhm_radius_factor", 1.5)),
        step=0.1,
        help=(
            "Multiplier applied to the measured FWHM to set the photometry aperture "
            "radius. Values 1.1 and 1.3 are reserved for internal fixed apertures."
        ),
    )
    st.session_state.analysis_parameters["fwhm_radius_factor"] = _fwhm_rf
    if _fwhm_rf in (1.1, 1.3):
        st.warning(
            "FWHM radius factor 1.1 and 1.3 are reserved for existing fixed apertures. "
            "Please choose a different value."
        )
    st.session_state.analysis_parameters["threshold_sigma"] = st.slider(
        "Detection Threshold (sigma)",
        min_value=1.0,
        max_value=6.0,
        value=st.session_state.analysis_parameters["threshold_sigma"],
        step=0.5,
        help=("Source detection threshold in units of background standard deviation."),
    )
    st.session_state.analysis_parameters["detection_mask"] = st.number_input(
        "Border Mask (pixels)",
        min_value=0,
        max_value=200,
        value=st.session_state.analysis_parameters["detection_mask"],
        step=5,
        help=(
            "Size of the border region (in pixels) to exclude from source detection."
        ),
    )
    # Use GAIA_BANDS (list of (label, field)) to present a friendly menu
    try:
        band_labels = [b[0] for b in GAIA_BANDS]
        band_fields = [b[1] for b in GAIA_BANDS]
    except Exception:
        band_labels = [str(b) for b in GAIA_BANDS]
        band_fields = band_labels

    # Determine default selection index based on stored filter field
    current_field = st.session_state.analysis_parameters.get(
        "filter_band", band_fields[0] if band_fields else "phot_g_mean_mag"
    )
    if current_field in band_fields:
        default_index = band_fields.index(current_field)
    else:
        default_index = 0

    selected_label = st.selectbox(
        "Filter Band",
        options=band_labels,
        index=default_index,
        help="Filter Magnitude band used for photometric calibration.",
    )

    # Store the actual catalog field name (second element) in session state
    try:
        selected_field = band_fields[band_labels.index(selected_label)]
    except Exception:
        selected_field = current_field

    st.session_state.analysis_parameters["filter_band"] = selected_field
    st.session_state.analysis_parameters["filter_max_mag"] = 21.0
    st.session_state.analysis_parameters["astrometry_check"] = st.toggle(
        "Astrometry check",
        value=st.session_state.analysis_parameters["astrometry_check"],
        help=("Attempt to plate solve and refine WCS before photometry. "),
    )
    st.session_state.colibri_api_key = st.text_input(
        "Astro-Colibri UID Key",
        value=st.session_state.get("colibri_api_key", ""),
        type="password",
        help="API key for Astro-Colibri query",
    )
    st.session_state.analysis_parameters["run_transient_finder"] = st.checkbox(
        "Enable Transient Finder",
        value=st.session_state.analysis_parameters.get("run_transient_finder", False),
    )

    filter_options = ["g", "r", "i"]
    current_transient_filter = st.session_state.analysis_parameters.get(
        "transient_filter", "r"
    )

    try:
        filter_index = filter_options.index(current_transient_filter)
    except ValueError:
        filter_index = 1  # Default to "r" if stored value is not valid

    st.session_state.analysis_parameters["transient_filter"] = st.selectbox(
        "Reference Filter",
        options=filter_options,
        index=filter_index,
        help="Filter/band for the reference image. Options depend on the selected survey.",
    )

if st.sidebar.button("💾 Save Settings"):
    analysis_params = dict(st.session_state.get("analysis_parameters", {}))
    observatory_params = dict(st.session_state.get("observatory_data", {}))
    colibri_api_key = st.session_state.get("colibri_api_key")

    # Add pre-process options to analysis_parameters
    params = {
        "analysis_parameters": analysis_params,
        "observatory_data": observatory_params,
        "colibri_api_key": colibri_api_key,
    }
    name = st.session_state.get("username", "user")

    mode = st.session_state.get("backend_mode", "legacy")

    if mode == "api":
        creds = st.session_state.get("api_credentials")
        if not creds:
            st.sidebar.warning("Missing API credentials. Please log in again.")
        else:
            client = ApiClient(creds.get("base_url"))
            try:
                client.save_config(
                    username=creds["username"],
                    password=creds["password"],
                    config=params,
                )
                st.sidebar.success("Settings saved")
            except ApiError as exc:
                st.sidebar.warning(f"Could not save config: {exc.message}")
    elif mode == "unavailable":
        st.sidebar.warning(st.session_state.get("backend_status_message"))
    else:
        legacy_url = st.session_state.get(
            "legacy_backend_url",
            "http://localhost:5000",
        )
        try:
            resp = requests.post(
                f"{legacy_url}/save_config",
                json={"username": name, "config_json": json.dumps(params)},
            )
            if resp.status_code != 200:
                st.sidebar.warning(f"Could not save config to DB: {resp.text}")
            else:
                st.sidebar.success("Settings saved.")
        except Exception as e:  # pragma: no cover - network failure
            st.sidebar.warning(f"Could not connect to backend: {e}")

with st.sidebar:
    if st.button("🧹 Reset Analysis"):
        clear_all_caches()

# Add archived files browser to sidebar
with st.sidebar.expander("📁 Archived Analysis", expanded=False):
    username = st.session_state.get("username", "anonymous")
    output_dir = ensure_output_directory(directory=f"{username}_results")
    display_archived_files_browser(output_dir)

# Add logout button at the top right if user is logged in
if st.session_state.logged_in:
    st.sidebar.markdown("")
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Logged in as:** _{st.session_state.username}_")
    if st.session_state.username in ("admin", "rsavalle"):
        if st.sidebar.button("📋 View Pipeline Logs"):
            st.switch_page("pages/showlogs.py")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.session_state.username = None
        st.session_state.api_credentials = None
        st.success("Logged out successfully.")
        try:
            st.switch_page("pages/login.py")
        except Exception:
            st.warning(
                "Automatic redirect is not available in this launch mode. "
                "Open the login page directly or start the app with frontend.py."
            )
        st.stop()
    st.sidebar.markdown("---")
    st.sidebar.markdown(
        "_Need help? Contact_ : [rpp_support](mailto:rpp_support@saf-astronomie.fr)"
    )

# Sidebar footer banner
st.sidebar.markdown(
        "[MIT LICENSE](https://opensource.org/licenses/MIT)")
st.sidebar.markdown(
        "GDPR: for more details, see the [Privacy Policy](https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng)."
    )

###########################################################################

# Persistent uploader: keep temp file path across reruns instead of raw bytes
if "uploaded_temp_path" not in st.session_state:
    uploaded = st.file_uploader(
        "Before uploading the image, check all the parameters on the sidebar. ",
        type=["fits", "fit", "fts", "fits.gz"],
        key="science_uploader",
    )
    if uploaded:
        try:
            suffix = os.path.splitext(uploaded.name)[1]
            system_tmp = tempfile.gettempdir()
            username = st.session_state.get("username", "anonymous")
            user_tmp_dir = os.path.join(system_tmp, username)
            os.makedirs(user_tmp_dir, exist_ok=True)

            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=suffix,
                dir=user_tmp_dir,
            ) as tmp_file:
                tmp_file.write(uploaded.getvalue())
                st.session_state["uploaded_temp_path"] = tmp_file.name

            st.session_state["uploaded_name"] = uploaded.name
            science_file = _build_persisted_upload(
                st.session_state["uploaded_temp_path"],
                uploaded.name,
            )
        except Exception:
            st.error("Failed to persist uploaded file. Please re-upload.")
            st.session_state.pop("uploaded_temp_path", None)
            science_file = None
    else:
        science_file = None
else:
    temp_path = st.session_state.get("uploaded_temp_path")
    if temp_path and os.path.exists(temp_path):
        science_file = _build_persisted_upload(
            temp_path,
            st.session_state.get("uploaded_name", "uploaded.fits"),
        )
    else:
        st.session_state.pop("uploaded_temp_path", None)
        science_file = None

science_file_path = None

# Update observatory_data dictionary with current session state values
st.session_state.observatory_data = {
    "name": st.session_state.observatory_name,
    "latitude": st.session_state.observatory_latitude,
    "longitude": st.session_state.observatory_longitude,
    "elevation": st.session_state.observatory_elevation,
}

# Add button to start the analysis (appears right after image upload)
if science_file is not None:
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if st.button(
            "▶️ Start Analysis",
            key="start_analysis_button",
            use_container_width=True,
            help="Click to start processing."
        ):
            st.session_state.run_analysis_pipeline = True


catalog_name = f"{st.session_state['base_filename']}_catalog.csv"
username = st.session_state.get("username", "anonymous")
output_dir = ensure_output_directory(directory=f"{username}_results")
st.session_state["output_dir"] = output_dir

if st.session_state.get("final_phot_table") is not None:
    provide_download_buttons(output_dir)
    zip_filename, zip_path = zip_results_on_exit(science_file, output_dir)

    # Record in database if ZIP was created and we have a stored FITS file
    if zip_filename and st.session_state.get("stored_fits_filename"):
        try:
            record_analysis_result(
                username=username,
                original_filename=science_file.name,
                stored_fits_filename=st.session_state["stored_fits_filename"],
                zip_archive_filename=zip_filename,
                zip_stored_relpath=zip_path,
                has_wcs=st.session_state.get("has_wcs", True)
            )
        except Exception as db_error:
            # Don't fail the app if database tracking fails
            print(f"Warning: Could not record analysis in database: {db_error}")

# Run the full analysis pipeline only if the button was clicked
if st.session_state.run_analysis_pipeline and science_file is not None:
    persisted_upload_path = st.session_state.get("uploaded_temp_path")
    if persisted_upload_path and os.path.exists(persisted_upload_path):
        science_file_path = persisted_upload_path
    else:
        suffix = os.path.splitext(science_file.name)[1]

        # Get the system temp directory
        system_tmp = tempfile.gettempdir()
        username = st.session_state.get("username", "anonymous")
        user_tmp_dir = os.path.join(system_tmp, username)

        # Create the user-specific temp directory if it doesn't exist
        os.makedirs(user_tmp_dir, exist_ok=True)

        # Now use this directory for the temp file
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, dir=user_tmp_dir
        ) as tmp_file:
            tmp_file.write(science_file.getvalue())
            science_file_path = tmp_file.name

    st.session_state["science_file_path"] = science_file_path
    st.session_state.files_loaded["science_file"] = science_file

    base_filename = get_base_filename(science_file)
    st.session_state["base_filename"] = base_filename
    st.session_state["log_buffer"] = initialize_log(science_file.name)
    st.session_state["log_summary_written"] = False
    st.session_state["last_saved_log_content"] = None

if st.session_state.run_analysis_pipeline and science_file is not None:
    with st.spinner("Loading FITS data..."):
        raw_data, science_header = load_fits_data(science_file)
        science_data = raw_data

    if science_data is not None and science_header is not None:
        write_to_log(
            st.session_state.log_buffer, f"Loaded FITS file: {science_file.name}"
        )

        # Update observatory data from FITS header if available
        result = update_observatory_from_fits_header(science_header)
        if result:
            st.success("Observatory information updated from FITS header")
            write_to_log(
                st.session_state.log_buffer,
                f"Observatory data updated from FITS header: {st.session_state.observatory_data}",
            )

    # Extract filter from header with multiple possible keywords
    filter_raw = None
    for filter_key in ["FILTER", "FILTERS", "FLT",
                       "FILTER1", "FILTRE", "INSFLNAM"]:
        if filter_key in science_header:
            filter_raw = str(science_header[filter_key]).strip().upper()
            break

    if filter_raw is None:
        filter_raw = "Unknown"

    # Map the raw filter name to standardized GAIA band using GAIA_BANDS.
    # Normalize by lowercasing and removing spaces so "RAPAS G" matches "RapasG".
    gaia_band_dict = {label.lower().replace(" ", ""): field for label, field in GAIA_BANDS}
    filter_mapped = gaia_band_dict.get(filter_raw.lower().replace(" ", ""), filter_raw)

    # Get the current selected filter band from analysis parameters
    selected_filter = st.session_state.analysis_parameters["filter_band"]

    # Check if they match (comparing the mapped value)
    if filter_mapped != selected_filter and filter_raw != "Unknown":
        st.info(
            f"Filter in FITS header ({filter_raw}) maps to '{filter_mapped}'."
        )
        write_to_log(
            st.session_state.log_buffer,
            f"Filter matched : Header={filter_raw} (→{filter_mapped}), Selected={selected_filter}",
            level="INFO"
        )
    else:
        write_to_log(
            st.session_state.log_buffer,
            f"Filter from header: {filter_raw} (→{filter_mapped})",
        )

    # Test WCS creation with better error handling
    wcs_obj, wcs_error, log_messages = safe_wcs_create(science_header)
    handle_log_messages(log_messages)
    # Initialize force_plate_solve as False by default
    force_plate_solve = st.session_state.analysis_parameters.get(
        "astrometry_check", False
    )

    # Initialize use_astrometry to track if we ran plate solving
    use_astrometry = False

    # If WCS creation fails due to singular matrix, try to proceed without WCS for detection
    proceed_without_wcs = False
    if wcs_obj is None and "singular" in str(wcs_error).lower():
        st.warning(
            "WCS header has issues: "
            f"{wcs_error}. Will attempt source detection without WCS "
            "and then try plate solving."
        )
        proceed_without_wcs = True
    elif wcs_obj is None:
        st.warning(f"No valid WCS found : {wcs_error}")
        st.write("Attempt plate solving...")
        use_astrometry = True
    else:
        st.success("Valid WCS found.")

        if force_plate_solve:
            write_to_log(
                st.session_state["log_buffer"],
                "Astrometry check enabled - will re-solve astrometry",
            )
            use_astrometry = True
            wcs_obj = None  # Reset to trigger plate solving
        else:
            log_buffer = st.session_state["log_buffer"]
            write_to_log(log_buffer, "Valid WCS found in header")
            proceed_without_wcs = False
            use_astrometry = False

    # Handle plate solving
    if (wcs_obj is None and not proceed_without_wcs) or force_plate_solve:
        use_astrometry = True
        if use_astrometry:
            plate_solve_reason = (
                "forced by user" if force_plate_solve else "no valid WCS found"
            )
            with st.spinner("Running astrometry check and plate solving..."):
                original_science_header = science_header
                wcs_obj, solved_header, log_messages, error = solve_with_astrometrynet(
                    science_file_path
                )
                science_header = select_science_header(
                    original_science_header,
                    solved_header,
                )
                handle_log_messages(log_messages)
                if error:
                    st.error(error)

                if wcs_obj is None:
                    if force_plate_solve:
                        st.error(
                            "Plate solving failed. Will use original WCS if available."
                        )
                        # Try to restore original WCS by reloading header
                        _, original_header = load_fits_data(science_file)
                        wcs_obj, wcs_error, _ = safe_wcs_create(original_header)
                        if wcs_obj is not None:
                            science_header = original_header
                            st.success("Restored original WCS solution")
                        else:
                            proceed_without_wcs = True
                    else:
                        st.error("Plate solving failed. No WCS solution was returned.")
                        proceed_without_wcs = True
                else:
                    st.session_state["calibrated_header"] = science_header
                    st.session_state["wcs_obj"] = wcs_obj

                log_buffer = st.session_state["log_buffer"]

                if wcs_obj is not None:
                    solve_type = "Plate-solve" if force_plate_solve else "Initial solve"
                    st.success(f"{solve_type} successful")
                    write_to_log(
                        log_buffer, f"Astrometry check completed ({plate_solve_reason})"
                    )

                    wcs_header_filename = (
                        f"{st.session_state['base_filename']}_wcs_header"
                    )
                    wcs_header_file_path = save_header_to_txt(
                        science_header, wcs_header_filename, output_dir
                    )
                    if wcs_header_file_path:
                        st.success("Updated WCS header saved")

                    # Save the FITS file with updated WCS header
                    wcs_fits_path, wcs_fits_error, stored_fits_filename = save_fits_with_wcs(
                        science_file_path,
                        science_header,
                        output_dir,
                        filename_suffix="_wcs",
                        also_save_to_data_dir=True,
                        original_filename=science_file.name,
                        username=st.session_state.get("username", "anonymous"),
                    )
                    if wcs_fits_path:
                        st.success("WCS-solved FITS saved "
                        )
                        write_to_log(log_buffer, f"WCS-solved FITS saved to {wcs_fits_path}")
                        st.session_state["stored_fits_filename"] = stored_fits_filename
                        st.session_state["has_wcs"] = True
                    elif wcs_fits_error:
                        st.warning(f"Could not save WCS FITS: {wcs_fits_error}")
                        write_to_log(log_buffer, f"Warning: {wcs_fits_error}", level="WARNING")

                    # Re-extract pixel scale and recalculate seeing with updated header
                    pixel_size_arcsec, pixel_scale_source = extract_pixel_scale(
                        science_header
                    )

                    # If pixel scale extraction failed, calculate from WCS object
                    if pixel_size_arcsec == 0 or not np.isfinite(pixel_size_arcsec):
                        try:
                            from stdpipe import astrometry

                            pixel_size_arcsec = (
                                astrometry.get_pixscale(wcs=wcs_obj) * 3600
                            )
                            pixel_scale_source = "WCS calculation"
                            write_to_log(
                                log_buffer,
                                f"Calculated pixel scale from WCS: {pixel_size_arcsec:.3f} arcsec/pixel",
                            )
                        except Exception as e:
                            write_to_log(
                                log_buffer,
                                f"ERROR: Could not calculate pixel scale: {e}",
                                level="ERROR",
                            )
                            pixel_size_arcsec = 1.0  # Fallback value
                            pixel_scale_source = "fallback default"

                    seeing = st.session_state.analysis_parameters["seeing"]
                    mean_fwhm_pixel = seeing / pixel_size_arcsec

                    # Update session state with the new values
                    st.session_state["pixel_size_arcsec"] = pixel_size_arcsec
                    st.session_state["mean_fwhm_pixel"] = mean_fwhm_pixel

                    write_to_log(
                        log_buffer,
                        f"Updated pixel scale: {pixel_size_arcsec:.2f} arcsec/pixel ({pixel_scale_source})",
                    )
                    write_to_log(
                        log_buffer,
                        f"Updated seeing FWHM: {seeing:.2f} arcsec ({mean_fwhm_pixel:.2f} pixels)",
                    )
                else:
                    st.error("Plate solving failed")
                    write_to_log(
                        log_buffer,
                        "Failed",
                        level="ERROR",
                    )
                    proceed_without_wcs = True

    # Store whether we're proceeding without WCS
    st.session_state["proceed_without_wcs"] = proceed_without_wcs
    if proceed_without_wcs:
        st.warning(
            "Proceeding without valid WCS - photometry will be limited to instrumental magnitudes"
        )
    elif not use_astrometry:
        # Extract pixel scale from existing header only if we didn't run plate solving
        # (plate solving already updated these values in session state)
        pixel_size_arcsec, pixel_scale_source = extract_pixel_scale(science_header)
        seeing = st.session_state.analysis_parameters["seeing"]
        mean_fwhm_pixel = seeing / pixel_size_arcsec

        # Update session state with the calculated values
        st.session_state["pixel_size_arcsec"] = pixel_size_arcsec
        st.session_state["mean_fwhm_pixel"] = mean_fwhm_pixel

    if science_header is not None:
        log_buffer = st.session_state["log_buffer"]

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        header_filename = f"{st.session_state['base_filename']}_header"
        header_file_path = os.path.join(output_dir, f"{header_filename}.txt")

        header_file = save_header_to_txt(science_header, header_filename, output_dir)
        if header_file:
            write_to_log(log_buffer, f"Saved header to {header_file}")

    log_buffer = st.session_state["log_buffer"]
    write_to_log(log_buffer, f"Loaded Image: {science_file.name}")

    if science_data is not None:
        st.header("Image", anchor="science-image")
        try:
            # Create a side-by-side plot using matplotlib subplots
            fig, (ax1, ax2) = plt.subplots(
                1,
                2,
                figsize=get_pipeline_figure_size(FIGURE_SIZES["medium"]),
            )

            ax1.set_title("ZScale Visualization")
            try:
                norm = ImageNormalize(science_data, interval=ZScaleInterval())
                im1 = ax1.imshow(
                    science_data, norm=norm, origin="lower", cmap="viridis"
                )
            except Exception as norm_error:
                st.warning(
                    f"ZScale normalization failed: {norm_error}. Using simple normalization."
                )
                vmin, vmax = np.percentile(science_data, [1, 99])
                im1 = ax1.imshow(
                    science_data, vmin=vmin, vmax=vmax, origin="lower", cmap="viridis"
                )
            fig.colorbar(im1, ax=ax1, label="Pixel Value")
            ax1.axis("off")

            # Histogram Equalization
            ax2.set_title("Histogram Equalization")
            try:
                data_finite = science_data[np.isfinite(science_data)]
                if len(data_finite) > 0:
                    vmin, vmax = np.percentile(data_finite, [0.5, 99.5])
                    data_scaled = np.clip(science_data, vmin, vmax)
                    data_scaled = (data_scaled - vmin) / (vmax - vmin)
                    im2 = ax2.imshow(data_scaled, origin="lower", cmap="viridis")
                    fig.colorbar(im2, ax=ax2, label="Normalized Value")
                    ax2.axis("off")

                    ax_inset = fig.add_axes([0.65, 0.15, 0.15, 0.25])
                    ax_inset.hist(
                        data_scaled.flatten(), bins=50, color="skyblue", alpha=0.8
                    )
                    ax_inset.set_title("Pixel Distribution", fontsize=8)
                    ax_inset.tick_params(axis="both", which="major", labelsize=6)
                else:
                    st.warning(
                        "No finite values in image data for histogram equalization"
                    )
            except Exception as hist_error:
                st.warning(f"Histogram equalization failed: {hist_error}")

            fig.suptitle(f"{science_file.name}")
            st.pyplot(fig)

            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            image_filename = f"{st.session_state['base_filename']}_image.png"
            image_path = os.path.join(output_dir, image_filename)

            try:
                fig.savefig(image_path, dpi=120, bbox_inches="tight")
                write_to_log(log_buffer, "Saved image plot")
            except Exception as save_error:
                write_to_log(
                    log_buffer,
                    f"Failed to save image plot: {str(save_error)}",
                    level="ERROR",
                )
                st.error(f"Error saving image: {str(save_error)}")

            plt.close(fig)

        except Exception as e:
            st.error(f"Error displaying image: {str(e)}")
            st.exception(e)

        with st.expander("Image Header"):
            if science_header:
                st.text(repr(science_header))
            else:
                st.warning("No header information available for Image.")

    st.subheader("Statistics")
    if science_data is not None:
        st.write(
            "Mean: ",
            f"{np.mean(science_data):.2f}",
        )
        st.write("Median: ", f"{np.median(science_data):.2f}")
        st.write("Rms: ", f"{np.std(science_data):.3f}")
        st.write("Min: ", f"{np.min(science_data):.2f}")
        st.write("Max: ", f"{np.max(science_data):.2f}")

        # Write statistics to log
        write_to_log(log_buffer, "Image Statistics", level="INFO")
        write_to_log(log_buffer, f"Mean: {np.mean(science_data):.2f}")
        write_to_log(log_buffer, f"Median: {np.median(science_data):.2f}")
        write_to_log(log_buffer, f"RMS: {np.std(science_data):.3f}")
        write_to_log(log_buffer, f"Min: {np.min(science_data):.2f}")
        write_to_log(log_buffer, f"Max: {np.max(science_data):.2f}")

        # Use updated header if available, otherwise use original
        header_for_stats = st.session_state.get("calibrated_header", science_header)

        if (
            "pixel_size_arcsec" in st.session_state
            and "mean_fwhm_pixel" in st.session_state
        ):
            pixel_size_arcsec = st.session_state["pixel_size_arcsec"]
            mean_fwhm_pixel = st.session_state["mean_fwhm_pixel"]
            pixel_scale_source = "session_state"
        else:
            pixel_size_arcsec, pixel_scale_source = extract_pixel_scale(
                header_for_stats
            )
            seeing = st.session_state.analysis_parameters["seeing"]
            mean_fwhm_pixel = seeing / pixel_size_arcsec

            st.session_state["pixel_size_arcsec"] = pixel_size_arcsec
            st.session_state["mean_fwhm_pixel"] = mean_fwhm_pixel

        st.write(
            "Mean Pixel Scale (arcsec): ",
            f"{pixel_size_arcsec:.2f}",
        )
        write_to_log(
            log_buffer,
            f"Final pixel scale: {pixel_size_arcsec:.2f} arcsec/pixel ({pixel_scale_source})",
        )
        seeing = st.session_state.analysis_parameters["seeing"]
        st.write("Estimated FWHM (pixels): ", f"{mean_fwhm_pixel:.2f}")
        write_to_log(
            log_buffer,
            f"Final Estimated FWHM: {seeing:.2f} arcsec ({mean_fwhm_pixel:.2f} pixels)",
        )

        ra_val, dec_val, coord_source = extract_coordinates(science_header)
        if ra_val is not None and dec_val is not None:
            from astropy.coordinates import SkyCoord
            import astropy.units as u
            coord = SkyCoord(ra=ra_val, dec=dec_val, unit="deg")
            ra_sex = coord.ra.to_string(unit=u.hour, sep=":", precision=2, pad=True)
            dec_sex = coord.dec.to_string(sep=":", precision=1, alwayssign=True, pad=True)
            st.write(
                f"RA={round(ra_val, 4)}° ({ra_sex})\n"
                f"DEC={round(dec_val, 4)}° ({dec_sex})"
            )
            write_to_log(
                log_buffer,
                f"Target coordinates: RA={ra_val}° ({ra_sex}), DEC={dec_val}° ({dec_sex}) ({coord_source})",
            )
            ra_missing = dec_missing = False
            ra_val = dec_val = None  # Add this line
        else:
            st.warning(f"Coordinate issue: {coord_source}")
            write_to_log(
                log_buffer, f"Coordinate issue: {coord_source}", level="WARNING"
            )
            ra_missing = dec_missing = True

        if "manual_ra" not in st.session_state:
            st.session_state["manual_ra"] = ""
        if "manual_dec" not in st.session_state:
            st.session_state["manual_dec"] = ""

        if ra_missing or dec_missing:
            st.warning(
                "Target coordinates (RA/DEC) not found in FITS header. Please enter them manually:"
            )

            coord_col1, coord_col2 = st.columns(2)

            with coord_col1:
                default_ra = st.session_state["manual_ra"]
                if (
                    not default_ra
                    and science_header is not None
                    and "NAXIS1" in science_header
                    and "CRPIX1" in science_header
                    and "CD1_1" in science_header
                ):
                    default_ra = str(science_header.get("CRVAL1", ""))

                manual_ra = st.text_input(
                    "Right Ascension (degrees)",
                    value=default_ra,
                    help="Enter RA in decimal degrees (0-360)",
                    key="ra_input",
                )
                st.session_state["manual_ra"] = manual_ra

            with coord_col2:
                default_dec = st.session_state["manual_dec"]
                if (
                    not default_dec
                    and science_header is not None
                    and "NAXIS2" in science_header
                    and "CRPIX2" in science_header
                    and "CD2_2" in science_header
                ):
                    default_dec = str(science_header.get("CRVAL2", ""))

                manual_dec = st.text_input(
                    "Declination (degrees)",
                    value=default_dec,
                    help="Enter DEC in decimal degrees (-90 to +90)",
                    key="dec_input",
                )
                st.session_state["manual_dec"] = manual_dec

            if manual_ra and manual_dec:
                try:
                    ra_val = float(manual_ra)
                    dec_val = float(manual_dec)

                    if not (0 <= ra_val < 360):
                        st.error("RA must be between 0 and 360 degrees")
                    elif not (-90 <= dec_val <= 90):
                        st.error("DEC must be between -90 and +90 degrees")
                    else:
                        if science_header is not None:
                            science_header["RA"] = ra_val
                            science_header["DEC"] = dec_val

                        st.session_state["valid_ra"] = ra_val
                        st.session_state["valid_dec"] = dec_val

                        st.success(
                            f"Using manual coordinates: RA={ra_val}°, DEC={dec_val}°"
                        )
                except ValueError:
                    st.error("RA and DEC must be valid numbers")
            else:
                st.warning("Please enter both RA and DEC coordinates")
        else:
            if science_header is not None:
                for ra_key in ["RA", "OBJRA", "RA---", "CRVAL1"]:
                    if ra_key in science_header:
                        st.session_state["valid_ra"] = science_header[ra_key]
                        break

                for dec_key in ["DEC", "OBJDEC", "DEC---", "CRVAL2"]:
                    if dec_key in science_header:
                        st.session_state["valid_dec"] = science_header[dec_key]
                        break

        if (
            "valid_ra" in st.session_state
            and "valid_dec" in st.session_state
            and science_header is not None
        ):
            science_header["RA"] = st.session_state["valid_ra"]
            science_header["DEC"] = st.session_state["valid_dec"]

        try:
            # Create observatory dictionary using user inputs
            observatory_data = {
                "name": st.session_state.observatory_data["name"],
                "latitude": st.session_state.observatory_data["latitude"],
                "longitude": st.session_state.observatory_data["longitude"],
                "elevation": st.session_state.observatory_data["elevation"],
            }

            air = airmass(science_header, observatory=observatory_data)
            st.write(f"Airmass : {air:.2f}")
        except Exception as e:
            st.warning(f"Error calculating airmass: {e}")
            air = 0.0
            st.write(f"Using default airmass: {air:.2f}")

        image_to_process = science_data
        header_to_process = copy_header_or_none(science_header)

        # Ensure RA/DEC are in the header for catalog queries
        if header_to_process is not None and (
            "RA" not in header_to_process or "DEC" not in header_to_process
        ):
            # Try to get from session state (validated coordinates)
            if "valid_ra" in st.session_state and "valid_dec" in st.session_state:
                header_to_process["RA"] = st.session_state["valid_ra"]
                header_to_process["DEC"] = st.session_state["valid_dec"]
                write_to_log(
                    log_buffer,
                    f"Set header RA/DEC from validated coordinates: RA={header_to_process['RA']:.4f}°, DEC={header_to_process['DEC']:.4f}°"
                )
            # Fallback to CRVAL1/CRVAL2 if available
            elif "CRVAL1" in header_to_process and "CRVAL2" in header_to_process:
                header_to_process["RA"] = header_to_process["CRVAL1"]
                header_to_process["DEC"] = header_to_process["CRVAL2"]
                write_to_log(
                    log_buffer,
                    f"Set header RA/DEC from CRVAL: RA={header_to_process['RA']:.4f}°, DEC={header_to_process['DEC']:.4f}°"
                )

        # Initialize zero_point variables to avoid undefined variable errors later
        zero_point_value = None
        zero_point_std = None

        # Validate that we have a header
        if header_to_process is None:
            st.error(
                "No header available for processing. Cannot proceed with photometry."
            )
            st.stop()

        # Get the correct pixel scale and FWHM values
        if (
            "pixel_size_arcsec" in st.session_state
            and "mean_fwhm_pixel" in st.session_state
        ):
            pixel_size_arcsec = st.session_state["pixel_size_arcsec"]
            mean_fwhm_pixel = st.session_state["mean_fwhm_pixel"]
        else:
            pixel_size_arcsec, _ = extract_pixel_scale(header_to_process)
            seeing = st.session_state.analysis_parameters["seeing"]
            mean_fwhm_pixel = seeing / pixel_size_arcsec

        if image_to_process is not None:
            try:
                with st.spinner(
                    "Background Extraction, FWHM Computation, Sources Detection and Photometry..."
                ):
                    # Extract parameters from session state
                    threshold_sigma = st.session_state.analysis_parameters[
                        "threshold_sigma"
                    ]
                    fwhm_radius_factor = st.session_state.analysis_parameters.get(
                        "fwhm_radius_factor", 1.5
                    )
                    detection_mask = st.session_state.analysis_parameters[
                        "detection_mask"
                    ]
                    filter_band = st.session_state.analysis_parameters["filter_band"]
                    filter_max_mag = 21.0

                    result = detection_and_photometry(
                        image_to_process,
                        header_to_process,
                        mean_fwhm_pixel,
                        threshold_sigma,
                        detection_mask,
                        fwhm_radius_factor,
                    )

                    if isinstance(result, tuple) and len(result) == 8:
                        (
                            phot_table_qtable,
                            epsf_table,
                            daofind,
                            bkg,
                            w,
                            bkg_fig,
                            fwhm_estimate,
                            mask,
                        ) = result
                    else:
                        st.error(
                            f"detection_and_photometry returned unexpected result: {type(result)}, length: {len(result) if hasattr(result, '__len__') else 'N/A'}"
                        )
                        phot_table_qtable = epsf_table = daofind = bkg = w = bkg_fig = (
                            None
                        )

                    if phot_table_qtable is not None:
                        phot_table_df = phot_table_qtable.to_pandas().copy(deep=True)

                        # Ensure RA/Dec columns are present
                        if (
                            "ra" not in phot_table_df.columns
                            or "dec" not in phot_table_df.columns
                        ):
                            st.warning(
                                "RA/Dec columns not found in photometry table, attempting to add them..."
                            )
                            if w is not None:
                                try:
                                    ra, dec = w.pixel_to_world_values(
                                        phot_table_df["xcenter"],
                                        phot_table_df["ycenter"],
                                    )
                                    phot_table_df["ra"] = ra
                                    phot_table_df["dec"] = dec
                                    st.success("Added RA/Dec coordinates from WCS")
                                except Exception as e:
                                    st.error(f"Could not add RA/Dec coordinates: {e}")
                    else:
                        st.error("No sources detected in the image.")
                        phot_table_df = None

                if phot_table_df is not None:
                    with st.spinner("Cross-matching with Catalogs for Zero Point..."):
                        matched_table, log_messages = cross_match_with_gaia(
                            phot_table_qtable,
                            header_to_process,
                            pixel_size_arcsec,
                            mean_fwhm_pixel,
                            filter_band,
                            filter_max_mag,
                            refined_wcs=w,
                        )
                        handle_log_messages(log_messages)

                    if matched_table is not None:
                        st.subheader("Cross-matched Catalog (first 10 rows)")
                        st.dataframe(matched_table.head(10))

                        with st.spinner("Calculating zero point..."):
                            zero_point_value, zero_point_std, zp_plot = (
                                calculate_zero_point(
                                    phot_table_qtable,
                                    matched_table,
                                    filter_band,
                                    air,
                                    fwhm_radius_factor,
                                )
                            )

                            if zero_point_value is not None:
                                write_to_log(
                                    log_buffer,
                                    f"Zero point: {zero_point_value:.2f} ± {zero_point_std:.2f}",
                                )
                                write_to_log(log_buffer, f"Airmass: {air:.2f}")

                                try:
                                    if (
                                        "epsf_photometry_result" in st.session_state
                                        and epsf_table is not None
                                    ):
                                        # Convert tables to pandas DataFrames
                                        epsf_df = (
                                            epsf_table.to_pandas()
                                            if not isinstance(epsf_table, pd.DataFrame)
                                            else epsf_table
                                        )
                                        final_table = phot_table_df.copy()

                                        # Merge keeping all sources
                                        final_table, log_messages = (
                                            merge_photometry_catalogs(
                                                aperture_table=final_table,
                                                psf_table=epsf_df,
                                                tolerance_pixels=2.0,
                                            )
                                        )
                                        handle_log_messages(log_messages)

                                        # Add calibrated magnitudes
                                        final_table = add_calibrated_magnitudes(
                                            final_table,
                                            zero_point=zero_point_value,
                                            airmass=air,
                                            filter_band=filter_band,
                                        )

                                        # Add metadata
                                        final_table["zero_point"] = zero_point_value
                                        final_table["zero_point_std"] = zero_point_std
                                        final_table["airmass"] = air

                                        # Clean the table
                                        final_table, log_messages = (
                                            clean_photometry_table(
                                                final_table, require_magnitude=True
                                            )
                                        )
                                        handle_log_messages(log_messages)

                                        display_table = drop_legacy_magnitude_columns(
                                            final_table.copy(), filter_band
                                        )

                                        # Save user-facing table to session state
                                        st.session_state["final_phot_table"] = (
                                            display_table
                                        )
                                        st.success(
                                            f"Catalog includes {len(final_table)} sources (with PSF photometry)."
                                        )
                                    else:
                                        # No PSF photometry - use aperture photometry only
                                        st.warning(
                                            "Using aperture photometry only (PSF photometry not available)."
                                        )
                                        final_table = phot_table_df.copy()

                                        # Add calibrated magnitudes
                                        final_table = add_calibrated_magnitudes(
                                            final_table,
                                            zero_point=zero_point_value,
                                            airmass=air,
                                            filter_band=filter_band,
                                        )

                                        # Add metadata
                                        final_table["zero_point"] = zero_point_value
                                        final_table["zero_point_std"] = zero_point_std
                                        final_table["airmass"] = air

                                        # Clean the table
                                        final_table, log_messages = (
                                            clean_photometry_table(
                                                final_table, require_magnitude=True
                                            )
                                        )
                                        handle_log_messages(log_messages)

                                        display_table = drop_legacy_magnitude_columns(
                                            final_table.copy(), filter_band
                                        )

                                        # Save user-facing table to session state
                                        st.session_state["final_phot_table"] = (
                                            display_table
                                        )
                                        st.success(
                                            f"Catalog includes {len(final_table)} sources (aperture photometry only)."
                                        )

                                except Exception as e:
                                    st.error(f"Error merging photometry: {e}")
                                    import traceback

                                    st.code(traceback.format_exc())

                                st.subheader("Final Photometry Catalog")
                                st.dataframe(display_table.head(10))

                                st.success(
                                    f"Catalog includes {len(final_table)} sources."
                                )

                                st.subheader("Magnitude Distribution (Aperture & PSF)")

                                fig_mag = plot_magnitude_distribution(
                                    display_table, log_buffer
                                )
                                st.pyplot(fig_mag)

                                try:
                                    base_filename = st.session_state.get(
                                        "base_filename", "photometry"
                                    )
                                    username = st.session_state.get(
                                        "username", "anonymous"
                                    )
                                    output_dir = ensure_output_directory(
                                        directory=f"{username}_results"
                                    )
                                    hist_filename = f"{base_filename}_histogram_mag.png"
                                    hist_filepath = os.path.join(
                                        output_dir, hist_filename
                                    )
                                    fig_mag.savefig(
                                        hist_filepath,
                                        dpi=120,
                                        bbox_inches="tight",
                                    )
                                    write_to_log(
                                        log_buffer,
                                        f"Saved magnitude histogram plot: {hist_filename}",
                                    )
                                except Exception as e:
                                    st.warning(
                                        f"Could not save magnitude histogram plot: {e}"
                                    )

                                # Clean up the figure
                                plt.close(fig_mag)

                                # Calculate search radius for both catalog enhancement and transient finding
                                try:
                                    search_radius = (
                                        max(
                                            header_to_process["NAXIS1"],
                                            header_to_process["NAXIS2"],
                                        )
                                        * pixel_size_arcsec
                                        / 2.0
                                    )
                                except Exception as e:
                                    st.warning(
                                        f"Could not calculate search radius from header: {e}"
                                    )
                                    # Fallback: assume 1800 arcsec (0.5 degrees) as reasonable default
                                    search_radius = 1800.0

                                if (
                                    final_table is not None
                                    and "ra" in final_table.columns
                                    and "dec" in final_table.columns
                                ):
                                    st.subheader(
                                        "Cross-matching with Astronomical Catalogs"
                                    )
                                    # Get colibri API key from session state
                                    colibri_api_key = st.session_state.get(
                                        "colibri_api_key", ""
                                    )
                                    # Ensure final_table exists
                                    if final_table is None:
                                        st.error(
                                            "Final photometry table is None - cannot perform catalog enhancement"
                                        )
                                        final_table = st.session_state.get(
                                            "final_phot_table"
                                        )

                                    if final_table is not None and len(final_table) > 0:
                                        final_table, log_messages = enhance_catalog(
                                            colibri_api_key,
                                            final_table,
                                            matched_table,
                                            header_to_process,
                                            pixel_size_arcsec,
                                            search_radius_arcsec=search_radius,
                                        )
                                        handle_log_messages(log_messages)
                                elif final_table is not None:
                                    st.warning(
                                        "RA/DEC coordinates not available for catalog cross-matching"
                                    )
                                else:
                                    st.error(
                                        "Final photometry table is None - cannot perform cross-matching"
                                    )

                                # Call the new function here
                                final_table_export = drop_legacy_magnitude_columns(
                                    final_table.copy(), filter_band
                                )
                                st.session_state["final_phot_table"] = final_table_export
                                success_messages, error_messages = save_catalog_files(
                                    final_table_export, base_filename, output_dir
                                )
                                for msg in success_messages:
                                    st.success(msg)
                                for msg in error_messages:
                                    st.error(msg)

            except Exception as e:
                st.error(f"Error during zero point calibration: {str(e)}")
                st.exception(e)

            # Calculate search radius for catalog enhancement and transient finding
            # This needs to be outside the try-except block so it's available for transient finder
            try:
                search_radius = (
                    max(
                        header_to_process["NAXIS1"],
                        header_to_process["NAXIS2"],
                    )
                    * pixel_size_arcsec
                    / 2.0
                )
            except Exception as e:
                st.warning(
                    f"Could not calculate search radius: {e}. Using default value."
                )
                search_radius = 1800.0  # Default 0.5 degrees in arcseconds

            ra_center = None
            dec_center = None

            # Use the correct header - prioritize calibrated header if available
            header_for_coords = st.session_state.get(
                "calibrated_header", science_header
            )
            if header_for_coords is None:
                header_for_coords = science_header

            coord_keys = [("CRVAL1", "CRVAL2"), ("RA", "DEC"), ("OBJRA", "OBJDEC")]

            for ra_key, dec_key in coord_keys:
                if (
                    header_for_coords is not None
                    and ra_key in header_for_coords
                    and dec_key in header_for_coords
                ):
                    try:
                        ra_center = float(header_for_coords[ra_key])
                        dec_center = float(header_for_coords[dec_key])
                        if ra_center is not None and dec_center is not None:
                            break
                    except (ValueError, TypeError):
                        continue

            # Fallback to session state coordinates if header failed
            if ra_center is None or dec_center is None:
                ra_center = st.session_state.get("valid_ra")
                dec_center = st.session_state.get("valid_dec")

            if ra_center is not None and dec_center is not None:
                # Check if we have a valid final photometry table
                final_phot_table = st.session_state.get("final_phot_table")

                # Show Astro-Colibri match cutouts (always, independent of transient finder)
                if final_phot_table is not None and not final_phot_table.empty:
                    with st.spinner("Generating Astro-Colibri match cutouts..."):
                        try:
                            plot_astrocolibri_cutouts(
                                final_table=final_phot_table,
                                image=science_data,
                                header=st.session_state.get(
                                    "calibrated_header", science_header
                                ),
                                output_dir=output_dir,
                                base_filename=st.session_state.get(
                                    "base_filename", "photometry"
                                ),
                                filter_name=st.session_state.analysis_parameters.get(
                                    "transient_filter", "r"
                                ),
                                dec_center=dec_center,
                            )
                        except Exception as e:
                            st.warning(
                                f"Could not generate Astro-Colibri cutouts: {e}"
                            )

                # Run Transient Finder if enabled
                if st.session_state.analysis_parameters.get("run_transient_finder"):
                    if (
                        "science_file_path" in st.session_state
                        and st.session_state["science_file_path"]
                    ):
                        with st.spinner(
                            "Running Transient Finder ... This may take a moment."
                        ):
                            try:
                                candidates = find_candidates(
                                    final_phot_table,
                                    zero_point_value,
                                    science_data,
                                    st.session_state["calibrated_header"],
                                    fwhm_estimate,
                                    pixel_size_arcsec,
                                    ra_center,
                                    dec_center,
                                    search_radius / 3600,
                                    mask=mask,
                                    filter_cat=st.session_state.analysis_parameters.get(
                                        "filter_band"
                                    ),
                                    filter_name=st.session_state.analysis_parameters.get(
                                        "transient_filter", "r"
                                    ),
                                )
                                if candidates:
                                    st.subheader("Transient Candidates Found")
                                    # Create a DataFrame from the candidates
                                    import pandas as pd

                                    transient_filter_band = (
                                        st.session_state.analysis_parameters.get(
                                            "filter_band"
                                        )
                                    )
                                    transient_filter_prefix = get_filter_prefix(
                                        transient_filter_band
                                    )
                                    transient_mag_col = (
                                        f"{transient_filter_prefix}_psf_mag"
                                        if transient_filter_prefix
                                        else "psf_mag"
                                    )
                                    transient_mag_err_col = (
                                        f"{transient_filter_prefix}_psf_mag_err"
                                        if transient_filter_prefix
                                        else "psf_mag_err"
                                    )

                                    candidates_df = pd.DataFrame(
                                        {
                                            "ID": range(1, len(candidates) + 1),
                                            "RA (deg)": [c["ra"] for c in candidates],
                                            "DEC (deg)": [c["dec"] for c in candidates],
                                            transient_mag_col: [
                                                c.get("mag_calib", None)
                                                for c in candidates
                                            ],
                                            transient_mag_err_col: [
                                                c.get("mag_calib_err", None)
                                                for c in candidates
                                            ],
                                            "X (pix)": [
                                                c.get("x", None) for c in candidates
                                            ],
                                            "Y (pix)": [
                                                c.get("y", None) for c in candidates
                                            ],
                                        }
                                    )
                                    # Display the DataFrame in Streamlit
                                    st.dataframe(
                                        candidates_df, use_container_width=True
                                    )

                                    # Save the DataFrame to CSV with base_filename
                                    base_filename = st.session_state.get(
                                        "base_filename", "transients"
                                    )
                                    transients_csv_path = os.path.join(
                                        output_dir, f"{base_filename}_transients.csv"
                                    )
                                    candidates_df.to_csv(
                                        transients_csv_path, index=False
                                    )
                                    st.success("Transient candidates saved")
                                else:
                                    st.warning("No transient candidates found.")
                            except Exception as e:
                                st.error(f"Error during transient finding: {str(e)}")

                if (
                    final_phot_table is not None
                    and not final_phot_table.empty
                    and len(final_phot_table) > 0
                ):
                    st.subheader("Aladin Catalog Viewer")

                    try:
                        display_catalog_in_aladin(
                            final_table=final_phot_table,
                            ra_center=ra_center,
                            dec_center=dec_center,
                            filter_band=st.session_state.analysis_parameters.get(
                                "filter_band"
                            ),
                            id_cols=[
                                "id",
                                "simbad_main_id",
                                "skybot_NAME",
                                "aavso_Name",
                                "gaia_source_id",
                                "astrocolibri_name",
                                "qso_name",
                            ],
                        )
                    except Exception as e:
                        st.error(f"Error displaying Aladin viewer: {str(e)}")
                        st.warning(
                            "Catalog data is available but cannot be displayed in interactive viewer."
                        )

                st.link_button(
                    "ESA Sky Viewer",
                    f"https://sky.esa.int/esasky/?target={ra_center}%20{dec_center}&hips=DSS2+color&fov=1.0&projection=SIN&cooframe=J2000&sci=true&lang=en",
                    help="Open ESA Sky with the same target coordinates",
                )

                st.link_button(
                    "SIMBAD",
                    f"https://simbad.u-strasbg.fr/simbad/sim-coo?Coord={ra_center}+{dec_center}&Radius=5&Radius.unit=arcmin",
                    help="Open SIMBAD at CDS Strasbourg for these coordinates",
                )

                st.link_button(
                    "XMatch",
                    f"https://cdsxmatch.u-strasbg.fr/xmatch?request=doQuery&RA={ra_center}&DEC={dec_center}&radius=5",
                    help="Open CDS XMatch service for these coordinates",
                )

                # Only provide download buttons if processing was completed
                if final_phot_table is not None:
                    provide_download_buttons(output_dir)
                    cleanup_temp_files()
                    zip_filename, zip_path = zip_results_on_exit(science_file, output_dir)
                    
                    # Record in database if ZIP was created and we have a stored FITS file
                    if zip_filename and st.session_state.get("stored_fits_filename"):
                        try:
                            record_analysis_result(
                                username=st.session_state.get("username", "anonymous"),
                                original_filename=science_file.name,
                                stored_fits_filename=st.session_state["stored_fits_filename"],
                                zip_archive_filename=zip_filename,
                                zip_stored_relpath=zip_path,
                                has_wcs=st.session_state.get("has_wcs", True)
                            )
                        except Exception as db_error:
                            # Don't fail the app if database tracking fails
                            print(f"Warning: Could not record analysis in database: {db_error}")
            else:
                st.warning(
                    "Could not determine coordinates from image header. Cannot display ESASky or Aladin Viewer."
                )

            # Reset the button flag at the end of successful pipeline execution
            st.session_state.run_analysis_pipeline = False
else:
    # Try connecting to GAIA server once per session.
    if not st.session_state.get("gaia_server_checked", False):
        st.session_state["gaia_server_available"] = try_gaia_server()
        st.session_state["gaia_server_checked"] = True

if "log_buffer" in st.session_state and st.session_state["log_buffer"] is not None:
    log_buffer = st.session_state["log_buffer"]
    log_filename = f"{st.session_state['base_filename']}.log"
    log_filepath = os.path.join(output_dir, log_filename)

    if not st.session_state.get("log_summary_written", False):
        # Get parameters from session state
        filter_band = st.session_state.analysis_parameters["filter_band"]
        filter_max_mag = 21.0

        write_to_log(
            log_buffer,
            f"Elevation: {st.session_state.observatory_data['elevation']} m",
        )

        write_to_log(log_buffer, "Gaia Parameters", level="INFO")
        write_to_log(log_buffer, f"Gaia Band: {filter_band}")
        write_to_log(log_buffer, f"Gaia Max Magnitude: {filter_max_mag}")

        write_to_log(log_buffer, "Calibration Options", level="INFO")
        write_to_log(log_buffer, "Processing completed", level="INFO")
        st.session_state["log_summary_written"] = True

    # Save the log only if content changed.
    log_content = log_buffer.getvalue()
    if st.session_state.get("last_saved_log_content") != log_content:
        with open(log_filepath, "w", encoding="utf-8") as f:
            f.write(log_content)
        st.session_state["last_saved_log_content"] = log_content
        write_to_log(log_buffer, f"Log saved to {log_filepath}")
