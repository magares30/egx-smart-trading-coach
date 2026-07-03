"""Telegram interactive bot — reads latest saved daily report JSON only."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from config import settings
from core.cloud_report_runner import find_latest_report_json
from core.cloud_state_store import load_latest_report_json_payload
from core.latest_report_sections import (
    CLOUD_PORTFOLIO_STATE_MESSAGE,
    decision_summary_sell_positions,
    format_report_metadata_block,
    resolve_portfolio_data_status,
)
from core.market_hours_auto_refresh import is_auto_refresh_enabled
from core.closed_market_digest import (
    format_closed_market_digest_arabic_block,
    format_closed_market_reason_arabic,
)
from core.talib_technical import format_talib_runtime_telegram_line

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_ALLOWED_CHAT_ID_ENV = "TELEGRAM_ALLOWED_CHAT_ID"
CALLBACK_WHY_PREFIX = "why:"

NO_REPORT_MESSAGE = (
    "لسه مفيش تقرير محفوظ. اضغط 🔄 حدّث التقرير دلوقتي."
)
UNAUTHORIZED_MESSAGE = "مش مسموح ليك تستخدم البوت ده."
WHY_PROMPT_MESSAGE = "اكتب رمز السهم كده: WHY ELKA"
WHY_SYMBOL_PROMPT_MESSAGE = "اختار السهم اللي عايز تفهمه من آخر تقرير:"
WHY_NO_SYMBOLS_MESSAGE = "مش لاقي أسهم في آخر تقرير محفوظ."
WHY_NOT_FOUND_MESSAGE = "مش لاقي السهم ده في آخر تقرير محفوظ."
WHY_ADVISORY_NOTE = "دي متابعة وتحليل ورقي فقط، مش أمر شراء أو بيع."
SELL_REVIEW_EMPTY_MESSAGE = "مفيش مراجعات بيع مهمة دلوقتي."
SELL_ONLY_EMPTY_MESSAGE = "مفيش إشارات بيع أو مراجعة خروج مهمة دلوقتي."
HOT_SECTORS_EMPTY_MESSAGE = "مفيش بيانات قطاعات كفاية في آخر تقرير."

BTN_DAILY = "📊 تقرير النهارده"
BTN_REFRESH_REPORT = "🔄 حدّث التقرير دلوقتي"
BTN_OPPORTUNITIES = "🔥 الفرص"
BTN_SELL_PORTFOLIO = "🚨 البيع والمحفظة"
BTN_MARKET_MENU = "📈 السوق"
BTN_WHY = "🧠 ليه السهم ده؟"
BTN_WARNINGS = "⚠️ التحذيرات"
BTN_HELP = "ℹ️ مساعدة"
BTN_BACK = "⬅️ القائمة الرئيسية"

BTN_BEST_THREE = "📌 أفضل 3 بس"
BTN_BEST = "🔥 أفضل فرص"
BTN_NEXT_SESSION = "👀 راقب الجلسة الجاية"
BTN_SELL = "🚨 مراجعة بيع"
BTN_SELL_ONLY = "🚨 البيع فقط"
BTN_PORTFOLIO = "💼 محفظتي الورقية"
BTN_PNL = "💰 الأرباح والخسائر"
BTN_MARKET = "📈 حالة السوق"
BTN_HOT_SECTORS = "🔥 القطاعات السخنة"
BTN_ULTRA_SHORT = "🧾 نسخة مختصرة"

MAIN_MENU_BUTTONS = (
    BTN_DAILY,
    BTN_REFRESH_REPORT,
    BTN_OPPORTUNITIES,
    BTN_SELL_PORTFOLIO,
    BTN_MARKET_MENU,
    BTN_WHY,
    BTN_WARNINGS,
    BTN_HELP,
    BTN_BACK,
)

OPPORTUNITIES_MENU_BUTTONS = (BTN_BEST_THREE, BTN_BEST, BTN_NEXT_SESSION, BTN_BACK)
SELL_PORTFOLIO_MENU_BUTTONS = (
    BTN_SELL,
    BTN_SELL_ONLY,
    BTN_PORTFOLIO,
    BTN_PNL,
    BTN_BACK,
)
MARKET_MENU_BUTTONS = (BTN_MARKET, BTN_HOT_SECTORS, BTN_ULTRA_SHORT, BTN_BACK)

SELL_ONLY_LABELS = frozenset(
    {
        "SELL_ALERT_TARGET",
        "SELL_ALERT_STOP",
        "EXIT_REVIEW_TARGET",
        "EXIT_REVIEW_STOP",
    }
)

WELCOME_MESSAGE = (
    "أهلاً بيك في EGX Smart Trading Coach 👋\n\n"
    "اختار من القائمة تحت عشان تشوف آخر تقرير محفوظ.\n\n"
    "ملاحظة: ده بوت استرشادي وورقي فقط، مفيش تنفيذ حقيقي."
)

_STRATEGY_HEADER_RE = re.compile(
    r"^\d+\.\s+(?P<symbol>[A-Z0-9]+)\s+\|\s+(?P<strategy_decision>\w+)"
    r"(?:\s+\|\s+Decision\s+(?P<decision_label>\w+))?"
    r"(?:\s+\|\s+Entry\s+(?P<entry>[\d.]+)\s+\|\s+Stop\s+(?P<stop>[\d.]+)"
    r"\s+\|\s+Target\s+(?P<target>[\d.]+))?"
    r"(?:\s+\|\s+Timing\s+(?P<timing>\w+))?"
)
_CANDIDATE_HEADER_RE = re.compile(
    r"^\d+\.\s+(?P<symbol>[A-Z0-9]+)\s+\|\s+Score\s+(?P<score>\d+)"
)
_SCORE_FROM_HEADER_RE = re.compile(r"Score\s+(?P<score>\d+)", re.IGNORECASE)


def get_bot_token() -> str | None:
    token = os.environ.get(TELEGRAM_BOT_TOKEN_ENV, "").strip()
    return token or None


def get_allowed_chat_id() -> str | None:
    chat_id = os.environ.get(TELEGRAM_ALLOWED_CHAT_ID_ENV, "").strip()
    return chat_id or None


def is_chat_authorized(chat_id: int, allowed_chat_id: str | None) -> bool:
    if not allowed_chat_id:
        return True
    return str(chat_id) == allowed_chat_id.strip()


def load_latest_report_payload(
    reports_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Load the newest saved daily report JSON payload."""
    return load_latest_report_json_payload(reports_dir=reports_dir)


