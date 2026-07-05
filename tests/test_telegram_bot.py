"""Tests for Telegram bot formatters and report loading helpers."""

from __future__ import annotations

import json
from pathlib import Path

from core.latest_report_sections import CLOUD_PORTFOLIO_STATE_MESSAGE
from core.telegram_bot import (
    BTN_BEST_THREE,
    BTN_HOT_SECTORS,
    BTN_OPPORTUNITIES,
    BTN_PNL,
    BTN_SELL_ONLY,
    BTN_TRADE_LOG,
    BTN_ULTRA_SHORT,
    NO_REPORT_MESSAGE,
    SELL_REVIEW_EMPTY_MESSAGE,
    WHY_ADVISORY_NOTE,
    WHY_NOT_FOUND_MESSAGE,
    build_main_menu,
    build_market_menu,
    build_opportunities_menu,
    build_sell_portfolio_menu,
    build_why_symbol_keyboard,
    collect_why_symbols,
    find_latest_report_json,
    format_best_opportunities,
    format_best_three,
    format_daily_overview,
    format_help,
    format_hot_sectors,
    format_market_status,
    format_next_session_watch,
    format_paper_portfolio,
    format_pnl_summary,
    format_sell_only,
    format_sell_portfolio_menu_intro,
    format_sell_review,
    format_symbol_why,
    format_symbol_why_response,
    format_ultra_short,
    format_warnings,
    is_chat_authorized,
    load_latest_report_payload,
    parse_why_command,
)


