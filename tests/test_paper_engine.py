"""Tests for shared paper trade engine helpers."""

from pathlib import Path

import pytest

from config import settings
from core.models import TradeSide
from core.paper_engine import (
    close_paper_trade,
    evaluate_buy_setup_for_open,
    sort_strategy_setups,
)
from core.portfolio import VirtualPortfolio
from core.risk import RiskManager
from core.strategy import StrategyDecision, StrategyResult
from core.trade_journal import TradeJournal
from tests.test_paper_trader import _buy_setup


@pytest.fixture
def tmp_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    portfolio_path = tmp_path / "portfolio_state.json"
    trades_path = tmp_path / "trades.json"
    monkeypatch.setattr(settings, "PORTFOLIO_STATE_PATH", portfolio_path)
    monkeypatch.setattr(settings, "TRADES_PATH", trades_path)
    return tmp_path


def test_sort_strategy_setups_orders_by_confidence_then_rr() -> None:
    setups = [
        _buy_setup(symbol="A", confidence=80, risk_reward=2.0),
        _buy_setup(symbol="B", confidence=90, risk_reward=1.5),
        _buy_setup(symbol="C", confidence=90, risk_reward=2.5),
    ]

    ordered = sort_strategy_setups(setups)

    assert [item.symbol for item in ordered] == ["C", "B", "A"]


def test_evaluate_buy_setup_for_open_skips_duplicate_position(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    portfolio.open_trade(
        symbol="FWRY",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=6.24,
        stop_loss=6.06,
        take_profit=6.60,
    )

    evaluation = evaluate_buy_setup_for_open(
        _buy_setup(),
        portfolio=portfolio,
        risk_manager=RiskManager(),
        min_confidence_score=70,
        ignore_market_hours=True,
    )

    assert evaluation.decision == "SKIPPED"
    assert evaluation.reason == "already open position"


def test_close_paper_trade_syncs_journal(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    journal = TradeJournal()
    journal.clear()

    trade = portfolio.open_trade(
        symbol="FWRY",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=6.24,
        stop_loss=6.06,
        take_profit=6.60,
    )

    closed = close_paper_trade(portfolio, journal, trade.id, exit_price=6.60)

    assert closed.exit_price == 6.60
    assert trade.id not in portfolio.positions
    assert len(journal.trades) == 1
    assert journal.trades[0].exit_price == 6.60
