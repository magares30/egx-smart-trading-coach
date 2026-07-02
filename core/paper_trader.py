"""Automatic paper trade execution from strategy signals."""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from core.market_hours import EgxMarketSession
from core.paper_engine import evaluate_buy_setup_for_open, sort_strategy_setups
from core.portfolio import VirtualPortfolio
from core.risk import RiskManager
from core.strategy import StrategyReport, StrategyResult
from core.trade_journal import TradeJournal


class PaperTradeDecision(str, Enum):
    OPENED = "OPENED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


class PaperTradeResult(BaseModel):
    symbol: str
    decision: PaperTradeDecision
    trade_id: str | None = None
    quantity: int | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    confidence_score: int | None = None
    reasons: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)


class PaperTradingReport(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    attempted_setups: int = 0
    opened_trades: list[PaperTradeResult] = Field(default_factory=list)
    rejected_trades: list[PaperTradeResult] = Field(default_factory=list)
    skipped_trades: list[PaperTradeResult] = Field(default_factory=list)


class AutoPaperTrader:
    """Opens paper trades from Strategy Scanner B BUY_SETUP signals."""

    def __init__(
        self,
        portfolio: VirtualPortfolio,
        journal: TradeJournal,
        risk_manager: RiskManager,
        max_trades_per_run: int = 3,
        min_confidence_score: int = 70,
        ignore_market_hours: bool = False,
        market_session: EgxMarketSession | None = None,
        now: datetime | None = None,
    ) -> None:
        self._portfolio = portfolio
        self._journal = journal
        self._risk_manager = risk_manager
        self._max_trades_per_run = max_trades_per_run
        self._min_confidence_score = min_confidence_score
        self._ignore_market_hours = ignore_market_hours
        self._market_session = market_session
        self._market_now = now

    def _to_paper_result(
        self, setup: StrategyResult, evaluation
    ) -> PaperTradeResult:
        if evaluation.decision == "SKIPPED":
            reason = evaluation.reason
            if reason == "no trade signal available":
                reasons = ["No trade signal available"]
            elif reason == "confidence below threshold":
                reasons = ["Confidence below threshold"]
            elif reason == "market closed for new paper entries":
                reasons = ["Market closed for new paper entries"]
            elif reason == "already open position":
                reasons = ["Open position already exists for symbol"]
            else:
                reasons = [reason or "Skipped"]
            return PaperTradeResult(
                symbol=setup.symbol,
                decision=PaperTradeDecision.SKIPPED,
                confidence_score=setup.confidence_score,
                entry_price=setup.entry_price,
                stop_loss=setup.stop_loss,
                take_profit=setup.take_profit,
                reasons=reasons,
            )

        if evaluation.decision == "REJECTED":
            return PaperTradeResult(
                symbol=setup.symbol,
                decision=PaperTradeDecision.REJECTED,
                confidence_score=setup.confidence_score,
                entry_price=setup.entry_price,
                stop_loss=setup.stop_loss,
                take_profit=setup.take_profit,
                rejection_reasons=evaluation.rejection_reasons or [evaluation.reason],
            )

        trade = evaluation.trade
        assert trade is not None
        return PaperTradeResult(
            symbol=setup.symbol,
            decision=PaperTradeDecision.OPENED,
            trade_id=trade.id,
            quantity=trade.quantity,
            entry_price=trade.entry_price,
            stop_loss=trade.stop_loss,
            take_profit=trade.take_profit,
            confidence_score=setup.confidence_score,
            reasons=["Risk approved", "paper trade opened"],
        )

    def execute_strategy_report(
        self, strategy_report: StrategyReport
    ) -> PaperTradingReport:
        """Evaluate and open paper trades from strategy BUY_SETUP plans."""
        opened: list[PaperTradeResult] = []
        rejected: list[PaperTradeResult] = []
        skipped: list[PaperTradeResult] = []
        attempted = 0

        for setup in sort_strategy_setups(strategy_report.buy_setups):
            if setup.signal is None:
                skipped.append(self._to_paper_result(setup, evaluate_buy_setup_for_open(
                    setup, portfolio=self._portfolio, risk_manager=self._risk_manager,
                    min_confidence_score=self._min_confidence_score,
                    ignore_market_hours=self._ignore_market_hours,
                    market_session=self._market_session,
                    now=self._market_now,
                )))
                continue

            if setup.confidence_score < self._min_confidence_score:
                skipped.append(self._to_paper_result(setup, evaluate_buy_setup_for_open(
                    setup, portfolio=self._portfolio, risk_manager=self._risk_manager,
                    min_confidence_score=self._min_confidence_score,
                    ignore_market_hours=self._ignore_market_hours,
                    market_session=self._market_session,
                    now=self._market_now,
                )))
                continue

            if setup.symbol in self._portfolio.positions:
                skipped.append(self._to_paper_result(setup, evaluate_buy_setup_for_open(
                    setup, portfolio=self._portfolio, risk_manager=self._risk_manager,
                    min_confidence_score=self._min_confidence_score,
                    ignore_market_hours=self._ignore_market_hours,
                    market_session=self._market_session,
                    now=self._market_now,
                )))
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
            result = self._to_paper_result(setup, evaluation)

            if evaluation.decision == "OPENED" and evaluation.trade is not None:
                self._journal.append_trade(evaluation.trade)
                opened.append(result)
            elif evaluation.decision == "REJECTED":
                rejected.append(result)

        return PaperTradingReport(
            attempted_setups=attempted,
            opened_trades=opened,
            rejected_trades=rejected,
            skipped_trades=skipped,
        )