def _sample_payload() -> dict:
    return {
        "report_date": "2026-07-02",
        "warnings": [f"warning {index}" for index in range(1, 8)],
        "market_session": {
            "status": "CLOSED",
            "note": "Market is closed. Signals are next-session watchlist ideas only.",
        },
        "market_breadth_mood": {
            "mood": "BULLISH",
            "advancers_count": 111,
            "symbols_count": 174,
            "avg_change_percent": 0.97,
        },
        "sector_momentum": [
            {
                "sector": "Health Technology",
                "status": "HOT",
                "sector_score": 100,
                "avg_change_percent": 2.5,
                "candidates_count": 7,
            },
            {
                "sector": "Retail Trade",
                "status": "HOT",
                "sector_score": 96,
                "avg_change_percent": 4.5,
                "candidates_count": 3,
            },
            {
                "sector": "Process Industries",
                "status": "HOT",
                "sector_score": 89,
                "avg_change_percent": 1.5,
                "candidates_count": 19,
            },
            {
                "sector": "Non-Energy Minerals",
                "status": "HOT",
                "sector_score": 87,
                "avg_change_percent": 1.5,
                "candidates_count": 7,
            },
            {
                "sector": "Finance",
                "status": "HOT",
                "sector_score": 82,
                "avg_change_percent": 1.2,
                "candidates_count": 5,
            },
            {
                "sector": "Utilities",
                "status": "HOT",
                "sector_score": 80,
                "avg_change_percent": 0.8,
                "candidates_count": 4,
            },
            {
                "sector": "Energy",
                "status": "WARM",
                "sector_score": 70,
                "avg_change_percent": 1.0,
                "candidates_count": 2,
            },
        ],
        "executive_summary": {
            "market": "CLOSED | BULLISH | Paper entries disabled",
            "best_ideas": ["ELKA", "LCSW", "TANM"],
            "action": "Watch next session: ELKA",
            "confirmation": "3 good setups; TA-Lib still waiting for history",
            "paper_pnl": "+38,101.88 (+38.10%) | Open positions: 2",
            "main_risk": "Market closed; paper entries disabled",
        },
        "decision_summary": {
            "watch_next_session": ["ELKA", "LCSW", "TANM", "OCDI", "POUL", "EBSC"],
            "sell_alerts": ["ABUK"],
            "signals": [
                {
                    "symbol": "ELKA",
                    "decision": "WATCH_NEXT_SESSION",
                    "explanation": "market closed; review next session only",
                    "strategy_decision": "WATCH",
                },
                {
                    "symbol": "LCSW",
                    "decision": "WATCH_NEXT_SESSION",
                    "explanation": "market closed; review next session only",
                    "strategy_decision": "WATCH",
                },
            ],
            "positions": [
                {
                    "symbol": "ABUK",
                    "decision": "SELL_ALERT_TARGET",
                    "review_timing": "NEXT_OPEN_SESSION",
                }
            ],
        },
        "confirmation_summary": {
            "signals": [
                {
                    "symbol": "ELKA",
                    "confirmation_label": "GOOD_CONFIRMATION",
                    "confirmation_text": (
                        "Confirmation: GOOD | TV strong | Timing ready | "
                        "TA-Lib waiting history"
                    ),
                    "tv_status": "STRONG",
                    "timing_status": "READY",
                    "talib_status": "INSUFFICIENT_HISTORY",
                },
                {
                    "symbol": "LCSW",
                    "confirmation_label": "GOOD_CONFIRMATION",
                    "confirmation_text": (
                        "Confirmation: GOOD | TV strong | Timing ready | "
                        "TA-Lib waiting history"
                    ),
                },
            ]
        },
        "paper_portfolio": {
            "available": True,
            "cash": 5815.53,
            "open_positions_count": 1,
            "market_value": 74427.15,
            "total_equity": 80242.68,
            "unrealized_pnl": 26509.95,
            "unrealized_pnl_pct": 55.32,
            "positions": [
                {
                    "symbol": "ABUK",
                    "market_value": 74427.15,
                    "unrealized_pnl": 26509.95,
                    "unrealized_pnl_pct": 55.32,
                    "decision": "SELL_ALERT_TARGET",
                    "exit_plan": "EXIT_REVIEW_TARGET",
                    "review_timing": "NEXT_OPEN_SESSION",
                    "exit_timing": "NEXT_OPEN_SESSION",
                }
            ],
        },
        "paper_trading_performance": {
            "available": True,
            "initial_capital": 100000,
            "current_equity": 80242.68,
            "total_pnl": 26509.95,
            "total_return_pct": 26.51,
            "unrealized_pnl": 26509.95,
            "realized_pnl": 0.0,
            "open_positions_count": 1,
        },
        "report_metadata": {
            "generated_at": "2026-07-02T10:00:00+00:00",
            "data_provider": "tradingview",
            "data_provider_label": "TradingView",
            "market_status": "CLOSED",
            "talib_available": True,
            "talib_mode": "active",
            "talib_reason": "",
            "tradingview_technical_available": True,
            "confidence_v2_available": True,
            "sector_intelligence_available": True,
            "portfolio_learning_available": True,
        },
        "portfolio_learning_summary": {
            "available": True,
            "open_positions_count": 1,
            "closed_trades_count": 5,
            "win_rate_pct": 60.0,
            "average_win_pct": 3.5,
            "average_loss_pct": -1.5,
            "expectancy_pct": 1.5,
            "best_trade": {"symbol": "ELKA", "pnl_percent": 4.0},
            "worst_trade": {"symbol": "ABUK", "pnl_percent": -2.0},
            "confidence_buckets": {},
            "sector_buckets": {},
            "memory_buckets": {},
            "best_pattern": "confidence STRONG avg +3.50%",
            "weak_pattern": "confidence WEAK avg -1.50%",
            "learning_notes": [],
            "learning_warnings": [],
        },
        "portfolio_learning_context": {
            "available": True,
            "open_positions": [],
            "symbols": {
                "ELKA": {
                    "symbol": "ELKA",
                    "learning_line": "STRONG setups have performed well so far",
                }
            },
        },
        "sector_intelligence_summary": {
            "available": True,
            "sector_supported": ["ELKA"],
            "sector_leaders": ["LCSW"],
            "isolated_strength": ["TANM"],
            "weak_in_hot_sector": ["ABUK"],
            "sector_drag": [],
            "unknown": [],
        },
        "sector_intelligence_context": {
            "ELKA": {
                "symbol": "ELKA",
                "sector": "Real Estate",
                "sector_label": "SUPPORTED_BY_SECTOR",
                "sector_score": 82,
                "sector_avg_change_pct": 1.3,
                "symbol_change_pct": 3.0,
                "relative_to_sector_pct": 1.7,
                "sector_is_hot": True,
                "sector_is_weak": False,
                "sector_reasons": ["Hot sector supports the symbol setup"],
                "sector_risks": [],
            }
        },
        "confidence_v2_summary": {
            "available": True,
            "strong": ["ELKA"],
            "good": ["LCSW"],
            "mixed": ["TANM"],
            "weak": [],
            "wait": ["ABUK"],
            "main_risks": ["market closed; review next session"],
            "top_reason": "TradingView technical confirmation supportive",
        },
        "confidence_v2_context": {
            "ELKA": {
                "symbol": "ELKA",
                "confidence_score_v2": 88,
                "confidence_label_v2": "STRONG",
                "confidence_reasons_v2": [
                    "TradingView technical confirmation supportive",
                    "Market Memory shows improvement",
                ],
                "confidence_risks_v2": ["market closed; review next session"],
                "confidence_components_v2": {
                    "base_score": 80,
                    "technical": 12,
                    "market_mood": 15,
                    "memory": 12,
                    "sector": 10,
                    "risk_reward": 8,
                    "fundamentals": 0,
                    "liquidity": 10,
                    "session": -8,
                },
            }
        },
        "candidate_fundamentals": [
            {
                "symbol": "ELKA",
                "status": "UNKNOWN",
                "summary": "fundamental fields unavailable",
            }
        ],
        "sections": [
            {
                "title": "Top Candidates",
                "lines": [
                    "1. ELKA | Score 100 | Change +3.01% | Volume 3.2x",
                    "   Reasons: Positive price change, Broke previous high",
                    "   Technical: STRONG (+20) | RSI 65 | MACD positive",
                    "   Entry Timing: READY (+19) | 1H OK | 15m WATCH",
                    "   TA-Lib: INSUFFICIENT_HISTORY | Need more saved history snapshots",
                ],
            },
            {
                "title": "Strategy Signals",
                "lines": [
                    (
                        "1. ELKA | WATCH | Decision WATCH_NEXT_SESSION | "
                        "Entry 1.37 | Stop 1.32 | Target 1.48 | Timing READY"
                    ),
                    "   Reason: Scanner marked symbol as candidate",
                    (
                        "2. LCSW | WATCH | Decision WATCH_NEXT_SESSION | "
                        "Entry 28.45 | Stop 27.64 | Target 30.07 | Timing READY"
                    ),
                    "   Reason: Scanner marked symbol as candidate",
                    (
                        "3. TANM | WATCH | Decision WATCH_NEXT_SESSION | "
                        "Entry 5.25 | Stop 5.02 | Target 5.71 | Timing READY"
                    ),
                    (
                        "4. OCDI | WATCH | Decision WATCH_NEXT_SESSION | "
                        "Entry 25.32 | Stop 24.46 | Target 27.04 | Timing READY"
                    ),
                    (
                        "5. POUL | WATCH | Decision WATCH_NEXT_SESSION | "
                        "Entry 38.00 | Stop 36.78 | Target 40.44 | Timing READY"
                    ),
                    (
                        "6. EBSC | WATCH | Decision WATCH_NEXT_SESSION | "
                        "Entry 2.10 | Stop 1.73 | Target 2.83 | Timing WATCH"
                    ),
                ],
            },
            {
                "title": "Market Mood",
                "lines": ["- BULLISH", "- Score: 80/100"],
            },
            {
                "title": "Watch List",
                "lines": [
                    "1. ELKA | Score 55 | Change +0.50% | Volume 1.0x",
                    "2. COMI | Score 50 | Change +0.20% | Volume 0.9x",
                ],
            },
        ],
    }


