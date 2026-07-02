"""Strategy Scanner B — entry, stop, and target signal generation."""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from core.market_data import MarketSnapshot, SymbolSnapshot
from core.models import SignalType, TradeSignal
from core.scanner import ScannerDecision, ScannerReport, ScannerResult


class StrategyDecision(str, Enum):
    BUY_SETUP = "BUY_SETUP"
    WATCH = "WATCH"
    BLOCKED = "BLOCKED"


class StrategyResult(BaseModel):
    symbol: str
    decision: StrategyDecision
    signal: TradeSignal | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward: float | None = None
    confidence_score: int = Field(ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class StrategyReport(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    strategy_name: str
    results: list[StrategyResult]
    buy_setups: list[StrategyResult]
    watch: list[StrategyResult]
    blocked: list[StrategyResult]


_DECISION_PRIORITY = {
    StrategyDecision.BUY_SETUP: 0,
    StrategyDecision.WATCH: 1,
    StrategyDecision.BLOCKED: 2,
}


class TrendJoinLongStrategy:
    """Egyptian Trend Join Long — converts scanner candidates into trade plans."""

    def __init__(self, risk_reward_target: float = 2.0) -> None:
        self._risk_reward_target = risk_reward_target

    def _snapshot_map(self, market_snapshot: MarketSnapshot) -> dict[str, SymbolSnapshot]:
        return {snap.symbol: snap for snap in market_snapshot.symbols}

    def _calculate_levels(
        self, snapshot: SymbolSnapshot
    ) -> tuple[float, float, float, float]:
        entry_price = snapshot.latest_close
        stop_loss = min(snapshot.day_low, snapshot.previous_close * 0.99)
        risk_per_share = entry_price - stop_loss
        take_profit = entry_price + (risk_per_share * self._risk_reward_target)
        risk_reward = (
            (take_profit - entry_price) / risk_per_share if risk_per_share > 0 else 0.0
        )
        return entry_price, stop_loss, take_profit, risk_reward

    def _calculate_confidence(
        self, scanner_result: ScannerResult, snapshot: SymbolSnapshot
    ) -> int:
        score = scanner_result.score
        if snapshot.broke_previous_high:
            score += 5
        if snapshot.volume_ratio >= 1.3:
            score += 5
        if snapshot.change_percent >= 2.0:
            score += 5
        if snapshot.volume_ratio < 1.1:
            score -= 10
        if not snapshot.broke_previous_high and snapshot.change_percent < 1.0:
            score -= 10
        return max(0, min(100, score))

    def _has_serious_blockers(
        self,
        snapshot: SymbolSnapshot,
        stop_loss: float,
        entry_price: float,
        blockers: list[str],
    ) -> bool:
        if snapshot.latest_close <= snapshot.previous_close:
            blockers.append("Price is not above previous close")
        if snapshot.volume_ratio < 0.8:
            blockers.append("Volume confirmation is weak")
        if not snapshot.above_sma_5:
            blockers.append("Not trading above SMA5")
        if stop_loss >= entry_price:
            blockers.append("Invalid stop loss")
        return len(blockers) > 0

    def _buy_setup_conditions_met(
        self,
        snapshot: SymbolSnapshot,
        stop_loss: float,
        entry_price: float,
        risk_reward: float,
        reasons: list[str],
    ) -> bool:
        if snapshot.latest_close <= snapshot.previous_close:
            return False
        reasons.append("Price is above previous close")

        if not (snapshot.broke_previous_high or snapshot.change_percent >= 1.0):
            return False
        if snapshot.broke_previous_high:
            reasons.append("Broke previous high")
        if snapshot.change_percent >= 1.0:
            reasons.append("Change percent is strong")

        if snapshot.volume_ratio < 1.1:
            return False
        if snapshot.insufficient_volume_history:
            return False
        reasons.append("Volume confirms move")

        if not snapshot.above_sma_5:
            return False
        reasons.append("Trading above SMA5")

        if stop_loss >= entry_price:
            return False

        if risk_reward < self._risk_reward_target:
            return False
        reasons.append(
            f"Generated 1:{self._risk_reward_target:.0f} risk/reward plan"
        )
        return True

    def _missing_buy_condition_blockers(
        self,
        snapshot: SymbolSnapshot,
        stop_loss: float,
        entry_price: float,
        risk_reward: float,
        blockers: list[str],
    ) -> None:
        if snapshot.latest_close <= snapshot.previous_close:
            blockers.append("Price is not above previous close")
        if snapshot.volume_ratio < 1.1:
            blockers.append("Volume confirmation is weak")
        if snapshot.insufficient_volume_history:
            blockers.append("Insufficient volume history for live confirmation")
        if not snapshot.above_sma_5:
            blockers.append("Not trading above SMA5")
        if not (snapshot.broke_previous_high or snapshot.change_percent >= 1.0):
            blockers.append("No breakout or strong move")
        if stop_loss >= entry_price:
            blockers.append("Invalid stop loss")
        if risk_reward < self._risk_reward_target:
            blockers.append(
                f"Risk/reward below 1:{self._risk_reward_target:.0f}"
            )

    def _build_signal(
        self,
        symbol: str,
        decision: StrategyDecision,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        confidence: int,
        reasons: list[str],
        blockers: list[str],
    ) -> TradeSignal | None:
        if decision == StrategyDecision.BLOCKED:
            return None

        if decision == StrategyDecision.BUY_SETUP:
            return TradeSignal(
                symbol=symbol,
                signal_type=SignalType.BUY_SETUP,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence_score=confidence,
                reasons=reasons,
                blockers=[],
            )

        if stop_loss >= entry_price:
            return None

        return TradeSignal(
            symbol=symbol,
            signal_type=SignalType.WATCH,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence_score=confidence,
            reasons=reasons,
            blockers=blockers,
        )

    def _evaluate_candidate(
        self, scanner_result: ScannerResult, snapshot: SymbolSnapshot
    ) -> StrategyResult:
        reasons: list[str] = ["Scanner marked symbol as candidate"]
        blockers: list[str] = []

        entry_price, stop_loss, take_profit, risk_reward = self._calculate_levels(
            snapshot
        )
        confidence = self._calculate_confidence(scanner_result, snapshot)

        serious_blockers: list[str] = []
        if self._has_serious_blockers(
            snapshot, stop_loss, entry_price, serious_blockers
        ):
            return StrategyResult(
                symbol=scanner_result.symbol,
                decision=StrategyDecision.BLOCKED,
                signal=None,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_reward=risk_reward,
                confidence_score=confidence,
                reasons=reasons,
                blockers=serious_blockers,
            )

        buy_reasons = list(reasons)
        if self._buy_setup_conditions_met(
            snapshot, stop_loss, entry_price, risk_reward, buy_reasons
        ):
            signal = self._build_signal(
                scanner_result.symbol,
                StrategyDecision.BUY_SETUP,
                entry_price,
                stop_loss,
                take_profit,
                confidence,
                buy_reasons,
                [],
            )
            return StrategyResult(
                symbol=scanner_result.symbol,
                decision=StrategyDecision.BUY_SETUP,
                signal=signal,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_reward=risk_reward,
                confidence_score=confidence,
                reasons=buy_reasons,
                blockers=[],
            )

        watch_blockers: list[str] = []
        self._missing_buy_condition_blockers(
            snapshot, stop_loss, entry_price, risk_reward, watch_blockers
        )
        signal = self._build_signal(
            scanner_result.symbol,
            StrategyDecision.WATCH,
            entry_price,
            stop_loss,
            take_profit,
            confidence,
            reasons,
            watch_blockers,
        )
        return StrategyResult(
            symbol=scanner_result.symbol,
            decision=StrategyDecision.WATCH,
            signal=signal,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=risk_reward,
            confidence_score=confidence,
            reasons=reasons,
            blockers=watch_blockers,
        )

    def _evaluate_watch(
        self, scanner_result: ScannerResult, snapshot: SymbolSnapshot
    ) -> StrategyResult:
        reasons: list[str] = []
        blockers: list[str] = []

        entry_price, stop_loss, take_profit, risk_reward = self._calculate_levels(
            snapshot
        )
        confidence = self._calculate_confidence(scanner_result, snapshot)

        serious_blockers: list[str] = []
        if self._has_serious_blockers(
            snapshot, stop_loss, entry_price, serious_blockers
        ):
            return StrategyResult(
                symbol=scanner_result.symbol,
                decision=StrategyDecision.BLOCKED,
                signal=None,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_reward=risk_reward,
                confidence_score=confidence,
                reasons=reasons,
                blockers=serious_blockers,
            )

        signal = self._build_signal(
            scanner_result.symbol,
            StrategyDecision.WATCH,
            entry_price,
            stop_loss,
            take_profit,
            confidence,
            reasons,
            blockers,
        )
        return StrategyResult(
            symbol=scanner_result.symbol,
            decision=StrategyDecision.WATCH,
            signal=signal,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=risk_reward,
            confidence_score=confidence,
            reasons=reasons,
            blockers=blockers,
        )

    def _evaluate_blocked(self, scanner_result: ScannerResult) -> StrategyResult:
        blockers = ["Scanner blocked this symbol", *scanner_result.blockers]
        return StrategyResult(
            symbol=scanner_result.symbol,
            decision=StrategyDecision.BLOCKED,
            signal=None,
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            risk_reward=None,
            confidence_score=scanner_result.score,
            reasons=[],
            blockers=blockers,
        )

    def _evaluate_symbol(
        self,
        scanner_result: ScannerResult,
        snapshot: SymbolSnapshot | None,
    ) -> StrategyResult:
        if scanner_result.decision == ScannerDecision.BLOCKED:
            return self._evaluate_blocked(scanner_result)

        if snapshot is None:
            return StrategyResult(
                symbol=scanner_result.symbol,
                decision=StrategyDecision.BLOCKED,
                signal=None,
                confidence_score=scanner_result.score,
                reasons=[],
                blockers=["Symbol snapshot not found"],
            )

        if scanner_result.decision == ScannerDecision.CANDIDATE:
            return self._evaluate_candidate(scanner_result, snapshot)

        return self._evaluate_watch(scanner_result, snapshot)

    def _sort_results(self, results: list[StrategyResult]) -> list[StrategyResult]:
        return sorted(
            results,
            key=lambda r: (
                _DECISION_PRIORITY[r.decision],
                -r.confidence_score,
                r.symbol,
            ),
        )

    def generate_signals(
        self,
        scanner_report: ScannerReport,
        market_snapshot: MarketSnapshot,
    ) -> StrategyReport:
        """Convert scanner results into strategy trade plans."""
        snapshots = self._snapshot_map(market_snapshot)
        results = [
            self._evaluate_symbol(item, snapshots.get(item.symbol))
            for item in scanner_report.results
        ]
        sorted_results = self._sort_results(results)

        buy_setups = [r for r in sorted_results if r.decision == StrategyDecision.BUY_SETUP]
        watch = [r for r in sorted_results if r.decision == StrategyDecision.WATCH]
        blocked = [r for r in sorted_results if r.decision == StrategyDecision.BLOCKED]

        return StrategyReport(
            strategy_name="Trend Join Long",
            results=sorted_results,
            buy_setups=buy_setups,
            watch=watch,
            blocked=blocked,
        )
