"""Market quality filters for full-market EGX live snapshot scanning."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot

DEFAULT_MIN_PRICE = 1.0
DEFAULT_MIN_VOLUME = 50_000


@dataclass(frozen=True)
class MarketQualityFilters:
    """Quality thresholds applied before full-market scanner scoring."""

    min_price: float = DEFAULT_MIN_PRICE
    min_volume: int = DEFAULT_MIN_VOLUME
    min_market_cap: float | None = None
    exclude_zero_volume: bool = True
    include_illiquid: bool = False


@dataclass(frozen=True)
class MarketQualityFilterResult:
    """Outcome of applying market quality filters to a snapshot dataframe."""

    filtered_df: pd.DataFrame
    original_count: int
    filtered_count: int
    removed_low_price: int
    removed_low_volume: int
    removed_low_market_cap: int
    removed_zero_volume: int
    filters: MarketQualityFilters


def build_market_quality_filters_from_cli(
    *,
    min_price: float | None = None,
    min_volume: int | None = None,
    min_market_cap: float | None = None,
    exclude_zero_volume: bool = True,
    include_illiquid: bool = False,
) -> MarketQualityFilters:
    """Build market quality filters from optional CLI values."""
    return MarketQualityFilters(
        min_price=min_price if min_price is not None else DEFAULT_MIN_PRICE,
        min_volume=min_volume if min_volume is not None else DEFAULT_MIN_VOLUME,
        min_market_cap=min_market_cap,
        exclude_zero_volume=exclude_zero_volume,
        include_illiquid=include_illiquid,
    )


def build_quality_filter_dataframe(
    live_snapshot: LiveMarketSnapshot,
    snapshot_path: Path | None = None,
) -> pd.DataFrame:
    """Build a filterable dataframe from a live snapshot and optional CSV extras."""
    rows = [
        {
            "symbol": snap.symbol,
            "close": snap.close,
            "volume": snap.volume,
        }
        for snap in live_snapshot.symbols.values()
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame

    if snapshot_path is not None and snapshot_path.exists():
        try:
            csv_frame = pd.read_csv(snapshot_path)
        except Exception:  # noqa: BLE001
            return frame
        if "market_cap" in csv_frame.columns and "symbol" in csv_frame.columns:
            extras = csv_frame[["symbol", "market_cap"]].drop_duplicates(subset=["symbol"])
            frame = frame.merge(extras, on="symbol", how="left")

    return frame


def apply_market_quality_filters(
    frame: pd.DataFrame,
    filters: MarketQualityFilters,
) -> MarketQualityFilterResult:
    """Filter snapshot rows by tradability thresholds."""
    if frame.empty:
        return MarketQualityFilterResult(
            filtered_df=frame.copy(),
            original_count=0,
            filtered_count=0,
            removed_low_price=0,
            removed_low_volume=0,
            removed_low_market_cap=0,
            removed_zero_volume=0,
            filters=filters,
        )

    working = frame.copy()
    original_count = len(working)
    keep_mask = pd.Series(True, index=working.index)
    removed_zero_volume = 0
    removed_low_price = 0
    removed_low_volume = 0
    removed_low_market_cap = 0

    close_values = pd.to_numeric(working["close"], errors="coerce")
    volume_values = pd.to_numeric(working["volume"], errors="coerce")
    has_market_cap = "market_cap" in working.columns
    market_cap_values = (
        pd.to_numeric(working["market_cap"], errors="coerce")
        if has_market_cap
        else None
    )

    for index in working.index:
        close = close_values.loc[index]
        volume = volume_values.loc[index]
        if pd.isna(close) or pd.isna(volume):
            keep_mask.loc[index] = False
            removed_low_price += 1
            continue

        if (
            not filters.include_illiquid
            and filters.exclude_zero_volume
            and float(volume) <= 0
        ):
            keep_mask.loc[index] = False
            removed_zero_volume += 1
            continue

        if float(close) < filters.min_price:
            keep_mask.loc[index] = False
            removed_low_price += 1
            continue

        if not filters.include_illiquid and float(volume) < filters.min_volume:
            keep_mask.loc[index] = False
            removed_low_volume += 1
            continue

        if filters.min_market_cap is not None and has_market_cap:
            market_cap = market_cap_values.loc[index]
            if pd.isna(market_cap) or float(market_cap) < filters.min_market_cap:
                keep_mask.loc[index] = False
                removed_low_market_cap += 1
            continue

    filtered_df = working.loc[keep_mask].reset_index(drop=True)
    return MarketQualityFilterResult(
        filtered_df=filtered_df,
        original_count=original_count,
        filtered_count=len(filtered_df),
        removed_low_price=removed_low_price,
        removed_low_volume=removed_low_volume,
        removed_low_market_cap=removed_low_market_cap,
        removed_zero_volume=removed_zero_volume,
        filters=filters,
    )


def allowed_symbols_from_quality_result(
    result: MarketQualityFilterResult,
) -> set[str]:
    """Return symbols that passed market quality filters."""
    if result.filtered_df.empty or "symbol" not in result.filtered_df.columns:
        return set()
    return {
        str(symbol).strip()
        for symbol in result.filtered_df["symbol"].tolist()
        if str(symbol).strip()
    }


def quality_filtered_symbol_snapshots(
    live_snapshot: LiveMarketSnapshot,
    quality_filter_result: MarketQualityFilterResult | None,
) -> list[LiveSymbolSnapshot]:
    """Return live symbol rows limited to the quality-filtered universe."""
    if quality_filter_result is None:
        return list(live_snapshot.symbols.values())
    allowed_symbols = allowed_symbols_from_quality_result(quality_filter_result)
    return [
        snap
        for symbol, snap in live_snapshot.symbols.items()
        if symbol in allowed_symbols
    ]


def build_market_quality_filter_summary_lines(
    result: MarketQualityFilterResult,
) -> list[str]:
    """Build report summary lines describing market quality filter results."""
    filters = result.filters
    min_market_cap = (
        "none" if filters.min_market_cap is None else f"{filters.min_market_cap:,.0f}"
    )
    return [
        f"- Min price: {filters.min_price:.2f}",
        f"- Min volume: {filters.min_volume:,}",
        f"- Min market cap: {min_market_cap}",
        f"- Exclude zero volume: {'yes' if filters.exclude_zero_volume else 'no'}",
        f"- Include illiquid: {'yes' if filters.include_illiquid else 'no'}",
        f"- Symbols before quality filter: {result.original_count}",
        f"- Symbols after quality filter: {result.filtered_count}",
        f"- Removed by low price: {result.removed_low_price}",
        f"- Removed by low volume: {result.removed_low_volume}",
        f"- Removed by market cap: {result.removed_low_market_cap}",
        f"- Removed by zero volume: {result.removed_zero_volume}",
    ]
