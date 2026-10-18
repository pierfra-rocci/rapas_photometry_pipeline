Examples
========

This page provides practical examples of using RAPAS Photometry Pipeline for
common workflows. The examples below are aligned with the current Streamlit UI
and the dual-backend architecture.

Basic Photometry Example
------------------------

This example demonstrates how to perform basic photometry on a single FITS image:

1. Log in to the application.
2. Upload a science image using the main uploader.
3. Configure parameters in the sidebar such as seeing, threshold, border mask,
   filter band, and optional Astrometry Check.
4. Click **Start Analysis**.
5. Review the results (plots, tables, Aladin viewer).
6. Download the results using the ZIP download controls.

.. note::
   Every successful login writes a timestamped line with the pipeline version
   to the service log, for example
   ``[2026-10-18 18:46:22] RPP v1.9.0 - User 'intern1' logged in successfully (FastAPI backend).``
   The FastAPI backend, the legacy backend and the frontend each emit their
   own line, so a login appears in both the backend and the frontend logs.

Variable Star Analysis
----------------------

This example shows how to analyze a variable star using RPP outputs:

1. Upload and process a sequence of science images of the same field individually through the RPP application. Ensure consistent parameters.
2. Download the results ZIP for each image.
3. Extract the `*_catalog.csv` files.
4. Use a script like the one below to combine catalogs and plot a light curve.

.. code-block:: python

   # Example script for processing multiple RPP catalogs
   import pandas as pd
   import matplotlib.pyplot as plt
   import glob
   import os
   from astropy.time import Time

   # Directory containing extracted CSV catalog files
   results_dir = "path/to/extracted/catalogs"
   catalog_files = glob.glob(os.path.join(results_dir, "*_catalog.csv"))

   # --- Option 1: Identify target by coordinates ---
   target_ra = 183.2641  # Example RA
   target_dec = 22.0145 # Example Dec
   search_radius_deg = 3.0 / 3600.0 # 3 arcseconds

   # --- Option 2: Identify target by a known ID (if available) ---
   # target_id_col = 'aavso_Name' # e.g., 'simbad_main_id', 'aavso_Name'
   # target_id_val = 'V* AB Aur'

   light_curve_data = []

   for file in catalog_files:
       try:
           df = pd.read_csv(file)
           # Extract observation time from header file (assuming it exists)
           header_file = file.replace("_catalog.csv", "_header.txt")
           obs_time_str = None
           if os.path.exists(header_file):
               with open(header_file, 'r') as hf:
                   for line in hf:
                       if line.startswith('DATE-OBS'):
                           obs_time_str = line.split('=')[1].strip().split('/')[0].strip() # Extract value
                           break
           if not obs_time_str:
               print(f"Warning: Could not find DATE-OBS in {header_file}")
               continue

           obs_time_jd = Time(obs_time_str).jd

           # Find the target
           target_row = None
           # Option 1: Match by coordinates
           if 'ra' in df.columns and 'dec' in df.columns:
               dist_deg = ((df['ra'] - target_ra)**2 + (df['dec'] - target_dec)**2)**0.5
               match_idx = dist_deg.idxmin() # Find closest match
               if dist_deg[match_idx] < search_radius_deg:
                   target_row = df.loc[match_idx]

           # Option 2: Match by ID (Uncomment and adapt if using)
           # elif target_id_col in df.columns:
           #     matches = df[df[target_id_col] == target_id_val]
           #     if not matches.empty:
           #         target_row = matches.iloc[0]

           # Calibrated magnitudes are exported either with the legacy names
           # (aperture_mag_1_3) or with a filter prefix (e.g.
           # rapasg_aperture_mag_1_3); pick whichever column is present.
           mag_col = next(
               (c for c in df.columns if c.endswith('aperture_mag_1_3') and 'err' not in c),
               None,
           )
           err_col = next(
               (c for c in df.columns if c.endswith('aperture_mag_err_1_3')),
               None,
           )

           if target_row is not None and mag_col and pd.notna(target_row[mag_col]):
               mag = target_row[mag_col]
               # The calibrated magnitude error already includes the flux error
               # (aperture + local background) and the zero-point uncertainty.
               mag_err = target_row[err_col] if err_col else None

               light_curve_data.append({'jd': obs_time_jd, 'mag': mag, 'mag_err': mag_err})
           else:
               print(f"Warning: Target not found or missing magnitude in {file}")

       except Exception as e:
           print(f"Error processing {file}: {e}")

   if not light_curve_data:
       print("No data points collected for light curve.")
   else:
       # Create DataFrame and sort by time
       lc_df = pd.DataFrame(light_curve_data).sort_values('jd')

       # Plot the light curve
       plt.figure(figsize=(10, 6))
       plt.errorbar(lc_df['jd'], lc_df['mag'], yerr=lc_df['mag_err'], fmt='o', capsize=3, label='Aperture Mag')
       plt.gca().invert_yaxis()  # Astronomical magnitude convention
       plt.xlabel('Time (JD)')
       plt.ylabel('Calibrated Magnitude')
       plt.title(f'Light Curve (RA:{target_ra:.4f}, Dec:{target_dec:.4f})')
       plt.legend()
       plt.grid(True, alpha=0.3)
       plt.tight_layout()
       plt.savefig('light_curve.png')
       print("Light curve saved to light_curve.png")


