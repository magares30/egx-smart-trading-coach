"""Tests for automatic paper trading from strategy signals."""

from pathlib import Path

import pytest

from config import settings
from core.models import SignalType, TradeSide, TradeSignal
from core.paper_trader import AutoPaperTrader, PaperTradeDecision
from core.portfolio import VirtualPortfolio
from core.risk import RiskManager
from core.strategy import StrategyDecision, StrategyReport, StrategyResult
from core.trade_journal import TradeJournal


@pytest.fixture
def tmp_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    portfolio_path = tmp_path / "portfolio_state.json"
    trades_path = tmp_path / "trades.json"
    monkeypatch.setattr(settings, "PORTFOLIO_STATE_PATH", portfolio_path)
    monkeypatch.setattr(settings, "TRADES_PATH", trades_path)
    return tmp_path


def _buy_signal(
    symbol: str = "FWRY",
    entry: float = 6.24,
    stop: float = 6.06,
    take_profit: float = 6.62,
    confidence: int = 85,
) -> TradeSignal:
    return TradeSignal(
        symbol=symbol,
        signal_type=SignalType.BUY_SETUP,
        entry_price=entry,
        stop_loss=stop,
        take_profit=take_profit,
        confidence_score=confidence,
        reasons=["Scanner marked symbol as candidate"],
    )


def _buy_setup(
    symbol: str = "FWRY",
    signal: TradeSignal | None = None,
    confidence: int = 85,
    risk_reward: float = 2.0,
) -> StrategyResult:
    sig = signal or _buy_signal(symbol=symbol, confidence=confidence)
    return StrategyResult(
        symbol=symbol,
        decision=StrategyDecision.BUY_SETUP,
        signal=sig,
        entry_price=sig.entry_price,
        stop_loss=sig.stop_loss,
        take_profit=sig.take_profit,
        risk_reward=risk_reward,
        confidence_score=confidence,
        reasons=["Scanner marked symbol as candidate"],
    )


def _run_trader(
    tmp_storage: Path,
    buy_setups: list[StrategyResult],
    max_trades_per_run: int = 3,
    min_confidence_score: int = 70,
    *,
    ignore_market_hours: bool = True,
) -> tuple[AutoPaperTrader, "PaperTradingReport"]:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    journal = TradeJournal()
    journal.clear()
    trader = AutoPaperTrader(
        portfolio=portfolio,
        journal=journal,
        risk_manager=RiskManager(),
        max_trades_per_run=max_trades_per_run,
        min_confidence_score=min_confidence_score,
        ignore_market_hours=ignore_market_hours,
    )
    report = StrategyReport(
        strategy_name="Trend Join Long",
        results=buy_setups,
        buy_setups=buy_setups,
        watch=[],
        blocked=[],
    )
    result = trader.execute_strategy_report(report)
    return trader, result


def test_opens_trade_from_buy_setup(tmp_storage: Path) -> None:
    trader, report = _run_trader(tmp_storage, [_buy_setup()])

    assert len(report.opened_trades) == 1
    assert report.opened_trades[0].decision == PaperTradeDecision.OPENED
    assert report.opened_trades[0].trade_id is not None
    assert len(trader._journal.trades) == 1
    assert "FWRY" in trader._portfolio.positions


def test_rejects_risk_failure(tmp_storage: Path) -> None:
    bad_signal = _buy_signal(entry=80.0, stop=78.0, take_profit=82.0)
    setup = _buy_setup(symbol="COMI", signal=bad_signal, risk_reward=1.0)
    _, report = _run_trader(tmp_storage, [setup])

    assert len(report.rejected_trades) == 1
    assert len(report.opened_trades) == 0
    assert report.attempted_setups == 1


def test_skips_low_confidence(tmp_storage: Path) -> None:
    setup = _buy_setup(confidence=60)
    _, report = _run_trader(tmp_storage, [setup], min_confidence_score=70)

    assert len(report.skipped_trades) == 1
    assert report.skipped_trades[0].decision == PaperTradeDecision.SKIPPED
    assert "Confidence below threshold" in report.skipped_trades[0].reasons
    assert report.attempted_setups == 0


def test_skips_duplicate_open_position(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    journal = TradeJournal()
    journal.clear()

    portfolio.open_trade(
        symbol="FWRY",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=6.24,
        stop_loss=6.06,
        take_profit=6.60,
    )

    trader = AutoPaperTrader(
        portfolio=portfolio,
        journal=journal,
        risk_manager=RiskManager(),
        ignore_market_hours=True,
    )
    report = trader.execute_strategy_report(
        StrategyReport(
            strategy_name="Trend Join Long",
            results=[_buy_setup()],
            buy_setups=[_buy_setup()],
            watch=[],
            blocked=[],
        )
    )

    assert len(report.skipped_trades) == 1
    assert "Open position already exists for symbol" in report.skipped_trades[0].reasons


def test_respects_max_trades_per_run(tmp_storage: Path) -> None:
    setups = [
        _buy_setup(symbol="FWRY", confidence=90),
        _buy_setup(symbol="HRHO", confidence=85, signal=_buy_signal("HRHO", 50.0, 48.0, 54.0)),
        _buy_setup(symbol="COMI", confidence=80, signal=_buy_signal("COMI", 80.0, 78.0, 84.0)),
        _buy_setup(symbol="TMGH", confidence=75, signal=_buy_signal("TMGH", 54.0, 52.0, 58.0)),
        _buy_setup(symbol="SWDY", confidence=70, signal=_buy_signal("SWDY", 29.0, 28.0, 31.0)),
    ]
    _, report = _run_trader(tmp_storage, setups, max_trades_per_run=2)

    assert report.attempted_setups == 2
    assert len(report.opened_trades) + len(report.rejected_trades) == 2
