"""TradingView Screener data provider for EGX live snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

import pandas as pd

from config import settings
from config.watchlist import DEFAULT_WATCHLIST
from core.market_data_providers import (
    DATA_PROVIDER_TRADINGVIEW,
    MIN_TRADINGVIEW_VALID_SYMBOLS,
    PARTIAL_TRADINGVIEW_SNAPSHOT_WARNING,
    TRADINGVIEW_FETCH_LIMIT,
)

MIN_QUERY_FIELDS = (
    "name",
    "description",
    "close",
    "change",
    "volume",
)

OPTIONAL_QUERY_FIELDS = (
    "open",
    "high",
    "low",
    "sector",
    "market_cap_basic",
    "price_earnings_ttm",
    "price_book_fq",
    "dividends_yield_current",
    "relative_volume_10d_calc",
)

OPTIONAL_TECHNICAL_QUERY_FIELDS = (
    "Recommend.All",
    "Recommend.MA",
    "Recommend.Other",
    "RSI",
    "RSI[1]",
    "MACD.macd",
    "MACD.signal",
    "EMA20",
    "SMA20",
    "EMA50",
    "SMA50",
    "ADX",
    "ATR",
)

TECHNICAL_FIELD_COLUMN_MAP: dict[str, str] = {
    "Recommend.All": "tv_recommend_all",
    "Recommend.MA": "tv_recommend_ma",
    "Recommend.Other": "tv_recommend_other",
    "RSI": "rsi",
    "RSI[1]": "rsi_prev",
    "MACD.macd": "macd",
    "MACD.signal": "macd_signal",
    "EMA20": "ema20",
    "SMA20": "sma20",
    "EMA50": "ema50",
    "SMA50": "sma50",
    "ADX": "adx",
    "ATR": "atr",
}

SNAPSHOT_BASE_COLUMNS = [
    "date",
    "symbol",
    "company_name",
    "previous_close",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

SNAPSHOT_EXTRA_COLUMNS = [
    "provider_symbol",
    "data_provider",
    "sector",
    "market_cap",
    "pe_ratio",
    "pb_ratio",
    "dividend_yield",
    "volume_ratio",
    "tv_relative_volume_10d",
    *TECHNICAL_FIELD_COLUMN_MAP.values(),
]


@dataclass(frozen=True)
class TradingViewQueryFilterConfig:
    """Optional TradingView screener query-level pre-filters."""

    enabled: bool = False
    min_price: float | None = None
    min_volume: int | None = None
    min_market_cap: float | None = None
    exclude_zero_volume: bool = True
    min_expected_rows: int = 50

    def has_active_filters(self) -> bool:
        """Return True when at least one query prefilter should be applied."""
        return (
            self.min_price is not None
            or self.min_volume is not None
            or self.min_market_cap is not None
            or self.exclude_zero_volume
        )


@dataclass(frozen=True)
class TradingViewQueryPrefilterDiagnostics:
    """Diagnostics for TradingView query prefilter attempts."""

    enabled: bool = False
    attempted: bool = False
    used: bool = False
    rows_fetched: int = 0
    fallback: bool = False
    fallback_reason: str | None = None
    watchlist_repair: bool = False
    watchlist_repaired_symbols: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "attempted": self.attempted,
            "used": self.used,
            "rows_fetched": self.rows_fetched,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "watchlist_repair": self.watchlist_repair,
            "watchlist_repaired_symbols": list(self.watchlist_repaired_symbols),
        }


def build_tradingview_query_filter_config_from_cli(
    *,
    enabled: bool = False,
    quality_filters: "MarketQualityFilters | None" = None,
) -> TradingViewQueryFilterConfig:
    """Build TradingView query prefilter config from CLI and market quality values."""
    from core.market_quality_filters import MarketQualityFilters

    filters = quality_filters or MarketQualityFilters()
    if not enabled:
        return TradingViewQueryFilterConfig(enabled=False)
    min_volume = None if filters.include_illiquid else filters.min_volume
    exclude_zero_volume = (
        False if filters.include_illiquid else filters.exclude_zero_volume
    )
    return TradingViewQueryFilterConfig(
        enabled=True,
        min_price=filters.min_price,
        min_volume=min_volume,
        min_market_cap=filters.min_market_cap,
        exclude_zero_volume=exclude_zero_volume,
    )


def build_tradingview_query_prefilter_summary_lines(
    diagnostics: TradingViewQueryPrefilterDiagnostics,
) -> list[str]:
    """Build compact report lines for TradingView query prefilter diagnostics."""
    lines = [
        f"- Enabled: {'yes' if diagnostics.enabled else 'no'}",
        f"- Attempted: {'yes' if diagnostics.attempted else 'no'}",
        f"- Used: {'yes' if diagnostics.used else 'no'}",
    ]
    if diagnostics.attempted or diagnostics.used:
        lines.append(f"- Rows fetched: {diagnostics.rows_fetched}")
    if diagnostics.enabled:
        lines.append(f"- Watchlist repair: {'yes' if diagnostics.watchlist_repair else 'no'}")
        repaired_text = (
            ", ".join(diagnostics.watchlist_repaired_symbols)
            if diagnostics.watchlist_repaired_symbols
            else "none"
        )
        lines.append(f"- Watchlist repaired symbols: {repaired_text}")
    fallback_text = "no"
    if diagnostics.fallback:
        fallback_text = (
            f"yes, reason: {diagnostics.fallback_reason}"
            if diagnostics.fallback_reason
            else "yes"
        )
    lines.append(f"- Fallback: {fallback_text}")
    return lines


@dataclass
class TradingViewSnapshotResult:
    success: bool
    valid_symbol_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    snapshot_path: Path | None = None
    selected_fields: list[str] = field(default_factory=list)
    query_prefilter_diagnostics: TradingViewQueryPrefilterDiagnostics | None = None


def normalize_tradingview_symbol(raw: object) -> str:
    """Normalize TradingView tickers like EGX:SWDY to SWDY."""
    symbol = str(raw).strip().upper()
    if ":" in symbol:
        symbol = symbol.split(":", 1)[-1].strip()
    return symbol


def _safe_float(value: object) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return float(parsed)


def _pick_column(frame: pd.DataFrame, *names: str) -> str | None:
    for name in names:
        if name in frame.columns:
            return name
    return None


def _extract_symbol_and_provider(row: pd.Series, frame: pd.DataFrame) -> tuple[str, str]:
    ticker_column = _pick_column(frame, "ticker", "name")
    provider_symbol = ""
    if ticker_column is not None:
        provider_symbol = str(row[ticker_column]).strip()

    symbol_source = provider_symbol or ""
    if ticker_column == "name" and ":" not in symbol_source:
        symbol = normalize_tradingview_symbol(symbol_source)
    else:
        symbol = normalize_tradingview_symbol(provider_symbol or symbol_source)

    if not symbol and ticker_column is not None:
        symbol = normalize_tradingview_symbol(row[ticker_column])

    return symbol, provider_symbol or symbol


def _extract_company_name(row: pd.Series, frame: pd.DataFrame, symbol: str) -> str:
    description_column = _pick_column(frame, "description")
    if description_column is not None:
        description = str(row[description_column]).strip()
        if description and description.upper() != symbol:
            return description

    name_column = _pick_column(frame, "name")
    if name_column is not None:
        name_value = str(row[name_column]).strip()
        if name_value and normalize_tradingview_symbol(name_value) != symbol:
            return name_value
        if name_value and ":" not in name_value and name_value.upper() != symbol:
            return name_value

    return symbol


def _extract_change_percent(row: pd.Series, frame: pd.DataFrame) -> float | None:
    change_column = _pick_column(
        frame,
        "change",
        "change_percent",
        "change|1",
    )
    if change_column is None:
        return None
    return _safe_float(row[change_column])


def _compute_previous_close(close: float, change_percent: float | None) -> float:
    if change_percent is None or change_percent <= -100:
        return close
    return close / (1 + (change_percent / 100))


def _normalize_ohlc(
    close: float,
    open_price: float | None,
    high: float | None,
    low: float | None,
) -> tuple[float, float, float]:
    open_value = open_price if open_price is not None else close
    high_value = high if high is not None else max(open_value, close)
    low_value = low if low is not None else min(open_value, close)
    high_value = max(high_value, open_value, close)
    low_value = min(low_value, open_value, close)
    return open_value, high_value, low_value


def _row_is_valid_ohlc(open_price: float, high: float, low: float, close: float) -> bool:
    if high < open_price or high < close:
        return False
    if low > open_price or low > close:
        return False
    return True


def normalize_tradingview_frame(
    frame: pd.DataFrame,
    *,
    snapshot_date: date | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Normalize TradingView screener rows into live snapshot CSV rows."""
    warnings: list[str] = []
    if frame is None or frame.empty:
        return pd.DataFrame(columns=SNAPSHOT_BASE_COLUMNS), ["TradingView returned no rows."]

    as_of_date = snapshot_date or date.today()
    close_column = _pick_column(frame, "close")
    volume_column = _pick_column(frame, "volume")
    open_column = _pick_column(frame, "open")
    high_column = _pick_column(frame, "high")
    low_column = _pick_column(frame, "low")
    sector_column = _pick_column(frame, "sector")
    market_cap_column = _pick_column(frame, "market_cap_basic", "market_cap")
    pe_column = _pick_column(frame, "price_earnings_ttm", "pe_ratio")
    pb_column = _pick_column(frame, "price_book_fq", "pb_ratio")
    dividend_column = _pick_column(
        frame,
        "dividends_yield_current",
        "dividend_yield",
    )
    volume_ratio_column = _pick_column(frame, "relative_volume_10d_calc", "volume_ratio")

    if close_column is None or volume_column is None:
        return pd.DataFrame(columns=SNAPSHOT_BASE_COLUMNS), [
            "TradingView response is missing required close/volume columns."
        ]

    normalized_rows: list[dict[str, object]] = []
    seen_symbols: set[str] = set()

    for _, row in frame.iterrows():
        symbol, provider_symbol = _extract_symbol_and_provider(row, frame)
        if not symbol:
            warnings.append("Skipped TradingView row with empty symbol.")
            continue
        if symbol in seen_symbols:
            warnings.append(f"Duplicate TradingView symbol {symbol}; keeping first row.")
            continue

        close = _safe_float(row[close_column])
        volume = _safe_float(row[volume_column])
        if close is None or close <= 0:
            warnings.append(f"Skipped {symbol}: invalid close.")
            continue
        if volume is None or volume < 0:
            warnings.append(f"Skipped {symbol}: invalid volume.")
            continue

        open_price = _safe_float(row[open_column]) if open_column else None
        high = _safe_float(row[high_column]) if high_column else None
        low = _safe_float(row[low_column]) if low_column else None
        open_value, high_value, low_value = _normalize_ohlc(close, open_price, high, low)
        if not _row_is_valid_ohlc(open_value, high_value, low_value, close):
            warnings.append(f"Skipped {symbol}: invalid OHLC range.")
            continue

        change_percent = _extract_change_percent(row, frame)
        previous_close = _compute_previous_close(close, change_percent)
        if previous_close <= 0:
            previous_close = close

        company_name = _extract_company_name(row, frame, symbol)
        volume_ratio = 1.0
        tv_relative_volume_10d: float | None = None
        if volume_ratio_column is not None:
            parsed_ratio = _safe_float(row[volume_ratio_column])
            if parsed_ratio is not None and parsed_ratio > 0:
                volume_ratio = parsed_ratio
                tv_relative_volume_10d = parsed_ratio

        normalized_row: dict[str, object] = {
            "date": as_of_date.isoformat(),
            "symbol": symbol,
            "company_name": company_name,
            "previous_close": previous_close,
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close,
            "volume": volume,
            "provider_symbol": provider_symbol,
            "data_provider": DATA_PROVIDER_TRADINGVIEW,
            "volume_ratio": volume_ratio,
        }
        if tv_relative_volume_10d is not None:
            normalized_row["tv_relative_volume_10d"] = tv_relative_volume_10d

        if sector_column is not None:
            sector = str(row[sector_column]).strip()
            if sector:
                normalized_row["sector"] = sector
        if market_cap_column is not None:
            market_cap = _safe_float(row[market_cap_column])
            if market_cap is not None:
                normalized_row["market_cap"] = market_cap
        if pe_column is not None:
            pe_ratio = _safe_float(row[pe_column])
            if pe_ratio is not None:
                normalized_row["pe_ratio"] = pe_ratio
        if pb_column is not None:
            pb_ratio = _safe_float(row[pb_column])
            if pb_ratio is not None:
                normalized_row["pb_ratio"] = pb_ratio
        if dividend_column is not None:
            dividend_yield = _safe_float(row[dividend_column])
            if dividend_yield is not None:
                normalized_row["dividend_yield"] = dividend_yield

        for tv_field, output_column in TECHNICAL_FIELD_COLUMN_MAP.items():
            source_column = _pick_column(frame, tv_field)
            if source_column is None:
                continue
            parsed_value = _safe_float(row[source_column])
            if parsed_value is not None:
                normalized_row[output_column] = parsed_value

        normalized_rows.append(normalized_row)
        seen_symbols.add(symbol)

    if not normalized_rows:
        return pd.DataFrame(columns=SNAPSHOT_BASE_COLUMNS), warnings + [
            "No valid TradingView rows remained after normalization."
        ]

    output = pd.DataFrame(normalized_rows)
    ordered_columns = [
        column
        for column in SNAPSHOT_BASE_COLUMNS + SNAPSHOT_EXTRA_COLUMNS
        if column in output.columns
    ]
    return output[ordered_columns], warnings


