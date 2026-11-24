# Changelog

## Version 1.9.6

**Released: November 24, 2026**

### Release metadata and documentation

- Updated the project version to 1.9.6 and rebuilt the published documentation.

## Version 1.9.5

**Released: November 22, 2026**

### Catalog query timeouts and fallback

- SIMBAD, VizieR and Gaia TAP requests are limited to 30 seconds per mirror.
- When a primary request times out or fails, the configured fallback mirror is
  tried with the same timeout. If all attempts fail, catalog enrichment
  continues without that service's results, preserving the rest of the
  processing pipeline.

### Survey cutout-service fallback for template cutouts

- When CDS hips2fits cannot deliver the reference-survey template for a
  transient-candidate or Astro-Colibri cutout, the same survey is requested
  from its own cutout service: the PS1 Image Cutout Service for northern
  fields and the SkyMapper Image Cutout Service for southern ones. The stamp
  is resampled onto the science cutout so both panels stay aligned.
- The SDSS and DSS2 HiPS fallbacks were removed: the reference survey is the
  only template source, served by hips2fits or by its cutout service.

### Catalog-specific zero-point calibration plots

- Both calibration plots identify the selected catalog in their titles and
  legends: **Gaia DR3**, **GAIA Synthetic**, **PanSTARRS**, or **SkyMapper**.
  The selected filter remains identified on the plot axes.
