import os
from unittest.mock import Mock

import numpy as np
import pytest

from src.tools_pipeline import extract_pixel_scale
from src.utils import zip_results_on_exit


class TestExtractPixelScale:
    def test_invalid_keyword_is_skipped(self):
        scale, src = extract_pixel_scale({"PIXSCALE": "abc", "SECPIX": 0.5})
        assert scale == 0.5 and "SECPIX" in src

    def test_rotated_cd_matrix_uses_column_norms(self):
        c = 1.0 / 3600.0
        theta = np.radians(45)
        hdr = {
            "CD1_1": c * np.cos(theta), "CD1_2": -c * np.sin(theta),
            "CD2_1": c * np.sin(theta), "CD2_2": c * np.cos(theta),
        }
        scale, _ = extract_pixel_scale(hdr)
        assert scale == pytest.approx(1.0, rel=1e-6)

    def test_garbage_cd_falls_back_to_zero_for_caller_guard(self):
        scale, _ = extract_pixel_scale({"CD1_1": "x", "CD2_2": "y"})
        assert scale == 0.0

    def test_none_header(self):
        assert extract_pixel_scale(None)[0] == 1.0


class TestZipPrefix:
    def test_does_not_zip_files_of_other_images_sharing_prefix(self, tmp_path):
        obj = Mock()
        obj.name = "img.fits"
        (tmp_path / "img_cat.csv").write_text("a")
        (tmp_path / "img10_cat.csv").write_text("b")
        name, path = zip_results_on_exit(obj, str(tmp_path))
        if path:
            import zipfile
            names = zipfile.ZipFile(path).namelist()
            assert "img10_cat.csv" not in names
