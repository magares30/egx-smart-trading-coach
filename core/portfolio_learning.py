"""Lightweight learning insights from existing paper-trading outcomes."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from core.models import Trade, TradeStatus
from core.portfolio import VirtualPortfolio
from core.trade_journal import TradeJournal

logger = logging.getLogger(__name__)

UNKNOWN_BUCKET = "UNKNOWN"
MIN_TRADES_FOR_PATTERN = 5

CONFIDENCE_LABELS = ("STRONG", "GOOD", "MIXED", "WEAK", "WAIT", UNKNOWN_BUCKET)
MEMORY_LABELS = (
    "NEW",
    "IMPROVING",
    "PERSISTENT",
    "FADING",
    "WEAKENING",
    "RETURNING",
    UNKNOWN_BUCKET,
)
SECTOR_LABELS = (
    "SUPPORTED_BY_SECTOR",
    "LEADER_IN_HOT_SECTOR",
    "STOCK_OUTPERFORMING_SECTOR",
    "STRONG_STOCK_WEAK_SECTOR",
    "WEAK_IN_HOT_SECTOR",
    "SECTOR_DRAG",
    "NEUTRAL_SECTOR_CONTEXT",
    "UNKNOWN_SECTOR",
    UNKNOWN_BUCKET,
)

_KEY_VALUE_RE = re.compile(r"(?P<key>[a-zA-Z0-9_]+)\s*[:=]\s*(?P<value>[A-Z0-9_]+)")


def empty_portfolio_learning_summary(
    *,
    warning: str | None = None,
) -> dict[str, Any]:
    warnings = [warning] if warning else []
    return {
        "available": False,
        "open_positions_count": 0,
        "closed_trades_count": 0,
        "win_rate_pct": None,
        "average_win_pct": None,
        "average_loss_pct": None,
        "expectancy_pct": None,
        "best_trade": None,
        "worst_trade": None,
        "confidence_buckets": {},
        "sector_buckets": {},
        "memory_buckets": {},
        "best_pattern": None,
        "weak_pattern": None,
        "learning_notes": [],
        "learning_warnings": warnings,
    }


def _safe_float(value: object | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _safe_datetime(value: object | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _closed_trades(
    portfolio: VirtualPortfolio | None,
    journal: TradeJournal | None,
) -> list[Trade]:
    if journal is not None:
        journal_closed = [
            trade
            for trade in journal.trades
            if trade.status == TradeStatus.CLOSED and trade.pnl is not None
        ]
        if journal_closed:
            return journal_closed
    if portfolio is None:
        return []
    return [
        trade
        for trade in portfolio.trades.values()
        if trade.status == TradeStatus.CLOSED and trade.pnl is not None
    ]


def _metadata_from_trade(trade: Trade) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for source in (trade.notes, trade.reason):
        if not source:
            continue
        text = str(source).strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    if value is not None:
                        metadata[str(key)] = str(value).upper()
        for match in _KEY_VALUE_RE.finditer(text):
            metadata[match.group("key")] = match.group("value").upper()
    return metadata


def _bucket_value(
    metadata: dict[str, str],
    keys: tuple[str, ...],
    allowed: tuple[str, ...],
) -> str:
    for key in keys:
        value = metadata.get(key)
        if value in allowed:
            return value
    return UNKNOWN_BUCKET


def _trade_metadata_labels(trade: Trade) -> tuple[str, str, str]:
    metadata = _metadata_from_trade(trade)
    confidence = _bucket_value(
        metadata,
        ("confidence_label_v2", "confidence_label", "confidence"),
        CONFIDENCE_LABELS,
    )
    sector = _bucket_value(
        metadata,
        ("sector_label", "sector_intelligence_label", "sector"),
        SECTOR_LABELS,
    )
    memory = _bucket_value(
        metadata,
        ("memory_label", "market_memory_label", "memory"),
        MEMORY_LABELS,
    )
    return confidence, sector, memory


def _bucket_template(labels: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    return {
        label: {
            "trades_count": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "average_pnl_pct": None,
            "total_pnl_pct": 0.0,
        }
        for label in labels
    }


def _add_trade_to_bucket(
    bucket: dict[str, dict[str, Any]],
    label: str,
    trade: Trade,
) -> None:
    target = bucket.setdefault(
        label or UNKNOWN_BUCKET,
        _bucket_template((UNKNOWN_BUCKET,))[UNKNOWN_BUCKET],
    )
    pnl_pct = _safe_float(trade.pnl_percent) or 0.0
    target["trades_count"] += 1
    target["wins"] += 1 if (trade.pnl or 0.0) > 0 else 0
    target["losses"] += 1 if (trade.pnl or 0.0) < 0 else 0
    target["total_pnl_pct"] = float(target.get("total_pnl_pct") or 0.0) + pnl_pct
    count = int(target["trades_count"])
    target["average_pnl_pct"] = target["total_pnl_pct"] / count if count else None
    target["win_rate_pct"] = (int(target["wins"]) / count) * 100 if count else None


def _trade_summary(trade: Trade | None) -> dict[str, Any] | None:
    if trade is None:
        return None
    return {
        "symbol": trade.symbol,
        "pnl": trade.pnl,
        "pnl_percent": trade.pnl_percent,
        "quantity": trade.quantity,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
        "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
    }


def _best_bucket_pattern(
    buckets_by_name: dict[str, dict[str, dict[str, Any]]],
    *,
    reverse: bool,
) -> str | None:
    candidates: list[tuple[float, str]] = []
    for family, buckets in buckets_by_name.items():
        for label, stats in buckets.items():
            count = int(stats.get("trades_count") or 0)
            avg = stats.get("average_pnl_pct")
            if count < 2 or avg is None or label == UNKNOWN_BUCKET:
                continue
            candidates.append((float(avg), f"{family} {label} avg {float(avg):+.2f}%"))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=reverse)
    return candidates[0][1]


def build_portfolio_learning(
    *,
    portfolio: VirtualPortfolio | None,
    journal: TradeJournal | None,
    paper_portfolio_payload: dict[str, Any] | None = None,
    latest_prices: dict[str, float] | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Build Portfolio Learning summary/context from existing paper-trading state."""
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    payload = paper_portfolio_payload or {}
    open_payload_positions = [
        item for item in payload.get("positions", []) if isinstance(item, dict)
    ]
    closed = _closed_trades(portfolio, journal)

    open_positions = _open_position_context(
        portfolio=portfolio,
        open_payload_positions=open_payload_positions,
        latest_prices=latest_prices,
        now=reference,
    )
    summary = empty_portfolio_learning_summary()
    summary["available"] = bool(open_positions or closed or payload.get("available"))
    summary["open_positions_count"] = len(open_positions)
    summary["closed_trades_count"] = len(closed)

    confidence_buckets = _bucket_template(CONFIDENCE_LABELS)
    sector_buckets = _bucket_template(SECTOR_LABELS)
    memory_buckets = _bucket_template(MEMORY_LABELS)
    metadata_missing = False

    if closed:
        wins = [trade for trade in closed if (trade.pnl or 0.0) > 0]
        losses = [trade for trade in closed if (trade.pnl or 0.0) < 0]
        win_pcts = [_safe_float(trade.pnl_percent) for trade in wins]
        loss_pcts = [_safe_float(trade.pnl_percent) for trade in losses]
        win_pcts = [value for value in win_pcts if value is not None]
        loss_pcts = [value for value in loss_pcts if value is not None]

        summary["win_rate_pct"] = (len(wins) / len(closed)) * 100
        summary["average_win_pct"] = (
            sum(win_pcts) / len(win_pcts) if win_pcts else None
        )
        summary["average_loss_pct"] = (
            sum(loss_pcts) / len(loss_pcts) if loss_pcts else None
        )
        summary["expectancy_pct"] = _expectancy_pct(
            win_rate_pct=summary["win_rate_pct"],
            average_win_pct=summary["average_win_pct"],
            average_loss_pct=summary["average_loss_pct"],
            closed_trades_count=len(closed),
        )
        summary["best_trade"] = _trade_summary(
            max(closed, key=lambda trade: trade.pnl or 0.0)
        )
        summary["worst_trade"] = _trade_summary(
            min(closed, key=lambda trade: trade.pnl or 0.0)
        )

        for trade in closed:
            confidence, sector, memory = _trade_metadata_labels(trade)
            metadata_missing = metadata_missing or (
                confidence == UNKNOWN_BUCKET
                and sector == UNKNOWN_BUCKET
                and memory == UNKNOWN_BUCKET
            )
            _add_trade_to_bucket(confidence_buckets, confidence, trade)
            _add_trade_to_bucket(sector_buckets, sector, trade)
            _add_trade_to_bucket(memory_buckets, memory, trade)

    summary["confidence_buckets"] = confidence_buckets if closed else {}
    summary["sector_buckets"] = sector_buckets if closed else {}
    summary["memory_buckets"] = memory_buckets if closed else {}
    summary["best_pattern"] = _best_bucket_pattern(
        {
            "confidence": confidence_buckets,
            "sector": sector_buckets,
            "memory": memory_buckets,
        },
        reverse=True,
    )
    summary["weak_pattern"] = _best_bucket_pattern(
        {
            "confidence": confidence_buckets,
            "sector": sector_buckets,
            "memory": memory_buckets,
        },
        reverse=False,
    )

    if summary["available"] and len(closed) < MIN_TRADES_FOR_PATTERN:
        summary["learning_notes"].append(
            "Portfolio Learning needs more closed paper trades."
        )
    if metadata_missing:
        summary["learning_warnings"].append(
            "Entry metadata unavailable for older paper trades; learning will improve over future trades."
        )

    context = {
        "available": summary["available"],
        "open_positions": open_positions,
        "symbols": {
            position["symbol"]: {
                "symbol": position["symbol"],
                "learning_line": "النمط ده لسه محتاج بيانات",
                "holding_days": position.get("holding_days"),
                "unrealized_pnl_pct": position.get("unrealized_pnl_pct"),
            }
            for position in open_positions
            if position.get("symbol")
        },
    }
    return summary, context, bool(summary["available"])


