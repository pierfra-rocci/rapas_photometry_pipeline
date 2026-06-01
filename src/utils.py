# Standard Library Imports
import os
import json
import zipfile
from io import StringIO
from datetime import datetime

# Third-Party Imports
import requests
import matplotlib.pyplot as plt
from astropy.io import fits
from astropy.table import Table
from astropy.io.votable import from_table, writeto

# Constants
FIGURE_SIZES = {
    "small": (6, 5),  # For small plots
    "medium": (8, 6),  # For medium plots
    "large": (10, 8),  # For large plots
    "wide": (12, 6),  # For wide plots
    "stars_grid": (10, 8),  # For grid of stars
}

PIPELINE_IMAGE_SCALE = 0.75
PIPELINE_PLOT_MIN_HEIGHT = 320


def get_pipeline_figure_size(base_size):
    """Return a smaller display-only size for pipeline image figures."""
    return (
        2 * base_size[0] * PIPELINE_IMAGE_SCALE,
        base_size[1] * PIPELINE_IMAGE_SCALE,
    )


def get_json(url: str):
    """
    Fetch JSON data from a given URL and handle errors gracefully.

    Parameters
    ----------
    url : str
        The URL to fetch JSON data from. Must start with 'http' or 'https'.

    Returns
    -------
    dict or str
        - Parsed JSON data as a Python dictionary if the request is successful
          and the response contains valid JSON.
        - A JSON-formatted string describing the error if the URL is invalid,
          the request fails (network error, timeout, non-2xx status),
          the response is empty, or the response is not valid JSON.
          Example error format: '{"error": "type", "message": "details"}'

    Notes
    -----
    - Handles network errors (requests.exceptions.RequestException) and JSON
      parsing errors (json.decoder.JSONDecodeError).
    - Validates the URL format.
    - Raises HTTPError for bad responses (4xx or 5xx).
    """
    if not url.startswith("http"):
        return json.dumps({"error": "invalid URL"})
    try:
        req = requests.get(url)
        req.raise_for_status()
        if not req.content:
            return json.dumps({"error": "empty response"})
        return req.json()
    except requests.exceptions.RequestException as e:
        return json.dumps({"error": "request exception", "message": str(e)})
    except json.decoder.JSONDecodeError as e:
        return json.dumps({"error": "invalid json", "message": str(e)})


def get_header_value(header, keys, default=None):
    """
    Extract a value from a FITS header by trying multiple possible keywords.

    Iterates through a list of potential keywords and returns the value of the
    first one found in the header. Useful for handling variations in FITS
    keyword conventions.

    Parameters
    ----------
    header : astropy.io.fits.Header or dict-like or None
        The FITS header object or dictionary to search within. If None, the
        default value is returned immediately.
    keys : list[str]
        A list of header keyword strings to try, in order of preference.
    default : any, optional
        The value to return if none of the specified keys are found in the
        header. Defaults to None.

    Returns
    -------
    any
        The value associated with the first key found in the header, or the
        `default` value if none of the keys are present or the header is None.

    Examples
    --------
    >>> from astropy.io import fits
    >>> hdr = fits.Header([('EXPTIME', 120.0), ('INSTRUME', 'CCD')])
    >>> exposure = get_header_value(hdr, ['EXPTIME', 'EXPOSURE', 'EXP'], 0.0)
    >>> print(exposure)
    120.0
    >>> filter_name = get_header_value(hdr, ['FILTER'], 'Unknown')
    >>> print(filter_name)
    Unknown
    """
    if header is None:
        return default

    for key in keys:
        if key in header:
            return header[key]
    return default


def get_base_filename(file_obj):
    """
    Extract the base filename (without extension) from a file object.

    Handles common cases like single extensions (.fits) and double extensions
    (.fits.fz, .tar.gz).

    Parameters
    ----------
    file_obj : file-like object or None
        An object representing the uploaded file, typically expected to have a
        `.name` attribute (like Streamlit's `UploadedFile`). If None, returns
        a default filename "photometry".

    Returns
    -------
    str
        The base filename derived from `file_obj.name` by removing the
        extension(s). Returns "photometry" if `file_obj` is None.

    Examples
    --------
    >>> class MockFile: name = "image.fits.fz"
    >>> get_base_filename(MockFile())
    'image'
    >>> class MockFile: name = "catalog.csv"
    >>> get_base_filename(MockFile())
    'catalog'
    >>> get_base_filename(None)
    'photometry'
    """
    if file_obj is None:
        return "photometry"

    original_name = file_obj.name
    base_name = os.path.splitext(original_name)[0]
    base_name = os.path.splitext(base_name)[0]

    return base_name


