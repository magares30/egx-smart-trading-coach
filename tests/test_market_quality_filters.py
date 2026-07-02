"""Tests for full-market quality filters before scanner scoring."""

from __future__ import annotations

import pandas as pd
from datetime import date

from core.market_quality_filters import (
    DEFAULT_MIN_PRICE,
    DEFAULT_MIN_VOLUME,
    MarketQualityFilters,
    apply_market_quality_filters,
    build_market_quality_filters_from_cli,
    build_market_quality_filter_summary_lines,
    quality_filtered_symbol_snapshots,
)
from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from main import parse_args


def test_apply_market_quality_filters_removes_low_price_and_volume() -> None:
    frame = pd.DataFrame(
        [
            {"symbol": "GOOD", "close": 10.0, "volume": 100_000},
            {"symbol": "LOWP", "close": 0.50, "volume": 100_000},
            {"symbol": "LOWV", "close": 10.0, "volume": 1_000},
            {"symbol": "ZERO", "close": 5.0, "volume": 0},
        ]
    )
    filters = MarketQualityFilters()

    result = apply_market_quality_filters(frame, filters)

    assert result.original_count == 4
    assert result.filtered_count == 1
    assert list(result.filtered_df["symbol"]) == ["GOOD"]
    assert result.removed_low_price == 1
    assert result.removed_low_volume == 1
    assert result.removed_zero_volume == 1


def test_include_illiquid_skips_volume_filters() -> None:
    frame = pd.DataFrame(
        [
            {"symbol": "ILLIQ", "close": 5.0, "volume": 0},
            {"symbol": "LOWP", "close": 0.50, "volume": 0},
        ]
    )
    filters = MarketQualityFilters(include_illiquid=True)

    result = apply_market_quality_filters(frame, filters)

    assert result.filtered_count == 1
    assert list(result.filtered_df["symbol"]) == ["ILLIQ"]
    assert result.removed_low_price == 1
    assert result.removed_low_volume == 0
    assert result.removed_zero_volume == 0


def test_min_market_cap_ignored_when_column_missing() -> None:
    frame = pd.DataFrame(
        [
            {"symbol": "COMI", "close": 80.0, "volume": 100_000},
        ]
    )
    filters = MarketQualityFilters(min_market_cap=1_000_000_000)

    result = apply_market_quality_filters(frame, filters)

    assert result.filtered_count == 1
    assert result.removed_low_market_cap == 0


def test_min_market_cap_filters_when_column_present() -> None:
    frame = pd.DataFrame(
        [
            {"symbol": "BIG", "close": 80.0, "volume": 100_000, "market_cap": 5_000_000_000},
            {"symbol": "SMALL", "close": 2.0, "volume": 100_000, "market_cap": 100_000},
        ]
    )
    filters = MarketQualityFilters(min_market_cap=1_000_000_000)

    result = apply_market_quality_filters(frame, filters)

    assert result.filtered_count == 1
    assert list(result.filtered_df["symbol"]) == ["BIG"]
    assert result.removed_low_market_cap == 1


def test_build_market_quality_filter_summary_lines() -> None:
    frame = pd.DataFrame(
        [
            {"symbol": "GOOD", "close": 10.0, "volume": 100_000},
            {"symbol": "LOWP", "close": 0.50, "volume": 100_000},
        ]
    )
    result = apply_market_quality_filters(frame, MarketQualityFilters())
    lines = build_market_quality_filter_summary_lines(result)

    assert "- Min price: 1.00" in lines
    assert "- Min volume: 50,000" in lines
    assert "- Symbols before quality filter: 2" in lines
    assert "- Symbols after quality filter: 1" in lines


def test_build_market_quality_filters_from_cli_defaults() -> None:
    filters = build_market_quality_filters_from_cli()

    assert filters.min_price == DEFAULT_MIN_PRICE
    assert filters.min_volume == DEFAULT_MIN_VOLUME
    assert filters.min_market_cap is None
    assert filters.exclude_zero_volume is True
    assert filters.include_illiquid is False


def test_quality_filtered_symbol_snapshots_limits_report_universe() -> None:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 2),
        symbols={
            "GOOD": LiveSymbolSnapshot(
                symbol="GOOD",
                date=date(2026, 7, 2),
                previous_close=9.0,
                open=9.0,
                high=10.0,
                low=9.0,
                close=10.0,
                volume=500_000.0,
                change_percent=11.11,
                volume_ratio=2.0,
                broke_previous_high=True,
            ),
            "LOWP": LiveSymbolSnapshot(
                symbol="LOWP",
                date=date(2026, 7, 2),
                previous_close=2.0,
                open=2.0,
                high=2.1,
                low=1.9,
                close=2.0,
                volume=500_000.0,
                change_percent=0.0,
                volume_ratio=1.0,
                broke_previous_high=False,
            ),
        },
    )
    frame = pd.DataFrame(
        [
            {"symbol": "GOOD", "close": 10.0, "volume": 500_000},
            {"symbol": "LOWP", "close": 2.0, "volume": 500_000},
        ]
    )
    result = apply_market_quality_filters(
        frame,
        MarketQualityFilters(min_price=3.0),
    )

    filtered = quality_filtered_symbol_snapshots(live_snapshot, result)

    assert [snap.symbol for snap in filtered] == ["GOOD"]


def test_parse_args_market_quality_flags() -> None:
    args = parse_args(
        [
            "--min-price",
            "2.5",
            "--min-volume",
            "100000",
            "--min-market-cap",
            "500000000",
            "--no-exclude-zero-volume",
            "--include-illiquid",
        ]
    )
    filters = build_market_quality_filters_from_cli(
        min_price=args.min_price,
        min_volume=args.min_volume,
        min_market_cap=args.min_market_cap,
        exclude_zero_volume=args.exclude_zero_volume,
        include_illiquid=args.include_illiquid,
    )

    assert filters.min_price == 2.5
    assert filters.min_volume == 100_000
    assert filters.min_market_cap == 500_000_000
    assert filters.exclude_zero_volume is False
    assert filters.include_illiquid is True
