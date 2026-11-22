# Changelog

## Version 1.9.5

**Released: November 22, 2026**

### Catalog query timeouts and fallback

- SIMBAD, VizieR and Gaia TAP requests are limited to 30 seconds per mirror.
- When a primary request times out or fails, the configured fallback mirror is
  tried with the same timeout. If all attempts fail, catalog enrichment
  continues without that service's results, preserving the rest of the
  processing pipeline.

### Catalog-specific zero-point calibration plots

- Both calibration plots identify the selected catalog in their titles and
  legends: **Gaia DR3**, **GAIA Synthetic**, **PanSTARRS**, or **SkyMapper**.
  The selected filter remains identified on the plot axes.
