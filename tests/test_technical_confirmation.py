"""Tests for TradingView technical confirmation scoring."""

from __future__ import annotations

import pandas as pd

from core.candidate_ranking import CandidateRankingConfig, rank_candidates
from core.scanner import ScannerDecision, ScannerResult
from core.technical_confirmation import (
    TechnicalConfirmationConfig,
    TechnicalStatus,
    evaluate_technical_confirmation,
    format_technical_confirmation_line,
)


def _candidate(symbol: str, *, score: int = 95) -> ScannerResult:
    return ScannerResult(
        symbol=symbol,
        decision=ScannerDecision.CANDIDATE,
        score=score,
        latest_close=10.0,
        change_percent=3.0,
        volume_ratio=2.0,
        broke_previous_high=True,
        above_sma_5=True,
        reasons=["Positive price change"],
        blockers=[],
    )


def test_missing_fields_return_unknown_without_crashing() -> None:
    result = evaluate_technical_confirmation(
        {"symbol": "COMI", "close": 10.0},
        TechnicalConfirmationConfig(),
    )

    assert result.status == TechnicalStatus.UNKNOWN
    assert result.technical_score == 0
    assert "Technical fields unavailable" in result.notes
    assert "UNKNOWN" in format_technical_confirmation_line(result)


def test_healthy_technical_row_scores_positive() -> None:
    row = {
        "symbol": "COMI",
        "close": 110.0,
        "rsi": 58.0,
        "ema20": 100.0,
        "ema50": 95.0,
        "macd": 1.5,
        "macd_signal": 1.0,
        "adx": 24.0,
        "tv_recommend_all": 0.5,
    }

    result = evaluate_technical_confirmation(row, TechnicalConfirmationConfig())

    assert result.technical_score > 0
    assert result.status in {TechnicalStatus.OK, TechnicalStatus.STRONG}
    line = format_technical_confirmation_line(result, row)
    assert "Technical: OK" in line or "Technical: STRONG" in line
    assert "RSI 58" in line
    assert "MACD positive" in line
    assert "Above EMA20" in line


def test_rsi_above_caution_threshold_produces_caution_note() -> None:
    row = {
        "symbol": "SPIKE",
        "close": 110.0,
        "rsi": 80.0,
        "ema20": 100.0,
        "macd": 1.5,
        "macd_signal": 1.0,
        "adx": 24.0,
    }

    result = evaluate_technical_confirmation(row, TechnicalConfirmationConfig())

    assert any("overbought caution" in note for note in result.notes)
    assert result.technical_score < 10


def test_technical_score_affects_ranking_within_same_scanner_score() -> None:
    candidates = [
        _candidate("WEAKTECH", score=95),
        _candidate("STRONGTECH", score=95),
    ]
    frame = pd.DataFrame(
        [
            {
                "symbol": "WEAKTECH",
                "close": 10.0,
                "volume": 100_000,
                "volume_ratio": 2.0,
                "rsi": 80.0,
                "ema20": 12.0,
                "macd": 0.5,
                "macd_signal": 1.0,
                "adx": 10.0,
            },
            {
                "symbol": "STRONGTECH",
                "close": 110.0,
                "volume": 100_000,
                "volume_ratio": 2.0,
                "rsi": 58.0,
                "ema20": 100.0,
                "macd": 1.5,
                "macd_signal": 1.0,
                "adx": 24.0,
                "tv_recommend_all": 0.5,
            },
        ]
    )

    ranked = rank_candidates(
        candidates,
        frame,
        CandidateRankingConfig(),
        technical_config=TechnicalConfirmationConfig(),
    )

    assert ranked[0].symbol == "STRONGTECH"
