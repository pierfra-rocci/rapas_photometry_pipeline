#!/usr/bin/env python3
"""
Standalone script to detect and mask satellite trails in astronomical FITS images.

This script uses the ASTRiDE library to detect linear streaks (satellite/airplane trails)
in astronomical images and creates a boolean mask to exclude these regions from analysis.

Usage:
    python satellite_trail_detector.py <input_fits_file> [options]
"""

import argparse
import sys
import numpy as np
from astropy.io import fits
import matplotlib.pyplot as plt


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

            # Fix header issues before saving
            hdu.verify("silentfix")
            hdu.writeto(temp_fits_path, overwrite=True)

            print("Detecting satellite trails using ASTRiDE...")

            # Create ASTRiDE streak detector
            # Use conservative parameters to avoid false positives
            streak_detector = astride.detect.Streak(
                temp_fits_path,
                remove_bkg="constant",  # Use constant background for simplicity
                bkg_box_size=50,
                contour_threshold=1.0,  # Higher threshold to reduce false positives
                min_points=15,  # Require more points for a valid streak
                shape_cut=0.2,
                area_cut=25.0,  # Larger area cut
                radius_dev_cut=0.5,
                connectivity_angle=3.0,
                fully_connected="high",
            )

            # Run streak detection
            streak_detector.detect()

            # Create mask from detected streaks
            mask = np.zeros_like(image_data, dtype=bool)

            if streak_detector.streaks and len(streak_detector.streaks) > 0:
                print(f"Found {len(streak_detector.streaks)} satellite trails")

                # Create mask by drawing lines along the detected streaks
                # Use a buffer around each streak to ensure complete masking
                buffer_size = 5  # pixels buffer around each streak

                for streak in streak_detector.streaks:
                    x_coords = streak["x"].astype(int)
                    y_coords = streak["y"].astype(int)

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
                                    if (
                                        0 <= nx < mask.shape[1]
                                        and 0 <= ny < mask.shape[0]
                                    ):
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
                print("No satellite trails detected")

            return mask

        except Exception as e:
            print(f"Error in satellite trail detection: {e}")
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
        print("ASTRiDE library not available. Satellite trail detection disabled.")
        return np.zeros_like(image_data, dtype=bool)
    except Exception as e:
        print(f"Unexpected error in satellite trail detection: {e}")
        return np.zeros_like(image_data, dtype=bool)


def load_fits_image(file_path):
    """
    Load a FITS image file.

    Parameters
    ----------
    file_path : str
        Path to the FITS file

    Returns
    -------
    tuple
        (image_data, header) where image_data is a numpy array and header is the FITS header
    """
    with fits.open(file_path) as hdul:
        image_data = hdul[0].data
        header = hdul[0].header
        # If image has more than 2 dimensions, take the first 2D slice
        if image_data.ndim > 2:
            image_data = image_data[0]
        return image_data, header


def save_mask(mask, output_path):
    """
    Save the mask as a FITS file.

    Parameters
    ----------
    mask : numpy.ndarray
        Boolean mask array
    output_path : str
        Path to save the mask file
    """
    hdu = fits.PrimaryHDU(mask.astype(np.uint8))
    hdu.writeto(output_path, overwrite=True)
    print(f"Mask saved to {output_path}")


def visualize_results(image_data, mask, output_plot_path=None):
    """
    Visualize the original image and the mask.

    Parameters
    ----------
    image_data : numpy.ndarray
        Original image data
    mask : numpy.ndarray
        Boolean mask array
    output_plot_path : str, optional
        Path to save the plot, if provided
    """
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(15, 5))

    # Original image
    ax1.imshow(image_data, origin="lower", cmap="viridis")
    ax1.set_title("Original Image")

    # Mask
    ax2.imshow(mask, origin="lower", cmap="gray")
    ax2.set_title("Satellite Trail Mask")

    # Image with mask overlay
    ax3.imshow(image_data, origin="lower", cmap="viridis")
    ax3.imshow(mask, origin="lower", cmap="viridis", alpha=0.5)
    ax3.set_title("Image with Mask Overlay")

    plt.tight_layout()

    if output_plot_path:
        plt.savefig(output_plot_path, dpi=150, bbox_inches="tight")
        print(f"Visualization saved to {output_plot_path}")
    else:
        plt.show()


def main():
    parser = argparse.ArgumentParser(
        description="Detect and mask satellite trails in astronomical FITS images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python satellite_trail_detector.py image.fits
  python satellite_trail_detector.py image.fits -o mask.fits
  python satellite_trail_detector.py image.fits -p plot.png
  python satellite_trail_detector.py image.fits -o mask.fits -p plot.png
        """,
    )

    parser.add_argument("input_file", help="Input FITS file path")
    parser.add_argument("-o", "--output", help="Output mask file path (FITS format)")
    parser.add_argument("-p", "--plot", help="Output plot file path (PNG format)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    try:
        # Load the FITS image
        print(f"Loading FITS file: {args.input_file}")
        image_data, header = load_fits_image(args.input_file)
        print(f"Image shape: {image_data.shape}")
        print(f"Image data type: {image_data.dtype}")

        # Detect and mask satellite trails
        mask = detect_and_mask_satellite_trails(image_data, header)

        # Print statistics
        trail_pixels = np.sum(mask)
        total_pixels = mask.size
        percentage = (trail_pixels / total_pixels) * 100
        print(f"Masked pixels: {trail_pixels} ({percentage:.2f}% of image)")

        # Save mask if output path provided
        if args.output:
            save_mask(mask, args.output)

        # Visualize results
        visualize_results(image_data, mask, args.plot)

        return 0

    except Exception as e:
        print(f"Error processing file: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
