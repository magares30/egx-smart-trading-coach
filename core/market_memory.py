"""Lightweight market memory across EGX report generations."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, UTC
from typing import Any

from core.cloud_state_store import (
    GcsStateStore,
    LocalStateStore,
    MARKET_MEMORY_KEY,
    get_state_store,
    is_gcs_state_enabled,
)

logger = logging.getLogger(__name__)

MARKET_MEMORY_VERSION = 1
MAX_RECENT_APPEARANCES = 5
SCORE_DELTA_THRESHOLD = 10
RANK_DELTA_THRESHOLD = 2

STATUS_SIGNAL = "SIGNAL"
STATUS_CANDIDATE = "CANDIDATE"
STATUS_WATCH = "WATCH"
STATUS_BLOCKED = "BLOCKED"
STATUS_POSITION = "POSITION"
STATUS_UNKNOWN = "UNKNOWN"

LABEL_NEW = "NEW"
LABEL_IMPROVING = "IMPROVING"
LABEL_PERSISTENT = "PERSISTENT"
LABEL_FADING = "FADING"
LABEL_WEAKENING = "WEAKENING"
LABEL_RETURNING = "RETURNING"
LABEL_STABLE = "STABLE"

STATUS_STRENGTH = {
    STATUS_UNKNOWN: 0,
    STATUS_BLOCKED: 1,
    STATUS_WATCH: 2,
    STATUS_POSITION: 3,
    STATUS_CANDIDATE: 4,
    STATUS_SIGNAL: 5,
}

_HEADER_SYMBOL_RE = re.compile(r"^(\d+\.\s+)([A-Z0-9]+)(\s+\|.*)$")


@dataclass(frozen=True)
class SymbolObservation:
    """Current report observation for one symbol."""

    symbol: str
    status: str = STATUS_UNKNOWN
    score: int | None = None
    change_pct: float | None = None
    sector: str | None = None
    rank: int | None = None


def empty_market_memory_state() -> dict[str, Any]:
    """Return a fresh market-memory payload."""
    return {
        "version": MARKET_MEMORY_VERSION,
        "updated_at": None,
        "symbols": {},
    }


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _coerce_int(value: object | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _coerce_float(value: object | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _status_strength(status: object | None) -> int:
    return STATUS_STRENGTH.get(str(status or STATUS_UNKNOWN), 0)


def _choose_better_observation(
    current: SymbolObservation | None,
    incoming: SymbolObservation,
) -> SymbolObservation:
    if current is None:
        return incoming
    current_strength = _status_strength(current.status)
    incoming_strength = _status_strength(incoming.status)
    if incoming_strength > current_strength:
        return incoming
    if incoming_strength < current_strength:
        return current
    current_score = current.score if current.score is not None else -1
    incoming_score = incoming.score if incoming.score is not None else -1
    if incoming_score > current_score:
        return incoming
    return current


def _fresh_entry(
    observation: SymbolObservation,
    *,
    report_date: date,
    label: str,
) -> dict[str, Any]:
    status = observation.status or STATUS_UNKNOWN
    return {
        "symbol": observation.symbol,
        "first_seen_date": report_date.isoformat(),
        "last_seen_date": report_date.isoformat(),
        "appearances_total": 1,
        "recent_appearances": 1,
        "last_status": status,
        "previous_status": None,
        "last_score": observation.score,
        "previous_score": None,
        "best_score": observation.score,
        "last_change_pct": observation.change_pct,
        "previous_change_pct": None,
        "last_sector": observation.sector,
        "last_rank": observation.rank,
        "previous_rank": None,
        "consecutive_watch_count": 1 if status == STATUS_WATCH else 0,
        "consecutive_candidate_count": 1 if status == STATUS_CANDIDATE else 0,
        "consecutive_blocked_count": 1 if status == STATUS_BLOCKED else 0,
        "last_memory_label": label,
        "notes": [],
    }


def classify_memory_label(
    previous: dict[str, Any] | None,
    observation: SymbolObservation,
    *,
    report_date: date,
) -> str:
    """Classify the current symbol memory transition."""
    if previous is None:
        return LABEL_NEW

    previous_status = str(previous.get("last_status") or STATUS_UNKNOWN)
    current_status = observation.status or STATUS_UNKNOWN
    previous_strength = _status_strength(previous_status)
    current_strength = _status_strength(current_status)
    previous_score = _coerce_int(previous.get("last_score"))
    current_score = observation.score
    previous_rank = _coerce_int(previous.get("last_rank"))
    current_rank = observation.rank
    previous_change = _coerce_float(previous.get("last_change_pct"))
    current_change = observation.change_pct

    recent = _coerce_int(previous.get("recent_appearances")) or 0
    if recent <= 0:
        if current_strength >= previous_strength:
            return LABEL_RETURNING

    if current_strength > previous_strength:
        return LABEL_IMPROVING
    if current_strength < previous_strength:
        return LABEL_FADING

    if previous_score is not None and current_score is not None:
        delta = current_score - previous_score
        if delta >= SCORE_DELTA_THRESHOLD:
            return LABEL_IMPROVING
        if delta <= -SCORE_DELTA_THRESHOLD:
            return LABEL_WEAKENING if current_strength >= previous_strength else LABEL_FADING

    if previous_rank is not None and current_rank is not None:
        rank_delta = previous_rank - current_rank
        if rank_delta >= RANK_DELTA_THRESHOLD:
            return LABEL_IMPROVING
        if rank_delta <= -RANK_DELTA_THRESHOLD:
            return LABEL_FADING

    if previous_change is not None and current_change is not None:
        if current_change <= previous_change - 1.0:
            return LABEL_WEAKENING

    if recent >= 2:
        return LABEL_PERSISTENT
    return LABEL_STABLE


def update_memory_entry(
    previous: dict[str, Any] | None,
    observation: SymbolObservation,
    *,
    report_date: date,
) -> dict[str, Any]:
    """Update one symbol's memory entry."""
    label = classify_memory_label(previous, observation, report_date=report_date)
    if previous is None:
        return _fresh_entry(observation, report_date=report_date, label=label)

    status = observation.status or STATUS_UNKNOWN
    previous_status = previous.get("last_status")
    previous_score = previous.get("last_score")
    previous_change = previous.get("last_change_pct")
    previous_rank = previous.get("last_rank")
    appearances = (_coerce_int(previous.get("appearances_total")) or 0) + 1
    recent = min((_coerce_int(previous.get("recent_appearances")) or 0) + 1, MAX_RECENT_APPEARANCES)
    best_score = observation.score
    existing_best = _coerce_int(previous.get("best_score"))
    if existing_best is not None and (best_score is None or existing_best > best_score):
        best_score = existing_best

    return {
        "symbol": observation.symbol,
        "first_seen_date": previous.get("first_seen_date") or report_date.isoformat(),
        "last_seen_date": report_date.isoformat(),
        "appearances_total": appearances,
        "recent_appearances": recent,
        "last_status": status,
        "previous_status": previous_status,
        "last_score": observation.score,
        "previous_score": previous_score,
        "best_score": best_score,
        "last_change_pct": observation.change_pct,
        "previous_change_pct": previous_change,
        "last_sector": observation.sector or previous.get("last_sector"),
        "last_rank": observation.rank,
        "previous_rank": previous_rank,
        "consecutive_watch_count": (
            (_coerce_int(previous.get("consecutive_watch_count")) or 0) + 1
            if status == STATUS_WATCH
            else 0
        ),
        "consecutive_candidate_count": (
            (_coerce_int(previous.get("consecutive_candidate_count")) or 0) + 1
            if status == STATUS_CANDIDATE
            else 0
        ),
        "consecutive_blocked_count": (
            (_coerce_int(previous.get("consecutive_blocked_count")) or 0) + 1
            if status == STATUS_BLOCKED
            else 0
        ),
        "last_memory_label": label,
        "notes": list(previous.get("notes") or []),
    }


