"""Consistency checks for the project version number.

The release version is declared independently in three places:

* ``pyproject.toml`` (``project.version``) - packaging metadata
* ``src/__version__.py`` (``version`` / ``version_info``) - runtime value used by the UI
* ``docs/conf.py`` (``release`` / ``version``) - Sphinx documentation

They must not drift apart, otherwise the installed distribution metadata, the
running application and the published documentation can disagree about which
release is current. The user-facing documentation (``README.md`` and
``docs/changelog.rst``) must also announce the current release.
"""

import ast
import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def _conf_py_versions() -> tuple:
    """Return the ``(release, version)`` strings assigned in ``docs/conf.py``.

    The file is parsed with :mod:`ast` rather than imported, so the test does
    not require Sphinx to be installed in the test environment.
    """
    source = (PROJECT_ROOT / "docs" / "conf.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    values = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                values[target.id] = node.value.value
    return values["release"], values["version"]


def test_version_sources_agree():
    """pyproject.toml, src/__version__.py and docs/conf.py must match."""
    from src.__version__ import version, version_info

    pyproject_version = _pyproject_version()
    conf_release, conf_short_version = _conf_py_versions()

    assert pyproject_version == version, (
        f"pyproject.toml ({pyproject_version}) != src/__version__.py ({version})"
    )
    assert conf_release == version, (
        f"docs/conf.py release ({conf_release}) != src/__version__.py ({version})"
    )
    # The Sphinx short version is the major.minor pair.
    assert conf_short_version == ".".join(version.split(".")[:2]), (
        f"docs/conf.py version ({conf_short_version}) is not the major.minor of "
        f"{version}"
    )
    # version_info must describe the same release as the version string.
    assert tuple(version_info[:3]) == tuple(
        int(part) for part in version.split(".")[:3]
    ), f"version_info ({version_info}) does not match version ({version})"


def test_version_is_semver_like():
    """The version string is a plain three-component semantic version."""
    from src.__version__ import version

    parts = version.split(".")
    assert len(parts) == 3, f"expected MAJOR.MINOR.PATCH, got {version!r}"
    assert all(part.isdigit() for part in parts), f"non-numeric version {version!r}"


def _rst_section_titles(text: str) -> list:
    """Return the RST section titles whose underline is a run of dashes."""
    lines = text.splitlines()
    titles = []
    for index, line in enumerate(lines[:-1]):
        if line.strip() and re.fullmatch(r"-{3,}", lines[index + 1].strip()):
            titles.append(line.strip())
    return titles


def test_readme_mentions_current_version():
    """README.md must announce the current release."""
    from src.__version__ import version

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert f"targets version {version}" in readme, (
        f"README.md does not state that the project targets version {version}"
    )
    assert f"### Current Release ({version})" in readme, (
        f"README.md has no 'Current Release ({version})' changelog section"
    )


def test_changelog_mentions_current_version():
    """docs/changelog.rst must open with the current release section."""
    from src.__version__ import version

    changelog = (PROJECT_ROOT / "docs" / "changelog.rst").read_text(encoding="utf-8")
    titles = _rst_section_titles(changelog)

    expected = f"Version {version}"
    assert expected in titles, (
        f"docs/changelog.rst has no '{expected}' section (found: {titles})"
    )
    assert titles[0] == expected, (
        f"the newest changelog section is '{titles[0]}', expected '{expected}'"
    )