def _open_position_context(
    *,
    portfolio: VirtualPortfolio | None,
    open_payload_positions: list[dict[str, Any]],
    latest_prices: dict[str, float] | None,
    now: datetime,
) -> list[dict[str, Any]]:
    portfolio_positions = portfolio.positions if portfolio is not None else {}
    payload_by_symbol = {
        str(position.get("symbol", "")).upper(): position
        for position in open_payload_positions
        if position.get("symbol")
    }
    symbols = sorted(set(portfolio_positions) | set(payload_by_symbol))
    positions: list[dict[str, Any]] = []
    for symbol in symbols:
        payload = payload_by_symbol.get(symbol, {})
        model_position = portfolio_positions.get(symbol)
        entry_price = (
            model_position.avg_entry_price
            if model_position is not None
            else _safe_float(payload.get("avg_entry_price"))
        )
        current_price = _safe_float(payload.get("current_price"))
        if current_price is None and latest_prices:
            current_price = latest_prices.get(symbol)
        opened_at = (
            model_position.opened_at
            if model_position is not None
            else payload.get("opened_at")
        )
        opened_dt = _safe_datetime(opened_at)
        positions.append(
            {
                "symbol": symbol,
                "entry_price": entry_price,
                "current_price": current_price,
                "unrealized_pnl": payload.get("unrealized_pnl"),
                "unrealized_pnl_pct": payload.get("unrealized_pnl_pct"),
                "holding_days": (
                    (now - opened_dt).days if opened_dt is not None else None
                ),
                "confidence_label_at_entry": payload.get(
                    "confidence_label_at_entry",
                    UNKNOWN_BUCKET,
                ),
                "sector_label_at_entry": payload.get(
                    "sector_label_at_entry",
                    UNKNOWN_BUCKET,
                ),
                "memory_label_at_entry": payload.get(
                    "memory_label_at_entry",
                    UNKNOWN_BUCKET,
                ),
            }
        )
    return positions


