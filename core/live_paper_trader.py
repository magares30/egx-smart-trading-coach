"""Paper trade execution from live EGX strategy scan signals."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from core.market_hours import EgxMarketSession
from core.paper_engine import evaluate_buy_setup_for_open, sort_strategy_setups
from core.portfolio import VirtualPortfolio
from core.risk import RiskManager
from core.strategy import StrategyReport, StrategyResult
from core.trade_journal import TradeJournal


class LivePaperTradeDecision(str, Enum):
    OPENED = "OPENED"
    SKIPPED = "SKIPPED"
    REJECTED = "REJECTED"


class LivePaperTradeResult(BaseModel):
    symbol: str
    decision: LivePaperTradeDecision
    reason: str
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    quantity: int | None = None
    confidence_score: int | None = None
    risk_amount: float | None = None
    cost: float | None = None


class LivePaperTradingReport(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    results: list[LivePaperTradeResult] = Field(default_factory=list)
    opened_count: int = 0
    skipped_count: int = 0
    rejected_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class LivePaperTrader:
    """Opens paper trades from live Strategy Scanner B BUY_SETUP signals."""

    def __init__(
        self,
        portfolio: VirtualPortfolio,
        trade_journal: TradeJournal,
        risk_manager: RiskManager,
        max_trades_per_run: int = 3,
        min_confidence_score: int = 75,
        ignore_market_hours: bool = False,
        market_session: EgxMarketSession | None = None,
        now: datetime | None = None,
    ) -> None:
        self._portfolio = portfolio
        self._journal = trade_journal
        self._risk_manager = risk_manager
        self._max_trades_per_run = max_trades_per_run
        self._min_confidence_score = min_confidence_score
        self._ignore_market_hours = ignore_market_hours
        self._market_session = market_session
        self._market_now = now

    def _append_result(
        self,
        setup: StrategyResult,
        evaluation,
        results: list[LivePaperTradeResult],
    ) -> LivePaperTradeResult:
        entry_price = setup.entry_price
        stop_loss = setup.stop_loss
        take_profit = setup.take_profit
        if evaluation.trade is not None:
            entry_price = evaluation.trade.entry_price
            stop_loss = evaluation.trade.stop_loss
            take_profit = evaluation.trade.take_profit
        result = LivePaperTradeResult(
            symbol=setup.symbol,
            decision=LivePaperTradeDecision(evaluation.decision),
            reason=evaluation.reason,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=evaluation.quantity,
            confidence_score=setup.confidence_score,
            risk_amount=evaluation.risk_amount,
            cost=evaluation.cost,
        )
        results.append(result)
        return result

    def trade_from_strategy_report(
        self, strategy_report: StrategyReport
    ) -> LivePaperTradingReport:
        """Evaluate and open paper trades from BUY_SETUP strategy signals."""
        results: list[LivePaperTradeResult] = []
        opened_count = 0
        skipped_count = 0
        rejected_count = 0
        attempted = 0

        for setup in sort_strategy_setups(strategy_report.buy_setups):
            if setup.signal is None:
                self._append_result(
                    setup,
                    evaluate_buy_setup_for_open(
                        setup,
                        portfolio=self._portfolio,
                        risk_manager=self._risk_manager,
                        min_confidence_score=self._min_confidence_score,
                        ignore_market_hours=self._ignore_market_hours,
                        market_session=self._market_session,
                        now=self._market_now,
                    ),
                    results,
                )
                skipped_count += 1
                continue

            if setup.confidence_score < self._min_confidence_score:
                self._append_result(
                    setup,
                    evaluate_buy_setup_for_open(
                        setup,
                        portfolio=self._portfolio,
                        risk_manager=self._risk_manager,
                        min_confidence_score=self._min_confidence_score,
                        ignore_market_hours=self._ignore_market_hours,
                        market_session=self._market_session,
                        now=self._market_now,
                    ),
                    results,
                )
                skipped_count += 1
                continue

            if setup.symbol in self._portfolio.positions:
                self._append_result(
                    setup,
                    evaluate_buy_setup_for_open(
                        setup,
                        portfolio=self._portfolio,
                        risk_manager=self._risk_manager,
                        min_confidence_score=self._min_confidence_score,
                        ignore_market_hours=self._ignore_market_hours,
                        market_session=self._market_session,
                        now=self._market_now,
                    ),
                    results,
                )
                skipped_count += 1
                continue

            if attempted >= self._max_trades_per_run:
                break

            attempted += 1
            evaluation = evaluate_buy_setup_for_open(
                setup,
                portfolio=self._portfolio,
                risk_manager=self._risk_manager,
                min_confidence_score=self._min_confidence_score,
                ignore_market_hours=self._ignore_market_hours,
                market_session=self._market_session,
                now=self._market_now,
            )
            result = self._append_result(setup, evaluation, results)

            if evaluation.decision == "OPENED" and evaluation.trade is not None:
                self._journal.append_trade(evaluation.trade)
                opened_count += 1
            elif evaluation.decision == "REJECTED":
                rejected_count += 1

        return LivePaperTradingReport(
            results=results,
            opened_count=opened_count,
            skipped_count=skipped_count,
            rejected_count=rejected_count,
        )