def memory_context_for_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Compact context stored in report JSON and used by Telegram."""
    last_score = _coerce_int(entry.get("last_score"))
    previous_score = _coerce_int(entry.get("previous_score"))
    score_delta = (
        last_score - previous_score
        if last_score is not None and previous_score is not None
        else None
    )
    return {
        "memory_label": entry.get("last_memory_label") or LABEL_STABLE,
        "appearances_total": _coerce_int(entry.get("appearances_total")) or 0,
        "recent_appearances": _coerce_int(entry.get("recent_appearances")) or 0,
        "previous_score": previous_score,
        "score_delta": score_delta,
        "previous_status": entry.get("previous_status"),
        "last_status": entry.get("last_status"),
        "last_score": last_score,
    }


def summarize_memory_context(
    context_by_symbol: dict[str, dict[str, Any]],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Build a compact summary bucketed by memory label."""
    buckets = {
        "new": [],
        "improving": [],
        "persistent": [],
        "fading": [],
        "weakening": [],
        "returning": [],
        "stable": [],
    }
    label_to_key = {
        LABEL_NEW: "new",
        LABEL_IMPROVING: "improving",
        LABEL_PERSISTENT: "persistent",
        LABEL_FADING: "fading",
        LABEL_WEAKENING: "weakening",
        LABEL_RETURNING: "returning",
        LABEL_STABLE: "stable",
    }
    for symbol in sorted(context_by_symbol):
        context = context_by_symbol[symbol]
        key = label_to_key.get(str(context.get("memory_label")), "stable")
        if len(buckets[key]) < limit:
            buckets[key].append(symbol)

    available = any(buckets[key] for key in buckets)
    return {
        "available": available,
        **buckets,
    }


