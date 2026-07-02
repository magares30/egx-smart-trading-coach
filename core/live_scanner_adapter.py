"""Adapt single-day EGX live snapshots for Scanner A and Strategy Scanner B."""

from __future__ import annotations

from pathlib import Path

from config.watchlist import DEFAULT_WATCHLIST, MARKET_INDEX_SYMBOLS
from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from core.live_volume import LiveVolumeHistoryStore
from core.market_breadth_mood import (
    BREADTH_MOOD_INFO_WARNING,
    MarketBreadthMoodResult,
    build_breadth_snapshot_dataframe,
    calculate_market_breadth_mood,
)
from core.market_data import MarketSnapshot, SymbolSnapshot
from core.market_data_providers import DATA_PROVIDER_TRADINGVIEW
from core.market_mood import MarketMood, MarketMoodDetector, MarketMoodResult
from core.market_quality_filters import MarketQualityFilterResult
from core.scanner_universe import (
    DEFAULT_SCANNER_UNIVERSE,
    is_full_market_universe,
)

MISSING_INDEX_MOOD_WARNING = (
    "Live snapshot has no EGX30/EGX70 rows; market mood set to NEUTRAL."
)
SMA5_HISTORY_WARNING = "Not enough live history to compute SMA5; using current close only"


def _compute_above_sma_5(
    live: LiveSymbolSnapshot,
    history_store: LiveVolumeHistoryStore | None,
) -> tuple[bool, str | None]:
    """Compute SMA5 alignment from stored live history closes."""
    if history_store is None:
        return True, None

    closes = history_store.load_previous_closes(live.symbol, live.date, count=5)
    if len(closes) < 5:
        if len(closes) == 0:
            return True, SMA5_HISTORY_WARNING
        sma_5 = sum(closes) / len(closes)
        return live.close > sma_5, SMA5_HISTORY_WARNING

    sma_5 = sum(closes) / 5
    return live.close > sma_5, None


def live_symbol_to_symbol_snapshot(
    live: LiveSymbolSnapshot,
    history_store: LiveVolumeHistoryStore | None = None,
) -> tuple[SymbolSnapshot, str | None]:
    """Convert one live row into a scanner-compatible symbol snapshot."""
    change = live.close - live.previous_close
    if live.volume_ratio > 0:
        average_volume_5d = live.volume / live.volume_ratio
    else:
        average_volume_5d = float(live.volume)
    above_sma_5, sma_warning = _compute_above_sma_5(live, history_store)
    return SymbolSnapshot(
        symbol=live.symbol,
        latest_close=live.close,
        previous_close=live.previous_close,
        change=change,
        change_percent=live.change_percent,
        latest_volume=int(live.volume),
        average_volume_5d=average_volume_5d,
        volume_ratio=live.volume_ratio,
        day_high=live.high,
        day_low=live.low,
        broke_previous_high=live.broke_previous_high,
        above_sma_5=above_sma_5,
        above_sma_20=None,
        insufficient_volume_history=live.insufficient_volume_history,
    ), sma_warning


def build_live_market_snapshot(
    live_snapshot: LiveMarketSnapshot,
    watchlist: list[str] | None = None,
    index_symbols: list[str] | None = None,
    volume_history_store: LiveVolumeHistoryStore | None = None,
    scanner_universe: str = DEFAULT_SCANNER_UNIVERSE,
    scan_symbols: list[str] | None = None,
    *,
    data_provider: str | None = None,
    quality_filter_result: MarketQualityFilterResult | None = None,
    snapshot_path: Path | None = None,
    compute_market_mood: bool = True,
) -> tuple[MarketSnapshot, MarketMoodResult, list[str], MarketBreadthMoodResult | None]:
    """Build scanner inputs from a live snapshot."""
    watchlist = watchlist or DEFAULT_WATCHLIST
    index_symbols = index_symbols or MARKET_INDEX_SYMBOLS
    warnings: list[str] = []

    if is_full_market_universe(scanner_universe):
        scan_symbols = scan_symbols or sorted(live_snapshot.symbols)
    else:
        scan_symbols = list(watchlist)

    symbol_snapshots: list[SymbolSnapshot] = []
    for symbol in scan_symbols:
        live_row = live_snapshot.symbols.get(symbol)
        if live_row is None:
            if not is_full_market_universe(scanner_universe):
                warnings.append(f"Watchlist symbol {symbol} missing from live snapshot")
            continue
        snapshot, sma_warning = live_symbol_to_symbol_snapshot(
            live_row,
            volume_history_store,
        )
        if sma_warning:
            warnings.append(f"{symbol}: {sma_warning}")
        symbol_snapshots.append(snapshot)

    index_snapshots: list[SymbolSnapshot] = []
    for symbol in index_symbols:
        live_row = live_snapshot.symbols.get(symbol)
        if live_row is None:
            continue
        snapshot, sma_warning = live_symbol_to_symbol_snapshot(
            live_row,
            volume_history_store,
        )
        if sma_warning:
            warnings.append(f"{symbol}: {sma_warning}")
        index_snapshots.append(snapshot)

    if not compute_market_mood:
        mood_result = MarketMoodResult(
            mood=MarketMood.NEUTRAL,
            score=50,
            reasons=[],
            blockers=[],
        )
        breadth_mood_result = None
    elif index_snapshots:
        mood_result = MarketMoodDetector().evaluate(index_snapshots)
        breadth_mood_result = None
    elif data_provider == DATA_PROVIDER_TRADINGVIEW:
        breadth_frame = build_breadth_snapshot_dataframe(
            live_snapshot,
            quality_filter_result=quality_filter_result,
            snapshot_path=snapshot_path,
            index_symbols=index_symbols,
        )
        breadth_mood_result = calculate_market_breadth_mood(breadth_frame)
        mood_result = breadth_mood_result.to_market_mood_result()
        if breadth_mood_result.warning:
            warnings.append(breadth_mood_result.warning)
        else:
            warnings.append(BREADTH_MOOD_INFO_WARNING)
    else:
        warnings.append(MISSING_INDEX_MOOD_WARNING)
        mood_result = MarketMoodResult(
            mood=MarketMood.NEUTRAL,
            score=50,
            reasons=[],
            blockers=[],
        )
        breadth_mood_result = None

    market_snapshot = MarketSnapshot(
        symbols=symbol_snapshots,
        index_snapshots=index_snapshots,
    )
    return market_snapshot, mood_result, warnings, breadth_mood_result
