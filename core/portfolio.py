"""Virtual paper portfolio with persistence."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from config import settings
from core.json_storage import atomic_write_json
from core.models import (
    PortfolioSnapshot,
    Position,
    Trade,
    TradeSide,
    TradeStatus,
)


class PortfolioError(Exception):
    """Raised when a portfolio operation is not allowed."""


class VirtualPortfolio:
    """Simulated portfolio for paper trading only."""

    def __init__(self, state_path: Optional[Path] = None) -> None:
        self._state_path = state_path or settings.PORTFOLIO_STATE_PATH
        self.cash: float = settings.INITIAL_CAPITAL_EGP
        self.initial_capital: float = settings.INITIAL_CAPITAL_EGP
        self.realized_pnl: float = 0.0
        self.positions: dict[str, Position] = {}
        self.trades: dict[str, Trade] = {}

        if self._state_path.exists():
            self.load()
        else:
            self.save()

    def load(self) -> None:
        """Load portfolio state from JSON."""
        with open(self._state_path, encoding="utf-8") as file:
            data = json.load(file)

        self.cash = data["cash"]
        self.initial_capital = data["initial_capital"]
        self.realized_pnl = data["realized_pnl"]
        self.positions = {
            symbol: Position.model_validate(pos)
            for symbol, pos in data.get("positions", {}).items()
        }
        self.trades = {
            trade_id: Trade.model_validate(trade)
            for trade_id, trade in data.get("trades", {}).items()
        }

    def save(self) -> None:
        """Persist portfolio state to JSON."""
        data = {
            "cash": self.cash,
            "initial_capital": self.initial_capital,
            "realized_pnl": self.realized_pnl,
            "positions": {
                symbol: pos.model_dump(mode="json")
                for symbol, pos in self.positions.items()
            },
            "trades": {
                trade_id: trade.model_dump(mode="json")
                for trade_id, trade in self.trades.items()
            },
        }
        atomic_write_json(self._state_path, data)

    def open_trade(
        self,
        symbol: str,
        side: TradeSide,
        quantity: int,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        reason: str = "",
        notes: str = "",
    ) -> Trade:
        """Open a paper trade after safety and balance checks."""
        if not settings.PAPER_TRADING_ONLY:
            raise PortfolioError(
                "Live trading is disabled — PAPER_TRADING_ONLY must be True"
            )

        open_count = len(self.positions)
        if open_count >= settings.MAX_OPEN_POSITIONS:
            raise PortfolioError(
                f"Maximum open positions ({settings.MAX_OPEN_POSITIONS}) reached"
            )

        cost = quantity * entry_price
        if cost > self.cash:
            raise PortfolioError(
                f"Insufficient cash: need {cost:,.2f} {settings.BASE_CURRENCY}, "
                f"have {self.cash:,.2f} {settings.BASE_CURRENCY}"
            )

        if symbol in self.positions:
            raise PortfolioError(f"Position already open for {symbol}")

        trade = Trade(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status=TradeStatus.OPEN,
            reason=reason,
            notes=notes,
        )

        position = Position(
            symbol=symbol,
            quantity=quantity,
            avg_entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            opened_at=trade.opened_at,
        )

        self.cash -= cost
        self.positions[symbol] = position
        self.trades[trade.id] = trade
        self.save()
        return trade

    def close_trade(self, trade_id: str, exit_price: float) -> Trade:
        """Close an open paper trade at the given exit price."""
        if trade_id not in self.trades:
            raise PortfolioError(f"Trade {trade_id} not found")

        trade = self.trades[trade_id]
        if trade.status != TradeStatus.OPEN:
            raise PortfolioError(f"Trade {trade_id} is not open")

        if trade.symbol not in self.positions:
            raise PortfolioError(f"No open position for {trade.symbol}")

        proceeds = trade.quantity * exit_price
        if trade.side == TradeSide.BUY:
            pnl = (exit_price - trade.entry_price) * trade.quantity
        else:
            pnl = (trade.entry_price - exit_price) * trade.quantity

        pnl_percent = (pnl / (trade.entry_price * trade.quantity)) * 100

        trade.exit_price = exit_price
        trade.status = TradeStatus.CLOSED
        trade.closed_at = datetime.now(UTC)
        trade.pnl = pnl
        trade.pnl_percent = pnl_percent

        self.cash += proceeds
        self.realized_pnl += pnl
        del self.positions[trade.symbol]
        self.save()
        return trade

    def get_open_trades(self) -> list[Trade]:
        """Return all trades with OPEN status."""
        return [
            trade for trade in self.trades.values() if trade.status == TradeStatus.OPEN
        ]

    def get_unrealized_pnl(
        self, latest_prices: Optional[dict[str, float]] = None
    ) -> float:
        """Calculate unrealized PnL from latest market prices."""
        if not latest_prices:
            return 0.0

        total = 0.0
        for symbol, position in self.positions.items():
            price = latest_prices.get(symbol)
            if price is None:
                continue
            total += (price - position.avg_entry_price) * position.quantity
        return total

    def get_snapshot(
        self, latest_prices: Optional[dict[str, float]] = None
    ) -> PortfolioSnapshot:
        """Return a summary snapshot of the current portfolio."""
        unrealized = self.get_unrealized_pnl(latest_prices)
        market_value = sum(
            (latest_prices or {}).get(symbol, pos.avg_entry_price) * pos.quantity
            for symbol, pos in self.positions.items()
        )
        equity = self.cash + market_value

        return PortfolioSnapshot(
            cash=self.cash,
            equity=equity,
            open_positions=len(self.positions),
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized,
            total_pnl=self.realized_pnl + unrealized,
        )

    def reset(self) -> None:
        """Reset portfolio to initial state (useful for demos/tests)."""
        self.cash = settings.INITIAL_CAPITAL_EGP
        self.initial_capital = settings.INITIAL_CAPITAL_EGP
        self.realized_pnl = 0.0
        self.positions = {}
        self.trades = {}
        self.save()