def build_main_menu():
    """Build the Egyptian Arabic main reply keyboard menu."""
    from telegram import KeyboardButton, ReplyKeyboardMarkup

    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_DAILY), KeyboardButton(BTN_REFRESH_REPORT)],
            [KeyboardButton(BTN_OPPORTUNITIES), KeyboardButton(BTN_SELL_PORTFOLIO)],
            [KeyboardButton(BTN_MARKET_MENU), KeyboardButton(BTN_WHY)],
            [KeyboardButton(BTN_WARNINGS), KeyboardButton(BTN_HELP)],
        ],
        resize_keyboard=True,
    )


def build_opportunities_menu():
    """Build the opportunities submenu reply keyboard."""
    from telegram import KeyboardButton, ReplyKeyboardMarkup

    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_BEST_THREE), KeyboardButton(BTN_BEST)],
            [KeyboardButton(BTN_NEXT_SESSION)],
            [KeyboardButton(BTN_BACK)],
        ],
        resize_keyboard=True,
    )


def build_sell_portfolio_menu():
    """Build the sell/portfolio submenu reply keyboard."""
    from telegram import KeyboardButton, ReplyKeyboardMarkup

    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_SELL), KeyboardButton(BTN_SELL_ONLY)],
            [KeyboardButton(BTN_PORTFOLIO), KeyboardButton(BTN_PNL)],
            [KeyboardButton(BTN_BACK)],
        ],
        resize_keyboard=True,
    )


def build_market_menu():
    """Build the market submenu reply keyboard."""
    from telegram import KeyboardButton, ReplyKeyboardMarkup

    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_MARKET), KeyboardButton(BTN_HOT_SECTORS)],
            [KeyboardButton(BTN_ULTRA_SHORT)],
            [KeyboardButton(BTN_BACK)],
        ],
        resize_keyboard=True,
    )