def create_figure(size="medium", dpi=120):
    """
    Create a matplotlib Figure object with a predefined or default size and DPI.

    Uses a dictionary `FIGURE_SIZES` to map descriptive size names ('small',
    'medium', 'large', 'wide', 'stars_grid') to (width, height) tuples in
    inches.

    Parameters
    ----------
    size : str, optional
        A key corresponding to a predefined figure size in the `FIGURE_SIZES`
        global dictionary. Allowed values are 'small', 'medium', 'large',
        'wide', 'stars_grid'. If an invalid key is provided, it defaults to
        'medium'. Defaults to "medium".
    dpi : int, optional
        The resolution of the figure in dots per inch. Defaults to 120.

    Returns
    -------
    matplotlib.figure.Figure
        A matplotlib Figure object initialized with the specified or default
        figsize and dpi.

    Notes
    -----
    - Relies on the global `FIGURE_SIZES` dictionary for size definitions.
    """
    if size in FIGURE_SIZES:
        figsize = FIGURE_SIZES[size]
    else:
        figsize = FIGURE_SIZES["medium"]
    return plt.figure(figsize=figsize, dpi=dpi)


def initialize_log(base_filename):
    """
    Initialize and return an in-memory text buffer (StringIO) for logging.

    Creates a StringIO object and writes a standard header including a
    timestamp and the provided base filename, suitable for logging processing
    steps.

    Parameters
    ----------
    base_filename : str
        The base name of the input file being processed, used in the log
        header.

    Returns
    -------
    io.StringIO
        An initialized StringIO buffer containing the log header.

    Notes
    -----
    - The log header format includes the title "RAPAS Photometry Pipeline Log",
      a separator line, the start timestamp, and the input filename.
    """
    log_buffer = StringIO()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log_buffer.write("RAPAS Photometry Pipeline Log\n")
    log_buffer.write("===============================\n")
    log_buffer.write(f"Processing started: {timestamp}\n")
    log_buffer.write(f"Input file: {base_filename}\n\n")

    return log_buffer


def write_to_log(log_buffer, message, level="INFO"):
    """
    Write a formatted message to the provided log buffer.

    Prepends the message with a timestamp ([HH:MM:SS]) and the specified
    log level (e.g., INFO, WARNING, ERROR).

    Parameters
    ----------
    log_buffer : io.StringIO or None
        The StringIO buffer object to write the log message to. If None,
        the function does nothing.
    message : str
        The log message content.
    level : str, optional
        The severity level of the log message (e.g., "INFO", "WARNING",
        "ERROR"). Defaults to "INFO".

    Returns
    -------
    None
        This function modifies the `log_buffer` in place and returns None.

    Notes
    -----
    - Format: "[HH:MM:SS] LEVEL: Message\n"
    """
    if log_buffer is None:
        return

    timestamp = datetime.now().strftime("%H:%M:%S")
    log_buffer.write(f"[{timestamp}] {level.upper()}: {message}\n")


def ensure_output_directory(directory=""):
    # c:\Users\pierf\rpp\src
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # c:\Users\pierf\rpp
    project_root = os.path.dirname(script_dir)
    # c:\Users\pierf
    parent_dir = os.path.dirname(project_root)
    # c:\Users\pierf\rpp_results
    results_root = os.path.join(parent_dir, "rpp_results")
    final_path = os.path.join(results_root, directory)

    if not os.path.exists(final_path):
        try:
            os.makedirs(final_path)
        except Exception:
            return "."
    return final_path


def safe_catalog_query(query_func, error_msg, *args, **kwargs):
    """
    Execute an astronomical catalog query function with robust error handling.

    Wraps a callable (e.g., a function from astroquery) to catch common
    exceptions like network errors, timeouts, and value errors that can occur
    during queries to online astronomical databases.

    Parameters
    ----------
    query_func : callable
        The function to call for performing the catalog query.
    error_msg : str
        A base error message string to prepend to any specific exception
        message caught during the query execution.
    *args
        Positional arguments to pass directly to `query_func`.
    **kwargs
        Keyword arguments to pass directly to `query_func`.

    Returns
    -------
    tuple (any | None, str | None)
        - (result, None): If `query_func` executes successfully, returns its
          result and None for the error message. The type of `result` depends
          on `query_func`.
        - (None, error_message): If an exception occurs during execution,
          returns None for the result and a formatted string describing the
          error (e.g., "Failed to query SIMBAD: Network error - details").

    Examples
    --------
    >>> from astroquery.simbad import Simbad
    >>> # Assuming Simbad.query_object raises a Timeout error
    >>> result, error = safe_catalog_query(
    ...     Simbad.query_object,
    ...     "Failed to query SIMBAD",
    ...     "M31"
    ... )
    >>> if error:
    ...     # Output: Query failed: Failed to query SIMBAD: Query timed out
    ...     print(f"Query failed: {error}")
    >>> else:
    ...     print(result)
    """
    try:
        result = query_func(*args, **kwargs)
        return result, None
    except requests.exceptions.Timeout:
        return None, f"{error_msg}: Query timed out"
    except requests.exceptions.RequestException as e:
        return None, f"{error_msg}: Network error - {str(e)}"
    except ValueError as e:
        return None, f"{error_msg}: Value error - {str(e)}"
    except Exception as e:
        return None, f"{error_msg}: {str(e)}"


