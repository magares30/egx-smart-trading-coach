"""Tests for advisory position exit plans."""

from datetime import date

import pytest

from config import settings
from core.daily_report import DailyReportBuilder, format_daily_report_text
from core.decision_labels import (
    REVIEW_TIMING_NEXT_OPEN_SESSION,
    REVIEW_TIMING_NOW,
)
from core.exit_plan import (
    ExitPlanLabel,
    build_executive_exit_plan_line,
    classify_position_exit_plan,
)
from core.live_snapshot import LiveMarketSnapshot
from core.market_hours import (
    detect_egx_market_session,
    sample_closed_market_datetime,
    sample_open_market_datetime,
)
from core.market_mood import MarketMood, MarketMoodResult
from core.models import TradeSide
from core.portfolio import VirtualPortfolio
from core.scanner import ScannerReport
from core.strategy import StrategyReport
from core.talib_technical import TalibTechnicalConfig
from core.trade_journal import TradeJournal
from tests.test_daily_report import _live_row


def test_target_reached_while_market_closed_exit_plan() -> None:
    session = detect_egx_market_session(now=sample_closed_market_datetime())
    plan = classify_position_exit_plan(
        symbol="ABUK",
        entry_price=10.0,
        current_price=12.0,
        stop_loss=9.0,
        take_profit=11.0,
        session=session,
    )

    assert plan.label == ExitPlanLabel.EXIT_REVIEW_TARGET
    assert plan.exit_timing == REVIEW_TIMING_NEXT_OPEN_SESSION
    assert plan.exit_executable_now is False
    assert (
        plan.explanation
        == "Target reached; review taking profit at next open session"
    )


def test_stop_reached_while_market_closed_exit_plan() -> None:
    session = detect_egx_market_session(now=sample_closed_market_datetime())
    plan = classify_position_exit_plan(
        symbol="ABUK",
        entry_price=10.0,
        current_price=8.5,
        stop_loss=9.0,
        take_profit=12.0,
        session=session,
    )

    assert plan.label == ExitPlanLabel.EXIT_REVIEW_STOP
    assert plan.exit_timing == REVIEW_TIMING_NEXT_OPEN_SESSION
    assert (
        plan.explanation
        == "Stop reached; review risk exit at next open session"
    )


def test_target_reached_while_market_open_is_executable_now() -> None:
    session = detect_egx_market_session(now=sample_open_market_datetime())
    plan = classify_position_exit_plan(
        symbol="ABUK",
        entry_price=10.0,
        current_price=12.0,
        stop_loss=9.0,
        take_profit=11.0,
        session=session,
    )

    assert plan.label == ExitPlanLabel.EXIT_REVIEW_TARGET
    assert plan.exit_executable_now is True
    assert plan.exit_timing == REVIEW_TIMING_NOW


def test_profitable_position_below_target_is_hold_profit_running() -> None:
    session = detect_egx_market_session(now=sample_open_market_datetime())
    plan = classify_position_exit_plan(
        symbol="ABUK",
        entry_price=10.0,
        current_price=10.4,
        stop_loss=9.0,
        take_profit=12.0,
        session=session,
    )

    assert plan.label == ExitPlanLabel.HOLD_PROFIT_RUNNING


def test_strong_profit_position_is_hold_protect_profit() -> None:
    session = detect_egx_market_session(now=sample_open_market_datetime())
    plan = classify_position_exit_plan(
        symbol="ABUK",
        entry_price=10.0,
        current_price=11.1,
        stop_loss=9.0,
        take_profit=12.0,
        session=session,
    )

    assert plan.label == ExitPlanLabel.HOLD_PROTECT_PROFIT


def test_missing_exit_data_is_hold_insufficient_data() -> None:
    session = detect_egx_market_session(now=sample_open_market_datetime())
    plan = classify_position_exit_plan(
        symbol="ABUK",
        entry_price=10.0,
        current_price=None,
        stop_loss=9.0,
        take_profit=12.0,
        session=session,
    )

    assert plan.label == ExitPlanLabel.HOLD_INSUFFICIENT_DATA


def test_executive_exit_plan_line_for_urgent_closed_market_targets() -> None:
    session = detect_egx_market_session(now=sample_closed_market_datetime())
    plans = [
        classify_position_exit_plan(
            symbol="ABUK",
            entry_price=10.0,
            current_price=12.0,
            stop_loss=9.0,
            take_profit=11.0,
            session=session,
        ),
        classify_position_exit_plan(
            symbol="CIRA",
            entry_price=20.0,
            current_price=24.0,
            stop_loss=18.0,
            take_profit=22.0,
            session=session,
        ),
    ]

    line = build_executive_exit_plan_line(plans, open_positions_count=2)

    assert line == (
        "ABUK target review next session; CIRA target review next session"
    )


@pytest.fixture
def tmp_storage(tmp_path, monkeypatch: pytest.MonkeyPatch):
    portfolio_path = tmp_path / "portfolio_state.json"
    trades_path = tmp_path / "trades.json"
    monkeypatch.setattr(settings, "PORTFOLIO_STATE_PATH", portfolio_path)
    monkeypatch.setattr(settings, "TRADES_PATH", trades_path)
    return tmp_path


def test_daily_report_txt_includes_exit_plan_under_open_positions(
    tmp_storage,
) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    trade = portfolio.open_trade(
        symbol="ABUK",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=10.0,
        stop_loss=9.0,
        take_profit=11.0,
        reason="test",
    )
    journal.append_trade(trade)

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={"ABUK": _live_row("ABUK", 12.0, 10.0, 1000, 1.0)},
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
    text = format_daily_report_text(report)

    assert "Exit Plan: EXIT_REVIEW_TARGET" in text
    assert report.exit_plan_summary["urgent_exits"] == ["ABUK"]
    assert report.executive_summary["exit_plan"] == (
        "ABUK target review next session"
    )
    position = report.exit_plan_summary["positions"][0]
    assert position["exit_plan"] == "EXIT_REVIEW_TARGET"
    assert position["exit_executable_now"] is False
    assert position["exit_timing"] == REVIEW_TIMING_NEXT_OPEN_SESSION
