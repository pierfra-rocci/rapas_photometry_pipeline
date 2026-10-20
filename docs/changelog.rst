Changelog
=========

This document records all notable changes to RAPAS Photometry Pipeline.

Version 1.9.0
-------------

**Released: October 15, 2026**

(Version 1.8.8 was not released; Version 1.8.9 followed 1.8.7 directly.)

**Login audit log**

*   Every successful login writes a timestamped line with the pipeline version
    to the service log:
    ``[YYYY-MM-DD HH:MM:SS] RPP vX.Y.Z - User '<username>' logged in successfully (<origin>).``
*   ``api/main.py`` (FastAPI backend), ``backend.py`` (legacy backend) and
    ``pages/login.py`` (frontend) each emit their own line; the origin suffix
    tells them apart. The version is read at runtime from
    ``src/__version__.py``, so the message follows future releases
    automatically.

**Curated catalog export**

*   **Fixed export columns.** ``*_catalog.csv`` and ``*_catalog.vot`` now contain
    only ``id``, ``ra``, ``dec``, the Astro-Colibri, SIMBAD, SkyBoT and AAVSO
    matches, ``catalog_matches``, ``x_center``, ``y_center``, ``snr_*``, the
    aperture magnitudes with their ``quality_flag_*``, the PSF magnitude, the
    magnitude errors and the ``psf_minus_aperture_mag_*`` offsets, in that
    order. Radius suffixes (third aperture = user choice) and the filter prefix
    (for example ``rapasg_``) are resolved dynamically; absent columns are
    skipped. Colour-corrected columns and internal diagnostics are no longer
    written to the files (they remain available in the session).
*   **Astro-Colibri first.** Rows with an ``astrocolibri_name`` are sorted at
    the top of both files.
*   **Unique source ``id``.** ``id`` is the first column, complete (PSF-only
    sources get new numbers) and unique; ``x_center``/``y_center`` are filled
    from the detection centroid for PSF-only sources.
*   **Aladin ID.** The ID shown in the Aladin Lite popup is always the ``id``
    column of the files (created when missing).
*   **Transient CSV.** ``*_transients.csv`` reuses the catalog columns, matching
    each candidate to its catalog source by sky position (the previous match by
    rounded pixel coordinates could silently miss sources).
*   New helpers ``ensure_source_ids``, ``build_export_columns`` and
    ``prepare_export_table`` in ``src/utils.py``, ``build_transient_table`` in
    ``src/transient.py``, and ``tests/test_export_catalog.py``.

**Transient finder filter association, sparse-field detection**

*   **Calibration band -> catalogue column fix.** The transient finder guessed
    the filter from the calibration column name with a substring search, so
    Gaia RP was read as ``r`` (PS1 ``rmag``), Gaia BP and the Johnson V/B
    bands matched nothing (every match was then silently confirmed) and Gaia G
    was compared with the PS1-transformed ``gmag``. A new
    ``normalize_calibration_filter`` maps each calibration band explicitly
    (``phot_g/bp/rp_mean_mag`` -> G/BP/RP, ``*_jkc_mag`` -> U/V/B/R/I,
    ``u_sdss_mag`` -> u, ``gmag``... -> g...) so the native Gaia columns are used.
*   **Gaia r vs g consistency.** For raw Vizier Gaia tables ``r``/``R``/``V``
    now map to ``Gmag`` (as in the XMatch branch) instead of ``BPmag``.
*   **Astrometry: 2 sigma when 3 sigma is sparse.** A 3 sigma detection with
    fewer than 100 sources is no longer accepted directly: 2 sigma is tried and
    kept when it finds more sources (otherwise the 3 sigma list is used).
*   **Transient candidates in Aladin.** Candidates are drawn as yellow stars in
    a dedicated "Transient candidates" overlay of the Aladin Lite widget (popup
    with the catalogue ``id``, coordinates and PSF magnitude) for visual
    checking, next to the red Astro-Colibri squares.
*   **Transient finder at 1.5 sigma.** Detection now starts directly at
    1.5 sigma (``TRANSIENT_DETECT_THRESH``) with a minimum S/N of 2 (3 above
    2 sigma) to be more sensitive to faint sources. Candidates are still
    required to match the photometry table.
*   **Transient finder without PSF photometry.** When the photometry table has
    no PSF magnitude columns, or when the matched detections have no finite
    PSF value, ``find_candidates()`` now falls back to the calibrated
    1.1 x FWHM aperture magnitude (``aperture_mag_1_1`` and its error) instead
    of aborting with "No transient candidates found". The app displays a
    warning when candidates rely on this fallback, using the same
    PSF-unavailable wording as the main pipeline. The shared warning is
    ``PSF photometry is not available; using calibrated aperture photometry instead.``;
    the full transient variant is
    ``PSF photometry is not available; using calibrated aperture photometry instead. Transient candidates use the calibrated 1.1 x FWHM aperture magnitude.``

**Finer astrometry and Astro-Colibri radius**

*   **Detection ladder 3 -> 2 -> 1 sigma.** The blind solve now targets at
    least 20 stars (Astrometry.net has no hard minimum: it builds quads from
    the brightest sources first, so about 20 well-measured stars is the
    practical target). The detection threshold is lowered from 3 to 2 and then
    1 sigma until the target is reached, with the measurement S/N relaxed
    accordingly (5, 3, 2). The small-image floor is raised from 10 to 20.
*   **Best effort on sparse fields.** If no threshold reaches the target, the
    richest detection with at least 5 stars is used instead of failing, with a
    warning in the log.
*   **Retry on failure.** When the local solve fails, detection is repeated at
    the next lower threshold and the solve is retried before the online
    fallback.
*   **Astro-Colibri progressive radius.** Events are matched at 5 arcsec, then
    10, 15 and 20 arcsec (was a fixed 10 arcsec); the log lists how many events
    were matched at each radius, and a WARNING is raised for matches only found
    at the 15 or 20 arcsec tier. New ``astrocolibri_match_radius`` helper and
    tests in ``tests/test_robustness_astrometry.py`` and
    ``tests/test_network_resilience.py``.
*   **Astro-Colibri sources listed and highlighted.** The event RA/Dec are
    kept (``astrocolibri_ra`` / ``astrocolibri_dec`` columns, exported after
    ``astrocolibri_classification``), every event returned for the field is
    displayed in a table (including events without coordinates), and
    cross-matched sources are drawn as larger red squares in a dedicated
    Aladin Lite layer.
*   **Richer Astro-Colibri events and errors.** The events table now also shows
    observatory, trigger ID, position error (arcsec), date and redshift when
    the API provides them; ``sources`` / ``xsources`` / ``icecat`` entries
    are reported in the log; HTTP 401/403/429 answers give an explicit reason
    and the response is parsed defensively.
