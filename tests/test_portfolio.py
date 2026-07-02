"""Tests for virtual portfolio and risk management."""

from pathlib import Path

import pytest

from config import settings
from core.models import SignalType, TradeSide, TradeSignal
from core.portfolio import PortfolioError, VirtualPortfolio
from core.risk import RiskManager


def make_signal(
    symbol: str = "COMI",
    entry: float = 80.0,
    stop: float = 78.0,
    take_profit: float = 84.0,
    signal_type: SignalType = SignalType.BUY_SETUP,
) -> TradeSignal:
    return TradeSignal(
        symbol=symbol,
        signal_type=signal_type,
        entry_price=entry,
        stop_loss=stop,
        take_profit=take_profit,
        confidence_score=72,
        reasons=["test signal"],
    )


@pytest.fixture
def tmp_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect storage paths to a temporary directory."""
    portfolio_path = tmp_path / "portfolio_state.json"
    trades_path = tmp_path / "trades.json"
    monkeypatch.setattr(settings, "PORTFOLIO_STATE_PATH", portfolio_path)
    monkeypatch.setattr(settings, "TRADES_PATH", trades_path)
    return tmp_path


def test_open_trade_deducts_cash(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()

    trade = portfolio.open_trade(
        symbol="COMI",
        side=TradeSide.BUY,
        quantity=500,
        entry_price=80.0,
        stop_loss=78.0,
        take_profit=84.0,
        reason="test open",
    )

    assert trade.status.value == "OPEN"
    assert portfolio.cash == 60_000.0
    assert "COMI" in portfolio.positions


def test_close_trade_with_profit(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()

    trade = portfolio.open_trade(
        symbol="COMI",
        side=TradeSide.BUY,
        quantity=500,
        entry_price=80.0,
        stop_loss=78.0,
        take_profit=84.0,
    )

    closed = portfolio.close_trade(trade.id, exit_price=84.0)

    assert closed.pnl == 2_000.0
    assert closed.pnl_percent == pytest.approx(5.0)
    assert portfolio.cash == 102_000.0
    assert portfolio.realized_pnl == 2_000.0
    assert len(portfolio.positions) == 0


def test_risk_rejects_invalid_stop_loss() -> None:
    manager = RiskManager()

    with pytest.raises(ValueError):
        make_signal(entry=80.0, stop=81.0, take_profit=84.0)

    # Bypass Pydantic level validation to test RiskManager stop-loss guard
    signal = TradeSignal.model_construct(
        symbol="COMI",
        signal_type=SignalType.BUY_SETUP,
        entry_price=80.0,
        stop_loss=81.0,
        take_profit=84.0,
        confidence_score=72,
        reasons=[],
        blockers=[],
    )
    decision = manager.evaluate(signal, equity=100_000)

    assert decision.approved is False
    assert any("stop loss" in r.lower() for r in decision.rejection_reasons)


def test_risk_rejects_bad_risk_reward() -> None:
    manager = RiskManager()
    # Risk = 2, reward = 2 → R:R = 1:1 (below 1:2 minimum)
    signal = make_signal(entry=80.0, stop=78.0, take_profit=82.0)

    decision = manager.evaluate(signal, equity=100_000)

    assert decision.approved is False
    assert any("risk/reward" in r.lower() for r in decision.rejection_reasons)


def test_portfolio_rejects_max_open_positions(
    tmp_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MAX_OPEN_POSITIONS", 2)

    portfolio = VirtualPortfolio()
    portfolio.reset()

    portfolio.open_trade(
        symbol="COMI",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=80.0,
        stop_loss=78.0,
        take_profit=84.0,
    )
    portfolio.open_trade(
        symbol="HRHO",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=50.0,
        stop_loss=48.0,
        take_profit=54.0,
    )

    with pytest.raises(PortfolioError, match="Maximum open positions"):
        portfolio.open_trade(
            symbol="SWDY",
            side=TradeSide.BUY,
            quantity=100,
            entry_price=30.0,
            stop_loss=28.0,
            take_profit=34.0,
        )
