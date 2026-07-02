"""Paper trade monitor for automatic exits on TP/SL/EOD."""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from core.models import TradeSide, TradeStatus
from core.paper_engine import close_paper_trade
from core.portfolio import PortfolioError, VirtualPortfolio
from core.trade_journal import TradeJournal


class ExitReason(str, Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    END_OF_DAY = "END_OF_DAY"
    NO_EXIT = "NO_EXIT"


class PaperExitDecision(str, Enum):
    CLOSED = "CLOSED"
    HELD = "HELD"
    ERROR = "ERROR"


class PaperExitResult(BaseModel):
    symbol: str
    trade_id: str
    decision: PaperExitDecision
    exit_reason: ExitReason
    exit_price: float | None = None
    pnl: float | None = None
    pnl_percent: float | None = None
    reasons: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PaperMonitorReport(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    checked_trades: int = 0
    closed_trades: list[PaperExitResult] = Field(default_factory=list)
    held_trades: list[PaperExitResult] = Field(default_factory=list)
    errors: list[PaperExitResult] = Field(default_factory=list)


class PaperTradeMonitor:
    """Reviews open paper trades and closes them when exit conditions are met."""

    OFFLINE_MONITOR_WARNING = (
        "Offline monitor uses CSV close prices only; use --egx-live-paper-monitor "
        "for live EGX OHLC exits."
    )

    def __init__(
        self,
        portfolio: VirtualPortfolio,
        journal: TradeJournal,
    ) -> None:
        self._portfolio = portfolio
        self._journal = journal

    def _close_trade(
        self,
        trade_id: str,
        symbol: str,
        exit_price: float,
        exit_reason: ExitReason,
    ) -> PaperExitResult:
        try:
            closed = close_paper_trade(self._portfolio, self._journal, trade_id, exit_price)
            return PaperExitResult(
                symbol=symbol,
                trade_id=trade_id,
                decision=PaperExitDecision.CLOSED,
                exit_reason=exit_reason,
                exit_price=closed.exit_price,
                pnl=closed.pnl,
                pnl_percent=closed.pnl_percent,
                reasons=[f"Closed at {exit_price:.2f}"],
            )
        except PortfolioError as error:
            return PaperExitResult(
                symbol=symbol,
                trade_id=trade_id,
                decision=PaperExitDecision.ERROR,
                exit_reason=ExitReason.NO_EXIT,
                errors=[str(error)],
            )

    def monitor_open_trades(
        self,
        latest_prices: dict[str, float],
        force_end_of_day_exit: bool = False,
    ) -> PaperMonitorReport:
        """Check open trades and close when TP, SL, or EOD conditions are met."""
        closed: list[PaperExitResult] = []
        held: list[PaperExitResult] = []
        errors: list[PaperExitResult] = []

        open_trades = self._portfolio.get_open_trades()

        for trade in open_trades:
            if trade.side != TradeSide.BUY:
                continue

            latest_price = latest_prices.get(trade.symbol)
            if latest_price is None:
                held.append(
                    PaperExitResult(
                        symbol=trade.symbol,
                        trade_id=trade.id,
                        decision=PaperExitDecision.HELD,
                        exit_reason=ExitReason.NO_EXIT,
                        reasons=["No latest price available"],
                    )
                )
                continue

            if force_end_of_day_exit:
                result = self._close_trade(
                    trade.id, trade.symbol, latest_price, ExitReason.END_OF_DAY
                )
                if result.decision == PaperExitDecision.CLOSED:
                    closed.append(result)
                else:
                    errors.append(result)
                continue

            if latest_price >= trade.take_profit:
                result = self._close_trade(
                    trade.id, trade.symbol, trade.take_profit, ExitReason.TAKE_PROFIT
                )
                if result.decision == PaperExitDecision.CLOSED:
                    closed.append(result)
                else:
                    errors.append(result)
                continue

            if latest_price <= trade.stop_loss:
                result = self._close_trade(
                    trade.id, trade.symbol, trade.stop_loss, ExitReason.STOP_LOSS
                )
                if result.decision == PaperExitDecision.CLOSED:
                    closed.append(result)
                else:
                    errors.append(result)
                continue

            held.append(
                PaperExitResult(
                    symbol=trade.symbol,
                    trade_id=trade.id,
                    decision=PaperExitDecision.HELD,
                    exit_reason=ExitReason.NO_EXIT,
                    reasons=["No exit condition met"],
                )
            )

        return PaperMonitorReport(
            checked_trades=len(open_trades),
            closed_trades=closed,
            held_trades=held,
            errors=errors,
        )