def _expectancy_pct(
    *,
    win_rate_pct: float | None,
    average_win_pct: float | None,
    average_loss_pct: float | None,
    closed_trades_count: int,
) -> float | None:
    if (
        closed_trades_count < MIN_TRADES_FOR_PATTERN
        or win_rate_pct is None
        or average_win_pct is None
        or average_loss_pct is None
    ):
        return None
    win_rate = win_rate_pct / 100
    loss_rate = 1 - win_rate
    return (win_rate * average_win_pct) + (loss_rate * average_loss_pct)


def format_portfolio_learning_report_lines(summary: dict[str, Any]) -> list[str]:
    if (
        not summary.get("available")
        or int(summary.get("closed_trades_count") or 0) == 0
    ):
        return [
            "- Portfolio Learning: waiting for more paper trade history.",
            f"- Open positions: {int(summary.get('open_positions_count') or 0)}",
            f"- Closed trades: {int(summary.get('closed_trades_count') or 0)}",
        ]

    win_rate = summary.get("win_rate_pct")
    return [
        f"- Open positions: {int(summary.get('open_positions_count') or 0)}",
        f"- Closed trades: {int(summary.get('closed_trades_count') or 0)}",
        (
            f"- Win rate: {float(win_rate):.1f}%"
            if win_rate is not None
            else "- Win rate: n/a"
        ),
        f"- Best pattern: {summary.get('best_pattern') or 'n/a'}",
        f"- Weak pattern: {summary.get('weak_pattern') or 'n/a'}",
        f"- Notes: {_notes_text(summary)}",
    ]


