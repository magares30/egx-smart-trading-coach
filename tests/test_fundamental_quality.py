"""Tests for TradingView fundamental quality scoring."""

from __future__ import annotations

import pandas as pd

from core.fundamental_quality import (
    FundamentalQualityConfig,
    FundamentalStatus,
    evaluate_fundamental_quality,
    format_fundamental_quality_line,
    passes_fundamental_filters,
)


def test_missing_fields_returns_unknown_and_does_not_crash() -> None:
    result = evaluate_fundamental_quality({"symbol": "COMI"})

    assert result.status == FundamentalStatus.UNKNOWN
    assert result.fundamental_score == 0
    assert "fundamental fields unavailable" in result.notes[0]
    assert format_fundamental_quality_line(result).startswith("Fundamentals: UNKNOWN")


def test_sane_pe_pb_and_market_cap_gives_ok_or_strong() -> None:
    row = {
        "symbol": "COMI",
        "market_cap_basic": 12_500_000_000,
        "price_earnings_ttm": 9.4,
        "price_book_fq": 1.8,
        "dividends_yield_current": 3.2,
    }
    result = evaluate_fundamental_quality(row, FundamentalQualityConfig())

    assert result.status in {FundamentalStatus.OK, FundamentalStatus.STRONG}
    assert result.fundamental_score >= 5
    line = format_fundamental_quality_line(result)
    assert "Fundamentals:" in line
    assert "MCap 12.5B" in line
    assert "P/E 9.4" in line
    assert "P/B 1.8" in line
    assert "Div 3.2%" in line


def test_expensive_pe_gives_caution_or_weak_note() -> None:
    row = {
        "symbol": "EXPEN",
        "market_cap": 2_000_000_000,
        "pe_ratio": 40.0,
        "pb_ratio": 2.0,
    }
    result = evaluate_fundamental_quality(row, FundamentalQualityConfig())

    assert result.status in {FundamentalStatus.CAUTION, FundamentalStatus.WEAK}
    assert any("expensive" in note.lower() for note in result.notes)


def test_technical_and_fundamental_lines_are_separate() -> None:
    from core.daily_report import DailyReportBuilder
    from core.scanner import ScannerDecision, ScannerResult

    candidate = ScannerResult(
        symbol="COMI",
        decision=ScannerDecision.CANDIDATE,
        score=90,
        latest_close=100.0,
        change_percent=2.0,
        volume_ratio=2.1,
        broke_previous_high=True,
        above_sma_5=True,
        reasons=["test"],
        blockers=[],
    )
    builder = DailyReportBuilder()
    lines = builder._format_scanner_item(
        1,
        candidate,
        ranking_note="Rank factors: volume 100, rel vol HIGH 2.1x, change quality clean, market cap available",
        technical_note="Technical: STRONG (+12) | RSI 58",
        fundamental_note="Fundamentals: OK (+8) | MCap 12.5B | P/E 9.4 | P/B 1.8 | Div 3.2%",
        display_volume_ratio=2.1,
    )

    assert len(lines) >= 5
    assert lines[2].strip().startswith("Rank factors:")
    assert lines[3].strip().startswith("Technical:")
    assert lines[4].strip().startswith("Fundamentals:")
    assert "Technical:" not in lines[2]
    assert "Fundamentals:" not in lines[3]


def test_max_pe_filter_excludes_expensive_candidate() -> None:
    frame = pd.DataFrame(
        [
            {
                "symbol": "CHEAP",
                "pe_ratio": 12.0,
                "pb_ratio": 1.5,
                "market_cap": 2_000_000_000,
            },
            {
                "symbol": "RICH",
                "pe_ratio": 40.0,
                "pb_ratio": 1.5,
                "market_cap": 2_000_000_000,
            },
        ]
    )

    assert passes_fundamental_filters(frame.iloc[0], max_pe=25.0)
    assert not passes_fundamental_filters(frame.iloc[1], max_pe=25.0)


def test_require_fundamentals_excludes_unknown_candidate() -> None:
    assert passes_fundamental_filters(
        {"symbol": "KNOWN", "market_cap": 2_000_000_000, "pe_ratio": 10.0},
        require_fundamentals=True,
    )
    assert not passes_fundamental_filters(
        {"symbol": "UNKNOWN"},
        require_fundamentals=True,
    )