def test_no_report_payload_message() -> None:
    assert format_daily_overview(None) == NO_REPORT_MESSAGE
    assert format_best_opportunities(None) == NO_REPORT_MESSAGE


def test_telegram_overview_includes_closed_market_arabic_block() -> None:
    payload = _sample_payload()
    payload["report_metadata"] = {
        **(payload.get("report_metadata") or {}),
        "closed_market_digest": {
            "enabled": True,
            "price_data_date": "2026-07-02",
            "price_data_source": "TradingView Screener",
            "is_price_data_stale": True,
        },
    }
    text = format_daily_overview(payload)
    assert "🔒 السوق مقفول النهارده" in text
    assert "2026-07-02" in text
    assert "مفيش دخول ورقي جديد" in text


def test_market_status_includes_closed_digest_details() -> None:
    payload = _sample_payload()
    payload["report_metadata"] = {
        **(payload.get("report_metadata") or {}),
        "closed_market_digest": {
            "enabled": True,
            "reason": "after_hours",
            "price_data_date": "2026-07-02",
            "is_price_data_stale": True,
        },
    }
    text = format_market_status(payload)
    assert "آخر بيانات أسعار" in text
    assert "2026-07-02" in text
    assert "قد تكون قديمة" in text


def test_daily_overview_formatter() -> None:
    text = format_daily_overview(_sample_payload())

    assert "2026-07-02" in text
    assert "CLOSED | BULLISH" in text
    assert "ELKA, LCSW, TANM" in text
    assert "3 good setups" in text
    assert "Market closed" in text
    assert "TA-Lib: ACTIVE" in text
    assert "📚 تعلم المحفظة:" in text
    assert "صفقات مغلقة: 5" in text
    assert "🏭 ذكاء القطاعات:" in text
    assert "مدعوم بالقطاع: ELKA" in text
    assert "🧠 الثقة الذكية:" in text
    assert "قوي: ELKA" in text
    assert text.index("📚 تعلم المحفظة:") < text.index("🏭 ذكاء القطاعات:")


