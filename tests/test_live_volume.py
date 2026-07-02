"""Tests for live volume history and ratio calculation."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.data_import import LIVE_SNAPSHOT_COLUMNS
from core.live_volume import (
    NOT_ENOUGH_VOLUME_HISTORY_WARNING,
    LiveVolumeHistoryStore,
)


def _write_snapshot(path: Path, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows, columns=LIVE_SNAPSHOT_COLUMNS).to_csv(path, index=False)


def _comi_row(day: str, volume: float) -> dict[str, object]:
    return {
        "date": day,
        "symbol": "COMI",
        "previous_close": 80.0,
        "open": 79.5,
        "high": 81.0,
        "low": 79.0,
        "close": 80.5,
        "volume": volume,
    }


def test_save_snapshot_creates_dated_file(tmp_path: Path) -> None:
    history_dir = tmp_path / "live_history"
    source = tmp_path / "egx_live_snapshot.csv"
    _write_snapshot(source, [_comi_row("2026-01-07", 1000)])

    store = LiveVolumeHistoryStore(history_dir)
    saved = store.save_snapshot(source, date(2026, 1, 7))

    assert saved == history_dir / "egx_live_snapshot_20260107.csv"
    assert saved.exists()
    reread = pd.read_csv(saved)
    assert reread.loc[0, "volume"] == 1000


def test_save_snapshot_overwrites_same_date(tmp_path: Path) -> None:
    history_dir = tmp_path / "live_history"
    source = tmp_path / "egx_live_snapshot.csv"
    store = LiveVolumeHistoryStore(history_dir)

    _write_snapshot(source, [_comi_row("2026-01-07", 1000)])
    store.save_snapshot(source, date(2026, 1, 7))
    _write_snapshot(source, [_comi_row("2026-01-07", 1500)])
    store.save_snapshot(source, date(2026, 1, 7))

    saved = history_dir / "egx_live_snapshot_20260107.csv"
    assert pd.read_csv(saved).loc[0, "volume"] == 1500


def test_load_previous_volumes_calculates_average(tmp_path: Path) -> None:
    history_dir = tmp_path / "live_history"
    store = LiveVolumeHistoryStore(history_dir)

    for day, volume in (
        ("2026-01-05", 1000),
        ("2026-01-06", 1200),
        ("2026-01-07", 1400),
    ):
        source = tmp_path / f"{day}.csv"
        _write_snapshot(source, [_comi_row(day, volume)])
        store.save_snapshot(source, date.fromisoformat(day))

    previous = store.load_previous_volumes("COMI", date(2026, 1, 8), lookback_days=20)

    assert previous == [1400.0, 1200.0, 1000.0]


def test_calculate_volume_stats_returns_expected_ratio(tmp_path: Path) -> None:
    history_dir = tmp_path / "live_history"
    store = LiveVolumeHistoryStore(history_dir)

    for day, volume in (
        ("2026-01-04", 1000),
        ("2026-01-05", 1000),
        ("2026-01-06", 1000),
    ):
        source = tmp_path / f"{day}.csv"
        _write_snapshot(source, [_comi_row(day, volume)])
        store.save_snapshot(source, date.fromisoformat(day))

    stats = store.calculate_volume_stats(
        symbol="COMI",
        current_volume=2000,
        current_date=date(2026, 1, 7),
        lookback_days=20,
        min_history_days=3,
    )

    assert stats.history_days == 3
    assert stats.average_volume == pytest.approx(1000.0)
    assert stats.volume_ratio == pytest.approx(2.0)
    assert stats.warning is None


def test_insufficient_history_returns_one_with_warning(tmp_path: Path) -> None:
    history_dir = tmp_path / "live_history"
    store = LiveVolumeHistoryStore(history_dir)
    source = tmp_path / "2026-01-05.csv"
    _write_snapshot(source, [_comi_row("2026-01-05", 1000)])
    store.save_snapshot(source, date(2026, 1, 5))

    stats = store.calculate_volume_stats(
        symbol="COMI",
        current_volume=2000,
        current_date=date(2026, 1, 7),
        lookback_days=20,
        min_history_days=3,
    )

    assert stats.volume_ratio == 1.0
    assert stats.warning == NOT_ENOUGH_VOLUME_HISTORY_WARNING
    assert stats.history_days == 1


def test_ignores_current_day_history_file(tmp_path: Path) -> None:
    history_dir = tmp_path / "live_history"
    store = LiveVolumeHistoryStore(history_dir)

    for day, volume in (
        ("2026-01-04", 1000),
        ("2026-01-05", 1000),
        ("2026-01-06", 1000),
        ("2026-01-07", 5000),
    ):
        source = tmp_path / f"{day}.csv"
        _write_snapshot(source, [_comi_row(day, volume)])
        store.save_snapshot(source, date.fromisoformat(day))

    previous = store.load_previous_volumes("COMI", date(2026, 1, 7), lookback_days=20)

    assert previous == [1000.0, 1000.0, 1000.0]
    assert 5000.0 not in previous


def test_ignores_bad_volume_rows(tmp_path: Path) -> None:
    history_dir = tmp_path / "live_history"
    store = LiveVolumeHistoryStore(history_dir)
    bad_source = tmp_path / "bad.csv"
    _write_snapshot(
        bad_source,
        [
            {
                "date": "2026-01-05",
                "symbol": "COMI",
                "previous_close": 80.0,
                "open": 79.5,
                "high": 81.0,
                "low": 79.0,
                "close": 80.5,
                "volume": -100,
            }
        ],
    )
    store.save_snapshot(bad_source, date(2026, 1, 5))

    previous = store.load_previous_volumes("COMI", date(2026, 1, 7), lookback_days=20)

    assert previous == []


def test_load_previous_closes_returns_recent_closes(tmp_path: Path) -> None:
    history_dir = tmp_path / "live_history"
    store = LiveVolumeHistoryStore(history_dir)
    for day, close in (
        ("2026-01-04", 79.0),
        ("2026-01-05", 80.0),
        ("2026-01-06", 81.0),
    ):
        source = tmp_path / f"{day}.csv"
        row = _comi_row(day, 1000)
        row["close"] = close
        _write_snapshot(source, [row])
        store.save_snapshot(source, date.fromisoformat(day))

    closes = store.load_previous_closes("COMI", date(2026, 1, 7), count=3)

    assert closes == [81.0, 80.0, 79.0]


def test_load_previous_day_high_returns_latest_prior_high(tmp_path: Path) -> None:
    history_dir = tmp_path / "live_history"
    store = LiveVolumeHistoryStore(history_dir)
    source = tmp_path / "2026-01-06.csv"
    row = _comi_row("2026-01-06", 1000)
    row["high"] = 82.5
    _write_snapshot(source, [row])
    store.save_snapshot(source, date(2026, 1, 6))

    previous_high = store.load_previous_day_high("COMI", date(2026, 1, 7))

    assert previous_high == 82.5


def test_load_ohlcv_series_returns_chronological_bars(tmp_path: Path) -> None:
    history_dir = tmp_path / "live_history"
    store = LiveVolumeHistoryStore(history_dir)

    for day, close in (
        ("2026-01-05", 80.0),
        ("2026-01-06", 81.0),
        ("2026-01-07", 82.0),
    ):
        source = tmp_path / f"{day}.csv"
        row = _comi_row(day, 1000)
        row["close"] = close
        _write_snapshot(source, [row])
        store.save_snapshot(source, date.fromisoformat(day))

    bars = store.load_ohlcv_series(
        "COMI",
        before_date=date(2026, 1, 8),
        count=3,
        current_bar={
            "open": 82.5,
            "high": 83.0,
            "low": 82.0,
            "close": 82.8,
            "volume": 1200.0,
        },
    )

    assert len(bars) == 4
    assert [bar["close"] for bar in bars] == [80.0, 81.0, 82.0, 82.8]
