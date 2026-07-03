"""Tests for scanner universe selection in live EGX scanning."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from config.watchlist import DEFAULT_WATCHLIST
from core.data_import import LIVE_SNAPSHOT_COLUMNS
from core.live_scanner_adapter import build_live_market_snapshot
from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from core.scanner_universe import (
    DEFAULT_SCANNER_UNIVERSE,
    SCANNER_UNIVERSE_FULL_MARKET,
    SCANNER_UNIVERSE_WATCHLIST,
)
from core.daily_report import DailyReportBuilder
from core.market_hours import sample_open_market_datetime
from core.market_data import MarketSnapshot
from core.market_mood import MarketMood, MarketMoodResult
from core.scanner import ScannerReport
from core.strategy import StrategyReport
from main import LiveScanPipelineResult, parse_args, print_live_scan_header, run_live_scan_pipeline


def _live_row(symbol: str, close: float = 100.0, previous_close: float = 99.0) -> LiveSymbolSnapshot:
    return LiveSymbolSnapshot(
        symbol=symbol,
        date=date(2026, 7, 1),
        previous_close=previous_close,
        open=previous_close,
        high=max(close, previous_close) + 0.5,
        low=min(close, previous_close) - 0.5,
        close=close,
        volume=1000.0,
        change_percent=((close - previous_close) / previous_close) * 100,
        volume_ratio=1.0,
        broke_previous_high=close > previous_close,
    )


def _live_snapshot(symbols: list[str]) -> LiveMarketSnapshot:
    return LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={symbol: _live_row(symbol) for symbol in symbols},
    )


def test_default_scanner_universe_is_watchlist() -> None:
    args = parse_args([])
    assert args.scanner_universe == DEFAULT_SCANNER_UNIVERSE
    assert args.scanner_universe == SCANNER_UNIVERSE_WATCHLIST


def test_parse_args_scanner_universe_full_market() -> None:
    args = parse_args(["--scanner-universe", "full-market"])
    assert args.scanner_universe == SCANNER_UNIVERSE_FULL_MARKET


def test_parse_args_rejects_invalid_scanner_universe() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--scanner-universe", "all-stocks"])


def test_watchlist_universe_preserves_missing_watchlist_warnings() -> None:
    live_snapshot = _live_snapshot(["COMI", "HRHO"])
    watchlist = ["COMI", "HRHO", "EFIH"]

    market_snapshot, _, warnings, _ = build_live_market_snapshot(
        live_snapshot,
        watchlist=watchlist,
        index_symbols=[],
        scanner_universe=SCANNER_UNIVERSE_WATCHLIST,
    )

    assert len(market_snapshot.symbols) == 2
    assert any("Watchlist symbol EFIH missing from live snapshot" in warning for warning in warnings)


def test_full_market_universe_scans_all_snapshot_symbols() -> None:
    symbols = ["COMI", "HRHO", "FWRY", "ZZZ"]
    live_snapshot = _live_snapshot(symbols)
    watchlist = ["COMI"]

    market_snapshot, _, warnings, _ = build_live_market_snapshot(
        live_snapshot,
        watchlist=watchlist,
        index_symbols=[],
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
    )

    assert len(market_snapshot.symbols) == len(symbols)
    assert {snap.symbol for snap in market_snapshot.symbols} == set(symbols)
    assert not any("Watchlist symbol" in warning for warning in warnings)


def test_full_market_universe_suppresses_missing_watchlist_symbol_warnings() -> None:
    live_snapshot = _live_snapshot(["COMI"])
    watchlist = DEFAULT_WATCHLIST

    _, _, warnings, _ = build_live_market_snapshot(
        live_snapshot,
        watchlist=watchlist,
        index_symbols=[],
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
    )

    assert not any("missing from live snapshot" in warning for warning in warnings)


def test_daily_report_prints_scanner_universe(tmp_path: Path) -> None:
    live_snapshot = _live_snapshot(["COMI", "HRHO"])
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, reasons=[], blockers=[])
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

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=[],
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
        now=sample_open_market_datetime(),
    )

    summary = next(
        section for section in report.sections if section.title == "Summary"
    )
    assert report.sections[0].title == "Executive Summary"
    assert "- Scanner Universe: full-market" in summary.lines


def test_run_live_scan_pipeline_defaults_to_watchlist_universe(tmp_path: Path) -> None:
    csv_path = tmp_path / "egx_live_snapshot.csv"
    pd.DataFrame(
        [
            {
                "date": "2026-07-01",
                "symbol": "COMI",
                "previous_close": 99.0,
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 1000,
            }
        ],
        columns=LIVE_SNAPSHOT_COLUMNS,
    ).to_csv(csv_path, index=False)

    pipeline = run_live_scan_pipeline(csv_path)
    assert pipeline is not None
    assert pipeline.scanner_universe == SCANNER_UNIVERSE_WATCHLIST
    assert len(pipeline.market_snapshot.symbols) == 1
    assert pipeline.market_snapshot.symbols[0].symbol == "COMI"
    assert any(
        "Watchlist symbol EFIH missing from live snapshot" in warning
        for warning in pipeline.warnings
    )


def test_run_live_scan_pipeline_full_market_scans_all_symbols(tmp_path: Path) -> None:
    csv_path = tmp_path / "egx_live_snapshot.csv"
    symbols = ["COMI", "HRHO", "FWRY", "TMGH"]
    pd.DataFrame(
        [
            {
                "date": "2026-07-01",
                "symbol": symbol,
                "previous_close": 99.0,
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 1000,
            }
            for symbol in symbols
        ],
        columns=LIVE_SNAPSHOT_COLUMNS,
    ).to_csv(csv_path, index=False)

    pipeline = run_live_scan_pipeline(
        csv_path,
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
    )
    assert pipeline is not None
    assert pipeline.scanner_universe == SCANNER_UNIVERSE_FULL_MARKET
    assert len(pipeline.market_snapshot.symbols) == len(symbols)


def test_full_market_sma5_warnings_are_summarized() -> None:
    from core.live_scanner_adapter import SMA5_HISTORY_WARNING
    from core.warning_formatting import SMA5_GENERAL_SUMMARY, summarize_live_scan_warnings

    raw_warnings = [f"SYM{i}: {SMA5_HISTORY_WARNING}" for i in range(20)]

    summarized = summarize_live_scan_warnings(raw_warnings)

    assert summarized == [SMA5_GENERAL_SUMMARY.format(count=20)]


def test_print_live_scan_header_shows_scanner_universe(capsys) -> None:
    live_snapshot = _live_snapshot(["COMI"])
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, reasons=[], blockers=[])
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
    pipeline = LiveScanPipelineResult(
        live_snapshot=live_snapshot,
        market_snapshot=MarketSnapshot(
            symbols=[],
            index_snapshots=[],
        ),
        mood_result=mood,
        scanner_report=scanner_report,
        strategy_report=strategy_report,
        warnings=[],
        snapshot_path=Path("data/live/egx_live_snapshot.csv"),
        lookback_days=5,
        min_history_days=3,
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
    )

    print_live_scan_header(pipeline)
    output = capsys.readouterr().out
    assert "Scanner universe: full-market" in output
