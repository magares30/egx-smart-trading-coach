"""Tests for the public EGX market-watch reader."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.egx_public_reader import (
    EGX_REQUEST_HEADERS,
    EgxPublicMarketWatchReader,
    EgxPublicPageType,
    _pick_best_table,
    normalize_stocks_table_to_ohlcv,
)

STOCKS_HTML = """
<html><body><table><tr><th>Symbol</th><th>Open</th><th>High</th><th>Low</th><th>Last</th><th>Volume</th></tr>
<tr><td>COMI</td><td>80</td><td>81</td><td>79</td><td>80.5</td><td>1000</td></tr></table></body></html>
"""

INDICES_HTML = """
<html><body><table><tr><th>Index</th><th>Last</th><th>Change</th></tr>
<tr><td>EGX30</td><td>28000</td><td>1.2</td></tr></table></body></html>
"""


@pytest.fixture
def reader(tmp_path: Path) -> EgxPublicMarketWatchReader:
    return EgxPublicMarketWatchReader(tmp_path)


def _mock_response(
    text: str,
    url: str = "https://egx.com.eg/en/Prices.aspx",
) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.content = text.encode("utf-8")
    response.status_code = 200
    response.url = url
    response.headers = {"Content-Type": "text/html; charset=utf-8"}
    response.raise_for_status = MagicMock()
    return response


def test_fetch_html_passes_browser_headers(
    reader: EgxPublicMarketWatchReader,
) -> None:
    with patch(
        "core.egx_public_reader.requests.get",
        return_value=_mock_response(STOCKS_HTML),
    ) as mock_get:
        html, metadata, errors, warnings = reader._fetch_html("https://egx.com.eg/en/Prices.aspx")

    assert html == STOCKS_HTML
    assert errors == []
    assert warnings == []
    assert metadata["status_code"] == 200
    assert metadata["final_url"] == "https://egx.com.eg/en/Prices.aspx"
    assert metadata["content_length"] == len(STOCKS_HTML.encode("utf-8"))
    assert metadata["content_type"] == "text/html; charset=utf-8"
    mock_get.assert_called_once_with(
        "https://egx.com.eg/en/Prices.aspx",
        timeout=30,
        headers=EGX_REQUEST_HEADERS,
    )


def test_fetch_html_retries_after_connection_error(
    reader: EgxPublicMarketWatchReader,
) -> None:
    with (
        patch(
            "core.egx_public_reader.requests.get",
            side_effect=[
                ConnectionResetError(10054, "connection reset"),
                _mock_response(STOCKS_HTML),
            ],
        ) as mock_get,
        patch("core.egx_public_reader.time.sleep") as mock_sleep,
    ):
        html, metadata, errors, warnings = reader._fetch_html("https://egx.com.eg/en/Prices.aspx")

    assert html == STOCKS_HTML
    assert errors == []
    assert metadata["status_code"] == 200
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once_with(2)
    for call in mock_get.call_args_list:
        assert call.kwargs["headers"] == EGX_REQUEST_HEADERS


def test_read_page_saves_csv_when_html_contains_table(
    reader: EgxPublicMarketWatchReader,
) -> None:
    stock_table = pd.DataFrame(
        {
            "Symbol": ["COMI"],
            "Open": [80.0],
            "High": [81.0],
            "Low": [79.0],
            "Last": [80.5],
            "Volume": [1000],
        }
    )

    with (
        patch(
            "core.egx_public_reader.requests.get",
            return_value=_mock_response(STOCKS_HTML),
        ),
        patch("core.egx_public_reader.pd.read_html", return_value=[stock_table]),
        patch(
            "core.egx_public_reader.EgxPublicMarketWatchReader._timestamp",
            return_value="20260701_120000",
        ),
    ):
        result = reader.read_page(EgxPublicPageType.STOCKS)

    assert result.success is True
    assert result.saved_csv is not None
    assert result.saved_csv.exists()
    assert result.rows == 1
    assert "Symbol" in result.columns


def test_read_page_returns_error_when_no_tables_exist(
    reader: EgxPublicMarketWatchReader,
) -> None:
    empty_html = "<html><body></body></html>"
    with (
        patch(
            "core.egx_public_reader.requests.get",
            return_value=_mock_response(
                empty_html,
                url="https://egx.com.eg/en/Indices.aspx",
            ),
        ),
        patch(
            "core.egx_public_reader.pd.read_html",
            side_effect=ValueError("No tables found"),
        ),
        patch(
            "core.egx_public_reader.EgxPublicMarketWatchReader._timestamp",
            return_value="20260701_120000",
        ),
    ):
        result = reader.read_page(EgxPublicPageType.INDICES)

    assert result.success is False
    assert result.saved_csv is None
    assert result.debug_html is not None
    assert result.debug_html.exists()
    assert result.debug_html.name == "debug_indices_20260701_120000.html"
    assert result.debug_html.read_text(encoding="utf-8") == empty_html
    assert any("No HTML tables found" in error for error in result.errors)
    assert any("Saved debug HTML:" in warning for warning in result.warnings)
    assert any("HTTP status code: 200" in warning for warning in result.warnings)
    assert any("Final URL:" in warning for warning in result.warnings)
    assert any("Content length:" in warning for warning in result.warnings)
    assert any("Content-Type:" in warning for warning in result.warnings)


def test_read_all_returns_four_results(reader: EgxPublicMarketWatchReader) -> None:
    with patch.object(reader, "read_page") as mock_read_page:
        mock_read_page.side_effect = [
            MagicMock(success=True, page_type=page_type)
            for page_type in EgxPublicMarketWatchReader.ALL_PAGE_TYPES
        ]
        results = reader.read_all()

    assert len(results) == 4
    assert mock_read_page.call_count == 4


def test_stocks_table_selection_prefers_stock_like_columns() -> None:
    stocks_table = pd.DataFrame(
        {
            "Symbol": ["COMI"],
            "Open": [80.0],
            "High": [81.0],
            "Low": [79.0],
            "Last": [80.5],
            "Volume": [1000],
        }
    )
    indices_table = pd.DataFrame(
        {"Index": ["EGX30"], "Last": [28000.0], "Change": [1.2]}
    )

    selected = _pick_best_table(
        [indices_table, stocks_table], EgxPublicPageType.STOCKS
    )

    assert selected is not None
    assert "Symbol" in selected.columns
    assert "Volume" in selected.columns


def test_normalize_stocks_table_to_ohlcv_creates_expected_columns(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "stocks_raw.csv"
    output_csv = tmp_path / "stocks_normalized.csv"
    pd.DataFrame(
        {
            "Date": ["2026-01-01", "2026-01-02"],
            "Symbol": ["COMI", "COMI"],
            "Open": [79.0, 80.0],
            "High": [81.0, 82.0],
            "Low": [78.5, 79.5],
            "Last": [80.5, 81.5],
            "Volume": [1000, 1100],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_stocks_table_to_ohlcv(input_csv, output_csv)

    assert result.valid is True
    assert output_csv.exists()
    normalized = pd.read_csv(output_csv)
    assert list(normalized.columns) == [
        "date",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    assert normalized.iloc[0]["symbol"] == "COMI"


def test_normalize_fails_when_ohlc_columns_missing(tmp_path: Path) -> None:
    input_csv = tmp_path / "stocks_incomplete.csv"
    output_csv = tmp_path / "stocks_normalized.csv"
    pd.DataFrame(
        {
            "Symbol": ["COMI"],
            "Last": [80.5],
            "Volume": [1000],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_stocks_table_to_ohlcv(input_csv, output_csv)

    assert result.valid is False
    assert not output_csv.exists()
    assert any("Missing required OHLCV columns" in error for error in result.errors)