def test_daily_overview_shows_portfolio_learning_with_zero_closed_trades() -> None:
    payload = _sample_payload()
    payload["portfolio_learning_summary"] = {
        "available": False,
        "open_positions_count": 0,
        "closed_trades_count": 0,
        "win_rate_pct": None,
        "best_pattern": None,
        "weak_pattern": None,
        "learning_notes": [],
        "learning_warnings": [],
    }

    text = format_daily_overview(payload)

    assert "📚 تعلم المحفظة:" in text
    assert "صفقات مغلقة: 0" in text
    assert "نسبة نجاح: n/a" in text
    assert "أفضل نمط: n/a" in text
    assert "ملاحظة: محتاج تاريخ أكتر" in text


def test_daily_overview_portfolio_learning_metadata_fallback() -> None:
    payload = _sample_payload()
    payload.pop("portfolio_learning_summary", None)
    payload["report_metadata"] = {
        **(payload.get("report_metadata") or {}),
        "portfolio_learning_available": True,
    }

    text = format_daily_overview(payload)

    assert "📚 تعلم المحفظة:" in text
    assert "محتاج تاريخ أكتر من الصفقات الورقية." in text


def test_daily_overview_paper_pnl_empty_portfolio_not_na() -> None:
    payload = _sample_payload()
    payload["executive_summary"]["paper_pnl"] = "n/a"
    payload["paper_portfolio"] = {
        "available": True,
        "cash": 100000.0,
        "open_positions_count": 0,
        "market_value": 0.0,
        "total_equity": 100000.0,
        "unrealized_pnl": 0.0,
        "unrealized_pnl_pct": 0.0,
        "positions": [],
    }
    payload["paper_trading_performance"] = {
        "available": True,
        "initial_capital": 100000,
        "current_equity": 100000,
        "total_pnl": 0.0,
        "total_return_pct": 0.0,
        "open_positions_count": 0,
    }

    text = format_daily_overview(payload)

    assert "💰 P&L ورقي: 0.00 (+0.00%) | Open positions: 0" in text


def test_daily_overview_paper_pnl_stays_na_when_portfolio_missing() -> None:
    payload = _cloud_payload_without_portfolio()
    payload["executive_summary"]["market"] = "CLOSED | BULLISH | Paper entries disabled"
    payload["executive_summary"]["best_ideas"] = []

    text = format_daily_overview(payload)

    assert "💰 P&L ورقي: n/a" in text
    assert "📚 تعلم المحفظة:" not in text


def test_daily_overview_includes_market_memory_block() -> None:
    payload = _sample_payload()
    payload["market_memory_summary"] = {
        "available": True,
        "new": ["ELKA"],
        "improving": ["LCSW"],
        "persistent": ["TANM"],
        "fading": ["ABUK"],
        "weakening": [],
    }

    text = format_daily_overview(payload)

    assert "🧠 ذاكرة السوق:" in text
    assert "بيتحسن: LCSW" in text
    assert "بيضعف: ABUK" in text


def test_symbol_why_includes_market_memory() -> None:
    payload = _sample_payload()
    payload["market_memory_context"] = {
        "ELKA": {
            "memory_label": "IMPROVING",
            "appearances_total": 3,
            "recent_appearances": 2,
            "previous_score": 65,
            "last_score": 80,
            "previous_status": "WATCH",
            "last_status": "CANDIDATE",
        }
    }

    text = format_symbol_why(payload, "ELKA")

    assert "ذاكرة السهم: IMPROVING" in text
    assert "65" in text and "80" in text


