"""Extract portfolio, P&L, and metadata from the latest saved daily report JSON."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from core.market_data_providers import format_data_provider_label
from core.talib_technical import format_talib_runtime_telegram_line

CLOUD_PORTFOLIO_STATE_MESSAGE = (
    "مفيش محفظة ورقية محفوظة على السيرفر لسه. "
    "التقرير شغال، بس جزء الأرباح والمحفظة محتاج Paper Portfolio cloud state."
)

_DATA_PROVIDER_LINE_RE = re.compile(
    r"^Data Provider:\s*(?P<label>.+)$",
    re.IGNORECASE,
)
_PNL_LINE_KEYWORDS = ("P&L", "PnL", "Equity", "Capital", "positions", "portfolio")


def build_report_metadata_payload(
    *,
    data_provider: str | None,
    market_session: dict[str, object],
    paper_portfolio_payload: dict[str, object],
    paper_performance_payload: dict[str, object],
    storage_on_server: bool,
    generated_at: datetime | None = None,
    talib_runtime: dict[str, object] | None = None,
    tradingview_technical_available: bool | None = None,
    closed_market_digest: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build report metadata stored alongside the daily report JSON."""
    timestamp = generated_at or datetime.now().astimezone()
    talib_fields = talib_runtime or {
        "talib_available": False,
        "talib_mode": "fallback",
        "talib_reason": "talib runtime status unavailable",
    }
    return {
        "generated_at": timestamp.isoformat(),
        "data_provider": data_provider or "unknown",
        "data_provider_label": format_data_provider_label(data_provider),
        "market_status": market_session.get("status"),
        "paper_portfolio_present": bool(paper_portfolio_payload.get("available")),
        "paper_performance_present": bool(paper_performance_payload.get("available")),
        "paper_portfolio_storage_on_server": storage_on_server,
        "talib_available": bool(talib_fields.get("talib_available")),
        "talib_mode": str(talib_fields.get("talib_mode") or "fallback"),
        "talib_reason": str(talib_fields.get("talib_reason") or ""),
        "tradingview_technical_available": (
            bool(tradingview_technical_available)
            if tradingview_technical_available is not None
            else False
        ),
        "closed_market_digest": closed_market_digest or {"enabled": False},
    }


def _section_lines(payload: dict[str, Any], title: str) -> list[str]:
    for section in payload.get("sections", []):
        if not isinstance(section, dict):
            continue
        if section.get("title") == title:
            lines = section.get("lines", [])
            return [str(line) for line in lines] if isinstance(lines, list) else []
    return []


def _infer_data_provider_label(payload: dict[str, Any]) -> str | None:
    metadata = payload.get("report_metadata") or {}
    label = metadata.get("data_provider_label")
    if label:
        return str(label)

    provider = metadata.get("data_provider")
    if provider:
        return format_data_provider_label(str(provider))

    for line in _section_lines(payload, "Summary"):
        stripped = line.lstrip("- ").strip()
        match = _DATA_PROVIDER_LINE_RE.match(stripped)
        if match:
            return match.group("label").strip()
    return None


