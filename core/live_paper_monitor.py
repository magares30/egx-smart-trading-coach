"""Monitor open paper trades against EGX live snapshot OHLC levels."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from core.models import TradeSide
from core.paper_engine import close_paper_trade
from core.portfolio import PortfolioError, VirtualPortfolio
from core.trade_journal import TradeJournal


class LivePaperExitReason(str, Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    HELD = "HELD"
    MISSING_SYMBOL = "MISSING_SYMBOL"


class LivePaperMonitorDecision(str, Enum):
    CLOSED = "CLOSED"
    HELD = "HELD"
    ERROR = "ERROR"


class LivePaperMonitorResult(BaseModel):
    symbol: str
    decision: LivePaperMonitorDecision
    reason: LivePaperExitReason
    entry_price: float | None = None
    current_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    exit_price: float | None = None
    pnl: float | None = None
    pnl_percent: float | None = None


class LivePaperMonitorReport(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    results: list[LivePaperMonitorResult] = Field(default_factory=list)
    closed_count: int = 0
    held_count: int = 0
    error_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class LivePaperMonitor:
    """Closes open paper trades when live snapshot OHLC hits TP or SL."""

    def __init__(
        self,
        portfolio: VirtualPortfolio,
        trade_journal: TradeJournal,
    ) -> None:
        self._portfolio = portfolio
        self._journal = trade_journal

    def _touched_take_profit(
        self, symbol_snapshot: LiveSymbolSnapshot, take_profit: float
    ) -> bool:
        return (
            symbol_snapshot.high >= take_profit
            or symbol_snapshot.close >= take_profit
        )

    def _touched_stop_loss(
        self, symbol_snapshot: LiveSymbolSnapshot, stop_loss: float
    ) -> bool:
        return symbol_snapshot.low <= stop_loss or symbol_snapshot.close <= stop_loss

    def _close_trade(
        self,
        symbol: str,
        trade_id: str,
        exit_price: float,
        exit_reason: LivePaperExitReason,
        *,
        entry_price: float,
        current_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> LivePaperMonitorResult:
        try:
            closed = close_paper_trade(
                self._portfolio,
                self._journal,
                trade_id,
                exit_price,
            )
            return LivePaperMonitorResult(
                symbol=symbol,
                decision=LivePaperMonitorDecision.CLOSED,
                reason=exit_reason,
                entry_price=entry_price,
                current_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                exit_price=closed.exit_price,
                pnl=closed.pnl,
                pnl_percent=closed.pnl_percent,
            )
        except PortfolioError:
            return LivePaperMonitorResult(
                symbol=symbol,
                decision=LivePaperMonitorDecision.ERROR,
                reason=LivePaperExitReason.HELD,
                entry_price=entry_price,
                current_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

    def monitor_from_live_snapshot(
        self, live_snapshot: LiveMarketSnapshot
    ) -> LivePaperMonitorReport:
        """Review open BUY paper trades against live snapshot OHLC."""
        results: list[LivePaperMonitorResult] = []
        warnings: list[str] = []
        closed_count = 0
        held_count = 0
        error_count = 0

        open_trades = self._portfolio.get_open_trades()

        for trade in open_trades:
            if trade.side != TradeSide.BUY:
                continue

            if trade.status.value != "OPEN":
                continue

            symbol_snapshot = live_snapshot.symbols.get(trade.symbol)
            if symbol_snapshot is None:
                warning = f"{trade.symbol}: symbol missing from live snapshot"
                warnings.append(warning)
                results.append(
                    LivePaperMonitorResult(
                        symbol=trade.symbol,
                        decision=LivePaperMonitorDecision.HELD,
                        reason=LivePaperExitReason.MISSING_SYMBOL,
                        entry_price=trade.entry_price,
                        stop_loss=trade.stop_loss,
                        take_profit=trade.take_profit,
                    )
                )
                held_count += 1
                continue

            current_price = symbol_snapshot.close

            if self._touched_take_profit(symbol_snapshot, trade.take_profit):
                result = self._close_trade(
                    trade.symbol,
                    trade.id,
                    trade.take_profit,
                    LivePaperExitReason.TAKE_PROFIT,
                    entry_price=trade.entry_price,
                    current_price=current_price,
                    stop_loss=trade.stop_loss,
                    take_profit=trade.take_profit,
                )
            elif self._touched_stop_loss(symbol_snapshot, trade.stop_loss):
                result = self._close_trade(
                    trade.symbol,
                    trade.id,
                    trade.stop_loss,
                    LivePaperExitReason.STOP_LOSS,
                    entry_price=trade.entry_price,
                    current_price=current_price,
                    stop_loss=trade.stop_loss,
                    take_profit=trade.take_profit,
                )
            else:
                result = LivePaperMonitorResult(
                    symbol=trade.symbol,
                    decision=LivePaperMonitorDecision.HELD,
                    reason=LivePaperExitReason.HELD,
                    entry_price=trade.entry_price,
                    current_price=current_price,
                    stop_loss=trade.stop_loss,
                    take_profit=trade.take_profit,
                )

            results.append(result)
            if result.decision == LivePaperMonitorDecision.CLOSED:
                closed_count += 1
            elif result.decision == LivePaperMonitorDecision.HELD:
                held_count += 1
            else:
                error_count += 1

        return LivePaperMonitorReport(
            results=results,
            closed_count=closed_count,
            held_count=held_count,
            error_count=error_count,
            warnings=warnings,
        )