*   **More robust SkyBoT queries.** New shared ``skybot_cone_search`` helper
    (``src/utils.py``) used by the catalog cross-match and the transient
    filter: 3 attempts on network errors and HTTP 429/5xx, ``-from`` label,
    epoch range check, HTTP error reporting, mid-exposure epoch and a per-object
    match tolerance of 10 to 30 arcsec derived from the ephemeris ``Err``.

**Scale-aware photometric error model**

*   **The Poisson term now follows the units of the frame.**
    ``calc_total_error`` adds ``data / gain`` to the pixel variance, which is
    only meaningful when the data are counts in the same units as the gain. A
    rescaled frame (a normalised float stack with a sky level of about 1e-2, for
    example) violates that: with a genuine e-/ADU gain the Poisson term at the
    sky level exceeded the measured sky variance by a factor of 1e5, so the
    error map said nothing about the real noise and the aperture S/N was
    underestimated by one to two orders of magnitude. The PSF step was hit
    hardest, because photutils weights the ePSF-to-star fits by
    ``1 / error**2``: the star cores ended up ~1e-4 of the weight of the sky
    pixels, 22 of 26 fitted fluxes came back negative, the next ePSF was
    therefore inverted and ``EPSFBuilder`` aborted with
    ``Cannot normalize ePSF: the data sum (...) is not finite and positive``,
    dropping PSF photometry for the whole frame.
*   **Resolution.** ``resolve_poisson_gain()`` keeps the resolved gain when the
    Poisson variance it predicts at the sky level stays within
    ``POISSON_SKY_VARIANCE_TOLERANCE`` (10x) of the measured background
    variance, and otherwise replaces it with the largest value the frame allows,
    ``gain = sky level / background_rms**2``. ``compute_error_map()`` builds the
    per-pixel error from that gain, falling back to the background RMS map alone
    when no usable gain exists. On the real normalised stack the core error
    dropped from ~1.4e3x the sky RMS (larger than the data itself) to a few x
    the sky RMS, and PSF photometry now runs.

**PSF, Transients and Messages**

*   **PSF star minimum lowered from 15 to 10.** A warning about possibly poor
    PSF quality is shown when fewer than 25 PSF stars are available.
*   **Transient finder accepts ``uint16`` images.** The image is converted to
    ``float32`` before SEP background estimation and extraction (SEP rejects
    ``uint16``).
*   **SCAMP refinement retries on too few matches.** SCAMP's default
    ``POSITION_MAXERR`` (1 arcmin) made refinement report "0 reference
    matches" when the plate solution was slightly off. A second attempt now
    widens ``POSITION_MAXERR``/``PIXSCALE_MAXERR``/``POSANGLE_MAXERR`` and
    relaxes ``SN_THRESHOLDS``.
*   **Cleaner messages.** The ASTRiDE candidate/diagnostic messages were
    removed, and the zero-point summary now reads
    ``Zero Point: X ± σ, calibrator scatter = S mag``.

**Astro-Colibri cross-match and cutouts**

*   **Nearest-event matching.** Astro-Colibri events are matched to the
    closest catalog source within 10 arcsec (``match_to_catalog_sky``); events
    without coordinates are ignored.
*   **Visible outcome.** The number of matches (or none, or service
    unavailable) is displayed in the app instead of only in the log.
*   **Cutouts with magnitude.** ``plot_astrocolibri_cutouts`` uses the WCS for
    pixel positions, circles the source, and shows its calibrated magnitude
    (filter-prefixed columns are now recognised).
*   **Hemisphere-aware template.** The HiPS template is fetched from
    PanSTARRS (north) or SkyMapper (south) and falls back to the other survey
    when the field is not covered.

**Session Isolation (concurrent users)**

*   **Per-session work folders.** Every browser session now gets its own
    ``work_<id>`` folder, both for temporary uploads
    (``/tmp/<user>/work_<id>/``) and for intermediate results
    (``rpp_results/<user>_results/work_<id>/``). Two sessions, including two
    sessions of the same account, can no longer overwrite or pick up each
    other's FITS files, headers or intermediate products. Final ZIP archives
    stay in the user-level ``rpp_results/<user>_results/`` folder.
*   **Reset works in one press.** The uploader widget is re-keyed on every
    reset, so the previously loaded file is dropped immediately instead of
    requiring a second click. Reset is now session-local: it only clears the
    current session's state and folders and no longer calls the global
    ``st.cache_data.clear()`` that affected every connected user.
*   **Run trigger consumed at start.** The *Start Analysis* trigger is cleared
    as soon as a run begins, so a failed or interrupted run cannot silently
    restart on the next interaction.
*   **Safer download matching.** Prefix matching of result files only pools
    files whose name continues with a delimiter (``_``, ``.``, ``-``), so
    ``image1`` no longer collects the files of ``image10``.

**Stale-File Cleanup**

*   Work folders and loose temporary uploads older than 24 hours (left by
    closed browsers or abandoned analyses) are removed once per session after
    login. Archived ZIP files are never touched, and the current session is
    always preserved.

**Cached FITS Parser and Shared State**

*   The cached FITS parser is now pure: it returns the data, header and a list
    of messages, and ``load_fits_data`` replays those messages in the calling
    session, so cached results no longer leak UI messages or state across
    users.
*   Matplotlib figures are closed after being displayed and after being saved,
    avoiding the process-wide figure registry being shared between sessions;
    the temporary ASTRiDE directory is removed after use.
*   Shared ``astroquery`` clients (Gaia main table, Skybot raw-response flag)
    are configured once at import instead of inside each function.

**Tests and Documentation**

*   New ``tests/test_session_isolation.py`` plus UI regression tests covering
    session folders, cleanup, delimiter matching, the pure parser and the
    import-time astroquery configuration.
*   README, features, troubleshooting and tutorial pages (English and French)
    describe session isolation, Reset and stale-file cleanup.

Version 1.8.7
-------------

**Released: October 7, 2026**

**Effective-Gain Resolution**

*   **``CVF`` conversion factor supported.** The FITS ``CVF`` keyword is now
    read and converted with ``gain = 1/CVF``. Explicit conversion keywords
    (``EGAIN``/``CCDGAIN`` in e-/ADU and ``CVF``) are tried before the
    conventional ``GAIN``, so a driver setting written to ``GAIN`` can no longer
    shadow a genuine conversion keyword.
*   **Driver-gain cameras recognised.** On QHY (abstract 0-100 scale; a QHY268M
    reaches ≈0.77 e-/ADU at ``GAIN=56``) and ZWO ASI (0.1 dB scale) cameras the
    bare ``GAIN`` value is a driver setting rather than e-/ADU and is now
    ignored: the pipeline falls back to ``EGAIN``/``CVF``, the user-supplied
    gain, or the frame's own photon-transfer estimate. The recognised cameras
    are listed in the editable ``DRIVER_GAIN_INSTRUMENT_PATTERNS``.
