"""Tests for live snapshot scanner adapter and CLI flags."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from config.watchlist import MARKET_INDEX_SYMBOLS
from core.data_import import LIVE_SNAPSHOT_COLUMNS
from core.live_scanner_adapter import (
    MISSING_INDEX_MOOD_WARNING,
    build_live_market_snapshot,
    live_symbol_to_symbol_snapshot,
)
from core.live_snapshot import EgxLiveSnapshotProvider, LiveSymbolSnapshot
from core.market_mood import MarketMood
from main import parse_args


def _live_row(symbol: str, close: float, previous_close: float) -> LiveSymbolSnapshot:
    return LiveSymbolSnapshot(
        symbol=symbol,
        date=date(2026, 1, 7),
        previous_close=previous_close,
        open=previous_close,
        high=max(close, previous_close) + 0.5,
        low=min(close, previous_close) - 0.5,
        close=close,
        volume=1000.0,
        change_percent=((close - previous_close) / previous_close) * 100,
        volume_ratio=1.0,
        broke_previous_high=max(close, previous_close) + 0.5 > previous_close,
    )


def test_live_symbol_to_symbol_snapshot_uses_live_fields() -> None:
    live = _live_row("COMI", close=82.0, previous_close=80.0)
    snapshot, sma_warning = live_symbol_to_symbol_snapshot(live)

    assert snapshot.latest_close == 82.0
    assert snapshot.previous_close == 80.0
    assert snapshot.change_percent == pytest.approx(2.5)
    assert snapshot.volume_ratio == 1.0
    assert snapshot.above_sma_5 is True
    assert snapshot.broke_previous_high is live.broke_previous_high
    assert sma_warning is None


def test_build_live_market_snapshot_sets_neutral_mood_without_indexes() -> None:
    from core.live_snapshot import LiveMarketSnapshot

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 1, 7),
        symbols={
            "COMI": _live_row("COMI", close=82.0, previous_close=80.0),
            "HRHO": _live_row("HRHO", close=10.4, previous_close=10.0),
        },
    )

    market_snapshot, mood_result, warnings, _ = build_live_market_snapshot(
        live_snapshot,
        watchlist=["COMI", "HRHO"],
        index_symbols=MARKET_INDEX_SYMBOLS,
    )

    assert len(market_snapshot.symbols) == 2
    assert market_snapshot.index_snapshots == []
    assert mood_result.mood == MarketMood.NEUTRAL
    assert mood_result.score == 50
    assert MISSING_INDEX_MOOD_WARNING in warnings


def test_build_live_market_snapshot_uses_index_rows_for_mood() -> None:
    from core.live_snapshot import LiveMarketSnapshot

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 1, 7),
        symbols={
            "COMI": _live_row("COMI", close=82.0, previous_close=80.0),
            "EGX30": _live_row("EGX30", close=2050.0, previous_close=2000.0),
            "EGX70": _live_row("EGX70", close=3100.0, previous_close=3000.0),
        },
    )

    _, mood_result, warnings, _ = build_live_market_snapshot(
        live_snapshot,
        watchlist=["COMI"],
        index_symbols=MARKET_INDEX_SYMBOLS,
    )

    assert mood_result.mood == MarketMood.STRONG
    assert MISSING_INDEX_MOOD_WARNING not in warnings


def test_live_snapshot_provider_roundtrip_for_scanner(tmp_path: Path) -> None:
    csv_path = tmp_path / "egx_live_snapshot.csv"
    pd.DataFrame(
        [
            {
                "date": "2026-01-07",
                "symbol": "COMI",
                "previous_close": 80.0,
                "open": 79.5,
                "high": 81.0,
                "low": 79.0,
                "close": 80.5,
                "volume": 1000,
            }
        ],
        columns=LIVE_SNAPSHOT_COLUMNS,
    ).to_csv(csv_path, index=False)

    live_snapshot = EgxLiveSnapshotProvider(csv_path).load()
    market_snapshot, _, _, _ = build_live_market_snapshot(
        live_snapshot,
        watchlist=["COMI"],
        index_symbols=[],
    )

    assert len(market_snapshot.symbols) == 1
    assert market_snapshot.symbols[0].symbol == "COMI"
    assert market_snapshot.symbols[0].latest_close == 80.5


def test_parse_args_egx_live_scan() -> None:
    args = parse_args(["--egx-live-scan"])
    assert args.egx_live_scan is True
    assert args.egx_live_snapshot is None


def test_parse_args_egx_live_snapshot() -> None:
    args = parse_args(
        [
            "--egx-live-scan",
            "--egx-live-snapshot",
            "data/real/custom_live.csv",
        ]
    )
    assert args.egx_live_scan is True
    assert args.egx_live_snapshot == Path("data/real/custom_live.csv")


def test_parse_args_egx_update_and_live_scan() -> None:
    args = parse_args(["--egx-update-and-live-scan"])
    assert args.egx_update_and_live_scan is True
    assert args.chrome_cdp_url == "http://127.0.0.1:9222"


def test_parse_args_egx_one_click_live_scan() -> None:
    args = parse_args(["--egx-one-click-live-scan"])
    assert args.egx_one_click_live_scan is True
    assert args.chrome_profile_dir is None


def test_parse_args_chrome_profile_dir() -> None:
    args = parse_args(
        [
            "--egx-one-click-live-scan",
            "--chrome-profile-dir",
            "C:/egx_chrome_profile",
        ]
    )
    assert args.egx_one_click_live_scan is True
    assert args.chrome_profile_dir == Path("C:/egx_chrome_profile")


def test_parse_args_live_volume_lookback_days() -> None:
    args = parse_args(
        [
            "--egx-live-scan",
            "--live-volume-lookback-days",
            "15",
        ]
    )
    assert args.live_volume_lookback_days == 15


def test_parse_args_live_volume_min_history_days() -> None:
    args = parse_args(
        [
            "--egx-live-scan",
            "--live-volume-min-history-days",
            "5",
        ]
    )
    assert args.live_volume_min_history_days == 5


def test_parse_args_save_live_history_only() -> None:
    args = parse_args(["--save-live-history-only"])
    assert args.save_live_history_only is True


def test_parse_args_egx_daily_report() -> None:
    args = parse_args(["--egx-daily-report"])
    assert args.egx_daily_report is True


def test_parse_args_egx_one_click_daily_report() -> None:
    args = parse_args(["--egx-one-click-daily-report"])
    assert args.egx_one_click_daily_report is True


def test_parse_args_egx_live_paper_trade() -> None:
    args = parse_args(["--egx-live-paper-trade"])
    assert args.egx_live_paper_trade is True
    assert args.live_paper_max_trades == 3
    assert args.live_paper_min_confidence == 75


def test_parse_args_egx_one_click_paper_trade() -> None:
    args = parse_args(["--egx-one-click-paper-trade"])
    assert args.egx_one_click_paper_trade is True
    assert args.chrome_cdp_url == "http://127.0.0.1:9222"


def test_parse_args_live_paper_max_trades() -> None:
    args = parse_args(
        [
            "--egx-live-paper-trade",
            "--live-paper-max-trades",
            "5",
        ]
    )
    assert args.live_paper_max_trades == 5


def test_parse_args_live_paper_min_confidence() -> None:
    args = parse_args(
        [
            "--egx-live-paper-trade",
            "--live-paper-min-confidence",
            "80",
        ]
    )
    assert args.live_paper_min_confidence == 80


def test_parse_args_egx_live_paper_monitor() -> None:
    args = parse_args(["--egx-live-paper-monitor"])
    assert args.egx_live_paper_monitor is True


def test_parse_args_egx_one_click_paper_monitor() -> None:
    args = parse_args(["--egx-one-click-paper-monitor"])
    assert args.egx_one_click_paper_monitor is True
    assert args.chrome_cdp_url == "http://127.0.0.1:9222"


def test_parse_args_egx_one_click_paper_cycle() -> None:
    args = parse_args(["--egx-one-click-paper-cycle"])
    assert args.egx_one_click_paper_cycle is True
    assert args.live_paper_max_trades == 3


def test_parse_args_egx_workflow_report() -> None:
    args = parse_args(["--egx-workflow", "report"])
    assert args.egx_workflow == "report"
    assert args.egx_local is False


def test_parse_args_egx_workflow_local_scan() -> None:
    args = parse_args(["--egx-workflow", "scan", "--egx-local"])
    assert args.egx_workflow == "scan"
    assert args.egx_local is True


def test_parse_args_egx_workflow_cycle_with_reset() -> None:
    args = parse_args(["--egx-workflow", "cycle", "--reset-paper-state"])
    assert args.egx_workflow == "cycle"
    assert args.reset_paper_state is True


def test_parse_args_egx_workflow_portfolio() -> None:
    args = parse_args(["--egx-workflow", "portfolio", "--egx-local"])
    assert args.egx_workflow == "portfolio"
    assert args.egx_local is True


def test_parse_args_egx_workflow_with_data_provider() -> None:
    args = parse_args(
        [
            "--egx-workflow",
            "report",
            "--data-provider",
            "tradingview",
            "--scanner-universe",
            "full-market",
        ]
    )
    assert args.egx_workflow == "report"
    assert args.data_provider == "tradingview"