def _notes_text(summary: dict[str, Any]) -> str:
    notes = list(summary.get("learning_notes") or [])
    warnings = list(summary.get("learning_warnings") or [])
    values = [str(value) for value in notes + warnings if str(value).strip()]
    return " | ".join(values[:2]) if values else "n/a"


def format_portfolio_learning_arabic_block(
    summary: dict[str, Any] | None,
) -> list[str]:
    if not summary or not summary.get("available"):
        return []
    win_rate = summary.get("win_rate_pct")
    win_rate_text = f"{float(win_rate):.1f}%" if win_rate is not None else "n/a"
    note = (
        "محتاج تاريخ أكتر"
        if int(summary.get("closed_trades_count") or 0) < MIN_TRADES_FOR_PATTERN
        else "متاح"
    )
    return [
        "📚 تعلم المحفظة:",
        f"صفقات مغلقة: {int(summary.get('closed_trades_count') or 0)}",
        f"نسبة نجاح: {win_rate_text}",
        f"أفضل نمط: {summary.get('best_pattern') or 'n/a'}",
        f"أضعف نمط: {summary.get('weak_pattern') or 'n/a'}",
        f"ملاحظة: {note}",
        "",
    ]


def format_symbol_portfolio_learning_arabic_line(
    context: dict[str, Any] | None,
    *,
    confidence_label: str | None = None,
) -> str | None:
    if not context:
        return None
    symbol_line = context.get("learning_line")
    if symbol_line:
        return f"تعلم المحفظة: {symbol_line}"
    if confidence_label:
        return f"تعلم المحفظة: {confidence_label} setups need more paper-trade data"
    return "تعلم المحفظة: النمط ده لسه محتاج بيانات"
