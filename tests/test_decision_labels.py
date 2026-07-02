"""Tests for buy/sell decision labels."""

from datetime import date

import pytest

from config import settings
from core.decision_labels import (
    DecisionLabel,
    REVIEW_TIMING_NEXT_OPEN_SESSION,
    REVIEW_TIMING_NOW,
    build_decision_summary,
    build_executive_action_from_decisions,
    classify_open_position_decision,
    classify_strategy_signal_decision,
)
from core.daily_report import DailyReportBuilder
from core.live_snapshot import LiveMarketSnapshot
from core.market_hours import (
    detect_egx_market_session,
    sample_closed_market_datetime,
    sample_open_market_datetime,
    sample_weekend_market_datetime,
)
from core.market_mood import MarketMood, MarketMoodResult
from core.models import TradeSide
from core.portfolio import VirtualPortfolio
from core.scanner import ScannerReport
from core.strategy import StrategyDecision, StrategyReport, StrategyResult
from core.talib_technical import TalibTechnicalConfig
from core.trade_journal import TradeJournal
from tests.test_daily_report import _live_row, _strategy_result


def _buy_setup(symbol: str = "ELKA") -> StrategyResult:
    return _strategy_result(symbol, StrategyDecision.BUY_SETUP)


def test_closed_market_signal_becomes_watch_next_session() -> None:
    session = detect_egx_market_session(now=sample_closed_market_datetime())
    decision = classify_strategy_signal_decision(
        _buy_setup(),
        session=session,
    )

    assert decision.label == DecisionLabel.WATCH_NEXT_SESSION
    assert decision.explanation == "market closed; review next session only"


def test_open_market_buy_setup_becomes_buy_setup_label() -> None:
    session = detect_egx_market_session(now=sample_open_market_datetime())
    decision = classify_strategy_signal_decision(
        _buy_setup(),
        session=session,
    )

    assert decision.label == DecisionLabel.BUY_SETUP


def test_open_position_between_stop_and_target_is_hold() -> None:
    session = detect_egx_market_session(now=sample_open_market_datetime())
    decision = classify_open_position_decision(
        symbol="ABUK",
        current_price=10.5,
        stop_loss=10.0,
        take_profit=11.0,
        session=session,
    )

    assert decision.label == DecisionLabel.HOLD


def test_target_reached_while_market_open_is_executable_now() -> None:
    session = detect_egx_market_session(now=sample_open_market_datetime())
    decision = classify_open_position_decision(
        symbol="ABUK",
        current_price=11.5,
        stop_loss=10.0,
        take_profit=11.0,
        session=session,
    )

    assert decision.label == DecisionLabel.SELL_ALERT_TARGET
    assert decision.executable_now is True
    assert decision.review_timing == REVIEW_TIMING_NOW
    assert (
        decision.explanation
        == "Price reached or crossed target; review exit during open market"
    )


def test_stop_reached_while_market_closed_uses_next_open_wording() -> None:
    session = detect_egx_market_session(now=sample_closed_market_datetime())
    decision = classify_open_position_decision(
        symbol="ABUK",
        current_price=9.5,
        stop_loss=10.0,
        take_profit=11.0,
        session=session,
    )

    assert decision.label == DecisionLabel.SELL_ALERT_STOP
    assert decision.executable_now is False
    assert decision.review_timing == REVIEW_TIMING_NEXT_OPEN_SESSION
    assert (
        decision.explanation
        == "Stop reached; market closed, review risk exit at next open session"
    )


def test_target_reached_on_weekend_uses_next_trading_session_wording() -> None:
    session = detect_egx_market_session(now=sample_weekend_market_datetime())
    decision = classify_open_position_decision(
        symbol="ABUK",
        current_price=11.5,
        stop_loss=10.0,
        take_profit=11.0,
        session=session,
    )

    assert decision.label == DecisionLabel.SELL_ALERT_TARGET
    assert decision.executable_now is False
    assert decision.review_timing == "NEXT_TRADING_SESSION"
    assert (
        decision.explanation
        == "Target reached; EGX is closed today, review selling next trading session"
    )


