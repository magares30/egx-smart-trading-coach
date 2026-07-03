"""Smarter Confidence Score V2 from existing report context."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

CONFIDENCE_LABEL_STRONG = "STRONG"
CONFIDENCE_LABEL_GOOD = "GOOD"
CONFIDENCE_LABEL_MIXED = "MIXED"
CONFIDENCE_LABEL_WEAK = "WEAK"
CONFIDENCE_LABEL_WAIT = "WAIT"

CONFIDENCE_COMPONENT_KEYS = (
    "base_score",
    "technical",
    "market_mood",
    "memory",
    "sector",
    "risk_reward",
    "fundamentals",
    "liquidity",
    "session",
)

_HEADER_SYMBOL_RE = re.compile(r"^(\d+\.\s+)([A-Z0-9]+)(\s+\|.*)$")


@dataclass(frozen=True)
class ConfidenceInput:
    """Inputs for one symbol's additive Confidence V2 score."""

    symbol: str
    base_score: int | None = None
    technical_status: str | None = None
    technical_score: int | None = None
    talib_status: str | None = None
    talib_available: bool = False
    market_mood: str | None = None
    memory_label: str | None = None
    sector_status: str | None = None
    risk_reward: float | None = None
    fundamental_status: str | None = None
    volume_ratio: float | None = None
    market_closed: bool = False
    stale_prices: bool = False


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _component_from_technical(
    status: str | None,
    score: int | None,
    *,
    talib_status: str | None,
    talib_available: bool,
) -> tuple[int, list[str], list[str]]:
    reasons: list[str] = []
    risks: list[str] = []
    total = 0
    normalized = str(status or "UNKNOWN").upper()

    if score is not None:
        total += _clamp(int(score), -14, 14)
    elif normalized == "STRONG":
        total += 12
    elif normalized in {"OK", "GOOD"}:
        total += 8
    elif normalized == "CAUTION":
        total -= 4
    elif normalized == "WEAK":
        total -= 12

    if normalized in {"STRONG", "OK", "GOOD"}:
        reasons.append("TradingView technical confirmation supportive")
    elif normalized in {"CAUTION", "WEAK"}:
        risks.append("TradingView technical confirmation is cautious")

    talib = str(talib_status or "").upper()
    if talib_available and talib in {"STRONG", "OK"}:
        total += 5
        reasons.append("TA-Lib confirmation aligned")
    elif talib_available and talib in {"CAUTION", "WEAK"}:
        total -= 5
        risks.append("TA-Lib confirmation is cautious")

    return _clamp(total, -20, 20), reasons, risks


def _component_from_market_mood(mood: str | None) -> tuple[int, list[str], list[str]]:
    normalized = str(mood or "NEUTRAL").upper()
    if normalized in {"STRONG", "BULLISH"}:
        return 15, ["Market mood supports risk appetite"], []
    if normalized == "POSITIVE":
        return 8, ["Market breadth is positive"], []
    if normalized in {"WEAK", "BEARISH"}:
        return -15, [], ["Market mood is weak"]
    return 0, [], []


def _component_from_memory(label: str | None) -> tuple[int, list[str], list[str]]:
    normalized = str(label or "").upper()
    if normalized == "IMPROVING":
        return 12, ["Market Memory shows improvement"], []
    if normalized == "PERSISTENT":
        return 6, ["Market Memory shows persistent visibility"], []
    if normalized == "RETURNING":
        return -3, [], ["Returning symbol; needs confirmation"]
    if normalized == "NEW":
        return -2, [], ["New symbol; less memory history"]
    if normalized == "FADING":
        return -12, [], ["Market Memory shows fading behavior"]
    if normalized == "WEAKENING":
        return -8, [], ["Market Memory shows weakening behavior"]
    return 0, [], []


def _component_from_sector(status: str | None) -> tuple[int, list[str], list[str]]:
    normalized = str(status or "").upper()
    if normalized == "HOT":
        return 10, ["Sector context is hot"], []
    if normalized == "WARM":
        return 5, ["Sector context is supportive"], []
    if normalized == "WEAK":
        return -8, [], ["Sector context is weak"]
    return 0, [], []


def _component_from_risk_reward(value: float | None) -> tuple[int, list[str], list[str]]:
    if value is None:
        return 0, [], []
    if value >= 2.0:
        return 8, ["Risk/reward is acceptable"], []
    if value >= 1.5:
        return 4, ["Risk/reward is fair"], []
    if value > 0:
        return -8, [], ["Risk/reward is weak"]
    return 0, [], []


def _component_from_fundamentals(status: str | None) -> tuple[int, list[str], list[str]]:
    normalized = str(status or "").upper()
    if normalized == "STRONG":
        return 10, ["Fundamentals are strong"], []
    if normalized == "OK":
        return 6, ["Fundamentals are acceptable"], []
    if normalized == "CAUTION":
        return -4, [], ["Fundamentals require caution"]
    if normalized == "WEAK":
        return -10, [], ["Fundamentals are weak"]
    return 0, [], []


