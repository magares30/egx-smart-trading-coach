"""Tests for Egyptian Momentum Scanner A."""

import pytest

from config import settings
from config.watchlist import DEFAULT_WATCHLIST, MARKET_INDEX_SYMBOLS
from core.market_data import CsvMarketDataProvider, MarketSnapshot, SymbolSnapshot
from core.market_mood import MarketMood, MarketMoodDetector, MarketMoodResult
from core.scanner import EgyptianMomentumScanner, ScannerDecision


def _make_snapshot(
    symbol: str,
    change_percent: float,
    volume_ratio: float = 1.3,
    broke_previous_high: bool = True,
    above_sma_5: bool = True,
    latest_close: float = 100.0,
) -> SymbolSnapshot:
    return SymbolSnapshot(
        symbol=symbol,
        latest_close=latest_close,
        previous_close=latest_close * (1 - change_percent / 100),
        change=latest_close * change_percent / 100,
        change_percent=change_percent,
        latest_volume=1_000_000,
        average_volume_5d=800_000.0,
        volume_ratio=volume_ratio,
        day_high=latest_close + 1,
        day_low=latest_close - 1,
        broke_previous_high=broke_previous_high,
        above_sma_5=above_sma_5,
        above_sma_20=None,
    )


def _strong_mood() -> MarketMoodResult:
    return MarketMoodResult(mood=MarketMood.STRONG, score=85, reasons=[], blockers=[])


def _weak_mood() -> MarketMoodResult:
    return MarketMoodResult(mood=MarketMood.WEAK, score=20, reasons=[], blockers=[])


def test_scanner_returns_candidates_when_strong_momentum() -> None:
    snapshot = MarketSnapshot(
        symbols=[
            _make_snapshot("FWRY", change_percent=3.0, volume_ratio=1.5),
            _make_snapshot("COMI", change_percent=0.5, volume_ratio=1.0),
        ],
        index_snapshots=[],
    )
    scanner = EgyptianMomentumScanner(_strong_mood())
    report = scanner.scan(snapshot)

    assert len(report.candidates) >= 1
    fwry = next(r for r in report.results if r.symbol == "FWRY")
    assert fwry.decision == ScannerDecision.CANDIDATE
    assert fwry.score >= 75
    assert len(fwry.reasons) > 0


def test_scanner_blocks_all_symbols_when_market_weak() -> None:
    snapshot = MarketSnapshot(
        symbols=[
            _make_snapshot("FWRY", change_percent=3.0, volume_ratio=1.5),
            _make_snapshot("HRHO", change_percent=2.0, volume_ratio=1.3),
        ],
        index_snapshots=[],
    )
    scanner = EgyptianMomentumScanner(_weak_mood())
    report = scanner.scan(snapshot)

    assert len(report.candidates) == 0
    assert len(report.watchlist) == 0
    assert len(report.blocked) == 2
    for result in report.results:
        assert result.decision == ScannerDecision.BLOCKED
        assert "Market mood is weak" in result.blockers


def test_scanner_sorts_candidates_before_watch_before_blocked() -> None:
    neutral_mood = MarketMoodResult(
        mood=MarketMood.NEUTRAL, score=55, reasons=[], blockers=[]
    )
    snapshot = MarketSnapshot(
        symbols=[
            _make_snapshot("LOW", change_percent=-2.0, volume_ratio=0.5, above_sma_5=False),
            _make_snapshot(
                "MID",
                change_percent=0.5,
                volume_ratio=1.0,
                broke_previous_high=False,
                above_sma_5=True,
            ),
            _make_snapshot("HIGH", change_percent=2.5, volume_ratio=1.5),
        ],
        index_snapshots=[],
    )
    scanner = EgyptianMomentumScanner(neutral_mood)
    report = scanner.scan(snapshot)

    decisions = [r.decision for r in report.results]
    candidate_idx = decisions.index(ScannerDecision.CANDIDATE)
    watch_idx = decisions.index(ScannerDecision.WATCH)
    blocked_idx = decisions.index(ScannerDecision.BLOCKED)
    assert candidate_idx < watch_idx < blocked_idx


def test_scanner_clamps_score_between_0_and_100() -> None:
    snapshot = MarketSnapshot(
        symbols=[_make_snapshot("MAX", change_percent=5.0, volume_ratio=2.0)],
        index_snapshots=[],
    )
    scanner = EgyptianMomentumScanner(_strong_mood())
    report = scanner.scan(snapshot)

    assert report.results[0].score == 100


def test_scanner_includes_reasons_and_blockers() -> None:
    snapshot = MarketSnapshot(
        symbols=[
            _make_snapshot("GOOD", change_percent=2.0, volume_ratio=1.3),
            _make_snapshot(
                "BAD", change_percent=-1.5, volume_ratio=0.5, above_sma_5=False
            ),
        ],
        index_snapshots=[],
    )
    scanner = EgyptianMomentumScanner(_strong_mood())
    report = scanner.scan(snapshot)

    good = next(r for r in report.results if r.symbol == "GOOD")
    bad = next(r for r in report.results if r.symbol == "BAD")

    assert "Positive price change" in good.reasons
    assert "Negative price change" in bad.blockers
    assert "Trading below SMA5" in bad.blockers


def test_scanner_with_sample_csv() -> None:
    provider = CsvMarketDataProvider(settings.EGX_DAILY_SAMPLE_PATH)
    market_snapshot = provider.build_market_snapshot(
        DEFAULT_WATCHLIST, MARKET_INDEX_SYMBOLS
    )
    mood_result = MarketMoodDetector().evaluate(market_snapshot.index_snapshots)
    scanner = EgyptianMomentumScanner(mood_result)
    report = scanner.scan(market_snapshot)

    assert len(report.results) == len(DEFAULT_WATCHLIST)
    assert report.market_mood == mood_result.mood.value
    if mood_result.mood != MarketMood.WEAK:
        assert len(report.candidates) + len(report.watchlist) + len(report.blocked) == len(
            DEFAULT_WATCHLIST
        )
