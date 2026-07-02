"""Conservative buy/sell decision labels for daily report signals and positions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.market_hours import EgxMarketSession
from core.multi_timeframe import EntryTimingStatus
from core.strategy import StrategyDecision, StrategyResult

DECISION_SUMMARY_NOTE = "Paper trading only; no real execution"
REVIEW_TIMING_NOW = "NOW"
REVIEW_TIMING_NEXT_OPEN_SESSION = "NEXT_OPEN_SESSION"
REVIEW_TIMING_NEXT_TRADING_SESSION = "NEXT_TRADING_SESSION"


class DecisionLabel(str, Enum):
    WATCH_NEXT_SESSION = "WATCH_NEXT_SESSION"
    BUY_SETUP = "BUY_SETUP"
    WATCH = "WATCH"
    HOLD = "HOLD"
    SELL_ALERT_TARGET = "SELL_ALERT_TARGET"
    SELL_ALERT_STOP = "SELL_ALERT_STOP"
    NO_ACTION = "NO_ACTION"


@dataclass(frozen=True)
class SignalDecision:
    symbol: str
    label: DecisionLabel
    explanation: str
    strategy_decision: str

    def to_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "decision": self.label.value,
            "explanation": self.explanation,
            "strategy_decision": self.strategy_decision,
        }


@dataclass(frozen=True)
class PositionDecision:
    symbol: str
    label: DecisionLabel
    explanation: str
    executable_now: bool = False
    review_timing: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "symbol": self.symbol,
            "decision": self.label.value,
            "decision_explanation": self.explanation,
            "executable_now": self.executable_now,
        }
        if self.review_timing is not None:
            payload["review_timing"] = self.review_timing
        return payload


@dataclass(frozen=True)
class DecisionSummary:
    buy_setups: list[str]
    watch_next_session: list[str]
    hold_positions: list[str]
    sell_alerts: list[str]
    no_action: list[str]
    signals: list[dict[str, str]]
    positions: list[dict[str, object]]
    note: str = DECISION_SUMMARY_NOTE

    def to_dict(self) -> dict[str, object]:
        return {
            "buy_setups": list(self.buy_setups),
            "watch_next_session": list(self.watch_next_session),
            "hold_positions": list(self.hold_positions),
            "sell_alerts": list(self.sell_alerts),
            "no_action": list(self.no_action),
            "signals": list(self.signals),
            "positions": list(self.positions),
            "note": self.note,
        }


def _has_trade_levels(item: StrategyResult) -> bool:
    return (
        item.entry_price is not None
        and item.stop_loss is not None
        and item.take_profit is not None
    )


def _position_review_timing(session: EgxMarketSession) -> str:
    if session.is_open_for_new_entries:
        return REVIEW_TIMING_NOW
    if session.is_trading_day:
        return REVIEW_TIMING_NEXT_OPEN_SESSION
    return REVIEW_TIMING_NEXT_TRADING_SESSION


def _sell_alert_explanation(
    label: DecisionLabel,
    *,
    review_timing: str,
) -> str:
    if label == DecisionLabel.SELL_ALERT_TARGET:
        if review_timing == REVIEW_TIMING_NOW:
            return (
                "Price reached or crossed target; review exit during open market"
            )
        if review_timing == REVIEW_TIMING_NEXT_OPEN_SESSION:
            return (
                "Target reached; market closed, review selling at next open session"
            )
        return (
            "Target reached; EGX is closed today, review selling next trading session"
        )

    if review_timing == REVIEW_TIMING_NOW:
        return (
            "Price reached or crossed stop loss; review risk exit during open market"
        )
    if review_timing == REVIEW_TIMING_NEXT_OPEN_SESSION:
        return (
            "Stop reached; market closed, review risk exit at next open session"
        )
    return (
        "Stop reached; EGX is closed today, review risk exit next trading session"
    )


def classify_strategy_signal_decision(
    item: StrategyResult,
    *,
    session: EgxMarketSession,
    entry_timing_status: str | None = None,
) -> SignalDecision:
    """Assign a conservative decision label to one strategy signal."""
    strategy_decision = item.decision.value

    if session.guard_enabled and not session.is_open_for_new_entries:
        if item.decision in (StrategyDecision.BUY_SETUP, StrategyDecision.WATCH):
            return SignalDecision(
                symbol=item.symbol,
                label=DecisionLabel.WATCH_NEXT_SESSION,
                explanation="market closed; review next session only",
                strategy_decision=strategy_decision,
            )
        return SignalDecision(
            symbol=item.symbol,
            label=DecisionLabel.NO_ACTION,
            explanation="no usable setup",
            strategy_decision=strategy_decision,
        )

    if (
        item.decision == StrategyDecision.BUY_SETUP
        and _has_trade_levels(item)
    ):
        weak_timing = entry_timing_status in {
            EntryTimingStatus.WATCH.value,
            EntryTimingStatus.WAIT.value,
            EntryTimingStatus.AVOID.value,
        }
        if weak_timing:
            return SignalDecision(
                symbol=item.symbol,
                label=DecisionLabel.WATCH,
                explanation="setup needs confirmation",
                strategy_decision=strategy_decision,
            )
        return SignalDecision(
            symbol=item.symbol,
            label=DecisionLabel.BUY_SETUP,
            explanation="valid only during open market using listed entry/stop/target",
            strategy_decision=strategy_decision,
        )

    if item.decision in (StrategyDecision.WATCH, StrategyDecision.BUY_SETUP):
        return SignalDecision(
            symbol=item.symbol,
            label=DecisionLabel.WATCH,
            explanation="setup needs confirmation",
            strategy_decision=strategy_decision,
        )

    return SignalDecision(
        symbol=item.symbol,
        label=DecisionLabel.NO_ACTION,
        explanation="no usable setup",
        strategy_decision=strategy_decision,
    )


def classify_position_decision(
    *,
    current_price: float | None,
    stop_loss: float | None,
    take_profit: float | None,
    session: EgxMarketSession,
) -> PositionDecision:
    """Assign a conservative alert label to one open paper position."""
    symbol_placeholder = ""
    review_timing = _position_review_timing(session)

    if current_price is None or stop_loss is None or take_profit is None:
        return PositionDecision(
            symbol=symbol_placeholder,
            label=DecisionLabel.HOLD,
            explanation="insufficient exit data; monitor manually",
            executable_now=False,
            review_timing=review_timing,
        )

    if current_price <= stop_loss:
        return PositionDecision(
            symbol=symbol_placeholder,
            label=DecisionLabel.SELL_ALERT_STOP,
            explanation=_sell_alert_explanation(
                DecisionLabel.SELL_ALERT_STOP,
                review_timing=review_timing,
            ),
            executable_now=review_timing == REVIEW_TIMING_NOW,
            review_timing=review_timing,
        )

    if current_price >= take_profit:
        return PositionDecision(
            symbol=symbol_placeholder,
            label=DecisionLabel.SELL_ALERT_TARGET,
            explanation=_sell_alert_explanation(
                DecisionLabel.SELL_ALERT_TARGET,
                review_timing=review_timing,
            ),
            executable_now=review_timing == REVIEW_TIMING_NOW,
            review_timing=review_timing,
        )

    return PositionDecision(
        symbol=symbol_placeholder,
        label=DecisionLabel.HOLD,
        explanation="position is between stop and target",
        executable_now=False,
        review_timing=review_timing,
    )


def classify_open_position_decision(
    *,
    symbol: str,
    current_price: float | None,
    stop_loss: float,
    take_profit: float,
    session: EgxMarketSession,
) -> PositionDecision:
    """Assign a position decision label with symbol set."""
    base = classify_position_decision(
        current_price=current_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        session=session,
    )
    return PositionDecision(
        symbol=symbol,
        label=base.label,
        explanation=base.explanation,
        executable_now=base.executable_now,
        review_timing=base.review_timing,
    )


def format_strategy_decision_line(decision: SignalDecision) -> str:
    """Render a compact decision note under a strategy signal."""
    return f"   Decision: {decision.label.value} | {decision.explanation}"


def format_position_decision_line(decision: PositionDecision) -> str:
    """Render a compact decision note under an open position."""
    explanation = decision.explanation
    if (
        decision.label == DecisionLabel.HOLD
        and explanation == "position is between stop and target"
    ):
        explanation = "Price is between stop and target"
    return f"   Decision: {decision.label.value} | {explanation}"


def build_decision_summary(
    signal_decisions: list[SignalDecision],
    position_decisions: list[PositionDecision],
) -> DecisionSummary:
    """Aggregate signal and position decisions for JSON output."""
    buy_setups: list[str] = []
    watch_next_session: list[str] = []
    no_action: list[str] = []
    hold_positions: list[str] = []
    sell_alerts: list[str] = []

    for decision in signal_decisions:
        if decision.label == DecisionLabel.BUY_SETUP:
            buy_setups.append(decision.symbol)
        elif decision.label == DecisionLabel.WATCH_NEXT_SESSION:
            watch_next_session.append(decision.symbol)
        elif decision.label == DecisionLabel.NO_ACTION:
            no_action.append(decision.symbol)

    for decision in position_decisions:
        if decision.label == DecisionLabel.HOLD:
            hold_positions.append(decision.symbol)
        elif decision.label in (
            DecisionLabel.SELL_ALERT_STOP,
            DecisionLabel.SELL_ALERT_TARGET,
        ):
            sell_alerts.append(decision.symbol)

    return DecisionSummary(
        buy_setups=buy_setups,
        watch_next_session=watch_next_session,
        hold_positions=hold_positions,
        sell_alerts=sell_alerts,
        no_action=no_action,
        signals=[item.to_dict() for item in signal_decisions],
        positions=[item.to_dict() for item in position_decisions],
    )


def build_executive_action_from_decisions(
    *,
    session: EgxMarketSession,
    decision_summary: DecisionSummary,
) -> str:
    """Build Executive Summary action line from decision labels."""
    if decision_summary.sell_alerts:
        symbols = ", ".join(decision_summary.sell_alerts)
        review_timing = _position_review_timing(session)
        if review_timing == REVIEW_TIMING_NOW:
            return f"Sell alerts need review now: {symbols}"
        if review_timing == REVIEW_TIMING_NEXT_OPEN_SESSION:
            return f"Sell alerts for next session review: {symbols}"
        return f"Sell alerts for next trading session review: {symbols}"

    if (
        session.guard_enabled
        and not session.is_open_for_new_entries
        and decision_summary.watch_next_session
    ):
        symbols = ", ".join(decision_summary.watch_next_session)
        return f"Watch next session: {symbols}"

    if decision_summary.buy_setups:
        symbols = ", ".join(decision_summary.buy_setups)
        return f"Review BUY SETUP: {symbols}"

    return "No actionable setup today"
