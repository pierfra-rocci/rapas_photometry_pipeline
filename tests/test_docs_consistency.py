"""Consistency checks between user-facing messages and the documentation.

The main pipeline and the transient finder share the same PSF fallback warning
wording, which is documented verbatim in the user guide.  Other user-facing
strings (UI labels, titles and log messages) are also quoted in the
documentation.  These tests fail when the code and documentation drift apart.
"""

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_GUIDE = PROJECT_ROOT / "docs" / "user_guide.rst"


def _user_guide_text() -> str:
    return USER_GUIDE.read_text(encoding="utf-8")


def _string_literals(path: str) -> set[str]:
    """Return the string literals used in a Python source file."""
    tree = ast.parse((PROJECT_ROOT / path).read_text(encoding="utf-8"))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _source_uses_message(path: str, message: str) -> bool:
    """Return True when *message* appears in a literal of the source file."""
    return any(message in literal for literal in _string_literals(path))


def _normalise_whitespace(text: str) -> str:
    """Collapse line breaks and repeated spaces for cross-file comparisons."""
    return " ".join(text.split())


# Other user-facing strings shared by the code and the documentation.
# Each entry is (message, source path, documentation paths).
SHARED_USER_FACING_MESSAGES = (
    (
        "Enable Transient Finder",
        "pages/app.py",
        ("docs/TUTORIAL.md", "docs/TUTORIAL_FR.md"),
    ),
    (
        "Pixel scale override (arcsec/pixel)",
        "pages/app.py",
        ("docs/TUTORIAL_FR.md",),
    ),
    (
        "RAPAS Photometry Pipeline",
        "pages/app.py",
        ("docs/troubleshooting.rst",),
    ),
    (
        "No sources found from 10 Parsec catalog",
        "src/xmatch_catalogs.py",
        ("docs/troubleshooting.rst",),
    ),
    (
        "is outside the plausible effective-gain range",
        "src/pipeline.py",
        ("docs/troubleshooting.rst",),
    ),
)


def test_user_guide_documents_shared_psf_fallback_message():
    """The main-pipeline PSF fallback warning must appear in the user guide."""
    from src.tools_pipeline import PSF_FALLBACK_MESSAGE

    assert PSF_FALLBACK_MESSAGE in _user_guide_text(), (
        "docs/user_guide.rst does not contain the shared PSF fallback message "
        f"{PSF_FALLBACK_MESSAGE!r}"
    )


def test_user_guide_documents_transient_psf_fallback_message():
    """The transient-finder PSF fallback warning must appear in the user guide."""
    from src.transient import TRANSIENT_PSF_FALLBACK_MESSAGE

    assert TRANSIENT_PSF_FALLBACK_MESSAGE in _user_guide_text(), (
        "docs/user_guide.rst does not contain the transient PSF fallback "
        f"message {TRANSIENT_PSF_FALLBACK_MESSAGE!r}"
    )


@pytest.mark.parametrize(
    ("message", "source_path", "doc_paths"),
    SHARED_USER_FACING_MESSAGES,
    ids=[entry[0] for entry in SHARED_USER_FACING_MESSAGES],
)
def test_shared_user_facing_message_in_code_and_docs(
    message: str, source_path: str, doc_paths: tuple[str, ...]
) -> None:
    """A shared message must still be used in code and quoted in the docs."""
    assert _source_uses_message(
        source_path, message
    ), f"{source_path} no longer uses the shared message {message!r}"
    for doc_path in doc_paths:
        text = (PROJECT_ROOT / doc_path).read_text(encoding="utf-8")
        assert message in _normalise_whitespace(
            text
        ), f"{doc_path} does not contain {message!r}"
