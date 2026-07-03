"""Tests for closed-market daily digest metadata and formatting."""

from __future__ import annotations

from datetime import date

from core.closed_market_digest import (
    build_closed_market_digest,
    closed_market_digest_enabled,
    format_closed_market_digest_arabic_block,
    format_closed_market_digest_report_lines,
    resolve_closed_market_reason,
)
from core.market_hours import (
    detect_egx_market_session,
    sample_closed_market_datetime,
    sample_open_market_datetime,
    sample_weekend_market_datetime,
)


def test_closed_market_digest_enabled_on_weekend() -> None:
    session = detect_egx_market_session(now=sample_weekend_market_datetime())
    assert closed_market_digest_enabled(session) is True
    assert resolve_closed_market_reason(
        session,
        as_of_date=date(2026, 7, 10),
    ) == "weekend"


def test_closed_market_digest_enabled_after_hours() -> None:
    session = detect_egx_market_session(now=sample_closed_market_datetime())
    digest = build_closed_market_digest(
        session=session,
        price_data_date=date(2026, 7, 7),
        data_provider="tradingview",
        as_of_date=date(2026, 7, 7),
    )
    assert digest["enabled"] is True
    assert digest["reason"] == "after_hours"
    assert digest["digest_type"] == "closed_market_daily_digest"
    assert digest["paper_entries_allowed"] is False
    assert digest["is_price_data_stale"] is True


def test_closed_market_digest_disabled_during_open_session() -> None:
    session = detect_egx_market_session(now=sample_open_market_datetime())
    digest = build_closed_market_digest(
        session=session,
        price_data_date=date(2026, 7, 7),
        data_provider="tradingview",
    )
    assert digest["enabled"] is False


def test_format_closed_market_digest_report_lines() -> None:
    digest = {
        "enabled": True,
        "reason": "weekend",
        "price_data_date": "2026-07-10",
        "price_data_source": "TradingView Screener",
    }
    lines = format_closed_market_digest_report_lines(digest)
    assert lines[0].startswith("- EGX is closed today:")
    assert "2026-07-10" in "\n".join(lines)
    assert "Paper entries are disabled" in "\n".join(lines)


def test_format_closed_market_digest_arabic_block() -> None:
    digest = {
        "enabled": True,
        "price_data_date": "2026-07-10",
        "price_data_source": "TradingView Screener",
    }
    lines = format_closed_market_digest_arabic_block(digest)
    assert "🔒 السوق مقفول النهارده" in lines
    assert "2026-07-10" in "\n".join(lines)
    assert "مفيش دخول ورقي جديد" in "\n".join(lines)


def test_format_closed_market_digest_arabic_block_empty_when_disabled() -> None:
    assert format_closed_market_digest_arabic_block({"enabled": False}) == []
