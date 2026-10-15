"""Structural regression checks on ``pages/app.py``.

These tests read the Streamlit script as source text rather than importing it
(the script has top-level side effects and drags in a GUI stack). To stay
robust, every assertion goes through the AST or through resolved string values
instead of raw source substrings, so reformatting, quote-style changes and line
rewrapping cannot break — or silently weaken — the checks.
"""

import ast
from pathlib import Path

from src.utils import (
    PIPELINE_IMAGE_SCALE,
    PIPELINE_PLOT_MIN_HEIGHT,
    get_pipeline_figure_size,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_PATH = ROOT_DIR / "pages" / "app.py"


def _app_tree() -> ast.Module:
    """Parse ``pages/app.py`` into an AST."""
    return ast.parse(APP_PATH.read_text(encoding="utf-8"))


def _streamlit_calls(attr: str) -> list[ast.Call]:
    """Return every ``st.<attr>(...)`` call found in the app script."""
    return [
        node
        for node in ast.walk(_app_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
        and node.func.attr == attr
    ]


def _first_string_arg(call: ast.Call) -> str | None:
    """Return a call's first argument as text, independent of source layout.

    Adjacent string literals are merged into a single ``Constant`` by the
    parser and f-strings are reduced to their literal fragments, so wrapping a
    message across lines or changing quote style cannot alter the result.
    Non-literal arguments yield ``None``.
    """
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr):
        return "".join(
            part.value
            for part in arg.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return None


def _streamlit_messages(level: str) -> list[str]:
    """Return the resolved message text of every ``st.<level>(...)`` call."""
    return [
        message
        for call in _streamlit_calls(level)
        if (message := _first_string_arg(call)) is not None
    ]


def _has_keyword(call: ast.Call, name: str, value: object) -> bool:
    """Return True if *call* passes ``name=<value>`` as a literal keyword."""
    return any(
        keyword.arg == name
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == value
        for keyword in call.keywords
    )


def test_app_has_no_streamlit_info_messages() -> None:
    # Info messages are allowed in focused guidance blocks, but should stay limited.
    assert len(_streamlit_calls("info")) <= 3


def test_compact_pipeline_scale_is_less_than_one() -> None:
    assert PIPELINE_IMAGE_SCALE < 1.0


def test_pipeline_figure_size_is_smaller_than_legacy() -> None:
    base_size = (8, 6)
    width, height = get_pipeline_figure_size(base_size)

    assert width < 2 * base_size[0]
    assert height < base_size[1]


def test_pipeline_plot_min_height_is_reduced() -> None:
    assert PIPELINE_PLOT_MIN_HEIGHT < 400


def test_app_uses_compact_pipeline_sizing_helpers() -> None:
    """The script must call the helper and reference the min-height constant."""
    tree = _app_tree()

    called_functions = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    referenced_names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }

    assert "get_pipeline_figure_size" in called_functions
    assert "PIPELINE_PLOT_MIN_HEIGHT" in referenced_names


def test_app_shows_header_preview_before_analysis_button() -> None:
    """The header preview expander must appear before the Start Analysis button."""
    tree = _app_tree()

    preview_expanders = [
        call
        for call in _streamlit_calls("expander")
        if _first_string_arg(call) == "Image Header (Preview Before Analysis)"
    ]
    analysis_buttons = [
        call
        for call in _streamlit_calls("button")
        if _has_keyword(call, "key", "start_analysis_button")
    ]

    assert preview_expanders, "header preview expander not found in pages/app.py"
    assert analysis_buttons, "Start Analysis button not found in pages/app.py"
    assert preview_expanders[0].lineno < analysis_buttons[0].lineno

    # The preview is driven by the session-state key captured at upload time.
    preview_key_reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == "preview_header"
    ]
    assert preview_key_reads, "preview_header session-state key is never accessed"


def test_key_pipeline_status_messages_use_expected_levels() -> None:
    success_messages = _streamlit_messages("success")
    warning_messages = _streamlit_messages("warning")

    assert "Observatory information updated from FITS header" in success_messages
    assert any(
        "File '" in message and "is ready." in message
        for message in success_messages
    )

    assert (
        "Using aperture photometry only (PSF photometry not available)."
        in warning_messages
    )
    assert (
        "Catalog data is available but cannot be displayed in interactive viewer."
        in warning_messages
    )


def test_app_renders_psf_aperture_offset_diagnostic() -> None:
    """The results view must render and save the PSF-minus-aperture offset plot."""
    tree = _app_tree()

    called_functions = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "plot_photometry_offsets" in called_functions

    string_constants = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert any("psf_aperture_offset" in value for value in string_constants)


def _load_pixel_scale_helpers(extract, override, stops):
    """Exec the pixel-scale helpers from app.py with stubbed Streamlit."""
    import types

    import numpy as np

    class _Stop(Exception):
        pass

    def _stop():
        stops.append(True)
        raise _Stop

    st = types.SimpleNamespace(
        session_state=types.SimpleNamespace(
            analysis_parameters={"pixel_scale_override": override}
        ),
        error=lambda *a, **k: None,
        stop=_stop,
    )
    names = {"_require_pixel_scale", "_user_pixel_scale"}
    funcs = [
        n for n in _app_tree().body
        if isinstance(n, ast.FunctionDef) and n.name in names
    ]
    ns = {"np": np, "st": st, "extract_pixel_scale": extract}
    exec(compile(ast.Module(body=funcs, type_ignores=[]), "app", "exec"), ns)
    return ns, _Stop


class TestPixelScaleOverride:
    def test_header_value_wins_over_override(self):
        ns, _ = _load_pixel_scale_helpers(lambda h: (0.5, "hdr"), 2.0, [])
        assert ns["_require_pixel_scale"]({}) == (0.5, "hdr")

    def test_override_used_when_header_has_no_scale(self):
        ns, _ = _load_pixel_scale_helpers(lambda h: (0.0, "x"), 2.0, [])
        assert ns["_require_pixel_scale"]({}) == (2.0, "user-provided")

    def test_stops_when_no_scale_and_no_override(self):
        import pytest

        stops = []
        ns, Stop = _load_pixel_scale_helpers(lambda h: (0.0, "x"), 0.0, stops)
        with pytest.raises(Stop):
            ns["_require_pixel_scale"]({})
        assert stops

    def test_no_silent_one_arcsec_default_in_app(self):
        src = APP_PATH.read_text(encoding="utf-8")
        assert "pixel_size_arcsec = 1.0" not in src


def test_file_uploader_key_rotates_so_reset_works_in_one_press():
    """A constant uploader key lets Reset leave a ghost file behind."""
    uploaders = _streamlit_calls("file_uploader")
    assert uploaders
    for call in uploaders:
        key = next((kw.value for kw in call.keywords if kw.arg == "key"), None)
        assert key is not None and not isinstance(key, ast.Constant), (
            "file_uploader key must be dynamic (rotating upload_generation)"
        )


def test_reset_never_clears_process_wide_streamlit_caches():
    """st.cache_data.clear() wipes the caches of every concurrent user."""
    tree = ast.parse((ROOT_DIR / "src" / "tools_app.py").read_text(encoding="utf-8"))
    func = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "clear_all_caches"
    )
    clears = [
        n for n in ast.walk(func)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "clear"
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr in ("cache_data", "cache_resource")
    ]
    assert not clears