*   **Plausible gain range widened 0.02-20 → 0.02-100 e-/ADU**, so cameras with
    a genuinely large conversion factor are no longer rejected; the
    driver-setting guard, not the range, is what rejects QHY/ZWO values. The
    *Detector Gain* control accepts up to 1000 e-/ADU.
*   **One resolver everywhere.** ``src/astrometry.py`` and ``src/transient.py``
    now call ``resolve_effective_gain()`` instead of the old
    ``65535 / max(image)`` heuristic, so ``EGAIN``/``CVF``/driver handling is
    consistent across photometry, cosmic-ray masking and SEP detection.
*   Tests: ``CVF`` inversion, keyword precedence, driver-camera detection
    (including a false-positive guard) and the widened range are covered by new
    cases in ``tests/test_photometry.py``.
*   **Documentation**: the photometry-formulas, features and tutorial pages
    (English and French) and the README describe the new gain resolution.

**Pixel Scale and Login**

*   **No silent 1 arcsec/pixel default.** When the pixel scale cannot be read
    from the header or computed from the solved WCS, the run stops and asks for
    the new *Pixel scale override* value (sidebar, Parameters).
*   The CD-matrix pixel scale uses column norms (valid for rotated matrices) and
    ignores non-numeric keywords.
*   The login page reminds Firefox users to press Enter after typing the
    password.

Version 1.8.6
-------------

**Released: October 4, 2026**

**Transient Candidate Selection**

*   **SEP detection flags are now actually applied.** The candidate filter was
    called with stdpipe's default flag mask (``0x7F00``), which only matches
    stdpipe/SExtractor flag bits; raw SEP flags live in the low byte, so the
    mask never matched and no detection was ever flag-filtered.
    ``find_candidates()`` now passes ``SEP_REJECT_FLAGS`` (``0xDE``: object or
    aperture truncated at the frame boundary, deblending overflow, degenerate
    centroid, all-masked or non-positive aperture), so unreliable detections
    are discarded.
*   **Seeing-based cross-match radius.** SEP detections are matched to the
    pipeline photometry catalog within a seeing-sized radius
    (``clip(FWHM, 1, 4)`` arcsec) instead of a fixed 10 arcsec. Because
    ``spherical_match()`` returns *all* pairs inside the radius, the old value
    produced many-to-many matches in crowded fields, duplicating candidates and
    attaching the wrong PSF magnitude; only the nearest counterpart is now
    kept.
*   **Correct catalogue matching radius in ``filter_transient_candidates()``.**
    The function was previously given an already arcsec-scaled FWHM and no
    ``pixscale``, so the radius silently fell back to a fixed 1 arcsec. It now
    receives the FWHM in pixels and ``pixscale`` in degrees/pixel, giving the
    intended half-FWHM radius.
*   **Brightness-excess test documented and configurable.** ``checker_fn()``
    follows stdpipe's contract (a ``True`` entry is a confirmed catalogue match
    that removes the detection); the docstring now states this explicitly and
    the excess threshold was exposed as ``mag_threshold`` (default 2.0 mag).
    Detections whose magnitude difference cannot be evaluated are kept instead
    of being silently discarded.
*   Tests: the SEP flag mask and the candidate-checker semantics are covered by
    new cases in ``tests/test_new_features.py``.

**Documentation**

*   The user guide, advanced-features and API reference pages, the English and
    French tutorials, and the README now describe the current transient
    candidate algorithm (SEP flag filtering, seeing-based cross-matching and
    the brightness-excess rule).

Version 1.8.5
-------------

**Released: October 2, 2026**

**Transient Candidate Filtering**

*   Removed the minimum S/N filter from transient-candidate selection so that
    low-S/N detections remain eligible. The other catalog, known-source,
    Solar System-object, and magnitude-error validity filters remain in place.

**PSF Photometry Reliability**

*   **Star selection no longer silently skips PSF photometry.** The "adaptive"
    S/N threshold was computed from ``sqrt(flux)`` when no per-source S/N or
    ``flux_err`` was available, and then kept only the top half of the
    candidates. That is not a quality measure, and combined with the
    median..90th-percentile flux window it left fewer than the minimum 15 stars
    on clean fields, so the entire PSF step was dropped. The approximate-S/N cut
    is now skipped (the flux window already keeps the bright half).
*   **Saturation filter relaxed.** The previous limit
    ``min(peak_85 * 0.95, img_max * 0.85)`` rejected the brightest unsaturated
    stars, which are exactly the best ePSF stars. Truly saturated pixels are
    already flagged by the pipeline mask before detection, so this is now only a
    last-resort guard that trims peaks within 0.5% of the frame maximum.
*   On a clean 121-star field that previously produced only 14 candidates (below
    the 15-star minimum, so PSF photometry was skipped), **49 stars are now
    selected and PSF photometry runs on all 121 detected sources**.

**PSF Shape and Dimensions**

*   **Fitting region rounded, not truncated.** ``int(3 * fwhm)`` shifted the
    effective aperture radius by up to half a pixel; it now uses ``round``.
*   **ePSF extracted from a larger cutout.** The empirical PSF was built from a
    ``3 x FWHM`` cutout whose radius equals the largest aperture used for the
    encircled-energy correction, so its wings were truncated and normalised
    away. It is now extracted from a ``5 x FWHM`` cutout, so both the
    aperture-correction curve and the fitted fluxes reflect the real PSF wings;
    the fit itself still uses the compact ``fit_shape``.
*   **Model-centre convention fixed.** The centre of an ``N``-pixel grid is
    ``(N - 1) / 2``, not ``N / 2``. The old value reported a spurious
    half/oversampled-pixel peak offset (and could trigger an unnecessary
    re-centering that shifted the model off-centre).
*   **Aperture correction uses the fitted PSF.** After an optional re-centering
    the returned / ``st.session_state["epsf_model"]`` model is now the exact
    object used for the fits, so the encircled-energy correction can no longer be
    derived from a stale (non-re-centred) PSF.

**PSF-vs-Aperture Consistency Diagnostic**

*   A new ``check_psf_aperture_consistency()`` helper matches PSF and aperture
    sources by position and reports the median ``psf_mag - aperture_mag`` for
    each aperture. Because both methods share the total-flux scale, a median
    offset larger than ``PSF_APERTURE_CONSISTENCY_THRESHOLD`` (0.15 mag) is now
    reported as a warning; the per-aperture counts, offsets and robust scatter
    are stored in ``st.session_state["psf_aperture_consistency"]``.
