"""Tests for public EGX endpoint probe."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from core.egx_endpoint_probe import (
    EGX_DISCOVERED_ENDPOINTS,
    EgxEndpointProbe,
    PREVIEW_CHAR_LIMIT,
)
from core.egx_public_reader import EGX_REQUEST_HEADERS


@pytest.fixture
def probe(tmp_path: Path) -> EgxEndpointProbe:
    return EgxEndpointProbe(tmp_path)


def _mock_response(
    text: str,
    *,
    url: str,
    status_code: int = 200,
    content_type: str = "application/json; charset=utf-8",
) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.content = text.encode("utf-8")
    response.status_code = status_code
    response.url = url
    response.headers = {"Content-Type": content_type}
    response.raise_for_status = MagicMock()
    return response


def test_probe_endpoint_passes_browser_headers(probe: EgxEndpointProbe) -> None:
    url = EGX_DISCOVERED_ENDPOINTS["GetPricesJson"]
    body = '{"prices": []}'

    with (
        patch(
            "core.egx_endpoint_probe.requests.get",
            return_value=_mock_response(body, url=url),
        ) as mock_get,
        patch(
            "core.egx_endpoint_probe.EgxEndpointProbe._timestamp",
            return_value="20260701_120000",
        ),
    ):
        result = probe.probe_endpoint("GetPricesJson", url)

    assert result.success is True
    assert result.status_code == 200
    assert result.content_type == "application/json; charset=utf-8"
    assert result.content_length == len(body.encode("utf-8"))
    assert result.preview == body
    mock_get.assert_called_once_with(
        url,
        timeout=30,
        headers=EGX_REQUEST_HEADERS,
    )


def test_probe_endpoint_saves_response_body(probe: EgxEndpointProbe) -> None:
    url = EGX_DISCOVERED_ENDPOINTS["MarketDataService"]
    body = "market-data-payload"

    with (
        patch(
            "core.egx_endpoint_probe.requests.get",
            return_value=_mock_response(
                body,
                url=url,
                content_type="text/plain; charset=utf-8",
            ),
        ),
        patch(
            "core.egx_endpoint_probe.EgxEndpointProbe._timestamp",
            return_value="20260701_120000",
        ),
    ):
        result = probe.probe_endpoint("MarketDataService", url)

    assert result.saved_path == probe.output_dir / "probe_MarketDataService_20260701_120000.txt"
    saved_path = probe.output_dir / "probe_MarketDataService_20260701_120000.txt"
    assert saved_path.read_text(encoding="utf-8") == body


def test_probe_endpoint_preview_is_limited_to_500_chars(probe: EgxEndpointProbe) -> None:
    url = EGX_DISCOVERED_ENDPOINTS["MarketFrame"]
    body = "x" * 800

    with (
        patch(
            "core.egx_endpoint_probe.requests.get",
            return_value=_mock_response(
                body,
                url=url,
                content_type="text/html; charset=utf-8",
            ),
        ),
        patch(
            "core.egx_endpoint_probe.EgxEndpointProbe._timestamp",
            return_value="20260701_120000",
        ),
    ):
        result = probe.probe_endpoint("MarketFrame", url)

    assert len(result.preview) == PREVIEW_CHAR_LIMIT
    assert result.preview == body[:PREVIEW_CHAR_LIMIT]
    assert len(result.saved_path.read_text(encoding="utf-8")) == 800


def test_probe_endpoint_retries_after_connection_error(probe: EgxEndpointProbe) -> None:
    url = EGX_DISCOVERED_ENDPOINTS["GetPricesJson"]
    body = '{"ok": true}'

    with (
        patch(
            "core.egx_endpoint_probe.requests.get",
            side_effect=[
                ConnectionResetError(10054, "connection reset"),
                _mock_response(body, url=url),
            ],
        ) as mock_get,
        patch("core.egx_endpoint_probe.time.sleep") as mock_sleep,
        patch(
            "core.egx_endpoint_probe.EgxEndpointProbe._timestamp",
            return_value="20260701_120000",
        ),
    ):
        result = probe.probe_endpoint("GetPricesJson", url)

    assert result.success is True
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once_with(2)


def test_probe_all_hits_every_discovered_endpoint(probe: EgxEndpointProbe) -> None:
    responses = {
        name: _mock_response(
            f"body-{name}",
            url=url,
            content_type="text/plain; charset=utf-8",
        )
        for name, url in EGX_DISCOVERED_ENDPOINTS.items()
    }

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        for name, endpoint_url in EGX_DISCOVERED_ENDPOINTS.items():
            if endpoint_url == url:
                return responses[name]
        raise AssertionError(f"Unexpected URL: {url}")

    with (
        patch("core.egx_endpoint_probe.requests.get", side_effect=fake_get) as mock_get,
        patch(
            "core.egx_endpoint_probe.EgxEndpointProbe._timestamp",
            return_value="20260701_120000",
        ),
    ):
        results = probe.probe_all()

    assert len(results) == len(EGX_DISCOVERED_ENDPOINTS)
    assert mock_get.call_count == len(EGX_DISCOVERED_ENDPOINTS)
    assert all(result.success for result in results)
    assert {result.name for result in results} == set(EGX_DISCOVERED_ENDPOINTS)


def test_probe_endpoint_records_request_failure(probe: EgxEndpointProbe) -> None:
    url = EGX_DISCOVERED_ENDPOINTS["GetPricesJson"]

    with patch(
        "core.egx_endpoint_probe.requests.get",
        side_effect=requests.Timeout("timed out"),
    ):
        result = probe.probe_endpoint("GetPricesJson", url)

    assert result.success is False
    assert result.saved_path is None
    assert result.errors
    assert "Failed to probe EGX endpoint" in result.errors[0]
