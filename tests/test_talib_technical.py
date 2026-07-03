"""Tests for TA-Lib technical engine."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from config import settings
from core.data_import import LIVE_SNAPSHOT_COLUMNS
from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from core.live_volume import LiveVolumeHistoryStore
from core.talib_technical import (
    TALIB_INSUFFICIENT_HISTORY_NOTE,
    TALIB_NOT_INSTALLED_WARNING,
    TalibOverallStatus,
    TalibTechnicalConfig,
    TalibTrendStatus,
    build_talib_lookup_for_symbols,
    build_talib_technical_config_from_cli,
    evaluate_talib_technical_from_bars,
    format_talib_runtime_log_line,
    format_talib_technical_line,
    format_talib_strategy_note,
    format_technical_engines_report_lines,
    is_talib_engine_available,
    resolve_talib_runtime_status,
)


def _require_talib():
    return pytest.importorskip("talib")


def _synthetic_bars(count: int, *, trend: float = 0.5) -> list[dict[str, float]]:
    bars: list[dict[str, float]] = []
    for index in range(count):
        close = 80.0 + (index * trend)
        bars.append(
            {
                "open": close - 0.3,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1000.0 + (index * 10.0),
            }
        )
    return bars


def _minimal_live_snapshot() -> LiveMarketSnapshot:
    return LiveMarketSnapshot(
        as_of_date=date(2026, 2, 20),
        symbols={
            "COMI": LiveSymbolSnapshot(
                symbol="COMI",
                date=date(2026, 2, 20),
                previous_close=127.0,
                open=127.5,
                high=129.0,
                low=127.0,
                close=128.5,
                volume=2500,
                change_percent=1.18,
                volume_ratio=1.5,
                broke_previous_high=True,
            )
        },
    )


def test_build_talib_config_from_cli() -> None:
    config = build_talib_technical_config_from_cli(
        enabled=False,
        min_history_days=30,
    )
    assert config.enabled is False
    assert config.min_history_days == 30


def test_insufficient_history_result() -> None:
    _require_talib()
    result = evaluate_talib_technical_from_bars(
        _synthetic_bars(10),
        TalibTechnicalConfig(min_history_days=50),
    )

    assert result.talib_available is False
    assert result.status == TalibOverallStatus.INSUFFICIENT_HISTORY
    assert TALIB_INSUFFICIENT_HISTORY_NOTE in result.notes[0]
    assert "INSUFFICIENT_HISTORY" in format_talib_technical_line(result)


def test_evaluate_talib_technical_with_enough_bars() -> None:
    _require_talib()
    result = evaluate_talib_technical_from_bars(
        _synthetic_bars(55),
        TalibTechnicalConfig(min_history_days=50),
    )

    assert result.talib_available is True
    assert result.status in {
        TalibOverallStatus.STRONG,
        TalibOverallStatus.OK,
        TalibOverallStatus.CAUTION,
    }
    assert result.trend_status == TalibTrendStatus.BULLISH
    assert result.indicators["rsi"] is not None
    assert result.indicators["macd"] is not None
    assert result.indicators["ema20"] is not None
    assert "TA-Lib:" in format_talib_technical_line(result)
    assert format_talib_strategy_note(result) is not None


def test_missing_ohlcv_values_return_insufficient_history() -> None:
    _require_talib()
    bars = _synthetic_bars(55)
    bars[-1]["close"] = float("nan")
    result = evaluate_talib_technical_from_bars(
        bars,
        TalibTechnicalConfig(min_history_days=50),
    )

    assert result.status == TalibOverallStatus.INSUFFICIENT_HISTORY


def test_build_talib_lookup_uses_live_history(tmp_path: Path) -> None:
    _require_talib()
    import pandas as pd

    history_dir = tmp_path / "live_history"
    store = LiveVolumeHistoryStore(history_dir)

    for offset in range(49):
        day = date(2026, 1, 1).toordinal() + offset
        snapshot_date = date.fromordinal(day)
        day_text = snapshot_date.isoformat()
        source = tmp_path / f"snapshot_{day_text}.csv"
        pd.DataFrame(
            [
                {
                    "date": day_text,
                    "symbol": "COMI",
                    "previous_close": 79.0 + offset,
                    "open": 79.0 + offset,
                    "high": 81.0 + offset,
                    "low": 78.5 + offset,
                    "close": 80.0 + offset,
                    "volume": 1000 + offset,
                }
            ],
            columns=LIVE_SNAPSHOT_COLUMNS,
        ).to_csv(source, index=False)
        store.save_snapshot(source, snapshot_date)

    lookup, warnings = build_talib_lookup_for_symbols(
        ["COMI"],
        history_store=store,
        live_snapshot=_minimal_live_snapshot(),
        config=TalibTechnicalConfig(min_history_days=50),
    )

    assert warnings == []
    assert "COMI" in lookup
    assert lookup["COMI"].talib_available is True
    assert lookup["COMI"].indicators["rsi"] is not None


def test_talib_not_installed_is_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.talib_technical.TALIB_AVAILABLE", False)
    result = evaluate_talib_technical_from_bars(
        _synthetic_bars(55),
        TalibTechnicalConfig(min_history_days=50),
    )

    assert result.talib_available is False
    assert TALIB_NOT_INSTALLED_WARNING in result.notes[0]


def test_build_talib_lookup_returns_warning_when_not_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.talib_technical.TALIB_AVAILABLE", False)
    store = LiveVolumeHistoryStore(tmp_path / "live_history")

    lookup, warnings = build_talib_lookup_for_symbols(
        ["COMI"],
        history_store=store,
        live_snapshot=_minimal_live_snapshot(),
        config=TalibTechnicalConfig(min_history_days=50),
    )

    assert lookup == {}
    assert warnings == [TALIB_NOT_INSTALLED_WARNING]


def test_default_min_history_days_matches_settings() -> None:
    config = build_talib_technical_config_from_cli()
    assert config.min_history_days == settings.DEFAULT_TALIB_MIN_HISTORY_DAYS


def test_is_talib_engine_available_when_installed() -> None:
    _require_talib()
    assert is_talib_engine_available() is True


def test_resolve_talib_runtime_status_active_when_installed() -> None:
    status = resolve_talib_runtime_status(enabled=True, package_installed=True)
    assert status.talib_available is True
    assert status.talib_mode == "active"
    assert status.talib_reason == ""


def test_resolve_talib_runtime_status_fallback_when_not_installed() -> None:
    status = resolve_talib_runtime_status(enabled=True, package_installed=False)
    assert status.talib_available is False
    assert status.talib_mode == "fallback"
    assert status.talib_reason == "talib package not installed"


def test_resolve_talib_runtime_status_fallback_when_disabled() -> None:
    status = resolve_talib_runtime_status(enabled=False, package_installed=True)
    assert status.talib_available is False
    assert status.talib_mode == "fallback"
    assert status.talib_reason == "talib engine disabled"


def test_format_talib_runtime_log_line_fallback() -> None:
    status = resolve_talib_runtime_status(enabled=True, package_installed=False)
    line = format_talib_runtime_log_line(status)
    assert "TALIB_RUNTIME_STATUS available=false mode=fallback" in line
    assert "talib package not installed" in line


def test_format_technical_engines_report_lines() -> None:
    active = resolve_talib_runtime_status(enabled=True, package_installed=True)
    lines = format_technical_engines_report_lines(
        active,
        tradingview_technical_available=True,
    )
    assert any("TradingView technical: ACTIVE" in line for line in lines)
    assert any("TA-Lib: ACTIVE" in line for line in lines)

    fallback = resolve_talib_runtime_status(enabled=True, package_installed=False)
    lines = format_technical_engines_report_lines(
        fallback,
        tradingview_technical_available=False,
    )
    assert any("TA-Lib: FALLBACK" in line for line in lines)