*   **Per-source offset columns**: the same difference is exported for every
    aperture as ``psf_minus_aperture_mag_X_X`` (with filter-prefixed aliases)
    and is left NaN where only one method measured the source.
*   **Plotted diagnostic**: the results view renders the per-source
    ``psf_mag - aperture_mag`` offset -- a scatter against the calibrated PSF
    magnitude with the per-aperture median and zero lines, plus the offset
    histogram -- and saves it next to the other diagnostics as
    ``<base>_psf_aperture_offset.png``.
*   **Catalog cross-match robustness**: SkyBoT requests VOTable output and
    skips malformed service-error responses instead of reporting them as empty
    fields. The 10 Parsec catalog's proper motions are propagated to the
    observation epoch when available, and Aladin popups include SkyBoT and
    nearby-star identifications.
*   **Gaia DR3 fallback reliability**: the VizieR fallback now queries the Gaia
    DR3 cone-search catalog and uses its returned results. The direct TAP query
    no longer orders by an unsupported ``DISTANCE()`` expression.
*   **Crowded-field local background left unchanged by design**: an empirical
    check showed that zeroing the per-source local background for sources with
    overlapping ``fit_shape`` regions biases crowded fluxes ~5-7% high, while
    keeping it recovers the true flux, so no guard was applied.

**Documentation**

*   ``docs/PHOTOMETRY_FORMULAS.md``, ``docs/features.rst``,
    ``docs/advanced_features.rst`` and ``docs/api_reference.rst`` document the
    selection, ePSF-extraction and consistency-diagnostic changes; the English
    and French tutorials note the new diagnostic.
*   Regression tests cover the field that previously skipped PSF photometry, the
    ePSF centring, and the consistency diagnostic.

Version 1.8.4
-------------

**Released: October 2, 2026**

**Configuration Persistence**

*   **New sidebar parameters are persisted and restored**: the *Max Magnitude
    Error* (``mag_err_max``) and *Detector Gain* (``effective_gain``) controls
    are written into ``analysis_parameters``, so they are saved with the rest of
    the settings and reloaded at the next login. The two keys are now also
    seeded in ``default_analysis_params`` (``src/tools_app.py``).
*   **Round-trip coverage**: the full save/reload path is now tested for both
    the FastAPI (``/api/config``) and legacy Flask (``/save_config`` /
    ``/get_config``) backends in ``tests/test_config_persistence.py``, alongside
    structural checks of the save/load wiring in ``pages/app.py`` and
    ``pages/login.py``.
*   **Legacy backend dependencies declared**: ``flask`` and ``flask-cors`` are
    now listed in ``pyproject.toml`` and ``requirements.txt`` so the legacy
    backend and its tests work in a fresh install.

**Documentation**

*   The English and French tutorials now document the *Max Magnitude Error* and
    *Detector Gain* parameters.
*   ``docs/api.rst`` documents the FastAPI versus legacy Flask
    configuration-persistence paths; ``docs/advanced_features.rst`` adds
    configuration-persistence and testing sections; ``docs/installation.rst``
    and ``docs/features.rst`` cover the backend dependencies and the
    both-backend persistence.
*   Corrected several RST section underlines that were shorter than their
    titles.

Version 1.8.3
-------------

**Released: October 1, 2026**

Follow-up to the error-budget review (``docs/ERROR_BUDGET_REVIEW.md``).

**Photometry Error Budget**

*   **Local-background uncertainty uses the median estimator**: the annulus
    uncertainty is now ``ApertureStats.median_err`` (``√(π/2)·s/√A_ann``)
    instead of the mean-based ``std/√A_ann``. Using the mean's standard error
    for a median under-estimated the local term by ~25% (about 8% of
    ``σ_flux`` in the background-limited regime). The mean-based value is kept
    only as a fallback when ``median_err`` is unavailable.
*   **Aperture-correction systematic**: every aperture magnitude error now
    includes ``σ_apcorr = 1.0857 · f_apcorr · |apcorr − 1| / apcorr``
    (``APCORR_SYSTEMATIC_FRACTION``, default 0.20), exported as
    ``apcorr_mag_err_X_X``. The correction previously shifted the magnitude with
    no error attached, so the systematic was invisible at high S/N.
*   **Global systematic floor wired through**: ``SYSTEMATIC_MAG_ERR_FLOOR`` is
    now passed to ``add_calibrated_magnitudes`` via ``resolve_systematic_floor()``
    (session state / ``RPP_SYSTEMATIC_MAG_ERR_FLOOR`` / module default).
*   **Color-term uncertainty propagated**: ``fit_color_term_correction`` returns
    the fit parameter covariance (plus ``intercept_std`` / ``color_term_std``),
    and ``apply_color_term_correction`` writes ``color_term_offset_err`` and a
    ``*_colorcorr_err`` column
    (``√(σ_mag_calib² + σ_color_term_offset²)``) for every corrected magnitude,
    with filter-prefixed aliases.
*   **Random vs correlated components**: calibrated magnitude errors now carry
    ``*_mag_err_*_random`` (per-source) and ``*_mag_err_*_correlated``
    (field-common zero-point/systematic) columns in addition to the total error.
*   **Centroid-position term**: the aperture flux error now includes a centroid
    contribution. ``σ_pos = σ_PSF / S/N`` (floored, capped at 0.5×FWHM) and the
    flux change for a one-sigma displacement is measured by re-evaluating the
    aperture shifted by ``±σ_pos`` along x and y, capturing the real PSF
    asymmetry (``centroid_flux_err_X_X``).
*   **Zero-point uncertainty de-biased**: the scatter used for ``σ_zp`` is now
    taken from the sigma-clipped sample *before* the ``|residual| <= 0.5 mag``
    hard cut, which truncated the distribution and biased the uncertainty low.
    Both scatter estimates and ``N`` are stored in
    ``st.session_state["zero_point_diagnostics"]``.

**Noise Model and Background**

*   The last-resort constant background model (``_constant_background``) now
    uses the same MAD-based RMS as the normal ``Background2D`` path, so crowded
    frames that trigger the fallback do not get a source-inflated noise map.
*   The source-detection threshold is now derived from the MAD-based global
    scatter (``stdfunc="mad_std"``) instead of the plain sigma-clipped standard
    deviation, so detection and the error model use the same robust noise
    estimate and the effective threshold is not raised by undetected sources.

**PSF Photometry**

*   A per-source **local background** (``LocalBackground``, sigma-clipped median
    annulus, 2×–3× FWHM) is now subtracted before the PSF fits; sources whose
    annulus is fully masked or off-frame fall back to no subtraction rather than
    a NaN fit.
