"""Tests for EGX live snapshot loading."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.data_import import LIVE_SNAPSHOT_COLUMNS
from core.live_snapshot import EgxLiveSnapshotProvider
from core.live_volume import (
    NOT_ENOUGH_VOLUME_HISTORY_WARNING,
    LiveVolumeHistoryStore,
)


def _write_snapshot(path: Path, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows, columns=LIVE_SNAPSHOT_COLUMNS).to_csv(path, index=False)


def test_loads_valid_live_snapshot(tmp_path: Path) -> None:
    csv_path = tmp_path / "egx_live_snapshot.csv"
    _write_snapshot(
        csv_path,
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
            },
            {
                "date": "2026-01-07",
                "symbol": "HRHO",
                "previous_close": 10.0,
                "open": 10.1,
                "high": 10.6,
                "low": 9.9,
                "close": 10.4,
                "volume": 500,
            },
        ],
    )

    provider = EgxLiveSnapshotProvider(csv_path)
    snapshot = provider.load()

    assert snapshot.as_of_date == date(2026, 1, 7)
    assert set(snapshot.symbols) == {"COMI", "HRHO"}
    assert snapshot.symbols["COMI"].close == 80.5


def test_calculates_change_percent(tmp_path: Path) -> None:
    csv_path = tmp_path / "egx_live_snapshot.csv"
    _write_snapshot(
        csv_path,
        [
            {
                "date": "2026-01-07",
                "symbol": "COMI",
                "previous_close": 80.0,
                "open": 79.5,
                "high": 82.5,
                "low": 79.0,
                "close": 82.0,
                "volume": 0,
            }
        ],
    )

    snapshot = EgxLiveSnapshotProvider(csv_path).load()

    assert snapshot.symbols["COMI"].change_percent == pytest.approx(2.5)
    assert snapshot.symbols["COMI"].volume_ratio == 1.0


def test_detects_broke_previous_high(tmp_path: Path) -> None:
    csv_path = tmp_path / "egx_live_snapshot.csv"
    _write_snapshot(
        csv_path,
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
            },
            {
                "date": "2026-01-07",
                "symbol": "HRHO",
                "previous_close": 10.0,
                "open": 9.8,
                "high": 9.9,
                "low": 9.6,
                "close": 9.7,
                "volume": 1000,
            },
        ],
    )

    snapshot = EgxLiveSnapshotProvider(csv_path).load()

    assert snapshot.symbols["COMI"].broke_previous_high is True
    assert snapshot.symbols["HRHO"].broke_previous_high is False


def test_skips_bad_rows_with_warning(tmp_path: Path) -> None:
    csv_path = tmp_path / "egx_live_snapshot.csv"
    _write_snapshot(
        csv_path,
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
            },
            {
                "date": "2026-01-07",
                "symbol": "BAD1",
                "previous_close": 0.0,
                "open": 1.0,
                "high": 1.2,
                "low": 0.8,
                "close": 1.1,
                "volume": 100,
            },
            {
                "date": "2026-01-07",
                "symbol": "BAD2",
                "previous_close": 10.0,
                "open": 10.0,
                "high": 9.5,
                "low": 9.0,
                "close": 9.8,
                "volume": 100,
            },
        ],
    )

    provider = EgxLiveSnapshotProvider(csv_path)
    snapshot = provider.load()

    assert set(snapshot.symbols) == {"COMI"}
    assert any("Skipped invalid row for BAD1" in warning for warning in provider.warnings)
    assert any("Skipped invalid row for BAD2" in warning for warning in provider.warnings)


def test_fails_if_no_valid_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "egx_live_snapshot.csv"
    _write_snapshot(
        csv_path,
        [
            {
                "date": "2026-01-07",
                "symbol": "BAD1",
                "previous_close": 0.0,
                "open": 1.0,
                "high": 1.2,
                "low": 0.8,
                "close": 1.1,
                "volume": 100,
            }
        ],
    )

    with pytest.raises(ValueError, match="No valid rows remained"):
        EgxLiveSnapshotProvider(csv_path).load()


def test_provider_uses_volume_history_when_available(tmp_path: Path) -> None:
    history_dir = tmp_path / "live_history"
    store = LiveVolumeHistoryStore(history_dir)
    for day, volume in (
        ("2026-01-04", 1000),
        ("2026-01-05", 1000),
        ("2026-01-06", 1000),
    ):
        source = tmp_path / f"{day}.csv"
        _write_snapshot(
            source,
            [
                {
                    "date": day,
                    "symbol": "COMI",
                    "previous_close": 80.0,
                    "open": 79.5,
                    "high": 81.0,
                    "low": 79.0,
                    "close": 80.5,
                    "volume": volume,
                }
            ],
        )
        store.save_snapshot(source, date.fromisoformat(day))

    current = tmp_path / "egx_live_snapshot.csv"
    _write_snapshot(
        current,
        [
            {
                "date": "2026-01-07",
                "symbol": "COMI",
                "previous_close": 80.0,
                "open": 79.5,
                "high": 81.0,
                "low": 79.0,
                "close": 80.5,
                "volume": 2000,
            }
        ],
    )

    provider = EgxLiveSnapshotProvider(
        current,
        volume_history_store=store,
        lookback_days=20,
        min_history_days=3,
    )
    snapshot = provider.load()

    assert snapshot.symbols["COMI"].volume_ratio == pytest.approx(2.0)


def test_provider_falls_back_to_one_without_history(tmp_path: Path) -> None:
    csv_path = tmp_path / "egx_live_snapshot.csv"
    _write_snapshot(
        csv_path,
        [
            {
                "date": "2026-01-07",
                "symbol": "COMI",
                "previous_close": 80.0,
                "open": 79.5,
                "high": 81.0,
                "low": 79.0,
                "close": 80.5,
                "volume": 2000,
            }
        ],
    )

    provider = EgxLiveSnapshotProvider(
        csv_path,
        volume_history_store=LiveVolumeHistoryStore(tmp_path / "live_history"),
        min_history_days=3,
    )
    snapshot = provider.load()

    assert snapshot.symbols["COMI"].volume_ratio == 1.0
    assert any(
        NOT_ENOUGH_VOLUME_HISTORY_WARNING in warning
        for warning in provider.warnings
    )


def test_loads_mapped_snapshot_with_company_name_column(tmp_path: Path) -> None:
    from core.data_import import LIVE_SNAPSHOT_OUTPUT_COLUMNS
    from core.live_scanner_adapter import build_live_market_snapshot

    csv_path = tmp_path / "egx_live_snapshot.csv"
    pd.DataFrame(
        [
            {
                "date": "2026-01-07",
                "symbol": "COMI",
                "company_name": "Commercial International Bank-Egypt (CIB)",
                "previous_close": 80.0,
                "open": 79.5,
                "high": 81.0,
                "low": 79.0,
                "close": 80.5,
                "volume": 1000,
            }
        ],
        columns=LIVE_SNAPSHOT_OUTPUT_COLUMNS,
    ).to_csv(csv_path, index=False)

    live_snapshot = EgxLiveSnapshotProvider(csv_path).load()
    market_snapshot, _, warnings, _ = build_live_market_snapshot(
        live_snapshot,
        watchlist=["COMI"],
        index_symbols=[],
    )

    assert "COMI" in live_snapshot.symbols
    assert len(market_snapshot.symbols) == 1
    assert market_snapshot.symbols[0].symbol == "COMI"
    assert not any("Watchlist symbol COMI missing" in warning for warning in warnings)
