"""Tests for EGX market hours guard and session detection."""

from pathlib import Path

import pytest

from config import settings
from core.market_hours import (
    EgxSessionStatus,
    detect_egx_market_session,
    format_market_session_report_lines,
    sample_closed_market_datetime,
    sample_open_market_datetime,
)
from core.paper_engine import evaluate_buy_setup_for_open
from core.portfolio import VirtualPortfolio
from core.risk import RiskManager
from tests.test_paper_trader import _buy_setup


@pytest.fixture
def tmp_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    portfolio_path = tmp_path / "portfolio_state.json"
    trades_path = tmp_path / "trades.json"
    monkeypatch.setattr(settings, "PORTFOLIO_STATE_PATH", portfolio_path)
    monkeypatch.setattr(settings, "TRADES_PATH", trades_path)
    return tmp_path


def test_detect_open_market_session() -> None:
    session = detect_egx_market_session(now=sample_open_market_datetime())

    assert session.is_trading_day is True
    assert session.session_status == EgxSessionStatus.OPEN
    assert session.is_open_for_new_entries is True
    assert session.paper_entries_enabled is True
    assert session.guard_enabled is True


def test_detect_closed_market_session_after_hours() -> None:
    session = detect_egx_market_session(now=sample_closed_market_datetime())

    assert session.is_trading_day is True
    assert session.session_status == EgxSessionStatus.CLOSED
    assert session.is_open_for_new_entries is False
    assert session.paper_entries_enabled is False
    assert session.is_after_close is True


def test_ignore_market_hours_enables_paper_entries_when_closed() -> None:
    session = detect_egx_market_session(
        now=sample_closed_market_datetime(),
        ignore_market_hours=True,
    )

    assert session.is_open_for_new_entries is False
    assert session.paper_entries_enabled is True
    assert session.guard_enabled is False
    assert "Market-hours guard ignored" in session.note


def test_evaluate_buy_setup_blocks_when_market_closed(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()

    evaluation = evaluate_buy_setup_for_open(
        _buy_setup(),
        portfolio=portfolio,
        risk_manager=RiskManager(),
        min_confidence_score=70,
        now=sample_closed_market_datetime(),
    )

    assert evaluation.decision == "SKIPPED"
    assert evaluation.reason == "market closed for new paper entries"


def test_evaluate_buy_setup_allows_open_during_trading_session(
    tmp_storage: Path,
) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()

    evaluation = evaluate_buy_setup_for_open(
        _buy_setup(),
        portfolio=portfolio,
        risk_manager=RiskManager(),
        min_confidence_score=70,
        now=sample_open_market_datetime(),
    )

    assert evaluation.decision == "OPENED"
    assert evaluation.reason == "paper trade opened"


def test_format_market_session_report_lines() -> None:
    session = detect_egx_market_session(now=sample_open_market_datetime())
    lines = format_market_session_report_lines(session)

    assert lines[0] == "- Status: OPEN"
    assert any(line.startswith("- Cairo Time:") for line in lines)
    assert any(line.startswith("- Paper Entries:") for line in lines)
