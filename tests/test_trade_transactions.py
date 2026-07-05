"""Tests for paper trade transaction Telegram formatting."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.models import Trade, TradeSide, TradeStatus
from core.trade_journal import TradeJournal
from core.trade_transactions import (
    EMPTY_TRADE_LOG_MESSAGE,
    UNREADABLE_TRADE_LOG_MESSAGE,
    format_trade_transactions,
    load_trade_records_from_state,
)


@pytest.fixture
def trades_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    trades_path = tmp_path / "storage" / "trades.json"
    trades_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.settings.TRADES_PATH", trades_path)
    return trades_path


def test_format_trade_transactions_empty_history(trades_storage: Path) -> None:
    assert format_trade_transactions() == EMPTY_TRADE_LOG_MESSAGE


def test_format_trade_transactions_unreadable_file(
    trades_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trades_storage.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(
        "core.trade_transactions.load_trade_journal_for_report",
        lambda: None,
    )

    assert format_trade_transactions() == UNREADABLE_TRADE_LOG_MESSAGE


def test_format_trade_transactions_open_and_closed(trades_storage: Path) -> None:
    journal = TradeJournal(journal_path=trades_storage)
    journal.trades = [
        Trade(
            symbol="ELKA",
            side=TradeSide.BUY,
            quantity=1000,
            entry_price=2.35,
            stop_loss=2.2,
            take_profit=2.6,
            status=TradeStatus.OPEN,
            opened_at=datetime(2026, 7, 1, tzinfo=UTC),
        ),
        Trade(
            symbol="ELKA",
            side=TradeSide.BUY,
            quantity=500,
            entry_price=2.10,
            stop_loss=2.0,
            take_profit=2.4,
            status=TradeStatus.CLOSED,
            opened_at=datetime(2026, 6, 20, tzinfo=UTC),
            closed_at=datetime(2026, 7, 3, tzinfo=UTC),
            exit_price=2.55,
            pnl=225.0,
            pnl_percent=8.51,
        ),
        Trade(
            symbol="ABCD",
            side=TradeSide.BUY,
            quantity=200,
            entry_price=2.0,
            stop_loss=1.8,
            take_profit=2.3,
            status=TradeStatus.CLOSED,
            opened_at=datetime(2026, 6, 15, tzinfo=UTC),
            closed_at=datetime(2026, 7, 3, tzinfo=UTC),
            exit_price=1.90,
            pnl=-20.0,
            pnl_percent=-3.20,
        ),
    ]
    journal.save()

    text = format_trade_transactions(limit=15)

    assert "📜 سجل العمليات الورقية:" in text
    assert "1) SELL ELKA" in text
    assert "📅 2026-07-03" in text
    assert "💵 بيع: 2.55" in text
    assert "+8.51%" in text
    assert "ربح ✅" in text
    assert "2) SELL ABCD" in text
    assert "خسارة ❌" in text
    assert "3) BUY ELKA" in text
    assert "💵 شراء: 2.35" in text
    assert "📦 الكمية: 1000" in text
    assert "الحالة: مفتوحة" in text


def test_format_trade_transactions_limits_output(trades_storage: Path) -> None:
    journal = TradeJournal(journal_path=trades_storage)
    journal.trades = [
        Trade(
            symbol=f"S{i:02d}",
            side=TradeSide.BUY,
            quantity=100,
            entry_price=1.0 + i * 0.01,
            stop_loss=0.9,
            take_profit=1.2,
            status=TradeStatus.CLOSED,
            opened_at=datetime(2026, 6, 1, i, tzinfo=UTC),
            closed_at=datetime(2026, 7, 1, i, tzinfo=UTC),
            exit_price=1.1,
            pnl=10.0,
            pnl_percent=1.0,
        )
        for i in range(20)
    ]
    journal.save()

    text = format_trade_transactions(limit=15)

    assert text.count(") SELL ") == 15
    assert "عرض آخر 15 عملية فقط." in text


def test_load_trade_records_supports_legacy_dict_fields(trades_storage: Path) -> None:
    trades_storage.write_text(
        json.dumps(
            [
                {
                    "symbol": "COMI",
                    "action": "BUY",
                    "status": "CLOSED",
                    "buy_price": 88.5,
                    "sell_price": 90.0,
                    "shares": 10,
                    "return_pct": 1.7,
                    "realized_pnl": 15.0,
                    "date": "2026-07-02T10:00:00+00:00",
                    "closed_at": "2026-07-04T10:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    records, error = load_trade_records_from_state()

    assert error is None
    assert len(records) == 1
    assert records[0]["symbol"] == "COMI"
    assert records[0]["exit_price"] == 90.0

    text = format_trade_transactions(limit=5)
    assert "SELL COMI" in text
    assert "💵 بيع: 90.00" in text
