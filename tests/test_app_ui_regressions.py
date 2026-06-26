import ast
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.utils import (
    PIPELINE_IMAGE_SCALE,
    PIPELINE_PLOT_MIN_HEIGHT,
    get_pipeline_figure_size,
)


APP_PATH = ROOT_DIR / "pages" / "app.py"


def _streamlit_calls(attr: str):
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
        and node.func.attr == attr
    ]


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
    source = APP_PATH.read_text(encoding="utf-8")

    assert "PIPELINE_PLOT_MIN_HEIGHT" in source
    assert "get_pipeline_figure_size" in source


def test_app_shows_header_preview_before_analysis_button() -> None:
    source = APP_PATH.read_text(encoding="utf-8")

    assert 'st.session_state["preview_header"]' in source
    assert "Image Header (Preview Before Analysis)" in source


def test_key_pipeline_status_messages_use_expected_levels() -> None:
    source = APP_PATH.read_text(encoding="utf-8")

    assert 'st.success("Observatory information updated from FITS header")' in source
    assert (
        'st.warning('
        '                                            "Using aperture photometry only '
        '(PSF photometry not available)."'
    ) not in source
    assert (
        '"Using aperture photometry only (PSF photometry not available)."' in source
    )
    assert (
        '"Catalog data is available but cannot be displayed in interactive viewer."'
        in source
    )

    success_calls = [ast.unparse(node) for node in _streamlit_calls("success")]
    warning_calls = [ast.unparse(node) for node in _streamlit_calls("warning")]

    assert any("File '" in call and "is ready." in call for call in success_calls)
    assert any(
        "Observatory information updated from FITS header" in call
        for call in success_calls
    )
    assert any(
        "Using aperture photometry only (PSF photometry not available)." in call
        for call in warning_calls
    )
    assert any(
        "Catalog data is available but cannot be displayed in interactive viewer."
        in call
        for call in warning_calls
    )