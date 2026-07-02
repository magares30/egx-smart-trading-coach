"""Market data provider identifiers for EGX live snapshot ingestion."""

from __future__ import annotations

DATA_PROVIDER_EGX = "egx"
DATA_PROVIDER_TRADINGVIEW = "tradingview"
DATA_PROVIDER_AUTO = "auto"
DEFAULT_DATA_PROVIDER = DATA_PROVIDER_EGX

DATA_PROVIDER_LABELS: dict[str, str] = {
    DATA_PROVIDER_EGX: "EGX Chrome Reader",
    DATA_PROVIDER_TRADINGVIEW: "TradingView Screener",
}

LOCAL_SNAPSHOT_PROVIDER_LABEL = "Local snapshot (cached)"

MIN_TRADINGVIEW_VALID_SYMBOLS = 80
TRADINGVIEW_FETCH_LIMIT = 1000

PARTIAL_TRADINGVIEW_SNAPSHOT_WARNING = (
    "TradingView snapshot looks partial; valid symbol count is low."
)
AUTO_FALLBACK_TO_EGX_WARNING = (
    "TradingView fetch failed or returned too few valid symbols; "
    "falling back to EGX Chrome Reader."
)


def format_data_provider_label(provider: str | None) -> str:
    """Return a human-readable data provider label."""
    if provider is None:
        return LOCAL_SNAPSHOT_PROVIDER_LABEL
    return DATA_PROVIDER_LABELS.get(provider, provider)
