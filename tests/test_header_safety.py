from unittest.mock import Mock

from src.header_utils import copy_header_or_none, select_science_header


class TestHeaderSafety:
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