def _component_from_liquidity(
    volume_ratio: float | None,
) -> tuple[int, list[str], list[str]]:
    if volume_ratio is None:
        return 0, [], []
    if volume_ratio >= 1.5:
        return 10, ["Relative volume is healthy"], []
    if volume_ratio >= 1.2:
        return 5, ["Relative volume is supportive"], []
    if volume_ratio < 0.8:
        return -10, [], ["Liquidity / relative volume is thin"]
    return 0, [], []


def _component_from_session(
    *,
    market_closed: bool,
    stale_prices: bool,
) -> tuple[int, list[str], list[str]]:
    if not market_closed and not stale_prices:
        return 0, [], []
    risks = []
    if market_closed:
        risks.append("market closed; review next session")
    if stale_prices:
        risks.append("prices may be stale")
    return -8, [], risks


def confidence_label_from_score(score: int) -> str:
    if score >= 85:
        return CONFIDENCE_LABEL_STRONG
    if score >= 70:
        return CONFIDENCE_LABEL_GOOD
    if score >= 55:
        return CONFIDENCE_LABEL_MIXED
    if score >= 40:
        return CONFIDENCE_LABEL_WEAK
    return CONFIDENCE_LABEL_WAIT


def build_confidence_v2(input_value: ConfidenceInput) -> dict[str, Any]:
    """Build explainable Confidence V2 output for one symbol."""
    base_score = _clamp(
        input_value.base_score if input_value.base_score is not None else 50,
        0,
        100,
    )
    reasons: list[str] = []
    risks: list[str] = []

    technical, technical_reasons, technical_risks = _component_from_technical(
        input_value.technical_status,
        input_value.technical_score,
        talib_status=input_value.talib_status,
        talib_available=input_value.talib_available,
    )
    market_mood, mood_reasons, mood_risks = _component_from_market_mood(
        input_value.market_mood
    )
    memory, memory_reasons, memory_risks = _component_from_memory(
        input_value.memory_label
    )
    sector, sector_reasons, sector_risks = _component_from_sector(
        input_value.sector_status
    )
    risk_reward, rr_reasons, rr_risks = _component_from_risk_reward(
        input_value.risk_reward
    )
    fundamentals, fundamental_reasons, fundamental_risks = _component_from_fundamentals(
        input_value.fundamental_status
    )
    liquidity, liquidity_reasons, liquidity_risks = _component_from_liquidity(
        input_value.volume_ratio
    )
    session, session_reasons, session_risks = _component_from_session(
        market_closed=input_value.market_closed,
        stale_prices=input_value.stale_prices,
    )

    for bucket in (
        technical_reasons,
        mood_reasons,
        memory_reasons,
        sector_reasons,
        rr_reasons,
        fundamental_reasons,
        liquidity_reasons,
        session_reasons,
    ):
        reasons.extend(bucket)
    for bucket in (
        technical_risks,
        mood_risks,
        memory_risks,
        sector_risks,
        rr_risks,
        fundamental_risks,
        liquidity_risks,
        session_risks,
    ):
        risks.extend(bucket)

    components = {
        "base_score": base_score,
        "technical": _clamp(technical, -20, 20),
        "market_mood": _clamp(market_mood, -15, 15),
        "memory": _clamp(memory, -15, 15),
        "sector": _clamp(sector, -10, 10),
        "risk_reward": _clamp(risk_reward, -10, 10),
        "fundamentals": _clamp(fundamentals, -10, 10),
        "liquidity": _clamp(liquidity, -10, 10),
        "session": _clamp(session, -10, 0),
    }
    score = _clamp(
        components["base_score"]
        + components["technical"]
        + components["market_mood"]
        + components["memory"]
        + components["sector"]
        + components["risk_reward"]
        + components["fundamentals"]
        + components["liquidity"]
        + components["session"],
        0,
        100,
    )

    return {
        "symbol": input_value.symbol,
        "confidence_score_v2": score,
        "confidence_label_v2": confidence_label_from_score(score),
        "confidence_reasons_v2": reasons[:6],
        "confidence_risks_v2": risks[:6],
        "confidence_components_v2": components,
    }