def format_market_memory_report_lines(summary: dict[str, Any]) -> list[str]:
    """Render the Market Memory report section."""
    if not summary.get("available"):
        return ["- Memory unavailable or starting fresh."]

    def symbols(key: str) -> str:
        values = summary.get(key) or []
        return ", ".join(str(value) for value in values) if values else "(none)"

    return [
        f"- New today: {symbols('new')}",
        f"- Improving: {symbols('improving')}",
        f"- Persistent: {symbols('persistent')}",
        f"- Fading / Weakening: {', '.join([s for s in [symbols('fading'), symbols('weakening')] if s != '(none)']) or '(none)'}",
    ]


def format_memory_context_suffix(context: dict[str, Any] | None) -> str:
    """Short text suffix appended to useful report rows."""
    if not context:
        return ""
    label = context.get("memory_label") or LABEL_STABLE
    seen = context.get("appearances_total") or 0
    return f" | Memory: {label} | Seen {seen}x"


def enrich_section_lines_with_memory(
    lines: list[str],
    context_by_symbol: dict[str, dict[str, Any]],
) -> list[str]:
    """Append memory context to section header rows without changing scoring."""
    enriched: list[str] = []
    for line in lines:
        match = _HEADER_SYMBOL_RE.match(line)
        if not match:
            enriched.append(line)
            continue
        symbol = match.group(2).upper()
        suffix = format_memory_context_suffix(context_by_symbol.get(symbol))
        enriched.append(f"{line}{suffix}" if suffix and " | Memory:" not in line else line)
    return enriched


def format_market_memory_arabic_block(summary: dict[str, Any] | None) -> list[str]:
    """Render a short Arabic market-memory block for Telegram."""
    if not summary or not summary.get("available"):
        return []

    def symbols(key: str) -> str:
        values = summary.get(key) or []
        return ", ".join(str(value) for value in values) if values else "لا يوجد"

    weakening = list(summary.get("fading") or []) + list(summary.get("weakening") or [])
    weakening_text = ", ".join(str(value) for value in weakening) if weakening else "لا يوجد"
    return [
        "🧠 ذاكرة السوق:",
        f"جديد: {symbols('new')}",
        f"بيتحسن: {symbols('improving')}",
        f"متكرر: {symbols('persistent')}",
        f"بيضعف: {weakening_text}",
        "",
    ]


def format_symbol_memory_arabic_lines(context: dict[str, Any] | None) -> list[str]:
    """Render symbol-specific memory lines for the WHY flow."""
    if not context:
        return []
    lines = [
        f"ذاكرة السهم: {context.get('memory_label', LABEL_STABLE)}",
        f"ظهر كام مرة: {context.get('appearances_total', 0)} (آخر {context.get('recent_appearances', 0)})",
    ]
    previous_score = context.get("previous_score")
    last_score = context.get("last_score")
    if previous_score is not None or last_score is not None:
        lines.append(f"السكور السابق/الحالي: {previous_score or 'n/a'} → {last_score or 'n/a'}")
    previous_status = context.get("previous_status")
    last_status = context.get("last_status")
    if previous_status or last_status:
        lines.append(f"الحالة السابقة/الحالية: {previous_status or 'n/a'} → {last_status or 'n/a'}")
    return lines


