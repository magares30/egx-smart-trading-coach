"""Tests for compact confirmation summary from existing report layers."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from core.confirmation_summary import (
    CONFIRMATION_SUMMARY_NOTE,
    ConfirmationLabel,
    build_confirmation_summary,
    build_executive_confirmation_line,
    build_signal_confirmation_summary,
    classify_confirmation_label,
)
from core.daily_report import DailyReportBuilder, format_daily_report_text
from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from core.market_mood import MarketMood, MarketMoodResult
from core.multi_timeframe import EntryTimingStatus, MultiTimeframeConfig
from core.scanner import ScannerDecision, ScannerReport, ScannerResult
from core.strategy import StrategyDecision, StrategyReport, StrategyResult
from core.talib_technical import TalibTechnicalConfig
from core.technical_confirmation import (
    TechnicalConfirmationResult,
    TechnicalStatus,
)


def test_confirmation_text_includes_talib_fallback_phrase() -> None:
    from core.confirmation_summary import build_confirmation_text, ConfirmationLabel
    from core.technical_confirmation import TechnicalStatus
    from core.multi_timeframe import EntryTimingStatus
    from core.talib_technical import TALIB_STATUS_FALLBACK

    text = build_confirmation_text(
        ConfirmationLabel.GOOD_CONFIRMATION,
        tv_status=TechnicalStatus.STRONG,
        timing_status=EntryTimingStatus.READY,
        talib_status=TALIB_STATUS_FALLBACK,
        talib_enabled=True,
    )
    assert "TA-Lib fallback" in text

    label = classify_confirmation_label(
        tv_status=TechnicalStatus.STRONG,
        timing_status=EntryTimingStatus.READY,
        talib_status="INSUFFICIENT_HISTORY",
    )
    summary = build_signal_confirmation_summary(
        "ELKA",
        tv_status=TechnicalStatus.STRONG,
        timing_status=EntryTimingStatus.READY,
        talib_status="INSUFFICIENT_HISTORY",
    )

    assert label == ConfirmationLabel.GOOD_CONFIRMATION
    assert summary.label == ConfirmationLabel.GOOD_CONFIRMATION
    assert summary.waiting_for_history is True
    assert "TA-Lib waiting history" in summary.confirmation_text
    assert "WEAK" not in summary.confirmation_text


def test_tv_strong_timing_watch_is_mixed() -> None:
    label = classify_confirmation_label(
        tv_status=TechnicalStatus.OK,
        timing_status=EntryTimingStatus.WATCH,
        talib_status="INSUFFICIENT_HISTORY",
    )
    summary = build_signal_confirmation_summary(
        "ELKA",
        tv_status=TechnicalStatus.OK,
        timing_status=EntryTimingStatus.WATCH,
        talib_status="INSUFFICIENT_HISTORY",
    )

    assert label == ConfirmationLabel.MIXED_CONFIRMATION
    assert "Timing watch" in summary.confirmation_text


def test_tv_strong_timing_ready_talib_aligned_is_strong() -> None:
    summary = build_signal_confirmation_summary(
        "ELKA",
        tv_status=TechnicalStatus.STRONG,
        timing_status=EntryTimingStatus.READY,
        talib_status="STRONG",
    )

    assert summary.label == ConfirmationLabel.STRONG_CONFIRMATION
    assert "TA-Lib aligned" in summary.confirmation_text


def test_tv_weak_is_weak_confirmation() -> None:
    summary = build_signal_confirmation_summary(
        "ELKA",
        tv_status=TechnicalStatus.WEAK,
        timing_status=EntryTimingStatus.READY,
        talib_status="STRONG",
    )

    assert summary.label == ConfirmationLabel.WEAK_CONFIRMATION


def test_confirmation_summary_json_buckets() -> None:
    summaries = [
        build_signal_confirmation_summary(
            "ELKA",
            tv_status=TechnicalStatus.STRONG,
            timing_status=EntryTimingStatus.READY,
            talib_status="INSUFFICIENT_HISTORY",
        ),
        build_signal_confirmation_summary(
            "LCSW",
            tv_status=TechnicalStatus.OK,
            timing_status=EntryTimingStatus.WATCH,
            talib_status="INSUFFICIENT_HISTORY",
        ),
    ]
    payload = build_confirmation_summary(summaries).to_dict()

    assert payload["good"] == ["ELKA"]
    assert payload["mixed"] == ["LCSW"]
    assert payload["waiting_for_history"] == ["ELKA", "LCSW"]
    assert payload["note"] == CONFIRMATION_SUMMARY_NOTE
    assert payload["signals"][0]["confirmation_label"] == "GOOD_CONFIRMATION"
    assert payload["signals"][0]["tv_status"] == "STRONG"
    assert payload["signals"][0]["timing_status"] == "READY"
    assert payload["signals"][0]["talib_status"] == "INSUFFICIENT_HISTORY"


def test_executive_confirmation_line_with_waiting_history() -> None:
    summaries = [
        build_signal_confirmation_summary(
            "ELKA",
            tv_status=TechnicalStatus.STRONG,
            timing_status=EntryTimingStatus.READY,
            talib_status="INSUFFICIENT_HISTORY",
        ),
        build_signal_confirmation_summary(
            "LCSW",
            tv_status=TechnicalStatus.OK,
            timing_status=EntryTimingStatus.READY,
            talib_status="INSUFFICIENT_HISTORY",
        ),
        build_signal_confirmation_summary(
            "TANM",
            tv_status=TechnicalStatus.STRONG,
            timing_status=EntryTimingStatus.READY,
            talib_status="INSUFFICIENT_HISTORY",
        ),
    ]

    line = build_executive_confirmation_line(summaries)

    assert line == "3 good setups; TA-Lib still waiting for history"


def test_executive_confirmation_line_without_signals() -> None:
    assert (
        build_executive_confirmation_line([])
        == "no actionable confirmations today"
    )


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


def _aligned_timeframe_row(symbol: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        **_aligned_timeframe_fields("tf_1h"),
        **_aligned_timeframe_fields("tf_15m"),
    }


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


@patch("core.daily_report.evaluate_technical_confirmation")
def test_strategy_signals_txt_includes_confirmation_line(
    mock_evaluate_technical: object,
) -> None:
    mock_evaluate_technical.return_value = TechnicalConfirmationResult(
        technical_score=15,
        status=TechnicalStatus.STRONG,
        notes=[],
    )
    symbol = "COMI"
    timeframe_df = pd.DataFrame([_aligned_timeframe_row(symbol)])
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 2),
        symbols={symbol: _live_row(symbol)},
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    candidates = [_candidate(symbol)]
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=candidates,
        candidates=candidates,
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[_strategy_result(symbol)],
        buy_setups=[],
        watch=[_strategy_result(symbol)],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        multi_timeframe_config=MultiTimeframeConfig(),
        timeframe_snapshot_df=timeframe_df,
        talib_config=TalibTechnicalConfig(enabled=True, min_history_days=50),
        enable_performance_analytics=False,
    )
    strategy_section = next(
        section for section in report.sections if section.title == "Strategy Signals"
    )
    strategy_text = "\n".join(strategy_section.lines)

    assert "Confirmation: GOOD | TV strong | Timing ready | TA-Lib waiting history" in (
        strategy_text
    )
    assert report.confirmation_summary["good"] == [symbol]
    assert report.confirmation_summary["waiting_for_history"] == [symbol]


@patch("core.daily_report.evaluate_technical_confirmation")
def test_executive_summary_includes_confirmation_line(
    mock_evaluate_technical: object,
) -> None:
    mock_evaluate_technical.return_value = TechnicalConfirmationResult(
        technical_score=15,
        status=TechnicalStatus.STRONG,
        notes=[],
    )
    symbols = ["ELKA", "LCSW", "TANM"]
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 2),
        symbols={symbol: _live_row(symbol) for symbol in symbols},
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    candidates = [_candidate(symbol) for symbol in symbols]
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=candidates,
        candidates=candidates,
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[_strategy_result(symbol) for symbol in symbols],
        buy_setups=[],
        watch=[_strategy_result(symbol) for symbol in symbols],
        blocked=[],
    )
    timeframe_df = pd.DataFrame(
        [_aligned_timeframe_row(symbol) for symbol in symbols]
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        multi_timeframe_config=MultiTimeframeConfig(),
        timeframe_snapshot_df=timeframe_df,
        talib_config=TalibTechnicalConfig(enabled=True, min_history_days=50),
        enable_performance_analytics=False,
    )
    text = format_daily_report_text(report)

    assert (
        report.executive_summary["confirmation"]
        == "3 good setups; TA-Lib still waiting for history"
    )
    assert (
        "- Confirmation: 3 good setups; TA-Lib still waiting for history"
        in text
    )


def test_report_json_contains_confirmation_summary() -> None:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 2),
        symbols={"COMI": _live_row("COMI")},
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=[],
        candidates=[],
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
        talib_config=TalibTechnicalConfig(enabled=False),
        enable_performance_analytics=False,
    )

    assert set(report.confirmation_summary) >= {
        "strong",
        "good",
        "mixed",
        "weak",
        "waiting_for_history",
        "note",
        "signals",
    }
    assert (
        report.executive_summary["confirmation"]
        == "no actionable confirmations today"
    )
