"""Tests for TradingView relative volume classification."""

from __future__ import annotations

import pandas as pd

from core.candidate_ranking import CandidateRankingConfig, rank_candidates
from core.candidate_filters import CandidateFilters, filter_candidates_for_strategy
from core.relative_volume import (
    RelativeVolumeStatus,
    classify_relative_volume,
    format_relative_volume_display,
    resolve_volume_ratio,
)
from core.scanner import ScannerDecision, ScannerReport, ScannerResult
from core.technical_confirmation import TechnicalConfirmationConfig


def _candidate(symbol: str, *, score: int = 95, volume_ratio: float = 1.0) -> ScannerResult:
    return ScannerResult(
        symbol=symbol,
        decision=ScannerDecision.CANDIDATE,
        score=score,
        latest_close=10.0,
        change_percent=3.0,
        volume_ratio=volume_ratio,
        broke_previous_high=True,
        above_sma_5=True,
        reasons=["Positive price change"],
        blockers=[],
    )


def test_classify_relative_volume_buckets() -> None:
    assert classify_relative_volume(None).status == RelativeVolumeStatus.UNKNOWN
    assert classify_relative_volume(0.5).status == RelativeVolumeStatus.LOW
    assert classify_relative_volume(1.0).status == RelativeVolumeStatus.NORMAL
    assert classify_relative_volume(1.8).status == RelativeVolumeStatus.HIGH
    assert classify_relative_volume(3.2).status == RelativeVolumeStatus.VERY_HIGH


def test_invalid_relative_volume_does_not_crash() -> None:
    result = classify_relative_volume(-1.0)
    assert result.status == RelativeVolumeStatus.UNKNOWN
    assert result.score_bonus == 0
    assert result.note == "rel vol unknown"


def test_resolve_volume_ratio_prefers_tradingview_column() -> None:
    resolved = resolve_volume_ratio(
        1.0,
        {"tv_relative_volume_10d": 1.8, "volume_ratio": 1.0},
    )
    assert resolved == 1.8


def test_high_relative_volume_ranks_above_normal_with_same_scores() -> None:
    candidates = [
        _candidate("NORMAL", volume_ratio=1.0),
        _candidate("HIGH", volume_ratio=1.0),
    ]
    frame = pd.DataFrame(
        [
            {
                "symbol": "NORMAL",
                "volume": 100_000,
                "volume_ratio": 1.0,
                "tv_relative_volume_10d": 1.2,
            },
            {
                "symbol": "HIGH",
                "volume": 100_000,
                "volume_ratio": 1.0,
                "tv_relative_volume_10d": 1.8,
            },
        ]
    )

    ranked = rank_candidates(
        candidates,
        frame,
        CandidateRankingConfig(),
        technical_config=TechnicalConfirmationConfig(enabled=False),
    )

    assert ranked[0].symbol == "HIGH"


def test_min_volume_ratio_filter_uses_tradingview_relative_volume() -> None:
    candidates = [
        _candidate("PASS", volume_ratio=1.0),
        _candidate("FAIL", volume_ratio=1.0),
    ]
    frame = pd.DataFrame(
        [
            {"symbol": "PASS", "tv_relative_volume_10d": 1.8, "volume_ratio": 1.8},
            {"symbol": "FAIL", "tv_relative_volume_10d": 1.1, "volume_ratio": 1.1},
        ]
    )
    filters = CandidateFilters(min_volume_ratio=1.5)

    filtered = filter_candidates_for_strategy(candidates, filters, frame)

    assert [item.symbol for item in filtered] == ["PASS"]


def test_format_candidate_ranking_note_includes_relative_volume_label() -> None:
    from core.candidate_ranking import format_candidate_ranking_note

    candidate = _candidate("COMI", volume_ratio=1.0)
    frame = pd.DataFrame(
        [
            {
                "symbol": "COMI",
                "volume": 4_524_224,
                "volume_ratio": 1.8,
                "tv_relative_volume_10d": 1.8,
            }
        ]
    )

    note = format_candidate_ranking_note(candidate, frame, CandidateRankingConfig())

    assert "rel vol HIGH 1.8x" in note
    assert "volume 4,524,224" in note


def test_format_relative_volume_display_uses_one_decimal() -> None:
    assert format_relative_volume_display(1.8) == "1.8x"
    assert format_relative_volume_display(3.25) == "3.2x"


def test_ranked_strategy_signals_retained_with_tradingview_relative_volume() -> None:
    from core.candidate_filters import ranked_strategy_signals_for_display
    from core.strategy import StrategyDecision, StrategyReport, StrategyResult

    candidates = [_candidate("COMI", volume_ratio=1.0)]
    scanner_report = ScannerReport(
        market_mood="NEUTRAL",
        results=candidates,
        candidates=candidates,
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[
            StrategyResult(
                symbol="COMI",
                decision=StrategyDecision.BUY_SETUP,
                entry_price=10.0,
                stop_loss=9.5,
                take_profit=11.0,
                risk_reward=2.0,
                confidence_score=85,
                reasons=["Scanner marked symbol as candidate"],
                blockers=[],
            )
        ],
        buy_setups=[
            StrategyResult(
                symbol="COMI",
                decision=StrategyDecision.BUY_SETUP,
                entry_price=10.0,
                stop_loss=9.5,
                take_profit=11.0,
                risk_reward=2.0,
                confidence_score=85,
                reasons=["Scanner marked symbol as candidate"],
                blockers=[],
            )
        ],
        watch=[],
        blocked=[],
    )
    frame = pd.DataFrame(
        [{"symbol": "COMI", "tv_relative_volume_10d": 2.1, "volume_ratio": 2.1}]
    )
    filters = CandidateFilters(min_volume_ratio=1.5)

    items = ranked_strategy_signals_for_display(
        strategy_report,
        scanner_report,
        filters,
        frame,
    )

    assert [item.symbol for item in items] == ["COMI"]


def test_display_volume_ratio_prefers_tradingview_relative_volume() -> None:
    from core.candidate_ranking import display_volume_ratio_for_candidate

    candidate = _candidate("COMI", volume_ratio=1.0)
    frame = pd.DataFrame(
        [{"symbol": "COMI", "tv_relative_volume_10d": 2.1, "volume_ratio": 1.0}]
    )

    assert display_volume_ratio_for_candidate(candidate, frame) == 2.1


def test_technical_line_is_separate_from_rank_factors_line() -> None:
    from core.daily_report import DailyReportBuilder

    builder = DailyReportBuilder()
    lines = builder._format_scanner_item(
        1,
        _candidate("COMI"),
        ranking_note="Rank factors: volume 100, rel vol HIGH 2.1x, change quality clean, market cap available",
        technical_note="Technical: STRONG (+12) | RSI 58",
        display_volume_ratio=2.1,
    )

    assert len(lines) >= 4
    assert lines[2].strip().startswith("Rank factors:")
    assert lines[3].strip().startswith("Technical:")
    assert "market cap available" in lines[2]
    assert "Technical:" not in lines[2]
