"""Build compact executive summary lines for the daily EGX report."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.confirmation_summary import (
    SignalConfirmationSummary,
    build_executive_confirmation_line,
)
from core.decision_labels import DecisionSummary, build_executive_action_from_decisions
from core.exit_plan import (
    PositionExitPlan,
    build_executive_exit_plan_line,
)
from core.market_breadth_mood import (
    BREADTH_MOOD_INFO_WARNING,
    MarketBreadthMoodResult,
)
from core.market_hours import EgxMarketSession
from core.closed_market_digest import (
    CLOSED_BUY_PLAN_TEXT,
    CLOSED_MAIN_RISK_TEXT,
    closed_market_digest_enabled,
)
from core.market_mood import MarketMoodResult
from core.scanner import ScannerResult
from core.strategy import StrategyResult
from core.warning_formatting import VOLUME_HISTORY_SUMMARY
from core.live_volume import NOT_ENOUGH_VOLUME_HISTORY_WARNING

BUY_PLAN_TEXT = "Use listed entry prices only during open market"
SELL_PLAN_TEXT = (
    "Use each signal stop loss and target; no automatic real execution; "
    "exit rules will be upgraded in a later patch"
)
ACTION_NO_SETUP = "No actionable setup today"
PAPER_PNL_UNAVAILABLE = "n/a"


@dataclass(frozen=True)
class ExecutiveSummary:
    """Compact daily action summary for TXT and JSON reports."""

    market: str
    best_ideas: list[str]
    action: str
    buy_plan: str
    sell_plan: str
    paper_pnl: str
    exit_plan: str
    confirmation: str
    main_risk: str

    def to_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "best_ideas": list(self.best_ideas),
            "action": self.action,
            "buy_plan": self.buy_plan,
            "sell_plan": self.sell_plan,
            "paper_pnl": self.paper_pnl,
            "exit_plan": self.exit_plan,
            "confirmation": self.confirmation,
            "main_risk": self.main_risk,
        }

    def to_lines(self) -> list[str]:
        best_ideas_text = ", ".join(self.best_ideas) if self.best_ideas else "(none)"
        return [
            f"- Market: {self.market}",
            f"- Best Ideas: {best_ideas_text}",
            f"- Action: {self.action}",
            f"- Buy Plan: {self.buy_plan}",
            f"- Sell Plan: {self.sell_plan}",
            f"- Paper P&L: {self.paper_pnl}",
            f"- Exit Plan: {self.exit_plan}",
            f"- Confirmation: {self.confirmation}",
            f"- Main Risk: {self.main_risk}",
        ]


def _format_paper_pnl_amount(amount: float, percent: float | None) -> str:
    sign = "+" if amount > 0 else ""
    line = f"{sign}{amount:,.2f}"
    if percent is not None:
        line += f" ({percent:+.2f}%)"
    return line


def _resolve_mood_label(
    market_mood: MarketMoodResult,
    *,
    report_date: date,
    session: EgxMarketSession,
    market_breadth_mood_result: MarketBreadthMoodResult | None,
) -> str:
    if not session.is_trading_day and report_date.weekday() in (4, 5):
        return "Weekend"
    if market_breadth_mood_result is not None:
        return market_breadth_mood_result.mood.value
    return market_mood.mood.value


def _build_market_line(
    session: EgxMarketSession,
    mood_label: str,
) -> str:
    entries_label = "enabled" if session.paper_entries_enabled else "disabled"
    return (
        f"{session.session_status.value} | {mood_label} | "
        f"Paper entries {entries_label}"
    )


def _best_idea_symbols(
    strategy_items: list[StrategyResult],
    display_candidates: list[ScannerResult],
    *,
    limit: int = 3,
) -> list[str]:
    if strategy_items:
        return [item.symbol for item in strategy_items[:limit]]
    return [item.symbol for item in display_candidates[:limit]]


def _build_action(
    *,
    session: EgxMarketSession,
    decision_summary: DecisionSummary | None,
) -> str:
    if decision_summary is not None:
        return build_executive_action_from_decisions(
            session=session,
            decision_summary=decision_summary,
        )
    return ACTION_NO_SETUP


def _build_paper_pnl_line(
    paper_performance_payload: dict[str, object],
    paper_portfolio_payload: dict[str, object],
) -> str:
    if paper_performance_payload.get("available"):
        total_pnl = paper_performance_payload.get("total_pnl")
        total_return_pct = paper_performance_payload.get("total_return_pct")
        open_positions_count = paper_performance_payload.get("open_positions_count", 0)
        if total_pnl is not None:
            percent = (
                float(total_return_pct)
                if total_return_pct is not None
                else None
            )
            pnl_text = _format_paper_pnl_amount(float(total_pnl), percent)
            return f"{pnl_text} | Open positions: {open_positions_count}"

    if paper_portfolio_payload.get("available"):
        unrealized_pnl = paper_portfolio_payload.get("unrealized_pnl")
        unrealized_pnl_pct = paper_portfolio_payload.get("unrealized_pnl_pct")
        open_positions_count = paper_portfolio_payload.get("open_positions_count", 0)
        if unrealized_pnl is not None:
            percent = (
                float(unrealized_pnl_pct)
                if unrealized_pnl_pct is not None
                else None
            )
            pnl_text = _format_paper_pnl_amount(float(unrealized_pnl), percent)
            return f"{pnl_text} | Open positions: {open_positions_count}"
        return f"0.00 | Open positions: {open_positions_count}"

    return PAPER_PNL_UNAVAILABLE


def _pick_main_risk(
    warnings: list[str],
    *,
    session: EgxMarketSession,
) -> str:
    if closed_market_digest_enabled(session):
        return CLOSED_MAIN_RISK_TEXT

    if session.guard_enabled and not session.paper_entries_enabled:
        return "Market closed; paper entries disabled"

    for warning in warnings:
        lowered = warning.lower()
        if "ta-lib" in lowered and "insufficient" in lowered:
            return warning

    volume_fragment = NOT_ENOUGH_VOLUME_HISTORY_WARNING.lower()
    volume_summary_fragment = "not enough volume history"
    for warning in warnings:
        lowered = warning.lower()
        if volume_fragment in lowered or volume_summary_fragment in lowered:
            return warning
        if VOLUME_HISTORY_SUMMARY.split("{")[0].strip().lower() in lowered:
            return warning

    for warning in warnings:
        lowered = warning.lower()
        if "egx30" in lowered or "egx70" in lowered:
            return warning
        if BREADTH_MOOD_INFO_WARNING.lower() in lowered:
            return warning

    if warnings:
        return warnings[0]
    return "none"


def build_executive_summary(
    *,
    report_date: date,
    market_session: EgxMarketSession,
    market_mood: MarketMoodResult,
    strategy_items: list[StrategyResult],
    display_candidates: list[ScannerResult],
    warnings: list[str],
    paper_performance_payload: dict[str, object] | None = None,
    paper_portfolio_payload: dict[str, object] | None = None,
    market_breadth_mood_result: MarketBreadthMoodResult | None = None,
    decision_summary: DecisionSummary | None = None,
    exit_plans: list[PositionExitPlan] | None = None,
    open_positions_count: int = 0,
    signal_confirmations: list[SignalConfirmationSummary] | None = None,
) -> ExecutiveSummary:
    """Build executive summary content from existing daily report inputs."""
    mood_label = _resolve_mood_label(
        market_mood,
        report_date=report_date,
        session=market_session,
        market_breadth_mood_result=market_breadth_mood_result,
    )
    performance_payload = paper_performance_payload or {}
    portfolio_payload = paper_portfolio_payload or {}
    buy_plan = (
        CLOSED_BUY_PLAN_TEXT
        if closed_market_digest_enabled(market_session)
        else BUY_PLAN_TEXT
    )

    return ExecutiveSummary(
        market=_build_market_line(market_session, mood_label),
        best_ideas=_best_idea_symbols(strategy_items, display_candidates),
        action=_build_action(
            session=market_session,
            decision_summary=decision_summary,
        ),
        buy_plan=buy_plan,
        sell_plan=SELL_PLAN_TEXT,
        paper_pnl=_build_paper_pnl_line(performance_payload, portfolio_payload),
        exit_plan=build_executive_exit_plan_line(
            exit_plans or [],
            open_positions_count=open_positions_count,
        ),
        confirmation=build_executive_confirmation_line(signal_confirmations or []),
        main_risk=_pick_main_risk(warnings, session=market_session),
    )