def _apply_tv_query_prefilters(
    query: object,
    query_filter_config: TradingViewQueryFilterConfig,
) -> object:
    """Apply TradingView screener where clauses for query-level pre-filters."""
    from tradingview_screener import col

    conditions: list[object] = []
    if query_filter_config.min_price is not None:
        conditions.append(col("close") >= query_filter_config.min_price)
    if query_filter_config.min_volume is not None:
        conditions.append(col("volume") >= query_filter_config.min_volume)
    if query_filter_config.min_market_cap is not None:
        conditions.append(col("market_cap_basic") >= query_filter_config.min_market_cap)
    if query_filter_config.exclude_zero_volume:
        conditions.append(col("volume") > 0)
    if not conditions:
        return query
    return query.where(*conditions)


def _fetch_tradingview_frame(
    selected_fields: list[str],
    query_filter_config: TradingViewQueryFilterConfig | None = None,
) -> pd.DataFrame:
    from tradingview_screener import Query

    query = (
        Query()
        .set_markets("egypt")
        .select(*selected_fields)
        .limit(TRADINGVIEW_FETCH_LIMIT)
    )
    if (
        query_filter_config is not None
        and query_filter_config.enabled
        and query_filter_config.has_active_filters()
    ):
        query = _apply_tv_query_prefilters(query, query_filter_config)

    total_count, frame = query.get_scanner_data()
    if frame is None or frame.empty:
        raise ValueError(f"TradingView returned no rows (reported count: {total_count}).")
    return frame