def test_symbol_why_includes_confidence_v2() -> None:
    text = format_symbol_why(_sample_payload(), "ELKA")

    assert "الثقة الذكية: STRONG 88" in text
    assert "أسباب الثقة" in text
    assert "مخاطر الثقة" in text
    assert "مكونات مختصرة" in text


def test_symbol_why_includes_sector_intelligence() -> None:
    text = format_symbol_why(_sample_payload(), "ELKA")

    assert "القطاع: Real Estate" in text
    assert "علاقة السهم بالقطاع: SUPPORTED_BY_SECTOR" in text
    assert "أقوى من متوسط القطاع" in text
    assert "سبب القطاع" in text


def test_symbol_why_includes_portfolio_learning() -> None:
    text = format_symbol_why(_sample_payload(), "ELKA")

    assert "تعلم المحفظة: STRONG setups have performed well so far" in text


def test_daily_overview_shows_talib_fallback() -> None:
    payload = _sample_payload()
    payload["report_metadata"] = {
        "talib_available": False,
        "talib_mode": "fallback",
        "talib_reason": "talib package not installed",
    }
    text = format_daily_overview(payload)
    assert "TA-Lib: FALLBACK" in text
    assert "talib package not installed" in text


def test_best_opportunities_max_five() -> None:
    text = format_best_opportunities(_sample_payload(), limit=5)

    assert text.count("\n1.") == 1
    assert "\n5." in text
    assert "\n6." not in text
    assert "دي متابعة مش تنفيذ حقيقي." in text
    assert "Confirmation: GOOD" in text


def test_next_session_watch_mentions_closed_market() -> None:
    text = format_next_session_watch(_sample_payload(), limit=5)

    assert "السوق مقفول" in text
    assert "ELKA" in text
    assert "WATCH_NEXT_SESSION" in text


def test_sell_review_with_positions() -> None:
    text = format_sell_review(_sample_payload())

    assert "ABUK" in text
    assert "SELL_ALERT_TARGET" in text
    assert "EXIT_REVIEW_TARGET" in text
    assert "NEXT_OPEN_SESSION" in text


def test_sell_review_empty_message() -> None:
    payload = _sample_payload()
    payload["paper_portfolio"]["positions"] = []
    payload["decision_summary"]["positions"] = []
    payload["decision_summary"]["sell_alerts"] = []

    text = format_sell_review(payload)

    assert SELL_REVIEW_EMPTY_MESSAGE in text
    assert "TradingView" not in text or "📡 المصدر" in text


def test_paper_portfolio_formatter() -> None:
    text = format_paper_portfolio(_sample_payload())

    assert "محفظتي الورقية" in text
    assert "تعلم المحفظة" in text
    assert "5,815.53" in text
    assert "ABUK" in text


def test_market_status_formatter() -> None:
    text = format_market_status(_sample_payload())

    assert "BULLISH" in text
    assert "CLOSED" in text
    assert "Health Technology" in text


def test_warnings_max_six() -> None:
    text = format_warnings(_sample_payload(), limit=6)

    assert text.count("warning ") == 6
    assert "warning 7" not in text


def test_help_mentions_paper_only() -> None:
    text = format_help()

    assert "البوت ده استرشادي وورقي فقط" in text
    assert "WHY ELKA" in text


def test_parse_why_command() -> None:
    assert parse_why_command("WHY ELKA") == "ELKA"
    assert parse_why_command("why elka") == "ELKA"
    assert parse_why_command("hello") is None


def test_format_symbol_why_found() -> None:
    text = format_symbol_why(_sample_payload(), "ELKA")

    assert "ELKA" in text
    assert "السكور: 100" in text
    assert "Technical:" in text
    assert "Confirmation: GOOD" in text
    assert "WATCH_NEXT_SESSION" in text


def test_format_symbol_why_not_found() -> None:
    assert format_symbol_why(_sample_payload(), "MISSING") == WHY_NOT_FOUND_MESSAGE


