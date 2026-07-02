"""Tests for live scan console warning output."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from core.live_scanner_adapter import SMA5_HISTORY_WARNING
from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from core.live_volume import NOT_ENOUGH_VOLUME_HISTORY_WARNING
from core.market_mood import MarketMood, MarketMoodResult
from core.market_data import MarketSnapshot
from core.scanner import ScannerReport
from core.strategy import StrategyReport
from core.warning_formatting import (
    SMA5_WATCHLIST_SUMMARY,
    VOLUME_HISTORY_SUMMARY,
    WATCHLIST_VOLUME_HISTORY_SUMMARY,
)
from core.scanner_universe import DEFAULT_SCANNER_UNIVERSE
from main import LiveScanPipelineResult, print_live_scan_header


def _live_row(symbol: str) -> LiveSymbolSnapshot:
    return LiveSymbolSnapshot(
        symbol=symbol,
        date=date(2026, 7, 1),
        previous_close=80.0,
        open=80.0,
        high=82.0,
        low=79.0,
        close=81.0,
        volume=1000.0,
        change_percent=1.25,
        volume_ratio=1.0,
        broke_previous_high=True,
    )


def _volume_warning(symbol: str) -> str:
    return f"{symbol}: {NOT_ENOUGH_VOLUME_HISTORY_WARNING}"


def _sma5_warning(symbol: str) -> str:
    return f"{symbol}: {SMA5_HISTORY_WARNING}"


def _pipeline(warnings: list[str]) -> LiveScanPipelineResult:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={"COMI": _live_row("COMI")},
    )
    mood = MarketMoodResult(
        mood=MarketMood.NEUTRAL,
        score=50,
        reasons=[],
        blockers=[],
    )
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=[],
        candidates=[],
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="Trend Join Long",
        results=[],
        buy_setups=[],
        watch=[],
        blocked=[],
    )
    return LiveScanPipelineResult(
        live_snapshot=live_snapshot,
        market_snapshot=MarketSnapshot(symbols=[], index_snapshots=[]),
        mood_result=mood,
        scanner_report=scanner_report,
        strategy_report=strategy_report,
        warnings=warnings,
        snapshot_path=Path("data/live/egx_live_snapshot.csv"),
        lookback_days=5,
        min_history_days=3,
        scanner_universe=DEFAULT_SCANNER_UNIVERSE,
    )


def test_print_live_scan_header_uses_summarized_warnings(capsys) -> None:
    watchlist = ["COMI", "HRHO", "FWRY", "TMGH"]
    raw_warnings = [
        "Low valid symbol count after dedupe: 12",
        *[_volume_warning(symbol) for symbol in watchlist],
        *[_volume_warning(f"SYM{i}") for i in range(10)],
        *[_sma5_warning(symbol) for symbol in watchlist[:3]],
    ]

    print_live_scan_header(_pipeline(raw_warnings))
    output = capsys.readouterr().out

    assert "=== EGX Live Snapshot Scanner ===" in output
    assert VOLUME_HISTORY_SUMMARY.format(count=14, min_history_days=3) in output
    assert WATCHLIST_VOLUME_HISTORY_SUMMARY.format(symbols="COMI, HRHO, FWRY, TMGH") in output
    assert SMA5_WATCHLIST_SUMMARY.format(count=3) in output
    assert "Low valid symbol count after dedupe: 12" in output
    assert "COMI: Not enough volume history" not in output
    assert "HRHO: Not enough live history to compute SMA5" not in output


def test_print_live_scan_header_keeps_critical_warnings_visible(capsys) -> None:
    critical_warnings = [
        "Partial EGX snapshot: only 80 rows collected",
        "Multi-sector collection unavailable; using visible table fallback",
        "Symbol mapping: 5 mapped, 2 unresolved, 1 duplicate",
        "Watchlist symbol ABUK missing from live snapshot",
    ]

    print_live_scan_header(_pipeline(critical_warnings))
    output = capsys.readouterr().out

    for warning in critical_warnings:
        assert warning in output


def test_print_live_scan_header_does_not_print_per_symbol_volume_spam(capsys) -> None:
    raw_warnings = [_volume_warning(f"SYM{i}") for i in range(50)]

    print_live_scan_header(_pipeline(raw_warnings))
    output = capsys.readouterr().out

    assert output.count("Not enough volume history") == 1
    assert "SYM0: Not enough volume history" not in output
    assert "SYM49: Not enough volume history" not in output
