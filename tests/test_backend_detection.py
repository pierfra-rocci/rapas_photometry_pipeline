from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pages.api_client import detect_backend


def _response(status_code: int) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    return response


def test_detect_backend_prefers_api_when_healthy() -> None:
    with patch("pages.api_client.requests.get", return_value=_response(200)) as mock_get:
        backend = detect_backend(timeout=0.1)

    assert backend["mode"] == "api"
    assert backend["message"] == ""
    mock_get.assert_called_once_with("http://localhost:8000/health", timeout=0.1)


def test_detect_backend_does_not_fallback_to_legacy_when_api_is_unhealthy() -> None:
    with patch(
        "pages.api_client.requests.get",
        side_effect=[_response(500), requests.ConnectionError("legacy down")],
    ):
        backend = detect_backend(timeout=0.1)

    assert backend["mode"] == "unavailable"
    assert "API backend reachable but unhealthy" in backend["message"]


def test_detect_backend_uses_legacy_when_api_is_unreachable() -> None:
    with patch(
        "pages.api_client.requests.get",
        side_effect=[requests.ConnectionError("api down"), _response(405)],
    ):
        backend = detect_backend(timeout=0.1)

    assert backend["mode"] == "legacy"
    assert backend["message"] == "API backend not reachable. Using legacy server"


def test_detect_backend_reports_no_backend_when_both_probes_fail() -> None:
    with patch(
        "pages.api_client.requests.get",
        side_effect=[
            requests.ConnectionError("api down"),
            requests.ConnectionError("legacy down"),
        ],
    ):
        backend = detect_backend(timeout=0.1)

    assert backend["mode"] == "unavailable"
    assert "No backend available" in backend["message"]