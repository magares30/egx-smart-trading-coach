"""Tests for market breadth mood calculation and integration."""

from __future__ import annotations

from datetime import date

import pandas as pd

from core.daily_report import DailyReportBuilder, format_daily_report_text
from core.live_scanner_adapter import MISSING_INDEX_MOOD_WARNING, build_live_market_snapshot
from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from core.market_breadth_mood import (
    BREADTH_MOOD_INFO_WARNING,
    MarketBreadthMood,
    MarketBreadthMoodConfig,
    NOT_ENOUGH_BREADTH_ROWS_WARNING,
    calculate_market_breadth_mood,
    format_market_breadth_mood_report_lines,
)
from core.market_data_providers import DATA_PROVIDER_TRADINGVIEW
from core.market_mood import MarketMood
from core.scanner import ScannerReport
from core.strategy import StrategyReport


def _live_row(
    symbol: str,
    close: float,
    previous_close: float,
    *,
    volume_ratio: float = 1.0,
) -> LiveSymbolSnapshot:
    return LiveSymbolSnapshot(
        symbol=symbol,
        date=date(2026, 7, 2),
        previous_close=previous_close,
        open=previous_close,
        high=max(close, previous_close) + 0.5,
        low=min(close, previous_close) - 0.5,
        close=close,
        volume=500_000.0,
        change_percent=((close - previous_close) / previous_close) * 100,
        volume_ratio=volume_ratio,
        broke_previous_high=close > previous_close,
    )


def _breadth_frame(changes: list[float], volume_ratio: float = 1.6) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": f"SYM{index}",
                "change_percent": change_percent,
                "volume_ratio": volume_ratio,
            }
            for index, change_percent in enumerate(changes)
        ]
    )


def test_bullish_breadth_scores_above_neutral() -> None:
    changes = [1.2] * 60 + [-0.2] * 40
    result = calculate_market_breadth_mood(_breadth_frame(changes))

    assert result.score > 50
    assert result.mood in {MarketBreadthMood.BULLISH, MarketBreadthMood.POSITIVE}
    assert result.advancers_count == 60
    assert result.symbols_count == 100


def test_mostly_declining_market_is_weak_or_bearish() -> None:
    changes = [-1.5] * 70 + [0.2] * 30
    result = calculate_market_breadth_mood(_breadth_frame(changes, volume_ratio=0.8))

    assert result.score < 45
    assert result.mood in {MarketBreadthMood.WEAK, MarketBreadthMood.BEARISH}
    assert result.decliners_count == 70


def test_not_enough_rows_returns_neutral_safely() -> None:
    frame = pd.DataFrame(
        [
            {"symbol": "COMI", "change_percent": 1.0, "volume_ratio": 1.2},
            {"symbol": "HRHO", "change_percent": -0.5, "volume_ratio": 0.9},
        ]
    )

    result = calculate_market_breadth_mood(
        frame,
        config=MarketBreadthMoodConfig(min_rows=5),
    )

    assert result.mood == MarketBreadthMood.NEUTRAL
    assert result.score == 50
    assert result.warning == NOT_ENOUGH_BREADTH_ROWS_WARNING


def test_missing_indexes_use_breadth_for_tradingview_provider() -> None:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 2),
        symbols={
            f"SYM{i}": _live_row(
                f"SYM{i}",
                close=100.0 + (1.0 if i < 58 else -1.0),
                previous_close=100.0,
                volume_ratio=1.6,
            )
            for i in range(96)
        },
    )

    _, mood_result, warnings, breadth_result = build_live_market_snapshot(
        live_snapshot,
        data_provider=DATA_PROVIDER_TRADINGVIEW,
    )

    assert breadth_result is not None
    assert breadth_result.score > 50
    assert MISSING_INDEX_MOOD_WARNING not in warnings
    assert BREADTH_MOOD_INFO_WARNING in warnings
    assert mood_result.score == breadth_result.score


def test_index_rows_keep_existing_index_mood_behavior() -> None:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 2),
        symbols={
            "COMI": _live_row("COMI", close=82.0, previous_close=80.0),
            "EGX30": _live_row("EGX30", close=2050.0, previous_close=2000.0),
            "EGX70": _live_row("EGX70", close=3100.0, previous_close=3000.0),
        },
    )

    _, mood_result, warnings, breadth_result = build_live_market_snapshot(
        live_snapshot,
        data_provider=DATA_PROVIDER_TRADINGVIEW,
    )

    assert breadth_result is None
    assert mood_result.mood == MarketMood.STRONG
    assert BREADTH_MOOD_INFO_WARNING not in warnings


def test_report_includes_breadth_details() -> None:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 2),
        symbols={
            f"SYM{i}": _live_row(
                f"SYM{i}",
                close=101.0,
                previous_close=100.0,
                volume_ratio=1.6,
            )
            for i in range(10)
        },
    )
    breadth_result = calculate_market_breadth_mood(
        _breadth_frame([1.0] * 10, volume_ratio=1.6),
        config=MarketBreadthMoodConfig(min_rows=5),
    )
    mood_result = breadth_result.to_market_mood_result()
    scanner_report = ScannerReport(
        market_mood=mood_result.mood.value,
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
        mood_result,
        scanner_report,
        strategy_report,
        warnings=[BREADTH_MOOD_INFO_WARNING],
        market_breadth_mood_result=breadth_result,
    )
    text = format_daily_report_text(report)
    mood_section = next(section for section in report.sections if section.title == "Market Mood")
    mood_text = "\n".join(mood_section.lines)

    assert "Market Mood:" in text
    assert "- Breadth: Advancers" in mood_text
    assert "- Avg change:" in mood_text
    assert "- Median change:" in mood_text
    assert "- Source: TradingView stock breadth" in mood_text
    assert report.market_breadth_mood["advancers_count"] == breadth_result.advancers_count
    assert format_market_breadth_mood_report_lines(breadth_result)
