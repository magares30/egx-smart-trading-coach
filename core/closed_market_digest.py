"""Closed-market daily digest metadata and formatting for honest off-hours reports."""

from __future__ import annotations

from datetime import date, datetime

from config import settings
from core.market_data_providers import format_data_provider_label
from core.market_hours import (
    EgxMarketSession,
    EgxSessionStatus,
    is_egx_holiday,
)

DIGEST_TYPE = "closed_market_daily_digest"
STALENESS_NOTE = (
    "Prices are based on the latest available market snapshot; EGX is closed today."
)
PRICE_DATA_SOURCE_LABEL = "TradingView Screener"
CLOSED_BUY_PLAN_TEXT = "No new paper entries while EGX is closed."
CLOSED_MAIN_RISK_TEXT = (
    "EGX closed; prices may be stale until the next open session; "
    "paper entries disabled"
)

_REASON_LABELS = {
    "weekend": "weekend",
    "holiday": "holiday",
    "closed": "market closed before/after session",
    "after_hours": "after trading hours",
    "unknown": "market closed",
}


def closed_market_digest_enabled(session: EgxMarketSession) -> bool:
    """Return True when the closed-market digest layer should be active."""
    if not session.guard_enabled:
        return False
    if not session.is_trading_day:
        return True
    return session.session_status == EgxSessionStatus.CLOSED


def resolve_closed_market_reason(
    session: EgxMarketSession,
    *,
    as_of_date: date,
    holidays: frozenset[date] | None = None,
) -> str:
    """Map the current session to a stable closed-market reason code."""
    holiday_set = holidays if holidays is not None else settings.EGX_TRADING_HOLIDAYS
    if is_egx_holiday(as_of_date, holiday_set):
        return "holiday"
    if not session.is_trading_day:
        if as_of_date.weekday() in (4, 5):
            return "weekend"
        return "weekend"
    if session.is_after_close:
        return "after_hours"
    if session.session_status == EgxSessionStatus.CLOSED:
        return "closed"
    return "unknown"


def build_closed_market_digest(
    *,
    session: EgxMarketSession,
    price_data_date: date,
    data_provider: str | None = None,
    as_of_date: date | None = None,
    holidays: frozenset[date] | None = None,
) -> dict[str, object]:
    """Build closed-market digest metadata for report JSON."""
    enabled = closed_market_digest_enabled(session)
    if not enabled:
        return {"enabled": False}

    reference_date = as_of_date or price_data_date
    reason = resolve_closed_market_reason(
        session,
        as_of_date=reference_date,
        holidays=holidays,
    )
    provider_label = format_data_provider_label(data_provider)
    if provider_label == "unknown":
        provider_label = PRICE_DATA_SOURCE_LABEL

    return {
        "enabled": True,
        "reason": reason,
        "price_data_date": price_data_date.isoformat(),
        "price_data_source": provider_label,
        "is_price_data_stale": True,
        "staleness_note": STALENESS_NOTE,
        "paper_entries_allowed": bool(session.paper_entries_enabled),
        "digest_type": DIGEST_TYPE,
    }


def format_closed_market_digest_report_lines(digest: dict[str, object]) -> list[str]:
    """Render compact Closed Market Digest lines for daily report text."""
    if not digest.get("enabled"):
        return []

    reason = str(digest.get("reason") or "unknown")
    reason_text = _REASON_LABELS.get(reason, reason)
    price_date = digest.get("price_data_date", "unknown")
    source = digest.get("price_data_source", PRICE_DATA_SOURCE_LABEL)

    return [
        f"- EGX is closed today: {reason_text}",
        f"- Latest price data: {price_date} from {source}",
        "- Prices may be stale until the next open session",
        "- Paper entries are disabled",
        "- Portfolio and sell alerts are for next trading session review only",
    ]


def format_closed_market_digest_arabic_block(
    digest: dict[str, object] | None,
) -> list[str]:
    """Render the Arabic closed-market honesty block for Telegram."""
    if not digest or not digest.get("enabled"):
        return []

    price_date = digest.get("price_data_date", "غير متاح")
    source = digest.get("price_data_source", "TradingView")
    if "TradingView" in str(source):
        source_short = "TradingView"
    else:
        source_short = str(source)

    return [
        "🔒 السوق مقفول النهارده",
        f"📅 آخر بيانات أسعار: {price_date}",
        f"📌 المصدر: {source_short}",
        "⚠️ الأسعار ممكن تكون قديمة لحد أول جلسة تداول",
        "🚫 مفيش دخول ورقي جديد",
        "👀 المراجعة للجلسة الجاية فقط",
        "",
    ]


def format_closed_market_reason_arabic(digest: dict[str, object] | None) -> str | None:
    """Short Arabic label for the closed-market reason."""
    if not digest or not digest.get("enabled"):
        return None

    reason = str(digest.get("reason") or "unknown")
    mapping = {
        "weekend": "إجازة نهاية الأسبوع",
        "holiday": "إجازة رسمية",
        "closed": "السوق مقفول",
        "after_hours": "بعد إغلاق الجلسة",
        "unknown": "السوق مقفول",
    }
    return mapping.get(reason, mapping["unknown"])