def zip_results_on_exit(science_file_obj, outputdir):
    """Compresses analysis result files into a timestamped ZIP archive.
    
    Returns:
        tuple (str, str) or (None, None): 
            - (zip_filename, zip_path) if successful
            - (None, None) if no files to zip or output_dir doesn't exist
    """
    output_dir = outputdir
    if not os.path.exists(output_dir):
        return None, None
    base_name = get_base_filename(science_file_obj)
    files = [
        f
        for f in os.listdir(output_dir)
        if os.path.isfile(os.path.join(output_dir, f))
        and f.startswith(base_name)
        and not f.lower().endswith(".zip")
        and not f.lower().endswith(".fits")
    ]
    if not files:
        return None, None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"{base_name}_{timestamp}.zip"
    zip_path = os.path.join(os.path.dirname(output_dir + "/rpp_results"), zip_filename)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file in files:
            file_path = os.path.join(output_dir, file)
            zipf.write(file_path, arcname=file)
    for file in files:
        try:
            os.remove(os.path.join(output_dir, file))
        except Exception as e:
            print(f"Warning: Could not remove file {file} after zipping: {e}")
    
    return zip_filename, zip_path


def save_header_to_txt(header, filename, output_dir):
    """
    Save a FITS header to a formatted text file.

    Parameters
    ----------
    header : dict or astropy.io.fits.Header
        FITS header dictionary or object
    filename : str
        Base filename to use (without extension)
    output_dir : str
        The directory to save the file in.

    Returns
    -------
    str or None
        Full path to the saved file, or None if saving failed
    """
    if header is None:
        return None

    header_txt = "FITS Header\n"
    header_txt += "==========\n\n"

    for key, value in header.items():
        header_txt += f"{key:8} = {value}\n"

    output_filename = os.path.join(output_dir, f"{filename}.txt")

    with open(output_filename, "w") as f:
        f.write(header_txt)

    return output_filename


def save_header_to_fits(header, filename, output_dir):
    """
    Save a FITS header to a FITS file with an empty primary HDU.

    Parameters
    ----------
    header : dict or astropy.io.fits.Header
        FITS header dictionary or object
    filename : str
        Base filename to use (without extension)
    output_dir : str
        The directory to save the file in.

    Returns
    -------
    tuple (str | None, str | None)
        - (filepath, None): If successful.
        - (None, error_message): If failed.
    """
    if header is None:
        return None, "Header is None"

    try:
        # Create a minimal primary HDU with the header
        primary_hdu = fits.PrimaryHDU(header=header)

        # Create HDU list
        hdul = fits.HDUList([primary_hdu])

        output_filename = os.path.join(output_dir, f"{filename}.fits")

        # Write FITS file
        hdul.writeto(output_filename, overwrite=True)
        hdul.close()

        return output_filename, None

    except Exception as e:
        return None, f"Failed to save header as FITS file: {str(e)}"