def extract_report_metadata(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Return normalized metadata for the latest report payload."""
    if payload is None:
        return {}

    stored = payload.get("report_metadata") or {}
    market_session = payload.get("market_session") or {}
    portfolio = payload.get("paper_portfolio") or {}
    performance = payload.get("paper_trading_performance") or {}

    generated_at = stored.get("generated_at") or payload.get("created_at")
    data_provider_label = _infer_data_provider_label(payload)
    market_status = stored.get("market_status") or market_session.get("status")

    paper_portfolio_present = stored.get("paper_portfolio_present")
    if paper_portfolio_present is None:
        paper_portfolio_present = bool(portfolio.get("available"))

    paper_performance_present = stored.get("paper_performance_present")
    if paper_performance_present is None:
        paper_performance_present = bool(performance.get("available"))

    storage_on_server = stored.get("paper_portfolio_storage_on_server")
    if storage_on_server is None and not portfolio.get("available"):
        message = str(portfolio.get("message", "")).lower()
        storage_on_server = "no paper portfolio data found" not in message

    return {
        "generated_at": generated_at,
        "data_provider_label": data_provider_label,
        "market_status": market_status,
        "paper_portfolio_present": bool(paper_portfolio_present),
        "paper_performance_present": bool(paper_performance_present),
        "paper_portfolio_storage_on_server": storage_on_server,
        "talib_available": stored.get("talib_available"),
        "talib_mode": stored.get("talib_mode"),
        "talib_reason": stored.get("talib_reason"),
        "tradingview_technical_available": stored.get("tradingview_technical_available"),
        "closed_market_digest": stored.get("closed_market_digest") or {"enabled": False},
    }


def _format_generated_at_display(value: object | None) -> str:
    if value is None:
        return "غير متاح"
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return text


def format_report_metadata_block(payload: dict[str, Any] | None) -> list[str]:
    """Render compact Arabic metadata lines for Telegram portfolio views."""
    metadata = extract_report_metadata(payload)
    lines: list[str] = []

    if metadata.get("generated_at"):
        lines.append(f"🕒 وقت التقرير: {_format_generated_at_display(metadata['generated_at'])}")
    if metadata.get("data_provider_label"):
        lines.append(f"📡 المصدر: {metadata['data_provider_label']}")
    if metadata.get("market_status"):
        lines.append(f"📈 حالة السوق: {metadata['market_status']}")

    storage_on_server = metadata.get("paper_portfolio_storage_on_server")
    if storage_on_server is True:
        lines.append("💾 محفظة السيرفر: محفوظة")
    elif storage_on_server is False:
        lines.append("💾 محفظة السيرفر: غير محفوظة بعد")

    if metadata.get("paper_portfolio_present"):
        lines.append("📊 بيانات المحفظة في التقرير: متوفرة")
    else:
        lines.append("📊 بيانات المحفظة في التقرير: غير متوفرة")

    talib_runtime = payload.get("report_metadata") if payload else None
    lines.append(format_talib_runtime_telegram_line(talib_runtime))

    tv_available = metadata.get("tradingview_technical_available")
    if tv_available is True:
        lines.append("TradingView technical: ACTIVE ✅")
    elif tv_available is False:
        lines.append("TradingView technical: UNAVAILABLE")

    return lines


def parse_pnl_lines_from_sections(payload: dict[str, Any]) -> list[str]:
    """Parse useful P&L lines from report section text when JSON blocks are absent."""
    parsed: list[str] = []
    for title in ("Paper Trading Performance", "Paper Portfolio", "Executive Summary"):
        for raw_line in _section_lines(payload, title):
            stripped = raw_line.lstrip("- ").strip()
            if not stripped:
                continue
            if "No paper portfolio data found" in stripped:
                parsed.append(stripped)
                continue
            if any(keyword.lower() in stripped.lower() for keyword in _PNL_LINE_KEYWORDS):
                parsed.append(stripped)
    return parsed


def resolve_portfolio_data_status(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Summarize whether portfolio/P&L data exists in the latest report."""
    if payload is None:
        return {
            "has_portfolio_json": False,
            "has_performance_json": False,
            "cloud_state_missing": True,
            "executive_pnl": None,
            "section_pnl_lines": [],
        }

    portfolio = payload.get("paper_portfolio") or {}
    performance = payload.get("paper_trading_performance") or {}
    metadata = extract_report_metadata(payload)
    executive = payload.get("executive_summary") or {}
    executive_pnl = executive.get("paper_pnl")
    if executive_pnl in (None, "", "n/a", "N/A"):
        executive_pnl = None

    has_portfolio_json = bool(portfolio.get("available"))
    has_performance_json = bool(performance.get("available"))
    storage_on_server = metadata.get("paper_portfolio_storage_on_server")
    cloud_state_missing = (
        not has_portfolio_json
        and not has_performance_json
        and storage_on_server is False
    )

    return {
        "has_portfolio_json": has_portfolio_json,
        "has_performance_json": has_performance_json,
        "cloud_state_missing": cloud_state_missing,
        "executive_pnl": executive_pnl,
        "section_pnl_lines": parse_pnl_lines_from_sections(payload),
        "portfolio_message": portfolio.get("message"),
    }


def decision_summary_sell_positions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return sell/exit review rows from decision_summary when portfolio JSON is absent."""
    positions = (payload.get("decision_summary") or {}).get("positions") or []
    sell_rows: list[dict[str, Any]] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        decision = str(position.get("decision", ""))
        if decision.startswith("SELL_ALERT") or decision.startswith("EXIT_REVIEW"):
            sell_rows.append(position)
    return sell_rows
