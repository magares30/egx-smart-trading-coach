"""Tests for automatic paper exits before daily report generation."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from config import settings
from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from core.market_hours import EgxMarketSession, EgxSessionStatus
from core.models import TradeSide, TradeStatus
from core.paper_exit_execution import execute_paper_exits_before_report
from core.portfolio import VirtualPortfolio
from core.trade_journal import TradeJournal


@pytest.fixture
def tmp_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    portfolio_path = tmp_path / "portfolio_state.json"
    trades_path = tmp_path / "trades.json"
    monkeypatch.setattr(settings, "PORTFOLIO_STATE_PATH", portfolio_path)
    monkeypatch.setattr(settings, "TRADES_PATH", trades_path)
    return tmp_path


def _open_session() -> EgxMarketSession:
    return EgxMarketSession(
        is_trading_day=True,
        session_status=EgxSessionStatus.OPEN,
        is_open_for_new_entries=True,
        is_after_close=False,
        cairo_time="2026-07-05 11:00:00",
        note="open",
        paper_entries_enabled=True,
    )


def _closed_session() -> EgxMarketSession:
    return EgxMarketSession(
        is_trading_day=True,
        session_status=EgxSessionStatus.CLOSED,
        is_open_for_new_entries=False,
        is_after_close=True,
        cairo_time="2026-07-05 18:00:00",
        note="closed",
        paper_entries_enabled=False,
    )


def _live_row(
    symbol: str,
    *,
    close: float,
    high: float | None = None,
    low: float | None = None,
) -> LiveSymbolSnapshot:
    prev = close - 0.5
    return LiveSymbolSnapshot(
        symbol=symbol,
        date=date(2026, 7, 5),
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


def _snapshot(*rows: LiveSymbolSnapshot) -> LiveMarketSnapshot:
    return LiveMarketSnapshot(
        as_of_date=date(2026, 7, 5),
        symbols={row.symbol: row for row in rows},
    )


def _open_buy(
    portfolio: VirtualPortfolio,
    journal: TradeJournal,
    symbol: str = "FWRY",
    *,
    entry: float = 6.24,
    stop: float = 6.00,
    target: float = 6.60,
) -> None:
    trade = portfolio.open_trade(
        symbol=symbol,
        side=TradeSide.BUY,
        quantity=100,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        reason="TEST",
    )
    journal.append_trade(trade)


def test_execute_paper_exits_skips_when_market_closed(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    journal = TradeJournal()
    journal.clear()
    _open_buy(portfolio, journal)

    result = execute_paper_exits_before_report(
        _snapshot(_live_row("FWRY", close=6.70, high=6.70)),
        market_session=_closed_session(),
    )

    assert result.closed_count == 0
    assert result.skip_reason == "market=CLOSED"
    assert TradeJournal().trades[0].status == TradeStatus.OPEN


def test_execute_paper_exits_closes_on_take_profit(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    journal = TradeJournal()
    journal.clear()
    _open_buy(portfolio, journal)

    result = execute_paper_exits_before_report(
        _snapshot(_live_row("FWRY", close=6.70, high=6.70)),
        market_session=_open_session(),
    )

    assert result.closed_count == 1
    assert result.skip_reason is None
    assert result.closed_symbols == ["FWRY"]
    assert result.closed_trades[0]["reason"] == "TAKE_PROFIT"
    closed = TradeJournal().trades[0]
    assert closed.status == TradeStatus.CLOSED
    assert closed.pnl is not None
    assert "FWRY" not in VirtualPortfolio().positions


def test_format_paper_exit_telegram_alert_when_closed() -> None:
    from core.paper_exit_execution import format_paper_exit_telegram_alert

    text = format_paper_exit_telegram_alert(
        {
            "paper_exit_execution_closed_count": 1,
            "paper_exit_execution_closed_trades": [
                {
                    "symbol": "FWRY",
                    "reason": "TAKE_PROFIT",
                    "pnl": 38.0,
                    "pnl_percent": 6.09,
                }
            ],
        }
    )
    assert text is not None
    assert "إغلاق ورقي تلقائي" in text
    assert "FWRY" in text
    assert "TAKE_PROFIT" in text


def test_format_paper_exit_telegram_alert_when_none_closed() -> None:
    from core.paper_exit_execution import format_paper_exit_telegram_alert

    assert format_paper_exit_telegram_alert({"paper_exit_execution_closed_count": 0}) is None


def test_execute_paper_exits_holds_when_levels_not_hit(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    journal = TradeJournal()
    journal.clear()
    _open_buy(portfolio, journal)

    result = execute_paper_exits_before_report(
        _snapshot(_live_row("FWRY", close=6.30, high=6.40, low=6.20)),
        market_session=_open_session(),
    )

    assert result.closed_count == 0
    assert result.held_count == 1
    assert TradeJournal().trades[0].status == TradeStatus.OPEN


def test_execute_paper_exits_skips_missing_snapshot(tmp_storage: Path) -> None:
    result = execute_paper_exits_before_report(
        None,
        market_session=_open_session(),
    )

    assert result.closed_count == 0
    assert result.skip_reason == "missing_live_snapshot"
