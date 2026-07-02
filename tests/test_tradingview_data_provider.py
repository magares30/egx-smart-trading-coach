"""Tests for TradingView screener snapshot normalization and CLI wiring."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from core.market_data_providers import (
    DATA_PROVIDER_EGX,
    DATA_PROVIDER_TRADINGVIEW,
    DEFAULT_DATA_PROVIDER,
    PARTIAL_TRADINGVIEW_SNAPSHOT_WARNING,
)
from core.tradingview_data_provider import (
    TradingViewQueryFilterConfig,
    TradingViewQueryPrefilterDiagnostics,
    TradingViewSnapshotResult,
    fetch_and_save_tradingview_snapshot,
    normalize_tradingview_frame,
    normalize_tradingview_symbol,
    tradingview_snapshot_is_usable,
)
from main import parse_args


def test_normalize_tradingview_symbol_strips_exchange_prefix() -> None:
    assert normalize_tradingview_symbol("EGX:SWDY") == "SWDY"
    assert normalize_tradingview_symbol("swdy") == "SWDY"


def test_normalize_tradingview_frame_maps_required_fields() -> None:
    frame = pd.DataFrame(
        [
            {
                "ticker": "EGX:COMI",
                "name": "COMI",
                "description": "Commercial International Bank",
                "close": 100.0,
                "change": 2.0,
                "volume": 1_000_000,
                "open": 99.0,
                "high": 101.0,
                "low": 98.5,
                "relative_volume_10d_calc": 1.5,
                "sector": "Banks",
            }
        ]
    )

    normalized, warnings = normalize_tradingview_frame(
        frame,
        snapshot_date=date(2026, 7, 2),
    )

    assert not warnings or all("Duplicate" not in warning for warning in warnings)
    assert len(normalized) == 1
    row = normalized.iloc[0]
    assert row["symbol"] == "COMI"
    assert row["company_name"] == "Commercial International Bank"
    assert row["close"] == 100.0
    assert row["previous_close"] == pytest.approx(100.0 / 1.02)
    assert row["volume_ratio"] == 1.5
    assert row["data_provider"] == DATA_PROVIDER_TRADINGVIEW


def test_normalize_tradingview_frame_maps_technical_fields() -> None:
    frame = pd.DataFrame(
        [
            {
                "ticker": "EGX:COMI",
                "description": "Commercial International Bank",
                "close": 100.0,
                "change": 2.0,
                "volume": 1_000_000,
                "relative_volume_10d_calc": 1.5,
                "RSI": 58.0,
                "EMA20": 95.0,
                "MACD.macd": 1.2,
                "MACD.signal": 0.8,
                "ADX": 25.0,
                "Recommend.All": 0.4,
            }
        ]
    )

    normalized, _warnings = normalize_tradingview_frame(
        frame,
        snapshot_date=date(2026, 7, 2),
    )

    row = normalized.iloc[0]
    assert row["rsi"] == 58.0
    assert row["ema20"] == 95.0
    assert row["macd"] == 1.2
    assert row["macd_signal"] == 0.8
    assert row["adx"] == 25.0
    assert row["tv_recommend_all"] == 0.4
    assert row["tv_relative_volume_10d"] == 1.5
    assert row["volume_ratio"] == 1.5


def test_normalize_tradingview_frame_uses_close_fallbacks() -> None:
    frame = pd.DataFrame(
        [
            {
                "ticker": "EGX:ORAS",
                "description": "Orascom Construction",
                "close": 50.0,
                "change": 0.0,
                "volume": 500.0,
            }
        ]
    )

    normalized, _warnings = normalize_tradingview_frame(frame, snapshot_date=date(2026, 7, 2))
    row = normalized.iloc[0]

    assert row["open"] == 50.0
    assert row["high"] == 50.0
    assert row["low"] == 50.0
    assert row["previous_close"] == 50.0
    assert row["volume_ratio"] == 1.0


def test_tradingview_snapshot_is_usable_threshold() -> None:
    low = TradingViewSnapshotResult(success=True, valid_symbol_count=50)
    high = TradingViewSnapshotResult(success=True, valid_symbol_count=80)
    failed = TradingViewSnapshotResult(success=False, valid_symbol_count=120)

    assert tradingview_snapshot_is_usable(low) is False
    assert tradingview_snapshot_is_usable(high) is True
    assert tradingview_snapshot_is_usable(failed) is False


def test_parse_args_data_provider_defaults_to_egx() -> None:
    args = parse_args([])
    assert args.data_provider == DEFAULT_DATA_PROVIDER
    assert args.data_provider == DATA_PROVIDER_EGX


def test_parse_args_data_provider_tradingview() -> None:
    args = parse_args(["--data-provider", "tradingview"])
    assert args.data_provider == DATA_PROVIDER_TRADINGVIEW


def test_fetch_and_save_tradingview_snapshot_writes_csv(tmp_path, monkeypatch) -> None:
    snapshot_path = tmp_path / "egx_live_snapshot.csv"
    raw_frame = pd.DataFrame(
        [
            {
                "ticker": "EGX:SWDY",
                "description": "El Sewedy Electric",
                "close": 200.0,
                "change": 1.0,
                "volume": 1000.0,
            }
        ]
    )

    def fake_fetch(
        _config: TradingViewQueryFilterConfig | None = None,
    ) -> tuple[pd.DataFrame, list[str], TradingViewQueryPrefilterDiagnostics]:
        return raw_frame, list(raw_frame.columns), TradingViewQueryPrefilterDiagnostics()

    monkeypatch.setattr(
        "core.tradingview_data_provider.fetch_tradingview_egypt_frame",
        fake_fetch,
    )

    result = fetch_and_save_tradingview_snapshot(snapshot_path)

    assert result.success is True
    assert snapshot_path.exists()
    saved = pd.read_csv(snapshot_path)
    assert "SWDY" in saved["symbol"].values


def test_partial_snapshot_warning_is_defined() -> None:
    assert "partial" in PARTIAL_TRADINGVIEW_SNAPSHOT_WARNING.lower()


def test_normalize_timeframe_snapshot_frame_maps_prefixed_columns() -> None:
    from core.tradingview_data_provider import _normalize_timeframe_snapshot_frame

    frame = pd.DataFrame(
        [
            {
                "name": "EGX:COMI",
                "close|60": 100.0,
                "change|60": 1.5,
                "RSI|60": 58.0,
                "MACD.macd|60": 1.2,
                "MACD.signal|60": 0.8,
                "EMA20|60": 95.0,
                "ADX|60": 24.0,
                "Recommend.All|60": 0.5,
            }
        ]
    )

    normalized = _normalize_timeframe_snapshot_frame(frame, "1h")

    assert len(normalized) == 1
    row = normalized.iloc[0]
    assert row["symbol"] == "COMI"
    assert row["tf_1h_close"] == 100.0
    assert row["tf_1h_rsi"] == 58.0
    assert row["tf_1h_recommend_all"] == 0.5


def test_fetch_tradingview_timeframe_snapshot_returns_empty_on_failure() -> None:
    from unittest.mock import patch

    from core.tradingview_data_provider import fetch_tradingview_timeframe_snapshot

    with patch("tradingview_screener.Query", side_effect=RuntimeError("network unavailable")):
        frame = fetch_tradingview_timeframe_snapshot("1h", ["COMI"])

    assert frame.empty
