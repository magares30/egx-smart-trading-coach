"""Format paper trade transaction history for Telegram."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from config import settings
from core.models import Trade, TradeStatus
from core.portfolio_report import load_trade_journal_for_report

EMPTY_TRADE_LOG_MESSAGE = (
    "📜 سجل العمليات الورقية:\n"
    "لسه مفيش عمليات ورقية مسجلة."
)
UNREADABLE_TRADE_LOG_MESSAGE = (
    "📜 سجل العمليات الورقية:\n"
    "تعذر قراءة سجل العمليات الورقية حالياً."
)
TRADE_LOG_FOOTER = "عرض آخر {limit} عملية فقط."


def _parse_datetime(value: object | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _format_date(value: object | None) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "n/a"
    return parsed.strftime("%Y-%m-%d")


def _format_price(value: object | None) -> str:
    if value is None or value == "":
        return "n/a"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _format_quantity(value: object | None) -> str:
    if value is None or value == "":
        return "n/a"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _first_value(record: dict[str, Any], *keys: str) -> object | None:
    for key in keys:
        if key not in record:
            continue
        value = record.get(key)
        if value is not None and value != "":
            return value
    return None


def _normalize_trade_record(raw: Trade | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, Trade):
        return {
            "symbol": raw.symbol,
            "status": raw.status.value,
            "side": raw.side.value,
            "quantity": raw.quantity,
            "entry_price": raw.entry_price,
            "exit_price": raw.exit_price,
            "opened_at": raw.opened_at,
            "closed_at": raw.closed_at,
            "pnl": raw.pnl,
            "pnl_percent": raw.pnl_percent,
        }

    record = raw if isinstance(raw, dict) else {}
    status = _first_value(record, "status", "state")
    side = _first_value(record, "side", "action", "type")
    return {
        "symbol": _first_value(record, "symbol"),
        "status": str(status).upper() if status is not None else "",
        "side": str(side).upper() if side is not None else "",
        "quantity": _first_value(record, "quantity", "shares"),
        "entry_price": _first_value(
            record,
            "entry_price",
            "buy_price",
            "price",
        ),
        "exit_price": _first_value(
            record,
            "exit_price",
            "sell_price",
        ),
        "opened_at": _first_value(record, "opened_at", "timestamp", "date"),
        "closed_at": _first_value(record, "closed_at"),
        "pnl": _first_value(record, "pnl", "realized_pnl"),
        "pnl_percent": _first_value(
            record,
            "pnl_percent",
            "return_pct",
            "pnl_pct",
        ),
    }


def _event_datetime(record: dict[str, Any]) -> datetime:
    status = str(record.get("status") or "").upper()
    if status == TradeStatus.CLOSED.value:
        return (
            _parse_datetime(record.get("closed_at"))
            or _parse_datetime(record.get("opened_at"))
            or datetime.min.replace(tzinfo=UTC)
        )
    return _parse_datetime(record.get("opened_at")) or datetime.min.replace(tzinfo=UTC)


def _result_label(pnl: object | None, pnl_percent: object | None) -> str:
    value = pnl
    if value is None and pnl_percent is not None:
        try:
            value = float(pnl_percent)
        except (TypeError, ValueError):
            value = None
    if value is None:
        return "n/a"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if amount > 0:
        return "ربح ✅"
    if amount < 0:
        return "خسارة ❌"
    return "تعادل ⚪"


def _format_pnl_result(record: dict[str, Any]) -> str:
    pnl_percent = record.get("pnl_percent")
    pnl = record.get("pnl")
    percent_text = "n/a"
    if pnl_percent is not None:
        try:
            percent_text = f"{float(pnl_percent):+.2f}%"
        except (TypeError, ValueError):
            percent_text = "n/a"
    elif pnl is not None:
        try:
            percent_text = f"{float(pnl):+,.2f}"
        except (TypeError, ValueError):
            percent_text = "n/a"
    return f"{percent_text} | {_result_label(pnl, pnl_percent)}"


def _format_open_transaction(index: int, record: dict[str, Any]) -> list[str]:
    symbol = str(record.get("symbol") or "?").upper()
    return [
        f"{index}) BUY {symbol}",
        f"📅 {_format_date(record.get('opened_at'))}",
        f"💵 شراء: {_format_price(record.get('entry_price'))}",
        f"📦 الكمية: {_format_quantity(record.get('quantity'))}",
        "الحالة: مفتوحة",
    ]


def _format_closed_transaction(index: int, record: dict[str, Any]) -> list[str]:
    symbol = str(record.get("symbol") or "?").upper()
    return [
        f"{index}) SELL {symbol}",
        f"📅 {_format_date(record.get('closed_at') or record.get('opened_at'))}",
        f"💵 بيع: {_format_price(record.get('exit_price'))}",
        f"📈 النتيجة: {_format_pnl_result(record)}",
    ]


def _format_transaction_block(index: int, record: dict[str, Any]) -> list[str]:
    status = str(record.get("status") or "").upper()
    if status == TradeStatus.CLOSED.value:
        return _format_closed_transaction(index, record)
    return _format_open_transaction(index, record)


def load_trade_records_from_state() -> tuple[list[dict[str, Any]], str | None]:
    """Load normalized trade records from existing paper-trading state."""
    journal = load_trade_journal_for_report()
    if journal is not None:
        return [_normalize_trade_record(trade) for trade in journal.trades], None

    path = settings.TRADES_PATH
    if not path.is_file():
        return [], None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], "unreadable"

    if not isinstance(raw, list):
        return [], "unreadable"

    records: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            records.append(_normalize_trade_record(Trade.model_validate(item)))
        except Exception:
            records.append(_normalize_trade_record(item))
    return records, None


def format_trade_transactions(
    payload: dict[str, Any] | None = None,
    *,
    limit: int = 15,
) -> str:
    """Render latest paper trade transactions for Telegram."""
    _ = payload
    records, error = load_trade_records_from_state()
    if error:
        return UNREADABLE_TRADE_LOG_MESSAGE
    if not records:
        return EMPTY_TRADE_LOG_MESSAGE

    sorted_records = sorted(records, key=_event_datetime, reverse=True)
    selected = sorted_records[:limit]

    lines = ["📜 سجل العمليات الورقية:", ""]
    for index, record in enumerate(selected, start=1):
        lines.extend(_format_transaction_block(index, record))
        lines.append("")

    if len(sorted_records) > limit:
        lines.append(TRADE_LOG_FOOTER.format(limit=limit))

    return "\n".join(lines).rstrip()
