"""Advisory exit plans for open paper positions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.decision_labels import (
    REVIEW_TIMING_NEXT_OPEN_SESSION,
    REVIEW_TIMING_NEXT_TRADING_SESSION,
    REVIEW_TIMING_NOW,
)
from core.market_hours import EgxMarketSession

EXIT_PLAN_SUMMARY_NOTE = (
    "Advisory only; paper trading only; no real execution"
)
PROFIT_PROTECT_THRESHOLD = 0.5


class ExitPlanLabel(str, Enum):
    EXIT_REVIEW_TARGET = "EXIT_REVIEW_TARGET"
    EXIT_REVIEW_STOP = "EXIT_REVIEW_STOP"
    HOLD_PROFIT_RUNNING = "HOLD_PROFIT_RUNNING"
    HOLD_PROTECT_PROFIT = "HOLD_PROTECT_PROFIT"
    HOLD_RISK_ACTIVE = "HOLD_RISK_ACTIVE"
    HOLD_INSUFFICIENT_DATA = "HOLD_INSUFFICIENT_DATA"


@dataclass(frozen=True)
class PositionExitPlan:
    symbol: str
    label: ExitPlanLabel
    explanation: str
    exit_timing: str | None
    exit_executable_now: bool

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "symbol": self.symbol,
            "exit_plan": self.label.value,
            "exit_plan_explanation": self.explanation,
            "exit_executable_now": self.exit_executable_now,
        }
        if self.exit_timing is not None:
            payload["exit_timing"] = self.exit_timing
        return payload


@dataclass(frozen=True)
class ExitPlanSummary:
    urgent_exits: list[str]
    protect_profit: list[str]
    hold: list[str]
    insufficient_data: list[str]
    positions: list[dict[str, object]]
    note: str = EXIT_PLAN_SUMMARY_NOTE

    def to_dict(self) -> dict[str, object]:
        return {
            "urgent_exits": list(self.urgent_exits),
            "protect_profit": list(self.protect_profit),
            "hold": list(self.hold),
            "insufficient_data": list(self.insufficient_data),
            "positions": list(self.positions),
            "note": self.note,
        }


def _exit_timing(session: EgxMarketSession) -> str:
    if session.is_open_for_new_entries:
        return REVIEW_TIMING_NOW
    if session.is_trading_day:
        return REVIEW_TIMING_NEXT_OPEN_SESSION
    return REVIEW_TIMING_NEXT_TRADING_SESSION


def _target_exit_explanation(review_timing: str) -> str:
    if review_timing == REVIEW_TIMING_NOW:
        return "Target reached; review taking profit during open market"
    if review_timing == REVIEW_TIMING_NEXT_OPEN_SESSION:
        return "Target reached; review taking profit at next open session"
    return "Target reached; review taking profit next trading session"


def _stop_exit_explanation(review_timing: str) -> str:
    if review_timing == REVIEW_TIMING_NOW:
        return "Stop reached; review risk exit during open market"
    if review_timing == REVIEW_TIMING_NEXT_OPEN_SESSION:
        return "Stop reached; review risk exit at next open session"
    return "Stop reached; review risk exit next trading session"


def classify_position_exit_plan(
    *,
    symbol: str,
    entry_price: float | None,
    current_price: float | None,
    stop_loss: float | None,
    take_profit: float | None,
    session: EgxMarketSession,
) -> PositionExitPlan:
    """Build an advisory exit plan for one open paper position."""
    exit_timing = _exit_timing(session)

    if (
        entry_price is None
        or current_price is None
        or stop_loss is None
        or take_profit is None
    ):
        return PositionExitPlan(
            symbol=symbol,
            label=ExitPlanLabel.HOLD_INSUFFICIENT_DATA,
            explanation="Missing exit data; monitor manually",
            exit_timing=exit_timing,
            exit_executable_now=False,
        )

    if current_price >= take_profit:
        return PositionExitPlan(
            symbol=symbol,
            label=ExitPlanLabel.EXIT_REVIEW_TARGET,
            explanation=_target_exit_explanation(exit_timing),
            exit_timing=exit_timing,
            exit_executable_now=exit_timing == REVIEW_TIMING_NOW,
        )

    if current_price <= stop_loss:
        return PositionExitPlan(
            symbol=symbol,
            label=ExitPlanLabel.EXIT_REVIEW_STOP,
            explanation=_stop_exit_explanation(exit_timing),
            exit_timing=exit_timing,
            exit_executable_now=exit_timing == REVIEW_TIMING_NOW,
        )

    if current_price > entry_price:
        target_distance = take_profit - entry_price
        gain = current_price - entry_price
        if target_distance > 0 and gain >= (PROFIT_PROTECT_THRESHOLD * target_distance):
            return PositionExitPlan(
                symbol=symbol,
                label=ExitPlanLabel.HOLD_PROTECT_PROFIT,
                explanation=(
                    "Profit is running; consider protecting profit by "
                    "reviewing stop level manually"
                ),
                exit_timing=exit_timing,
                exit_executable_now=False,
            )
        return PositionExitPlan(
            symbol=symbol,
            label=ExitPlanLabel.HOLD_PROFIT_RUNNING,
            explanation="Position is profitable but target not reached yet",
            exit_timing=exit_timing,
            exit_executable_now=False,
        )

    return PositionExitPlan(
        symbol=symbol,
        label=ExitPlanLabel.HOLD_RISK_ACTIVE,
        explanation=(
            "Position is still above stop but below/near entry; risk remains active"
        ),
        exit_timing=exit_timing,
        exit_executable_now=False,
    )


def format_exit_plan_line(plan: PositionExitPlan) -> str:
    """Render advisory exit plan line for the Paper Portfolio section."""
    return f"   Exit Plan: {plan.label.value} | {plan.explanation}"


def build_exit_plan_summary(
    exit_plans: list[PositionExitPlan],
) -> ExitPlanSummary:
    """Aggregate per-position exit plans for JSON output."""
    urgent_exits: list[str] = []
    protect_profit: list[str] = []
    hold: list[str] = []
    insufficient_data: list[str] = []

    for plan in exit_plans:
        if plan.label in (
            ExitPlanLabel.EXIT_REVIEW_TARGET,
            ExitPlanLabel.EXIT_REVIEW_STOP,
        ):
            urgent_exits.append(plan.symbol)
        elif plan.label == ExitPlanLabel.HOLD_PROTECT_PROFIT:
            protect_profit.append(plan.symbol)
        elif plan.label == ExitPlanLabel.HOLD_INSUFFICIENT_DATA:
            insufficient_data.append(plan.symbol)
        else:
            hold.append(plan.symbol)

    return ExitPlanSummary(
        urgent_exits=urgent_exits,
        protect_profit=protect_profit,
        hold=hold,
        insufficient_data=insufficient_data,
        positions=[plan.to_dict() for plan in exit_plans],
    )


def _compact_urgent_exit_note(plan: PositionExitPlan) -> str:
    if plan.label == ExitPlanLabel.EXIT_REVIEW_TARGET:
        if plan.exit_timing == REVIEW_TIMING_NOW:
            return f"{plan.symbol} target review now"
        if plan.exit_timing == REVIEW_TIMING_NEXT_OPEN_SESSION:
            return f"{plan.symbol} target review next session"
        return f"{plan.symbol} target review next trading session"

    if plan.exit_timing == REVIEW_TIMING_NOW:
        return f"{plan.symbol} stop review now"
    if plan.exit_timing == REVIEW_TIMING_NEXT_OPEN_SESSION:
        return f"{plan.symbol} stop review next session"
    return f"{plan.symbol} stop review next trading session"


def build_executive_exit_plan_line(
    exit_plans: list[PositionExitPlan],
    *,
    open_positions_count: int,
) -> str:
    """Build compact exit plan line for the Executive Summary."""
    if open_positions_count == 0 or not exit_plans:
        return "No open positions"

    urgent_notes = [
        _compact_urgent_exit_note(plan)
        for plan in exit_plans
        if plan.label
        in (ExitPlanLabel.EXIT_REVIEW_TARGET, ExitPlanLabel.EXIT_REVIEW_STOP)
    ]

    if urgent_notes:
        return "; ".join(urgent_notes)

    protect_symbols = [
        plan.symbol
        for plan in exit_plans
        if plan.label == ExitPlanLabel.HOLD_PROTECT_PROFIT
    ]
    if protect_symbols:
        return f"Protect profit review: {', '.join(protect_symbols)}"

    return "No urgent exit alerts; open positions are hold/monitor"