def test_allowed_chat_id_logic() -> None:
    assert is_chat_authorized(12345, None) is True
    assert is_chat_authorized(12345, "") is True
    assert is_chat_authorized(12345, "12345") is True
    assert is_chat_authorized(99999, "12345") is False


def test_find_latest_report_json_and_load(tmp_path: Path) -> None:
    older = tmp_path / "egx_daily_report_20260701_120000.json"
    newer = tmp_path / "egx_daily_report_20260702_120000.json"
    older.write_text(json.dumps({"report_date": "2026-07-01"}), encoding="utf-8")
    newer.write_text(json.dumps({"report_date": "2026-07-02"}), encoding="utf-8")

    assert find_latest_report_json(tmp_path) == newer
    payload = load_latest_report_payload(tmp_path)
    assert payload is not None
    assert payload["report_date"] == "2026-07-02"


def test_build_main_menu_has_expected_buttons() -> None:
    menu = build_main_menu()
    labels = {button.text for row in menu.keyboard for button in row}

    assert "📊 تقرير النهارده" in labels
    assert BTN_OPPORTUNITIES in labels
    assert "🧠 ليه السهم ده؟" in labels
    assert "ℹ️ مساعدة" in labels


def test_submenus_include_new_section_buttons() -> None:
    opp_labels = {
        button.text for row in build_opportunities_menu().keyboard for button in row
    }
    sell_labels = {
        button.text for row in build_sell_portfolio_menu().keyboard for button in row
    }

    assert BTN_BEST_THREE in opp_labels
    assert BTN_SELL_ONLY in sell_labels
    assert BTN_PNL in sell_labels
    assert BTN_TRADE_LOG in sell_labels
    assert BTN_HOT_SECTORS in {
        button.text
        for row in build_market_menu().keyboard
        for button in row
    }
    assert BTN_ULTRA_SHORT in {
        button.text
        for row in build_market_menu().keyboard
        for button in row
    }


def test_collect_why_symbols_max_ten_unique_and_priority() -> None:
    payload = _sample_payload()
    strategy_lines = [
        (
            f"{index}. S{index:02d} | WATCH | Decision WATCH | "
            f"Entry 1.0 | Stop 0.9 | Target 1.1 | Timing READY"
        )
        for index in range(1, 12)
    ]
    for section in payload["sections"]:
        if section["title"] == "Strategy Signals":
            section["lines"] = strategy_lines
            break

    symbols = collect_why_symbols(payload, limit=10)

    assert len(symbols) == 10
    assert symbols[0] == "S01"
    assert symbols[9] == "S10"
    assert symbols.count("S01") == 1
    # Structured V2 context symbols may appear after parsed sections within limit.
    assert "COMI" not in symbols


def test_collect_why_symbols_adds_watch_list_after_strategy() -> None:
    symbols = collect_why_symbols(_sample_payload(), limit=10)

    assert "ELKA" in symbols
    assert "COMI" in symbols
    assert symbols.index("ELKA") < symbols.index("COMI")


def test_build_why_symbol_keyboard_uses_symbol_callbacks() -> None:
    keyboard = build_why_symbol_keyboard(["ELKA", "LCSW", "TANM"])
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]

    assert callbacks == ["why:ELKA", "why:LCSW", "why:TANM"]


def test_format_symbol_why_response_includes_advisory_note() -> None:
    text = format_symbol_why_response(_sample_payload(), "ELKA")

    assert "السكور: 100" in text
    assert WHY_ADVISORY_NOTE in text


def test_why_symbol_text_fallback_still_parses() -> None:
    assert parse_why_command("WHY ELKA") == "ELKA"
    assert format_symbol_why_response(_sample_payload(), "ELKA").endswith(WHY_ADVISORY_NOTE)


def test_format_best_three_returns_max_three() -> None:
    text = format_best_three(_sample_payload())

    assert "📌 أفضل 3" in text
    assert "\n1." in text
    assert "\n3." in text
    assert "\n4." not in text


def test_format_sell_only_filters_review_labels() -> None:
    text = format_sell_only(_sample_payload())

    assert "ABUK" in text
    assert "SELL_ALERT_TARGET" in text
    assert "EXIT_REVIEW_TARGET" in text


