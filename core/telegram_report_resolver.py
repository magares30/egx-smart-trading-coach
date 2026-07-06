"""Resolve Telegram opportunity/watch symbols from structured JSON V2 with legacy fallback."""

from __future__ import annotations

import re
from typing import Any

EMPTY_OPPORTUNITIES_MESSAGE = (
    "مفيش فرص أو أفكار متابعة واضحة في آخر تقرير."
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
        current.setdefault("details", []).append(line)
    if current is not None:
        items.append(current)
    return items


def _normalize_symbol(value: object) -> str:
    return str(value).strip().upper()


def _safe_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def is_market_closed(payload: dict[str, Any] | None) -> bool:
    """Return True when the latest report indicates EGX is closed."""
    if payload is None:
        return False
    session = _safe_dict(payload.get("market_session"))
    metadata = _safe_dict(payload.get("report_metadata"))
    closed_digest = _safe_dict(metadata.get("closed_market_digest"))
    status_values = {
        str(session.get("status", "")).upper(),
        str(metadata.get("market_status", "")).upper(),
    }
    if "CLOSED" in status_values:
        return True
    return bool(closed_digest.get("enabled"))


def _legacy_strategy_signals(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return _parse_grouped_section(_section_lines(payload, "Strategy Signals"))


def _legacy_top_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return _parse_grouped_section(_section_lines(payload, "Top Candidates"))


def _legacy_watch_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return _parse_grouped_section(_section_lines(payload, "Watch List"))


def _legacy_lookup(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for source in (_legacy_strategy_signals, _legacy_top_candidates, _legacy_watch_list):
        for item in source(payload):
            symbol = _normalize_symbol(item.get("symbol"))
            if symbol and symbol not in lookup:
                lookup[symbol] = item
    return lookup


def _confidence_symbols(payload: dict[str, Any]) -> list[str]:
    summary = _safe_dict(payload.get("confidence_v2_summary"))
    if not summary.get("available"):
        return []
    symbols: list[str] = []
    for key in ("strong", "good"):
        for symbol in summary.get(key) or []:
            normalized = _normalize_symbol(symbol)
            if normalized:
                symbols.append(normalized)
    return symbols


def _base_item(
    symbol: str,
    *,
    decision: str | None = None,
    source: str,
    rank: int,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "decision": decision,
        "source": source,
        "rank": rank,
        "confidence_label_v2": None,
        "confidence_score_v2": None,
        "sector_label": None,
        "market_memory_label": None,
        "portfolio_learning_note": None,
        "confirmation": None,
        "entry": None,
        "stop": None,
        "target": None,
        "risk": None,
        "timing": None,
        "strategy_decision": None,
    }


def _enrich_item(payload: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    symbol = _normalize_symbol(item.get("symbol"))
    if not symbol:
        return item

    confidence_context = _safe_dict(
        (_safe_dict(payload.get("confidence_v2_context"))).get(symbol)
    )
    sector_context = _safe_dict(
        (_safe_dict(payload.get("sector_intelligence_context"))).get(symbol)
    )
    memory_context = _safe_dict(
        (_safe_dict(payload.get("market_memory_context"))).get(symbol)
    )
    portfolio_context = _safe_dict(payload.get("portfolio_learning_context"))
    learning_symbols = _safe_dict(portfolio_context.get("symbols"))
    learning_context = _safe_dict(learning_symbols.get(symbol))

    confirmation_lookup = {
        _normalize_symbol(entry.get("symbol")): entry
        for entry in (_safe_dict(payload.get("confirmation_summary")).get("signals") or [])
        if isinstance(entry, dict) and entry.get("symbol")
    }
    decision_lookup = {
        _normalize_symbol(entry.get("symbol")): entry
        for entry in (_safe_dict(payload.get("decision_summary")).get("signals") or [])
        if isinstance(entry, dict) and entry.get("symbol")
    }

    legacy = _legacy_lookup(payload).get(symbol, {})
    confirmation = confirmation_lookup.get(symbol, {})
    decision_row = decision_lookup.get(symbol, {})

    if confidence_context:
        item["confidence_label_v2"] = confidence_context.get("confidence_label_v2")
        item["confidence_score_v2"] = confidence_context.get("confidence_score_v2")
        risks = confidence_context.get("confidence_risks_v2") or []
        if risks and not item.get("risk"):
            item["risk"] = str(risks[0])

    if sector_context:
        item["sector_label"] = sector_context.get("sector_label")

    if memory_context:
        item["market_memory_label"] = memory_context.get("memory_label")

    learning_line = learning_context.get("learning_line")
    if learning_line:
        item["portfolio_learning_note"] = str(learning_line)

    confirmation_text = confirmation.get("confirmation_text") or confirmation.get(
        "confirmation_label"
    )
    if confirmation_text:
        item["confirmation"] = str(confirmation_text)

    if not item.get("decision"):
        item["decision"] = (
            decision_row.get("decision")
            or legacy.get("decision_label")
            or legacy.get("strategy_decision")
        )

    if not item.get("strategy_decision"):
        item["strategy_decision"] = (
            decision_row.get("strategy_decision") or legacy.get("strategy_decision")
        )

    for field in ("entry", "stop", "target", "timing"):
        if item.get(field) is None and legacy.get(field) is not None:
            item[field] = legacy.get(field)

    return item


def _append_symbol(
    items: list[dict[str, Any]],
    seen: set[str],
    symbol_value: object,
    *,
    decision: str | None,
    source: str,
) -> None:
    symbol = _normalize_symbol(symbol_value)
    if not symbol or symbol in seen:
        return
    seen.add(symbol)
    items.append(
        _base_item(
            symbol,
            decision=decision,
            source=source,
            rank=len(items) + 1,
        )
    )


def resolve_executable_opportunity_items(
    payload: dict[str, Any] | None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Single ranked executable opportunity list for Telegram and paper fallback."""
    return _resolve_ranked_opportunity_items(payload, limit=limit, mode="opportunities")


def resolve_opportunity_items(
    payload: dict[str, Any] | None,
    *,
    limit: int | None = None,
    mode: str = "opportunities",
) -> list[dict[str, Any]]:
    """Resolve ranked opportunity items, preferring structured JSON over parsed text."""
    if mode == "opportunities":
        return resolve_executable_opportunity_items(payload, limit=limit)
    return _resolve_ranked_opportunity_items(payload, limit=limit, mode=mode)


def _resolve_ranked_opportunity_items(
    payload: dict[str, Any] | None,
    *,
    limit: int | None = None,
    mode: str = "opportunities",
) -> list[dict[str, Any]]:
    if payload is None:
        return []

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    decision_summary = _safe_dict(payload.get("decision_summary"))
    executive = _safe_dict(payload.get("executive_summary"))

    for signal in decision_summary.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        _append_symbol(
            items,
            seen,
            signal.get("symbol"),
            decision=str(signal.get("decision") or signal.get("strategy_decision") or ""),
            source="decision_summary.signals",
        )

    for symbol in executive.get("best_ideas") or []:
        _append_symbol(
            items,
            seen,
            symbol,
            decision="WATCH_NEXT_SESSION" if is_market_closed(payload) else None,
            source="executive_summary.best_ideas",
        )

    if mode == "opportunities":
        for symbol in decision_summary.get("watch_next_session") or []:
            _append_symbol(
                items,
                seen,
                symbol,
                decision="WATCH_NEXT_SESSION",
                source="decision_summary.watch_next_session",
            )

    for symbol in _confidence_symbols(payload):
        _append_symbol(
            items,
            seen,
            symbol,
            decision="WATCH_NEXT_SESSION" if is_market_closed(payload) else None,
            source="confidence_v2_summary",
        )

    for legacy_item in _legacy_top_candidates(payload):
        _append_symbol(
            items,
            seen,
            legacy_item.get("symbol"),
            decision=None,
            source="parsed.top_candidates",
        )

    for legacy_item in _legacy_strategy_signals(payload):
        _append_symbol(
            items,
            seen,
            legacy_item.get("symbol"),
            decision=legacy_item.get("decision_label") or legacy_item.get("strategy_decision"),
            source="parsed.strategy_signals",
        )

    if mode == "opportunities":
        for legacy_item in _legacy_watch_list(payload):
            _append_symbol(
                items,
                seen,
                legacy_item.get("symbol"),
                decision="WATCH_NEXT_SESSION",
                source="parsed.watch_list",
            )

    enriched = [_enrich_item(payload, dict(item)) for item in items]
    if limit is not None:
        enriched = enriched[:limit]
    return enriched


def resolve_next_session_items(
    payload: dict[str, Any] | None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Resolve next-session watch items with structured JSON first."""
    if payload is None:
        return []

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    decision_summary = _safe_dict(payload.get("decision_summary"))
    executive = _safe_dict(payload.get("executive_summary"))
    closed = is_market_closed(payload)

    for symbol in decision_summary.get("watch_next_session") or []:
        _append_symbol(
            items,
            seen,
            symbol,
            decision="WATCH_NEXT_SESSION",
            source="decision_summary.watch_next_session",
        )

    for signal in decision_summary.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        decision = str(signal.get("decision") or "").upper()
        if decision != "WATCH_NEXT_SESSION":
            continue
        _append_symbol(
            items,
            seen,
            signal.get("symbol"),
            decision="WATCH_NEXT_SESSION",
            source="decision_summary.signals",
        )

    if closed:
        for symbol in executive.get("best_ideas") or []:
            _append_symbol(
                items,
                seen,
                symbol,
                decision="WATCH_NEXT_SESSION",
                source="executive_summary.best_ideas",
            )

    for symbol in _confidence_symbols(payload):
        _append_symbol(
            items,
            seen,
            symbol,
            decision="WATCH_NEXT_SESSION",
            source="confidence_v2_summary",
        )

    for legacy_item in _legacy_watch_list(payload):
        _append_symbol(
            items,
            seen,
            legacy_item.get("symbol"),
            decision="WATCH_NEXT_SESSION",
            source="parsed.watch_list",
        )

    enriched = [_enrich_item(payload, dict(item)) for item in items]
    if limit is not None:
        enriched = enriched[:limit]
    return enriched


def resolve_report_symbols(
    payload: dict[str, Any] | None,
    *,
    include_context: bool = True,
    limit: int | None = None,
) -> list[str]:
    """Collect unique report symbols in stable priority order."""
    if payload is None:
        return []

    ordered: list[str] = []
    seen: set[str] = set()

    def add_symbol(symbol_value: object) -> None:
        if limit is not None and len(ordered) >= limit:
            return
        symbol = _normalize_symbol(symbol_value)
        if not symbol or symbol in seen:
            return
        seen.add(symbol)
        ordered.append(symbol)

    for item in _legacy_strategy_signals(payload):
        add_symbol(item.get("symbol"))
    for item in _legacy_top_candidates(payload):
        add_symbol(item.get("symbol"))
    for item in _legacy_watch_list(payload):
        add_symbol(item.get("symbol"))

    executive = _safe_dict(payload.get("executive_summary"))
    for symbol in executive.get("best_ideas") or []:
        add_symbol(symbol)

    decision_summary = _safe_dict(payload.get("decision_summary"))
    for signal in decision_summary.get("signals") or []:
        if isinstance(signal, dict):
            add_symbol(signal.get("symbol"))
    for symbol in decision_summary.get("watch_next_session") or []:
        add_symbol(symbol)

    if include_context:
        for mapping_key in (
            "confidence_v2_context",
            "sector_intelligence_context",
            "market_memory_context",
        ):
            mapping = _safe_dict(payload.get(mapping_key))
            for symbol in sorted(mapping):
                add_symbol(symbol)

        portfolio_context = _safe_dict(payload.get("portfolio_learning_context"))
        symbols = _safe_dict(portfolio_context.get("symbols"))
        for symbol in sorted(symbols):
            add_symbol(symbol)

        for symbol in _confidence_symbols(payload):
            add_symbol(symbol)

    return ordered


def format_opportunity_item_short(item: dict[str, Any], *, index: int) -> str:
    """Render one compact opportunity line for Telegram."""
    symbol = item.get("symbol", "?")
    decision = item.get("decision") or item.get("strategy_decision") or "غير متاح"
    parts = [f"{index}. {symbol} | {decision}"]

    context_bits: list[str] = []
    if item.get("confidence_label_v2") or item.get("confidence_score_v2") is not None:
        context_bits.append(
            f"ثقة {item.get('confidence_label_v2', '?')} "
            f"{item.get('confidence_score_v2', '?')}"
        )
    if item.get("sector_label"):
        context_bits.append(f"قطاع {item['sector_label']}")
    if item.get("market_memory_label"):
        context_bits.append(f"ذاكرة {item['market_memory_label']}")
    if item.get("confirmation"):
        context_bits.append(str(item["confirmation"]))

    if context_bits:
        parts.append(" | ".join(context_bits))

    entry = item.get("entry")
    stop = item.get("stop")
    target = item.get("target")
    if entry and stop and target:
        parts.append(f"{entry}/{stop}/{target}")

    return " | ".join(parts)


def format_opportunity_item_block(item: dict[str, Any], *, index: int) -> list[str]:
    """Render a slightly richer opportunity block for Telegram."""
    symbol = item.get("symbol", "?")
    decision = item.get("decision") or item.get("strategy_decision") or "غير متاح"
    lines = [f"{index}. {symbol} | {decision}"]

    confirmation = item.get("confirmation")
    if confirmation:
        lines.append(f"   تأكيد: {confirmation}")

    if item.get("confidence_label_v2") or item.get("confidence_score_v2") is not None:
        lines.append(
            "   ثقة ذكية: "
            f"{item.get('confidence_label_v2', 'غير متاح')} "
            f"{item.get('confidence_score_v2', 'غير متاح')}"
        )
    if item.get("sector_label"):
        lines.append(f"   قطاع: {item['sector_label']}")
    if item.get("market_memory_label"):
        lines.append(f"   ذاكرة: {item['market_memory_label']}")
    if item.get("portfolio_learning_note"):
        lines.append(f"   تعلم: {item['portfolio_learning_note']}")

    entry = item.get("entry")
    stop = item.get("stop")
    target = item.get("target")
    if entry and stop and target:
        lines.append(f"   دخول {entry} | وقف {stop} | هدف {target}")
    timing = item.get("timing")
    if timing:
        lines.append(f"   توقيت: {timing}")
    return lines


def format_sector_intelligence_snippet(summary: dict[str, Any] | None) -> list[str]:
    """Compact sector intelligence lines for market views."""
    if not summary or not summary.get("available"):
        return []

    def symbols(key: str) -> str:
        values = summary.get(key) or []
        return ", ".join(str(value) for value in values) if values else "لا يوجد"

    return [
        "🏭 ذكاء القطاعات:",
        f"مدعوم بالقطاع: {symbols('sector_supported')}",
        f"قادة قطاعاتهم: {symbols('sector_leaders')}",
    ]


def format_confidence_v2_compact_line(summary: dict[str, Any] | None) -> str | None:
    if not summary or not summary.get("available"):
        return None
    strong = summary.get("strong") or []
    good = summary.get("good") or []
    strong_text = ", ".join(str(value) for value in strong) if strong else "لا يوجد"
    good_text = ", ".join(str(value) for value in good) if good else "لا يوجد"
    return f"ثقة: قوي {strong_text} | جيد {good_text}"


def format_sector_intelligence_compact_line(summary: dict[str, Any] | None) -> str | None:
    if not summary or not summary.get("available"):
        return None
    supported = summary.get("sector_supported") or []
    leaders = summary.get("sector_leaders") or []
    supported_text = ", ".join(str(value) for value in supported) if supported else "لا يوجد"
    leaders_text = ", ".join(str(value) for value in leaders) if leaders else "لا يوجد"
    return f"قطاعات: مدعوم {supported_text} | قادة {leaders_text}"