def build_why_symbol_keyboard(symbols: list[str]):
    """Build inline symbol buttons for the WHY flow."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for symbol in symbols:
        row.append(
            InlineKeyboardButton(
                symbol,
                callback_data=f"{CALLBACK_WHY_PREFIX}{symbol}",
            )
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _section_lines(payload: dict[str, Any], title: str) -> list[str]:
    for section in payload.get("sections", []):
        if not isinstance(section, dict):
            continue
        if section.get("title") == title:
            lines = section.get("lines", [])
            return [str(line) for line in lines] if isinstance(lines, list) else []
    return []


def _parse_grouped_section(lines: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        header_match = _CANDIDATE_HEADER_RE.match(line) or _STRATEGY_HEADER_RE.match(line)
        if header_match:
            if current is not None:
                items.append(current)
            current = {"header": line, **header_match.groupdict()}
            continue
        if current is None:
            continue
        detail_key = "details"
        current.setdefault(detail_key, []).append(line)
    if current is not None:
        items.append(current)
    return items


def _strategy_signals(payload: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = _parse_grouped_section(_section_lines(payload, "Strategy Signals"))
    confirmation_lookup = {
        str(item.get("symbol", "")).upper(): item
        for item in (payload.get("confirmation_summary") or {}).get("signals", [])
        if isinstance(item, dict) and item.get("symbol")
    }
    decision_lookup = {
        str(item.get("symbol", "")).upper(): item
        for item in (payload.get("decision_summary") or {}).get("signals", [])
        if isinstance(item, dict) and item.get("symbol")
    }
    for item in parsed:
        symbol = str(item.get("symbol", "")).upper()
        confirmation = confirmation_lookup.get(symbol, {})
        decision = decision_lookup.get(symbol, {})
        item["confirmation_label"] = confirmation.get("confirmation_label")
        item["confirmation_text"] = confirmation.get("confirmation_text")
        item["decision"] = decision.get("decision") or item.get("decision_label")
        item["strategy_decision"] = decision.get("strategy_decision") or item.get(
            "strategy_decision"
        )
    return parsed


def _top_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return _parse_grouped_section(_section_lines(payload, "Top Candidates"))


def _watch_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return _parse_grouped_section(_section_lines(payload, "Watch List"))


def collect_why_symbols(
    payload: dict[str, Any] | None,
    *,
    limit: int = 10,
) -> list[str]:
    """Collect unique symbols for WHY buttons: strategy, then candidates, then watch."""
    if payload is None:
        return []

    ordered: list[str] = []
    seen: set[str] = set()

    def add_symbol(symbol_value: object) -> None:
        if len(ordered) >= limit:
            return
        symbol = str(symbol_value).strip().upper()
        if not symbol or symbol in seen:
            return
        seen.add(symbol)
        ordered.append(symbol)

    for item in _strategy_signals(payload):
        add_symbol(item.get("symbol"))
    for item in _top_candidates(payload):
        add_symbol(item.get("symbol"))
    for item in _watch_list(payload):
        add_symbol(item.get("symbol"))

    return ordered


def _detail_line(details: list[str], prefix: str) -> str | None:
    for line in details:
        if line.startswith(prefix):
            return line
    return None


def format_daily_overview(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return NO_REPORT_MESSAGE

    executive = payload.get("executive_summary") or {}
    best_ideas = executive.get("best_ideas") or []
    best_text = ", ".join(str(symbol) for symbol in best_ideas) if best_ideas else "لا يوجد"
    metadata = payload.get("report_metadata") or {}
    closed_digest = metadata.get("closed_market_digest") or {}

    lines: list[str] = []
    lines.extend(format_closed_market_digest_arabic_block(closed_digest))
    lines.extend(
        [
            f"📅 التاريخ: {payload.get('report_date', 'غير متاح')}",
            f"📈 السوق: {executive.get('market', 'غير متاح')}",
            f"⚡ الإجراء: {executive.get('action', 'غير متاح')}",
            f"🔥 أفضل أفكار: {best_text}",
            f"✅ التأكيد: {executive.get('confirmation', 'غير متاح')}",
            f"💰 P&L ورقي: {executive.get('paper_pnl', 'غير متاح')}",
            f"⚠️ المخاطر: {executive.get('main_risk', 'غير متاح')}",
            format_talib_runtime_telegram_line(metadata),
        ]
    )

    decision_summary = payload.get("decision_summary") or {}
    sell_alerts = decision_summary.get("sell_alerts") or []
    if sell_alerts:
        lines.append(f"🚨 تنبيهات بيع (الجلسة الجاية): {', '.join(str(s) for s in sell_alerts)}")

    warning_lines = [
        str(warning).strip()
        for warning in (payload.get("warnings") or [])
        if str(warning).strip()
    ][:3]
    if warning_lines:
        lines.append("⚠️ تحذيرات:")
        lines.extend(f"- {warning}" for warning in warning_lines)

    return "\n".join(lines)


def format_best_opportunities(payload: dict[str, Any] | None, *, limit: int = 5) -> str:
    if payload is None:
        return NO_REPORT_MESSAGE

    signals = _strategy_signals(payload)[:limit]
    if not signals:
        return "مفيش فرص واضحة في آخر تقرير."

    lines = ["🔥 أفضل الفرص:", ""]
    for index, signal in enumerate(signals, start=1):
        lines.extend(_format_signal_block(signal, index=index))
        lines.append("")

    lines.append("دي متابعة مش تنفيذ حقيقي.")
    return "\n".join(lines).rstrip()


def _format_signal_block(signal: dict[str, Any], *, index: int) -> list[str]:
    symbol = signal.get("symbol", "?")
    decision = signal.get("decision") or signal.get("strategy_decision") or "غير متاح"
    confirmation = signal.get("confirmation_text") or signal.get(
        "confirmation_label"
    ) or "غير متاح"
    entry = signal.get("entry")
    stop = signal.get("stop")
    target = signal.get("target")
    timing = signal.get("timing")

    lines = [f"{index}. {symbol} | {decision}", f"   تأكيد: {confirmation}"]
    if entry and stop and target:
        lines.append(f"   دخول {entry} | وقف {stop} | هدف {target}")
    if timing:
        lines.append(f"   توقيت: {timing}")
    return lines


def _format_signal_short(signal: dict[str, Any], *, index: int) -> str:
    symbol = signal.get("symbol", "?")
    decision = signal.get("decision") or signal.get("strategy_decision") or "غير متاح"
    confirmation = signal.get("confirmation_text") or signal.get(
        "confirmation_label"
    ) or "غير متاح"
    entry = signal.get("entry")
    stop = signal.get("stop")
    target = signal.get("target")
    line = f"{index}. {symbol} | {decision} | {confirmation}"
    if entry and stop and target:
        line += f" | {entry}/{stop}/{target}"
    return line


def format_best_three(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return NO_REPORT_MESSAGE

    signals = _strategy_signals(payload)[:3]
    if not signals:
        return "مفيش فرص واضحة في آخر تقرير."

    lines = ["📌 أفضل 3 بس:", ""]
    lines.extend(_format_signal_short(signal, index=index) for index, signal in enumerate(signals, start=1))
    lines.extend(["", "دي متابعة مش تنفيذ حقيقي."])
    return "\n".join(lines)


def format_next_session_watch(payload: dict[str, Any] | None, *, limit: int = 5) -> str:
    if payload is None:
        return NO_REPORT_MESSAGE

    decision_summary = payload.get("decision_summary") or {}
    watch_symbols = [
        str(symbol).upper()
        for symbol in decision_summary.get("watch_next_session", [])
    ]
    signals = [
        signal
        for signal in _strategy_signals(payload)
        if str(signal.get("symbol", "")).upper() in watch_symbols
    ][:limit]

    if not signals and watch_symbols:
        signals = [{"symbol": symbol, "decision": "WATCH_NEXT_SESSION"} for symbol in watch_symbols[:limit]]

    if not signals:
        return "مفيش أسهم للمتابعة في الجلسة الجاية حالياً."

    market_session = payload.get("market_session") or {}
    metadata = payload.get("report_metadata") or {}
    closed_digest = metadata.get("closed_market_digest") or {}
    lines = ["👀 راقب الجلسة الجاية:", ""]
    if closed_digest.get("enabled") or market_session.get("status") == "CLOSED":
        lines.append("السوق مقفول دلوقتي — الإشارات دي للمتابعة مش للتنفيذ الفوري.")
        lines.append("")

    for index, signal in enumerate(signals, start=1):
        symbol = signal.get("symbol", "?")
        decision = signal.get("decision", "WATCH_NEXT_SESSION")
        confirmation = signal.get("confirmation_text") or "غير متاح"
        lines.append(f"{index}. {symbol} | {decision}")
        lines.append(f"   {confirmation}")
        lines.append("")

    return "\n".join(lines).rstrip()


def format_sell_review(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return NO_REPORT_MESSAGE

    portfolio = payload.get("paper_portfolio") or {}
    positions = portfolio.get("positions") or []
    sell_positions = [
        position
        for position in positions
        if isinstance(position, dict)
        and str(position.get("decision", "")).startswith("SELL_ALERT")
    ]
    if not sell_positions:
        sell_positions = decision_summary_sell_positions(payload)

    if not sell_positions:
        status = resolve_portfolio_data_status(payload)
        lines = ["🚨 مراجعة بيع:", ""] + format_report_metadata_block(payload) + [""]
        lines.append(SELL_REVIEW_EMPTY_MESSAGE)
        if status["cloud_state_missing"]:
            lines.extend(["", CLOUD_PORTFOLIO_STATE_MESSAGE])
        return "\n".join(lines)

    lines = ["🚨 مراجعة بيع:", ""] + format_report_metadata_block(payload) + [""]
    for index, position in enumerate(sell_positions, start=1):
        symbol = position.get("symbol", "?")
        decision = position.get("decision", "غير متاح")
        exit_plan = position.get("exit_plan", "غير متاح")
        pnl = position.get("unrealized_pnl")
        pnl_pct = position.get("unrealized_pnl_pct")
        review_timing = position.get("review_timing") or position.get("exit_timing")

        pnl_text = "غير متاح"
        if pnl is not None:
            sign = "+" if float(pnl) > 0 else ""
            pnl_text = f"{sign}{float(pnl):,.2f}"
            if pnl_pct is not None:
                pnl_text += f" ({float(pnl_pct):+.2f}%)"

        lines.append(f"{index}. {symbol}")
        lines.append(f"   قرار: {decision}")
        if exit_plan != "غير متاح":
            lines.append(f"   خطة خروج: {exit_plan}")
        lines.append(f"   P&L: {pnl_text}")
        if review_timing:
            lines.append(f"   مراجعة: {review_timing}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _position_has_sell_or_exit_review(position: dict[str, Any]) -> bool:
    decision = str(position.get("decision", ""))
    exit_plan = str(position.get("exit_plan", ""))
    return decision in SELL_ONLY_LABELS or exit_plan in SELL_ONLY_LABELS


def format_sell_only(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return NO_REPORT_MESSAGE

    portfolio = payload.get("paper_portfolio") or {}
    positions = [
        position
        for position in (portfolio.get("positions") or [])
        if isinstance(position, dict) and _position_has_sell_or_exit_review(position)
    ]
    if not positions:
        positions = decision_summary_sell_positions(payload)

    if not positions:
        status = resolve_portfolio_data_status(payload)
        lines = ["🚨 البيع فقط:", ""] + format_report_metadata_block(payload) + [""]
        lines.append(SELL_ONLY_EMPTY_MESSAGE)
        if status["cloud_state_missing"]:
            lines.extend(["", CLOUD_PORTFOLIO_STATE_MESSAGE])
        return "\n".join(lines)

    lines = ["🚨 البيع فقط:", ""] + format_report_metadata_block(payload) + [""]
    for index, position in enumerate(positions, start=1):
        symbol = position.get("symbol", "?")
        decision = position.get("decision", "غير متاح")
        exit_plan = position.get("exit_plan", "غير متاح")
        review_timing = position.get("review_timing") or position.get("exit_timing")
        lines.append(f"{index}. {symbol} | {decision} | {exit_plan}")
        if review_timing:
            lines.append(f"   مراجعة: {review_timing}")

    lines.extend(["", "مراجعة ورقية فقط، مفيش تنفيذ حقيقي."])
    return "\n".join(lines)


def format_pnl_summary(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return NO_REPORT_MESSAGE

    performance = payload.get("paper_trading_performance") or {}
    portfolio = payload.get("paper_portfolio") or {}
    status = resolve_portfolio_data_status(payload)

    def fmt_amount(value: object | None) -> str:
        if value is None:
            return "غير متاح"
        amount = float(value)
        sign = "+" if amount > 0 else ""
        return f"{sign}{amount:,.2f}"

    lines = ["💰 الأرباح والخسائر:", ""] + format_report_metadata_block(payload) + [""]

    if status["has_performance_json"] or status["has_portfolio_json"]:
        initial_capital = performance.get("initial_capital")
        current_equity = performance.get("current_equity") or portfolio.get("total_equity")
        total_pnl = performance.get("total_pnl")
        total_return_pct = performance.get("total_return_pct")
        unrealized = performance.get("unrealized_pnl") or portfolio.get("unrealized_pnl")
        realized = performance.get("realized_pnl")
        open_count = performance.get("open_positions_count") or portfolio.get(
            "open_positions_count", 0
        )

        lines.extend(
            [
                f"رأس المال الابتدائي: {fmt_amount(initial_capital)}",
                f"رأس المال الحالي: {fmt_amount(current_equity)}",
                f"إجمالي P&L: {fmt_amount(total_pnl)}",
            ]
        )
        if total_return_pct is not None:
            lines[-1] += f" ({float(total_return_pct):+.2f}%)"
        lines.extend(
            [
                f"P&L غير محقق: {fmt_amount(unrealized)}",
                f"P&L محقق: {fmt_amount(realized)}",
                f"مراكز مفتوحة: {open_count}",
                "",
                "محفظة ورقية فقط.",
            ]
        )
        return "\n".join(lines)

    if status["executive_pnl"]:
        lines.append(f"من الملخص التنفيذي: P&L ورقي {status['executive_pnl']}")

    section_lines = status["section_pnl_lines"]
    if section_lines:
        lines.append("من نص التقرير:")
        for line in section_lines[:5]:
            lines.append(f"• {line}")

    if status["cloud_state_missing"]:
        lines.extend(["", CLOUD_PORTFOLIO_STATE_MESSAGE])
    elif not status["executive_pnl"] and not section_lines:
        lines.append("بيانات الأرباح والخسائر غير متاحة في آخر تقرير.")
        lines.extend(["", CLOUD_PORTFOLIO_STATE_MESSAGE])

    return "\n".join(lines)


def format_paper_portfolio(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return NO_REPORT_MESSAGE

    portfolio = payload.get("paper_portfolio") or {}
    status = resolve_portfolio_data_status(payload)
    lines = ["💼 محفظتي الورقية:", ""] + format_report_metadata_block(payload) + [""]

    if not portfolio.get("available"):
        if status["cloud_state_missing"]:
            lines.append(CLOUD_PORTFOLIO_STATE_MESSAGE)
        elif portfolio.get("open_positions_count", 0) == 0:
            lines.append("المحفظة الورقية فارغة على السيرفر (مفيش مراكز مفتوحة).")
        else:
            lines.append("محفظة ورقية غير متاحة في آخر تقرير.")
        return "\n".join(lines)

    lines.extend(
        [
            f"كاش: {float(portfolio.get('cash', 0)):,.2f}",
            f"مراكز مفتوحة: {portfolio.get('open_positions_count', 0)}",
            f"قيمة السوق: {float(portfolio.get('market_value', 0)):,.2f}",
            f"إجمالي رأس المال: {float(portfolio.get('total_equity', 0)):,.2f}",
        ]
    )

    unrealized = portfolio.get("unrealized_pnl")
    unrealized_pct = portfolio.get("unrealized_pnl_pct")
    if unrealized is not None:
        sign = "+" if float(unrealized) > 0 else ""
        pnl_line = f"P&L غير محقق: {sign}{float(unrealized):,.2f}"
        if unrealized_pct is not None:
            pnl_line += f" ({float(unrealized_pct):+.2f}%)"
        lines.append(pnl_line)

    performance = payload.get("paper_trading_performance") or {}
    total_pnl = performance.get("total_pnl")
    total_return_pct = performance.get("total_return_pct")
    if total_pnl is not None:
        sign = "+" if float(total_pnl) > 0 else ""
        total_line = f"إجمالي P&L: {sign}{float(total_pnl):,.2f}"
        if total_return_pct is not None:
            total_line += f" ({float(total_return_pct):+.2f}%)"
        lines.append(total_line)

    positions = [
        position for position in (portfolio.get("positions") or []) if isinstance(position, dict)
    ]
    if positions:
        lines.extend(["", "أهم المراكز:"])
        for index, position in enumerate(positions[:3], start=1):
            symbol = position.get("symbol", "?")
            market_value = float(position.get("market_value", 0))
            pnl = position.get("unrealized_pnl")
            pnl_text = ""
            if pnl is not None:
                sign = "+" if float(pnl) > 0 else ""
                pnl_text = f" | P&L {sign}{float(pnl):,.2f}"
            lines.append(f"{index}. {symbol} | قيمة {market_value:,.2f}{pnl_text}")

    return "\n".join(lines)


def format_sell_portfolio_menu_intro(payload: dict[str, Any] | None) -> str:
    """Intro text for the sell/portfolio submenu with honest Cloud Run context."""
    if payload is None:
        return f"🚨 البيع والمحفظة:\n\n{NO_REPORT_MESSAGE}"

    status = resolve_portfolio_data_status(payload)
    decision_summary = payload.get("decision_summary") or {}
    sell_alerts = [
        str(symbol).upper()
        for symbol in decision_summary.get("sell_alerts") or []
        if str(symbol).strip()
    ]

    lines = ["🚨 البيع والمحفظة:", ""] + format_report_metadata_block(payload) + [""]

    if status["has_portfolio_json"]:
        open_count = (payload.get("paper_portfolio") or {}).get("open_positions_count", 0)
        lines.append(f"💼 المحفظة الورقية: متوفرة ({open_count} مركز مفتوح)")
    elif status["cloud_state_missing"]:
        lines.append(f"💼 المحفظة: {CLOUD_PORTFOLIO_STATE_MESSAGE}")
    else:
        lines.append("💼 المحفظة: غير متاحة في التقرير الحالي")

    if sell_alerts:
        lines.append(f"🚨 تنبيهات بيع من التقرير: {', '.join(sell_alerts)}")
    else:
        lines.append("🚨 مراجعة البيع: من إشارات التقرير ومحفظة السيرفر إن وُجدت")

    lines.extend(["", "اختار من القائمة:"])
    return "\n".join(lines)


def format_hot_sectors(payload: dict[str, Any] | None, *, limit: int = 5) -> str:
    if payload is None:
        return NO_REPORT_MESSAGE

    hot_sectors = [
        sector
        for sector in (payload.get("sector_momentum") or [])
        if isinstance(sector, dict) and sector.get("status") == "HOT"
    ][:limit]

    if not hot_sectors:
        return HOT_SECTORS_EMPTY_MESSAGE

    lines = ["🔥 القطاعات السخنة:", ""]
    for index, sector in enumerate(hot_sectors, start=1):
        name = sector.get("sector", "غير معروف")
        status = sector.get("status", "HOT")
        score = sector.get("sector_score", "?")
        avg_change = sector.get("avg_change_percent")
        candidates_count = sector.get("candidates_count", "?")
        change_text = (
            f" | متوسط {float(avg_change):+.1f}%"
            if avg_change is not None
            else ""
        )
        lines.append(
            f"{index}. {name} | {status} | Score {score}{change_text} | "
            f"Candidates {candidates_count}"
        )
    return "\n".join(lines)


def format_ultra_short(payload: dict[str, Any] | None, *, max_lines: int = 8) -> str:
    if payload is None:
        return NO_REPORT_MESSAGE

    executive = payload.get("executive_summary") or {}
    decision_summary = payload.get("decision_summary") or {}
    best_ideas = executive.get("best_ideas") or []
    sell_alerts = decision_summary.get("sell_alerts") or []

    lines = [
        "🧾 نسخة مختصرة:",
        f"السوق: {executive.get('market', 'غير متاح')}",
        f"الإجراء: {executive.get('action', 'غير متاح')}",
        f"أفضل أفكار: {', '.join(str(symbol) for symbol in best_ideas) or 'لا يوجد'}",
    ]
    if sell_alerts:
        lines.append(f"تنبيهات بيع: {', '.join(str(symbol) for symbol in sell_alerts)}")
    lines.extend(
        [
            f"P&L ورقي: {executive.get('paper_pnl', 'غير متاح')}",
            f"المخاطر: {executive.get('main_risk', 'غير متاح')}",
            "ورقي واسترشادي فقط.",
        ]
    )
    return "\n".join(lines[:max_lines])


def format_market_status(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return NO_REPORT_MESSAGE

    mood_lines = _section_lines(payload, "Market Mood")
    mood_text = mood_lines[0].lstrip("- ").strip() if mood_lines else "غير متاح"

    session = payload.get("market_session") or {}
    session_status = session.get("status", "غير متاح")
    session_note = session.get("note")
    metadata = payload.get("report_metadata") or {}
    closed_digest = metadata.get("closed_market_digest") or {}

    breadth = payload.get("market_breadth_mood") or {}
    breadth_bits: list[str] = []
    if breadth.get("mood"):
        breadth_bits.append(f"مزاج العرض: {breadth['mood']}")
    if breadth.get("advancers_count") is not None and breadth.get("symbols_count"):
        breadth_bits.append(
            f"صاعد {breadth['advancers_count']}/{breadth['symbols_count']}"
        )
    if breadth.get("avg_change_percent") is not None:
        breadth_bits.append(f"متوسط التغير {float(breadth['avg_change_percent']):+.1f}%")

    lines = [
        "📈 حالة السوق:",
        "",
        f"مزاج السوق: {mood_text}",
        f"جلسة السوق: {session_status}",
    ]
    closed_reason = format_closed_market_reason_arabic(closed_digest)
    if closed_reason:
        lines.append(f"سبب الإغلاق: {closed_reason}")
    if closed_digest.get("enabled"):
        lines.append(f"📅 آخر بيانات أسعار: {closed_digest.get('price_data_date', 'غير متاح')}")
        if closed_digest.get("is_price_data_stale"):
            lines.append("⚠️ التقرير مبني على بيانات قد تكون قديمة لحد الجلسة الجاية")
        lines.append("👀 المتابعة والبيع للجلسة الجاية فقط — مفيش دخول ورقي جديد")
    if session_note:
        lines.append(f"ملاحظة: {session_note}")
    if breadth_bits:
        lines.append(f"العرض: {' | '.join(breadth_bits)}")

    hot_sectors = [
        sector
        for sector in (payload.get("sector_momentum") or [])
        if isinstance(sector, dict) and sector.get("status") == "HOT"
    ][:3]
    if hot_sectors:
        lines.extend(["", "قطاعات ساخنة:"])
        for index, sector in enumerate(hot_sectors, start=1):
            name = sector.get("sector", "غير معروف")
            score = sector.get("sector_score", "?")
            avg_change = sector.get("avg_change_percent")
            change_text = (
                f" | متوسط {float(avg_change):+.1f}%"
                if avg_change is not None
                else ""
            )
            lines.append(f"{index}. {name} | Score {score}{change_text}")

    return "\n".join(lines)


def format_warnings(payload: dict[str, Any] | None, *, limit: int = 6) -> str:
    if payload is None:
        return NO_REPORT_MESSAGE

    warnings = [
        str(warning).strip()
        for warning in (payload.get("warnings") or [])
        if str(warning).strip()
    ][:limit]
    if not warnings:
        return "مفيش تحذيرات مهمة في آخر تقرير."

    lines = ["⚠️ التحذيرات:", ""]
    for index, warning in enumerate(warnings, start=1):
        lines.append(f"{index}. {warning}")
    return "\n".join(lines)


def format_help() -> str:
    lines = [
        "ℹ️ مساعدة:",
        "",
        f"{BTN_DAILY} — ملخص سريع لآخر تقرير",
        f"{BTN_REFRESH_REPORT} — يشغّل تقرير EGX من السيرفر",
        f"{BTN_OPPORTUNITIES} — {BTN_BEST_THREE} / {BTN_BEST} / {BTN_NEXT_SESSION}",
        f"{BTN_SELL_PORTFOLIO} — {BTN_SELL} / {BTN_SELL_ONLY} / {BTN_PORTFOLIO} / {BTN_PNL}",
        f"{BTN_MARKET_MENU} — {BTN_MARKET} / {BTN_HOT_SECTORS} / {BTN_ULTRA_SHORT}",
        f"{BTN_WHY} — اختار سهم من أزرار أو اكتب WHY ELKA",
        f"{BTN_WARNINGS} — أهم التحذيرات",
        f"{BTN_BACK} — رجوع للقائمة الرئيسية",
        "",
        "☁️ السيرفر: 🔄 يحدّث التقرير من Cloud Run (TradingView). TA-Lib اختياري.",
        "☁️ محفظة السيرفر: لو لسه مش متفعّلة، شغّل bootstrap على Cloud Run.",
    ]
    if is_auto_refresh_enabled():
        lines.append(
            "🔄 التحديث التلقائي: أوقات سوق EGX فقط (مش 24/7) ومش الجمعة/السبت."
        )
    lines.append("البوت ده استرشادي وورقي فقط، مفيش تنفيذ حقيقي.")
    return "\n".join(lines)


def parse_why_command(text: str) -> str | None:
    cleaned = text.strip().upper()
    if not cleaned.startswith("WHY "):
        return None
    symbol = cleaned[4:].strip()
    return symbol or None


def _fundamental_for_symbol(payload: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    for item in payload.get("candidate_fundamentals") or []:
        if isinstance(item, dict) and str(item.get("symbol", "")).upper() == symbol:
            return item
    return None


def _extract_item_score(item: dict[str, Any] | None) -> str | None:
    """Read a scanner score from parsed section items or alternate field names."""
    if item is None:
        return None

    for key in ("score", "scanner_score", "candidate_score"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()

    nested_candidate = item.get("candidate")
    if isinstance(nested_candidate, dict):
        nested_score = _extract_item_score(nested_candidate)
        if nested_score is not None:
            return nested_score

    header = item.get("header")
    if isinstance(header, str):
        match = _SCORE_FROM_HEADER_RE.search(header)
        if match:
            return match.group("score")

    return None


def _resolve_symbol_score(
    *,
    candidate: dict[str, Any] | None,
    strategy: dict[str, Any] | None,
    watch_item: dict[str, Any] | None,
) -> str:
    for item in (candidate, watch_item, strategy):
        score = _extract_item_score(item)
        if score is not None:
            return score
    return "غير متاح"


def format_symbol_why(payload: dict[str, Any] | None, symbol: str) -> str:
    if payload is None:
        return NO_REPORT_MESSAGE

    normalized = symbol.strip().upper()
    if not normalized:
        return WHY_NOT_FOUND_MESSAGE

    strategy = next(
        (
            item
            for item in _strategy_signals(payload)
            if str(item.get("symbol", "")).upper() == normalized
        ),
        None,
    )
    candidate = next(
        (
            item
            for item in _top_candidates(payload)
            if str(item.get("symbol", "")).upper() == normalized
        ),
        None,
    )
    watch_item = next(
        (
            item
            for item in _watch_list(payload)
            if str(item.get("symbol", "")).upper() == normalized
        ),
        None,
    )

    if strategy is None and candidate is None and watch_item is None:
        return WHY_NOT_FOUND_MESSAGE

    lines = [f"🧠 ليه {normalized}؟", ""]
    lines.append(
        f"السكور: {_resolve_symbol_score(candidate=candidate, strategy=strategy, watch_item=watch_item)}"
    )

    if candidate:
        details = candidate.get("details") or []
        reason = _detail_line(details, "Reasons:")
        if reason:
            lines.append(reason.replace("Reasons:", "الأسباب:").strip())
        technical = _detail_line(details, "Technical:")
        if technical:
            lines.append(technical)
        timing = _detail_line(details, "Entry Timing:")
        if timing:
            lines.append(timing)
        talib = _detail_line(details, "TA-Lib:")
        if talib:
            lines.append(talib)
    elif watch_item:
        details = watch_item.get("details") or []
        reason = _detail_line(details, "Reasons:")
        if reason:
            lines.append(reason.replace("Reasons:", "الأسباب:").strip())

    if strategy:
        decision = strategy.get("decision") or strategy.get("strategy_decision")
        if decision:
            lines.append(f"قرار الاستراتيجية: {decision}")
        confirmation = strategy.get("confirmation_text")
        if confirmation:
            lines.append(confirmation)
        entry = strategy.get("entry")
        stop = strategy.get("stop")
        target = strategy.get("target")
        if entry and stop and target:
            lines.append(f"دخول {entry} | وقف {stop} | هدف {target}")
        timing = strategy.get("timing")
        if timing:
            lines.append(f"توقيت الدخول: {timing}")

    fundamentals = _fundamental_for_symbol(payload, normalized)
    if fundamentals:
        status = fundamentals.get("status")
        summary = fundamentals.get("summary")
        if status or summary:
            lines.append(
                f"الأساسيات: {status or 'غير متاح'}"
                + (f" | {summary}" if summary else "")
            )

    decision_summary = payload.get("decision_summary") or {}
    for item in decision_summary.get("signals") or []:
        if str(item.get("symbol", "")).upper() == normalized:
            lines.append(
                f"التصنيف: {item.get('decision', 'غير متاح')} | {item.get('explanation', '')}"
            )
            break

    return "\n".join(line for line in lines if line).rstrip()


def format_symbol_why_response(payload: dict[str, Any] | None, symbol: str) -> str:
    """Return WHY details plus the paper-trading advisory note."""
    body = format_symbol_why(payload, symbol)
    if body in {NO_REPORT_MESSAGE, WHY_NOT_FOUND_MESSAGE}:
        return body
    return f"{body}\n\n{WHY_ADVISORY_NOTE}"


def validate_telegram_bot_startup() -> str | None:
    """Return an startup error message when the bot cannot start."""
    if get_bot_token():
        return None
    return f"{TELEGRAM_BOT_TOKEN_ENV} environment variable is not set."


def run_telegram_bot() -> int:
    """Start the Telegram bot polling loop."""
    import asyncio

    from core.cloud_report_runner import (
        REPORT_ALREADY_RUNNING_MESSAGE,
        REPORT_STARTING_MESSAGE,
        ReportRunResult,
        format_report_run_telegram_message,
        report_run_lock,
        run_report_once,
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)

    startup_error = validate_telegram_bot_startup()
    if startup_error:
        logger.error(startup_error)
        print(f"Error: {startup_error}")
        return 1

    token = get_bot_token()
    if token is None:
        logger.error("%s environment variable is not set.", TELEGRAM_BOT_TOKEN_ENV)
        print(f"Error: {TELEGRAM_BOT_TOKEN_ENV} environment variable is not set.")
        return 1

    from core.health_server import start_health_server

    start_health_server()

    from core.market_hours_auto_refresh import start_market_hours_auto_refresh_worker

    start_market_hours_auto_refresh_worker()

    allowed_chat_id = get_allowed_chat_id()
    logger.info("Starting Telegram bot.")
    if allowed_chat_id:
        logger.info("Telegram bot restricted to allowed chat id.")
    else:
        logger.warning(
            "TELEGRAM_ALLOWED_CHAT_ID is not set; bot accepts messages from all chats."
        )

    from telegram import Update
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    async def _reply(update: Update, text: str, *, reply_markup=None) -> None:
        if update.message is None:
            return
        await update.message.reply_text(
            text,
            reply_markup=reply_markup or build_main_menu(),
        )

    async def _ensure_authorized(update: Update) -> bool:
        chat = update.effective_chat
        if chat is None:
            return False
        if is_chat_authorized(chat.id, allowed_chat_id):
            return True
        if update.message is not None:
            await update.message.reply_text(UNAUTHORIZED_MESSAGE)
        elif update.callback_query is not None and update.callback_query.message is not None:
            await update.callback_query.message.reply_text(UNAUTHORIZED_MESSAGE)
        return False

    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _ensure_authorized(update):
            return
        await _reply(update, WELCOME_MESSAGE)

    async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _ensure_authorized(update):
            return
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        data = query.data or ""
        if not data.startswith(CALLBACK_WHY_PREFIX):
            return
        symbol = data[len(CALLBACK_WHY_PREFIX) :].strip().upper()
        if not symbol or query.message is None:
            return
        payload = load_latest_report_payload()
        await query.message.reply_text(
            format_symbol_why_response(payload, symbol),
            reply_markup=build_main_menu(),
        )

    async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _ensure_authorized(update) or update.message is None:
            return

        text = (update.message.text or "").strip()
        if not text:
            return

        why_symbol = parse_why_command(text)
        if why_symbol is not None:
            payload = load_latest_report_payload()
            await _reply(update, format_symbol_why_response(payload, why_symbol))
            return

        payload = load_latest_report_payload()

        if text == BTN_DAILY:
            await _reply(update, format_daily_overview(payload))
        elif text == BTN_REFRESH_REPORT:
            if not report_run_lock.try_acquire():
                await _reply(update, REPORT_ALREADY_RUNNING_MESSAGE)
                return

            await _reply(update, REPORT_STARTING_MESSAGE)
            chat_id = update.effective_chat.id
            bot = context.bot

            async def _run_cloud_report() -> None:
                try:
                    result = await asyncio.to_thread(run_report_once)
                    latest_payload = load_latest_report_payload()
                    overview = format_daily_overview(latest_payload)
                    closed_digest = (latest_payload or {}).get("report_metadata", {}).get(
                        "closed_market_digest"
                    )
                    message = format_report_run_telegram_message(
                        result,
                        overview_text=overview,
                        closed_market_digest=closed_digest,
                    )
                    await bot.send_message(
                        chat_id,
                        message,
                        reply_markup=build_main_menu(),
                    )
                except Exception:
                    logger.exception("Cloud report background task failed.")
                    await bot.send_message(
                        chat_id,
                        format_report_run_telegram_message(
                            ReportRunResult(
                                success=False,
                                returncode=None,
                                stdout_tail="",
                                stderr_tail="",
                                error="unexpected_error",
                                latest_report_path=None,
                            )
                        ),
                        reply_markup=build_main_menu(),
                    )
                finally:
                    report_run_lock.release()

            asyncio.create_task(_run_cloud_report())
        elif text == BTN_OPPORTUNITIES:
            await update.message.reply_text(
                "🔥 قائمة الفرص:",
                reply_markup=build_opportunities_menu(),
            )
        elif text == BTN_SELL_PORTFOLIO:
            await update.message.reply_text(
                format_sell_portfolio_menu_intro(payload),
                reply_markup=build_sell_portfolio_menu(),
            )
        elif text == BTN_MARKET_MENU:
            await update.message.reply_text(
                "📈 قائمة السوق:",
                reply_markup=build_market_menu(),
            )
        elif text == BTN_BACK:
            await _reply(update, "رجعت للقائمة الرئيسية.")
        elif text == BTN_BEST_THREE:
            await _reply(update, format_best_three(payload))
        elif text == BTN_BEST:
            await _reply(update, format_best_opportunities(payload))
        elif text == BTN_NEXT_SESSION:
            await _reply(update, format_next_session_watch(payload))
        elif text == BTN_SELL:
            await _reply(update, format_sell_review(payload))
        elif text == BTN_SELL_ONLY:
            await _reply(update, format_sell_only(payload))
        elif text == BTN_PORTFOLIO:
            await _reply(update, format_paper_portfolio(payload))
        elif text == BTN_PNL:
            await _reply(update, format_pnl_summary(payload))
        elif text == BTN_MARKET:
            await _reply(update, format_market_status(payload))
        elif text == BTN_HOT_SECTORS:
            await _reply(update, format_hot_sectors(payload))
        elif text == BTN_ULTRA_SHORT:
            await _reply(update, format_ultra_short(payload))
        elif text == BTN_WARNINGS:
            await _reply(update, format_warnings(payload))
        elif text == BTN_HELP:
            await _reply(update, format_help())
        elif text == BTN_WHY:
            if payload is None:
                await _reply(update, NO_REPORT_MESSAGE)
                return
            symbols = collect_why_symbols(payload)
            if not symbols:
                await _reply(update, WHY_NO_SYMBOLS_MESSAGE)
                return
            await update.message.reply_text(
                WHY_SYMBOL_PROMPT_MESSAGE,
                reply_markup=build_why_symbol_keyboard(symbols),
            )
        else:
            await _reply(
                update,
                "اختار زر من القائمة تحت، أو اكتب WHY ELKA لو عايز تفاصيل سهم.",
            )

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Telegram bot polling started.")
    print("Telegram bot started. Press Ctrl+C to stop.")
    application.run_polling(drop_pending_updates=True)
    return 0
