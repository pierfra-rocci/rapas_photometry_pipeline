"""Build the Sphinx documentation so broken RST fails the test suite.

Sphinx is a development dependency (``.[dev]``), so the test is skipped on a
plain runtime install instead of failing there. The build is hermetic: the
intersphinx inventories are not fetched, so a missing network never looks like
a documentation error.
"""

import io
import re
from pathlib import Path

import pytest

pytest.importorskip("sphinx")
pytest.importorskip("sphinx_rtd_theme")

ROOT_DIR = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT_DIR / "docs"


def test_documentation_parses_without_warnings(tmp_path):
    """Every page in ``docs/`` must parse and build with no warning."""
    from sphinx.application import Sphinx

    status = io.StringIO()
    warning = io.StringIO()
    app = Sphinx(
        srcdir=str(DOCS_DIR),
        confdir=str(DOCS_DIR),
        outdir=str(tmp_path / "out"),
        doctreedir=str(tmp_path / "doctrees"),
        buildername="dummy",
        confoverrides={"intersphinx_mapping": {}},
        status=status,
        warning=warning,
        freshenv=True,
    )
    app.build(force_all=True)
    messages = warning.getvalue()

    # Guard against a silently empty build (wrong source directory, for one).
    assert "user_guide" in app.env.found_docs

    # A broken page is not always a WARNING: an unknown directive is reported
    # as ``ERROR`` and still leaves ``statuscode`` at 0, so match both.
    problems = [
        line
        for line in messages.splitlines()
        if re.search(r"\b(WARNING|ERROR|SEVERE)\b", line)
    ]
    assert not problems, "\n".join(problems)
    assert app.statuscode == 0, messages