*   The **empirical PSF is built from de-contaminated star cutouts**: the same
    local background is removed from a copy of the image around each PSF star
    before ``extract_stars``, so the ePSF is not constructed from
    sky-contaminated stars. On a synthetic sky-pedestal frame the recovered PSF
    flux bias drops from ×2.5 (no local background) to ×1.001.

**Observability**

*   ``ee_fraction_X_X`` is exported next to ``apcorr_X_X`` so the aperture
    correction is auditable.
*   The misleading ``zero_point_error`` column was renamed to
    ``zero_point_scatter``; ``zero_point_std`` is now exported as a catalog
    column, while ``zero_point_scatter``, ``zero_point_scatter_kept`` and
    ``zero_point_n`` are recorded on the matched calibration table.
*   **Consistent prefixed error-component export**: ``drop_legacy_magnitude_columns``
    now also removes the legacy ``psf_mag_err_random`` / ``psf_mag_err_correlated``
    columns when a filter prefix applies, so the PSF error components are
    replaced by their prefixed aliases just like the aperture ones
    (``aperture_mag_err_*_random`` / ``_correlated``) instead of surviving
    unprefixed alongside them.
*   **Colour-corrected columns kept consistently**: ``aperture_mag_X_X_colorcorr_err``
    is no longer mistaken for a legacy aperture magnitude and dropped under a
    filter prefix; both the legacy and prefixed ``*_colorcorr`` /
    ``*_colorcorr_err`` columns are exported in parallel.

Version 1.8.1
-------------

**Released: September 29, 2026**

**Photometry Error Budget**

*   **Consistent flux for S/N, magnitude and error**: the S/N, instrumental
    magnitude and magnitude error are now all derived from one flux — the
    background-subtracted, aperture-corrected aperture sum. Previously the
    magnitude used the background-corrected flux while its error used the raw
    aperture sum, which diverged whenever the local background was not ~0.
*   **Local-background error propagation**: the uncertainty of the sigma-clipped
    annulus background is added to the flux error in quadrature
    (``σ_flux² = σ_aperture² + σ_total_bkg²``). This removes a systematic
    under-estimate of the error and over-estimate of the S/N of roughly 20% for
    the default annulus geometry.
*   **Robust local background**: the annulus background is now a sigma-clipped
    median (``ApertureStats``) rather than a plain mean, so neighbours and
    cosmic rays no longer bias it.
*   **Masked-pixel handling**: the pipeline mask (border, cosmic rays, satellite
    trails and NaNs) is now applied to both the source and the annulus
    photometry, and the exact masked-pixel area (``area_overlap``) is used.
*   **Aperture correction**: an encircled-energy correction derived from the
    empirical PSF (analytic Gaussian fallback) puts aperture and PSF photometry
    on the same total-flux scale. The factor is stored in the ``apcorr_X_X``
    column.
*   **Zero-point error propagation**: calibrated magnitude errors now combine
    the instrumental and zero-point uncertainties in quadrature
    (``σ_mag_calib = √(σ_mag_inst² + σ_zp²)``), and the zero point is no longer
    rounded to two decimals.
*   **Correct zero-point uncertainty for a median estimator**: the zero point is
    a median, so its formal uncertainty now carries the ``√(π/2)`` median factor
    (``zero_point_std = 1.2533 × scatter / √N``) instead of the mean-based
    ``scatter / √N``, which under-estimated it by ~20%. Exported as
    ``MEDIAN_STD_ERROR_FACTOR`` in ``src.pipeline``.
*   **Unreliable-photometry cut is now a last resort**: a source is removed only
    when *every* photometry method reports a magnitude error of 2 mag or more
    (``MAG_ERR_MAX``, up from 1 mag); a source with one usable method is kept,
    and sources carrying no error information at all (all NaN) are kept as well.
    The previous all-columns requirement deleted sources that still had a valid
    aperture (or PSF) measurement. The threshold is exposed as the *Max
    Magnitude Error* parameter, and 0 disables the cut entirely.

**Noise Model**

*   The effective gain for ``calc_total_error`` is read from the FITS
    ``GAIN``/``EGAIN`` keyword; when it is missing or invalid the data are
    treated as electrons (``effective_gain = 1``) instead of the previous
    arbitrary ``65535 / max(image)`` heuristic.
*   **Gain resolution order and empirical estimation**: when the header carries
    no usable gain, the pipeline now falls back to a user-supplied value (the
    *Detector Gain* sidebar parameter, ``analysis_parameters["effective_gain"]``
    or the ``RPP_EFFECTIVE_GAIN`` environment variable) and then to an
    empirical photon-transfer estimate computed from the frame itself
    (``1/slope`` of a robust variance-vs-signal fit on source-free sky boxes,
    accepted only when the sky level constrains it at ≥ 3σ and the result is
    physically plausible). Assuming ``gain = 1`` for ADU data overestimates the
    source Poisson variance by the true gain, i.e. the magnitude errors of
    bright sources by up to ``√gain``; that silent overestimate is now avoided
    whenever the gain can be measured or supplied.

**Sky Background Model**

*   **Robust noise map**: the per-box background RMS now uses the MAD-based
    estimator (`MADStdBackgroundRMS`) instead of the plain sigma-clipped standard
    deviation. Undetected sources in the boxes used to inflate the noise map by
    x1.11 / x1.38 / x2.03 at 15 / 12 / 10 px source spacing on synthetic frames,
    and that factor propagated directly into every flux error.
*   **Adaptive smoothing**: the median filter applied to the background mesh is
    capped at 3x3 when the mesh is small (< 12 boxes across). A 5x5 filter over
    an 8x8 mesh over-smoothed the model: on a 300 ADU sky ramp the subtracted
    frame was x2.24 noisier than the raw frame, vs x1.54 with a 3x3 filter.
*   **No more aborted runs on hard frames**: `Background2D` rejects any box with
    more than 10 % masked/clipped pixels (`exclude_percentile`) and raised a
    `ValueError` when that excluded *every* box, which happens for crowded fields
    and frames with large bad areas. The estimation is now retried with 50 % and
    100 % tolerance and with a coarser box, and falls back to a single
    sigma-clipped background level when no mesh can be built, so photometry still
    runs. The chosen fallback is reported to the user.
*   **Background plot failure is no longer fatal**: a missing background figure
    no longer calls `st.pyplot(None)`.

**Bug Fixes**

*   The local-background-flatness filter used for PSF model star selection
    compared the local background scatter to the (near-zero) local median of the
    sky-subtracted image, which could reject every candidate star and silently
    skip PSF photometry. It now compares the local scatter to the global
    background scatter.
