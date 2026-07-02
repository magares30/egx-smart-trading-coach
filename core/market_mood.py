"""Market mood detection from index snapshots."""

from enum import Enum

from pydantic import BaseModel, Field

from core.market_data import SymbolSnapshot


class MarketMood(str, Enum):
    STRONG = "STRONG"
    NEUTRAL = "NEUTRAL"
    WEAK = "WEAK"


class MarketMoodResult(BaseModel):
    mood: MarketMood
    score: int = Field(ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class MarketMoodDetector:
    """Evaluates overall EGX market mood from index symbol snapshots."""

    STRONG_THRESHOLD = 70
    WEAK_THRESHOLD = 40

    def evaluate(self, index_snapshots: list[SymbolSnapshot]) -> MarketMoodResult:
        """Score index snapshots and return a market mood result."""
        score = 50
        reasons: list[str] = []
        blockers: list[str] = []

        for snapshot in index_snapshots:
            label = snapshot.symbol

            if snapshot.change_percent > 0.5:
                score += 15
                reasons.append(
                    f"{label} up {snapshot.change_percent:.2f}% — positive momentum"
                )
            elif snapshot.change_percent < -0.5:
                score -= 15
                blockers.append(
                    f"{label} down {abs(snapshot.change_percent):.2f}% — negative momentum"
                )

            if snapshot.above_sma_5:
                score += 10
                reasons.append(f"{label} trading above 5-day SMA")
            else:
                score -= 10
                blockers.append(f"{label} trading below 5-day SMA")

            if snapshot.volume_ratio > 1.2:
                score += 5
                reasons.append(f"{label} volume ratio {snapshot.volume_ratio:.2f} — elevated")

        score = max(0, min(100, score))

        if score >= self.STRONG_THRESHOLD:
            mood = MarketMood.STRONG
        elif score <= self.WEAK_THRESHOLD:
            mood = MarketMood.WEAK
        else:
            mood = MarketMood.NEUTRAL

        return MarketMoodResult(
            mood=mood,
            score=score,
            reasons=reasons,
            blockers=blockers,
        )
