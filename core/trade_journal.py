"""Trade journal with JSON persistence and summary statistics."""

import json
from pathlib import Path
from typing import Any, Optional

from config import settings
from core.json_storage import atomic_write_json
from core.models import Trade, TradeStatus


class TradeJournal:
    """Persists and summarizes closed paper trades."""

    def __init__(self, journal_path: Optional[Path] = None) -> None:
        self._journal_path = journal_path or settings.TRADES_PATH
        self.trades: list[Trade] = []
        self.load()

    def load(self) -> None:
        """Load trades from JSON file."""
        if not self._journal_path.exists():
            self.trades = []
            return

        with open(self._journal_path, encoding="utf-8") as file:
            data = json.load(file)

        self.trades = [Trade.model_validate(item) for item in data]

    def save(self) -> None:
        """Save all trades to JSON file."""
        data = [trade.model_dump(mode="json") for trade in self.trades]
        atomic_write_json(self._journal_path, data)

    def append_trade(self, trade: Trade) -> None:
        """Add a new trade to the journal."""
        self.trades.append(trade)
        self.save()

    def update_trade(self, trade: Trade) -> None:
        """Update an existing trade in the journal."""
        for index, existing in enumerate(self.trades):
            if existing.id == trade.id:
                self.trades[index] = trade
                self.save()
                return
        self.append_trade(trade)

    def summary(self) -> dict[str, Any]:
        """Return performance summary for closed trades."""
        closed = [
            t for t in self.trades
            if t.status == TradeStatus.CLOSED and t.pnl is not None
        ]

        if not closed:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "best_trade": None,
                "worst_trade": None,
            }

        winning = [t for t in closed if t.pnl > 0]
        losing = [t for t in closed if t.pnl <= 0]
        total_pnl = sum(t.pnl for t in closed)
        best = max(closed, key=lambda t: t.pnl)
        worst = min(closed, key=lambda t: t.pnl)

        return {
            "total_trades": len(closed),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": (len(winning) / len(closed)) * 100,
            "total_pnl": total_pnl,
            "best_trade": best,
            "worst_trade": worst,
        }

    def clear(self) -> None:
        """Clear all journal entries."""
        self.trades = []
        self.save()
