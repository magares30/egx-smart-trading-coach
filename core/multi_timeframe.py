"""Multi-timeframe entry timing checks using TradingView intraday fields."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

import pandas as pd

from core.tradingview_data_provider import (
    MULTI_TIMEFRAME_UNAVAILABLE_WARNING,
    fetch_tradingview_timeframe_snapshot,
    merge_timeframe_snapshots,
)

DEFAULT_ENTRY_TIMEFRAMES = ("1h", "15m")
DEFAULT_RSI_MIN = 45.0
DEFAULT_RSI_MAX = 70.0
DEFAULT_RSI_CAUTION = 75.0
DEFAULT_RSI_WEAK = 40.0
DEFAULT_ADX_MIN = 20.0

ENTRY_TIMING_UNAVAILABLE_NOTE = "multi-timeframe data unavailable"

TIMEFRAME_PREFIX_BY_NAME = {
    "1h": "tf_1h",
    "15m": "tf_15m",
}


class EntryTimingStatus(str, Enum):
    READY = "READY"
    WATCH = "WATCH"
    WAIT = "WAIT"
    AVOID = "AVOID"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MultiTimeframeConfig:
    """Configuration for multi-timeframe entry timing checks."""

    enabled: bool = True
    timeframes: tuple[str, ...] = DEFAULT_ENTRY_TIMEFRAMES
    rsi_min: float = DEFAULT_RSI_MIN
    rsi_max: float = DEFAULT_RSI_MAX
    rsi_caution: float = DEFAULT_RSI_CAUTION
    rsi_weak: float = DEFAULT_RSI_WEAK
    adx_min: float = DEFAULT_ADX_MIN


@dataclass(frozen=True)
class MultiTimeframeResult:
    """Entry timing outcome for one candidate."""

    entry_timing_score: int
    status: EntryTimingStatus
    notes: list[str]
    summary: str
    tf_1h_label: str | None = None
    tf_15m_label: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize entry timing for JSON report output."""
        return {
            "entry_timing_score": self.entry_timing_score,
            "status": self.status.value,
            "notes": list(self.notes),
            "summary": self.summary,
            "tf_1h_label": self.tf_1h_label,
            "tf_15m_label": self.tf_15m_label,
        }


def build_multi_timeframe_config_from_cli(
    *,
    enabled: bool = True,
    entry_timeframes: str = "1h,15m",
) -> MultiTimeframeConfig:
    """Build multi-timeframe config from optional CLI values."""
    parsed = tuple(
        item.strip().lower()
        for item in entry_timeframes.split(",")
        if item.strip()
    )
    timeframes = tuple(
        timeframe
        for timeframe in parsed
        if timeframe in TIMEFRAME_PREFIX_BY_NAME
    )
    return MultiTimeframeConfig(
        enabled=enabled,
        timeframes=timeframes or DEFAULT_ENTRY_TIMEFRAMES,
    )


def _safe_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _row_mapping(row: Mapping[str, object] | pd.Series | None) -> dict[str, object]:
    if row is None:
        return {}
    if isinstance(row, pd.Series):
        return row.to_dict()
    return dict(row)


def _timeframe_prefix(timeframe: str) -> str:
    return TIMEFRAME_PREFIX_BY_NAME.get(timeframe, f"tf_{timeframe}")


def _timeframe_field(row: Mapping[str, object] | pd.Series | None, prefix: str, field: str) -> float | None:
    mapping = _row_mapping(row)
    return _safe_float(mapping.get(f"{prefix}_{field}"))


def has_timeframe_data(row: Mapping[str, object] | pd.Series | None, timeframe: str) -> bool:
    """Return True when a row includes usable values for one timeframe."""
    prefix = _timeframe_prefix(timeframe)
    return any(
        _timeframe_field(row, prefix, field) is not None
        for field in ("close", "rsi", "recommend_all", "macd", "macd_signal")
    )


def _status_from_score(score: int) -> EntryTimingStatus:
    if score >= 12:
        return EntryTimingStatus.READY
    if score >= 5:
        return EntryTimingStatus.WATCH
    if score > -5:
        return EntryTimingStatus.WAIT
    return EntryTimingStatus.AVOID