def test_format_sell_only_empty_when_no_matching_labels() -> None:
    payload = _sample_payload()
    payload["paper_portfolio"]["positions"] = [
        {
            "symbol": "HOLD",
            "decision": "HOLD",
            "exit_plan": "HOLD_PROFIT_RUNNING",
        }
    ]
    payload["decision_summary"]["positions"] = []
    payload["decision_summary"]["sell_alerts"] = []

    text = format_sell_only(payload)

    assert "مفيش إشارات بيع" in text


def test_format_pnl_summary_uses_paper_performance() -> None:
    text = format_pnl_summary(_sample_payload())

    assert "100,000.00" in text
    assert "26,509.95" in text
    assert "P&L محقق" in text
    assert "مراكز مفتوحة: 1" in text


def test_format_hot_sectors_returns_max_five() -> None:
    text = format_hot_sectors(_sample_payload(), limit=5)

    assert "Health Technology" in text
    assert "Finance" in text
    assert "Utilities" not in text
    assert text.strip().splitlines()[0] == "🔥 القطاعات السخنة:"


def test_format_ultra_short_max_lines_and_executive_fields() -> None:
    text = format_ultra_short(_sample_payload(), max_lines=8)
    lines = [line for line in text.splitlines() if line.strip()]

    assert len(lines) <= 8
    assert "CLOSED | BULLISH" in text
    assert "ELKA" in text
    assert "ABUK" in text
    assert text.strip().endswith("ورقي واسترشادي فقط.")


def _cloud_payload_without_portfolio() -> dict:
    return {
        "report_date": "2026-07-03",
        "created_at": "2026-07-03T10:00:00+00:00",
        "market_session": {"status": "CLOSED"},
        "executive_summary": {
            "paper_pnl": "n/a",
            "market": "CLOSED | BULLISH | Paper entries disabled",
        },
        "decision_summary": {
            "sell_alerts": ["ABUK"],
            "positions": [
                {
                    "symbol": "ABUK",
                    "decision": "SELL_ALERT_TARGET",
                    "review_timing": "NEXT_OPEN_SESSION",
                }
            ],
        },
        "paper_portfolio": {
            "available": False,
            "message": "No paper portfolio data found.",
            "open_positions_count": 0,
        },
        "paper_trading_performance": {
            "available": False,
            "message": "No paper portfolio data found.",
        },
        "report_metadata": {
            "generated_at": "2026-07-03T10:00:00+00:00",
            "data_provider_label": "TradingView",
            "market_status": "CLOSED",
            "paper_portfolio_present": False,
            "paper_performance_present": False,
            "paper_portfolio_storage_on_server": False,
        },
        "sections": [
            {
                "title": "Paper Portfolio",
                "lines": ["- No paper portfolio data found."],
            },
            {
                "title": "Executive Summary",
                "lines": ["- Paper P&L: n/a"],
            },
        ],
    }


def test_format_paper_portfolio_cloud_state_message() -> None:
    text = format_paper_portfolio(_cloud_payload_without_portfolio())

    assert CLOUD_PORTFOLIO_STATE_MESSAGE in text
    assert "TradingView" in text
    assert "CLOSED" in text


def test_format_pnl_summary_cloud_state_message() -> None:
    text = format_pnl_summary(_cloud_payload_without_portfolio())

    assert CLOUD_PORTFOLIO_STATE_MESSAGE in text
    assert "📡 المصدر" in text
    assert "1234567890:AA" not in text


def test_format_pnl_summary_with_executive_pnl_only() -> None:
    payload = _cloud_payload_without_portfolio()
    payload["executive_summary"]["paper_pnl"] = "+1,500.00 (+1.50%) | Open positions: 0"

    text = format_pnl_summary(payload)

    assert "+1,500.00" in text
    assert CLOUD_PORTFOLIO_STATE_MESSAGE in text


def test_format_sell_portfolio_menu_intro_explains_cloud_state() -> None:
    text = format_sell_portfolio_menu_intro(_cloud_payload_without_portfolio())

    assert "🚨 البيع والمحفظة" in text
    assert CLOUD_PORTFOLIO_STATE_MESSAGE in text
    assert "ABUK" in text
    assert "اختار من القائمة" in text


def test_format_sell_review_uses_decision_summary_without_portfolio_json() -> None:
    text = format_sell_review(_cloud_payload_without_portfolio())

    assert "ABUK" in text
    assert "SELL_ALERT_TARGET" in text
    assert "NEXT_OPEN_SESSION" in text
