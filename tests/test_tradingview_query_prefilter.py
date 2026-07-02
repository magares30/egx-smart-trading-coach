"""Tests for TradingView query-level pre-filters."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from core.daily_report import DailyReportBuilder, format_daily_report_text
from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from core.market_mood import MarketMood, MarketMoodResult
from core.market_quality_filters import MarketQualityFilters, apply_market_quality_filters
from core.scanner import ScannerDecision, ScannerReport, ScannerResult
from core.scanner_universe import SCANNER_UNIVERSE_FULL_MARKET
from core.strategy import StrategyReport
from core.tradingview_data_provider import (
    TradingViewQueryFilterConfig,
    TradingViewQueryPrefilterDiagnostics,
    build_tradingview_query_filter_config_from_cli,
    build_tradingview_query_prefilter_summary_lines,
    fetch_and_save_tradingview_snapshot,
    fetch_tradingview_egypt_frame,
    repair_missing_watchlist_symbols,
)
from main import parse_args


def _tv_row(
    symbol: str,
    *,
    close: float = 10.0,
    volume: float = 500_000,
) -> dict[str, object]:
    return {
        "name": f"EGX:{symbol}",
        "close": close,
        "change": 1.0,
        "volume": volume,
    }


def _live_row(
    symbol: str,
    close: float,
    previous_close: float,
    volume: float,
    volume_ratio: float,
) -> LiveSymbolSnapshot:
    return LiveSymbolSnapshot(
        symbol=symbol,
        date=date(2026, 7, 2),
        previous_close=previous_close,
        open=previous_close,
        high=max(close, previous_close) + 0.5,
        low=min(close, previous_close) - 0.5,
        close=close,
        volume=volume,
        change_percent=((close - previous_close) / previous_close) * 100,
        volume_ratio=volume_ratio,
        broke_previous_high=close > previous_close,
    )


def _scanner_result(
    symbol: str,
    decision: ScannerDecision,
    score: int,
    change_percent: float,
    volume_ratio: float,
    *,
    reasons: list[str] | None = None,
) -> ScannerResult:
    return ScannerResult(
        symbol=symbol,
        decision=decision,
        score=score,
        latest_close=100.0,
        change_percent=change_percent,
        volume_ratio=volume_ratio,
        broke_previous_high=True,
        above_sma_5=True,
        reasons=reasons or [],
        blockers=[],
    )


def test_parse_args_supports_tv_prefilter_flags() -> None:
    enabled_args = parse_args(["--enable-tv-prefilter"])
    disabled_args = parse_args(["--disable-tv-prefilter"])

    assert enabled_args.enable_tv_prefilter is True
    assert disabled_args.enable_tv_prefilter is False


def test_query_prefilter_config_disabled_by_default() -> None:
    config = build_tradingview_query_filter_config_from_cli(
        quality_filters=MarketQualityFilters(min_price=3.0, min_volume=300_000),
    )

    assert config.enabled is False


def test_provider_falls_back_if_prefilter_returns_too_few_rows() -> None:
    small_frame = pd.DataFrame(
        [{"name": "EGX:COMI", "close": 10.0, "change": 1.0, "volume": 500_000}]
    )
    full_frame = pd.DataFrame(
        [
            {
                "name": f"EGX:SYM{i}",
                "close": 10.0 + i,
                "change": 1.0,
                "volume": 500_000,
            }
            for i in range(80)
        ]
    )
    config = TradingViewQueryFilterConfig(
        enabled=True,
        min_price=3.0,
        min_volume=300_000,
        min_expected_rows=50,
    )

    with patch(
        "core.tradingview_data_provider._fetch_tradingview_frame",
        side_effect=[small_frame, full_frame],
    ) as mock_fetch:
        frame, _fields, diagnostics = fetch_tradingview_egypt_frame(config)

    assert len(frame) == 80
    assert diagnostics.fallback is True
    assert diagnostics.used is False
    assert diagnostics.attempted is True
    assert "returned only" in (diagnostics.fallback_reason or "")
    assert mock_fetch.call_count == 2
    assert mock_fetch.call_args_list[0].args[1] == config
    assert mock_fetch.call_args_list[1].args[1] is None


def test_prefilter_returns_filtered_market_rows() -> None:
    filtered_frame = pd.DataFrame([_tv_row(f"SYM{i}") for i in range(96)])
    config = TradingViewQueryFilterConfig(
        enabled=True,
        min_price=3.0,
        min_volume=300_000,
        min_expected_rows=50,
    )

    with patch(
        "core.tradingview_data_provider._fetch_tradingview_frame",
        return_value=filtered_frame,
    ) as mock_fetch:
        frame, _fields, diagnostics = fetch_tradingview_egypt_frame(config)

    assert len(frame) == 96
    assert diagnostics.used is True
    assert diagnostics.fallback is False
    assert diagnostics.rows_fetched == 96
    assert mock_fetch.call_count == 1
    assert mock_fetch.call_args.args[1] == config


def test_repair_missing_watchlist_symbols_merges_rows() -> None:
    prefiltered = pd.DataFrame([_tv_row("COMI"), _tv_row("HRHO")])
    repair_frame = pd.DataFrame(
        [
            _tv_row("COMI"),
            _tv_row("SWDY", volume=50_000),
            _tv_row("ORAS", close=1.5),
            _tv_row("OTHER"),
        ]
    )
    selected_fields = ["name", "close", "change", "volume"]

    with patch(
        "core.tradingview_data_provider._fetch_tradingview_frame",
        return_value=repair_frame,
    ) as mock_fetch:
        merged, repaired = repair_missing_watchlist_symbols(
            prefiltered,
            selected_fields,
            watchlist=["COMI", "SWDY", "ORAS"],
        )

    assert repaired == ("ORAS", "SWDY")
    assert len(merged) == 4
    assert mock_fetch.call_args.args[1] is None


def test_fetch_and_save_repairs_watchlist_when_prefilter_used(tmp_path) -> None:
    snapshot_path = tmp_path / "egx_live_snapshot.csv"
    prefiltered = pd.DataFrame([_tv_row("COMI"), _tv_row("HRHO")])
    repair_frame = pd.DataFrame(
        [
            _tv_row("SWDY", volume=50_000),
            _tv_row("ORAS", close=1.5),
        ]
    )
    selected_fields = ["name", "close", "change", "volume"]
    prefilter_diag = TradingViewQueryPrefilterDiagnostics(
        enabled=True,
        attempted=True,
        used=True,
        rows_fetched=2,
        fallback=False,
    )

    def fake_fetch(
        _config: TradingViewQueryFilterConfig | None = None,
    ) -> tuple[pd.DataFrame, list[str], TradingViewQueryPrefilterDiagnostics]:
        return prefiltered, selected_fields, prefilter_diag

    with (
        patch(
            "core.tradingview_data_provider.fetch_tradingview_egypt_frame",
            side_effect=fake_fetch,
        ),
        patch(
            "core.tradingview_data_provider._fetch_tradingview_frame",
            return_value=repair_frame,
        ),
    ):
        result = fetch_and_save_tradingview_snapshot(snapshot_path)

    assert result.success is True
    assert result.query_prefilter_diagnostics is not None
    assert result.query_prefilter_diagnostics.watchlist_repair is True
    assert result.query_prefilter_diagnostics.watchlist_repaired_symbols == ("ORAS", "SWDY")
    assert result.query_prefilter_diagnostics.rows_fetched == 2
    saved = pd.read_csv(snapshot_path)
    assert set(saved["symbol"]) == {"COMI", "HRHO", "ORAS", "SWDY"}
    assert any("watchlist repair added" in warning.lower() for warning in result.warnings)


def test_report_includes_tradingview_query_prefilter_diagnostics() -> None:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 2),
        symbols={
            "COMI": LiveSymbolSnapshot(
                symbol="COMI",
                date=date(2026, 7, 2),
                previous_close=99.0,
                open=99.0,
                high=101.0,
                low=98.5,
                close=100.0,
                volume=500_000,
                change_percent=1.0,
                volume_ratio=2.0,
                broke_previous_high=True,
            )
        },
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
    diagnostics = TradingViewQueryPrefilterDiagnostics(
        enabled=True,
        attempted=True,
        used=True,
        rows_fetched=96,
        fallback=False,
        watchlist_repair=True,
        watchlist_repaired_symbols=("ORAS", "SWDY"),
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        tv_query_filter_config=TradingViewQueryFilterConfig(enabled=True),
        tv_query_prefilter_diagnostics=diagnostics,
    )
    text = format_daily_report_text(report)
    prefilter_section = next(
        section
        for section in report.sections
        if section.title == "TradingView Query Prefilter"
    )

    assert "TradingView Query Prefilter:" in text
    assert "- Enabled: yes" in prefilter_section.lines
    assert "- Used: yes" in prefilter_section.lines
    assert "- Rows fetched: 96" in prefilter_section.lines
    assert "- Watchlist repair: yes" in prefilter_section.lines
    assert "- Watchlist repaired symbols: ORAS, SWDY" in prefilter_section.lines
    assert report.tv_query_prefilter["rows_fetched"] == 96
    assert report.tv_query_prefilter["watchlist_repair"] is True
    assert report.tv_query_prefilter["watchlist_repaired_symbols"] == ["ORAS", "SWDY"]


def test_repaired_watchlist_symbols_appear_in_watch_list_section() -> None:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 2),
        symbols={
            "GOOD": _live_row("GOOD", 10.0, 9.0, 500_000, 3.0),
            "SWDY": _live_row("SWDY", 8.0, 7.5, 50_000, 0.8),
            "ORAS": _live_row("ORAS", 2.0, 1.9, 500_000, 1.2),
        },
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=[],
        candidates=[
            _scanner_result("GOOD", ScannerDecision.CANDIDATE, 80, 5.0, 3.0),
        ],
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
    quality_result = apply_market_quality_filters(
        pd.DataFrame(
            [
                {"symbol": "GOOD", "close": 10.0, "volume": 500_000},
                {"symbol": "SWDY", "close": 8.0, "volume": 50_000},
                {"symbol": "ORAS", "close": 2.0, "volume": 500_000},
            ]
        ),
        MarketQualityFilters(min_price=3.0, min_volume=300_000),
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
        quality_filter_result=quality_result,
        configured_watchlist=["SWDY", "ORAS"],
        watchlist_scanner_results={
            "SWDY": _scanner_result(
                "SWDY",
                ScannerDecision.WATCH,
                55,
                2.0,
                0.8,
                reasons=["Repaired watchlist symbol"],
            ),
            "ORAS": _scanner_result(
                "ORAS",
                ScannerDecision.WATCH,
                50,
                1.0,
                1.2,
                reasons=["Repaired watchlist symbol"],
            ),
        },
        tv_query_prefilter_diagnostics=TradingViewQueryPrefilterDiagnostics(
            enabled=True,
            attempted=True,
            used=True,
            rows_fetched=96,
            watchlist_repair=True,
            watchlist_repaired_symbols=("ORAS", "SWDY"),
        ),
    )
    watch_section = next(
        section for section in report.sections if section.title == "Watch List"
    )
    watch_text = "\n".join(watch_section.lines)

    assert "SWDY" in watch_text
    assert "ORAS" in watch_text
    assert "missing from live snapshot" not in watch_text


def test_repaired_watchlist_symbols_do_not_bypass_quality_filters_for_candidates() -> None:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 2),
        symbols={
            "GOOD": _live_row("GOOD", 10.0, 9.0, 500_000, 3.0),
            "SWDY": _live_row("SWDY", 8.0, 7.5, 50_000, 0.8),
            "ORAS": _live_row("ORAS", 2.0, 1.9, 500_000, 1.2),
        },
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=[],
        candidates=[
            _scanner_result("GOOD", ScannerDecision.CANDIDATE, 80, 5.0, 3.0),
        ],
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
    quality_result = apply_market_quality_filters(
        pd.DataFrame(
            [
                {"symbol": "GOOD", "close": 10.0, "volume": 500_000},
                {"symbol": "SWDY", "close": 8.0, "volume": 50_000},
                {"symbol": "ORAS", "close": 2.0, "volume": 500_000},
            ]
        ),
        MarketQualityFilters(min_price=3.0, min_volume=300_000),
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
        quality_filter_result=quality_result,
        configured_watchlist=["SWDY", "ORAS"],
        watchlist_scanner_results={
            "SWDY": _scanner_result("SWDY", ScannerDecision.WATCH, 55, 2.0, 0.8),
            "ORAS": _scanner_result("ORAS", ScannerDecision.WATCH, 50, 1.0, 1.2),
        },
    )
    movers_section = next(
        section for section in report.sections if section.title == "Strongest Movers"
    )
    volume_section = next(
        section for section in report.sections if section.title == "Volume Leaders"
    )
    candidates_section = next(
        section for section in report.sections if section.title == "Top Candidates"
    )
    movers_text = "\n".join(movers_section.lines)
    volume_text = "\n".join(volume_section.lines)
    candidates_text = "\n".join(candidates_section.lines)

    assert "GOOD" in movers_text
    assert "SWDY" not in movers_text
    assert "ORAS" not in movers_text
    assert "SWDY" not in volume_text
    assert "ORAS" not in volume_text
    assert "GOOD" in candidates_text
    assert "SWDY" not in candidates_text
    assert "ORAS" not in candidates_text


def test_prefilter_summary_lines_include_watchlist_repair_diagnostics() -> None:
    lines = build_tradingview_query_prefilter_summary_lines(
        TradingViewQueryPrefilterDiagnostics(
            enabled=True,
            attempted=True,
            used=True,
            rows_fetched=96,
            watchlist_repair=True,
            watchlist_repaired_symbols=("ORAS", "SWDY"),
            fallback=False,
        )
    )

    assert "- Watchlist repair: yes" in lines
    assert "- Watchlist repaired symbols: ORAS, SWDY" in lines


def test_local_market_quality_filters_still_work_when_query_prefilter_disabled() -> None:
    frame = pd.DataFrame(
        [
            {"symbol": "GOOD", "close": 10.0, "volume": 500_000},
            {"symbol": "BAD", "close": 1.0, "volume": 10_000},
        ]
    )
    filters = MarketQualityFilters(min_price=3.0, min_volume=300_000)
    config = build_tradingview_query_filter_config_from_cli(
        enabled=False,
        quality_filters=filters,
    )

    result = apply_market_quality_filters(frame, filters)

    assert config.enabled is False
    assert result.filtered_count == 1
    assert result.filtered_df.iloc[0]["symbol"] == "GOOD"
