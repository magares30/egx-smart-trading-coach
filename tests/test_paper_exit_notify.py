"""Tests for paper-close Telegram notifications."""

from __future__ import annotations

from core.paper_exit_notify import notify_paper_closes_from_report_payload


def test_notify_skips_when_no_closes() -> None:
    assert (
        notify_paper_closes_from_report_payload(
            {"report_metadata": {"paper_exit_execution_closed_count": 0}}
        )
        is False
    )


def test_notify_skips_when_chat_id_missing(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    assert (
        notify_paper_closes_from_report_payload(
            {
                "report_metadata": {
                    "paper_exit_execution_closed_count": 1,
                    "paper_exit_execution_closed_trades": [
                        {"symbol": "FWRY", "reason": "TAKE_PROFIT", "pnl": 1.0}
                    ],
                }
            }
        )
        is False
    )
