"""Scanner universe modes for EGX live snapshot scanning."""

from __future__ import annotations

SCANNER_UNIVERSE_WATCHLIST = "watchlist"
SCANNER_UNIVERSE_FULL_MARKET = "full-market"
DEFAULT_SCANNER_UNIVERSE = SCANNER_UNIVERSE_WATCHLIST
SCANNER_UNIVERSE_CHOICES = (
    SCANNER_UNIVERSE_WATCHLIST,
    SCANNER_UNIVERSE_FULL_MARKET,
)

MISSING_WATCHLIST_SYMBOL_WARNING_PREFIX = "Watchlist symbol "


def is_full_market_universe(scanner_universe: str) -> bool:
    """Return True when the scanner should evaluate every live snapshot symbol."""
    return scanner_universe == SCANNER_UNIVERSE_FULL_MARKET


def format_scanner_universe_label(scanner_universe: str) -> str:
    """Return the user-facing scanner universe label."""
    return scanner_universe