*   **Cosmic-ray detection used a non-physical gain**: ``mask_and_remove_cosmic_rays``
    passed the raw ``GAIN`` value to ``astroscrappy.detect_cosmics``. On a real
    900 s frame carrying ``GAIN=56`` (a QHY driver setting, not e-/ADU) the
    predicted sky noise was ~1.8 ADU against a real 13.3 ADU, so **49% of the
    frame was flagged as cosmic rays** and excluded from detection and
    photometry. The routine now uses the resolved effective gain (plus a
    ``RDNOISE``/``READNOIS``/``ENOISE`` value when available) and discards an
    unphysical mask covering more than ``MAX_CR_MASK_FRACTION`` (20%) of the
    frame instead of deleting a large part of the science image. On that frame
    the mask went from 49.0% to 0.011% of the pixels, detections from 236 to
    312, and the calibrator scatter from 0.235 to 0.050 mag.
*   **``GAIN`` no longer hides a valid ``EGAIN``**: the keyword scan stopped at
    the first *present* key, so a frame with ``GAIN=0`` and ``EGAIN=3.12``
    (Moravian cameras) fell through to ``effective_gain = 1``. Invalid values are
    now skipped in favour of the next candidate keyword.
*   **Implausible gains are rejected**: values outside 0.02-20 e-/ADU
    (``GAIN_PLAUSIBLE_RANGE``) are reported and ignored, because a detector
    driver setting used as a gain inflates the S/N of bright sources by up to
    ~7x and corrupts the cosmic-ray mask.
*   ``validate_cross_match_results()`` read a ``gaia_separation_arcsec`` column
    that the cross-match never writes (it writes
    ``catalog_separation_arcsec``), so the median-separation sanity check was a
    no-op, and the function was never called at all. It now reads the real
    column (legacy alias accepted), tolerates missing coordinates, and is wired
    into the calibration workflow, which warns the user when the matches are
    clustered or show an unusually large median separation.

**New Output Columns**

*   ``aperture_sum_bkg_corr_err_X_X``: error of the background-subtracted flux,
    including the local-background term.
*   ``apcorr_X_X``: aperture-correction factor applied to each aperture.

Version 1.8.0
-------------

**Released: September 29, 2026**

**Photometric Calibration**

*   **Gaia VizieR fallback**: When the Gaia TAP service is unavailable or
    returns an error, the pipeline now automatically retries the query against
    the VizieR mirror with a reduced column set, keeping photometric calibration
    available during TAP outages.
*   **Zero-point calibration improvements**:

    *   Sigma clipping is applied before the final zero-point fit.
    *   Sources with residuals beyond ±0.5 mag from the median are excluded
        before the fit.
    *   Aperture 1.3× FWHM is used as the primary reference aperture for
        zero-point determination.
*   **Color-term calibration**:

    *   The pipeline can now fit a simple first-order color term from matched
        calibration stars when the selected calibration band has a supported
        catalog color.
    *   Supported color definitions currently include Gaia ``BP-RP`` and the
        Sloan-style colors ``g-r``, ``r-i``, and ``i-z``.
    *   Color-corrected magnitudes are written as parallel ``*_colorcorr``
        columns instead of replacing the baseline zero-point-calibrated
        magnitudes.

**New Functions**

*   **``plot_astrocolibri_cutouts()``**: Generates postage-stamp cutout images
    for Astro-Colibri transient candidates directly from the science frame,
    enabling rapid visual inspection alongside catalog alerts.
*   **``drop_legacy_magnitude_columns()``**: Utility to strip the legacy
    (non-prefixed) magnitude columns from the output catalog when only
    filter-prefixed aliases are needed.

**Robustness & Testing**

*   **Backend detection improvements**: Enhanced probing and fallback logic for
    FastAPI / legacy backend auto-detection; new ``tests/test_backend_detection.py``
    covers probe and fallback scenarios.
*   **Graceful stdpipe import handling**: ``ImportError`` from optional stdpipe
    components is caught and reported cleanly rather than crashing the module load.

**Infrastructure**

*   **Gunicorn UvicornWorker by default**: ``gunicorn.conf.py`` now sets
    ``uvicorn.workers.UvicornWorker``, preventing WSGI/ASGI mismatches.
*   **Dependency cleanup**: Removed unused ``mpmath`` dependency; tightened
    ``setuptools`` version constraints for reproducible builds.

**UI & Logging**

*   **Consistent Streamlit message levels**: Messages use success / warning /
    info / error severity throughout the pipeline.
*   **Reduced log noise**: Verbose debug messages removed from the hot path.
*   **FITS excluded from download list**: ``.fits`` files are no longer shown
    among the per-analysis download buttons.
*   **Archived ZIP lazy loading**: The sidebar archive browser now lists
    historical ZIP files without reading them at page load; a ZIP is read only
    when the user explicitly requests that specific archived download.
*   **FWHM sampling**: Source sample size increased to 1 250 for better seeing
    measurement stability.

**External Catalog Robustness**

*   **SkyBoT malformed-response fallback**: If SkyBoT returns an unreadable
    payload, the pipeline now logs a warning and continues without SkyBoT
    matches instead of surfacing an unexpected processing error.

**Documentation**

*   New FastAPI + Gunicorn troubleshooting section added to
    ``docs/troubleshooting.rst``.

Version 1.7.3
-------------

**Released: April 21, 2026**

*   **Photometry catalog aliases**: Exported calibrated magnitude columns now
    also include filter-prefixed aliases derived from the selected calibration
    band in ``GAIA_BANDS`` (for example ``rapasg_psf_mag``), while preserving
    the legacy column names for compatibility.

Version 1.7.2
-------------

**Released: April 20, 2026**

**User Interface Improvements**

*   **Sexagesimal Coordinate Display**: Target RA and DEC in the Statistics
    section are now shown in both decimal degrees and sexagesimal format
    (HH:MM:SS / ±DD:MM:SS). The same dual format is recorded in the processing
    log.
*   **Magnitude Error Plot — Logarithmic Scale**: The Y-axis of the "Magnitude
    Error vs Magnitude" scatter plot is now displayed on a logarithmic scale,
    improving readability across a wide dynamic range of photometric precision.

Version 1.7.0
-------------

**Released: 2026**

**Application Workflow & Stability**

*   **Explicit Start Gate**: Uploading a FITS file now stages it in the Streamlit UI without immediately triggering FITS loading, WCS checks, or photometry. Scientific processing starts only after clicking **Start Analysis**.
*   **Astrometry Toggle Fix**: The frontend now reads the astrometry option from the persisted analysis settings, so the sidebar toggle consistently controls forced astrometry checks.
*   **Safer Astrometry Fallback**: The upload/start workflow no longer passes an unset temporary file path into the astrometry step during the pre-start phase.

**Catalog & Network Handling**

*   **SkyBoT Timeout Adjustment**: SkyBoT queries in both catalog enhancement and transient filtering now use a 120-second timeout instead of the previous 300-second default.
*   **Graceful Remote-Service Behavior**: User-facing documentation and workflow descriptions were aligned with the existing behavior of continuing with partial results when optional remote services are slow, empty, or unavailable.

