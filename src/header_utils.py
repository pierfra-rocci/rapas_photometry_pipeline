def select_science_header(original_header, solved_header):
    """Return solved header when available, otherwise keep original header."""
    return solved_header if solved_header is not None else original_header


def copy_header_or_none(header):
    """Return a shallow copy of header or None if header is missing."""
    return header.copy() if header is not None else None


def recover_original_wcs(science_file, load_fits_data_func, safe_wcs_create_func):
    """Reload the original FITS header and restore its WCS when possible.

    Parameters
    ----------
    science_file : Any
        File-like object accepted by ``load_fits_data_func``.
    load_fits_data_func : callable
        Callable returning ``(image_data, header)`` for the original FITS file.
    safe_wcs_create_func : callable
        Callable returning ``(wcs_obj, error, log_messages)`` from a header.

    Returns
    -------
    tuple
        ``(wcs_obj, original_header)`` if a valid WCS can be restored,
        otherwise ``(None, None)``.
    """
    _, original_header = load_fits_data_func(science_file)
    if original_header is None:
        return None, None

    wcs_obj, _, _ = safe_wcs_create_func(original_header)
    if wcs_obj is None:
        return None, None

    return wcs_obj, original_header
