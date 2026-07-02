"""Scanner A — Egyptian momentum watchlist scanner."""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from core.market_data import MarketSnapshot, SymbolSnapshot
from core.market_mood import MarketMood, MarketMoodResult


class ScannerDecision(str, Enum):
    WATCH = "WATCH"
    CANDIDATE = "CANDIDATE"
    BLOCKED = "BLOCKED"


class ScannerResult(BaseModel):
    symbol: str
    decision: ScannerDecision
    score: int = Field(ge=0, le=100)
    latest_close: float
    change_percent: float
    volume_ratio: float
    broke_previous_high: bool
    above_sma_5: bool
    reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class ScannerReport(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    market_mood: str
    results: list[ScannerResult]
    candidates: list[ScannerResult]
    watchlist: list[ScannerResult]
    blocked: list[ScannerResult]


_DECISION_PRIORITY = {
    ScannerDecision.CANDIDATE: 0,
    ScannerDecision.WATCH: 1,
    ScannerDecision.BLOCKED: 2,
}


class EgyptianMomentumScanner:
    """Ranks watchlist symbols by momentum, volume, and breakout signals."""

    def __init__(self, market_mood_result: MarketMoodResult) -> None:
        self._mood_result = market_mood_result

    def _score_symbol(self, snap: SymbolSnapshot) -> ScannerResult:
        mood = self._mood_result.mood
        score = 50
        reasons: list[str] = []
        blockers: list[str] = []

        if snap.change_percent > 0:
            score += 10
            reasons.append("Positive price change")
            if snap.change_percent > 1.0:
                score += 10
                reasons.append("Strong price move above 1%")
        elif snap.change_percent < 0:
            score -= 15
            blockers.append("Negative price change")

        if snap.volume_ratio >= 1.2:
            score += 15
            reasons.append("Volume ratio is high")
        elif snap.volume_ratio < 0.8:
            score -= 10
            blockers.append("Weak volume ratio")

        if snap.broke_previous_high:
            score += 15
            reasons.append("Broke previous high")

        if snap.above_sma_5:
            score += 10
            reasons.append("Trading above SMA5")
        else:
            score -= 10
            blockers.append("Trading below SMA5")

        if mood == MarketMood.STRONG:
            score += 10
            reasons.append("Market mood is strong")
        elif mood == MarketMood.WEAK:
            score -= 25
            blockers.append("Market mood is weak")

        score = max(0, min(100, score))

        if mood == MarketMood.WEAK:
            decision = ScannerDecision.BLOCKED
        elif score >= 75:
            decision = ScannerDecision.CANDIDATE
        elif score >= 55:
            decision = ScannerDecision.WATCH
        else:
            decision = ScannerDecision.BLOCKED

        return ScannerResult(
            symbol=snap.symbol,
            decision=decision,
            score=score,
            latest_close=snap.latest_close,
            change_percent=snap.change_percent,
            volume_ratio=snap.volume_ratio,
            broke_previous_high=snap.broke_previous_high,
            above_sma_5=snap.above_sma_5,
            reasons=reasons,
            blockers=blockers,
        )

    def _sort_results(self, results: list[ScannerResult]) -> list[ScannerResult]:
        return sorted(
            results,
            key=lambda r: (
                _DECISION_PRIORITY[r.decision],
                -r.score,
                -r.volume_ratio,
            ),
        )

    def scan(self, snapshot: MarketSnapshot) -> ScannerReport:
        """Scan all symbols in the market snapshot and build a ranked report."""
        results = [self._score_symbol(snap) for snap in snapshot.symbols]
        sorted_results = self._sort_results(results)

        candidates = [r for r in sorted_results if r.decision == ScannerDecision.CANDIDATE]
        watchlist = [r for r in sorted_results if r.decision == ScannerDecision.WATCH]
        blocked = [r for r in sorted_results if r.decision == ScannerDecision.BLOCKED]

        return ScannerReport(
            market_mood=self._mood_result.mood.value,
            results=sorted_results,
            candidates=candidates,
            watchlist=watchlist,
            blocked=blocked,
        )
