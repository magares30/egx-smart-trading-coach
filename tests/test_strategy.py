"""Tests for Strategy Scanner B — Trend Join Long."""

import pytest

from core.market_data import MarketSnapshot, SymbolSnapshot
from core.models import SignalType
from core.scanner import ScannerDecision, ScannerReport, ScannerResult
from core.strategy import StrategyDecision, TrendJoinLongStrategy


def _snapshot(
    symbol: str,
    latest_close: float,
    previous_close: float,
    day_low: float,
    change_percent: float = 2.0,
    volume_ratio: float = 1.3,
    broke_previous_high: bool = True,
    above_sma_5: bool = True,
) -> SymbolSnapshot:
    return SymbolSnapshot(
        symbol=symbol,
        latest_close=latest_close,
        previous_close=previous_close,
        change=latest_close - previous_close,
        change_percent=change_percent,
        latest_volume=1_000_000,
        average_volume_5d=800_000.0,
        volume_ratio=volume_ratio,
        day_high=latest_close + 1.0,
        day_low=day_low,
        broke_previous_high=broke_previous_high,
        above_sma_5=above_sma_5,
        above_sma_20=None,
        insufficient_volume_history=False,
    )


def _scanner_result(
    symbol: str,
    decision: ScannerDecision = ScannerDecision.CANDIDATE,
    score: int = 80,
    change_percent: float = 2.0,
    volume_ratio: float = 1.3,
    broke_previous_high: bool = True,
    above_sma_5: bool = True,
    latest_close: float = 100.0,
) -> ScannerResult:
    return ScannerResult(
        symbol=symbol,
        decision=decision,
        score=score,
        latest_close=latest_close,
        change_percent=change_percent,
        volume_ratio=volume_ratio,
        broke_previous_high=broke_previous_high,
        above_sma_5=above_sma_5,
    )


def _run_strategy(
    scanner_result: ScannerResult,
    symbol_snapshot: SymbolSnapshot,
) -> "StrategyResult":
    scanner_report = ScannerReport(
        market_mood="STRONG",
        results=[scanner_result],
        candidates=[scanner_result]
        if scanner_result.decision == ScannerDecision.CANDIDATE
        else [],
        watchlist=[scanner_result]
        if scanner_result.decision == ScannerDecision.WATCH
        else [],
        blocked=[scanner_result]
        if scanner_result.decision == ScannerDecision.BLOCKED
        else [],
    )
    market_snapshot = MarketSnapshot(symbols=[symbol_snapshot], index_snapshots=[])
    return TrendJoinLongStrategy().generate_signals(scanner_report, market_snapshot).results[0]


def test_candidate_becomes_buy_setup() -> None:
    snap = _snapshot("FWRY", latest_close=100.0, previous_close=98.0, day_low=97.0)
    result = _scanner_result("FWRY", latest_close=100.0)
    strategy_result = _run_strategy(result, snap)

    assert strategy_result.decision == StrategyDecision.BUY_SETUP
    assert strategy_result.signal is not None
    assert strategy_result.signal.signal_type == SignalType.BUY_SETUP
    assert strategy_result.stop_loss < strategy_result.entry_price
    assert strategy_result.take_profit > strategy_result.entry_price
    assert strategy_result.risk_reward is not None
    assert strategy_result.risk_reward >= 2.0


def test_weak_volume_becomes_watch_not_buy_setup() -> None:
    snap = _snapshot(
        "COMI",
        latest_close=100.0,
        previous_close=99.0,
        day_low=97.0,
        volume_ratio=0.9,
    )
    result = _scanner_result("COMI", volume_ratio=0.9, latest_close=100.0)
    strategy_result = _run_strategy(result, snap)

    assert strategy_result.decision != StrategyDecision.BUY_SETUP
    assert "Volume confirmation is weak" in strategy_result.blockers


def test_serious_weak_volume_blocks() -> None:
    snap = _snapshot(
        "SWDY",
        latest_close=100.0,
        previous_close=99.0,
        day_low=97.0,
        volume_ratio=0.6,
    )
    result = _scanner_result("SWDY", volume_ratio=0.6, latest_close=100.0)
    strategy_result = _run_strategy(result, snap)

    assert strategy_result.decision == StrategyDecision.BLOCKED
    assert strategy_result.signal is None
    assert "Volume confirmation is weak" in strategy_result.blockers


def test_scanner_blocked_remains_blocked() -> None:
    result = _scanner_result("EFIH", decision=ScannerDecision.BLOCKED, score=30)
    snap = _snapshot("EFIH", latest_close=19.0, previous_close=19.5, day_low=18.5)
    strategy_result = _run_strategy(result, snap)

    assert strategy_result.decision == StrategyDecision.BLOCKED
    assert strategy_result.signal is None
    assert "Scanner blocked this symbol" in strategy_result.blockers


def test_invalid_stop_loss_blocks() -> None:
    # day_low and previous_close*0.99 both >= entry -> invalid stop
    snap = _snapshot(
        "ORAS",
        latest_close=100.0,
        previous_close=102.0,
        day_low=100.0,
    )
    result = _scanner_result("ORAS", latest_close=100.0)
    strategy_result = _run_strategy(result, snap)

    assert strategy_result.decision == StrategyDecision.BLOCKED
    assert strategy_result.signal is None
    assert "Invalid stop loss" in strategy_result.blockers


def test_confidence_clamped_between_0_and_100() -> None:
    snap = _snapshot("FWRY", latest_close=100.0, previous_close=95.0, day_low=94.0)
    high_score = _scanner_result("FWRY", score=98, change_percent=3.0, volume_ratio=1.5)
    strategy_result = _run_strategy(high_score, snap)
    assert 0 <= strategy_result.confidence_score <= 100

    low_snap = _snapshot(
        "BAD",
        latest_close=100.0,
        previous_close=99.5,
        day_low=97.0,
        change_percent=0.3,
        volume_ratio=0.9,
        broke_previous_high=False,
    )
    low_result = _scanner_result(
        "BAD",
        score=5,
        change_percent=0.3,
        volume_ratio=0.9,
        broke_previous_high=False,
        latest_close=100.0,
    )
    low_strategy = _run_strategy(low_result, low_snap)
    assert 0 <= low_strategy.confidence_score <= 100


def test_insufficient_volume_history_blocks_buy_setup() -> None:
    snap = SymbolSnapshot(
        symbol="FWRY",
        latest_close=100.0,
        previous_close=95.0,
        change=5.0,
        change_percent=5.0,
        latest_volume=1_000_000,
        average_volume_5d=800_000.0,
        volume_ratio=1.2,
        day_high=101.0,
        day_low=94.0,
        broke_previous_high=True,
        above_sma_5=True,
        above_sma_20=None,
        insufficient_volume_history=True,
    )
    scanner_result = _scanner_result("FWRY", score=85, change_percent=2.0, volume_ratio=1.2)
    strategy_result = _run_strategy(scanner_result, snap)

    assert strategy_result.decision == StrategyDecision.WATCH
    assert any("volume history" in blocker.lower() for blocker in strategy_result.blockers)
