"""Tests for sector momentum analysis."""

from __future__ import annotations

import pandas as pd

from core.scanner import ScannerDecision, ScannerResult
from core.sector_momentum import (
    UNKNOWN_SECTOR,
    SectorStatus,
    build_sector_momentum,
)


def _candidate(symbol: str) -> ScannerResult:
    return ScannerResult(
        symbol=symbol,
        decision=ScannerDecision.CANDIDATE,
        score=80,
        latest_close=10.0,
        change_percent=2.0,
        volume_ratio=1.8,
        broke_previous_high=True,
        above_sma_5=True,
        reasons=["test"],
        blockers=[],
    )


def test_sector_momentum_hot_when_many_advancers_and_high_avg_change() -> None:
    rows = [
        {
            "symbol": f"SYM{i}",
            "sector": "Real Estate",
            "change_percent": 2.0 + (i * 0.1),
            "volume": 1_000_000,
            "tv_relative_volume_10d": 1.8,
        }
        for i in range(12)
    ]
    rows.extend(
        [
            {
                "symbol": "FLAT1",
                "sector": "Real Estate",
                "change_percent": 0.0,
                "volume": 500_000,
                "tv_relative_volume_10d": 1.6,
            },
            {
                "symbol": "FLAT2",
                "sector": "Real Estate",
                "change_percent": 0.02,
                "volume": 500_000,
                "tv_relative_volume_10d": 1.6,
            },
            {
                "symbol": "DOWN1",
                "sector": "Real Estate",
                "change_percent": -0.5,
                "volume": 400_000,
                "tv_relative_volume_10d": 1.5,
            },
        ]
    )
    snapshot_df = pd.DataFrame(rows)
    candidates = [_candidate("SYM0"), _candidate("SYM1"), _candidate("SYM2")]

    result = build_sector_momentum(snapshot_df, candidates=candidates)
    assert result.sectors
    sector = result.sectors[0]
    assert sector.sector == "Real Estate"
    assert sector.status == SectorStatus.HOT
    assert sector.sector_score >= 75
    assert sector.advancers_count >= 10
    assert sector.candidates_count == 3


def test_missing_sector_becomes_unknown_and_does_not_crash() -> None:
    snapshot_df = pd.DataFrame(
        [
            {
                "symbol": "A",
                "change_percent": 1.0,
                "volume": 100_000,
            },
            {
                "symbol": "B",
                "sector": "",
                "change_percent": -1.0,
                "volume": 200_000,
            },
        ]
    )

    result = build_sector_momentum(snapshot_df)
    assert len(result.sectors) == 1
    assert result.sectors[0].sector == UNKNOWN_SECTOR
    assert result.sectors[0].symbols_count == 2
    assert result.symbol_status_by_symbol["A"] == result.sectors[0].status.value


def test_format_candidate_ranking_note_includes_sector_status() -> None:
    from core.candidate_ranking import (
        CandidateRankingConfig,
        format_candidate_ranking_note,
    )

    candidate = _candidate("SYM0")
    frame = pd.DataFrame(
        [
            {
                "symbol": "SYM0",
                "volume": 3_819_108,
                "volume_ratio": 2.1,
                "tv_relative_volume_10d": 2.1,
                "change_percent": 2.0,
                "market_cap": 1_000_000_000,
            }
        ]
    )

    note = format_candidate_ranking_note(
        candidate,
        frame,
        CandidateRankingConfig(),
        sector_status="HOT",
    )

    assert "sector HOT" in note
    assert note.index("rel vol") < note.index("sector HOT")
    assert note.index("sector HOT") < note.index("change quality")
