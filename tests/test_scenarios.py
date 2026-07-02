"""Tests for bull, mixed, and weak sample market scenarios."""

import pytest

from config import settings
from config.watchlist import DEFAULT_WATCHLIST, MARKET_INDEX_SYMBOLS
from core.market_data import CsvMarketDataProvider
from core.market_mood import MarketMood, MarketMoodDetector
from core.scanner import EgyptianMomentumScanner, ScannerDecision


def _run_scenario(csv_path) -> tuple:
    provider = CsvMarketDataProvider(csv_path)
    snapshot = provider.build_market_snapshot(
        DEFAULT_WATCHLIST, MARKET_INDEX_SYMBOLS
    )
    mood_result = MarketMoodDetector().evaluate(snapshot.index_snapshots)
    report = EgyptianMomentumScanner(mood_result).scan(snapshot)
    return mood_result, report


def test_bull_scenario_strong_mood_with_candidates() -> None:
    mood_result, report = _run_scenario(settings.EGX_BULL_SAMPLE_PATH)

    assert mood_result.mood == MarketMood.STRONG
    assert len(report.candidates) >= 2


def test_mixed_scenario_neutral_mood_with_varied_results() -> None:
    mood_result, report = _run_scenario(settings.EGX_MIXED_SAMPLE_PATH)

    assert mood_result.mood == MarketMood.NEUTRAL
    assert len(report.watchlist) + len(report.blocked) >= 1
    assert len(report.candidates) + len(report.watchlist) > 0
    assert len(report.blocked) < len(DEFAULT_WATCHLIST)


def test_weak_scenario_blocks_all_symbols() -> None:
    mood_result, report = _run_scenario(settings.EGX_WEAK_SAMPLE_PATH)

    assert mood_result.mood == MarketMood.WEAK
    assert len(report.candidates) == 0
    assert len(report.watchlist) == 0
    assert len(report.blocked) == len(DEFAULT_WATCHLIST)

    for result in report.blocked:
        assert result.decision == ScannerDecision.BLOCKED
        assert "Market mood is weak" in result.blockers
