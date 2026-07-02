"""Tests for live paper trading from EGX strategy scan signals."""

from pathlib import Path

import pytest

from config import settings
from core.live_paper_trader import (
    LivePaperTradeDecision,
    LivePaperTrader,
    LivePaperTradingReport,
)
from core.models import SignalType, TradeSide, TradeSignal
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
    *,
    watch: list[StrategyResult] | None = None,
    max_trades_per_run: int = 3,
    min_confidence_score: int = 75,
    ignore_market_hours: bool = True,
) -> tuple[LivePaperTrader, LivePaperTradingReport, TradeJournal]:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    journal = TradeJournal()
    journal.clear()
    trader = LivePaperTrader(
        portfolio=portfolio,
        trade_journal=journal,
        risk_manager=RiskManager(),
        max_trades_per_run=max_trades_per_run,
        min_confidence_score=min_confidence_score,
        ignore_market_hours=ignore_market_hours,
    )
    report = StrategyReport(
        strategy_name="Trend Join Long",
        results=buy_setups + (watch or []),
        buy_setups=buy_setups,
        watch=watch or [],
        blocked=[],
    )
    result = trader.trade_from_strategy_report(report)
    return trader, result, journal


def test_opens_buy_setup(tmp_storage: Path) -> None:
    trader, report, journal = _run_trader(tmp_storage, [_buy_setup()])

    opened = [item for item in report.results if item.decision == LivePaperTradeDecision.OPENED]
    assert report.opened_count == 1
    assert len(opened) == 1
    assert opened[0].symbol == "FWRY"
    assert "FWRY" in trader._portfolio.positions
    assert len(journal.trades) == 1


def test_skips_watch_signals(tmp_storage: Path) -> None:
    watch = StrategyResult(
        symbol="HRHO",
        decision=StrategyDecision.WATCH,
        signal=_buy_signal("HRHO", 50.0, 48.0, 54.0, confidence=90),
        entry_price=50.0,
        stop_loss=48.0,
        take_profit=54.0,
        risk_reward=2.0,
        confidence_score=90,
        reasons=["Watch only"],
    )
    _, report, journal = _run_trader(tmp_storage, [], watch=[watch])

    assert report.opened_count == 0
    assert report.results == []
    assert len(journal.trades) == 0


def test_skips_low_confidence(tmp_storage: Path) -> None:
    setup = _buy_setup(confidence=70)
    _, report, _ = _run_trader(
        tmp_storage,
        [setup],
        min_confidence_score=75,
    )

    skipped = [item for item in report.results if item.decision == LivePaperTradeDecision.SKIPPED]
    assert report.skipped_count == 1
    assert len(skipped) == 1
    assert skipped[0].reason == "confidence below threshold"


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

    trader = LivePaperTrader(
        portfolio=portfolio,
        trade_journal=journal,
        risk_manager=RiskManager(),
        ignore_market_hours=True,
    )
    report = trader.trade_from_strategy_report(
        StrategyReport(
            strategy_name="Trend Join Long",
            results=[_buy_setup()],
            buy_setups=[_buy_setup()],
            watch=[],
            blocked=[],
        )
    )

    skipped = [item for item in report.results if item.decision == LivePaperTradeDecision.SKIPPED]
    assert len(skipped) == 1
    assert skipped[0].reason == "already open position"
    assert len(journal.trades) == 0


def test_respects_max_trades_per_run(tmp_storage: Path) -> None:
    setups = [
        _buy_setup(symbol="FWRY", confidence=90),
        _buy_setup(
            symbol="HRHO",
            confidence=85,
            signal=_buy_signal("HRHO", 50.0, 48.0, 54.0),
        ),
        _buy_setup(
            symbol="COMI",
            confidence=80,
            signal=_buy_signal("COMI", 80.0, 78.0, 84.0),
        ),
        _buy_setup(
            symbol="TMGH",
            confidence=75,
            signal=_buy_signal("TMGH", 54.0, 52.0, 58.0),
        ),
    ]
    _, report, _ = _run_trader(tmp_storage, setups, max_trades_per_run=2)

    opened = [item for item in report.results if item.decision == LivePaperTradeDecision.OPENED]
    assert report.opened_count == 2
    assert len(opened) == 2


def test_rejects_insufficient_cash(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    journal = TradeJournal()
    journal.clear()
    portfolio.open_trade(
        symbol="LOCK",
        side=TradeSide.BUY,
        quantity=15000,
        entry_price=6.24,
        stop_loss=6.06,
        take_profit=6.62,
    )

    trader = LivePaperTrader(
        portfolio=portfolio,
        trade_journal=journal,
        risk_manager=RiskManager(),
        ignore_market_hours=True,
    )
    report = trader.trade_from_strategy_report(
        StrategyReport(
            strategy_name="Trend Join Long",
            results=[
                _buy_setup(
                    symbol="TMGH",
                    confidence=85,
                    signal=_buy_signal("TMGH", 78.90, 77.20, 82.31, confidence=85),
                )
            ],
            buy_setups=[
                _buy_setup(
                    symbol="TMGH",
                    confidence=85,
                    signal=_buy_signal("TMGH", 78.90, 77.20, 82.31, confidence=85),
                )
            ],
            watch=[],
            blocked=[],
        )
    )

    rejected = [
        item for item in report.results if item.decision == LivePaperTradeDecision.REJECTED
    ]
    assert len(rejected) == 1
    assert rejected[0].symbol == "TMGH"
    assert rejected[0].reason == "insufficient cash"
    assert len(journal.trades) == 0


def test_appends_opened_trade_to_journal(tmp_storage: Path) -> None:
    _, report, journal = _run_trader(
        tmp_storage,
        [_buy_setup(symbol="COMI", signal=_buy_signal("COMI", 78.90, 77.20, 82.31))],
    )

    assert report.opened_count == 1
    assert len(journal.trades) == 1
    assert journal.trades[0].symbol == "COMI"
