"""Shared paper trade open and close helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from config import settings
from core.market_hours import EgxMarketSession, detect_egx_market_session
from core.models import Trade, TradeSide
from core.portfolio import PortfolioError, VirtualPortfolio
from core.risk import RiskManager
from core.strategy import StrategyResult
from core.trade_journal import TradeJournal


@dataclass
class PaperOpenEvaluation:
    decision: str
    reason: str
    trade: Trade | None = None
    quantity: int | None = None
    risk_amount: float | None = None
    cost: float | None = None
    rejection_reasons: list[str] | None = None


def sort_strategy_setups(buy_setups: list[StrategyResult]) -> list[StrategyResult]:
    """Sort BUY_SETUP plans by confidence then risk/reward."""
    return sorted(
        buy_setups,
        key=lambda setup: (
            -setup.confidence_score,
            -(setup.risk_reward or 0.0),
        ),
    )


def risk_amount_for_signal(portfolio: VirtualPortfolio) -> float:
    """Return configured risk budget from current portfolio equity."""
    equity = portfolio.get_snapshot().equity
    return equity * (settings.RISK_PER_TRADE_PERCENT / 100)


def evaluate_buy_setup_for_open(
    setup: StrategyResult,
    *,
    portfolio: VirtualPortfolio,
    risk_manager: RiskManager,
    min_confidence_score: int,
    ignore_market_hours: bool = False,
    market_session: EgxMarketSession | None = None,
    now: datetime | None = None,
) -> PaperOpenEvaluation:
    """Evaluate one BUY_SETUP without mutating portfolio state."""
    if not ignore_market_hours:
        session = (
            market_session
            if market_session is not None
            else detect_egx_market_session(now=now)
        )
        if not session.is_open_for_new_entries:
            return PaperOpenEvaluation(
                decision="SKIPPED",
                reason="market closed for new paper entries",
            )

    if setup.signal is None:
        return PaperOpenEvaluation(
            decision="SKIPPED",
            reason="no trade signal available",
        )

    if setup.confidence_score < min_confidence_score:
        return PaperOpenEvaluation(
            decision="SKIPPED",
            reason="confidence below threshold",
        )

    if setup.symbol in portfolio.positions:
        return PaperOpenEvaluation(
            decision="SKIPPED",
            reason="already open position",
        )

    signal = setup.signal
    equity = portfolio.get_snapshot().equity
    risk_decision = risk_manager.evaluate(signal, equity)
    if not risk_decision.approved:
        reason = ", ".join(risk_decision.rejection_reasons) or "risk rejected"
        return PaperOpenEvaluation(
            decision="REJECTED",
            reason=reason,
            rejection_reasons=risk_decision.rejection_reasons,
            quantity=risk_decision.quantity,
        )

    risk_amount = risk_amount_for_signal(portfolio)
    cost = risk_decision.quantity * signal.entry_price
    try:
        trade = portfolio.open_trade(
            symbol=setup.symbol,
            side=TradeSide.BUY,
            quantity=risk_decision.quantity,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            reason="; ".join(signal.reasons),
        )
    except PortfolioError as error:
        reason = str(error)
        if "Insufficient cash" in reason:
            reason = "insufficient cash"
        return PaperOpenEvaluation(
            decision="REJECTED",
            reason=reason,
            quantity=risk_decision.quantity,
            risk_amount=risk_amount,
            cost=cost,
            rejection_reasons=[str(error)],
        )

    return PaperOpenEvaluation(
        decision="OPENED",
        reason="paper trade opened",
        trade=trade,
        quantity=trade.quantity,
        risk_amount=risk_amount,
        cost=trade.quantity * trade.entry_price,
    )


def close_paper_trade(
    portfolio: VirtualPortfolio,
    journal: TradeJournal,
    trade_id: str,
    exit_price: float,
) -> Trade:
    """Close a paper trade and sync the journal."""
    closed = portfolio.close_trade(trade_id, exit_price)
    journal.update_trade(closed)
    return closed
