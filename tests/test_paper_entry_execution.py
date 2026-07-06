"""Tests for automatic paper entry after daily report generation."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from config import settings
from core.market_hours import EgxMarketSession, EgxSessionStatus
from core.models import SignalType, TradeSignal
from core.paper_entry_execution import (
    SOURCE_BEST_IDEAS_FALLBACK,
    SOURCE_BUY_SETUP,
    execute_paper_entries_after_report,
    patch_saved_report_with_entry_metadata,
)
from core.portfolio import VirtualPortfolio
from core.strategy import StrategyDecision, StrategyReport, StrategyResult
from core.telegram_report_resolver import resolve_executable_opportunity_items
from core.trade_journal import TradeJournal

CAIRO = ZoneInfo("Africa/Cairo")


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


def _buy_setup(symbol: str = "FWRY", confidence: int = 85) -> StrategyResult:
    signal = TradeSignal(
        symbol=symbol,
        signal_type=SignalType.BUY_SETUP,
        entry_price=6.24,
        stop_loss=6.06,
        take_profit=6.62,
        confidence_score=confidence,
        reasons=["Scanner marked symbol as candidate"],
    )
    return StrategyResult(
        symbol=symbol,
        decision=StrategyDecision.BUY_SETUP,
        signal=signal,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        risk_reward=2.0,
        confidence_score=confidence,
        reasons=["Scanner marked symbol as candidate"],
    )


def _strategy_report(*buy_setups: StrategyResult) -> StrategyReport:
    setups = list(buy_setups)
    return StrategyReport(
        strategy_name="Trend Join Long",
        results=setups,
        buy_setups=setups,
        watch=[],
        blocked=[],
    )


def test_execute_paper_entries_skips_when_market_closed(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    TradeJournal().clear()

    result = execute_paper_entries_after_report(
        _strategy_report(_buy_setup()),
        market_session=_closed_session(),
    )

    assert result.opened_count == 0
    assert result.skip_reason == "market=CLOSED"
    assert len(TradeJournal().trades) == 0
    assert len(VirtualPortfolio().positions) == 0


def test_execute_paper_entries_opens_trades_when_market_open(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    TradeJournal().clear()

    result = execute_paper_entries_after_report(
        _strategy_report(_buy_setup("FWRY"), _buy_setup("LCSW")),
        market_session=_open_session(),
        max_trades_per_run=1,
    )

    assert result.opened_count == 1
    assert len(TradeJournal().trades) == 1
    assert "FWRY" in VirtualPortfolio().positions


def _fallback_report_payload(symbols: list[str]) -> dict:
    return {
        "market_session": {"status": "OPEN"},
        "executive_summary": {"best_ideas": symbols},
        "decision_summary": {"signals": [], "watch_next_session": []},
        "confidence_v2_summary": {
            "available": True,
            "strong": [symbols[0]] if symbols else [],
            "good": symbols[1:2],
            "mixed": [],
            "weak": [],
            "wait": [],
        },
        "confidence_v2_context": {
            symbols[0]: {
                "confidence_label_v2": "STRONG",
                "confidence_score_v2": 88,
            }
        },
        "sections": [],
    }


def test_execute_paper_entries_fallback_opens_from_best_ideas(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    TradeJournal().clear()

    result = execute_paper_entries_after_report(
        _strategy_report(),
        report_payload=_fallback_report_payload(["UEGC", "ELKA"]),
        latest_prices={"UEGC": 10.0, "ELKA": 6.24},
        market_session=_open_session(),
        max_trades_per_run=1,
    )

    assert result.buy_setups_count == 0
    assert result.fallback_used is True
    assert result.execution_source == SOURCE_BEST_IDEAS_FALLBACK
    assert result.opened_count == 1
    assert result.candidate_symbols[0] == "UEGC"
    assert result.opened_symbols == ["UEGC"]
    assert "UEGC" in VirtualPortfolio().positions
    trade = TradeJournal().trades[0]
    assert trade.reason == SOURCE_BEST_IDEAS_FALLBACK
    assert "BEST_IDEAS_FALLBACK" in trade.notes


def test_fallback_attempts_same_order_as_telegram_opportunities(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    TradeJournal().clear()

    payload = {
        "market_session": {"status": "OPEN"},
        "executive_summary": {"best_ideas": ["CICH", "LCSW", "EBSC"]},
        "decision_summary": {
            "signals": [
                {"symbol": "EBSC", "decision": "WATCH"},
                {"symbol": "NHPS", "decision": "WATCH"},
                {"symbol": "RAYA", "decision": "WATCH"},
            ],
            "watch_next_session": [],
        },
        "confidence_v2_summary": {
            "available": True,
            "strong": ["CICH", "LCSW"],
            "good": [],
            "mixed": [],
            "weak": [],
            "wait": [],
        },
        "sections": [],
    }
    telegram_top3 = [
        item["symbol"]
        for item in resolve_executable_opportunity_items(payload, limit=3)
    ]
    assert telegram_top3 == ["EBSC", "NHPS", "RAYA"]

    result = execute_paper_entries_after_report(
        _strategy_report(),
        report_payload=payload,
        latest_prices={
            "EBSC": 10.0,
            "NHPS": 8.0,
            "RAYA": 5.0,
            "CICH": 12.0,
            "LCSW": 7.0,
        },
        market_session=_open_session(),
        max_trades_per_run=3,
    )

    assert result.candidate_symbols[:3] == telegram_top3
    assert result.attempted_symbols[:3] == telegram_top3
    assert result.opened_symbols == telegram_top3


def test_fallback_records_skip_reason_for_visible_symbol(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    TradeJournal().clear()

    payload = {
        "market_session": {"status": "OPEN"},
        "executive_summary": {"best_ideas": ["CICH"]},
        "decision_summary": {
            "signals": [
                {"symbol": "EBSC", "decision": "WATCH"},
                {"symbol": "NHPS", "decision": "WATCH"},
            ],
            "watch_next_session": [],
        },
        "confidence_v2_summary": {"available": False},
        "sections": [],
    }

    result = execute_paper_entries_after_report(
        _strategy_report(),
        report_payload=payload,
        latest_prices={"EBSC": 10.0},
        market_session=_open_session(),
        max_trades_per_run=2,
    )

    assert result.candidate_symbols[:2] == ["EBSC", "NHPS"]
    assert result.opened_symbols == ["EBSC"]
    assert result.skipped_symbols_with_reasons["NHPS"] == "missing price"


def test_execute_paper_entries_buy_setup_blocks_fallback(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    TradeJournal().clear()

    result = execute_paper_entries_after_report(
        _strategy_report(_buy_setup("FWRY")),
        report_payload=_fallback_report_payload(["UEGC"]),
        latest_prices={"UEGC": 10.0, "FWRY": 6.24},
        market_session=_open_session(),
    )

    assert result.execution_source == SOURCE_BUY_SETUP
    assert result.fallback_used is False
    assert result.opened_count == 1
    assert "FWRY" in VirtualPortfolio().positions
    assert "UEGC" not in VirtualPortfolio().positions


def test_execute_paper_entries_closed_market_skips_fallback(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    TradeJournal().clear()

    result = execute_paper_entries_after_report(
        _strategy_report(),
        report_payload=_fallback_report_payload(["UEGC"]),
        latest_prices={"UEGC": 10.0},
        market_session=_closed_session(),
    )

    assert result.opened_count == 0
    assert result.fallback_used is False
    assert len(TradeJournal().trades) == 0


def test_execute_paper_entries_uses_strategy_buy_setups_only(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    TradeJournal().clear()

    result = execute_paper_entries_after_report(
        _strategy_report(),
        market_session=_open_session(),
    )

    assert result.buy_setups_count == 0
    assert result.opened_count == 0


def test_patch_saved_report_with_entry_metadata(tmp_path: Path) -> None:
    json_path = tmp_path / "egx_daily_report_20260705_120000.json"
    txt_path = tmp_path / "egx_daily_report_20260705_120000.txt"
    json_path.write_text(
        json.dumps({"report_metadata": {"generated_at": "2026-07-05T12:00:00+00:00"}}),
        encoding="utf-8",
    )
    txt_path.write_text("report text", encoding="utf-8")

    from core.paper_entry_execution import PaperEntryExecutionResult

    execution = PaperEntryExecutionResult(
        checked=True,
        market_status="OPEN",
        buy_setups_count=2,
        open_positions_count=0,
        opened_count=1,
        skipped_count=1,
        rejected_count=0,
        execution_source=SOURCE_BUY_SETUP,
        fallback_used=False,
        fallback_candidates_count=0,
        candidate_symbols=["FWRY", "LCSW"],
        attempted_symbols=["FWRY"],
        opened_symbols=["FWRY"],
        skipped_symbols_with_reasons={"LCSW": "already open"},
    )
    patch_saved_report_with_entry_metadata(json_path, execution)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    metadata = payload["report_metadata"]
    assert metadata["paper_entry_execution_checked"] is True
    assert metadata["paper_entry_execution_opened_count"] == 1
    assert metadata["paper_entry_execution_buy_setups_count"] == 2
    assert metadata["paper_entry_execution_source"] == SOURCE_BUY_SETUP
    assert metadata["paper_entry_execution_fallback_used"] is False
    assert metadata["paper_entry_execution_candidate_symbols"] == ["FWRY", "LCSW"]
    assert metadata["paper_entry_execution_attempted_symbols"] == ["FWRY"]
    assert metadata["paper_entry_execution_opened_symbols"] == ["FWRY"]
    assert metadata["paper_entry_execution_skipped_symbols_with_reasons"] == {
        "LCSW": "already open",
    }
