"""Tests for candidate tie-breaker ranking."""

from __future__ import annotations

import pandas as pd

from core.candidate_ranking import (
    CandidateRankingConfig,
    extreme_change_penalty,
    rank_candidates,
)
from core.scanner import ScannerDecision, ScannerResult


def _candidate(
    symbol: str,
    *,
    score: int = 95,
    change_percent: float = 3.0,
    volume_ratio: float = 2.0,
) -> ScannerResult:
    return ScannerResult(
        symbol=symbol,
        decision=ScannerDecision.CANDIDATE,
        score=score,
        latest_close=10.0,
        change_percent=change_percent,
        volume_ratio=volume_ratio,
        broke_previous_high=True,
        above_sma_5=True,
        reasons=["Positive price change"],
        blockers=[],
    )


def test_higher_score_ranks_above_lower_score() -> None:
    candidates = [
        _candidate("LOW", score=80),
        _candidate("HIGH", score=95),
    ]

    ranked = rank_candidates(candidates, pd.DataFrame(), CandidateRankingConfig())

    assert [item.symbol for item in ranked] == ["HIGH", "LOW"]


def test_same_score_prefers_clean_change_over_extreme_spike() -> None:
    candidates = [
        _candidate("SPIKE", change_percent=20.0, volume_ratio=3.0),
        _candidate("CLEAN", change_percent=3.0, volume_ratio=2.0),
    ]
    frame = pd.DataFrame(
        [
            {"symbol": "SPIKE", "volume": 100_000, "volume_ratio": 3.0},
            {"symbol": "CLEAN", "volume": 100_000, "volume_ratio": 2.0},
        ]
    )

    ranked = rank_candidates(candidates, frame, CandidateRankingConfig())

    assert ranked[0].symbol == "CLEAN"
    assert extreme_change_penalty(3.0, CandidateRankingConfig()) == 0
    assert extreme_change_penalty(20.0, CandidateRankingConfig()) == 2


def test_clean_change_beats_spike_even_with_higher_relative_volume() -> None:
    candidates = [
        _candidate("SPIKE", change_percent=20.0, volume_ratio=1.0),
        _candidate("CLEAN", change_percent=3.0, volume_ratio=1.0),
    ]
    frame = pd.DataFrame(
        [
            {
                "symbol": "SPIKE",
                "volume": 100_000,
                "volume_ratio": 1.0,
                "tv_relative_volume_10d": 5.0,
            },
            {
                "symbol": "CLEAN",
                "volume": 100_000,
                "volume_ratio": 1.0,
                "tv_relative_volume_10d": 1.8,
            },
        ]
    )

    ranked = rank_candidates(candidates, frame, CandidateRankingConfig())

    assert ranked[0].symbol == "CLEAN"


def test_same_score_prefers_higher_volume() -> None:
    candidates = [
        _candidate("LOWV", change_percent=4.0, volume_ratio=2.0),
        _candidate("HIGHV", change_percent=4.0, volume_ratio=2.0),
    ]
    frame = pd.DataFrame(
        [
            {"symbol": "LOWV", "volume": 50_000, "volume_ratio": 2.0},
            {"symbol": "HIGHV", "volume": 500_000, "volume_ratio": 2.0},
        ]
    )

    ranked = rank_candidates(candidates, frame, CandidateRankingConfig())

    assert ranked[0].symbol == "HIGHV"


def test_missing_fields_do_not_crash() -> None:
    candidates = [
        _candidate("A", change_percent=0.0, volume_ratio=0.0),
        _candidate("B", change_percent=0.0, volume_ratio=0.0),
    ]

    ranked = rank_candidates(candidates, None, CandidateRankingConfig())

    assert len(ranked) == 2
    assert ranked[0].symbol == "A"


def test_ranking_order_is_deterministic() -> None:
    candidates = [
        _candidate("BBB", change_percent=4.0, volume_ratio=2.0),
        _candidate("AAA", change_percent=4.0, volume_ratio=2.0),
    ]
    frame = pd.DataFrame(
        [
            {"symbol": "AAA", "volume": 100_000, "volume_ratio": 2.0},
            {"symbol": "BBB", "volume": 100_000, "volume_ratio": 2.0},
        ]
    )

    first = rank_candidates(candidates, frame, CandidateRankingConfig())
    second = rank_candidates(candidates, frame, CandidateRankingConfig())

    assert [item.symbol for item in first] == [item.symbol for item in second] == ["AAA", "BBB"]
