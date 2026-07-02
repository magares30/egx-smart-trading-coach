"""Sector momentum analysis from TradingView snapshot sectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from statistics import median

import pandas as pd

from core.relative_volume import resolve_volume_ratio
from core.scanner import ScannerResult

UNKNOWN_SECTOR = "Unknown"
DEFAULT_TOP_SECTORS = 5
DEFAULT_TOP_SYMBOLS_PER_SECTOR = 3
FLAT_CHANGE_THRESHOLD = 0.05


class SectorStatus(str, Enum):
    HOT = "HOT"
    WARM = "WARM"
    NEUTRAL = "NEUTRAL"
    WEAK = "WEAK"


@dataclass(frozen=True)
class SectorMomentumConfig:
    """Thresholds for sector momentum scoring and report limits."""

    hot_threshold: int = 75
    warm_threshold: int = 60
    neutral_threshold: int = 45
    top_sectors_limit: int = DEFAULT_TOP_SECTORS
    top_symbols_per_sector: int = DEFAULT_TOP_SYMBOLS_PER_SECTOR


@dataclass(frozen=True)
class SectorTopSymbol:
    symbol: str
    change_percent: float


@dataclass(frozen=True)
class SectorMomentumRow:
    sector: str
    symbols_count: int
    advancers_count: int
    decliners_count: int
    flat_count: int
    avg_change_percent: float
    median_change_percent: float
    total_volume: float
    avg_relative_volume: float
    candidates_count: int
    top_symbols: list[SectorTopSymbol] = field(default_factory=list)
    sector_score: int = 0
    status: SectorStatus = SectorStatus.NEUTRAL

    def to_dict(self) -> dict[str, object]:
        """Serialize sector momentum row for JSON report output."""
        return {
            "sector": self.sector,
            "status": self.status.value,
            "sector_score": self.sector_score,
            "symbols_count": self.symbols_count,
            "advancers_count": self.advancers_count,
            "decliners_count": self.decliners_count,
            "flat_count": self.flat_count,
            "avg_change_percent": self.avg_change_percent,
            "median_change_percent": self.median_change_percent,
            "total_volume": self.total_volume,
            "avg_relative_volume": self.avg_relative_volume,
            "candidates_count": self.candidates_count,
            "top_symbols": [
                {
                    "symbol": item.symbol,
                    "change_percent": item.change_percent,
                }
                for item in self.top_symbols
            ],
        }


@dataclass(frozen=True)
class SectorMomentumResult:
    sectors: list[SectorMomentumRow]
    symbol_status_by_symbol: dict[str, str] = field(default_factory=dict)

    def to_dict_list(self) -> list[dict[str, object]]:
        return [row.to_dict() for row in self.sectors]


def _safe_float(value: object, default: float = 0.0) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:
        return default
    return parsed


def _normalize_sector(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return UNKNOWN_SECTOR
    sector = str(value).strip()
    return sector or UNKNOWN_SECTOR


def _classify_change(change_percent: float) -> str:
    if change_percent > FLAT_CHANGE_THRESHOLD:
        return "advancer"
    if change_percent < -FLAT_CHANGE_THRESHOLD:
        return "decliner"
    return "flat"


def _sector_status(score: int, config: SectorMomentumConfig) -> SectorStatus:
    if score >= config.hot_threshold:
        return SectorStatus.HOT
    if score >= config.warm_threshold:
        return SectorStatus.WARM
    if score >= config.neutral_threshold:
        return SectorStatus.NEUTRAL
    return SectorStatus.WEAK


def _compute_sector_score(
    *,
    avg_change_percent: float,
    advancers_count: int,
    symbols_count: int,
    avg_relative_volume: float,
    candidates_count: int,
) -> int:
    score = 50

    if avg_change_percent > 0:
        score += min(20, int(avg_change_percent * 8))
    elif avg_change_percent < 0:
        score -= min(20, int(abs(avg_change_percent) * 8))

    if symbols_count > 0:
        advancers_ratio = advancers_count / symbols_count
        if advancers_ratio >= 0.7:
            score += 15
        elif advancers_ratio >= 0.5:
            score += 8
        elif advancers_ratio < 0.3:
            score -= 10

    if avg_relative_volume >= 1.5:
        score += 10
    elif avg_relative_volume >= 1.2:
        score += 5

    score += min(10, candidates_count * 2)
    return max(0, min(100, score))


def _row_relative_volume(row: dict[str, object]) -> float:
    resolved = resolve_volume_ratio(0.0, row)
    if resolved is not None:
        return resolved
    return 1.0


def build_sector_momentum(
    snapshot_df: pd.DataFrame | None,
    candidates: list[ScannerResult] | None = None,
    config: SectorMomentumConfig | None = None,
) -> SectorMomentumResult:
    """Build ranked sector momentum rows from a snapshot dataframe."""
    cfg = config or SectorMomentumConfig()
    if snapshot_df is None or snapshot_df.empty:
        return SectorMomentumResult(sectors=[], symbol_status_by_symbol={})

    candidate_symbols = {item.symbol for item in (candidates or [])}
    working = snapshot_df.copy()
    if "symbol" not in working.columns:
        return SectorMomentumResult(sectors=[], symbol_status_by_symbol={})

    working["symbol"] = working["symbol"].astype(str).str.strip()
    working = working[working["symbol"] != ""]
    if working.empty:
        return SectorMomentumResult(sectors=[], symbol_status_by_symbol={})

    if "sector" not in working.columns:
        working["sector"] = UNKNOWN_SECTOR
    working["sector"] = working["sector"].map(_normalize_sector)

    if "change_percent" not in working.columns:
        working["change_percent"] = 0.0
    working["change_percent"] = working["change_percent"].map(
        lambda value: _safe_float(value, 0.0)
    )

    if "volume" not in working.columns:
        working["volume"] = 0.0
    working["volume"] = working["volume"].map(lambda value: _safe_float(value, 0.0))

    sector_rows: list[SectorMomentumRow] = []
    symbol_status_by_symbol: dict[str, str] = {}

    grouped = working.groupby("sector", sort=False)
    for sector_name, group in grouped:
        changes = [float(value) for value in group["change_percent"].tolist()]
        advancers_count = sum(
            1 for change in changes if _classify_change(change) == "advancer"
        )
        decliners_count = sum(
            1 for change in changes if _classify_change(change) == "decliner"
        )
        flat_count = len(changes) - advancers_count - decliners_count
        symbols_count = len(group)
        avg_change_percent = sum(changes) / symbols_count if symbols_count else 0.0
        median_change_percent = float(median(changes)) if changes else 0.0
        total_volume = float(group["volume"].sum())
        relative_volumes = [
            _row_relative_volume(row)
            for row in group.to_dict(orient="records")
        ]
        avg_relative_volume = (
            sum(relative_volumes) / len(relative_volumes) if relative_volumes else 1.0
        )
        sector_candidates = [
            symbol
            for symbol in group["symbol"].tolist()
            if symbol in candidate_symbols
        ]
        top_symbol_rows = sorted(
            group[["symbol", "change_percent"]].to_dict(orient="records"),
            key=lambda row: _safe_float(row.get("change_percent"), 0.0),
            reverse=True,
        )[: cfg.top_symbols_per_sector]
        top_symbols = [
            SectorTopSymbol(
                symbol=str(row["symbol"]),
                change_percent=_safe_float(row.get("change_percent"), 0.0),
            )
            for row in top_symbol_rows
        ]
        sector_score = _compute_sector_score(
            avg_change_percent=avg_change_percent,
            advancers_count=advancers_count,
            symbols_count=symbols_count,
            avg_relative_volume=avg_relative_volume,
            candidates_count=len(sector_candidates),
        )
        status = _sector_status(sector_score, cfg)
        sector_row = SectorMomentumRow(
            sector=str(sector_name),
            symbols_count=symbols_count,
            advancers_count=advancers_count,
            decliners_count=decliners_count,
            flat_count=flat_count,
            avg_change_percent=avg_change_percent,
            median_change_percent=median_change_percent,
            total_volume=total_volume,
            avg_relative_volume=avg_relative_volume,
            candidates_count=len(sector_candidates),
            top_symbols=top_symbols,
            sector_score=sector_score,
            status=status,
        )
        sector_rows.append(sector_row)
        for symbol in group["symbol"].tolist():
            symbol_status_by_symbol[str(symbol)] = status.value

    sector_rows.sort(
        key=lambda row: (-row.sector_score, row.sector),
    )
    return SectorMomentumResult(
        sectors=sector_rows[: cfg.top_sectors_limit],
        symbol_status_by_symbol=symbol_status_by_symbol,
    )


def format_sector_momentum_lines(result: SectorMomentumResult) -> list[str]:
    """Build compact report lines for the Sector Momentum section."""
    if not result.sectors:
        return ["- (none)"]

    lines: list[str] = []
    for index, row in enumerate(result.sectors, start=1):
        lines.append(
            (
                f"{index}. {row.sector} | {row.status.value} | Score {row.sector_score} | "
                f"Avg {row.avg_change_percent:+.1f}% | "
                f"Adv {row.advancers_count}/{row.symbols_count} | "
                f"RelVol {row.avg_relative_volume:.1f}x | "
                f"Candidates {row.candidates_count}"
            )
        )
        if row.top_symbols:
            top_text = ", ".join(
                f"{item.symbol} {item.change_percent:+.2f}%"
                for item in row.top_symbols
            )
            lines.append(f"   Top: {top_text}")
    return lines


def sector_status_for_symbol(
    symbol: str,
    result: SectorMomentumResult | None,
) -> str | None:
    """Return sector status label for a symbol when sector data exists."""
    if result is None:
        return None
    return result.symbol_status_by_symbol.get(symbol)