def test_executive_action_uses_review_now_when_market_open() -> None:
    session = detect_egx_market_session(now=sample_open_market_datetime())
    summary = build_decision_summary(
        [
            classify_strategy_signal_decision(_buy_setup(), session=session),
        ],
        [
            classify_open_position_decision(
                symbol="ABUK",
                current_price=9.5,
                stop_loss=10.0,
                take_profit=11.0,
                session=session,
            )
        ],
    )

    action = build_executive_action_from_decisions(
        session=session,
        decision_summary=summary,
    )

    assert action == "Sell alerts need review now: ABUK"


def test_executive_action_uses_next_session_review_when_market_closed() -> None:
    session = detect_egx_market_session(now=sample_closed_market_datetime())
    summary = build_decision_summary(
        [],
        [
            classify_open_position_decision(
                symbol="ABUK",
                current_price=11.5,
                stop_loss=10.0,
                take_profit=11.0,
                session=session,
            )
        ],
    )

    action = build_executive_action_from_decisions(
        session=session,
        decision_summary=summary,
    )

    assert action == "Sell alerts for next session review: ABUK"


@pytest.fixture
def tmp_storage(tmp_path, monkeypatch: pytest.MonkeyPatch):
    portfolio_path = tmp_path / "portfolio_state.json"
    trades_path = tmp_path / "trades.json"
    monkeypatch.setattr(settings, "PORTFOLIO_STATE_PATH", portfolio_path)
    monkeypatch.setattr(settings, "TRADES_PATH", trades_path)
    return tmp_path


def test_daily_report_contains_decision_summary_json(tmp_storage) -> None:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={"ELKA": _live_row("ELKA", 1.37, 1.30, 1000, 1.2)},
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    strategy_report = StrategyReport(
        strategy_name="Trend Join Long",
        results=[_buy_setup("ELKA")],
        buy_setups=[_buy_setup("ELKA")],
        watch=[],
        blocked=[],
    )
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=[],
        candidates=[],
        watchlist=[],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        now=sample_closed_market_datetime(),
        talib_config=TalibTechnicalConfig(enabled=False),
        enable_performance_analytics=False,
    )

    assert report.decision_summary
    assert report.decision_summary["note"] == "Paper trading only; no real execution"
    assert "ELKA" in report.decision_summary["watch_next_session"]
    assert report.executive_summary["action"] == "Watch next session: ELKA"


def test_daily_report_executive_action_mentions_sell_alert_first(
    tmp_storage,
) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    trade = portfolio.open_trade(
        symbol="ABUK",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=10.0,
        stop_loss=10.0,
        take_profit=12.0,
        reason="test",
    )
    journal.append_trade(trade)

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={"ABUK": _live_row("ABUK", 9.5, 10.0, 1000, 1.0)},
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=[],
        candidates=[],
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="Trend Join Long",
        results=[_buy_setup("ELKA")],
        buy_setups=[_buy_setup("ELKA")],
        watch=[],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        now=sample_open_market_datetime(),
        talib_config=TalibTechnicalConfig(enabled=False),
    )

    assert report.executive_summary["action"] == "Sell alerts need review now: ABUK"
    assert "ABUK" in report.decision_summary["sell_alerts"]
    position = report.decision_summary["positions"][0]
    assert position["executable_now"] is True
    assert position["review_timing"] == REVIEW_TIMING_NOW


def test_daily_report_closed_market_sell_alert_json_fields(tmp_storage) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    trade = portfolio.open_trade(
        symbol="ABUK",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=10.0,
        stop_loss=10.0,
        take_profit=11.0,
        reason="test",
    )
    journal.append_trade(trade)

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={"ABUK": _live_row("ABUK", 11.5, 10.0, 1000, 1.0)},
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=[],
        candidates=[],
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="Trend Join Long",
        results=[],
        buy_setups=[],
        watch=[],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        now=sample_closed_market_datetime(),
        talib_config=TalibTechnicalConfig(enabled=False),
    )

    assert (
        report.executive_summary["action"]
        == "Sell alerts for next session review: ABUK"
    )
    position = report.decision_summary["positions"][0]
    assert position["decision"] == "SELL_ALERT_TARGET"
    assert position["executable_now"] is False
    assert position["review_timing"] == REVIEW_TIMING_NEXT_OPEN_SESSION
    assert (
        position["decision_explanation"]
        == "Target reached; market closed, review selling at next open session"
    )
