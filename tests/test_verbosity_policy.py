"""Regression tests for the analysis-view verbosity policy.

Only a zero-source cross-match or a transient search that found nothing may
raise a warning in the analysis view. Every other analysis diagnostic is an
info note, while the log buffer keeps the original ``WARNING`` level.
"""

from __future__ import annotations

import ast
from io import StringIO
from pathlib import Path

import pytest

import src.tools_app as tools_app


ROOT_DIR = Path(__file__).resolve().parents[1]

# Modules whose ``st.warning`` calls are part of the analysis workflow.
ANALYSIS_MODULES = (
    "src/pipeline.py",
    "src/psf.py",
    "src/transient.py",
    "src/tools_pipeline.py",
)

# The only warnings the analysis may still show: a transient search that found
# nothing. The cross-match zero-source cases come through ``handle_log_messages``
# as WARNING strings and are covered below.
KEPT_ANALYSIS_WARNINGS = {
    "No objects found in the image.",
    "No candidates returned from filtering.",
}


class _SessionState:
    """Session state with the attribute the log writer reads."""

    def __init__(self) -> None:
        self.log_buffer = StringIO()


class _FakeStreamlit:
    """Minimal Streamlit stand-in that records displayed messages."""

    def __init__(self) -> None:
        self.session_state = _SessionState()
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def info(self, message, *args, **kwargs) -> None:
        self.infos.append(str(message))

    def warning(self, message, *args, **kwargs) -> None:
        self.warnings.append(str(message))

    def error(self, message, *args, **kwargs) -> None:
        self.errors.append(str(message))


@pytest.fixture
def fake_st(monkeypatch):
    fake = _FakeStreamlit()
    monkeypatch.setattr(tools_app, "st", fake)
    return fake


def test_generic_analysis_warning_is_shown_as_info(fake_st):
    tools_app.handle_log_messages(
        ["WARNING: Astropy WCS correction: adjusted the WCS", "INFO: log only"]
    )

    assert fake_st.warnings == []
    assert fake_st.infos == ["Astropy WCS correction: adjusted the WCS"]
    # The diagnostic level is preserved in the log, only the UI is softened.
    log = fake_st.session_state.log_buffer.getvalue()
    assert "WARNING" in log
    assert "Astropy WCS correction" in log


def test_zero_source_cross_match_stays_a_warning(fake_st):
    tools_app.handle_log_messages(
        ["WARNING: No sources to cross-match with catalogs."]
    )

    assert fake_st.warnings == ["No sources to cross-match with catalogs."]
    assert fake_st.infos == []


def test_zero_catalog_sources_stay_a_warning(fake_st):
    tools_app.handle_log_messages(
        ["WARNING: No catalog sources found within search radius."]
    )

    assert fake_st.warnings == ["No catalog sources found within search radius."]
    assert fake_st.infos == []


def test_zero_source_transient_stays_a_warning(fake_st):
    tools_app.handle_log_messages(["WARNING: No candidates returned from filtering."])

    assert fake_st.warnings == ["No candidates returned from filtering."]
    assert fake_st.infos == []


def _st_warning_messages(path: Path) -> list[str]:
    """Return the literal messages of every ``st.warning(...)`` call in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    messages: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "warning"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "st"
        ):
            continue
        argument = node.args[0] if node.args else None
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            messages.append(argument.value)
        elif isinstance(argument, ast.JoinedStr):
            messages.append(
                "".join(
                    part.value
                    for part in argument.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
            )
        else:
            messages.append("<dynamic>")
    return messages


def test_analysis_modules_warn_only_about_zero_sources():
    unexpected = [
        (relative, message)
        for relative in ANALYSIS_MODULES
        for message in _st_warning_messages(ROOT_DIR / relative)
        if message not in KEPT_ANALYSIS_WARNINGS
    ]

    assert unexpected == []