def save_fits_with_wcs(original_path, updated_header, output_dir,
                       filename_suffix="_wcs", also_save_to_data_dir=True,
                       original_filename=None, username=None):
    """
    Save a FITS file with original image data and updated WCS header.

    Parameters
    ----------
    original_path : str
        Path to the original FITS file (may be a temp file path).
    updated_header : astropy.io.fits.Header
        Updated header containing the new WCS solution.
    output_dir : str
        The directory to save the file in (for ZIP archive).
    filename_suffix : str, optional
        Suffix to append to the original filename (default: "_wcs").
    also_save_to_data_dir : bool, optional
        If True, also save a copy to rpp_data/fits/ without timestamp (default: True).
        This copy will be overwritten if the same file is processed again.
    original_filename : str, optional
        The original filename of the uploaded file (without temp prefix).
        If None, extracts from original_path.
    username : str, optional
        Username to prefix the permanent copy filename.

    Returns
    -------
    tuple (str | None, str | None, str | None)
        - (filepath, None, stored_filename): If successful.
          stored_filename is the name of the file saved to rpp_data/fits/
        - (None, error_message, None): If failed.
    """
    if updated_header is None:
        return None, "Updated header is None", None

    if not os.path.exists(original_path):
        return None, f"Original FITS file not found: {original_path}", None

    stored_filename = None  # Track the filename saved to rpp_data/fits/

    try:
        # Open original FITS and get the image data
        with fits.open(original_path) as hdul:
            image_data = hdul[0].data

        # Create new HDU with original data and updated header
        primary_hdu = fits.PrimaryHDU(data=image_data, header=updated_header)
        hdul_new = fits.HDUList([primary_hdu])

        # Use original_filename if provided, otherwise extract from path
        if original_filename:
            base_name = os.path.splitext(original_filename)[0]
        else:
            base_name = os.path.splitext(os.path.basename(original_path))[0]

        # Build output filename for the results directory (goes into ZIP)
        output_filename = os.path.join(output_dir, f"{base_name}{filename_suffix}.fits")

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Write FITS file to results directory
        hdul_new.writeto(output_filename, overwrite=True)

        # Also save a copy to rpp_data/fits/ without timestamp (overwrites existing)
        if also_save_to_data_dir:
            try:
                # Get rpp_data/fits path (same level as rpp_results)
                script_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.dirname(script_dir)
                parent_dir = os.path.dirname(project_root)
                data_fits_dir = os.path.join(parent_dir, "rpp_data", "fits")
                os.makedirs(data_fits_dir, exist_ok=True)

                # Build filename with username prefix if provided
                if username:
                    data_base_name = f"{username}_{base_name}"
                else:
                    data_base_name = base_name

                # Save with username prefix - overwrites on reprocessing
                stored_filename = f"{data_base_name}{filename_suffix}.fits"
                data_output_filename = os.path.join(data_fits_dir, stored_filename)
                hdul_new.writeto(data_output_filename, overwrite=True)
            except Exception as data_save_error:
                # Don't fail the whole operation if data dir save fails
                print(f"Warning: Could not save to rpp_data/fits: {data_save_error}")
                stored_filename = None

        hdul_new.close()

        return output_filename, None, stored_filename

    except Exception as e:
        return None, f"Failed to save FITS with WCS: {str(e)}", None


def save_catalog_files(final_table, catalog_name, output_dir):
    """
    Save the final photometry table as both VOTable (XML) and CSV files.

    Args:
        final_table (pd.DataFrame): The photometry results table.
        catalog_name (str): The base name for the catalog files.
        output_dir (str): The directory to save the files in.

    Returns:
        tuple (list[str], list[str])
            - list of success messages
            - list of error messages
    """
    success_messages = []
    error_messages = []

    if final_table is None or len(final_table) == 0:
        error_messages.append("Cannot create VOTable: final_table is None or empty")
        return success_messages, error_messages

    try:
        # Clean the DataFrame before conversion to handle problematic columns
        df_for_votable = final_table.copy()

        # Remove or fix columns that might cause issues with astropy Table conversion
        problematic_columns = []
        for col in df_for_votable.columns:
            # Check for columns with mixed types or object arrays that might cause issues
            if df_for_votable[col].dtype == "object":
                # Try to convert to string, handling None/NaN values
                try:
                    df_for_votable[col] = df_for_votable[col].astype(str)
                    df_for_votable[col] = df_for_votable[col].replace("nan", "")
                    df_for_votable[col] = df_for_votable[col].replace("None", "")
                except Exception:
                    problematic_columns.append(col)

        # Remove columns that still cause issues
        if problematic_columns:
            df_for_votable = df_for_votable.drop(columns=problematic_columns)
            error_messages.append(
                f"Removed problematic columns from VOTable: {problematic_columns}"
            )

        # Convert pandas DataFrame to astropy Table
        astropy_table = Table.from_pandas(df_for_votable)

        # Create VOTable
        votable = from_table(astropy_table)
        # Define base_catalog_name here to ensure it's available for both
        base_catalog_name = catalog_name + ".csv"
        if base_catalog_name.endswith(".csv"):
            base_catalog_name = base_catalog_name[:-4]
        filename = f"{base_catalog_name}.vot"  # VOTable extension

        catalog_path = os.path.join(output_dir, filename)

        # Write VOTable to file
        writeto(votable, catalog_path)

        # Also create CSV buffer for backward compatibility if needed
        csv_buffer = StringIO()
        final_table.to_csv(csv_buffer, index=False)
        csv_data = csv_buffer.getvalue()

        # Save CSV file using catalog_name as file name
        base_catalog_name = catalog_name + ".csv"
        csv_file_path = os.path.join(output_dir, base_catalog_name)
        with open(csv_file_path, "w", encoding="utf-8") as f:
            f.write(csv_data)
        success_messages.append(f"CSV and VOT Catalogs saved")

    except Exception as e:
        error_messages.append(f"Error preparing VOTable download: {e}")

    return success_messages, error_messages
