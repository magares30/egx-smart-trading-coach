"""Tests for paper trade monitor exits."""

from pathlib import Path

import pytest

from config import settings
from core.models import TradeSide
from core.paper_monitor import (
    ExitReason,
    PaperExitDecision,
    PaperTradeMonitor,
)
from core.portfolio import VirtualPortfolio
from core.trade_journal import TradeJournal


@pytest.fixture
def tmp_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[VirtualPortfolio, TradeJournal]:
    portfolio_path = tmp_path / "portfolio_state.json"
    trades_path = tmp_path / "trades.json"
    monkeypatch.setattr(settings, "PORTFOLIO_STATE_PATH", portfolio_path)
    monkeypatch.setattr(settings, "TRADES_PATH", trades_path)

    portfolio = VirtualPortfolio()
    portfolio.reset()
    journal = TradeJournal()
    journal.clear()
    return portfolio, journal


def _open_buy(
    portfolio: VirtualPortfolio,
    journal: TradeJournal,
    symbol: str = "FWRY",
    entry: float = 6.24,
    stop: float = 6.06,
    take_profit: float = 6.62,
    quantity: int = 1000,
) -> str:
    trade = portfolio.open_trade(
        symbol=symbol,
        side=TradeSide.BUY,
        quantity=quantity,
        entry_price=entry,
        stop_loss=stop,
        take_profit=take_profit,
    )
    journal.append_trade(trade)
    return trade.id


def test_holds_when_no_exit_condition_met(tmp_storage: tuple) -> None:
    portfolio, journal = tmp_storage
    trade_id = _open_buy(portfolio, journal)

    monitor = PaperTradeMonitor(portfolio, journal)
    report = monitor.monitor_open_trades({"FWRY": 6.30})

    assert report.checked_trades == 1
    assert len(report.held_trades) == 1
    assert report.held_trades[0].trade_id == trade_id
    assert "No exit condition met" in report.held_trades[0].reasons
    assert len(portfolio.get_open_trades()) == 1


def test_closes_at_take_profit(tmp_storage: tuple) -> None:
    portfolio, journal = tmp_storage
    trade_id = _open_buy(portfolio, journal, take_profit=6.62)

    monitor = PaperTradeMonitor(portfolio, journal)
    report = monitor.monitor_open_trades({"FWRY": 7.00})

    assert len(report.closed_trades) == 1
    result = report.closed_trades[0]
    assert result.decision == PaperExitDecision.CLOSED
    assert result.exit_reason == ExitReason.TAKE_PROFIT
    assert result.exit_price == 6.62
    assert result.pnl is not None
    assert result.pnl > 0
    assert len(portfolio.get_open_trades()) == 0


def test_closes_at_stop_loss(tmp_storage: tuple) -> None:
    portfolio, journal = tmp_storage
    _open_buy(portfolio, journal, stop=6.06)

    monitor = PaperTradeMonitor(portfolio, journal)
    report = monitor.monitor_open_trades({"FWRY": 5.90})

    assert len(report.closed_trades) == 1
    result = report.closed_trades[0]
    assert result.exit_reason == ExitReason.STOP_LOSS
    assert result.exit_price == 6.06
    assert result.pnl is not None
    assert result.pnl < 0


def test_force_end_of_day_closes_at_latest_price(tmp_storage: tuple) -> None:
    portfolio, journal = tmp_storage
    _open_buy(portfolio, journal)

    monitor = PaperTradeMonitor(portfolio, journal)
    report = monitor.monitor_open_trades(
        {"FWRY": 6.30}, force_end_of_day_exit=True
    )

    assert len(report.closed_trades) == 1
    result = report.closed_trades[0]
    assert result.exit_reason == ExitReason.END_OF_DAY
    assert result.exit_price == 6.30


def test_missing_latest_price_holds_trade(tmp_storage: tuple) -> None:
    portfolio, journal = tmp_storage
    _open_buy(portfolio, journal)

    monitor = PaperTradeMonitor(portfolio, journal)
    report = monitor.monitor_open_trades({})

    assert len(report.held_trades) == 1
    assert "No latest price available" in report.held_trades[0].reasons
    assert len(portfolio.get_open_trades()) == 1


def test_journal_updated_after_close(tmp_storage: tuple) -> None:
    portfolio, journal = tmp_storage
    _open_buy(portfolio, journal, take_profit=6.62)

    monitor = PaperTradeMonitor(portfolio, journal)
    monitor.monitor_open_trades({"FWRY": 7.00})

    summary = journal.summary()
    assert summary["total_trades"] == 1
    assert summary["total_pnl"] > 0
