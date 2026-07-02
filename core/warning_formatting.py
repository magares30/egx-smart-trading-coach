"""Summarize repeated live-scan warnings for reports and console output."""

from __future__ import annotations

from config import settings
from config.watchlist import DEFAULT_WATCHLIST
from core.live_scanner_adapter import SMA5_HISTORY_WARNING
from core.live_volume import NOT_ENOUGH_VOLUME_HISTORY_WARNING

VOLUME_HISTORY_SUMMARY = (
    "Not enough volume history for {count} symbols. "
    "This is normal until at least {min_history_days} live history snapshots exist."
)
WATCHLIST_VOLUME_HISTORY_SUMMARY = (
    "Watchlist symbols with insufficient volume history: {symbols}"
)
SMA5_WATCHLIST_SUMMARY = (
    "Not enough live history to compute SMA5 for {count} watchlist symbols; "
    "using current close only."
)
SMA5_GENERAL_SUMMARY = (
    "Not enough live history to compute SMA5 for {count} symbols; "
    "using current close only."
)


def _split_symbol_prefixed_warning(warning: str, message: str) -> str | None:
    """Return the symbol when *warning* is ``SYMBOL: message``."""
    suffix = f": {message}"
    if warning.endswith(message) and warning.endswith(suffix):
        symbol = warning[: -len(suffix)]
        return symbol or None
    return None


def _dedupe_preserve_order(warnings: list[str]) -> list[str]:
    """Remove duplicate warning lines while preserving first-seen order."""
    seen: set[str] = set()
    deduped: list[str] = []
    for warning in warnings:
        if warning in seen:
            continue
        seen.add(warning)
        deduped.append(warning)
    return deduped


def summarize_live_scan_warnings(
    warnings: list[str],
    *,
    watchlist: list[str] | None = None,
    min_history_days: int | None = None,
) -> list[str]:
    """Collapse repeated live-history warnings while keeping critical alerts."""
    watchlist_symbols = watchlist or DEFAULT_WATCHLIST
    watchlist_set = set(watchlist_symbols)
    min_days = (
        min_history_days
        if min_history_days is not None
        else settings.DEFAULT_LIVE_VOLUME_MIN_HISTORY_DAYS
    )

    volume_symbols: list[str] = []
    sma5_symbols: list[str] = []
    other_warnings: list[str] = []

    for warning in warnings:
        volume_symbol = _split_symbol_prefixed_warning(
            warning,
            NOT_ENOUGH_VOLUME_HISTORY_WARNING,
        )
        if volume_symbol is not None:
            volume_symbols.append(volume_symbol)
            continue

        sma5_symbol = _split_symbol_prefixed_warning(warning, SMA5_HISTORY_WARNING)
        if sma5_symbol is not None:
            sma5_symbols.append(sma5_symbol)
            continue

        other_warnings.append(warning)

    summarized = list(other_warnings)

    if volume_symbols:
        summarized.append(
            VOLUME_HISTORY_SUMMARY.format(
                count=len(volume_symbols),
                min_history_days=min_days,
            )
        )
        volume_symbol_set = set(volume_symbols)
        watchlist_volume_symbols = [
            symbol
            for symbol in watchlist_symbols
            if symbol in volume_symbol_set
        ]
        if watchlist_volume_symbols:
            summarized.append(
                WATCHLIST_VOLUME_HISTORY_SUMMARY.format(
                    symbols=", ".join(watchlist_volume_symbols)
                )
            )

    if sma5_symbols:
        sma5_symbol_set = set(sma5_symbols)
        if sma5_symbol_set.issubset(watchlist_set):
            summarized.append(
                SMA5_WATCHLIST_SUMMARY.format(count=len(sma5_symbols))
            )
        else:
            summarized.append(
                SMA5_GENERAL_SUMMARY.format(count=len(sma5_symbols))
            )

    return _dedupe_preserve_order(summarized)


# Backward-compatible alias used by daily reports.
summarize_daily_report_warnings = summarize_live_scan_warnings
