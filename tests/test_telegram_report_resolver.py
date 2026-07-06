"""Tests for Telegram report resolver and JSON V2 button alignment."""

from __future__ import annotations

from core.telegram_bot import (
    collect_why_symbols,
    format_best_opportunities,
    format_best_three,
    format_help,
    format_hot_sectors,
    format_next_session_watch,
    format_opportunities_menu_intro,
    format_ultra_short,
)
from core.telegram_report_resolver import (
    EMPTY_OPPORTUNITIES_MESSAGE,
    is_market_closed,
    resolve_executable_opportunity_items,
    resolve_next_session_items,
    resolve_opportunity_items,
    resolve_report_symbols,
)


def _closed_market_payload() -> dict:
    return {
        "market_session": {"status": "CLOSED"},
        "report_metadata": {"market_status": "CLOSED"},
        "executive_summary": {
            "best_ideas": ["ELKA", "LCSW", "TANM"],
            "market": "CLOSED",
            "action": "Watch next session",
        },
        "decision_summary": {
            "watch_next_session": [],
            "signals": [],
        },
        "confidence_v2_summary": {
            "available": True,
            "strong": ["ELKA"],
            "good": ["LCSW"],
            "mixed": [],
            "weak": [],
            "wait": [],
        },
        "confidence_v2_context": {
            "ELKA": {
                "confidence_label_v2": "STRONG",
                "confidence_score_v2": 88,
            }
        },
        "sector_intelligence_summary": {
            "available": True,
            "sector_supported": ["ELKA"],
            "sector_leaders": ["LCSW"],
            "isolated_strength": ["TANM"],
            "weak_in_hot_sector": [],
        },
        "sector_intelligence_context": {
            "TANM": {"sector_label": "STRONG_STOCK_WEAK_SECTOR"}
        },
        "sections": [
            {"title": "Top Candidates", "lines": []},
            {"title": "Strategy Signals", "lines": []},
            {"title": "Watch List", "lines": []},
        ],
    }


def _open_market_payload() -> dict:
    return {
        "market_session": {"status": "OPEN"},
        "report_metadata": {"market_status": "OPEN"},
        "executive_summary": {"best_ideas": ["ELKA", "LCSW"]},
        "decision_summary": {
            "watch_next_session": ["TANM"],
            "signals": [
                {
                    "symbol": "ELKA",
                    "decision": "BUY_SETUP",
                    "strategy_decision": "BUY",
                }
            ],
        },
        "confidence_v2_summary": {"available": True, "strong": [], "good": []},
        "sections": [
            {
                "title": "Strategy Signals",
                "lines": [
                    (
                        "1. ELKA | BUY | Decision BUY_SETUP | "
                        "Entry 1.37 | Stop 1.32 | Target 1.48 | Timing READY"
                    ),
                    "   Reason: strong setup",
                ],
            },
            {"title": "Top Candidates", "lines": []},
            {"title": "Watch List", "lines": []},
        ],
        "confirmation_summary": {
            "signals": [
                {
                    "symbol": "ELKA",
                    "confirmation_text": "Confirmation: GOOD | TV strong",
                }
            ]
        },
    }


def test_closed_market_best_three_uses_executive_best_ideas() -> None:
    text = format_best_three(_closed_market_payload())

    assert "📌 أفضل 3 للجلسة الجاية:" in text
    assert "السوق مقفول" in text
    assert "ELKA" in text
    assert "LCSW" in text
    assert EMPTY_OPPORTUNITIES_MESSAGE not in text


def test_closed_market_best_opportunities_uses_best_ideas_fallback() -> None:
    text = format_best_opportunities(_closed_market_payload(), limit=5)

    assert "🔥 أفضل أفكار للجلسة الجاية:" in text
    assert "ELKA" in text
    assert "TANM" in text
    assert EMPTY_OPPORTUNITIES_MESSAGE not in text


