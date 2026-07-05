"""Tests for Portfolio Learning V1."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from core.models import Trade, TradeSide, TradeStatus
from core.portfolio import VirtualPortfolio
from core.portfolio_learning import (
    build_portfolio_learning,
    empty_portfolio_learning_summary,
    format_portfolio_learning_arabic_block,
    format_portfolio_learning_report_lines,
    format_symbol_portfolio_learning_arabic_line,
)


def _closed_trade(
    symbol: str,
    pnl_percent: float,
    *,
    confidence: str = "UNKNOWN",
    sector: str = "UNKNOWN",
    memory: str = "UNKNOWN",
) -> Trade:
    pnl = pnl_percent * 10
    return Trade(
        symbol=symbol,
        side=TradeSide.BUY,
        quantity=10,
        entry_price=100.0,
        exit_price=100.0 + pnl_percent,
        stop_loss=95.0,
        take_profit=110.0,
        status=TradeStatus.CLOSED,
        opened_at=datetime(2026, 7, 1, tzinfo=UTC),
        closed_at=datetime(2026, 7, 2, tzinfo=UTC),
        pnl=pnl,
        pnl_percent=pnl_percent,
        notes=(
            f"confidence_label_v2={confidence} "
            f"sector_label={sector} memory_label={memory}"
        ),
    )


def test_empty_portfolio_learning_is_safe() -> None:
    summary, context, available = build_portfolio_learning(
        portfolio=None,
        journal=None,
        paper_portfolio_payload={},
        latest_prices={},
    )

    assert available is False
    assert summary == empty_portfolio_learning_summary()
    assert context["available"] is False


def test_portfolio_learning_summarizes_closed_trade_buckets() -> None:
    trades = [
        _closed_trade(
            "A",
            4.0,
            confidence="STRONG",
            sector="SUPPORTED_BY_SECTOR",
            memory="IMPROVING",
        ),
        _closed_trade("B", 3.0, confidence="STRONG"),
        _closed_trade("C", -2.0, confidence="WEAK", sector="SECTOR_DRAG"),
        _closed_trade("D", 1.0, confidence="GOOD"),
        _closed_trade("E", -1.0, confidence="WEAK"),
    ]

    summary, context, available = build_portfolio_learning(
        portfolio=None,
        journal=SimpleNamespace(trades=trades),
        paper_portfolio_payload={"available": True, "positions": []},
    )

    assert available is True
    assert context["available"] is True
    assert summary["closed_trades_count"] == 5
    assert summary["win_rate_pct"] == 60.0
    assert summary["expectancy_pct"] is not None
    assert summary["confidence_buckets"]["STRONG"]["trades_count"] == 2
    assert summary["sector_buckets"]["SUPPORTED_BY_SECTOR"]["trades_count"] == 1
    assert summary["memory_buckets"]["IMPROVING"]["trades_count"] == 1
    assert summary["best_trade"]["symbol"] == "A"
    assert summary["worst_trade"]["symbol"] == "C"


def test_portfolio_learning_open_position_context(tmp_path) -> None:
    portfolio = VirtualPortfolio(state_path=tmp_path / "portfolio_state.json")
    trade = portfolio.open_trade(
        symbol="COMI",
        side=TradeSide.BUY,
        quantity=10,
        entry_price=85.0,
        stop_loss=80.0,
        take_profit=95.0,
        reason="test",
    )
    portfolio.positions["COMI"].opened_at = datetime.now(UTC) - timedelta(days=3)
    portfolio.trades[trade.id].opened_at = portfolio.positions["COMI"].opened_at

    summary, context, available = build_portfolio_learning(
        portfolio=portfolio,
        journal=SimpleNamespace(trades=[]),
        paper_portfolio_payload={
            "available": True,
            "positions": [
                {
                    "symbol": "COMI",
                    "avg_entry_price": 85.0,
                    "current_price": 88.0,
                    "unrealized_pnl_pct": 3.5,
                }
            ],
        },
        latest_prices={"COMI": 88.0},
        now=datetime.now(UTC),
    )

    assert available is True
    assert summary["open_positions_count"] == 1
    assert context["symbols"]["COMI"]["holding_days"] >= 2
    assert context["symbols"]["COMI"]["learning_line"]


def test_portfolio_learning_formatters() -> None:
    summary = {
        **empty_portfolio_learning_summary(),
        "available": True,
        "open_positions_count": 1,
        "closed_trades_count": 5,
        "win_rate_pct": 60.0,
        "best_pattern": "confidence STRONG avg +3.50%",
        "weak_pattern": "confidence WEAK avg -1.50%",
    }

    report_lines = format_portfolio_learning_report_lines(summary)
    arabic_lines = format_portfolio_learning_arabic_block(summary)
    symbol_line = format_symbol_portfolio_learning_arabic_line(
        {"available": True, "learning_line": "STRONG setups have performed well so far"}
    )

    assert any("Win rate: 60.0%" in line for line in report_lines)
    assert "📚 تعلم المحفظة:" in arabic_lines
    assert any("نسبة نجاح: 60.0%" in line for line in arabic_lines)
    assert symbol_line == "تعلم المحفظة: STRONG setups have performed well so far"


def test_portfolio_learning_arabic_block_can_skip_available_gate() -> None:
    summary = empty_portfolio_learning_summary()
    assert format_portfolio_learning_arabic_block(summary) == []
    lines = format_portfolio_learning_arabic_block(summary, require_available=False)
    assert "📚 تعلم المحفظة:" in lines
    assert "صفقات مغلقة: 0" in lines
    assert "ملاحظة: محتاج تاريخ أكتر" in lines