def _read_memory_text() -> str | None:
    if is_gcs_state_enabled():
        try:
            store = get_state_store()
            if isinstance(store, GcsStateStore):
                content = store.read_text(MARKET_MEMORY_KEY)
                if content:
                    return content
        except Exception as exc:  # pragma: no cover - cloud failure defensive path
            logger.warning("Market memory cloud read failed; using local fallback: %s", exc)

    try:
        return LocalStateStore().read_text(MARKET_MEMORY_KEY)
    except Exception as exc:  # pragma: no cover - defensive path
        logger.warning("Market memory local read failed; starting fresh: %s", exc)
        return None


def load_market_memory_state() -> dict[str, Any]:
    """Load market memory from GCS/local state, starting fresh on missing/corrupt data."""
    content = _read_memory_text()
    if not content:
        return empty_market_memory_state()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Market memory file is corrupt; rebuilding from current report.")
        return empty_market_memory_state()
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), dict):
        logger.warning("Market memory payload has invalid shape; rebuilding.")
        return empty_market_memory_state()
    payload.setdefault("version", MARKET_MEMORY_VERSION)
    payload.setdefault("updated_at", None)
    return payload


def save_market_memory_state(state: dict[str, Any]) -> None:
    """Persist memory to GCS when enabled, with local fallback on failures."""
    content = json.dumps(state, indent=2, sort_keys=True)
    if is_gcs_state_enabled():
        try:
            store = get_state_store()
            if isinstance(store, GcsStateStore):
                store.write_text(MARKET_MEMORY_KEY, content)
                return
        except Exception as exc:  # pragma: no cover - cloud failure defensive path
            logger.warning("Market memory cloud write failed; using local fallback: %s", exc)

    try:
        LocalStateStore().write_text(MARKET_MEMORY_KEY, content)
    except Exception as exc:  # pragma: no cover - defensive path
        logger.warning("Market memory local write failed; report will continue: %s", exc)


def update_market_memory_state(
    previous_state: dict[str, Any],
    observations: list[SymbolObservation],
    *,
    report_date: date,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    """Update memory with current observations and return context/summary."""
    state = {
        "version": MARKET_MEMORY_VERSION,
        "updated_at": datetime.now(UTC).isoformat(),
        "symbols": dict(previous_state.get("symbols") or {}),
    }
    current_by_symbol: dict[str, SymbolObservation] = {}
    for observation in observations:
        symbol = _normalize_symbol(observation.symbol)
        if not symbol:
            continue
        normalized = SymbolObservation(
            symbol=symbol,
            status=observation.status or STATUS_UNKNOWN,
            score=observation.score,
            change_pct=observation.change_pct,
            sector=observation.sector,
            rank=observation.rank,
        )
        current_by_symbol[symbol] = _choose_better_observation(
            current_by_symbol.get(symbol),
            normalized,
        )

    context_by_symbol: dict[str, dict[str, Any]] = {}
    for symbol, observation in current_by_symbol.items():
        previous = state["symbols"].get(symbol)
        if not isinstance(previous, dict):
            previous = None
        entry = update_memory_entry(previous, observation, report_date=report_date)
        state["symbols"][symbol] = entry
        context_by_symbol[symbol] = memory_context_for_entry(entry)

    summary = summarize_memory_context(context_by_symbol)
    return state, context_by_symbol, summary


def process_market_memory(
    observations: list[SymbolObservation],
    *,
    report_date: date,
) -> tuple[bool, dict[str, dict[str, Any]], dict[str, Any]]:
    """Load, update, save, and summarize market memory without failing reports."""
    try:
        previous_state = load_market_memory_state()
        updated_state, context, summary = update_market_memory_state(
            previous_state,
            observations,
            report_date=report_date,
        )
        save_market_memory_state(updated_state)
        return True, context, summary
    except Exception as exc:  # pragma: no cover - last-resort safety guard
        logger.warning("Market memory update failed; report will continue: %s", exc)
        return False, {}, {"available": False}