def test_next_session_watch_falls_back_to_best_ideas_and_confidence() -> None:
    payload = _closed_market_payload()
    items = resolve_next_session_items(payload, limit=5)

    assert [item["symbol"] for item in items[:3]] == ["ELKA", "LCSW", "TANM"]

    text = format_next_session_watch(payload, limit=5)
    assert "ELKA" in text
    assert "السوق مقفول" in text
    assert EMPTY_OPPORTUNITIES_MESSAGE not in text


def test_open_market_strategy_signals_still_work() -> None:
    items = resolve_opportunity_items(_open_market_payload(), limit=5)
    assert items[0]["symbol"] == "ELKA"
    assert items[0]["decision"] == "BUY_SETUP"

    text = format_best_opportunities(_open_market_payload(), limit=5)
    assert "🔥 أفضل فرص:" in text
    assert "Confirmation: GOOD" in text


def test_collect_why_symbols_includes_structured_v2_context() -> None:
    payload = _closed_market_payload()
    symbols = collect_why_symbols(payload, limit=10)

    assert "ELKA" in symbols
    assert "TANM" in symbols


def test_hot_sectors_includes_sector_intelligence_when_available() -> None:
    payload = _closed_market_payload()
    payload["sector_momentum"] = [
        {
            "sector": "Real Estate",
            "status": "HOT",
            "sector_score": 90,
            "avg_change_percent": 2.0,
            "candidates_count": 4,
        }
    ]
    text = format_hot_sectors(payload, limit=5)

    assert "🏭 ذكاء القطاعات:" in text
    assert "مدعوم بالقطاع: ELKA" in text
    assert "قوة منفردة: TANM" in text


def test_ultra_short_includes_closed_market_honesty_and_layers() -> None:
    text = format_ultra_short(_closed_market_payload(), max_lines=10)

    assert "السوق مقفول" in text
    assert "ELKA" in text
    assert "ثقة:" in text
    assert "قطاعات:" in text


def test_help_mentions_new_layers_and_closed_market_behavior() -> None:
    text = format_help()

    assert "Confidence V2" in text
    assert "Sector Intelligence" in text
    assert "Market Memory" in text
    assert "Portfolio Learning" in text
    assert "Closed Market Digest" in text
    assert "لما السوق مقفول" in text


def test_opportunities_menu_intro_shows_closed_preview() -> None:
    text = format_opportunities_menu_intro(_closed_market_payload())

    assert "السوق مقفول" in text
    assert "أفضل أفكار: ELKA, LCSW, TANM" in text


def test_missing_fields_do_not_crash_resolver() -> None:
    payload: dict = {"sections": []}
    assert resolve_opportunity_items(payload) == []
    assert resolve_next_session_items(payload) == []
    assert resolve_report_symbols(payload) == []
    assert is_market_closed(payload) is False
    assert format_best_three(payload) == EMPTY_OPPORTUNITIES_MESSAGE


def test_resolve_opportunity_items_no_duplicates() -> None:
    payload = _closed_market_payload()
    payload["decision_summary"] = {
        "watch_next_session": ["ELKA"],
        "signals": [{"symbol": "ELKA", "decision": "WATCH_NEXT_SESSION"}],
    }
    items = resolve_opportunity_items(payload)
    symbols = [item["symbol"] for item in items]

    assert symbols.count("ELKA") == 1
    assert symbols[0] == "ELKA"


def test_resolve_executable_opportunity_items_matches_opportunity_resolver() -> None:
    payload = _open_market_payload()
    executable = resolve_executable_opportunity_items(payload, limit=5)
    legacy = resolve_opportunity_items(payload, limit=5, mode="opportunities")

    assert [item["symbol"] for item in executable] == [item["symbol"] for item in legacy]


def test_executable_opportunity_order_prefers_decision_signals_over_best_ideas() -> None:
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

    symbols = [item["symbol"] for item in resolve_executable_opportunity_items(payload, limit=3)]

    assert symbols == ["EBSC", "NHPS", "RAYA"]
    assert "EBSC" in format_best_three(payload)
    assert "NHPS" in format_best_three(payload)
