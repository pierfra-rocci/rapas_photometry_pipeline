import sys
import tomllib
from pathlib import Path
from types import ModuleType


ROOT_DIR = Path(__file__).resolve().parents[1]


streamlit_module = ModuleType("streamlit")
streamlit_module.warning = lambda *args, **kwargs: None
streamlit_module.info = lambda *args, **kwargs: None
streamlit_module.success = lambda *args, **kwargs: None
streamlit_module.error = lambda *args, **kwargs: None
streamlit_module.write = lambda *args, **kwargs: None
sys.modules.setdefault("streamlit", streamlit_module)


def test_pyproject_pins_compatible_setuptools() -> None:
    pyproject = tomllib.loads(
        (ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8")
    )

    build_requires = pyproject["build-system"]["requires"]
    dependencies = pyproject["project"]["dependencies"]

    assert any(req == "setuptools==71.0.0" for req in build_requires)
    assert any(dep == "setuptools==71.0.0" for dep in dependencies)


def test_requirements_pin_compatible_setuptools() -> None:
    requirements = (ROOT_DIR / "requirements.txt").read_text(encoding="utf-8")

    assert "setuptools==71.0.0" in requirements


def test_solve_with_astrometrynet_fails_gracefully_without_stdpipe_runtime() -> None:
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