def build_confidence_v2_context(
    inputs: list[ConfidenceInput],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], bool]:
    """Build per-symbol confidence context, skipping failed symbols safely."""
    context: dict[str, dict[str, Any]] = {}
    for item in inputs:
        symbol = str(item.symbol or "").strip().upper()
        if not symbol:
            continue
        try:
            context[symbol] = build_confidence_v2(
                ConfidenceInput(
                    symbol=symbol,
                    base_score=item.base_score,
                    technical_status=item.technical_status,
                    technical_score=item.technical_score,
                    talib_status=item.talib_status,
                    talib_available=item.talib_available,
                    market_mood=item.market_mood,
                    memory_label=item.memory_label,
                    sector_status=item.sector_status,
                    risk_reward=item.risk_reward,
                    fundamental_status=item.fundamental_status,
                    volume_ratio=item.volume_ratio,
                    market_closed=item.market_closed,
                    stale_prices=item.stale_prices,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive safety guard
            logger.warning("Confidence V2 failed for %s; skipping: %s", symbol, exc)

    return context, summarize_confidence_v2_context(context), bool(context)


def summarize_confidence_v2_context(
    context_by_symbol: dict[str, dict[str, Any]],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    buckets = {
        "strong": [],
        "good": [],
        "mixed": [],
        "weak": [],
        "wait": [],
    }
    risks: list[str] = []
    sorted_rows = sorted(
        context_by_symbol.values(),
        key=lambda row: (
            -int(row.get("confidence_score_v2") or 0),
            str(row.get("symbol") or ""),
        ),
    )
    for row in sorted_rows:
        label = str(row.get("confidence_label_v2") or CONFIDENCE_LABEL_WAIT).lower()
        key = label if label in buckets else "wait"
        if len(buckets[key]) < limit:
            buckets[key].append(row.get("symbol"))
        for risk in row.get("confidence_risks_v2") or []:
            risk_text = str(risk)
            if risk_text and risk_text not in risks:
                risks.append(risk_text)
            if len(risks) >= 3:
                break

    return {
        "available": bool(context_by_symbol),
        **buckets,
        "main_risks": risks,
        "top_reason": _top_reason(sorted_rows),
    }


def _top_reason(rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        reasons = row.get("confidence_reasons_v2") or []
        if reasons:
            return str(reasons[0])
    return None


def format_confidence_v2_report_lines(summary: dict[str, Any]) -> list[str]:
    if not summary.get("available"):
        return ["- Confidence V2 unavailable."]

    def symbols(key: str) -> str:
        values = summary.get(key) or []
        return ", ".join(str(value) for value in values) if values else "(none)"

    mixed_wait = [
        value
        for value in [symbols("mixed"), symbols("wait")]
        if value != "(none)"
    ]
    risks = summary.get("main_risks") or []
    return [
        f"- Strong confidence: {symbols('strong')}",
        f"- Good confidence: {symbols('good')}",
        f"- Mixed / wait: {', '.join(mixed_wait) if mixed_wait else '(none)'}",
        (
            "- Main confidence risks: "
            f"{', '.join(str(risk) for risk in risks) if risks else '(none)'}"
        ),
    ]


def format_confidence_v2_suffix(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    label = context.get("confidence_label_v2") or CONFIDENCE_LABEL_WAIT
    score = context.get("confidence_score_v2")
    return f" | Confidence V2: {label} {score}"


def enrich_section_lines_with_confidence_v2(
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
        suffix = format_confidence_v2_suffix(context_by_symbol.get(symbol))
        if not suffix or " | Confidence V2:" in line:
            enriched.append(line)
            continue
        if " | Memory:" in line:
            before, after = line.split(" | Memory:", 1)
            enriched.append(f"{before}{suffix} | Memory:{after}")
        else:
            enriched.append(f"{line}{suffix}")
    return enriched


def format_confidence_v2_arabic_block(summary: dict[str, Any] | None) -> list[str]:
    if not summary or not summary.get("available"):
        return []

    def symbols(key: str) -> str:
        values = summary.get(key) or []
        return ", ".join(str(value) for value in values) if values else "لا يوجد"

    mixed_wait_values = list(summary.get("mixed") or []) + list(
        summary.get("wait") or []
    )
    mixed_wait = (
        ", ".join(str(value) for value in mixed_wait_values)
        if mixed_wait_values
        else "لا يوجد"
    )
    top_reason = summary.get("top_reason") or "غير متاح"
    return [
        "🧠 الثقة الذكية:",
        f"قوي: {symbols('strong')}",
        f"جيد: {symbols('good')}",
        f"انتظار/مختلط: {mixed_wait}",
        f"أهم سبب: {top_reason}",
        "",
    ]


def format_symbol_confidence_v2_arabic_lines(
    context: dict[str, Any] | None,
) -> list[str]:
    if not context:
        return []

    label = context.get("confidence_label_v2") or CONFIDENCE_LABEL_WAIT
    score = context.get("confidence_score_v2")
    lines = [f"الثقة الذكية: {label} {score}"]
    reasons = [str(value) for value in (context.get("confidence_reasons_v2") or [])]
    risks = [str(value) for value in (context.get("confidence_risks_v2") or [])]
    components = context.get("confidence_components_v2") or {}
    if reasons:
        lines.append(f"أسباب الثقة: {', '.join(reasons[:3])}")
    if risks:
        lines.append(f"مخاطر الثقة: {', '.join(risks[:3])}")
    if isinstance(components, dict):
        compact = []
        for key in ("technical", "market_mood", "memory", "sector", "session"):
            value = components.get(key)
            if value not in (None, 0):
                compact.append(
                    f"{key} {value:+d}" if isinstance(value, int) else f"{key} {value}"
                )
        if compact:
            lines.append(f"مكونات مختصرة: {', '.join(compact[:5])}")
    return lines
