"""Tests for CSV market data provider and market mood detector."""

from datetime import date
from pathlib import Path

import pytest

from config import settings
from core.market_data import CsvMarketDataProvider, SymbolSnapshot
from core.market_mood import MarketMood, MarketMoodDetector


@pytest.fixture
def provider() -> CsvMarketDataProvider:
    return CsvMarketDataProvider(settings.EGX_DAILY_SAMPLE_PATH)


def test_csv_provider_loads_data(provider: CsvMarketDataProvider) -> None:
    df = provider.load_data()

    assert len(df) >= 30
    assert set(df.columns) == {"date", "symbol", "open", "high", "low", "close", "volume"}
    assert "COMI" in df["symbol"].values


def test_get_latest_bar_returns_latest_date(provider: CsvMarketDataProvider) -> None:
    bar = provider.get_latest_bar("COMI")

    assert bar.symbol == "COMI"
    assert bar.date == date(2026, 6, 27)
    assert bar.close == 80.0


def test_build_symbol_snapshot_calculates_change_percent(
    provider: CsvMarketDataProvider,
) -> None:
    snapshot = provider.build_symbol_snapshot("COMI")

    assert snapshot.symbol == "COMI"
    assert snapshot.latest_close == 80.0
    assert snapshot.previous_close == 79.5
    assert snapshot.change == pytest.approx(0.5)
    assert snapshot.change_percent == pytest.approx(0.5 / 79.5 * 100)
    assert snapshot.broke_previous_high is False  # close 80.0 == previous high 80.0
    assert snapshot.above_sma_5 is True
    assert snapshot.above_sma_20 is None


def test_missing_symbol_raises_value_error(provider: CsvMarketDataProvider) -> None:
    with pytest.raises(ValueError, match="not found"):
        provider.get_latest_bar("INVALID")


def _make_index_snapshot(
    symbol: str,
    change_percent: float,
    above_sma_5: bool,
    volume_ratio: float = 1.0,
) -> SymbolSnapshot:
    return SymbolSnapshot(
        symbol=symbol,
        latest_close=100.0,
        previous_close=100.0 - change_percent,
        change=change_percent,
        change_percent=change_percent,
        latest_volume=1000,
        average_volume_5d=1000.0,
        volume_ratio=volume_ratio,
        day_high=101.0,
        day_low=99.0,
        broke_previous_high=change_percent > 0,
        above_sma_5=above_sma_5,
        above_sma_20=None,
    )


def test_market_mood_detector_returns_strong() -> None:
    detector = MarketMoodDetector()
    snapshots = [
        _make_index_snapshot("EGX30", change_percent=1.0, above_sma_5=True, volume_ratio=1.5),
        _make_index_snapshot("EGX70", change_percent=0.8, above_sma_5=True, volume_ratio=1.3),
    ]

    result = detector.evaluate(snapshots)

    assert result.mood == MarketMood.STRONG
    assert result.score >= 70
    assert len(result.reasons) > 0


def test_market_mood_detector_returns_neutral() -> None:
    detector = MarketMoodDetector()
    snapshots = [
        _make_index_snapshot("EGX30", change_percent=0.2, above_sma_5=True),
        _make_index_snapshot("EGX70", change_percent=0.1, above_sma_5=False),
    ]

    result = detector.evaluate(snapshots)

    assert result.mood == MarketMood.NEUTRAL
    assert 40 < result.score < 70


def test_market_mood_detector_returns_weak() -> None:
    detector = MarketMoodDetector()
    snapshots = [
        _make_index_snapshot("EGX30", change_percent=-1.5, above_sma_5=False),
        _make_index_snapshot("EGX70", change_percent=-1.0, above_sma_5=False),
    ]

    result = detector.evaluate(snapshots)

    assert result.mood == MarketMood.WEAK
    assert result.score <= 40
    assert len(result.blockers) > 0


def test_build_market_snapshot(provider: CsvMarketDataProvider) -> None:
    snapshot = provider.build_market_snapshot(
        symbols=["COMI", "HRHO"],
        index_symbols=["EGX30"],
    )

    assert len(snapshot.symbols) == 2
    assert len(snapshot.index_snapshots) == 1
    assert snapshot.symbols[0].symbol == "COMI"
