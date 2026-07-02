"""Tests for public EGX type endpoint probe."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from core.egx_endpoint_probe import EGX_DISCOVERED_ENDPOINTS
from core.egx_public_reader import EGX_REQUEST_HEADERS
from core.egx_type_probe import (
    EGX_TYPE_VALUES,
    EgxResponseKind,
    EgxTypeEndpointProbe,
    PREVIEW_CHAR_LIMIT,
    build_type_probe_url,
    detect_response_kind,
)


@pytest.fixture
def probe(tmp_path: Path) -> EgxTypeEndpointProbe:
    return EgxTypeEndpointProbe(tmp_path)


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


@pytest.mark.parametrize(
    ("body", "content_type", "expected"),
    [
        ('{"prices": []}', "application/json; charset=utf-8", EgxResponseKind.JSON),
        ("<html><body>EGX</body></html>", "text/html; charset=utf-8", EgxResponseKind.HTML),
        ("Symbol,Last,Volume\nCOMI,80.5,1000", "text/csv", EgxResponseKind.CSV),
        ("plain text payload", "text/plain; charset=utf-8", EgxResponseKind.UNKNOWN),
    ],
)
def test_detect_response_kind(
    body: str, content_type: str, expected: EgxResponseKind
) -> None:
    assert detect_response_kind(body, content_type) == expected


def test_build_type_probe_url() -> None:
    url = build_type_probe_url(EGX_DISCOVERED_ENDPOINTS["GetPricesJson"], 11)

    assert url == "https://egx.com.eg/en/GetPricesJson.aspx?type=11"


def test_probe_variant_passes_browser_headers(probe: EgxTypeEndpointProbe) -> None:
    url = build_type_probe_url(EGX_DISCOVERED_ENDPOINTS["GetPricesJson"], 11)
    body = '{"prices": []}'

    with (
        patch(
            "core.egx_type_probe.requests.get",
            return_value=_mock_response(body, url=url),
        ) as mock_get,
        patch(
            "core.egx_type_probe.EgxTypeEndpointProbe._timestamp",
            return_value="20260701_120000",
        ),
    ):
        result = probe.probe_variant("GetPricesJson", 11)

    assert result.success is True
    assert result.url == url
    assert result.response_kind == EgxResponseKind.JSON
    mock_get.assert_called_once_with(
        url,
        timeout=30,
        headers=EGX_REQUEST_HEADERS,
    )


def test_probe_variant_saves_response_body(probe: EgxTypeEndpointProbe) -> None:
    url = build_type_probe_url(EGX_DISCOVERED_ENDPOINTS["MarketDataService"], 12)
    body = "service-payload"

    with (
        patch(
            "core.egx_type_probe.requests.get",
            return_value=_mock_response(
                body,
                url=url,
                content_type="text/plain; charset=utf-8",
            ),
        ),
        patch(
            "core.egx_type_probe.EgxTypeEndpointProbe._timestamp",
            return_value="20260701_120000",
        ),
    ):
        result = probe.probe_variant("MarketDataService", 12)

    saved_path = probe.output_dir / "type_probe_MarketDataService_12_20260701_120000.txt"
    assert result.saved_path == saved_path
    assert saved_path.read_text(encoding="utf-8") == body


def test_probe_variant_preview_is_limited_to_500_chars(probe: EgxTypeEndpointProbe) -> None:
    url = build_type_probe_url(EGX_DISCOVERED_ENDPOINTS["MarketFrame"], 25)
    body = "x" * 800

    with (
        patch(
            "core.egx_type_probe.requests.get",
            return_value=_mock_response(
                body,
                url=url,
                content_type="text/html; charset=utf-8",
            ),
        ),
        patch(
            "core.egx_type_probe.EgxTypeEndpointProbe._timestamp",
            return_value="20260701_120000",
        ),
    ):
        result = probe.probe_variant("MarketFrame", 25)

    assert len(result.preview) == PREVIEW_CHAR_LIMIT
    assert result.response_kind == EgxResponseKind.HTML


def test_probe_variant_retries_after_connection_error(probe: EgxTypeEndpointProbe) -> None:
    url = build_type_probe_url(EGX_DISCOVERED_ENDPOINTS["GetPricesJson"], 11)
    body = '{"ok": true}'

    with (
        patch(
            "core.egx_type_probe.requests.get",
            side_effect=[
                ConnectionResetError(10054, "connection reset"),
                _mock_response(body, url=url),
            ],
        ) as mock_get,
        patch("core.egx_type_probe.time.sleep") as mock_sleep,
        patch(
            "core.egx_type_probe.EgxTypeEndpointProbe._timestamp",
            return_value="20260701_120000",
        ),
    ):
        result = probe.probe_variant("GetPricesJson", 11)

    assert result.success is True
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once_with(2)


def test_probe_all_hits_every_endpoint_and_type(probe: EgxTypeEndpointProbe) -> None:
    expected_count = len(EGX_DISCOVERED_ENDPOINTS) * len(EGX_TYPE_VALUES)

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        for endpoint, base_url in EGX_DISCOVERED_ENDPOINTS.items():
            for type_value in EGX_TYPE_VALUES:
                if url == build_type_probe_url(base_url, type_value):
                    return _mock_response(
                        f"body-{endpoint}-{type_value}",
                        url=url,
                        content_type="text/plain; charset=utf-8",
                    )
        raise AssertionError(f"Unexpected URL: {url}")

    with (
        patch("core.egx_type_probe.requests.get", side_effect=fake_get) as mock_get,
        patch(
            "core.egx_type_probe.EgxTypeEndpointProbe._timestamp",
            return_value="20260701_120000",
        ),
    ):
        results = probe.probe_all()

    assert len(results) == expected_count
    assert mock_get.call_count == expected_count
    assert all(result.success for result in results)


def test_probe_variant_records_request_failure(probe: EgxTypeEndpointProbe) -> None:
    with patch(
        "core.egx_type_probe.requests.get",
        side_effect=requests.Timeout("timed out"),
    ):
        result = probe.probe_variant("GetPricesJson", 11)

    assert result.success is False
    assert result.saved_path is None
    assert result.errors
    assert "Failed to probe EGX type endpoint" in result.errors[0]