def _symbols_present_in_raw_frame(frame: pd.DataFrame) -> set[str]:
    """Return normalized symbols present in a raw TradingView screener frame."""
    if frame is None or frame.empty:
        return set()
    symbols: set[str] = set()
    for _, row in frame.iterrows():
        symbol, _ = _extract_symbol_and_provider(row, frame)
        if symbol:
            symbols.add(symbol)
    return symbols


def repair_missing_watchlist_symbols(
    frame: pd.DataFrame,
    selected_fields: list[str],
    *,
    watchlist: list[str] | None = None,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Fetch unfiltered rows for configured watchlist symbols missing from frame."""
    configured = watchlist or DEFAULT_WATCHLIST
    normalized_watchlist = [
        normalize_tradingview_symbol(symbol)
        for symbol in configured
        if str(symbol).strip()
    ]
    present = _symbols_present_in_raw_frame(frame)
    missing = [symbol for symbol in normalized_watchlist if symbol not in present]
    if not missing:
        return frame, ()

    missing_symbols = set(missing)
    try:
        repair_frame = _fetch_tradingview_frame(selected_fields, None)
    except Exception:  # noqa: BLE001
        return frame, ()

    repair_rows: list[pd.Series] = []
    for _, row in repair_frame.iterrows():
        symbol, _ = _extract_symbol_and_provider(row, repair_frame)
        if symbol in missing_symbols:
            repair_rows.append(row)

    if not repair_rows:
        return frame, ()

    repair_df = pd.DataFrame(repair_rows)
    merged = pd.concat([frame, repair_df], ignore_index=True)
    repaired_symbols = tuple(
        sorted(
            {
                _extract_symbol_and_provider(row, repair_df)[0]
                for _, row in repair_df.iterrows()
            }
        )
    )
    return merged, repaired_symbols


def _resolve_working_query_fields(
    base_fields: list[str],
    optional_fields: list[str],
    query_filter_config: TradingViewQueryFilterConfig | None = None,
) -> list[str]:
    """Return the largest working TradingView field set without failing the fetch."""
    selected = list(dict.fromkeys(base_fields))
    for field_name in optional_fields:
        try:
            _fetch_tradingview_frame(selected + [field_name], query_filter_config)
            selected.append(field_name)
        except Exception:  # noqa: BLE001
            continue
    return selected


def _attempt_prefiltered_fetch(
    selected_fields: list[str],
    query_filter_config: TradingViewQueryFilterConfig,
) -> tuple[pd.DataFrame, TradingViewQueryPrefilterDiagnostics]:
    diagnostics = TradingViewQueryPrefilterDiagnostics(
        enabled=True,
        attempted=True,
    )
    try:
        frame = _fetch_tradingview_frame(selected_fields, query_filter_config)
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), TradingViewQueryPrefilterDiagnostics(
            enabled=True,
            attempted=True,
            used=False,
            rows_fetched=0,
            fallback=True,
            fallback_reason=f"query failed ({exc})",
        )

    row_count = len(frame)
    if row_count < query_filter_config.min_expected_rows:
        return frame, TradingViewQueryPrefilterDiagnostics(
            enabled=True,
            attempted=True,
            used=False,
            rows_fetched=row_count,
            fallback=True,
            fallback_reason=f"returned only {row_count} rows",
        )

    return frame, TradingViewQueryPrefilterDiagnostics(
        enabled=True,
        attempted=True,
        used=True,
        rows_fetched=row_count,
        fallback=False,
    )


def fetch_tradingview_egypt_frame(
    query_filter_config: TradingViewQueryFilterConfig | None = None,
) -> tuple[pd.DataFrame, list[str], TradingViewQueryPrefilterDiagnostics]:
    """Fetch Egypt market rows from TradingView with resilient field selection."""
    config = query_filter_config or TradingViewQueryFilterConfig()
    diagnostics = TradingViewQueryPrefilterDiagnostics(enabled=config.enabled)
    base_fields = list(dict.fromkeys([*MIN_QUERY_FIELDS, *OPTIONAL_QUERY_FIELDS]))
    technical_fields = list(OPTIONAL_TECHNICAL_QUERY_FIELDS)

    def _fetch_fields(
        fields: list[str],
        *,
        use_prefilter: bool,
    ) -> pd.DataFrame:
        active_config = config if use_prefilter and config.enabled else None
        return _fetch_tradingview_frame(fields, active_config)

    if config.enabled and config.has_active_filters():
        selected_fields = list(dict.fromkeys([*base_fields, *technical_fields]))
        frame, prefilter_diag = _attempt_prefiltered_fetch(selected_fields, config)
        if prefilter_diag.used:
            return frame, selected_fields, prefilter_diag

        diagnostics = prefilter_diag
        try:
            frame = _fetch_fields(selected_fields, use_prefilter=False)
            return frame, selected_fields, TradingViewQueryPrefilterDiagnostics(
                enabled=True,
                attempted=True,
                used=False,
                rows_fetched=len(frame),
                fallback=True,
                fallback_reason=prefilter_diag.fallback_reason,
            )
        except Exception:  # noqa: BLE001
            pass

        selected_fields = _resolve_working_query_fields(
            base_fields,
            technical_fields,
            None,
        )
        try:
            frame = _fetch_fields(selected_fields, use_prefilter=False)
            return frame, selected_fields, diagnostics
        except Exception as first_error:  # noqa: BLE001
            try:
                frame = _fetch_fields(list(MIN_QUERY_FIELDS), use_prefilter=False)
                return frame, list(MIN_QUERY_FIELDS), diagnostics
            except Exception as second_error:  # noqa: BLE001
                raise RuntimeError(
                    f"TradingView fetch failed after query prefilter fallback "
                    f"({first_error}); minimum-field retry also failed ({second_error})."
                ) from second_error

    try:
        all_fields = list(dict.fromkeys([*base_fields, *technical_fields]))
        frame = _fetch_fields(all_fields, use_prefilter=False)
        return frame, all_fields, diagnostics
    except Exception:  # noqa: BLE001
        pass

    selected_fields = _resolve_working_query_fields(base_fields, technical_fields, None)
    try:
        frame = _fetch_fields(selected_fields, use_prefilter=False)
        return frame, selected_fields, diagnostics
    except Exception as first_error:  # noqa: BLE001
        try:
            frame = _fetch_fields(list(MIN_QUERY_FIELDS), use_prefilter=False)
            return frame, list(MIN_QUERY_FIELDS), diagnostics
        except Exception as second_error:  # noqa: BLE001
            raise RuntimeError(
                f"TradingView fetch failed with extended fields ({first_error}); "
                f"minimum-field retry also failed ({second_error})."
            ) from second_error


def fetch_and_save_tradingview_snapshot(
    snapshot_path: Path,
    query_filter_config: TradingViewQueryFilterConfig | None = None,
) -> TradingViewSnapshotResult:
    """Fetch TradingView Egypt stocks and save a normalized live snapshot CSV."""
    warnings: list[str] = []
    query_prefilter_diagnostics = TradingViewQueryPrefilterDiagnostics(
        enabled=(query_filter_config.enabled if query_filter_config else False),
    )
    try:
        raw_frame, selected_fields, query_prefilter_diagnostics = fetch_tradingview_egypt_frame(
            query_filter_config,
        )
    except Exception as exc:  # noqa: BLE001
        return TradingViewSnapshotResult(
            success=False,
            errors=[str(exc)],
            query_prefilter_diagnostics=query_prefilter_diagnostics,
        )

    if query_prefilter_diagnostics.fallback and query_prefilter_diagnostics.fallback_reason:
        warnings.append(
            "TradingView query prefilter fallback: "
            f"{query_prefilter_diagnostics.fallback_reason}"
        )

    if query_prefilter_diagnostics.used:
        raw_frame, repaired_symbols = repair_missing_watchlist_symbols(
            raw_frame,
            selected_fields,
        )
        query_prefilter_diagnostics = replace(
            query_prefilter_diagnostics,
            watchlist_repair=bool(repaired_symbols),
            watchlist_repaired_symbols=repaired_symbols,
        )
        if repaired_symbols:
            warnings.append(
                "TradingView query prefilter watchlist repair added: "
                + ", ".join(repaired_symbols)
            )

    normalized_frame, normalize_warnings = normalize_tradingview_frame(raw_frame)
    warnings.extend(normalize_warnings)

    if normalized_frame.empty:
        return TradingViewSnapshotResult(
            success=False,
            warnings=warnings,
            errors=["No valid TradingView symbols remained after normalization."],
            selected_fields=selected_fields,
            query_prefilter_diagnostics=query_prefilter_diagnostics,
        )

    valid_symbol_count = len(normalized_frame)
    if valid_symbol_count < MIN_TRADINGVIEW_VALID_SYMBOLS:
        warnings.append(
            f"{PARTIAL_TRADINGVIEW_SNAPSHOT_WARNING} ({valid_symbol_count} symbols)."
        )

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_frame.to_csv(snapshot_path, index=False)

    return TradingViewSnapshotResult(
        success=True,
        valid_symbol_count=valid_symbol_count,
        warnings=warnings,
        snapshot_path=snapshot_path,
        selected_fields=selected_fields,
        query_prefilter_diagnostics=query_prefilter_diagnostics,
    )


SUPPORTED_TIMEFRAMES = frozenset({"1h", "15m"})

TIMEFRAME_TV_SUFFIX = {
    "1h": "60",
    "15m": "15",
}

TIMEFRAME_COLUMN_PREFIX = {
    "1h": "tf_1h",
    "15m": "tf_15m",
}

MULTI_TIMEFRAME_QUERY_BASE_FIELDS = (
    "close",
    "change",
    "volume",
    "Recommend.All",
    "Recommend.MA",
    "RSI",
    "MACD.macd",
    "MACD.signal",
    "EMA20",
    "SMA20",
    "ADX",
)

MULTI_TIMEFRAME_OUTPUT_FIELD_MAP = {
    "close": "close",
    "change": "change",
    "volume": "volume",
    "Recommend.All": "recommend_all",
    "RSI": "rsi",
    "MACD.macd": "macd",
    "MACD.signal": "macd_signal",
    "EMA20": "ema20",
    "SMA20": "sma20",
    "ADX": "adx",
}

MULTI_TIMEFRAME_UNAVAILABLE_WARNING = "Multi-timeframe data unavailable"


def _timeframe_tv_suffix(timeframe: str) -> str | None:
    return TIMEFRAME_TV_SUFFIX.get(timeframe)


def _build_timeframe_query_fields(timeframe: str) -> list[str]:
    suffix = _timeframe_tv_suffix(timeframe)
    if suffix is None:
        return []
    return ["name", *[f"{field}|{suffix}" for field in MULTI_TIMEFRAME_QUERY_BASE_FIELDS]]


def _normalize_timeframe_snapshot_frame(
    frame: pd.DataFrame,
    timeframe: str,
) -> pd.DataFrame:
    suffix = _timeframe_tv_suffix(timeframe)
    prefix = TIMEFRAME_COLUMN_PREFIX.get(timeframe)
    if suffix is None or prefix is None or frame is None or frame.empty:
        return pd.DataFrame(columns=["symbol"])

    normalized_rows: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        symbol, _ = _extract_symbol_and_provider(row, frame)
        if not symbol:
            continue
        normalized_row: dict[str, object] = {"symbol": symbol}
        for tv_field, output_field in MULTI_TIMEFRAME_OUTPUT_FIELD_MAP.items():
            column_name = f"{tv_field}|{suffix}"
            if column_name not in frame.columns:
                continue
            parsed = _safe_float(row[column_name])
            if parsed is not None:
                normalized_row[f"{prefix}_{output_field}"] = parsed
        if len(normalized_row) > 1:
            normalized_rows.append(normalized_row)

    if not normalized_rows:
        return pd.DataFrame(columns=["symbol"])
    return pd.DataFrame(normalized_rows)


def fetch_tradingview_timeframe_snapshot(
    timeframe: str,
    symbols: list[str],
) -> pd.DataFrame:
    """Fetch normalized TradingView rows for one intraday timeframe."""
    if timeframe not in SUPPORTED_TIMEFRAMES or not symbols:
        return pd.DataFrame(columns=["symbol"])

    requested_symbols = {
        normalize_tradingview_symbol(symbol)
        for symbol in symbols
        if str(symbol).strip()
    }
    if not requested_symbols:
        return pd.DataFrame(columns=["symbol"])

    select_fields = _build_timeframe_query_fields(timeframe)
    if not select_fields:
        return pd.DataFrame(columns=["symbol"])

    try:
        from tradingview_screener import Query

        _, frame = (
            Query()
            .set_markets("egypt")
            .select(*select_fields)
            .limit(TRADINGVIEW_FETCH_LIMIT)
            .get_scanner_data()
        )
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=["symbol"])

    if frame is None or frame.empty:
        return pd.DataFrame(columns=["symbol"])

    normalized = _normalize_timeframe_snapshot_frame(frame, timeframe)
    if normalized.empty or "symbol" not in normalized.columns:
        return pd.DataFrame(columns=["symbol"])

    filtered = normalized[normalized["symbol"].isin(requested_symbols)].copy()
    return filtered.reset_index(drop=True)


def merge_timeframe_snapshots(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Merge normalized timeframe snapshots on symbol."""
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    if not usable:
        return pd.DataFrame(columns=["symbol"])

    merged = usable[0]
    for frame in usable[1:]:
        merged = merged.merge(frame, on="symbol", how="outer")
    return merged


def tradingview_snapshot_is_usable(result: TradingViewSnapshotResult) -> bool:
    """Return True when a TradingView snapshot has enough symbols for auto mode."""
    return result.success and result.valid_symbol_count >= MIN_TRADINGVIEW_VALID_SYMBOLS


def print_tradingview_snapshot_summary(result: TradingViewSnapshotResult) -> None:
    """Print TradingView snapshot save summary lines."""
    if result.snapshot_path is None:
        return
    try:
        snapshot_display = result.snapshot_path.relative_to(settings.PROJECT_ROOT)
    except ValueError:
        snapshot_display = result.snapshot_path
    print(f"Live snapshot: {snapshot_display}")
    print(f"Valid symbols: {result.valid_symbol_count}")
    if result.selected_fields:
        print(f"TradingView fields: {', '.join(result.selected_fields)}")
