"""TradingView technical confirmation scoring for scanner candidates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

import pandas as pd

DEFAULT_RSI_MIN = 45.0
DEFAULT_RSI_MAX = 70.0
DEFAULT_RSI_CAUTION = 75.0
DEFAULT_ADX_MIN = 20.0

TECHNICAL_UNAVAILABLE_NOTE = "Technical fields unavailable"

TECHNICAL_SNAPSHOT_COLUMNS = (
    "tv_recommend_all",
    "tv_recommend_ma",
    "tv_recommend_other",
    "rsi",
    "rsi_prev",
    "macd",
    "macd_signal",
    "ema20",
    "sma20",
    "ema50",
    "sma50",
    "adx",
    "atr",
)


class TechnicalStatus(str, Enum):
    STRONG = "STRONG"
    OK = "OK"
    CAUTION = "CAUTION"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TechnicalConfirmationConfig:
    """Thresholds for TradingView technical confirmation scoring."""

    enabled: bool = True
    rsi_min: float = DEFAULT_RSI_MIN
    rsi_max: float = DEFAULT_RSI_MAX
    rsi_caution: float = DEFAULT_RSI_CAUTION
    adx_min: float = DEFAULT_ADX_MIN


@dataclass(frozen=True)
class TechnicalConfirmationResult:
    """Technical confirmation outcome for one symbol row."""

    technical_score: int
    status: TechnicalStatus
    notes: list[str]


def build_technical_confirmation_config_from_cli(
    *,
    enabled: bool = True,
    rsi_min: float | None = None,
    rsi_max: float | None = None,
    rsi_caution: float | None = None,
    adx_min: float | None = None,
) -> TechnicalConfirmationConfig:
    """Build technical confirmation config from optional CLI values."""
    return TechnicalConfirmationConfig(
        enabled=enabled,
        rsi_min=rsi_min if rsi_min is not None else DEFAULT_RSI_MIN,
        rsi_max=rsi_max if rsi_max is not None else DEFAULT_RSI_MAX,
        rsi_caution=rsi_caution if rsi_caution is not None else DEFAULT_RSI_CAUTION,
        adx_min=adx_min if adx_min is not None else DEFAULT_ADX_MIN,
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


def _row_mapping(row: Mapping[str, object] | pd.Series) -> dict[str, object]:
    if isinstance(row, pd.Series):
        return row.to_dict()
    return dict(row)


def has_technical_fields(row: Mapping[str, object] | pd.Series) -> bool:
    """Return True when at least one technical column has a usable value."""
    mapping = _row_mapping(row)
    for column in TECHNICAL_SNAPSHOT_COLUMNS:
        if _safe_float(mapping.get(column)) is not None:
            return True
    return False


def technical_fields_available_in_dataframe(
    snapshot_df: pd.DataFrame | None,
) -> bool:
    """Return True when a snapshot dataframe includes technical columns."""
    if snapshot_df is None or snapshot_df.empty:
        return False
    return any(column in snapshot_df.columns for column in TECHNICAL_SNAPSHOT_COLUMNS)


def _status_from_score(score: int) -> TechnicalStatus:
    if score >= 10:
        return TechnicalStatus.STRONG
    if score >= 3:
        return TechnicalStatus.OK
    if score >= -2:
        return TechnicalStatus.CAUTION
    return TechnicalStatus.WEAK


def evaluate_technical_confirmation(
    row: Mapping[str, object] | pd.Series,
    config: TechnicalConfirmationConfig,
) -> TechnicalConfirmationResult:
    """Score TradingView technical fields for one symbol row."""
    if not config.enabled:
        return TechnicalConfirmationResult(
            technical_score=0,
            status=TechnicalStatus.UNKNOWN,
            notes=[],
        )

    mapping = _row_mapping(row)
    if not has_technical_fields(mapping):
        return TechnicalConfirmationResult(
            technical_score=0,
            status=TechnicalStatus.UNKNOWN,
            notes=[TECHNICAL_UNAVAILABLE_NOTE],
        )

    score = 0
    notes: list[str] = []
    close = _safe_float(mapping.get("close"))

    rsi = _safe_float(mapping.get("rsi"))
    if rsi is not None:
        if config.rsi_min <= rsi <= config.rsi_max:
            score += 4
            notes.append(f"RSI {rsi:.0f} in range")
        elif rsi > config.rsi_caution:
            score -= 6
            notes.append(f"RSI {rsi:.0f} overbought caution")
        elif rsi < 40:
            score -= 4
            notes.append(f"RSI {rsi:.0f} weak")

    ema20 = _safe_float(mapping.get("ema20"))
    sma20 = _safe_float(mapping.get("sma20"))
    if close is not None:
        if (ema20 is not None and close > ema20) or (sma20 is not None and close > sma20):
            score += 3
            notes.append("Above EMA20/SMA20")

    ema50 = _safe_float(mapping.get("ema50"))
    sma50 = _safe_float(mapping.get("sma50"))
    if close is not None:
        if (ema50 is not None and close > ema50) or (sma50 is not None and close > sma50):
            score += 3
            notes.append("Above EMA50/SMA50")

    macd = _safe_float(mapping.get("macd"))
    macd_signal = _safe_float(mapping.get("macd_signal"))
    if macd is not None and macd_signal is not None:
        if macd > macd_signal:
            score += 4
            notes.append("MACD positive")
        else:
            score -= 2
            notes.append("MACD negative")

    adx = _safe_float(mapping.get("adx"))
    if adx is not None:
        if adx >= config.adx_min:
            score += 3
            notes.append(f"ADX {adx:.0f} strong trend")
        else:
            score -= 1
            notes.append(f"ADX {adx:.0f} weak trend")

    recommend_all = _safe_float(mapping.get("tv_recommend_all"))
    if recommend_all is not None:
        if recommend_all > 0:
            score += 3
            notes.append("TV recommend positive")
        elif recommend_all < 0:
            score -= 3
            notes.append("TV recommend negative")

    clamped_score = max(-20, min(20, score))
    return TechnicalConfirmationResult(
        technical_score=clamped_score,
        status=_status_from_score(clamped_score),
        notes=notes,
    )


def row_for_symbol(
    snapshot_df: pd.DataFrame | None,
    symbol: str,
) -> dict[str, object]:
    """Return a merged lookup row for one symbol from a ranking dataframe."""
    if snapshot_df is None or snapshot_df.empty or "symbol" not in snapshot_df.columns:
        return {"symbol": symbol}
    matches = snapshot_df.loc[snapshot_df["symbol"] == symbol]
    if matches.empty:
        return {"symbol": symbol}
    return matches.iloc[0].to_dict()


def format_technical_confirmation_line(
    result: TechnicalConfirmationResult,
    row: Mapping[str, object] | pd.Series | None = None,
) -> str:
    """Build a compact technical confirmation line for report output."""
    if result.status == TechnicalStatus.UNKNOWN:
        return "Technical: UNKNOWN | technical fields unavailable"

    parts = [f"Technical: {result.status.value} ({result.technical_score:+d})"]
    mapping = _row_mapping(row) if row is not None else {}

    rsi = _safe_float(mapping.get("rsi"))
    if rsi is not None:
        parts.append(f"RSI {rsi:.0f}")

    macd = _safe_float(mapping.get("macd"))
    macd_signal = _safe_float(mapping.get("macd_signal"))
    if macd is not None and macd_signal is not None:
        parts.append("MACD positive" if macd > macd_signal else "MACD negative")

    close = _safe_float(mapping.get("close"))
    ema20 = _safe_float(mapping.get("ema20"))
    sma20 = _safe_float(mapping.get("sma20"))
    if close is not None and (
        (ema20 is not None and close > ema20) or (sma20 is not None and close > sma20)
    ):
        if ema20 is not None and close > ema20:
            parts.append("Above EMA20")
        elif sma20 is not None and close > sma20:
            parts.append("Above SMA20")

    return " | ".join(parts)
