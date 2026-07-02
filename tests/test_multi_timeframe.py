"""Tests for multi-timeframe entry timing checks."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from core.daily_report import DailyReportBuilder
from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from core.market_mood import MarketMood, MarketMoodResult
from core.multi_timeframe import (
    EntryTimingStatus,
    MultiTimeframeConfig,
    evaluate_entry_timing,
    format_entry_timing_line,
)
from core.scanner import ScannerDecision, ScannerReport, ScannerResult
from core.strategy import StrategyDecision, StrategyReport, StrategyResult


def _aligned_timeframe_fields(prefix: str) -> dict[str, float]:
    return {
        f"{prefix}_close": 100.0,
        f"{prefix}_recommend_all": 0.5,
        f"{prefix}_rsi": 58.0,
        f"{prefix}_macd": 1.5,
        f"{prefix}_macd_signal": 1.0,
        f"{prefix}_ema20": 95.0,
        f"{prefix}_adx": 24.0,
    }


def _aligned_row() -> dict[str, object]:
    return {
        "symbol": "COMI",
        **_aligned_timeframe_fields("tf_1h"),
        **_aligned_timeframe_fields("tf_15m"),
    }


def test_missing_timeframe_data_returns_unknown() -> None:
    result = evaluate_entry_timing(None, tf_1h_row=None, tf_15m_row=None)

    assert result.status == EntryTimingStatus.UNKNOWN
    assert "multi-timeframe data unavailable" in result.notes[0]
    assert "UNKNOWN" in format_entry_timing_line(result)


def test_aligned_1h_and_15m_gives_ready() -> None:
    row = _aligned_row()
    result = evaluate_entry_timing(None, tf_1h_row=row, tf_15m_row=row)

    assert result.status == EntryTimingStatus.READY
    assert result.entry_timing_score >= 12
    assert result.tf_1h_label == "OK"
    assert result.tf_15m_label == "OK"


def test_overbought_rsi_gives_caution_or_wait_note() -> None:
    row = {
        "symbol": "HOT",
        **_aligned_timeframe_fields("tf_1h"),
    }
    row["tf_1h_rsi"] = 80.0

    result = evaluate_entry_timing(None, tf_1h_row=row, tf_15m_row=row)

    assert result.status in {EntryTimingStatus.WAIT, EntryTimingStatus.AVOID, EntryTimingStatus.WATCH}
    assert any("overbought" in note.lower() for note in result.notes)


def _live_row(symbol: str) -> LiveSymbolSnapshot:
    return LiveSymbolSnapshot(
        symbol=symbol,
        date=date(2026, 7, 2),
        previous_close=99.0,
        open=99.0,
        high=101.0,
        low=98.5,
        close=100.0,
        volume=1_000_000,
        change_percent=1.0,
        volume_ratio=2.0,
        broke_previous_high=True,
    )


def _candidate(symbol: str) -> ScannerResult:
    return ScannerResult(
        symbol=symbol,
        decision=ScannerDecision.CANDIDATE,
        score=90,
        latest_close=100.0,
        change_percent=1.0,
        volume_ratio=2.0,
        broke_previous_high=True,
        above_sma_5=True,
        reasons=["Positive price change"],
        blockers=[],
    )


def _strategy_result(symbol: str) -> StrategyResult:
    return StrategyResult(
        symbol=symbol,
        decision=StrategyDecision.WATCH,
        entry_price=28.45,
        stop_loss=27.64,
        take_profit=30.07,
        risk_reward=2.0,
        confidence_score=80,
        reasons=["Scanner marked symbol as candidate"],
        blockers=[],
    )


def test_report_includes_entry_timing_line() -> None:
    timeframe_df = pd.DataFrame([_aligned_row()])
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 2),
        symbols={"COMI": _live_row("COMI")},
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    candidates = [_candidate("COMI")]
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=candidates,
        candidates=candidates,
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[_strategy_result("COMI")],
        buy_setups=[],
        watch=[_strategy_result("COMI")],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        multi_timeframe_config=MultiTimeframeConfig(),
        timeframe_snapshot_df=timeframe_df,
        data_provider="tradingview",
    )
    top_section = next(
        section for section in report.sections if section.title == "Top Candidates"
    )
    top_text = "\n".join(top_section.lines)

    assert "Entry Timing:" in top_text
    assert report.candidate_entry_timing
    assert report.candidate_entry_timing[0]["status"] == EntryTimingStatus.READY.value


def test_strategy_signals_include_timing_status() -> None:
    timeframe_df = pd.DataFrame([_aligned_row()])
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 2),
        symbols={"COMI": _live_row("COMI")},
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    candidates = [_candidate("COMI")]
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=candidates,
        candidates=candidates,
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[_strategy_result("COMI")],
        buy_setups=[],
        watch=[_strategy_result("COMI")],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        multi_timeframe_config=MultiTimeframeConfig(),
        timeframe_snapshot_df=timeframe_df,
        data_provider="tradingview",
    )
    strategy_section = next(
        section for section in report.sections if section.title == "Strategy Signals"
    )
    strategy_text = "\n".join(strategy_section.lines)

    assert "Timing READY" in strategy_text


@patch("core.multi_timeframe.fetch_tradingview_timeframe_snapshot")
def test_disabling_multi_timeframe_omits_lines_and_does_not_fetch(
    mock_fetch: pytest.Mock,
) -> None:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 2),
        symbols={"COMI": _live_row("COMI")},
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    candidates = [_candidate("COMI")]
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=candidates,
        candidates=candidates,
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[],
        buy_setups=[],
        watch=[],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        multi_timeframe_config=MultiTimeframeConfig(enabled=False),
        data_provider="tradingview",
    )
    top_section = next(
        section for section in report.sections if section.title == "Top Candidates"
    )
    top_text = "\n".join(top_section.lines)

    assert "Entry Timing:" not in top_text
    assert report.candidate_entry_timing == []
    mock_fetch.assert_not_called()


def test_parse_args_supports_multi_timeframe_flags() -> None:
    from main import parse_args

    enabled_args = parse_args(["--enable-multi-timeframe"])
    disabled_args = parse_args(["--disable-multi-timeframe"])
    custom_args = parse_args(["--entry-timeframes", "1h"])

    assert enabled_args.enable_multi_timeframe is True
    assert disabled_args.enable_multi_timeframe is False
    assert custom_args.entry_timeframes == "1h"