Asteroids and Moving Objects
----------------------------

This example demonstrates how to identify potential moving objects:

1. Upload an image suspected to contain solar system objects.
2. Run the analysis with **Start Analysis**. The pipeline
   automatically queries SkyBoT.
3. Review the "Matched Objects Summary" table or the full catalog (`*_catalog.csv`).
4. Look for the SkyBoT identification in the Aladin popup or the `skybot_NAME`
   column in the exported catalog.
5. SkyBoT service failures are logged and do not imply that the field contains
   no moving objects.

Tutorial: Complete Photometry Workflow
--------------------------------------

This step-by-step tutorial covers a complete workflow:

1. **Preparation**

   * Ensure the backend server is running.
   * Have your science image(s) ready.
   * Know your approximate observatory location.
   * Obtain an Astro-Colibri UID key if you want transient matching.

2. **Login & Configuration**

   * Launch the frontend (`streamlit run frontend.py`) and log in. Each
     successful sign-in is written to the service logs with a local timestamp
     and the pipeline version, tagged with its origin (backend or frontend).
   * Upload your science image.
   * Configure observatory values, analysis parameters, transient options, and
     the Astro-Colibri key in the sidebar.
   * Click "Save Configuration" to store settings.

3. **Run Pipeline**

   * Click **Start Analysis**.
   * Monitor progress messages in the main panel.

4. **Analysis & Review**

   * Examine the image display and plots (Background, FWHM, PSF, Zero Point).
   * Review the matched sources tables (GAIA calibration, Catalog Matches Summary).
   * Explore objects in the Aladin viewer.
   * Check the log output for details or warnings.

5. **Results**

   * Download the complete results using the ZIP button.
   * Use the `*_catalog.csv` file for your scientific analysis.

Advanced: Scripting with RPP Functions
--------------------------------------

While RPP is primarily a web app, some core functions from ``src/`` can be
used in standalone Python scripts if dependencies are managed correctly.

.. warning::
     Directly calling Streamlit-cached functions or functions relying heavily on
     ``st.session_state`` or other ``streamlit`` UI calls outside the Streamlit
     environment may not work as expected and may require adaptation.

.. code-block:: python

    from astropy.io import fits

    from src.header_utils import select_science_header
    from src.tools_pipeline import extract_pixel_scale, safe_wcs_create

    file_path = "my_image.fits"

    with fits.open(file_path) as hdul:
        image_data = hdul[0].data
        header = hdul[0].header

    wcs_obj, wcs_error, log_messages = safe_wcs_create(header)
    pixel_scale_arcsec, pixel_scale_source = extract_pixel_scale(header)

    print("WCS OK:", wcs_obj is not None)
    print("WCS error:", wcs_error)
    print("Pixel scale:", pixel_scale_arcsec, pixel_scale_source)
    print("Log messages:")
    for message in log_messages:
        print(" -", message)

Notes and Next Steps
--------------------

- Use :doc:`installation` and :doc:`usage` to set up the frontend/backend
  stack before trying the UI examples.
- Use the generated ``*_catalog.csv`` and log files for downstream scientific
  workflows such as light-curve building or object triage.
- When scripting with internal functions, prefer modules under ``src/`` and
  avoid directly depending on Streamlit page code.