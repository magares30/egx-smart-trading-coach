"""Tests for latest report section extraction helpers."""

from __future__ import annotations

from core.latest_report_sections import (
    CLOUD_PORTFOLIO_STATE_MESSAGE,
    build_report_metadata_payload,
    extract_report_metadata,
    format_report_metadata_block,
    parse_pnl_lines_from_sections,
    resolve_daily_overview_paper_pnl,
    resolve_portfolio_data_status,
)


def _cloud_payload_without_portfolio() -> dict:
    return {
        "report_date": "2026-07-03",
        "created_at": "2026-07-03T10:00:00+00:00",
        "market_session": {"status": "CLOSED"},
        "executive_summary": {
            "paper_pnl": "n/a",
            "market": "CLOSED | BULLISH | Paper entries disabled",
        },
        "paper_portfolio": {
            "available": False,
            "message": "No paper portfolio data found.",
            "open_positions_count": 0,
        },
        "paper_trading_performance": {
            "available": False,
            "message": "No paper portfolio data found.",
        },
        "report_metadata": {
            "generated_at": "2026-07-03T10:00:00+00:00",
            "data_provider": "tradingview",
            "data_provider_label": "TradingView",
            "market_status": "CLOSED",
            "paper_portfolio_present": False,
            "paper_performance_present": False,
            "paper_portfolio_storage_on_server": False,
            "talib_available": False,
            "talib_mode": "fallback",
            "talib_reason": "talib package not installed",
            "tradingview_technical_available": True,
        },
        "sections": [
            {
                "title": "Summary",
                "lines": ["- Data Provider: TradingView"],
            },
            {
                "title": "Paper Portfolio",
                "lines": ["- No paper portfolio data found."],
            },
            {
                "title": "Executive Summary",
                "lines": ["- Paper P&L: n/a"],
            },
        ],
    }


def test_build_report_metadata_payload_includes_talib_runtime_fields() -> None:
    payload = build_report_metadata_payload(
        data_provider="tradingview",
        market_session={"status": "CLOSED"},
        paper_portfolio_payload={"available": False},
        paper_performance_payload={"available": False},
        storage_on_server=False,
        talib_runtime={
            "talib_available": False,
            "talib_mode": "fallback",
            "talib_reason": "talib package not installed",
        },
        tradingview_technical_available=True,
    )

    assert payload["talib_available"] is False
    assert payload["talib_mode"] == "fallback"
    assert payload["talib_reason"] == "talib package not installed"
    assert payload["tradingview_technical_available"] is True


def test_build_report_metadata_payload_includes_cloud_flags() -> None:
    payload = build_report_metadata_payload(
        data_provider="tradingview",
        market_session={"status": "CLOSED"},
        paper_portfolio_payload={"available": False},
        paper_performance_payload={"available": False},
        storage_on_server=False,
    )

    assert payload["data_provider"] == "tradingview"
    assert payload["data_provider_label"] == "TradingView Screener"
    assert payload["market_status"] == "CLOSED"
    assert payload["paper_portfolio_present"] is False
    assert payload["paper_portfolio_storage_on_server"] is False


def test_extract_report_metadata_falls_back_to_summary_section() -> None:
    payload = _cloud_payload_without_portfolio()
    payload.pop("report_metadata")

    metadata = extract_report_metadata(payload)

    assert metadata["data_provider_label"] == "TradingView"
    assert metadata["market_status"] == "CLOSED"
    assert metadata["paper_portfolio_present"] is False


def test_resolve_portfolio_data_status_marks_cloud_state_missing() -> None:
    status = resolve_portfolio_data_status(_cloud_payload_without_portfolio())

    assert status["has_portfolio_json"] is False
    assert status["has_performance_json"] is False
    assert status["cloud_state_missing"] is True
    assert status["executive_pnl"] is None


def test_parse_pnl_lines_from_sections() -> None:
    payload = _cloud_payload_without_portfolio()
    payload["sections"].append(
        {
            "title": "Paper Trading Performance",
            "lines": ["- Total P&L: +1,000.00 (+1.00%)"],
        }
    )

    lines = parse_pnl_lines_from_sections(payload)

    assert any("Total P&L" in line for line in lines)
    assert any("No paper portfolio data found" in line for line in lines)


def test_format_report_metadata_block_is_safe_and_informative() -> None:
    text = "\n".join(format_report_metadata_block(_cloud_payload_without_portfolio()))

    assert "TradingView" in text
    assert "CLOSED" in text
    assert "غير محفوظة" in text
    assert "TA-Lib: FALLBACK" in text
    assert "TradingView technical: ACTIVE" in text
    assert "1234567890:AA" not in text
    assert CLOUD_PORTFOLIO_STATE_MESSAGE not in text


def test_resolve_daily_overview_paper_pnl_prefers_executive_when_valid() -> None:
    payload = {
        "executive_summary": {"paper_pnl": "+1,500.00 (+1.50%) | Open positions: 0"},
        "paper_portfolio": {"available": True, "unrealized_pnl": 0.0},
    }
    assert resolve_daily_overview_paper_pnl(payload) == (
        "+1,500.00 (+1.50%) | Open positions: 0"
    )


def test_resolve_daily_overview_paper_pnl_empty_portfolio() -> None:
    payload = {
        "executive_summary": {"paper_pnl": "n/a"},
        "paper_portfolio": {
            "available": True,
            "open_positions_count": 0,
            "cash": 100000.0,
            "total_equity": 100000.0,
        },
        "paper_trading_performance": {
            "available": True,
            "total_pnl": 0.0,
            "total_return_pct": 0.0,
            "open_positions_count": 0,
        },
    }
    assert resolve_daily_overview_paper_pnl(payload) == (
        "0.00 (+0.00%) | Open positions: 0"
    )


def test_resolve_daily_overview_paper_pnl_missing_portfolio() -> None:
    payload = {
        "executive_summary": {"paper_pnl": "n/a"},
        "paper_portfolio": {"available": False},
        "paper_trading_performance": {"available": False},
    }
    assert resolve_daily_overview_paper_pnl(payload) == "n/a"
