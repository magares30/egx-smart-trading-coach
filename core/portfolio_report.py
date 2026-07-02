"""Build and save EGX paper portfolio reports."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from config import settings
from core.decision_labels import (
    classify_open_position_decision,
    format_position_decision_line,
)
from core.exit_plan import (
    classify_position_exit_plan,
    format_exit_plan_line,
)
from core.live_snapshot import LiveMarketSnapshot
from core.market_hours import EgxMarketSession, detect_egx_market_session
from core.models import Position, Trade, TradeSide, TradeStatus
from core.portfolio import VirtualPortfolio
from core.trade_journal import TradeJournal

REPORT_SOURCE_PAPER_PORTFOLIO = "EGX Paper Portfolio"
SAFETY_NOTICE = "Paper trading only. No real orders were placed."
EMPTY_TRADES_MESSAGE = "No paper trades found yet."
EMPTY_PORTFOLIO_MESSAGE = "Portfolio is empty."


class PortfolioReportSection(BaseModel):
    title: str
    lines: list[str] = Field(default_factory=list)


class PortfolioReport(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    snapshot_date: date | None = None
    price_source: str | None = None
    is_empty: bool = False
    safety_notice: str = SAFETY_NOTICE
    sections: list[PortfolioReportSection] = Field(default_factory=list)


def _format_money(amount: float) -> str:
    sign = "+" if amount > 0 else ""
    return f"{sign}{amount:,.2f} {settings.BASE_CURRENCY}"


def _format_pnl(amount: float | None, percent: float | None = None) -> str:
    if amount is None:
        return "unavailable"
    line = _format_money(amount)
    if percent is not None:
        line += f" ({percent:+.2f}%)"
    return line


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _open_risk_amount(trade: Trade) -> float:
    if trade.side == TradeSide.BUY:
        return abs(trade.entry_price - trade.stop_loss) * trade.quantity
    return abs(trade.stop_loss - trade.entry_price) * trade.quantity


def _unrealized_for_trade(
    trade: Trade,
    latest_prices: dict[str, float] | None,
) -> tuple[float | None, float | None]:
    if not latest_prices:
        return None, None
    current_price = latest_prices.get(trade.symbol)
    if current_price is None:
        return None, None
    if trade.side == TradeSide.BUY:
        pnl = (current_price - trade.entry_price) * trade.quantity
    else:
        pnl = (trade.entry_price - current_price) * trade.quantity
    cost_basis = trade.entry_price * trade.quantity
    pnl_percent = (pnl / cost_basis) * 100 if cost_basis else None
    return pnl, pnl_percent


def _performance_stats(closed_trades: list[Trade]) -> dict[str, object]:
    if not closed_trades:
        return {
            "win_rate": 0.0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "average_win": None,
            "average_loss": None,
            "best_trade": None,
            "worst_trade": None,
            "total_closed_pnl": 0.0,
        }

    wins = [trade for trade in closed_trades if trade.pnl is not None and trade.pnl > 0]
    losses = [trade for trade in closed_trades if trade.pnl is not None and trade.pnl < 0]
    breakeven = [
        trade for trade in closed_trades if trade.pnl is not None and trade.pnl == 0
    ]
    total_closed_pnl = sum(trade.pnl or 0.0 for trade in closed_trades)
    best_trade = max(closed_trades, key=lambda trade: trade.pnl or 0.0)
    worst_trade = min(closed_trades, key=lambda trade: trade.pnl or 0.0)

    return {
        "win_rate": (len(wins) / len(closed_trades)) * 100,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "average_win": (
            sum(trade.pnl or 0.0 for trade in wins) / len(wins) if wins else None
        ),
        "average_loss": (
            sum(trade.pnl or 0.0 for trade in losses) / len(losses) if losses else None
        ),
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "total_closed_pnl": total_closed_pnl,
    }


def build_latest_prices_from_snapshot(
    live_snapshot: LiveMarketSnapshot,
) -> dict[str, float]:
    return {symbol: snap.close for symbol, snap in live_snapshot.symbols.items()}


def paper_portfolio_storage_exists() -> bool:
    """Return True when local paper portfolio storage files are present."""
    return (
        settings.PORTFOLIO_STATE_PATH.exists()
        or settings.TRADES_PATH.exists()
    )


def load_portfolio_for_marking() -> VirtualPortfolio | None:
    """Load portfolio state when present without creating new storage files."""
    from core.cloud_state_store import hydrate_local_storage_from_cloud

    hydrate_local_storage_from_cloud()
    path = settings.PORTFOLIO_STATE_PATH
    if not path.exists():
        return None
    try:
        return VirtualPortfolio(state_path=path)
    except (json.JSONDecodeError, KeyError, OSError, ValidationError, TypeError, ValueError):
        return None


def _format_plain_amount(amount: float, *, signed: bool = False) -> str:
    if signed:
        sign = "+" if amount > 0 else ""
        return f"{sign}{amount:,.2f}"
    return f"{amount:,.2f}"


def _format_plain_pnl(amount: float | None, percent: float | None = None) -> str:
    if amount is None:
        return "n/a"
    line = _format_plain_amount(amount, signed=True)
    if percent is not None:
        line += f" ({percent:+.2f}%)"
    return line


def _mark_position(
    position: Position,
    latest_prices: dict[str, float] | None,
) -> dict[str, object]:
    """Mark a single open position to market."""
    quantity = position.quantity
    avg_entry = position.avg_entry_price
    cost_basis = quantity * avg_entry
    current_price = (latest_prices or {}).get(position.symbol)
    warning: str | None = None

    if quantity <= 0:
        warning = "zero quantity"
        return {
            "symbol": position.symbol,
            "quantity": quantity,
            "avg_entry_price": avg_entry,
            "stop_loss": position.stop_loss,
            "take_profit": position.take_profit,
            "current_price": current_price,
            "market_value": None,
            "cost_basis": cost_basis,
            "unrealized_pnl": None,
            "unrealized_pnl_pct": None,
            "status": "OPEN",
            "warning": warning,
        }

    if current_price is None:
        warning = "current price unavailable"
        return {
            "symbol": position.symbol,
            "quantity": quantity,
            "avg_entry_price": avg_entry,
            "stop_loss": position.stop_loss,
            "take_profit": position.take_profit,
            "current_price": None,
            "market_value": None,
            "cost_basis": cost_basis,
            "unrealized_pnl": None,
            "unrealized_pnl_pct": None,
            "status": "OPEN",
            "warning": warning,
        }

    market_value = quantity * current_price
    unrealized_pnl = market_value - cost_basis
    unrealized_pnl_pct = (
        (unrealized_pnl / cost_basis) * 100 if cost_basis else None
    )
    return {
        "symbol": position.symbol,
        "quantity": quantity,
        "avg_entry_price": avg_entry,
        "stop_loss": position.stop_loss,
        "take_profit": position.take_profit,
        "current_price": current_price,
        "market_value": market_value,
        "cost_basis": cost_basis,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "status": "OPEN",
        "warning": warning,
    }


def build_daily_report_paper_portfolio(
    portfolio: VirtualPortfolio | None,
    *,
    latest_prices: dict[str, float] | None = None,
    storage_available: bool | None = None,
    market_session: EgxMarketSession | None = None,
) -> tuple[list[str], dict[str, object]]:
    """Build Paper Portfolio section lines and JSON payload for the daily report."""
    session = market_session or detect_egx_market_session()
    storage_present = (
        storage_available
        if storage_available is not None
        else paper_portfolio_storage_exists()
    )
    if not storage_present:
        payload: dict[str, object] = {
            "available": False,
            "message": "No paper portfolio data found.",
            "cash": None,
            "open_positions_count": 0,
            "cost_basis": 0.0,
            "market_value": None,
            "unrealized_pnl": None,
            "unrealized_pnl_pct": None,
            "total_equity": None,
            "positions": [],
        }
        return (
            ["- No paper portfolio data found."],
            payload,
        )

    if portfolio is None:
        payload = {
            "available": False,
            "message": "Paper portfolio storage is unreadable.",
            "cash": None,
            "open_positions_count": 0,
            "cost_basis": 0.0,
            "market_value": None,
            "unrealized_pnl": None,
            "unrealized_pnl_pct": None,
            "total_equity": None,
            "positions": [],
        }
        return (
            ["- Paper portfolio storage is unreadable."],
            payload,
        )

    open_positions = sorted(
        portfolio.positions.values(),
        key=lambda position: position.symbol,
    )
    marked_positions = [
        _mark_position(position, latest_prices) for position in open_positions
    ]

    if not marked_positions:
        payload = {
            "available": True,
            "cash": portfolio.cash,
            "open_positions_count": 0,
            "cost_basis": 0.0,
            "market_value": 0.0,
            "unrealized_pnl": 0.0,
            "unrealized_pnl_pct": 0.0,
            "total_equity": portfolio.cash,
            "positions": [],
        }
        return (
            [
                "- Open Positions: 0",
                "- No open paper positions to mark.",
            ],
            payload,
        )

    total_cost_basis = sum(
        float(position["cost_basis"]) for position in marked_positions
    )
    marked_with_prices = [
        position
        for position in marked_positions
        if position.get("current_price") is not None
    ]
    total_market_value = sum(
        float(position["market_value"])
        for position in marked_with_prices
        if position.get("market_value") is not None
    )
    marked_cost_basis = sum(
        float(position["cost_basis"]) for position in marked_with_prices
    )
    total_unrealized = total_market_value - marked_cost_basis
    total_unrealized_pct = (
        (total_unrealized / marked_cost_basis) * 100
        if marked_cost_basis
        else None
    )
    total_equity = portfolio.cash + total_market_value

    lines = [
        f"- Cash: {_format_plain_amount(portfolio.cash)}",
        f"- Open Positions: {len(marked_positions)}",
        f"- Cost Basis: {_format_plain_amount(total_cost_basis)}",
    ]
    if marked_with_prices and len(marked_with_prices) == len(marked_positions):
        lines.extend(
            [
                f"- Market Value: {_format_plain_amount(total_market_value)}",
                (
                    "- Unrealized P&L: "
                    f"{_format_plain_pnl(total_unrealized, total_unrealized_pct)}"
                ),
                f"- Total Equity: {_format_plain_amount(total_equity)}",
            ]
        )
    else:
        lines.append("- Market Value: n/a (missing snapshot prices)")
        lines.append("- Unrealized P&L: n/a")
        if marked_with_prices:
            lines.append(
                f"- Partial market value: {_format_plain_amount(total_market_value)}"
            )
        lines.append(f"- Cash available for equity: {_format_plain_amount(portfolio.cash)}")

    lines.append("")
    lines.append("Open Positions:")
    for index, position in enumerate(marked_positions, start=1):
        symbol = str(position["symbol"])
        quantity = int(position["quantity"])
        avg_entry = float(position["avg_entry_price"])
        current_price = position.get("current_price")
        stop_loss = position.get("stop_loss")
        take_profit = position.get("take_profit")
        warning = position.get("warning")
        position_decision = classify_open_position_decision(
            symbol=symbol,
            current_price=float(current_price) if current_price is not None else None,
            stop_loss=float(stop_loss) if stop_loss is not None else 0.0,
            take_profit=float(take_profit) if take_profit is not None else 0.0,
            session=session,
        )
        exit_plan = classify_position_exit_plan(
            symbol=symbol,
            entry_price=avg_entry,
            current_price=float(current_price) if current_price is not None else None,
            stop_loss=float(stop_loss) if stop_loss is not None else None,
            take_profit=float(take_profit) if take_profit is not None else None,
            session=session,
        )
        position["decision"] = position_decision.label.value
        position["decision_explanation"] = position_decision.explanation
        position["executable_now"] = position_decision.executable_now
        if position_decision.review_timing is not None:
            position["review_timing"] = position_decision.review_timing
        position["exit_plan"] = exit_plan.label.value
        position["exit_plan_explanation"] = exit_plan.explanation
        position["exit_executable_now"] = exit_plan.exit_executable_now
        if exit_plan.exit_timing is not None:
            position["exit_timing"] = exit_plan.exit_timing
        if current_price is None:
            lines.append(
                f"{index}. {symbol} | Qty {quantity} | Avg {avg_entry:.2f} | "
                "Current n/a | Value n/a | P&L n/a"
                + (f" ({warning})" if warning else " (price unavailable)")
            )
            lines.append(format_position_decision_line(position_decision))
            lines.append(format_exit_plan_line(exit_plan))
            continue
        market_value = float(position["market_value"])
        unrealized_pnl = float(position["unrealized_pnl"])
        unrealized_pct = position.get("unrealized_pnl_pct")
        lines.append(
            f"{index}. {symbol} | Qty {quantity} | Avg {avg_entry:.2f} | "
            f"Current {float(current_price):.2f} | "
            f"Value {_format_plain_amount(market_value)} | "
            f"P&L {_format_plain_pnl(unrealized_pnl, float(unrealized_pct) if unrealized_pct is not None else None)}"
        )
        lines.append(format_position_decision_line(position_decision))
        lines.append(format_exit_plan_line(exit_plan))

    payload = {
        "available": True,
        "cash": portfolio.cash,
        "open_positions_count": len(marked_positions),
        "cost_basis": total_cost_basis,
        "market_value": total_market_value if marked_with_prices else None,
        "unrealized_pnl": total_unrealized if marked_with_prices else None,
        "unrealized_pnl_pct": total_unrealized_pct if marked_with_prices else None,
        "total_equity": total_equity if marked_with_prices else None,
        "positions": marked_positions,
    }
    return lines, payload


def load_trade_journal_for_report() -> TradeJournal | None:
    """Load trade journal when present without creating new storage files."""
    from core.cloud_state_store import hydrate_local_storage_from_cloud

    hydrate_local_storage_from_cloud()
    path = settings.TRADES_PATH
    if not path.exists():
        return None
    try:
        return TradeJournal(journal_path=path)
    except (json.JSONDecodeError, OSError, ValidationError, TypeError, ValueError):
        return None


def _open_positions_cost_basis(portfolio: VirtualPortfolio) -> float:
    return sum(
        position.quantity * position.avg_entry_price
        for position in portfolio.positions.values()
    )


def _resolve_initial_capital(
    portfolio: VirtualPortfolio,
    *,
    open_cost_basis: float,
    realized_pnl: float,
) -> tuple[float | None, bool]:
    if portfolio.initial_capital > 0:
        return portfolio.initial_capital, False

    inferred = portfolio.cash + open_cost_basis - realized_pnl
    if inferred > 0:
        return inferred, True
    return None, False


def _closed_trades_for_analytics(
    portfolio: VirtualPortfolio | None,
    journal: TradeJournal | None,
) -> list[Trade]:
    if journal is not None:
        journal_closed = [
            trade
            for trade in journal.trades
            if trade.status == TradeStatus.CLOSED and trade.pnl is not None
        ]
        if journal_closed:
            return journal_closed

    if portfolio is not None:
        return [
            trade
            for trade in portfolio.trades.values()
            if trade.status == TradeStatus.CLOSED and trade.pnl is not None
        ]
    return []


def _trade_summary_for_json(trade: Trade | None) -> dict[str, object] | None:
    if trade is None:
        return None
    return {
        "symbol": trade.symbol,
        "pnl": trade.pnl,
        "pnl_percent": trade.pnl_percent,
        "quantity": trade.quantity,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
    }


def _format_trade_highlight(trade: Trade | None) -> str:
    if trade is None or trade.pnl is None:
        return "n/a"
    return f"{trade.symbol} {_format_plain_amount(trade.pnl, signed=True)}"


def build_daily_report_paper_trading_performance(
    portfolio: VirtualPortfolio | None,
    journal: TradeJournal | None,
    *,
    latest_prices: dict[str, float] | None = None,
    paper_portfolio_payload: dict[str, object] | None = None,
    storage_available: bool | None = None,
) -> tuple[list[str], dict[str, object]]:
    """Build Paper Trading Performance section lines and JSON payload."""
    storage_present = (
        storage_available
        if storage_available is not None
        else paper_portfolio_storage_exists()
    )
    if not storage_present:
        payload: dict[str, object] = {
            "available": False,
            "message": "No paper portfolio data found.",
            "initial_capital": None,
            "initial_capital_inferred": False,
            "current_equity": None,
            "realized_pnl": None,
            "unrealized_pnl": None,
            "total_pnl": None,
            "total_return_pct": None,
            "closed_trades_count": 0,
            "open_positions_count": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": None,
            "average_win": None,
            "average_loss": None,
            "best_trade": None,
            "worst_trade": None,
        }
        return (["- No paper portfolio data found."], payload)

    if portfolio is None:
        payload = {
            "available": False,
            "message": "Paper portfolio storage is unreadable.",
            "initial_capital": None,
            "initial_capital_inferred": False,
            "current_equity": None,
            "realized_pnl": None,
            "unrealized_pnl": None,
            "total_pnl": None,
            "total_return_pct": None,
            "closed_trades_count": 0,
            "open_positions_count": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": None,
            "average_win": None,
            "average_loss": None,
            "best_trade": None,
            "worst_trade": None,
        }
        return (["- Paper portfolio storage is unreadable."], payload)

    open_cost_basis = _open_positions_cost_basis(portfolio)
    closed_trades = _closed_trades_for_analytics(portfolio, journal)
    realized_pnl = portfolio.realized_pnl
    if closed_trades:
        realized_pnl = sum(trade.pnl or 0.0 for trade in closed_trades)

    initial_capital, initial_capital_inferred = _resolve_initial_capital(
        portfolio,
        open_cost_basis=open_cost_basis,
        realized_pnl=realized_pnl,
    )

    portfolio_payload = paper_portfolio_payload or {}
    open_positions_count = int(
        portfolio_payload.get("open_positions_count", len(portfolio.positions))
    )
    market_value = portfolio_payload.get("market_value")
    unrealized_pnl = portfolio_payload.get("unrealized_pnl")
    current_equity = portfolio_payload.get("total_equity")

    if market_value is None and latest_prices:
        marked_positions = [
            _mark_position(position, latest_prices)
            for position in portfolio.positions.values()
        ]
        priced_positions = [
            position
            for position in marked_positions
            if position.get("current_price") is not None
        ]
        if priced_positions and len(priced_positions) == len(marked_positions):
            market_value = sum(
                float(position["market_value"])
                for position in priced_positions
                if position.get("market_value") is not None
            )
            marked_cost_basis = sum(
                float(position["cost_basis"]) for position in priced_positions
            )
            unrealized_pnl = float(market_value) - marked_cost_basis

    if market_value is not None:
        current_equity = portfolio.cash + float(market_value)
    elif current_equity is None:
        current_equity = portfolio.cash + open_cost_basis
        unrealized_pnl = 0.0

    unrealized_value = float(unrealized_pnl) if unrealized_pnl is not None else 0.0
    total_pnl = realized_pnl + unrealized_value
    total_return_pct = (
        (total_pnl / initial_capital) * 100 if initial_capital else None
    )

    stats = _performance_stats(closed_trades)
    winning_trades = int(stats["wins"])
    losing_trades = int(stats["losses"])
    win_rate = float(stats["win_rate"]) if closed_trades else None
    average_win = stats["average_win"]
    average_loss = stats["average_loss"]
    best_trade = stats["best_trade"]
    worst_trade = stats["worst_trade"]
    best_trade_obj = best_trade if isinstance(best_trade, Trade) else None
    worst_trade_obj = worst_trade if isinstance(worst_trade, Trade) else None

    initial_capital_line = (
        f"- Initial Capital: {_format_plain_amount(initial_capital)} (inferred)"
        if initial_capital is not None and initial_capital_inferred
        else (
            f"- Initial Capital: {_format_plain_amount(initial_capital)}"
            if initial_capital is not None
            else "- Initial Capital: n/a"
        )
    )
    lines = [
        initial_capital_line,
        (
            "- Current Equity: "
            + (
                _format_plain_amount(float(current_equity))
                if current_equity is not None
                else "n/a"
            )
        ),
        (
            "- Total P&L: "
            + _format_plain_pnl(total_pnl, total_return_pct)
        ),
        f"- Realized P&L: {_format_plain_amount(realized_pnl, signed=True)}",
        f"- Unrealized P&L: {_format_plain_pnl(unrealized_value)}",
        f"- Closed Trades: {len(closed_trades)}",
        f"- Open Positions: {open_positions_count}",
    ]
    if closed_trades:
        lines.extend(
            [
                f"- Winning Trades: {winning_trades}",
                f"- Losing Trades: {losing_trades}",
                (
                    "- Win Rate: "
                    + (f"{win_rate:.2f}%" if win_rate is not None else "n/a")
                ),
                (
                    "- Average Win: "
                    + (
                        _format_plain_amount(float(average_win), signed=True)
                        if average_win is not None
                        else "n/a"
                    )
                ),
                (
                    "- Average Loss: "
                    + (
                        _format_plain_amount(float(average_loss), signed=True)
                        if average_loss is not None
                        else "n/a"
                    )
                ),
                f"- Best Trade: {_format_trade_highlight(best_trade_obj)}",
                f"- Worst Trade: {_format_trade_highlight(worst_trade_obj)}",
            ]
        )
    else:
        lines.extend(
            [
                "- Win Rate: n/a",
                "- Best Trade: n/a",
                "- Worst Trade: n/a",
            ]
        )

    payload = {
        "available": True,
        "initial_capital": initial_capital,
        "initial_capital_inferred": initial_capital_inferred,
        "current_cash": portfolio.cash,
        "open_positions_market_value": market_value,
        "current_equity": current_equity,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_value,
        "total_pnl": total_pnl,
        "total_return_pct": total_return_pct,
        "closed_trades_count": len(closed_trades),
        "open_positions_count": open_positions_count,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": win_rate,
        "average_win": average_win,
        "average_loss": average_loss,
        "best_trade": _trade_summary_for_json(best_trade_obj),
        "worst_trade": _trade_summary_for_json(worst_trade_obj),
    }
    return lines, payload


def _all_open_trades_have_prices(
    open_trades: list[Trade],
    latest_prices: dict[str, float] | None,
) -> bool:
    if not open_trades:
        return latest_prices is not None
    if not latest_prices:
        return False
    return all(trade.symbol in latest_prices for trade in open_trades)


class PortfolioReportBuilder:
    """Build readable portfolio reports from portfolio state and trade journal."""

    def build(
        self,
        portfolio: VirtualPortfolio,
        journal: TradeJournal,
        *,
        latest_prices: dict[str, float] | None = None,
        price_source: str | None = None,
        snapshot_date: date | None = None,
    ) -> PortfolioReport:
        all_trades = list(journal.trades)
        open_trades = [
            trade for trade in all_trades if trade.status == TradeStatus.OPEN
        ]
        closed_trades = [
            trade
            for trade in all_trades
            if trade.status == TradeStatus.CLOSED and trade.pnl is not None
        ]
        is_empty = len(all_trades) == 0
        marks_available = _all_open_trades_have_prices(open_trades, latest_prices)

        snapshot = portfolio.get_snapshot(latest_prices if marks_available else None)
        exposure = 0.0
        for trade in open_trades:
            current_price = (latest_prices or {}).get(trade.symbol, trade.entry_price)
            exposure += current_price * trade.quantity

        if marks_available:
            equity_line = f"- Estimated total equity: {_format_money(snapshot.equity)}"
        else:
            cost_basis = sum(
                trade.entry_price * trade.quantity for trade in open_trades
            )
            estimated_equity = snapshot.cash + cost_basis
            equity_line = (
                "- Estimated total equity: "
                f"{_format_money(estimated_equity)} (at entry cost; marks unavailable)"
            )

        summary_lines = [
            f"- Starting cash: {_format_money(portfolio.initial_capital)}",
            f"- Current cash: {_format_money(snapshot.cash)}",
            f"- Total realized PnL: {_format_money(snapshot.realized_pnl)}",
            (
                "- Total unrealized PnL: "
                f"{_format_pnl(snapshot.unrealized_pnl) if marks_available else 'unavailable'}"
            ),
            equity_line,
            f"- Current exposure: {_format_money(exposure)}",
            f"- Open positions: {len(open_trades)}",
            f"- Closed trades: {len(closed_trades)}",
        ]
        if price_source:
            summary_lines.append(f"- Price source: {price_source}")
        if snapshot_date is not None:
            summary_lines.append(f"- Snapshot date: {snapshot_date.isoformat()}")

        open_lines: list[str] = []
        if open_trades:
            for index, trade in enumerate(open_trades, start=1):
                current_price = (latest_prices or {}).get(trade.symbol)
                unrealized_pnl, unrealized_pct = _unrealized_for_trade(
                    trade,
                    latest_prices,
                )
                open_lines.extend(
                    [
                        (
                            f"{index}. {trade.symbol} | {trade.status.value} | "
                            f"Qty {trade.quantity} | Entry {trade.entry_price:.2f}"
                        ),
                        (
                            f"   Current price: "
                            f"{current_price:.2f}"
                            if current_price is not None
                            else "   Current price: unavailable"
                        ),
                        (
                            f"   Stop {trade.stop_loss:.2f} | "
                            f"Target {trade.take_profit:.2f}"
                        ),
                        (
                            f"   Unrealized PnL: "
                            f"{_format_pnl(unrealized_pnl, unrealized_pct)}"
                        ),
                        (
                            f"   Open risk: "
                            f"{_format_money(_open_risk_amount(trade))}"
                        ),
                        f"   Opened: {_format_datetime(trade.opened_at)}",
                    ]
                )
        else:
            open_lines = ["- (none)"]

        closed_lines: list[str] = []
        if closed_trades:
            for index, trade in enumerate(
                sorted(
                    closed_trades,
                    key=lambda item: item.closed_at or item.opened_at,
                    reverse=True,
                ),
                start=1,
            ):
                exit_reason = trade.notes.strip() or trade.reason.strip() or "Closed"
                closed_lines.extend(
                    [
                        (
                            f"{index}. {trade.symbol} | Qty {trade.quantity} | "
                            f"Entry {trade.entry_price:.2f} | "
                            f"Exit {trade.exit_price:.2f}"
                            if trade.exit_price is not None
                            else f"{index}. {trade.symbol} | Qty {trade.quantity}"
                        ),
                        f"   Realized PnL: {_format_pnl(trade.pnl, trade.pnl_percent)}",
                        f"   Exit reason: {exit_reason}",
                        (
                            f"   Opened: {_format_datetime(trade.opened_at)} | "
                            f"Closed: {_format_datetime(trade.closed_at)}"
                        ),
                    ]
                )
        else:
            closed_lines = ["- (none)"]

        stats = _performance_stats(closed_trades)
        best_trade = stats["best_trade"]
        worst_trade = stats["worst_trade"]
        performance_lines = [
            f"- Win rate: {stats['win_rate']:.1f}%",
            (
                f"- Wins / losses / breakeven: "
                f"{stats['wins']} / {stats['losses']} / {stats['breakeven']}"
            ),
            (
                "- Average win: "
                f"{_format_money(stats['average_win']) if stats['average_win'] is not None else 'n/a'}"
            ),
            (
                "- Average loss: "
                f"{_format_money(stats['average_loss']) if stats['average_loss'] is not None else 'n/a'}"
            ),
            (
                "- Best trade: "
                + (
                    f"{best_trade.symbol} ({_format_pnl(best_trade.pnl, best_trade.pnl_percent)})"
                    if isinstance(best_trade, Trade)
                    else "n/a"
                )
            ),
            (
                "- Worst trade: "
                + (
                    f"{worst_trade.symbol} ({_format_pnl(worst_trade.pnl, worst_trade.pnl_percent)})"
                    if isinstance(worst_trade, Trade)
                    else "n/a"
                )
            ),
            f"- Total closed PnL: {_format_money(float(stats['total_closed_pnl']))}",
        ]

        sections = [
            PortfolioReportSection(title="Portfolio Summary", lines=summary_lines),
            PortfolioReportSection(title="Open Positions", lines=open_lines),
            PortfolioReportSection(title="Closed Trades", lines=closed_lines),
            PortfolioReportSection(title="Performance Stats", lines=performance_lines),
        ]

        return PortfolioReport(
            snapshot_date=snapshot_date,
            price_source=price_source,
            is_empty=is_empty,
            sections=sections,
        )


def format_portfolio_report_text(report: PortfolioReport) -> str:
    """Render a portfolio report as plain text."""
    lines = [
        "=== EGX Paper Portfolio Report ===",
        report.safety_notice,
        "",
    ]
    if report.is_empty:
        lines.append(EMPTY_TRADES_MESSAGE)
        lines.append(EMPTY_PORTFOLIO_MESSAGE)
        lines.append("")

    for section in report.sections:
        lines.append(f"{section.title}:")
        lines.extend(section.lines)
        lines.append("")

    lines.append(report.safety_notice)
    return "\n".join(lines).rstrip() + "\n"


def save_portfolio_report(
    report: PortfolioReport,
    reports_dir: Path,
) -> tuple[Path, Path]:
    """Save a portfolio report as timestamped text and JSON files."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = report.created_at.strftime("%Y%m%d_%H%M%S")
    txt_path = reports_dir / f"egx_portfolio_report_{timestamp}.txt"
    json_path = reports_dir / f"egx_portfolio_report_{timestamp}.json"

    txt_path.write_text(format_portfolio_report_text(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return txt_path, json_path