def _score_timeframe_row(
    row: Mapping[str, object] | pd.Series | None,
    timeframe: str,
    config: MultiTimeframeConfig,
) -> tuple[int, list[str], str | None]:
    prefix = _timeframe_prefix(timeframe)
    if not has_timeframe_data(row, timeframe):
        return 0, [], None

    score = 0
    notes: list[str] = []
    close = _timeframe_field(row, prefix, "close")
    recommend_all = _timeframe_field(row, prefix, "recommend_all")
    rsi = _timeframe_field(row, prefix, "rsi")
    macd = _timeframe_field(row, prefix, "macd")
    macd_signal = _timeframe_field(row, prefix, "macd_signal")
    ema20 = _timeframe_field(row, prefix, "ema20")
    sma20 = _timeframe_field(row, prefix, "sma20")
    adx = _timeframe_field(row, prefix, "adx")

    if recommend_all is not None and recommend_all > 0:
        score += 2
        notes.append(f"{timeframe} recommend positive")

    if rsi is not None:
        if config.rsi_min <= rsi <= config.rsi_max:
            score += 3
            notes.append(f"{timeframe} RSI aligned")
        elif rsi > config.rsi_caution:
            score -= 4
            notes.append(f"{timeframe} RSI overbought caution")
        elif rsi < config.rsi_weak:
            score -= 3
            notes.append(f"{timeframe} RSI weak")

    if macd is not None and macd_signal is not None:
        if macd > macd_signal:
            score += 3
            notes.append(f"{timeframe} MACD aligned")
        else:
            score -= 2
            notes.append(f"{timeframe} MACD negative")

    if close is not None and (
        (ema20 is not None and close > ema20) or (sma20 is not None and close > sma20)
    ):
        score += 2
        notes.append(f"{timeframe} above MA")

    if adx is not None:
        if adx >= config.adx_min:
            score += 2
            notes.append(f"{timeframe} ADX strong")
        else:
            score -= 1
            notes.append(f"{timeframe} ADX weak")

    if score >= 8:
        label = "OK"
    elif score >= 3:
        label = "WATCH"
    elif score > -2:
        label = "CAUTION"
    else:
        label = "WEAK"
    return score, notes, label


def evaluate_entry_timing(
    daily_row: Mapping[str, object] | pd.Series | None,
    tf_1h_row: Mapping[str, object] | pd.Series | None = None,
    tf_15m_row: Mapping[str, object] | pd.Series | None = None,
    config: MultiTimeframeConfig | None = None,
) -> MultiTimeframeResult:
    """Score entry timing from daily and optional intraday timeframe rows."""
    _ = daily_row
    cfg = config or MultiTimeframeConfig()
    combined_row = _row_mapping(tf_1h_row)
    combined_row.update(_row_mapping(tf_15m_row))

    requested = [timeframe for timeframe in cfg.timeframes if timeframe in TIMEFRAME_PREFIX_BY_NAME]
    available = [timeframe for timeframe in requested if has_timeframe_data(combined_row, timeframe)]
    if not available:
        return MultiTimeframeResult(
            entry_timing_score=0,
            status=EntryTimingStatus.UNKNOWN,
            notes=[ENTRY_TIMING_UNAVAILABLE_NOTE],
            summary=f"Entry Timing: UNKNOWN | {ENTRY_TIMING_UNAVAILABLE_NOTE}",
        )

    total_score = 0
    notes: list[str] = []
    tf_1h_label: str | None = None
    tf_15m_label: str | None = None
    for timeframe in available:
        timeframe_score, timeframe_notes, label = _score_timeframe_row(combined_row, timeframe, cfg)
        total_score += timeframe_score
        notes.extend(timeframe_notes)
        if timeframe == "1h":
            tf_1h_label = label
        elif timeframe == "15m":
            tf_15m_label = label

    clamped_score = max(-20, min(20, total_score))
    status = _status_from_score(clamped_score)
    summary = format_entry_timing_line(
        MultiTimeframeResult(
            entry_timing_score=clamped_score,
            status=status,
            notes=notes,
            summary="",
            tf_1h_label=tf_1h_label,
            tf_15m_label=tf_15m_label,
        )
    )
    return MultiTimeframeResult(
        entry_timing_score=clamped_score,
        status=status,
        notes=notes,
        summary=summary,
        tf_1h_label=tf_1h_label,
        tf_15m_label=tf_15m_label,
    )


