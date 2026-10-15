from unittest.mock import Mock

from astropy.io import fits
from astropy.wcs import WCS

from src.header_utils import copy_header_or_none, select_science_header
from src.tools_pipeline import fix_header


class TestHeaderSafety:
    def test_fix_header_adds_sip_suffix_when_coefficients_are_present(self):
        header = fits.Header()
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CRVAL1"] = 123.4
        header["CRVAL2"] = 45.6
        header["CRPIX1"] = 50.0
        header["CRPIX2"] = 50.0
        header["A_ORDER"] = 2
        header["B_ORDER"] = 2
        header["A_2_0"] = 1e-5
        header["B_0_2"] = 1e-5

        fixed_header, _ = fix_header(header)

        assert fixed_header["CTYPE1"] == "RA---TAN-SIP"
        assert fixed_header["CTYPE2"] == "DEC--TAN-SIP"
        assert fixed_header["A_2_0"] == 1e-5
        assert fixed_header["B_0_2"] == 1e-5
        assert WCS(fixed_header).sip is not None

    def test_fix_header_preserves_existing_sip_suffix(self):
        header = fits.Header()
        header["CTYPE1"] = "RA---TAN-SIP"
        header["CTYPE2"] = "DEC--TAN-SIP"
        header["CRVAL1"] = 123.4
        header["CRVAL2"] = 45.6
        header["CRPIX1"] = 50.0
        header["CRPIX2"] = 50.0
        header["A_ORDER"] = 2
        header["B_ORDER"] = 2

        fixed_header, _ = fix_header(header)

        assert fixed_header["CTYPE1"] == "RA---TAN-SIP"
        assert fixed_header["CTYPE2"] == "DEC--TAN-SIP"

    def test_fix_header_does_not_add_sip_without_coefficients(self):
        header = fits.Header()
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CRVAL1"] = 123.4
        header["CRVAL2"] = 45.6
        header["CRPIX1"] = 50.0
        header["CRPIX2"] = 50.0

        fixed_header, _ = fix_header(header)

        assert fixed_header["CTYPE1"] == "RA---TAN"
        assert fixed_header["CTYPE2"] == "DEC--TAN"

    def test_select_science_header_keeps_original_when_solved_is_none(self):
        original_header = {"OBJECT": "M31", "EXPTIME": 120.0}

        selected = select_science_header(original_header, solved_header=None)

        assert selected is original_header
        assert selected["OBJECT"] == "M31"

    def test_select_science_header_prefers_solved_when_available(self):
        original_header = {"OBJECT": "M31", "EXPTIME": 120.0}
        solved_header = {"OBJECT": "M31", "EXPTIME": 120.0, "CRVAL1": 12.34}

        selected = select_science_header(original_header, solved_header)

        assert selected is solved_header
        assert selected["CRVAL1"] == 12.34

    def test_copy_header_or_none_returns_none_for_missing_header(self):
        copied = copy_header_or_none(None)

        assert copied is None

    def test_copy_header_or_none_uses_copy_method(self):
        header = Mock()
        expected_copy = {"copied": True}
        header.copy.return_value = expected_copy

        copied = copy_header_or_none(header)

        assert copied == expected_copy
        header.copy.assert_called_once_with()
