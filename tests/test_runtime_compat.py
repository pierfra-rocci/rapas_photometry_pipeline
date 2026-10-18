import sys
import tomllib
from pathlib import Path
from types import ModuleType


ROOT_DIR = Path(__file__).resolve().parents[1]


def _stub_streamlit() -> ModuleType:
    """Build a minimal stand-in so the pipeline can be imported without Streamlit."""
    module = ModuleType("streamlit")
    module.warning = lambda *args, **kwargs: None
    module.info = lambda *args, **kwargs: None
    module.success = lambda *args, **kwargs: None
    module.error = lambda *args, **kwargs: None
    module.write = lambda *args, **kwargs: None
    return module


def test_pyproject_pins_compatible_setuptools() -> None:
    pyproject = tomllib.loads(
        (ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8")
    )

    build_requires = pyproject["build-system"]["requires"]
    dependencies = pyproject["project"]["dependencies"]

    assert any(req == "setuptools==71.0.0" for req in build_requires)
    assert any(dep == "setuptools==71.0.0" for dep in dependencies)


def test_solve_with_astrometrynet_fails_gracefully_without_stdpipe_runtime(
    monkeypatch,
) -> None:
    # Install the stub only for this test: replacing ``streamlit`` in
    # ``sys.modules`` at import time would leak into every other test module
    # collected in the same pytest process.
    monkeypatch.setitem(sys.modules, "streamlit", _stub_streamlit())
    import src.astrometry as astrometry_module

    original_error = astrometry_module.STDPIPE_IMPORT_ERROR
    try:
        astrometry_module.STDPIPE_IMPORT_ERROR = ModuleNotFoundError(
            "No module named 'pkg_resources'"
        )
        wcs_obj, header, log_messages, error = (
            astrometry_module.solve_with_astrometrynet("dummy.fits")
        )
    finally:
        astrometry_module.STDPIPE_IMPORT_ERROR = original_error

    assert wcs_obj is None
    assert header is None
    assert error is not None
    assert "setuptools==71.0.0" in error
    assert any("ERROR:" in message for message in log_messages)