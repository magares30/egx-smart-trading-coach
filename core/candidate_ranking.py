"""Candidate tie-breaker ranking after scanner scoring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from core.live_snapshot import LiveMarketSnapshot
from core.relative_volume import (
    RelativeVolumeConfig,
    classify_relative_volume,
    resolve_volume_ratio,
)
from core.scanner import ScannerResult
from core.strategy import StrategyResult
from core.technical_confirmation import (
    TECHNICAL_SNAPSHOT_COLUMNS,
    TechnicalConfirmationConfig,
    evaluate_technical_confirmation,
    format_technical_confirmation_line,
    row_for_symbol,
    technical_fields_available_in_dataframe,
)
from core.fundamental_quality import (
    FUNDAMENTAL_SNAPSHOT_COLUMNS,
    fundamental_fields_available_in_dataframe,
)

DEFAULT_MAX_RANK_CHANGE = 12.0
DEFAULT_PREFER_CHANGE_MIN = 0.5
DEFAULT_PREFER_CHANGE_MAX = 7.0

_MISSING_RISK_DISTANCE = float("inf")


@dataclass(frozen=True)
class CandidateRankingConfig:
    """Tie-breaker thresholds for ordering candidates with the same score."""

    max_rank_change: float = DEFAULT_MAX_RANK_CHANGE
    prefer_change_min: float = DEFAULT_PREFER_CHANGE_MIN
    prefer_change_max: float = DEFAULT_PREFER_CHANGE_MAX


def build_candidate_ranking_config_from_cli(
    *,
    max_rank_change: float | None = None,
    prefer_change_min: float | None = None,
    prefer_change_max: float | None = None,
) -> CandidateRankingConfig:
    """Build ranking config from optional CLI values."""
    return CandidateRankingConfig(
        max_rank_change=(
            max_rank_change
            if max_rank_change is not None
            else DEFAULT_MAX_RANK_CHANGE
        ),
        prefer_change_min=(
            prefer_change_min
            if prefer_change_min is not None
            else DEFAULT_PREFER_CHANGE_MIN
        ),
        prefer_change_max=(
            prefer_change_max
            if prefer_change_max is not None
            else DEFAULT_PREFER_CHANGE_MAX
        ),
    )


def extreme_change_penalty(
    change_percent: float,
    config: CandidateRankingConfig,
) -> int:
    """Return a soft penalty tier for daily change magnitude."""
    if change_percent <= 0:
        return 1
    if config.prefer_change_min <= change_percent <= config.prefer_change_max:
        return 0
    if change_percent > config.max_rank_change:
        return 2
    if change_percent > config.prefer_change_max:
        return 1
    return 1


def change_quality_label(
    change_percent: float,
    config: CandidateRankingConfig,
) -> str:
    """Compact label for report ranking notes."""
    return "clean" if extreme_change_penalty(change_percent, config) == 0 else "extreme"


def build_candidate_ranking_dataframe(
    live_snapshot: LiveMarketSnapshot,
    snapshot_path: Path | None = None,
) -> pd.DataFrame:
    """Build a ranking lookup dataframe from live snapshot and optional CSV extras."""
    rows = [
        {
            "symbol": snap.symbol,
            "volume": snap.volume,
            "volume_ratio": snap.volume_ratio,
            "change_percent": snap.change_percent,
            "close": snap.close,
        }
        for snap in live_snapshot.symbols.values()
    ]
    frame = pd.DataFrame(rows)
    if frame.empty or snapshot_path is None or not snapshot_path.exists():
        return frame

    try:
        csv_frame = pd.read_csv(snapshot_path)
    except Exception:  # noqa: BLE001
        return frame

    if "symbol" not in csv_frame.columns:
        return frame

    extra_columns = ["symbol"]
    for column in (
        "market_cap",
        "sector",
        "pe_ratio",
        "pb_ratio",
        "dividend_yield",
        "volume",
        "volume_ratio",
        "tv_relative_volume_10d",
        *TECHNICAL_SNAPSHOT_COLUMNS,
        *FUNDAMENTAL_SNAPSHOT_COLUMNS,
    ):
        if column in csv_frame.columns and column not in extra_columns:
            extra_columns.append(column)

    extras = csv_frame[extra_columns].drop_duplicates(subset=["symbol"])
    if len(extra_columns) == 1:
        return frame

    merged = frame.merge(extras, on="symbol", how="left", suffixes=("", "_csv"))
    if "market_cap" in merged.columns:
        pass
    elif "market_cap_csv" in merged.columns:
        merged["market_cap"] = merged["market_cap_csv"]

    for column in ("volume", "volume_ratio", "tv_relative_volume_10d"):
        csv_column = f"{column}_csv"
        if csv_column in merged.columns:
            merged[column] = merged[column].fillna(merged[csv_column])
            merged = merged.drop(columns=[csv_column])

    if "tv_relative_volume_10d" in merged.columns:
        tv_relative = pd.to_numeric(merged["tv_relative_volume_10d"], errors="coerce")
        merged["volume_ratio"] = tv_relative.where(tv_relative > 0, merged["volume_ratio"])

    return merged


def build_candidate_ranking_summary_lines(
    config: CandidateRankingConfig,
    *,
    technical_config: TechnicalConfirmationConfig | None = None,
    technical_fields_available: bool = False,
    fundamental_fields_available: bool = False,
) -> list[str]:
    """Build report summary lines describing candidate tie-breaker ranking."""
    lines = [
        "",
        "Candidate Ranking:",
        (
            "- Tie-breakers: score, technical score, change quality, relative volume "
            "score, volume, relative volume, market cap"
        ),
        (
            "- Preferred change range: "
            f"{config.prefer_change_min:.2f}% to {config.prefer_change_max:.2f}%"
        ),
        f"- Extreme change threshold: {config.max_rank_change:.2f}%",
        "- Relative volume: TradingView 10-day relative volume when available",
        "- Fundamentals: shown as company quality context, optional filter only",
        "- Multi-timeframe: entry timing context only, not a ranking input by default",
    ]
    technical_values = technical_config or TechnicalConfirmationConfig()
    if technical_values.enabled:
        if technical_fields_available:
            lines.append(
                "- Technical confirmation: enabled when TradingView technical fields are available"
            )
        else:
            lines.append(
                "- Technical confirmation: enabled (technical fields unavailable in snapshot)"
            )
    if fundamental_fields_available:
        lines.append(
            "- Fundamental fields: available in snapshot for company quality context"
        )
    return lines


def _safe_float(value: object, default: float = 0.0) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _snapshot_lookup(
    snapshot_df: pd.DataFrame | None,
) -> dict[str, dict[str, float]]:
    if snapshot_df is None or snapshot_df.empty or "symbol" not in snapshot_df.columns:
        return {}

    lookup: dict[str, dict[str, float]] = {}
    for row in snapshot_df.to_dict(orient="records"):
        symbol = str(row.get("symbol", "")).strip()
        if not symbol:
            continue
        lookup[symbol] = {
            "volume": _safe_float(row.get("volume"), 0.0),
            "volume_ratio": _safe_float(row.get("volume_ratio"), 1.0),
            "tv_relative_volume_10d": _safe_float(
                row.get("tv_relative_volume_10d"),
                0.0,
            ),
            "market_cap": _safe_float(row.get("market_cap"), 0.0),
            "change_percent": _safe_float(row.get("change_percent"), 0.0),
        }
    return lookup


def _candidate_volume(
    candidate: ScannerResult,
    lookup: dict[str, dict[str, float]],
) -> float:
    row = lookup.get(candidate.symbol)
    if row is not None and row["volume"] > 0:
        return row["volume"]
    return 0.0


def _lookup_row(
    symbol: str,
    lookup: dict[str, dict[str, float]],
) -> dict[str, float] | None:
    return lookup.get(symbol)


def _candidate_volume_ratio(
    candidate: ScannerResult,
    lookup: dict[str, dict[str, float]],
) -> float:
    row = _lookup_row(candidate.symbol, lookup)
    resolved = resolve_volume_ratio(
        candidate.volume_ratio,
        row,
    )
    if resolved is not None:
        return resolved
    return 1.0


def _relative_volume_score_for_candidate(
    candidate: ScannerResult,
    lookup: dict[str, dict[str, float]],
    relative_volume_config: RelativeVolumeConfig | None,
) -> int:
    ratio = _candidate_volume_ratio(candidate, lookup)
    result = classify_relative_volume(ratio, relative_volume_config)
    return result.score_bonus


def _candidate_market_cap(
    candidate: ScannerResult,
    lookup: dict[str, dict[str, float]],
) -> float:
    row = lookup.get(candidate.symbol)
    if row is None:
        return 0.0
    return row["market_cap"]


def _candidate_change_percent(
    candidate: ScannerResult,
    lookup: dict[str, dict[str, float]],
) -> float:
    if candidate.change_percent != 0:
        return candidate.change_percent
    row = lookup.get(candidate.symbol)
    if row is None:
        return 0.0
    return row["change_percent"]


def _risk_distance(strategy: StrategyResult | None) -> float:
    if strategy is None:
        return _MISSING_RISK_DISTANCE
    entry = strategy.entry_price
    stop = strategy.stop_loss
    if entry is None or stop is None or entry <= 0:
        return _MISSING_RISK_DISTANCE
    return abs(entry - stop) / entry


def _technical_score_for_symbol(
    symbol: str,
    snapshot_df: pd.DataFrame | None,
    technical_config: TechnicalConfirmationConfig | None,
) -> int:
    if technical_config is None or not technical_config.enabled:
        return 0
    row = row_for_symbol(snapshot_df, symbol)
    return evaluate_technical_confirmation(row, technical_config).technical_score


def _ranking_sort_key(
    candidate: ScannerResult,
    *,
    lookup: dict[str, dict[str, float]],
    config: CandidateRankingConfig,
    strategy_by_symbol: dict[str, StrategyResult] | None,
    snapshot_df: pd.DataFrame | None,
    technical_config: TechnicalConfirmationConfig | None,
    relative_volume_config: RelativeVolumeConfig | None = None,
) -> tuple[float, int, int, int, float, float, float, float, str]:
    change = _candidate_change_percent(candidate, lookup)
    strategy = (strategy_by_symbol or {}).get(candidate.symbol)
    technical_score = _technical_score_for_symbol(
        candidate.symbol,
        snapshot_df,
        technical_config,
    )
    relative_volume_score = _relative_volume_score_for_candidate(
        candidate,
        lookup,
        relative_volume_config,
    )
    return (
        -float(candidate.score),
        -technical_score,
        extreme_change_penalty(change, config),
        -relative_volume_score,
        -_candidate_volume(candidate, lookup),
        -_candidate_volume_ratio(candidate, lookup),
        -_candidate_market_cap(candidate, lookup),
        _risk_distance(strategy),
        candidate.symbol,
    )


def rank_candidates(
    candidates: list[ScannerResult],
    snapshot_df: pd.DataFrame | None,
    config: CandidateRankingConfig,
    *,
    strategy_by_symbol: dict[str, StrategyResult] | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
    relative_volume_config: RelativeVolumeConfig | None = None,
) -> list[ScannerResult]:
    """Rank scanner candidates using score-first tie-breakers."""
    if not candidates:
        return []

    lookup = _snapshot_lookup(snapshot_df)
    return sorted(
        candidates,
        key=lambda candidate: _ranking_sort_key(
            candidate,
            lookup=lookup,
            config=config,
            strategy_by_symbol=strategy_by_symbol,
            snapshot_df=snapshot_df,
            technical_config=technical_config,
            relative_volume_config=relative_volume_config,
        ),
    )


def format_candidate_technical_line(
    candidate: ScannerResult,
    snapshot_df: pd.DataFrame | None,
    technical_config: TechnicalConfirmationConfig | None,
) -> str | None:
    """Build a compact technical confirmation line for one candidate."""
    if technical_config is None or not technical_config.enabled:
        return None
    row = row_for_symbol(snapshot_df, candidate.symbol)
    result = evaluate_technical_confirmation(row, technical_config)
    return format_technical_confirmation_line(result, row)


def display_volume_ratio_for_candidate(
    candidate: ScannerResult,
    snapshot_df: pd.DataFrame | None,
) -> float:
    """Return the resolved relative volume used for display and filtering."""
    lookup = _snapshot_lookup(snapshot_df)
    return _candidate_volume_ratio(candidate, lookup)


def format_candidate_ranking_note(
    candidate: ScannerResult,
    snapshot_df: pd.DataFrame | None,
    config: CandidateRankingConfig,
    relative_volume_config: RelativeVolumeConfig | None = None,
    *,
    sector_status: str | None = None,
) -> str:
    """Build a compact ranking note for report output."""
    lookup = _snapshot_lookup(snapshot_df)
    volume = int(_candidate_volume(candidate, lookup))
    relative_result = classify_relative_volume(
        _candidate_volume_ratio(candidate, lookup),
        relative_volume_config,
    )
    quality = change_quality_label(
        _candidate_change_percent(candidate, lookup),
        config,
    )
    market_cap = _candidate_market_cap(candidate, lookup)
    market_cap_label = "available" if market_cap > 0 else "missing"
    note_parts = [
        f"volume {volume:,}",
        relative_result.note,
    ]
    if sector_status:
        note_parts.append(f"sector {sector_status}")
    note_parts.extend(
        [
            f"change quality {quality}",
            f"market cap {market_cap_label}",
        ]
    )
    return f"Rank factors: {', '.join(note_parts)}"


def order_strategy_signals_by_rank(
    items: list[StrategyResult],
    ranked_candidates: list[ScannerResult],
) -> list[StrategyResult]:
    """Order strategy signals to match ranked candidate priority."""
    rank_order = {candidate.symbol: index for index, candidate in enumerate(ranked_candidates)}
    fallback_rank = len(rank_order)
    return sorted(
        items,
        key=lambda item: (rank_order.get(item.symbol, fallback_rank), item.symbol),
    )
