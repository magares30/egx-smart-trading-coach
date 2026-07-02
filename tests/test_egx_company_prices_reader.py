"""Tests for public EGX GetCompanyPricesList reader."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

from core.egx_company_prices_reader import (
    DEFAULT_EGX_COMPANY_PREFIXES,
    EGX_COMPANY_PRICES_HEADERS,
    EGX_COMPANY_PRICES_URL,
    EGX_WARMUP_HEADERS,
    EGX_WARMUP_URL,
    EgxCompanyPricesReader,
    _extract_records,
)
from core.egx_public_reader import REQUEST_TIMEOUT_SECONDS


@pytest.fixture
def reader(tmp_path: Path) -> EgxCompanyPricesReader:
    return EgxCompanyPricesReader(tmp_path)


def _mock_response(
    payload: object,
    *,
    status_code: int = 200,
    content_type: str = "application/json; charset=utf-8",
) -> MagicMock:
    text = json.dumps(payload)
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.content = text.encode("utf-8")
    response.headers = {"Content-Type": content_type}
    response.raise_for_status = MagicMock()
    return response


def _mock_text_response(
    text: str,
    *,
    status_code: int = 200,
    content_type: str = "text/html; charset=utf-8",
) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.content = text.encode("utf-8")
    response.headers = {"Content-Type": content_type}
    response.raise_for_status = MagicMock()
    return response


def _mock_session(
    *,
    post_return: MagicMock | None = None,
    get_return: MagicMock | None = None,
) -> MagicMock:
    session = MagicMock()
    session.get.return_value = get_return or _mock_text_response("<html>EGX prices</html>")
    session.post.return_value = post_return or _mock_response(
        {"d": [{"Symbol": "COMI", "CompanyName": "Commercial International Bank"}]}
    )
    session.close = MagicMock()
    session.cookies = requests.cookies.RequestsCookieJar()
    return session


def test_extract_records_handles_asmx_wrapped_json() -> None:
    raw = {
        "d": '[{"Symbol":"COMI","CompanyName":"Commercial International Bank"}]'
    }

    records = _extract_records(raw)

    assert len(records) == 1
    assert records[0]["Symbol"] == "COMI"


def test_fetch_by_prefix_warmups_session_before_post(
    reader: EgxCompanyPricesReader,
) -> None:
    payload = {
        "d": [{"Symbol": "COMI", "CompanyName": "Commercial International Bank"}]
    }
    session = _mock_session(post_return=_mock_response(payload))
    call_order: list[str] = []

    def track_get(*args: object, **kwargs: object) -> MagicMock:
        call_order.append("get")
        return session.get.return_value

    def track_post(*args: object, **kwargs: object) -> MagicMock:
        call_order.append("post")
        return session.post.return_value

    session.get.side_effect = track_get
    session.post.side_effect = track_post

    with patch(
        "core.egx_company_prices_reader.requests.Session",
        return_value=session,
    ):
        result = reader.fetch_by_prefix("bank", count=20)

    assert result.success is True
    assert result.warmup_status_code == 200
    assert call_order == ["get", "post"]
    session.get.assert_called_once_with(
        EGX_WARMUP_URL,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers=EGX_WARMUP_HEADERS,
    )
    session.post.assert_called_once_with(
        EGX_COMPANY_PRICES_URL,
        json={"prefixText": "bank", "count": 20},
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers=EGX_COMPANY_PRICES_HEADERS,
    )
    session.close.assert_called_once()
    assert EGX_COMPANY_PRICES_HEADERS["Referer"] == EGX_WARMUP_URL


def test_fetch_by_prefix_does_not_write_cookie_files(
    reader: EgxCompanyPricesReader,
) -> None:
    session = _mock_session()

    with patch(
        "core.egx_company_prices_reader.requests.Session",
        return_value=session,
    ):
        reader.fetch_by_prefix("bank")

    cookie_files = list(reader.output_dir.glob("*cookie*"))
    assert cookie_files == []
    assert not hasattr(session.cookies, "filename")


def test_fetch_by_prefix_saves_debug_file_when_json_parse_fails(
    reader: EgxCompanyPricesReader,
) -> None:
    raw_body = "<html><body>Not JSON</body></html>"
    session = _mock_session(post_return=_mock_text_response(raw_body))

    with (
        patch(
            "core.egx_company_prices_reader.requests.Session",
            return_value=session,
        ),
        patch(
            "core.egx_company_prices_reader.EgxCompanyPricesReader._timestamp",
            return_value="20260701_120000",
        ),
    ):
        result = reader.fetch_by_prefix("bank")

    debug_path = reader.output_dir / "company_prices_debug_bank_20260701_120000.txt"
    assert result.success is False
    assert result.warmup_status_code == 200
    assert result.debug_path == debug_path
    assert debug_path.exists()
    saved = debug_path.read_text(encoding="utf-8")
    assert "Warmup HTTP status: 200" in saved
    assert "POST HTTP status: 200" in saved
    assert raw_body in saved
    assert result.errors
    assert "JSON parse failed" in result.errors[0]
    assert raw_body[:300] in result.errors[0]


def test_fetch_by_prefix_handles_request_error(reader: EgxCompanyPricesReader) -> None:
    session = _mock_session()
    session.post.side_effect = requests.Timeout("timed out")

    with patch(
        "core.egx_company_prices_reader.requests.Session",
        return_value=session,
    ):
        result = reader.fetch_by_prefix("bank")

    assert result.success is False
    assert result.records == []
    assert result.errors
    assert "Failed to fetch company prices" in result.errors[0]
    session.close.assert_called_once()


def test_fetch_by_prefix_retries_after_connection_reset(
    reader: EgxCompanyPricesReader,
) -> None:
    payload = {"d": [{"Symbol": "SWDY", "CompanyName": "El Sewedy Electric"}]}
    sessions = [_mock_session(), _mock_session(post_return=_mock_response(payload))]

    with (
        patch(
            "core.egx_company_prices_reader.requests.Session",
            side_effect=sessions,
        ),
        patch("core.egx_company_prices_reader.time.sleep") as mock_sleep,
    ):
        sessions[0].post.side_effect = ConnectionResetError(10054, "connection reset")
        result = reader.fetch_by_prefix("egypt")

    assert result.success is True
    assert sessions[0].get.call_count == 1
    assert sessions[0].post.call_count == 1
    assert sessions[1].get.call_count == 1
    assert sessions[1].post.call_count == 1
    mock_sleep.assert_called_once_with(2)
    sessions[0].close.assert_called_once()
    sessions[1].close.assert_called_once()


def test_fetch_many_prefixes_merges_and_deduplicates(
    reader: EgxCompanyPricesReader,
) -> None:
    responses = {
        "bank": {"d": [{"Symbol": "COMI", "CompanyName": "Commercial International Bank"}]},
        "cement": {
            "d": [
                {"Symbol": "COMI", "CompanyName": "Commercial International Bank"},
                {"Symbol": "SWDY", "CompanyName": "El Sewedy Electric"},
            ]
        },
    }

    def build_session(prefix: str) -> MagicMock:
        return _mock_session(post_return=_mock_response(responses[prefix]))

    session_map = {
        "bank": build_session("bank"),
        "cement": build_session("cement"),
    }
    prefix_queue = iter(["bank", "cement"])

    def session_factory() -> MagicMock:
        prefix = next(prefix_queue)
        return session_map[prefix]

    with patch(
        "core.egx_company_prices_reader.requests.Session",
        side_effect=session_factory,
    ):
        records = reader.fetch_many_prefixes(["bank", "cement"])

    assert len(records) == 2
    symbols = {record["Symbol"] for record in records}
    assert symbols == {"COMI", "SWDY"}


def test_save_results_csv_writes_raw_fields(reader: EgxCompanyPricesReader, tmp_path: Path) -> None:
    records = [
        {"Symbol": "COMI", "CompanyName": "Commercial International Bank", "Last": 80.5},
        {"Symbol": "SWDY", "CompanyName": "El Sewedy Electric", "Last": 45.2},
    ]
    output_path = tmp_path / "company_prices.csv"

    saved_path = reader.save_results_csv(records, output_path)
    frame = pd.read_csv(saved_path)

    assert saved_path == output_path
    assert len(frame) == 2
    assert list(frame.columns) == ["Symbol", "CompanyName", "Last"]


def test_read_and_save_writes_timestamped_csv(reader: EgxCompanyPricesReader) -> None:
    payload = {"d": [{"Symbol": "COMI", "CompanyName": "Commercial International Bank"}]}
    session = _mock_session(post_return=_mock_response(payload))

    with (
        patch(
            "core.egx_company_prices_reader.requests.Session",
            return_value=session,
        ),
        patch(
            "core.egx_company_prices_reader.EgxCompanyPricesReader._timestamp",
            return_value="20260701_120000",
        ),
    ):
        result = reader.read_and_save(list(DEFAULT_EGX_COMPANY_PREFIXES))

    assert result.saved_csv == reader.output_dir / "company_prices_20260701_120000.csv"
    assert result.saved_csv.exists()
    assert len(result.records) == 1
