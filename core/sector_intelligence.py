"""Per-symbol sector relationship context from existing Sector Momentum."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from core.sector_momentum import UNKNOWN_SECTOR, SectorMomentumResult

logger = logging.getLogger(__name__)

LABEL_LEADER_IN_HOT_SECTOR = "LEADER_IN_HOT_SECTOR"
LABEL_SUPPORTED_BY_SECTOR = "SUPPORTED_BY_SECTOR"
LABEL_STOCK_OUTPERFORMING_SECTOR = "STOCK_OUTPERFORMING_SECTOR"
LABEL_WEAK_IN_HOT_SECTOR = "WEAK_IN_HOT_SECTOR"
LABEL_STRONG_STOCK_WEAK_SECTOR = "STRONG_STOCK_WEAK_SECTOR"
LABEL_SECTOR_DRAG = "SECTOR_DRAG"
LABEL_NEUTRAL_SECTOR_CONTEXT = "NEUTRAL_SECTOR_CONTEXT"
LABEL_UNKNOWN_SECTOR = "UNKNOWN_SECTOR"

POSITIVE_SECTOR_LABELS = {
    LABEL_LEADER_IN_HOT_SECTOR,
    LABEL_SUPPORTED_BY_SECTOR,
    LABEL_STOCK_OUTPERFORMING_SECTOR,
    LABEL_STRONG_STOCK_WEAK_SECTOR,
}
RISKY_SECTOR_LABELS = {
    LABEL_WEAK_IN_HOT_SECTOR,
    LABEL_SECTOR_DRAG,
}

OUTPERFORMANCE_THRESHOLD = 1.0
WEAK_SYMBOL_CHANGE_THRESHOLD = -0.1
STRONG_SYMBOL_CHANGE_THRESHOLD = 1.0
STRONG_SYMBOL_SCORE_THRESHOLD = 75

_HEADER_SYMBOL_RE = re.compile(r"^(\d+\.\s+)([A-Z0-9]+)(\s+\|.*)$")


@dataclass(frozen=True)
class SectorIntelligenceInput:
    symbol: str
    score: int | None = None
    change_pct: float | None = None


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


def _safe_int(value: object | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalize_sector(value: object | None) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return LABEL_UNKNOWN_SECTOR
    sector = str(value).strip()
    if not sector or sector == UNKNOWN_SECTOR:
        return LABEL_UNKNOWN_SECTOR
    return sector


def _row_for_symbol(snapshot_df: pd.DataFrame | None, symbol: str) -> dict[str, object]:
    if snapshot_df is None or snapshot_df.empty or "symbol" not in snapshot_df.columns:
        return {"symbol": symbol}
    matches = snapshot_df.loc[snapshot_df["symbol"].astype(str) == symbol]
    if matches.empty:
        return {"symbol": symbol}
    return matches.iloc[0].to_dict()


def _sector_lookup(
    sector_momentum: SectorMomentumResult | None,
) -> dict[str, dict[str, object]]:
    if sector_momentum is None:
        return {}
    return {row.sector: row.to_dict() for row in sector_momentum.sectors}


def _is_sector_leader(symbol: str, sector_row: Mapping[str, object]) -> bool:
    top_symbols = sector_row.get("top_symbols") or []
    if not top_symbols:
        return False
    first = top_symbols[0]
    return isinstance(first, dict) and str(first.get("symbol", "")).upper() == symbol


def _classify_sector_label(
    *,
    symbol: str,
    symbol_score: int | None,
    symbol_change_pct: float | None,
    sector_status: str | None,
    sector_row: Mapping[str, object],
    relative_to_sector_pct: float | None,
) -> tuple[str, list[str], list[str]]:
    reasons: list[str] = []
    risks: list[str] = []
    status = str(sector_status or "").upper()
    is_hot = status == "HOT"
    is_weak = status == "WEAK"
    score_is_strong = (
        symbol_score is not None and symbol_score >= STRONG_SYMBOL_SCORE_THRESHOLD
    )
    change_is_strong = (
        symbol_change_pct is not None
        and symbol_change_pct >= STRONG_SYMBOL_CHANGE_THRESHOLD
    )
    change_is_weak = (
        symbol_change_pct is not None
        and symbol_change_pct <= WEAK_SYMBOL_CHANGE_THRESHOLD
    )
    outperforming = (
        relative_to_sector_pct is not None
        and relative_to_sector_pct >= OUTPERFORMANCE_THRESHOLD
    )

    if is_hot and _is_sector_leader(symbol, sector_row):
        reasons.append("Symbol is one of the strongest names in a hot sector")
        return LABEL_LEADER_IN_HOT_SECTOR, reasons, risks
    if is_hot and change_is_weak:
        risks.append("Symbol is weak while its sector is hot")
        return LABEL_WEAK_IN_HOT_SECTOR, reasons, risks
    if is_hot and (score_is_strong or change_is_strong):
        reasons.append("Hot sector supports the symbol setup")
        return LABEL_SUPPORTED_BY_SECTOR, reasons, risks
    if is_weak and (score_is_strong or change_is_strong):
        reasons.append("Symbol is showing isolated strength in a weak sector")
        risks.append("Weak sector may limit follow-through")
        return LABEL_STRONG_STOCK_WEAK_SECTOR, reasons, risks
    if outperforming:
        reasons.append("Symbol is outperforming its sector average")
        return LABEL_STOCK_OUTPERFORMING_SECTOR, reasons, risks
    if is_weak:
        risks.append("Weak sector context is a drag")
        return LABEL_SECTOR_DRAG, reasons, risks

    return LABEL_NEUTRAL_SECTOR_CONTEXT, reasons, risks


def build_sector_intelligence_context(
    inputs: list[SectorIntelligenceInput],
    *,
    snapshot_df: pd.DataFrame | None,
    sector_momentum: SectorMomentumResult | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], bool]:
    """Build per-symbol sector relationship context from sector momentum output."""
    sector_rows = _sector_lookup(sector_momentum)
    context: dict[str, dict[str, Any]] = {}

    for item in inputs:
        symbol = str(item.symbol or "").strip().upper()
        if not symbol:
            continue
        try:
            row = _row_for_symbol(snapshot_df, symbol)
            sector = _normalize_sector(row.get("sector"))
            symbol_change = item.change_pct
            if symbol_change is None:
                symbol_change = _safe_float(row.get("change_percent"))
            symbol_score = item.score
            if symbol_score is None:
                symbol_score = _safe_int(row.get("score"))

            sector_row = sector_rows.get(sector)
            if sector == LABEL_UNKNOWN_SECTOR or sector_row is None:
                context[symbol] = _unknown_context(symbol, symbol_change)
                continue

            sector_score = _safe_int(sector_row.get("sector_score"))
            sector_avg_change = _safe_float(sector_row.get("avg_change_percent"))
            relative = (
                symbol_change - sector_avg_change
                if symbol_change is not None and sector_avg_change is not None
                else None
            )
            sector_status = str(sector_row.get("status") or "")
            label, reasons, risks = _classify_sector_label(
                symbol=symbol,
                symbol_score=symbol_score,
                symbol_change_pct=symbol_change,
                sector_status=sector_status,
                sector_row=sector_row,
                relative_to_sector_pct=relative,
            )

            context[symbol] = {
                "symbol": symbol,
                "sector": sector,
                "sector_label": label,
                "sector_score": sector_score,
                "sector_avg_change_pct": sector_avg_change,
                "symbol_change_pct": symbol_change,
                "relative_to_sector_pct": relative,
                "sector_is_hot": sector_status.upper() == "HOT",
                "sector_is_weak": sector_status.upper() == "WEAK",
                "sector_reasons": reasons,
                "sector_risks": risks,
            }
        except Exception as exc:  # pragma: no cover - defensive safety guard
            logger.warning("Sector Intelligence failed for %s; skipping: %s", symbol, exc)

    return context, summarize_sector_intelligence(context), bool(context)


def _unknown_context(
    symbol: str,
    symbol_change_pct: float | None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "sector": UNKNOWN_SECTOR,
        "sector_label": LABEL_UNKNOWN_SECTOR,
        "sector_score": None,
        "sector_avg_change_pct": None,
        "symbol_change_pct": symbol_change_pct,
        "relative_to_sector_pct": None,
        "sector_is_hot": False,
        "sector_is_weak": False,
        "sector_reasons": [],
        "sector_risks": ["Sector data unavailable"],
    }


def summarize_sector_intelligence(
    context_by_symbol: dict[str, dict[str, Any]],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    buckets = {
        "sector_supported": [],
        "sector_leaders": [],
        "isolated_strength": [],
        "weak_in_hot_sector": [],
        "sector_drag": [],
        "unknown": [],
    }
    label_to_key = {
        LABEL_SUPPORTED_BY_SECTOR: "sector_supported",
        LABEL_STOCK_OUTPERFORMING_SECTOR: "sector_supported",
        LABEL_LEADER_IN_HOT_SECTOR: "sector_leaders",
        LABEL_STRONG_STOCK_WEAK_SECTOR: "isolated_strength",
        LABEL_WEAK_IN_HOT_SECTOR: "weak_in_hot_sector",
        LABEL_SECTOR_DRAG: "sector_drag",
        LABEL_UNKNOWN_SECTOR: "unknown",
    }
    for symbol in sorted(context_by_symbol):
        context = context_by_symbol[symbol]
        label = str(context.get("sector_label") or LABEL_UNKNOWN_SECTOR)
        key = label_to_key.get(label)
        if key and len(buckets[key]) < limit:
            buckets[key].append(symbol)

    return {
        "available": bool(context_by_symbol),
        **buckets,
    }


def format_sector_intelligence_report_lines(summary: dict[str, Any]) -> list[str]:
    if not summary.get("available"):
        return ["- Sector Intelligence unavailable."]

    def symbols(key: str) -> str:
        values = summary.get(key) or []
        return ", ".join(str(value) for value in values) if values else "(none)"

    return [
        f"- Sector-supported names: {symbols('sector_supported')}",
        f"- Sector leaders: {symbols('sector_leaders')}",
        f"- Isolated strength: {symbols('isolated_strength')}",
        f"- Weak names inside hot sectors: {symbols('weak_in_hot_sector')}",
    ]


def format_sector_intelligence_suffix(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    label = context.get("sector_label") or LABEL_UNKNOWN_SECTOR
    relative = context.get("relative_to_sector_pct")
    if isinstance(relative, (int, float)):
        return f" | Sector: {label} | {relative:+.1f}% vs sector"
    return f" | Sector: {label}"


def enrich_section_lines_with_sector_intelligence(
    lines: list[str],
    context_by_symbol: dict[str, dict[str, Any]],
) -> list[str]:
    enriched: list[str] = []
    for line in lines:
        match = _HEADER_SYMBOL_RE.match(line)
        if not match:
            enriched.append(line)
            continue
        symbol = match.group(2).upper()
        suffix = format_sector_intelligence_suffix(context_by_symbol.get(symbol))
        if not suffix or " | Sector:" in line:
            enriched.append(line)
            continue
        insert_markers = (" | Confidence V2:", " | Memory:")
        for marker in insert_markers:
            if marker in line:
                before, after = line.split(marker, 1)
                enriched.append(f"{before}{suffix}{marker}{after}")
                break
        else:
            enriched.append(f"{line}{suffix}")
    return enriched


def format_sector_intelligence_arabic_block(
    summary: dict[str, Any] | None,
) -> list[str]:
    if not summary or not summary.get("available"):
        return []

    def symbols(key: str) -> str:
        values = summary.get(key) or []
        return ", ".join(str(value) for value in values) if values else "لا يوجد"

    return [
        "🏭 ذكاء القطاعات:",
        f"مدعوم بالقطاع: {symbols('sector_supported')}",
        f"قادة قطاعاتهم: {symbols('sector_leaders')}",
        f"قوة منفردة: {symbols('isolated_strength')}",
        f"ضعيف داخل قطاع قوي: {symbols('weak_in_hot_sector')}",
        "",
    ]


def format_symbol_sector_intelligence_arabic_lines(
    context: dict[str, Any] | None,
) -> list[str]:
    if not context:
        return []

    sector = context.get("sector") or UNKNOWN_SECTOR
    label = context.get("sector_label") or LABEL_UNKNOWN_SECTOR
    relative = context.get("relative_to_sector_pct")
    relation = "غير متاح"
    if isinstance(relative, (int, float)):
        relation = (
            f"أقوى من متوسط القطاع بـ {relative:+.1f}%"
            if relative >= 0
            else f"أضعف من متوسط القطاع بـ {relative:+.1f}%"
        )
    lines = [
        f"القطاع: {sector}",
        f"علاقة السهم بالقطاع: {label}",
        f"مقارنة بمتوسط القطاع: {relation}",
    ]
    reasons = [str(value) for value in (context.get("sector_reasons") or [])]
    risks = [str(value) for value in (context.get("sector_risks") or [])]
    if reasons:
        lines.append(f"سبب القطاع: {reasons[0]}")
    elif risks:
        lines.append(f"مخاطر القطاع: {risks[0]}")
    return lines