**Packaging & Environment**

*   **Missing Dependency Fix**: Added ``email-validator`` to project dependencies so FastAPI schema imports using ``EmailStr`` work in a clean environment.
*   **Local Editable Install**: Updated ``requirements.txt`` to install the local checkout with ``-e .`` instead of pointing back to a remote repository URL.
*   **Frontend Entry Point**: Added ``run_frontend.py`` so the configured ``pfr`` script entry point resolves correctly.
*   **Tooling Target Update**: Updated Black configuration to target Python 3.12.

**Documentation Refresh**

*   Updated ``README.md`` and ``docs/TUTORIAL.md`` to reflect the staged upload workflow, backend auto-detection, account features, maintenance scripts, astrometry fallback behavior, and partial-result handling.
*   Updated the main Sphinx pages including ``installation.rst``, ``usage.rst``, ``troubleshooting.rst``, ``features.rst``, ``examples.rst``, ``api_reference.rst``, ``user_guide.rst``, ``advanced_features.rst``, ``api.rst``, and ``index.rst``.
*   Replaced outdated terminology such as old start-button labels and stale API/module references with the current code paths and workflow names.

Version 1.6.0 (Current)
-----------------------

**Released: January 9, 2026**

**Storage & Data Organization**

*   **New FITS Storage Location**: Moved FITS file storage from ``data/fits/`` (inside project) to ``rpp_data/fits/`` (at parent level, same as ``rpp_results/``)
*   **WCS-Solved FITS Export**: New utility function ``save_fits_with_wcs()`` to save original FITS images with updated WCS headers after astrometric solving
*   **Consistent Directory Structure**: Both results (``rpp_results/``) and data files (``rpp_data/fits/``) are now stored at the same directory level outside the project folder

**Database Tracking System**

*   **New Database Tables**: Added three new tables for tracking analysis results:
    
    - ``wcs_fits_files``: Tracks WCS-solved FITS files stored in ``rpp_data/fits/``
    - ``zip_archives``: Tracks result ZIP archives stored in ``rpp_results/``
    - ``wcs_fits_zip_assoc``: Junction table linking FITS files to their associated ZIP archives (many-to-many relationship)

*   **Analysis History**: Each user can now view their complete analysis history including which FITS files generated which result archives
*   **Migration Script**: New script ``scripts/migrate_add_wcs_zip_tables.py`` safely adds new tables to existing databases without data loss
*   **Database Helper Module**: New ``src/db_tracking.py`` module provides functions for recording and querying analysis results

**API Improvements**

*   Updated ``FITS_STORAGE_ROOT`` configuration to use the new storage path
*   Added ``PARENT_DIR`` export for consistent path management across the application
*   New Pydantic schemas for WCS FITS files and ZIP archive API responses
*   New ORM models with proper relationships and constraints

**Quality Improvements**

*   Magnitude error threshold reduced from 2.0 to 1.5 for stricter quality filtering
*   Added absolute value handling for occasional negative magnitude error values

**Testing**

*   New comprehensive test suite ``tests/test_database.py`` with 35 tests covering:
    
    - ORM model creation and relationships
    - Pydantic schema validation
    - Database tracking functions
    - Migration script functionality
    - Integration workflows

Version 1.5.3
-------------

**Released: January 7, 2026**

**Photometry Calculation Improvements**

*   **Critical Bug Fix**: Fixed PSF S/N calculation that was incorrectly using ``sqrt(flux_err)`` instead of ``flux_err``. This was causing incorrect S/N values and magnitude errors for all PSF photometry.
*   **Precision Enhancement**: Removed S/N rounding to preserve full floating-point precision and avoid divide-by-zero errors in magnitude error calculations.
*   **Improved S/N Accuracy**: S/N now uses background-corrected flux (``aperture_sum_bkg_corr``) when available, providing more accurate estimates especially for faint sources.
*   **Error Propagation**: Added proper zero-point error propagation in calibrated magnitudes using quadrature: ``σ_mag_calib = √(σ_mag_inst² + σ_zp²)``
*   **Quality Flags**: Added photometric quality flags for both aperture and PSF photometry:
    
    - ``'good'``: S/N > 10 (reliable photometry)
    - ``'marginal'``: 5 < S/N ≤ 10 (use with caution)
    - ``'poor'``: S/N ≤ 5 (unreliable)
    - ``'unknown'``: missing data

**Documentation & Testing**

*   Comprehensive mathematical documentation of S/N and magnitude error formulas
*   New documentation file: ``docs/PHOTOMETRY_FORMULAS.md``
*   Complete unit test suite: ``tests/test_photometry.py``
*   Inline formula documentation in source code

**Technical Details**

*   S/N Formula: ``S/N = flux / flux_error``
*   Magnitude Error Formula: ``σ_mag = 1.0857 × (σ_flux / flux)`` where 1.0857 = 2.5/ln(10)
*   Calibrated Error: ``σ_mag_calib = √(σ_mag_inst² + σ_zp²)``

Version 1.5.1
-------------

**Released: January 3, 2026**

**Current Status**
*   All features from version 1.4.7 maintained and stabilized
*   Python 3.12 compatibility confirmed
*   All dependencies verified and up-to-date
*   Production-ready release with comprehensive testing

Version 1.4.7
-------------

**Released: December 30, 2025**

**Python 3.12 Upgrade & Dependency Management**
*   Upgraded to Python 3.12 with verified dependency resolution
*   Updated astropy to 7.1.1 with latest IERS data (0.2025.11.3.0.38.37)
*   Updated aiohttp to 3.11.18 with latest security patches
*   Updated certifi to 2025.1.31 for current CA certificate bundle
*   All dependencies verified compatible with Python 3.12

**Maintenance & Stability**
*   Comprehensive dependency audit and updates
*   Improved compatibility with latest Python ecosystem
*   Enhanced system library integration

Version 1.4.5
-------------

**Major Features**

*   **Advanced Astrometric Pipeline**: 
    *   Integrated `stdpipe` for robust local astrometric solving
    *   Added WCS refinement using SCAMP (via stdpipe) and GAIA DR3 catalog
    *   Automatic header validation and fixing for problematic WCS keywords

*   **Transient Detection (Beta)**:
    *   New **Transient Finder** module utilizing reference-survey comparison and catalog filtering
    *   Integration with PanSTARRS and SkyMapper surveys for template comparison
    *   Automatic candidate filtering against cataloged sources
    *   Hemisphere-based automatic survey selection (PanSTARRS north, SkyMapper south)