def format_entry_timing_line(result: MultiTimeframeResult) -> str:
    """Build a compact entry timing line for report output."""
    if result.status == EntryTimingStatus.UNKNOWN:
        return f"Entry Timing: UNKNOWN | {ENTRY_TIMING_UNAVAILABLE_NOTE}"

    parts = [f"Entry Timing: {result.status.value} ({result.entry_timing_score:+d})"]
    if result.tf_1h_label:
        parts.append(f"1H {result.tf_1h_label}")
    if result.tf_15m_label:
        parts.append(f"15m {result.tf_15m_label}")

    compact_notes: list[str] = []
    joined_notes = " ".join(result.notes)
    if "RSI aligned" in joined_notes:
        compact_notes.append("RSI aligned")
    if "RSI overbought caution" in joined_notes:
        compact_notes.append("RSI caution")
    if "MACD aligned" in joined_notes:
        compact_notes.append("MACD aligned")
    if "MACD negative" in joined_notes and "MACD aligned" not in joined_notes:
        compact_notes.append("MACD weak")
    parts.extend(compact_notes[:3])
    return " | ".join(parts)


def fetch_entry_timing_snapshot(
    symbols: list[str],
    config: MultiTimeframeConfig | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Fetch and merge multi-timeframe snapshots for candidate symbols."""
    cfg = config or MultiTimeframeConfig()
    if not cfg.enabled or not symbols:
        return pd.DataFrame(columns=["symbol"]), []

    warnings: list[str] = []
    frames: list[pd.DataFrame] = []
    for timeframe in cfg.timeframes:
        if timeframe not in TIMEFRAME_PREFIX_BY_NAME:
            continue
        frame = fetch_tradingview_timeframe_snapshot(timeframe, symbols)
        if frame is None or frame.empty:
            warnings.append(MULTI_TIMEFRAME_UNAVAILABLE_WARNING)
            continue
        frames.append(frame)

    if not frames:
        if not warnings:
            warnings.append(MULTI_TIMEFRAME_UNAVAILABLE_WARNING)
        return pd.DataFrame(columns=["symbol"]), warnings

    merged = merge_timeframe_snapshots(frames)
    if merged.empty:
        warnings.append(MULTI_TIMEFRAME_UNAVAILABLE_WARNING)
    return merged, warnings


def build_entry_timing_lookup(
    symbols: list[str],
    config: MultiTimeframeConfig | None = None,
) -> tuple[dict[str, MultiTimeframeResult], list[str]]:
    """Build per-symbol entry timing results for candidate symbols."""
    cfg = config or MultiTimeframeConfig()
    if not cfg.enabled or not symbols:
        return {}, []

    merged, warnings = fetch_entry_timing_snapshot(symbols, cfg)
    lookup: dict[str, MultiTimeframeResult] = {}
    for symbol in symbols:
        if merged.empty or "symbol" not in merged.columns:
            row = None
        else:
            matches = merged.loc[merged["symbol"] == symbol]
            row = matches.iloc[0] if not matches.empty else None
        lookup[symbol] = evaluate_entry_timing(
            None,
            tf_1h_row=row,
            tf_15m_row=row,
            config=cfg,
        )
    return lookup, warnings


def row_for_symbol_timeframes(
    timeframe_df: pd.DataFrame | None,
    symbol: str,
) -> dict[str, object]:
    """Return merged timeframe row dict for one symbol."""
    if timeframe_df is None or timeframe_df.empty or "symbol" not in timeframe_df.columns:
        return {"symbol": symbol}
    matches = timeframe_df.loc[timeframe_df["symbol"] == symbol]
    if matches.empty:
        return {"symbol": symbol}
    return matches.iloc[0].to_dict()
