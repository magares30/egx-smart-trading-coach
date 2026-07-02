"""TradingView fundamental quality scoring for scanner candidates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

import pandas as pd

DEFAULT_GOOD_MARKET_CAP = 1_000_000_000.0
DEFAULT_GOOD_PE_MAX = 25.0
DEFAULT_GOOD_PB_MAX = 5.0

FUNDAMENTAL_UNAVAILABLE_NOTE = "fundamental fields unavailable"

FUNDAMENTAL_SNAPSHOT_COLUMNS = (
    "market_cap_basic",
    "market_cap",
    "price_earnings_ttm",
    "pe_ratio",
    "price_book_fq",
    "pb_ratio",
    "dividends_yield_current",
    "dividend_yield",
)


class FundamentalStatus(str, Enum):
    STRONG = "STRONG"
    OK = "OK"
    CAUTION = "CAUTION"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FundamentalQualityConfig:
    """Thresholds for fundamental quality scoring and filter defaults."""

    good_market_cap: float = DEFAULT_GOOD_MARKET_CAP
    good_pe_max: float = DEFAULT_GOOD_PE_MAX
    good_pb_max: float = DEFAULT_GOOD_PB_MAX


@dataclass(frozen=True)
class FundamentalQualityResult:
    """Fundamental quality outcome for one symbol row."""

    fundamental_score: int
    status: FundamentalStatus
    notes: list[str]
    market_cap: float | None
    pe_ratio: float | None
    pb_ratio: float | None
    dividend_yield: float | None

    def to_dict(self) -> dict[str, object]:
        """Serialize fundamental quality for JSON report output."""
        return {
            "fundamental_score": self.fundamental_score,
            "status": self.status.value,
            "notes": list(self.notes),
            "market_cap": self.market_cap,
            "pe_ratio": self.pe_ratio,
            "pb_ratio": self.pb_ratio,
            "dividend_yield": self.dividend_yield,
        }


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


def _resolve_field(mapping: dict[str, object], *keys: str) -> float | None:
    for key in keys:
        value = _safe_float(mapping.get(key))
        if value is not None:
            return value
    return None


def normalize_fundamental_values(
    row: Mapping[str, object] | pd.Series,
) -> tuple[float | None, float | None, float | None, float | None]:
    """Read normalized fundamental values from a snapshot row."""
    mapping = _row_mapping(row)
    market_cap = _resolve_field(mapping, "market_cap_basic", "market_cap")
    pe_ratio = _resolve_field(mapping, "price_earnings_ttm", "pe_ratio")
    pb_ratio = _resolve_field(mapping, "price_book_fq", "pb_ratio")
    dividend_yield = _resolve_field(
        mapping,
        "dividends_yield_current",
        "dividend_yield",
    )
    return market_cap, pe_ratio, pb_ratio, dividend_yield


def has_fundamental_fields(row: Mapping[str, object] | pd.Series) -> bool:
    """Return True when at least one fundamental column has a usable value."""
    market_cap, pe_ratio, pb_ratio, dividend_yield = normalize_fundamental_values(row)
    return any(
        value is not None
        for value in (market_cap, pe_ratio, pb_ratio, dividend_yield)
    )


def fundamental_fields_available_in_dataframe(
    snapshot_df: pd.DataFrame | None,
) -> bool:
    """Return True when a snapshot dataframe includes fundamental columns."""
    if snapshot_df is None or snapshot_df.empty:
        return False
    return any(column in snapshot_df.columns for column in FUNDAMENTAL_SNAPSHOT_COLUMNS)


def _status_from_score(
    score: int,
    *,
    expensive_pe: bool = False,
) -> FundamentalStatus:
    if score >= 12:
        status = FundamentalStatus.STRONG
    elif score >= 5:
        status = FundamentalStatus.OK
    elif score > -5:
        status = FundamentalStatus.CAUTION
    else:
        status = FundamentalStatus.WEAK
    if expensive_pe and status in {FundamentalStatus.STRONG, FundamentalStatus.OK}:
        return FundamentalStatus.CAUTION
    return status


def _format_market_cap(value: float) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    return f"{int(value):,}"


def _format_dividend_yield(value: float) -> str:
    if 0 < value <= 1:
        return f"{value * 100:.1f}%"
    return f"{value:.1f}%"


def evaluate_fundamental_quality(
    row: Mapping[str, object] | pd.Series,
    config: FundamentalQualityConfig | None = None,
) -> FundamentalQualityResult:
    """Score fundamental quality for one symbol snapshot row."""
    cfg = config or FundamentalQualityConfig()
    market_cap, pe_ratio, pb_ratio, dividend_yield = normalize_fundamental_values(row)

    key_fields = (market_cap, pe_ratio, pb_ratio)
    missing_count = sum(value is None for value in key_fields)
    if missing_count >= 2:
        return FundamentalQualityResult(
            fundamental_score=0,
            status=FundamentalStatus.UNKNOWN,
            notes=[FUNDAMENTAL_UNAVAILABLE_NOTE],
            market_cap=market_cap,
            pe_ratio=pe_ratio,
            pb_ratio=pb_ratio,
            dividend_yield=dividend_yield,
        )

    score = 0
    notes: list[str] = []
    expensive_pe = False

    if market_cap is not None and market_cap > cfg.good_market_cap:
        score += 5
        notes.append("Market cap good")
    elif market_cap is None or market_cap <= 0:
        score -= 3
        notes.append("Market cap unknown/small")
    else:
        score -= 2
        notes.append("Market cap small")

    if pe_ratio is not None:
        if 0 < pe_ratio <= cfg.good_pe_max:
            score += 5
            notes.append(f"P/E {pe_ratio:.1f} sane")
        elif pe_ratio > cfg.good_pe_max:
            score -= 5
            expensive_pe = True
            notes.append(f"P/E {pe_ratio:.1f} expensive")
        else:
            score -= 3
            notes.append("P/E invalid")
    else:
        notes.append("P/E unknown")

    if pb_ratio is not None:
        if 0 < pb_ratio <= cfg.good_pb_max:
            score += 5
            notes.append(f"P/B {pb_ratio:.1f} sane")
        elif pb_ratio > cfg.good_pb_max:
            score -= 3
            notes.append(f"P/B {pb_ratio:.1f} expensive")
        else:
            score -= 2
            notes.append("P/B invalid")
    else:
        notes.append("P/B unknown")

    if dividend_yield is not None and dividend_yield > 0:
        score += 3
        notes.append(f"Dividend {_format_dividend_yield(dividend_yield)}")

    clamped_score = max(-20, min(20, score))
    return FundamentalQualityResult(
        fundamental_score=clamped_score,
        status=_status_from_score(clamped_score, expensive_pe=expensive_pe),
        notes=notes,
        market_cap=market_cap,
        pe_ratio=pe_ratio,
        pb_ratio=pb_ratio,
        dividend_yield=dividend_yield,
    )


def format_fundamental_quality_line(
    result: FundamentalQualityResult,
) -> str:
    """Build a compact fundamental quality line for report output."""
    if result.status == FundamentalStatus.UNKNOWN:
        return f"Fundamentals: UNKNOWN | {FUNDAMENTAL_UNAVAILABLE_NOTE}"

    parts = [
        f"Fundamentals: {result.status.value} ({result.fundamental_score:+d})",
    ]
    if result.market_cap is not None and result.market_cap > 0:
        parts.append(f"MCap {_format_market_cap(result.market_cap)}")
    if result.pe_ratio is not None and result.pe_ratio > 0:
        parts.append(f"P/E {result.pe_ratio:.1f}")
    if result.pb_ratio is not None and result.pb_ratio > 0:
        parts.append(f"P/B {result.pb_ratio:.1f}")
    if result.dividend_yield is not None and result.dividend_yield > 0:
        parts.append(f"Div {_format_dividend_yield(result.dividend_yield)}")
    return " | ".join(parts)


def format_candidate_fundamental_line(
    candidate_symbol: str,
    snapshot_df: pd.DataFrame | None,
    config: FundamentalQualityConfig | None = None,
) -> str:
    """Build a compact fundamental line for one candidate."""
    from core.technical_confirmation import row_for_symbol

    row = row_for_symbol(snapshot_df, candidate_symbol)
    result = evaluate_fundamental_quality(row, config)
    return format_fundamental_quality_line(result)


def passes_fundamental_filters(
    row: Mapping[str, object] | pd.Series,
    *,
    min_market_cap_quality: float | None = None,
    max_pe: float | None = None,
    max_pb: float | None = None,
    require_fundamentals: bool = False,
    config: FundamentalQualityConfig | None = None,
) -> bool:
    """Return True when a snapshot row passes optional fundamental filters."""
    result = evaluate_fundamental_quality(row, config)
    if require_fundamentals and result.status == FundamentalStatus.UNKNOWN:
        return False
    market_cap, pe_ratio, pb_ratio, _ = normalize_fundamental_values(row)
    if min_market_cap_quality is not None:
        if market_cap is None or market_cap < min_market_cap_quality:
            return False
    if max_pe is not None:
        if pe_ratio is None or pe_ratio <= 0 or pe_ratio > max_pe:
            return False
    if max_pb is not None:
        if pb_ratio is None or pb_ratio <= 0 or pb_ratio > max_pb:
            return False
    return True