*   **Enhanced Cross-Matching**:
    *   Added **10 Parsec Catalog** for nearby star identification
    *   Improved filtering for existing catalogs
    *   Multi-band photometric support (GAIA G, BP, RP)

*   **Photometry Improvements**:
    *   Improved PSF Photometry with Gaussian fallback if EPSF building fails
    *   Automatic Cosmic Ray rejection enabled by default using `astroscrappy` (L.A.Cosmic algorithm)
    *   Multi-aperture photometry with 1.5×, 2.0× FWHM radii

*   **User Interface**:
    *   Astrometry check toggle for automatic/manual plate solving
    *   Aladin Lite v3 integration for improved interactive visualization
    *   Enhanced progress tracking and status updates

Version 0.9.8
-----------------------

**Major Features & Enhancements**

*   **Advanced Astrometric Pipeline**: 
    *   Integrated stdpipe for robust local plate solving with Astrometry.net
    *   Added WCS refinement using SCAMP and GAIA DR3 catalog
    *   Automatic header validation and fixing for problematic WCS keywords
    *   Force re-solve option for overriding existing WCS solutions
    *   Comprehensive coordinate validation and transformation checking

*   **Multi-Aperture Photometry**:
    *   Implemented four aperture radii (1.5×, 2.0×, 2.5×, 3.0× FWHM)
    *   Local background estimation with annular apertures for each radius
    *   Background-corrected and raw flux measurements
    *   Signal-to-noise ratio calculation for all apertures
    *   Backward compatibility with legacy single-aperture columns

*   **Enhanced PSF Photometry**:
    *   Empirical PSF model construction using EPSFBuilder
    *   Advanced star selection with quality filtering criteria
    *   Iterative PSF fitting with IterativePSFPhotometry
    *   PSF model visualization and FITS output
    *   Integration with multi-aperture photometry workflow

*   **Comprehensive Catalog Cross-Matching**:
    *   GAIA DR3 with quality filtering (variability, color, astrometry)
    *   SIMBAD for object identification and classification
    *   Astro-Colibri API for transient event correlation
    *   SkyBoT for solar system object identification
    *   AAVSO VSX for variable star catalog matching
    *   VizieR Milliquas for quasar and AGN identification

*   **Interactive Visualization**:
    *   Aladin Lite integration for interactive sky viewing
    *   Source overlay with detailed popup information
    *   Multiple survey options and coordinate display
    *   ESA Sky external viewer integration
    *   Real-time catalog cross-match results display

*   **Robust Error Handling**:
    *   Comprehensive error recovery mechanisms
    *   Graceful degradation when optional components fail
    *   Detailed error messages with recovery suggestions
    *   Automatic fallback options for common failure modes
    *   Enhanced logging with severity levels and timestamps

**Image Processing Improvements**:
    *   Advanced FITS header validation and automatic fixing
    *   Support for multi-extension FITS and data cubes
    *   RGB astronomical image handling (first channel extraction)
    *   Cosmic ray removal integration with L.A.Cosmic algorithm
    *   Enhanced background modeling with visualization and FITS output

**User Interface Enhancements**:
    *   Streamlined sidebar organization with collapsible sections
    *   Real-time parameter validation with helpful error messages
    *   Improved file upload handling with format detection
    *   Enhanced progress indicators and status updates
    *   Mobile-friendly responsive design improvements

**Data Management**:
    *   Automatic result archiving with timestamped ZIP files
    *   User-specific workspace isolation and management
    *   Archive browser for accessing previous results
    *   Automatic cleanup of old files (30+ day retention)
    *   Comprehensive output file generation and organization

**Performance Optimizations**:
    *   Streamlit caching for improved response times
    *   Memory-efficient handling of large FITS files
    *   Optimized algorithms for typical astronomical image sizes
    *   Background processing for time-consuming operations
    *   Resource monitoring and management improvements

**Configuration Management**:
    *   Enhanced parameter persistence across user sessions
    *   Observatory information extraction from FITS headers
    *   API key management with secure storage
    *   Default parameter sets for common use cases
    *   Configuration validation and error checking

**Minor Enhancements & Bug Fixes**

*   Improved coordinate system handling with multiple keyword support
*   Enhanced pixel scale calculation with multiple methods
*   Better FWHM estimation with outlier rejection
*   Robust airmass calculation with multiple date format support
*   Enhanced zero-point calculation with atmospheric extinction correction
*   Improved catalog query error handling and retry mechanisms
*   Better temporary file management and cleanup
*   Enhanced logging with structured format and severity levels

**Documentation Updates**:
    *   Comprehensive API reference documentation
    *   Updated installation guide with current dependencies
    *   Enhanced user guide with step-by-step workflows
    *   Feature documentation with detailed descriptions
    *   Troubleshooting guide with common issues and solutions

Version 1.0.0 (2023-09-01) - Previous Release
---------------------------------------------

**Features**:
- FITS image calibration (bias, dark, flat)
- Basic plate solving with astrometry.net
- Source detection and aperture photometry
- Zero-point calibration with Gaia DR3
- CSV catalog export
- Image visualization with matplotlib
- User-friendly Streamlit interface

**Requirements**:
- Python 3.8+
- Core packages: astropy, photutils, astroquery, streamlit, numpy, pandas, matplotlib

Version 0.9.0 (2023-07-15) - Beta Release
-----------------------------------------

**Initial Beta Features**:
- Core functionality implemented
- Basic calibration and photometry workflow
- Preliminary documentation
- Single-user desktop application

**Technical Foundation**:
- photutils-based photometry pipeline
- Basic FITS loading and display
- Simple aperture photometry
- Gaia DR3 cross-matching
- Basic CSV export functionality

Development Roadmap
-------------------

**Planned Features for v1.0**:
- Enhanced PSF modeling with multiple PSF types
- Advanced astrometric distortion correction
- Batch processing capabilities for image sequences
- Machine learning-based source classification
- Advanced visualization tools and interactive plots
- Integration with additional astronomical catalogs
- Performance optimizations for large surveys
- Enhanced documentation and tutorials

**Future Enhancements**:
- Multi-band photometry across different filters
- Time-series analysis for variable source monitoring
- Advanced statistical analysis tools
- Integration with observatory control systems
- Cloud-based processing and storage options
- Advanced machine learning features
- Professional data reduction pipeline integration

**Known Issues & Limitations**:
- SCAMP refinement requires proper installation and configuration
- Large images (>8K×8K) may require increased memory allocation
- Some catalog services may have temporary availability issues
- Complex WCS solutions with high-order distortions may need manual validation
- Network-dependent features require stable internet connection

**Migration Notes**:
- Configuration files from previous versions are automatically migrated
- Output file formats are backward compatible
- API changes are minimal and documented in the API reference
- Legacy single-aperture photometry columns are maintained for compatibility
