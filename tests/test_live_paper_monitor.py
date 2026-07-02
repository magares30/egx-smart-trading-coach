"""Tests for live paper trade monitoring against EGX snapshot OHLC."""

from datetime import date
from pathlib import Path

import pytest

from config import settings
from core.live_paper_monitor import (
    LivePaperExitReason,
    LivePaperMonitor,
    LivePaperMonitorDecision,
)
from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from core.models import TradeSide, TradeStatus
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


def _live_row(
    symbol: str,
    *,
    close: float,
    high: float | None = None,
    low: float | None = None,
    previous_close: float | None = None,
) -> LiveSymbolSnapshot:
    prev = previous_close if previous_close is not None else close - 0.5
    return LiveSymbolSnapshot(
        symbol=symbol,
        date=date(2026, 1, 7),
        previous_close=prev,
        open=prev,
        high=high if high is not None else max(close, prev) + 0.5,
        low=low if low is not None else min(close, prev) - 0.5,
        close=close,
        volume=1000.0,
        change_percent=((close - prev) / prev) * 100,
        volume_ratio=1.0,
        broke_previous_high=(high if high is not None else close) > prev,
    )


def _live_snapshot(*rows: LiveSymbolSnapshot) -> LiveMarketSnapshot:
    return LiveMarketSnapshot(
        as_of_date=date(2026, 1, 7),
        symbols={row.symbol: row for row in rows},
    )


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


def test_closes_at_take_profit(tmp_storage: tuple) -> None:
    portfolio, journal = tmp_storage
    _open_buy(portfolio, journal, take_profit=6.62)

    monitor = LivePaperMonitor(portfolio, journal)
    report = monitor.monitor_from_live_snapshot(
        _live_snapshot(_live_row("FWRY", close=6.55, high=6.70))
    )

    assert report.closed_count == 1
    result = report.results[0]
    assert result.decision == LivePaperMonitorDecision.CLOSED
    assert result.reason == LivePaperExitReason.TAKE_PROFIT
    assert result.exit_price == 6.62
    assert result.pnl is not None
    assert result.pnl > 0
    assert len(portfolio.get_open_trades()) == 0


def test_closes_at_stop_loss(tmp_storage: tuple) -> None:
    portfolio, journal = tmp_storage
    _open_buy(portfolio, journal, stop=6.06)

    monitor = LivePaperMonitor(portfolio, journal)
    report = monitor.monitor_from_live_snapshot(
        _live_snapshot(_live_row("FWRY", close=6.10, low=6.00))
    )

    assert report.closed_count == 1
    result = report.results[0]
    assert result.reason == LivePaperExitReason.STOP_LOSS
    assert result.exit_price == 6.06
    assert result.pnl is not None
    assert result.pnl < 0


def test_holds_when_neither_hit(tmp_storage: tuple) -> None:
    portfolio, journal = tmp_storage
    _open_buy(portfolio, journal)

    monitor = LivePaperMonitor(portfolio, journal)
    report = monitor.monitor_from_live_snapshot(
        _live_snapshot(_live_row("FWRY", close=6.30, high=6.40, low=6.15))
    )

    assert report.held_count == 1
    result = report.results[0]
    assert result.decision == LivePaperMonitorDecision.HELD
    assert result.reason == LivePaperExitReason.HELD
    assert result.current_price == 6.30
    assert len(portfolio.get_open_trades()) == 1


def test_missing_symbol_returns_held_warning(tmp_storage: tuple) -> None:
    portfolio, journal = tmp_storage
    _open_buy(portfolio, journal, symbol="HRHO")

    monitor = LivePaperMonitor(portfolio, journal)
    report = monitor.monitor_from_live_snapshot(
        _live_snapshot(_live_row("FWRY", close=6.30))
    )

    assert report.held_count == 1
    result = report.results[0]
    assert result.symbol == "HRHO"
    assert result.reason == LivePaperExitReason.MISSING_SYMBOL
    assert any("HRHO" in warning for warning in report.warnings)
    assert len(portfolio.get_open_trades()) == 1


def test_updates_journal_and_portfolio_on_close(tmp_storage: tuple) -> None:
    portfolio, journal = tmp_storage
    _open_buy(portfolio, journal, take_profit=6.62)

    monitor = LivePaperMonitor(portfolio, journal)
    monitor.monitor_from_live_snapshot(
        _live_snapshot(_live_row("FWRY", close=6.70, high=6.75))
    )

    summary = journal.summary()
    assert summary["total_trades"] == 1
    assert summary["total_pnl"] > 0
    assert journal.trades[0].status == TradeStatus.CLOSED


def test_does_not_close_already_closed_trade(tmp_storage: tuple) -> None:
    portfolio, journal = tmp_storage
    trade_id = _open_buy(portfolio, journal, take_profit=6.62)
    portfolio.close_trade(trade_id, 6.62)
    journal.update_trade(portfolio.trades[trade_id])

    monitor = LivePaperMonitor(portfolio, journal)
    report = monitor.monitor_from_live_snapshot(
        _live_snapshot(_live_row("FWRY", close=7.00, high=7.10))
    )

    assert report.results == []
    assert report.closed_count == 0
    assert report.held_count == 0


def test_take_profit_priority_over_stop_loss(tmp_storage: tuple) -> None:
    portfolio, journal = tmp_storage
    _open_buy(portfolio, journal, entry=10.0, stop=9.0, take_profit=12.0)

    monitor = LivePaperMonitor(portfolio, journal)
    report = monitor.monitor_from_live_snapshot(
        _live_snapshot(
            _live_row("FWRY", close=10.5, high=13.0, low=8.0, previous_close=10.0)
        )
    )

    assert report.closed_count == 1
    result = report.results[0]
    assert result.reason == LivePaperExitReason.TAKE_PROFIT
    assert result.exit_price == 12.0
