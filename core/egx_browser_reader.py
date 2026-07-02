"""Read visible EGX stocks table from the official public prices page via Playwright."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from config import settings
from core.data_import import (
    LIVE_SNAPSHOT_OUTPUT_COLUMNS,
    LIVE_SNAPSHOT_REQUIRED_COLUMNS,
    REQUIRED_COLUMNS,
    DataImportValidationResult,
    EgxCsvImportValidator,
    EgxLiveSnapshotValidator,
    resolve_column_name,
)
from core.symbol_mapping import MappingResult, apply_symbol_mapping_to_snapshot_dataframe

EGX_BROWSER_STOCKS_URLS: tuple[str, ...] = (
    "https://egx.com.eg/en/prices.aspx",
    "https://egx.com.eg/en/Prices.aspx",
)
EGX_BROWSER_STOCKS_URL = EGX_BROWSER_STOCKS_URLS[0]
EGX_BROWSER_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
]
DEFAULT_CHROME_CDP_URL = "http://127.0.0.1:9222"
NO_EGX_PRICES_PAGE_ERROR = "No open EGX prices page found in attached Chrome."
NO_STOCKS_TABLE_ERROR = (
    "No EGX stocks table found in page DOM. Open EGX prices page at "
    "Stocks > Trading Data and wait for the table."
)
NO_BROWSER_CONTEXT_ERROR = "No browser context available in attached Chrome."
EGX_PAGE_OPEN_FAILED_ERROR = "Failed to open EGX prices page."
PAGE_LOAD_TIMEOUT_MS = 60_000
NETWORK_IDLE_TIMEOUT_MS = 60_000
TABLE_REFRESH_WAIT_MS = 1_500
EGX_UPDATE_OVERLAY_TIMEOUT_MS = 60_000
EGX_UPDATE_RECOVERY_WAIT_MS = 2_000
EGX_STOCKS_TABLE_WAIT_TIMEOUT_MS = 60_000
STOCKS_TAB_TEXT = "Stocks"
TRADING_DATA_TAB_TEXT = "Trading Data"
SECTOR_TAB_TEXT = "Sector"
COMPANY_TAB_TEXT = "Company"
MARKET_SEGMENT_TAB_TEXT = "Market Segment"
HEADER_SEARCH_INPUT_ID = "ctl00_H_txtSearchAll"
EXCLUDED_INPUT_ID_MARKERS = ("_H_txtSearch", "txtSearchAll")
COMPANY_NAME_PLACEHOLDER_MARKERS = (
    "enter part of the company name",
    "company name",
    "part of the company",
)
MARKET_WATCH_ROOT_SELECTORS = (
    "[id*='ContentPlaceHolder']",
    "[id*='MarketWatch']",
    "[id*='marketwatch']",
    "#content",
)
EGX_UPDATE_OVERLAY_TIMEOUT_WARNING = (
    "EGX update overlay timeout; attempting recovery."
)
MULTI_SECTOR_SECTOR_SKIPPED_WARNING = (
    "Skipped sector {sector} after update overlay timeout."
)
LOW_ROW_COUNT_THRESHOLD = 100
LOW_ROW_COUNT_WARNING = (
    "Low EGX row count after reset; page may still be filtered."
)
RESET_FILTER_WARNING_PREFIX = "Reset filter warning:"
ALL_OPTION_MARKERS = {
    "",
    "all",
    "all sectors",
    "all market segments",
    "all traded stocks",
    "all stocks",
    "traded stocks",
    "select",
    "--",
}
TRADED_STOCKS_INDEX_MARKERS = (
    "egx 30",
    "egx 70",
    "egx 100",
    "most active",
    "sharia",
)
LOW_SYMBOL_COUNT_WARNING = (
    "Low valid EGX symbol count after normalization ({count}); "
    "page may still be filtered."
)
CRITICAL_SYMBOL_COUNT_WARNING = (
    "Very low valid EGX symbol count after normalization ({count}); "
    "snapshot likely partial or filtered."
)
MULTI_SECTOR_COLLECTED_WARNING = "Collected EGX stocks from {count} sectors"
MULTI_SECTOR_BEFORE_DEDUPE_WARNING = "Combined rows before dedupe: {count}"
MULTI_SECTOR_AFTER_DEDUPE_WARNING = "Combined rows after dedupe: {count}"
MULTI_SECTOR_UNAVAILABLE_FALLBACK_WARNING = (
    "Multi-sector collection unavailable; using single visible table extraction."
)
MULTI_SECTOR_FAILED_VISIBLE_TABLE_FALLBACK_WARNING = (
    "Multi-sector collection failed; used current visible table fallback."
)
MULTI_SECTOR_REUSED_TABLE_CRITICAL_WARNING = (
    "Multi-sector collection likely reused the same table repeatedly. "
    "Sector/Market Segment filters were not applied correctly."
)
SECTOR_DUPLICATE_FINGERPRINT_WARNING = (
    "Sector {sector} did not change the table; skipping duplicate extraction."
)
SECTOR_FINGERPRINT_UNCHANGED_WARNING = (
    "Sector '{sector}' selected but table fingerprint did not change."
)
MARKET_SEGMENT_FINGERPRINT_UNCHANGED_WARNING = (
    "Market Segment '{segment}' selected but table fingerprint did not change."
)
FILTER_DUPLICATE_FINGERPRINT_WARNING = (
    "{filter_kind} {filter_name!r} did not change the table; "
    "skipping duplicate extraction."
)
SEVERAL_FILTER_FINGERPRINTS_UNCHANGED_WARNING = (
    "Several {filter_kind}s selected but table fingerprint did not change; "
    "skipping duplicate extractions."
)
MULTI_MARKET_SEGMENT_COLLECTED_WARNING = (
    "Collected EGX stocks from {count} market segments"
)
NO_VISIBLE_FILTER_SUBMIT_WARNING = (
    "No visible filter submit button found; relying on dropdown change event."
)
COMPANY_FILTER_UNAVAILABLE_WARNING = (
    "Company Name filter not visible; continuing without clearing it."
)
ALREADY_ON_STOCKS_TRADING_DATA_MESSAGE = (
    "Already on Stocks Trading Data; skipping tab click."
)
VOLUME_MISSING_WARNING = "Volume missing from EGX visible table; saved as 0."
LAST_PRICE_CLOSE_WARNING = "Using Last Price as normalized close."
INVALID_OHLC_RANGE_WARNING = (
    "Dropped {count} rows with invalid OHLC ranges during normalization."
)
NON_STOCK_NAME_MARKERS = ("egyptian pound", "us dollar")
NOISY_SUBMIT_WARNING_MARKERS = (
    "no visible Go button found",
    "no visible Search button found",
    "no visible Submit button found",
    "no visible Go submit found",
    "no visible Search submit found",
    "no visible Submit submit found",
)


class WarningDeduper:
    """Track warning keys so noisy messages are only emitted once."""

    def __init__(self) -> None:
        self._seen_keys: set[str] = set()

    def add_once(self, warnings: list[str], key: str, message: str) -> None:
        if key in self._seen_keys:
            return
        self._seen_keys.add(key)
        warnings.append(message)

    def seen(self, key: str) -> bool:
        return key in self._seen_keys


BROWSER_STOCK_TABLE_KEYWORDS = {
    "name",
    "open",
    "close",
    "last price",
    "high",
    "low",
    "p.c.",
    "%chg",
    "volume",
    "symbol",
    "stock",
}
PREFERRED_STOCK_HEADERS = {
    "name",
    "open",
    "close",
    "last price",
    "high",
    "low",
}


class EgxBrowserReadResult(BaseModel):
    success: bool
    saved_csv: Path | None = None
    rows: int = 0
    columns: list[str] = Field(default_factory=list)
    page_action: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BrowserStocksNormalizationResult(BaseModel):
    ohlcv: DataImportValidationResult
    live_snapshot: DataImportValidationResult | None = None
    live_snapshot_csv: Path | None = None
    validation_warnings: list[str] = Field(default_factory=list)
    valid_symbol_count: int = 0
    symbol_mapping: MappingResult | None = None


def _normalize_header(column: object) -> str:
    return str(column).strip().lower()


def _column_text(columns: list[object]) -> str:
    return " ".join(_normalize_header(column) for column in columns)


def _score_stocks_table(columns: list[object]) -> int:
    text = _column_text(columns)
    score = sum(1 for keyword in BROWSER_STOCK_TABLE_KEYWORDS if keyword in text)
    normalized = {_normalize_header(column) for column in columns}
    if "name" in normalized:
        score += 2
    if "last price" in normalized or "close" in normalized:
        score += 2
    if PREFERRED_STOCK_HEADERS.issubset(normalized):
        score += 3
    return score


def extract_tables_from_html(html: str) -> list[pd.DataFrame]:
    """Extract HTML tables from page content."""
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return []
    except Exception:  # noqa: BLE001
        return []
    return [table for table in tables if not table.empty]


def select_stocks_table(tables: list[pd.DataFrame]) -> pd.DataFrame | None:
    """Pick the table that looks most like the EGX stocks trading grid."""
    if not tables:
        return None

    best_table: pd.DataFrame | None = None
    best_score = -1

    for table in tables:
        score = _score_stocks_table(list(table.columns))
        if score <= 0:
            continue
        if len(table) == 0:
            continue
        if score > best_score:
            best_score = score
            best_table = table
        elif score == best_score and best_table is not None and len(table) > len(best_table):
            best_table = table

    if best_table is None or best_score <= 0:
        return None
    return best_table


def summarize_tables_in_html(html: str) -> list[dict[str, object]]:
    """Summarize HTML tables for diagnostics (DOM-based, not visibility)."""
    summaries: list[dict[str, object]] = []
    for index, table in enumerate(extract_tables_from_html(html)):
        columns = [str(column) for column in table.columns]
        summaries.append(
            {
                "index": index,
                "columns": columns[:12],
                "rows": len(table),
                "score": _score_stocks_table(list(table.columns)),
            }
        )
    return summaries


def is_egx_prices_page_url(url: str) -> bool:
    """Return True when a page URL is the public EGX prices page."""
    return "egx.com.eg/en/prices.aspx" in url.lower()


def collect_browser_pages(browser: object) -> list[object]:
    """Collect open pages from a Playwright browser instance."""
    pages: list[object] = []
    for context in browser.contexts:
        pages.extend(context.pages)
    return pages


def select_egx_prices_page(pages: list[object]) -> object | None:
    """Pick the first open EGX prices page from attached Chrome tabs."""
    for page in pages:
        page_url = getattr(page, "url", "")
        if is_egx_prices_page_url(page_url):
            return page
    return None


def extract_stocks_table_from_html(html: str) -> pd.DataFrame | None:
    """Extract and select the visible EGX stocks table from page HTML."""
    return select_stocks_table(extract_tables_from_html(html))


def _is_low_row_count(rows_count: int) -> bool:
    """Return True when the extracted stocks table looks too small."""
    return rows_count < LOW_ROW_COUNT_THRESHOLD


def _build_low_row_count_warning(rows_count: int) -> str:
    """Build the standard low row-count warning message."""
    _ = rows_count
    return LOW_ROW_COUNT_WARNING


def build_symbol_count_warnings(
    symbol_count: int,
    *,
    warn_threshold: int | None = None,
    critical_threshold: int | None = None,
) -> list[str]:
    """Build warnings when normalized symbol count looks too small."""
    warn_at = warn_threshold if warn_threshold is not None else settings.MIN_VALID_SYMBOL_COUNT_WARN
    critical_at = (
        critical_threshold
        if critical_threshold is not None
        else settings.MIN_VALID_SYMBOL_COUNT_CRITICAL
    )
    warnings: list[str] = []
    if symbol_count < critical_at:
        warnings.append(CRITICAL_SYMBOL_COUNT_WARNING.format(count=symbol_count))
    elif symbol_count < warn_at:
        warnings.append(LOW_SYMBOL_COUNT_WARNING.format(count=symbol_count))
    return warnings


def filter_report_warnings(warnings: list[str]) -> list[str]:
    """Remove duplicate/noisy warnings before showing them in reports."""
    has_submit_notice = any(
        NO_VISIBLE_FILTER_SUBMIT_WARNING in warning for warning in warnings
    )
    filtered: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        if warning in seen:
            continue
        if has_submit_notice and any(
            marker in warning for marker in NOISY_SUBMIT_WARNING_MARKERS
        ):
            continue
        seen.add(warning)
        filtered.append(warning)
    return filtered


def _describe_filter_kind_for_options(options: list[str]) -> str:
    """Classify a dropdown as sector or market segment for user-facing messages."""
    if not options:
        return "sector"

    label = _infer_select_dropdown_label(options, 0)
    if label == "market segment":
        return "market segment"
    if label == "sector":
        return "sector"

    joined = " ".join(str(option).lower() for option in options)
    if any(marker in joined for marker in TRADED_STOCKS_INDEX_MARKERS):
        return "market segment"
    if label == "traded stocks":
        return "market segment"
    return "sector"


def _filter_kind_display_name(filter_kind: str) -> str:
    if filter_kind == "market segment":
        return "Market Segment"
    return "Sector"


def _build_collected_filter_summary_warning(
    filter_kind: str,
    count: int,
) -> str:
    if filter_kind == "market segment":
        return MULTI_MARKET_SEGMENT_COLLECTED_WARNING.format(count=count)
    return MULTI_SECTOR_COLLECTED_WARNING.format(count=count)


def _build_fingerprint_unchanged_warning(filter_kind: str, filter_name: str) -> str:
    if filter_kind == "market segment":
        return MARKET_SEGMENT_FINGERPRINT_UNCHANGED_WARNING.format(
            segment=filter_name
        )
    return SECTOR_FINGERPRINT_UNCHANGED_WARNING.format(sector=filter_name)


def _has_successful_multi_filter_collection(warnings: list[str]) -> bool:
    """Return True when multi-sector or multi-segment collection already succeeded."""
    return any(
        warning.startswith("Collected EGX stocks from")
        and (" sectors" in warning or " market segments" in warning)
        for warning in warnings
    )


def _find_all_option_value(options: list[str]) -> str | None:
    """Pick the first dropdown option that means all/unfiltered."""
    for option in options:
        normalized = str(option).strip()
        lowered = normalized.lower()
        if lowered in ALL_OPTION_MARKERS:
            return normalized
        if normalized in {"All", "ALL", "All Sectors", "All Market Segments", "Select", "--"}:
            return normalized
    return None


def _find_traded_stocks_all_option(options: list[str]) -> str | None:
    """Pick the broadest Traded Stocks dropdown option when present."""
    all_option = _find_all_option_value(options)
    if all_option is not None:
        return all_option

    for option in options:
        normalized = str(option).strip()
        lowered = normalized.lower()
        if any(marker in lowered for marker in TRADED_STOCKS_INDEX_MARKERS):
            continue
        if normalized:
            return normalized
    return None


def _infer_select_dropdown_label(options: list[str], index: int) -> str:
    """Guess whether a select is sector, market segment, or traded stocks."""
    joined = " ".join(str(option).lower() for option in options)
    if any(marker in joined for marker in TRADED_STOCKS_INDEX_MARKERS):
        return "traded stocks"
    if "market segment" in joined:
        return "market segment"
    if "sector" in joined:
        return "sector"
    return f"select {index + 1}"


def _select_all_option_for_dropdown(options: list[str], label: str) -> str | None:
    """Return the best all/unfiltered option for a dropdown label."""
    if label == "traded stocks":
        return _find_traded_stocks_all_option(options)
    return _find_all_option_value(options)


def _get_select_option_values(select: object) -> list[str]:
    """Read option values/text from a select element."""
    return [entry["value"] or entry["label"] for entry in _get_select_options_detailed(select)]


def _get_select_options_detailed(select: object) -> list[dict[str, str]]:
    """Read option value/label pairs from a select element."""
    evaluate = getattr(select, "evaluate", None)
    if callable(evaluate):
        try:
            options = evaluate(
                """
                el => Array.from(el.options).map(option => ({
                    value: (option.value || '').trim(),
                    label: (option.textContent || '').replace(/\\s+/g, ' ').trim(),
                }))
                """
            )
            if isinstance(options, list):
                normalized: list[dict[str, str]] = []
                for option in options:
                    if isinstance(option, dict):
                        normalized.append(
                            {
                                "value": str(option.get("value") or "").strip(),
                                "label": str(option.get("label") or "").strip(),
                            }
                        )
                    elif isinstance(option, str):
                        token = option.strip()
                        normalized.append({"value": token, "label": token})
                return normalized
        except Exception:  # noqa: BLE001
            return []
    return []


def _normalize_selection_text(text: object) -> str:
    """Normalize dropdown labels/values for tolerant comparisons."""
    import re

    collapsed = re.sub(r"\s+", " ", str(text or "").strip())
    collapsed = collapsed.replace(" ,", ",").replace(", ", ",")
    return collapsed.casefold()


def _get_selected_option(select: object) -> dict[str, str]:
    """Read the currently selected option value and label from a dropdown."""
    evaluate = getattr(select, "evaluate", None)
    if not callable(evaluate):
        return {"value": "", "label": ""}
    try:
        selected = evaluate(
            """
            el => {
                const option = el.options[el.selectedIndex];
                if (!option) return { value: '', label: '' };
                return {
                    value: (option.value || '').trim(),
                    label: (option.textContent || '').replace(/\\s+/g, ' ').trim(),
                };
            }
            """
        )
        if isinstance(selected, dict):
            return {
                "value": str(selected.get("value") or "").strip(),
                "label": str(selected.get("label") or "").strip(),
            }
        if isinstance(selected, str):
            token = selected.strip()
            return {"value": token, "label": token}
    except Exception:  # noqa: BLE001
        return {"value": "", "label": ""}
    return {"value": "", "label": ""}


def _get_selected_dropdown_value(select: object) -> str:
    """Read the currently selected option text/value from a dropdown."""
    selected = _get_selected_option(select)
    return selected.get("label") or selected.get("value") or ""


def _option_selection_matches(
    requested_value: str,
    requested_label: str,
    selected_value: str,
    selected_label: str,
    *,
    option_pairs: list[dict[str, str]] | None = None,
) -> bool:
    """Return True when a dropdown selection matches the requested value or label."""
    req_value = _normalize_selection_text(requested_value)
    req_label = _normalize_selection_text(requested_label)
    sel_value = _normalize_selection_text(selected_value)
    sel_label = _normalize_selection_text(selected_label)

    if req_value and sel_value and req_value == sel_value:
        return True
    if req_label and sel_label and (
        req_label == sel_label or req_label in sel_label or sel_label in req_label
    ):
        return True

    pairs = option_pairs or []
    for option in pairs:
        opt_value = _normalize_selection_text(option.get("value", ""))
        opt_label = _normalize_selection_text(option.get("label", ""))

        if req_value and opt_value == req_value and sel_value and sel_value == opt_value:
            return True
        if req_value and opt_value == req_value and sel_label and (
            opt_label == sel_label or opt_label in sel_label or sel_label in opt_label
        ):
            return True
        if req_label and opt_label == req_label and sel_value and sel_value == opt_value:
            return True
        if req_label and opt_label == req_label and sel_label and (
            opt_label == sel_label or opt_label in sel_label or sel_label in opt_label
        ):
            return True

    return False


def _resolve_option_value_and_label(
    option_token: str,
    option_pairs: list[dict[str, str]],
) -> tuple[str, str]:
    """Resolve a dropdown token to its value and label."""
    token = str(option_token).strip()
    token_norm = _normalize_selection_text(token)
    for option in option_pairs:
        value = str(option.get("value") or "").strip()
        label = str(option.get("label") or "").strip()
        if token_norm in {
            _normalize_selection_text(value),
            _normalize_selection_text(label),
        }:
            return value or label, label or value
    return token, token


def _is_element_visible(element: object) -> bool:
    """Return True when a Playwright locator points at a visible element."""
    is_visible = getattr(element, "is_visible", None)
    if not callable(is_visible):
        return True
    try:
        return bool(is_visible())
    except Exception:  # noqa: BLE001
        return False


def _is_element_editable(element: object) -> bool:
    """Return True when an input-like element can be filled."""
    is_editable = getattr(element, "is_editable", None)
    if callable(is_editable):
        try:
            return bool(is_editable())
        except Exception:  # noqa: BLE001
            return False
    is_disabled = getattr(element, "is_disabled", None)
    if callable(is_disabled):
        try:
            return not bool(is_disabled())
        except Exception:  # noqa: BLE001
            return True
    return True


def _read_element_meta(element: object) -> dict[str, str]:
    """Read basic DOM metadata from a Playwright locator."""
    evaluate = getattr(element, "evaluate", None)
    if not callable(evaluate):
        return {}
    try:
        meta = evaluate(
            """
            el => ({
                id: el.id || '',
                name: el.name || '',
                tag: (el.tagName || '').toLowerCase(),
                href: el.getAttribute('href') || '',
                placeholder: el.placeholder || '',
                className: (el.className || '').toString(),
                text: (el.textContent || '').replace(/\\s+/g, ' ').trim(),
            })
            """
        )
        if isinstance(meta, dict):
            return {str(key): str(value) for key, value in meta.items()}
    except Exception:  # noqa: BLE001
        return {}
    return {}


def _is_excluded_control(element: object) -> bool:
    """Return True for header/hidden navigation controls that must be ignored."""
    meta = _read_element_meta(element)
    element_id = meta.get("id", "")
    if element_id == HEADER_SEARCH_INPUT_ID:
        return True
    if any(marker in element_id for marker in EXCLUDED_INPUT_ID_MARKERS):
        return True
    if meta.get("tag") == "title":
        return True
    href = meta.get("href", "").lower()
    if href.endswith("/en/stocks.aspx") or href.endswith("/en/stocks.aspx/"):
        return True
    return False


def first_visible(
    locator: object,
    description: str,
    warnings: list[str],
    *,
    require_editable: bool = False,
    warn_if_missing: bool = True,
) -> object | None:
    """Return the first visible (and optionally editable) locator match."""
    count_fn = getattr(locator, "count", None)
    if not callable(count_fn):
        if warn_if_missing:
            warnings.append(
                f"{RESET_FILTER_WARNING_PREFIX} no visible {description} found"
            )
        return None

    count = count_fn()
    if not isinstance(count, int) or count <= 0:
        if warn_if_missing:
            warnings.append(
                f"{RESET_FILTER_WARNING_PREFIX} no visible {description} found"
            )
        return None

    nth = getattr(locator, "nth", None)
    if not callable(nth):
        if warn_if_missing:
            warnings.append(
                f"{RESET_FILTER_WARNING_PREFIX} no visible {description} found"
            )
        return None

    for index in range(count):
        candidate = nth(index)
        if _is_excluded_control(candidate):
            continue
        if not _is_element_visible(candidate):
            continue
        if require_editable and not _is_element_editable(candidate):
            continue
        return candidate

    if warn_if_missing:
        warnings.append(
            f"{RESET_FILTER_WARNING_PREFIX} no visible {description} found"
        )
    return None


def _market_watch_root_locator(page: object) -> object | None:
    """Return a scoped locator for Today's Market Watch content when possible."""
    locator_fn = getattr(page, "locator", None)
    if not callable(locator_fn):
        return None

    for selector in MARKET_WATCH_ROOT_SELECTORS:
        scoped = locator_fn(selector)
        count_fn = getattr(scoped, "count", None)
        if not callable(count_fn):
            continue
        count = count_fn()
        if not isinstance(count, int) or count <= 0:
            continue
        first = getattr(scoped, "first", scoped)
        if not _is_element_visible(first):
            continue
        root_locator = getattr(first, "locator", None)
        if callable(root_locator):
            inner = root_locator("select, input[type='text'], table")
            inner_count_fn = getattr(inner, "count", None)
            if callable(inner_count_fn):
                inner_count = inner_count_fn()
                if not isinstance(inner_count, int) or inner_count <= 0:
                    continue
        return first
    return None


def _scoped_market_watch_locator(page: object, selector: str) -> object:
    """Build a locator scoped to market watch when available."""
    root = _market_watch_root_locator(page)
    locator_fn = getattr(page, "locator", None)
    if not callable(locator_fn):
        return page
    if root is None:
        return locator_fn(selector)
    root_locator = getattr(root, "locator", None)
    if callable(root_locator):
        return root_locator(selector)
    return locator_fn(selector)


def _safe_click(
    element: object,
    warnings: list[str],
    description: str,
) -> bool:
    """Scroll into view and click, falling back to a JS click when needed."""
    scroll_into_view = getattr(element, "scroll_into_view_if_needed", None)
    if callable(scroll_into_view):
        try:
            scroll_into_view(timeout=5_000)
        except Exception:  # noqa: BLE001
            pass

    wait_for = getattr(element, "wait_for", None)
    if callable(wait_for):
        try:
            wait_for(state="visible", timeout=5_000)
        except Exception:  # noqa: BLE001
            pass

    click = getattr(element, "click", None)
    if callable(click):
        try:
            click(timeout=5_000)
            return True
        except Exception:  # noqa: BLE001
            pass

    evaluate = getattr(element, "evaluate", None)
    if callable(evaluate):
        try:
            evaluate("el => el.click()")
            return True
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"{RESET_FILTER_WARNING_PREFIX} could not click {description}: {exc}"
            )
            return False

    warnings.append(
        f"{RESET_FILTER_WARNING_PREFIX} could not click {description}"
    )
    return False


def _safe_fill(element: object, value: str, warnings: list[str], description: str) -> bool:
    """Fill a visible input, clearing any existing value first."""
    scroll_into_view = getattr(element, "scroll_into_view_if_needed", None)
    if callable(scroll_into_view):
        try:
            scroll_into_view(timeout=5_000)
        except Exception:  # noqa: BLE001
            pass

    fill = getattr(element, "fill", None)
    if callable(fill):
        try:
            fill(value)
            return True
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"{RESET_FILTER_WARNING_PREFIX} could not fill {description}: {exc}"
            )
            return False

    warnings.append(
        f"{RESET_FILTER_WARNING_PREFIX} could not fill {description}"
    )
    return False


def _normalized_element_text(element: object) -> str:
    meta = _read_element_meta(element)
    return meta.get("text", "")


def _first_visible_matching_text(
    locator: object,
    text: str,
    warnings: list[str],
    description: str,
    *,
    exact: bool,
    warn_on_failure: bool = True,
) -> object | None:
    """Find the first visible clickable whose text matches exactly or partially."""
    count_fn = getattr(locator, "count", None)
    if not callable(count_fn):
        if warn_on_failure:
            warnings.append(
                f"{RESET_FILTER_WARNING_PREFIX} could not click {description}"
            )
        return None

    count = count_fn()
    if not isinstance(count, int) or count <= 0:
        if warn_on_failure:
            warnings.append(
                f"{RESET_FILTER_WARNING_PREFIX} could not click {description}"
            )
        return None

    nth = getattr(locator, "nth", None)
    if not callable(nth):
        if warn_on_failure:
            warnings.append(
                f"{RESET_FILTER_WARNING_PREFIX} could not click {description}"
            )
        return None

    for index in range(count):
        candidate = nth(index)
        if _is_excluded_control(candidate):
            continue
        if not _is_element_visible(candidate):
            continue
        label = _normalized_element_text(candidate)
        if exact and label != text:
            continue
        if not exact and text not in label:
            continue
        return candidate

    if warn_on_failure:
        warnings.append(
            f"{RESET_FILTER_WARNING_PREFIX} could not click {description}"
        )
    return None


def _click_visible_market_watch_tab(
    page: object,
    tab_text: str,
    warnings: list[str],
    *,
    exact: bool = True,
    warn_on_failure: bool = True,
) -> bool:
    """Click a visible tab inside the market watch area, ignoring hidden/header links."""
    evaluate = getattr(page, "evaluate", None)
    if callable(evaluate):
        try:
            clicked = evaluate(
                """
                ([text, exact]) => {
                    const isVisible = (el) => {
                        if (!el) return false;
                        if (el.closest('head, title')) return false;
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') return false;
                        if (Number(style.opacity) === 0) return false;
                        const rect = el.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) return false;
                        return true;
                    };
                    const isExcluded = (el) => {
                        const id = el.id || '';
                        if (id.includes('_H_') || id.includes('txtSearchAll')) return true;
                        const href = (el.getAttribute('href') || '').toLowerCase();
                        if (href.endsWith('/en/stocks.aspx')) return true;
                        if ((el.tagName || '').toLowerCase() === 'title') return true;
                        return false;
                    };
                    const nodes = document.querySelectorAll(
                        'a, button, span, li, td, div[role=\"tab\"]'
                    );
                    for (const el of nodes) {
                        if (!isVisible(el) || isExcluded(el)) continue;
                        const label = (el.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (exact ? label !== text : !label.includes(text)) continue;
                        el.scrollIntoView({block: 'center'});
                        el.click();
                        return true;
                    }
                    return false;
                }
                """,
                [tab_text, exact],
            )
            if clicked:
                return True
        except Exception:  # noqa: BLE001
            pass

    candidates = _scoped_market_watch_locator(
        page,
        "a, button, span, li, td, div[role='tab']",
    )
    element = _first_visible_matching_text(
        candidates,
        tab_text,
        warnings,
        f"{tab_text} tab",
        exact=exact,
        warn_on_failure=warn_on_failure,
    )
    if element is None:
        return False
    return _safe_click(element, warnings, f"{tab_text} tab")


def _find_visible_company_name_input(
    page: object,
    warnings: list[str],
    *,
    warn_if_missing: bool = True,
) -> object | None:
    """Locate the visible Company filter text input inside market watch filters."""
    candidates = _scoped_market_watch_locator(page, "input[type='text']")
    count_fn = getattr(candidates, "count", None)
    if not callable(count_fn):
        return None

    count = count_fn()
    if not isinstance(count, int):
        return None

    nth = getattr(candidates, "nth", None)
    if not callable(nth):
        return None

    best_match: object | None = None
    for index in range(count):
        candidate = nth(index)
        if _is_excluded_control(candidate):
            continue
        if not _is_element_visible(candidate):
            continue
        if not _is_element_editable(candidate):
            continue

        meta = _read_element_meta(candidate)
        placeholder = meta.get("placeholder", "").lower()
        element_id = meta.get("id", "").lower()
        name = meta.get("name", "").lower()
        class_name = meta.get("className", "").lower()

        if any(marker in placeholder for marker in COMPANY_NAME_PLACEHOLDER_MARKERS):
            return candidate
        if "company" in element_id or "company" in name:
            return candidate
        if "normaltextbox" in class_name and best_match is None:
            best_match = candidate

    if best_match is not None:
        return best_match

    if warn_if_missing:
        warnings.append(
            f"{RESET_FILTER_WARNING_PREFIX} no visible Company Name filter input found"
        )
    return None


def _visible_market_watch_selects(page: object) -> list[object]:
    """Return visible select elements scoped to the market watch filter panel."""
    candidates = _scoped_market_watch_locator(page, "select")
    count_fn = getattr(candidates, "count", None)
    if not callable(count_fn):
        return []

    count = count_fn()
    if not isinstance(count, int) or count <= 0:
        return []

    nth = getattr(candidates, "nth", None)
    if not callable(nth):
        return []

    visible: list[object] = []
    for index in range(count):
        candidate = nth(index)
        if _is_excluded_control(candidate):
            continue
        if not _is_element_visible(candidate):
            continue
        visible.append(candidate)
    return visible


def _click_sector_filter_tab(page: object, warnings: list[str]) -> None:
    """Open the Sector filter tab inside market watch when present."""
    _activate_filter_tab(page, SECTOR_TAB_TEXT, warnings)


def _is_stocks_trading_data_active(page: object) -> bool:
    """Return True when Stocks > Trading Data appears to be the active view."""
    nav = _detect_navigation_state(page)
    main_tab = str(nav.get("main_tab", "")).strip().lower()
    sub_tab = str(nav.get("sub_tab", "")).strip().lower()
    return main_tab == "stocks" and "trading data" in sub_tab


def _is_filter_tab_active(page: object, tab_text: str) -> bool:
    """Return True when a market-watch filter tab appears selected."""
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return False

    try:
        active = evaluate(
            """
            (tabText) => {
                const nodes = Array.from(document.querySelectorAll(
                    'a, button, span, li, td, div[role=\"tab\"]'
                ));
                for (const node of nodes) {
                    const text = (node.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (text !== tabText) continue;
                    const cls = (node.className || '').toString().toLowerCase();
                    const selected = node.getAttribute('aria-selected') === 'true';
                    if (cls.includes('active') || cls.includes('selected') || selected) {
                        return true;
                    }
                }
                return false;
            }
            """,
            tab_text,
        )
        return bool(active)
    except Exception:  # noqa: BLE001
        return False


def _activate_filter_tab(
    page: object,
    tab_text: str,
    warnings: list[str],
    *,
    warn_on_failure: bool = False,
) -> bool:
    """Activate a market-watch filter tab when it is not already selected."""
    if _is_filter_tab_active(page, tab_text):
        return True

    clicked = _click_visible_market_watch_tab(
        page,
        tab_text,
        warnings,
        exact=True,
        warn_on_failure=warn_on_failure,
    )
    if clicked:
        _wait_ms(page, 500)
    return clicked


def _trigger_select_change_events(select: object) -> None:
    """Dispatch input/change events after programmatic dropdown selection."""
    evaluate = getattr(select, "evaluate", None)
    if not callable(evaluate):
        return
    try:
        evaluate(
            """
            el => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """
        )
    except Exception:  # noqa: BLE001
        pass


def _print_sector_selection_verification(
    requested_value: str,
    requested_label: str,
    selected_value: str,
    selected_label: str,
    *,
    matched: bool,
) -> None:
    """Print sector dropdown verification details for live debugging."""
    print(
        "Sector selection verification: "
        f"requested value={requested_value!r}, requested label={requested_label!r}, "
        f"selected value={selected_value!r}, selected label={selected_label!r}, "
        f"match={'YES' if matched else 'NO'}"
    )


def _table_fingerprint(df: pd.DataFrame) -> str:
    """Build a stable fingerprint for comparing table contents across filter changes."""
    if df.empty:
        return "rows:0"

    name_column = _find_name_column(df)
    if name_column is None:
        return f"rows:{len(df)}"

    names = (
        df[name_column]
        .astype(str)
        .str.strip()
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
        .dropna()
        .head(10)
        .tolist()
    )
    return f"rows:{len(df)}|names:{'|'.join(names)}"


def _first_company_names(df: pd.DataFrame, count: int = 3) -> list[str]:
    """Return the first company names from a stocks table."""
    name_column = _find_name_column(df)
    if name_column is None or df.empty:
        return []

    return (
        df[name_column]
        .astype(str)
        .str.strip()
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
        .dropna()
        .head(count)
        .tolist()
    )


def _table_fingerprint_from_page(page: object) -> str:
    """Extract a table fingerprint from the current page DOM without waiting."""
    html = _get_page_html(page)
    if not html:
        return "rows:0"
    table = extract_stocks_table_from_html(html)
    if table is None or table.empty:
        return "rows:0"
    return _table_fingerprint(table)


def _find_visible_select_for_filter(
    page: object,
    filter_label: str,
) -> tuple[object | None, list[str]]:
    """Locate the visible dropdown for an active filter tab (sector/market segment)."""
    visible_selects = _visible_market_watch_selects(page)
    if not visible_selects:
        return None, []

    target = filter_label.strip().lower()
    candidates: list[tuple[object, list[str], int, bool]] = []
    for index, select in enumerate(visible_selects):
        options = _get_select_option_values(select)
        label = _infer_select_dropdown_label(options, index)
        valid_options = [
            option for option in options if not _is_skippable_sector_option(option)
        ]
        if len(valid_options) < 1 and target != "market segment":
            continue

        candidates.append(
            (
                select,
                options,
                len(valid_options),
                label == target,
            )
        )

    if not candidates:
        return None, []

    for select, options, _, is_match in candidates:
        if is_match:
            return select, options

    if len(candidates) == 1:
        return candidates[0][0], candidates[0][1]

    candidates.sort(key=lambda item: item[2], reverse=True)
    return candidates[0][0], candidates[0][1]


def _apply_filter_search(
    page: object,
    warnings: list[str],
    *,
    deduper: WarningDeduper | None = None,
    phase: str = "filter",
) -> None:
    """Click visible Go/Search/Submit controls after changing a filter."""
    _submit_search_after_clear(page, warnings, deduper=deduper, phase=phase)


def _select_and_verify_dropdown_option(
    page: object,
    select: object,
    requested_value: str,
    requested_label: str,
    warnings: list[str],
    *,
    option_pairs: list[dict[str, str]] | None = None,
) -> tuple[bool, dict[str, str]]:
    """Select a dropdown option and verify using both value and label."""
    if not _select_dropdown_option(
        select,
        requested_value,
        warnings,
        fallback_label=requested_label,
    ):
        return False, _get_selected_option(select)

    _trigger_select_change_events(select)
    _wait_ms(page, 400)
    selected = _get_selected_option(select)
    matched = _option_selection_matches(
        requested_value,
        requested_label,
        selected.get("value", ""),
        selected.get("label", ""),
        option_pairs=option_pairs,
    )
    _print_sector_selection_verification(
        requested_value,
        requested_label,
        selected.get("value", ""),
        selected.get("label", ""),
        matched=matched,
    )
    if not matched:
        warnings.append(
            "Sector selection verification: "
            f"requested value={requested_value!r}, requested label={requested_label!r}, "
            f"selected value={selected.get('value', '')!r}, "
            f"selected label={selected.get('label', '')!r}, "
            "match=NO"
        )
        return False, selected

    return True, selected


def _clear_company_filter_if_visible(
    page: object,
    warnings: list[str],
    *,
    deduper: WarningDeduper | None = None,
) -> None:
    """Activate Company tab and clear the company-name filter when visible."""
    company_tab_seen = _activate_filter_tab(page, COMPANY_TAB_TEXT, warnings)
    if not company_tab_seen:
        if deduper is not None:
            deduper.add_once(
                warnings,
                "company_filter_unavailable",
                COMPANY_FILTER_UNAVAILABLE_WARNING,
            )
        elif COMPANY_FILTER_UNAVAILABLE_WARNING not in warnings:
            warnings.append(COMPANY_FILTER_UNAVAILABLE_WARNING)
        return

    company_input = _find_visible_company_name_input(
        page,
        warnings,
        warn_if_missing=False,
    )
    if company_input is None:
        if deduper is not None:
            deduper.add_once(
                warnings,
                "company_filter_unavailable",
                COMPANY_FILTER_UNAVAILABLE_WARNING,
            )
        elif COMPANY_FILTER_UNAVAILABLE_WARNING not in warnings:
            warnings.append(COMPANY_FILTER_UNAVAILABLE_WARNING)
        return

    if not _safe_fill(company_input, "", warnings, "Company Name filter"):
        return

    _apply_filter_search(page, warnings, deduper=deduper, phase="company")
    _wait_for_table_refresh(page, warnings)


def _reset_market_segment_filter(
    page: object,
    warnings: list[str],
    *,
    deduper: WarningDeduper | None = None,
) -> None:
    """Activate Market Segment tab and reset its dropdown to the broadest option."""
    _activate_filter_tab(page, MARKET_SEGMENT_TAB_TEXT, warnings)
    _wait_ms(page, 300)

    segment_select, options = _find_visible_select_for_filter(page, "market segment")
    if segment_select is None or not options:
        return

    _reset_select_dropdown(page, segment_select, 0, warnings, deduper=deduper)
    _apply_filter_search(page, warnings, deduper=deduper, phase="market_segment_reset")
    _wait_for_table_refresh(page, warnings)


def _prepare_multi_sector_filters(
    page: object,
    warnings: list[str],
    *,
    deduper: WarningDeduper | None = None,
) -> None:
    """Reset company and market-segment filters before sector iteration."""
    _clear_company_filter_if_visible(page, warnings, deduper=deduper)
    _reset_market_segment_filter(page, warnings, deduper=deduper)


def _print_sector_collection_diagnostics(
    filter_kind: str,
    filter_label: str,
    selected_value: str,
    row_count: int,
    sample_names: list[str],
    fingerprint_before: str,
    fingerprint_after: str,
) -> None:
    """Print per-filter diagnostics for live verification."""
    changed = "YES" if fingerprint_before != fingerprint_after else "NO"
    sample = ", ".join(sample_names) if sample_names else "(none)"
    kind_label = _filter_kind_display_name(filter_kind)
    print(
        "EGX filter collection: "
        f"{kind_label.lower()}={filter_label!r} "
        f"selected={selected_value!r} "
        f"rows={row_count} "
        f"sample=[{sample}] "
        f"fingerprint_before={fingerprint_before} "
        f"fingerprint_after={fingerprint_after} "
        f"changed={changed}"
    )


def _audit_control_candidates(page: object) -> dict[str, object]:
    """Collect visibility metadata for market-watch controls."""
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return {}

    try:
        result = evaluate(
            """
            () => {
                const isVisible = (el) => {
                    if (!el) return false;
                    if (el.closest('head, title')) return false;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const summarizeClickable = (text) => {
                    const nodes = Array.from(document.querySelectorAll(
                        'a, button, span, li, td, div[role=\"tab\"], title'
                    ));
                    const matches = nodes.filter((el) => {
                        const label = (el.textContent || '').replace(/\\s+/g, ' ').trim();
                        return label === text || label.includes(text);
                    });
                    return {
                        total: matches.length,
                        visible: matches.filter(isVisible).length,
                    };
                };
                const inputs = Array.from(document.querySelectorAll('input[type=\"text\"]')).map((el) => ({
                    id: el.id || '',
                    name: el.name || '',
                    placeholder: el.placeholder || '',
                    visible: isVisible(el),
                }));
                const selects = Array.from(document.querySelectorAll('select')).map((el) => ({
                    id: el.id || '',
                    name: el.name || '',
                    visible: isVisible(el),
                    options: el.options ? el.options.length : 0,
                }));
                return {
                    stocks: summarizeClickable('Stocks'),
                    trading_data: summarizeClickable('Trading Data'),
                    inputs,
                    selects,
                };
            }
            """
        )
        if isinstance(result, dict):
            return result
    except Exception:  # noqa: BLE001
        return {}
    return {}


def print_control_detection_diagnostics(page: object) -> None:
    """Print market-watch control visibility diagnostics."""
    audit = _audit_control_candidates(page)
    nav = _detect_navigation_state(page)

    print("EGX control diagnostics:")
    stocks = audit.get("stocks", {})
    trading = audit.get("trading_data", {})
    print(
        "- Stocks matches: "
        f"total={stocks.get('total', 0)} visible={stocks.get('visible', 0)}"
    )
    print(
        "- Trading Data matches: "
        f"total={trading.get('total', 0)} visible={trading.get('visible', 0)}"
    )
    print(f"- Active main tab: {nav.get('main_tab', 'unknown')}")
    print(f"- Active sub tab: {nav.get('sub_tab', 'unknown')}")

    inputs = audit.get("inputs", [])
    print(f"- Text inputs found: {len(inputs) if isinstance(inputs, list) else 0}")
    if isinstance(inputs, list):
        for item in inputs[:8]:
            if not isinstance(item, dict):
                continue
            print(
                "  - input "
                f"id={item.get('id', '')!r} "
                f"name={item.get('name', '')!r} "
                f"placeholder={item.get('placeholder', '')!r} "
                f"visible={item.get('visible', False)}"
            )

    selects = audit.get("selects", [])
    print(f"- Selects found: {len(selects) if isinstance(selects, list) else 0}")
    if isinstance(selects, list):
        for item in selects[:8]:
            if not isinstance(item, dict):
                continue
            print(
                "  - select "
                f"id={item.get('id', '')!r} "
                f"name={item.get('name', '')!r} "
                f"visible={item.get('visible', False)} "
                f"options={item.get('options', 0)}"
            )


def ensure_stocks_trading_data_view(page: object, warnings: list[str]) -> None:
    """Navigate the EGX prices page to visible Stocks > Trading Data tabs."""
    page_url = getattr(page, "url", "")
    if page_url and not is_egx_prices_page_url(str(page_url)):
        warnings.append(
            f"{RESET_FILTER_WARNING_PREFIX} page is not the EGX prices page"
        )

    if _is_stocks_trading_data_active(page):
        print(ALREADY_ON_STOCKS_TRADING_DATA_MESSAGE)
        return

    stocks_clicked = _click_visible_market_watch_tab(
        page,
        STOCKS_TAB_TEXT,
        warnings,
        exact=True,
        warn_on_failure=False,
    )
    if stocks_clicked:
        _wait_ms(page, 800)
        wait_for_egx_update_complete(page, warnings=warnings)

    trading_clicked = _click_visible_market_watch_tab(
        page,
        TRADING_DATA_TAB_TEXT,
        warnings,
        exact=True,
        warn_on_failure=False,
    )
    if trading_clicked:
        _wait_ms(page, 800)
        wait_for_egx_update_complete(page, warnings=warnings)

    if not stocks_clicked and not trading_clicked and _is_stocks_trading_data_active(page):
        print(ALREADY_ON_STOCKS_TRADING_DATA_MESSAGE)


def _scroll_to_stocks_table_area(page: object) -> None:
    """Scroll toward likely stock table elements, including below-the-fold tables."""
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return

    try:
        evaluate(
            """
            () => {
                const keywords = ['name', 'last price', 'open', 'high', 'low', 'volume'];
                const tables = Array.from(document.querySelectorAll('table'));
                let best = null;
                let bestScore = -1;
                for (const table of tables) {
                    const text = (table.innerText || '').toLowerCase();
                    let score = 0;
                    for (const keyword of keywords) {
                        if (text.includes(keyword)) score += 1;
                    }
                    if (score > bestScore) {
                        bestScore = score;
                        best = table;
                    }
                }
                if (best) {
                    best.scrollIntoView({behavior: 'instant', block: 'center'});
                    return;
                }
                window.scrollBy(0, Math.max(700, window.innerHeight * 0.75));
            }
            """
        )
    except Exception:  # noqa: BLE001
        pass


def _get_page_html(page: object) -> str | None:
    content = getattr(page, "content", None)
    if not callable(content):
        return None
    try:
        return content()
    except Exception:  # noqa: BLE001
        return None


def _detect_navigation_state(page: object) -> dict[str, str]:
    """Best-effort detection of selected main/sub tabs on the EGX prices page."""
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return {"main_tab": "unknown", "sub_tab": "unknown"}

    try:
        result = evaluate(
            """
            () => {
                const pick = (labels) => {
                    for (const label of labels) {
                        const nodes = Array.from(document.querySelectorAll('a, button, span, li, div'));
                        for (const node of nodes) {
                            const text = (node.textContent || '').trim();
                            if (!text) continue;
                            if (text.toLowerCase() === label.toLowerCase()) {
                                const style = window.getComputedStyle(node);
                                const cls = (node.className || '').toString().toLowerCase();
                                const active = cls.includes('active')
                                    || cls.includes('selected')
                                    || node.getAttribute('aria-selected') === 'true';
                                if (active) return text;
                            }
                        }
                    }
                    return '';
                };
                return {
                    main_tab: pick(['Stocks', 'Indices', 'Company']) || 'unknown',
                    sub_tab: pick(['Trading Data', 'Company', 'Indices']) || 'unknown',
                };
            }
            """
        )
        if isinstance(result, dict):
            return {
                "main_tab": str(result.get("main_tab") or "unknown"),
                "sub_tab": str(result.get("sub_tab") or "unknown"),
            }
    except Exception:  # noqa: BLE001
        pass
    return {"main_tab": "unknown", "sub_tab": "unknown"}


def _detect_filter_values(page: object) -> dict[str, str]:
    """Read current Company Name / Sector / Market Segment filter values if present."""
    filters = {
        "company_name": "",
        "sector": "",
        "market_segment": "",
    }

    company_input = _find_visible_company_name_input(page, warnings=[])
    if company_input is not None:
        input_value = getattr(company_input, "input_value", None)
        if callable(input_value):
            try:
                filters["company_name"] = str(input_value() or "").strip()
            except Exception:  # noqa: BLE001
                pass

    visible_selects = _visible_market_watch_selects(page)
    for index, select in enumerate(visible_selects):
        options = _get_select_option_values(select)
        label = _infer_select_dropdown_label(options, index)
        selected = ""
        evaluate = getattr(select, "evaluate", None)
        if callable(evaluate):
            try:
                selected = str(
                    evaluate(
                        "el => el.options[el.selectedIndex]"
                        " ? (el.options[el.selectedIndex].text || '') : ''"
                    )
                    or ""
                ).strip()
            except Exception:  # noqa: BLE001
                selected = ""
        if label == "sector":
            filters["sector"] = selected
        elif label == "market segment":
            filters["market_segment"] = selected

    return filters


def print_table_detection_diagnostics(page: object) -> None:
    """Print debug details when EGX stock table extraction fails."""
    page_url = getattr(page, "url", "unknown")
    nav = _detect_navigation_state(page)
    filters = _detect_filter_values(page)
    html = _get_page_html(page) or ""
    summaries = summarize_tables_in_html(html)

    print("EGX table diagnostics:")
    print(f"- URL: {page_url}")
    print(f"- Main tab: {nav.get('main_tab', 'unknown')}")
    print(f"- Sub tab: {nav.get('sub_tab', 'unknown')}")
    print(f"- Tables in DOM: {len(summaries)}")
    for summary in summaries:
        columns = summary.get("columns", [])
        preview = ", ".join(str(item) for item in columns[:8])
        if len(columns) > 8:
            preview += ", ..."
        print(
            f"  - table {summary.get('index')}: rows={summary.get('rows')} "
            f"score={summary.get('score')} columns=[{preview}]"
        )
    print(
        "- Filters: "
        f"company_name={filters.get('company_name', '')!r} "
        f"sector={filters.get('sector', '')!r} "
        f"market_segment={filters.get('market_segment', '')!r}"
    )
    print_control_detection_diagnostics(page)


def wait_for_stocks_table_in_dom(
    page: object,
    timeout_ms: int = EGX_STOCKS_TABLE_WAIT_TIMEOUT_MS,
    warnings: list[str] | None = None,
) -> pd.DataFrame | None:
    """Poll page HTML until a stocks-like table appears in the DOM."""
    warning_sink = warnings if warnings is not None else []
    deadline = time.monotonic() + (timeout_ms / 1000)

    while time.monotonic() < deadline:
        wait_for_egx_update_complete(
            page,
            timeout_ms=min(10_000, int((deadline - time.monotonic()) * 1000)),
            warnings=warning_sink,
        )
        _scroll_to_stocks_table_area(page)
        html = _get_page_html(page)
        if html:
            table = extract_stocks_table_from_html(html)
            if table is not None and not table.empty:
                return table
        _wait_ms(page, 1_000)

    return None


def _clear_text_search_inputs(page: object, warnings: list[str]) -> None:
    """Clear the visible Company Name filter input inside market watch filters."""
    _clear_company_filter_if_visible(page, warnings)


def _reset_select_dropdown(
    page: object,
    select: object,
    index: int,
    warnings: list[str],
    *,
    deduper: WarningDeduper | None = None,
) -> None:
    """Reset one select dropdown to its all/unfiltered option when possible."""
    options = _get_select_option_values(select)
    label = _infer_select_dropdown_label(options, index)
    all_value = _select_all_option_for_dropdown(options, label)
    if all_value is None:
        message = f"{RESET_FILTER_WARNING_PREFIX} no All option found for {label} dropdown"
        if deduper is not None:
            deduper.add_once(warnings, f"no_all_option:{label}", message)
        else:
            warnings.append(message)
        return

    select_option = getattr(select, "select_option", None)
    if not callable(select_option):
        return

    try:
        if all_value == "":
            select_option(label=options[0] if options else "")
        else:
            select_option(value=all_value)
    except Exception as exc:  # noqa: BLE001
        label = _infer_select_dropdown_label(options, index)
        warnings.append(
            f"{RESET_FILTER_WARNING_PREFIX} could not reset {label} dropdown: {exc}"
        )


def _reset_select_dropdowns(page: object, warnings: list[str]) -> None:
    """Reset visible market-segment dropdown filters when present."""
    _reset_market_segment_filter(page, warnings)


def _submit_search_after_clear(
    page: object,
    warnings: list[str],
    *,
    deduper: WarningDeduper | None = None,
    phase: str = "filter",
) -> None:
    """Click visible search/submit controls after clearing text filters."""
    submit_labels = ("Go", "Search", "Submit")
    scope = _market_watch_root_locator(page)
    search_root = scope if scope is not None else page
    silent_warnings: list[str] = []

    try:
        get_by_role = getattr(search_root, "get_by_role", None)
        if callable(get_by_role):
            for label in submit_labels:
                button = get_by_role("button", name=label)
                visible_button = first_visible(
                    button,
                    f"{label} button",
                    silent_warnings,
                    warn_if_missing=False,
                )
                if visible_button is not None and _safe_click(
                    visible_button,
                    silent_warnings,
                    f"{label} button",
                ):
                    return

        locator_fn = getattr(search_root, "locator", None)
        if callable(locator_fn):
            for label in submit_labels:
                control = locator_fn(f"input[type='submit'][value='{label}']")
                visible_control = first_visible(
                    control,
                    f"{label} submit",
                    silent_warnings,
                    warn_if_missing=False,
                )
                if visible_control is not None and _safe_click(
                    visible_control,
                    silent_warnings,
                    f"{label} submit",
                ):
                    return

        if deduper is not None:
            deduper.add_once(
                warnings,
                f"submit_button:{phase}",
                NO_VISIBLE_FILTER_SUBMIT_WARNING,
            )
        else:
            warnings.append(NO_VISIBLE_FILTER_SUBMIT_WARNING)
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"{RESET_FILTER_WARNING_PREFIX} could not submit search after clear: {exc}"
        )


def _wait_for_table_refresh(page: object, warnings: list[str]) -> None:
    """Wait for the stocks table to settle after filter changes."""
    wait_for_egx_update_complete(page, warnings=warnings)


def _is_updating_visible(page: object) -> bool:
    """Return True when the EGX Updating overlay text is visible."""
    get_by_text = getattr(page, "get_by_text", None)
    if not callable(get_by_text):
        return False

    for label in ("Updating...", "Updating"):
        try:
            locator = get_by_text(label, exact=False)
            count_fn = getattr(locator, "count", None)
            if not callable(count_fn):
                continue
            count = count_fn()
            if not isinstance(count, int) or count <= 0:
                continue

            first = getattr(locator, "first", locator)
            is_visible = getattr(first, "is_visible", None)
            if callable(is_visible):
                try:
                    if is_visible():
                        return True
                except Exception:  # noqa: BLE001
                    return True
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _wait_for_updating_to_disappear(page: object, timeout_ms: int) -> bool:
    """Wait until the Updating overlay is no longer visible."""
    get_by_text = getattr(page, "get_by_text", None)
    if callable(get_by_text):
        for label in ("Updating...", "Updating"):
            try:
                locator = get_by_text(label, exact=False)
                wait_for = getattr(locator, "wait_for", None)
                if callable(wait_for):
                    wait_for(state="hidden", timeout=timeout_ms)
                    return True
            except Exception:  # noqa: BLE001
                pass

    wait_for_function = getattr(page, "wait_for_function", None)
    if callable(wait_for_function):
        try:
            wait_for_function(
                "() => !document.body || !document.body.innerText.includes('Updating')",
                timeout=timeout_ms,
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    return True


def _press_escape(page: object) -> None:
    """Press Escape on the page keyboard when available."""
    keyboard = getattr(page, "keyboard", None)
    if keyboard is None:
        return
    press = getattr(keyboard, "press", None)
    if callable(press):
        try:
            press("Escape")
        except Exception:  # noqa: BLE001
            pass


def _wait_ms(page: object, delay_ms: int) -> None:
    """Sleep for a fixed delay using Playwright's wait_for_timeout when available."""
    wait_for_timeout = getattr(page, "wait_for_timeout", None)
    if callable(wait_for_timeout):
        try:
            wait_for_timeout(delay_ms)
        except Exception:  # noqa: BLE001
            pass


def _wait_for_visible_table(page: object, timeout_ms: int) -> bool:
    """Wait until at least one table element is present."""
    wait_for_selector = getattr(page, "wait_for_selector", None)
    if not callable(wait_for_selector):
        return _has_extractable_table(page)

    try:
        wait_for_selector("table", timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001
        return False


def _has_extractable_table(page: object) -> bool:
    """Return True when page HTML currently contains a stocks table."""
    html = _get_page_html(page)
    if not html:
        return False
    table = extract_stocks_table_from_html(html)
    return table is not None and not table.empty


def wait_for_egx_update_complete(
    page: object,
    timeout_ms: int = EGX_UPDATE_OVERLAY_TIMEOUT_MS,
    warnings: list[str] | None = None,
) -> bool:
    """Wait for EGX Updating overlay to clear and a stocks table to appear."""
    warning_sink = warnings if warnings is not None else []
    recovery_attempted = False

    if _is_updating_visible(page):
        if not _wait_for_updating_to_disappear(page, timeout_ms):
            warning_sink.append(EGX_UPDATE_OVERLAY_TIMEOUT_WARNING)
            _press_escape(page)
            _wait_ms(page, EGX_UPDATE_RECOVERY_WAIT_MS)
            recovery_attempted = True

    if _wait_for_visible_table(page, timeout_ms):
        return True

    if not recovery_attempted:
        warning_sink.append(EGX_UPDATE_OVERLAY_TIMEOUT_WARNING)
        _press_escape(page)
        _wait_ms(page, EGX_UPDATE_RECOVERY_WAIT_MS)

    return _has_extractable_table(page)


def prepare_full_market_stocks_view(page: object) -> list[str]:
    """Prepare EGX Stocks > Trading Data view and reset filters before reading."""
    warnings: list[str] = []
    deduper = WarningDeduper()

    ensure_stocks_trading_data_view(page, warnings)
    _prepare_multi_sector_filters(page, warnings, deduper=deduper)
    _scroll_to_stocks_table_area(page)
    _wait_for_table_refresh(page, warnings)
    return filter_report_warnings(warnings)


def _is_skippable_sector_option(option: str) -> bool:
    """Return True for placeholder dropdown options that should not be collected."""
    normalized = str(option).strip()
    if not normalized:
        return True
    lowered = normalized.lower()
    if lowered in ALL_OPTION_MARKERS:
        return True
    if normalized in {"Select", "--", "..."}:
        return True
    return False


def _find_name_column(df: pd.DataFrame) -> str | None:
    """Return the stocks table name column when present."""
    for column in df.columns:
        if _normalize_header(column) == "name":
            return str(column)
    return None


def find_sector_select_with_options(page: object) -> tuple[object | None, list[str]]:
    """Locate the visible sector filter dropdown and return it plus option values."""
    try:
        _activate_filter_tab(page, SECTOR_TAB_TEXT, [])
        _wait_ms(page, 300)
        return _find_visible_select_for_filter(page, "sector")
    except Exception:  # noqa: BLE001
        return None, []


def _select_dropdown_option(
    select: object,
    option_value: str,
    warnings: list[str],
    *,
    fallback_label: str | None = None,
) -> bool:
    """Select one dropdown option by value, falling back to label when needed."""
    select_option = getattr(select, "select_option", None)
    if not callable(select_option):
        return False

    label = fallback_label or option_value
    try:
        select_option(value=option_value)
        return True
    except Exception:  # noqa: BLE001
        pass

    try:
        select_option(label=label)
        return True
    except Exception:  # noqa: BLE001
        pass

    if label != option_value:
        try:
            select_option(label=option_value)
            return True
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"{RESET_FILTER_WARNING_PREFIX} could not select sector option "
                f"value={option_value!r} label={label!r}: {exc}"
            )
            return False

    warnings.append(
        f"{RESET_FILTER_WARNING_PREFIX} could not select sector option "
        f"value={option_value!r} label={label!r}"
    )
    return False


def tag_rows_with_sector(df: pd.DataFrame, sector_name: str) -> pd.DataFrame:
    """Tag extracted rows with the sector used for collection."""
    tagged = df.copy()
    sector_column: str | None = None
    for column in tagged.columns:
        if _normalize_header(column) == "sector":
            sector_column = str(column)
            break

    if sector_column is None:
        tagged["Sector"] = sector_name
        return tagged

    sector_series = tagged[sector_column].astype(str).str.strip()
    empty_mask = sector_series.eq("") | sector_series.str.lower().isin({"nan", "none"})
    tagged.loc[empty_mask, sector_column] = sector_name
    return tagged


def merge_sector_stock_rows(sector_frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate sector tables into one combined frame."""
    if not sector_frames:
        return pd.DataFrame()
    return pd.concat(sector_frames, ignore_index=True)


def dedupe_stocks_by_name(df: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate combined sector rows by stock Name."""
    name_column = _find_name_column(df)
    if name_column is None:
        return df.copy()
    return df.drop_duplicates(subset=[name_column], keep="first").reset_index(drop=True)


def extract_stocks_table_from_page(
    page: object,
    warnings: list[str] | None = None,
) -> pd.DataFrame | None:
    """Extract the stocks table from page DOM HTML (not viewport visibility)."""
    warning_sink = warnings if warnings is not None else []
    _scroll_to_stocks_table_area(page)
    wait_for_egx_update_complete(page, warnings=warning_sink)

    html = _get_page_html(page)
    if html:
        table = extract_stocks_table_from_html(html)
        if table is not None and not table.empty:
            return table

    return wait_for_stocks_table_in_dom(page, warnings=warning_sink)


def collect_stocks_by_all_sectors(
    page: object,
) -> tuple[pd.DataFrame | None, list[str], bool]:
    """Collect stocks by iterating every sector dropdown option and merging results."""
    warnings: list[str] = []
    deduper = WarningDeduper()
    _prepare_multi_sector_filters(page, warnings, deduper=deduper)

    _activate_filter_tab(page, SECTOR_TAB_TEXT, warnings)
    sector_select, sector_options = find_sector_select_with_options(page)
    if sector_select is None or not sector_options:
        print_control_detection_diagnostics(page)
        return None, filter_report_warnings(warnings), False

    valid_options = [
        option for option in sector_options if not _is_skippable_sector_option(option)
    ]
    if not valid_options:
        return None, filter_report_warnings(warnings), False

    option_pairs = _get_select_options_detailed(sector_select)
    filter_kind = _describe_filter_kind_for_options(
        [entry.get("label") or entry.get("value", "") for entry in option_pairs]
        or sector_options
    )
    sector_frames: list[pd.DataFrame] = []
    sectors_with_rows = 0
    seen_fingerprints: set[str] = set()
    baseline_fingerprint = _table_fingerprint_from_page(page)

    for option_token in valid_options:
        requested_value, requested_label = _resolve_option_value_and_label(
            option_token,
            option_pairs,
        )
        filter_name = requested_label or requested_value

        _activate_filter_tab(page, SECTOR_TAB_TEXT, warnings)
        _wait_ms(page, 300)
        sector_select, _ = _find_visible_select_for_filter(page, "sector")
        if sector_select is None:
            warnings.append(
                f"{RESET_FILTER_WARNING_PREFIX} "
                f"{_filter_kind_display_name(filter_kind).lower()} dropdown not visible for "
                f"{filter_name!r}"
            )
            continue

        active_option_pairs = _get_select_options_detailed(sector_select)
        filter_kind = _describe_filter_kind_for_options(
            [entry.get("label") or entry.get("value", "") for entry in active_option_pairs]
            or sector_options
        )
        requested_value, requested_label = _resolve_option_value_and_label(
            option_token,
            active_option_pairs,
        )
        filter_name = requested_label or requested_value

        fingerprint_before = _table_fingerprint_from_page(page)
        if fingerprint_before == "rows:0":
            fingerprint_before = baseline_fingerprint

        selected_ok, selected = _select_and_verify_dropdown_option(
            page,
            sector_select,
            requested_value,
            requested_label,
            warnings,
            option_pairs=active_option_pairs,
        )
        if not selected_ok:
            continue

        _apply_filter_search(page, warnings, deduper=deduper, phase="sector_collect")
        update_ready = wait_for_egx_update_complete(page, warnings=warnings)
        table = extract_stocks_table_from_page(page, warnings=warnings)

        fingerprint_after = (
            _table_fingerprint(table) if table is not None and not table.empty else "rows:0"
        )
        sample_names = _first_company_names(table, 3) if table is not None else []
        row_count = len(table) if table is not None else 0
        selected_label = selected.get("label") or selected.get("value") or filter_name
        _print_sector_collection_diagnostics(
            filter_kind,
            filter_name,
            selected_label,
            row_count,
            sample_names,
            fingerprint_before,
            fingerprint_after,
        )

        if fingerprint_after != "rows:0" and fingerprint_after in seen_fingerprints:
            deduper.add_once(
                warnings,
                f"duplicate_fingerprint:{filter_kind}",
                FILTER_DUPLICATE_FINGERPRINT_WARNING.format(
                    filter_kind=_filter_kind_display_name(filter_kind),
                    filter_name=filter_name,
                ),
            )
            continue

        if (
            fingerprint_after != "rows:0"
            and fingerprint_before == fingerprint_after
            and sectors_with_rows > 0
        ):
            deduper.add_once(
                warnings,
                f"fingerprint_unchanged:{filter_kind}",
                SEVERAL_FILTER_FINGERPRINTS_UNCHANGED_WARNING.format(
                    filter_kind=_filter_kind_display_name(filter_kind).lower(),
                ),
            )
            continue

        if not update_ready and (table is None or table.empty):
            warnings.append(
                MULTI_SECTOR_SECTOR_SKIPPED_WARNING.format(sector=filter_name)
            )
            continue

        if table is None or table.empty:
            continue

        if fingerprint_after != "rows:0":
            seen_fingerprints.add(fingerprint_after)

        sector_frames.append(tag_rows_with_sector(table, filter_name))
        sectors_with_rows += 1

    if not sector_frames:
        return None, filter_report_warnings(warnings), True

    merged = merge_sector_stock_rows(sector_frames)
    before_dedupe = len(merged)
    deduped = dedupe_stocks_by_name(merged)
    after_dedupe = len(deduped)

    warnings.append(
        _build_collected_filter_summary_warning(filter_kind, sectors_with_rows)
    )
    warnings.append(
        MULTI_SECTOR_BEFORE_DEDUPE_WARNING.format(count=before_dedupe)
    )
    warnings.append(
        MULTI_SECTOR_AFTER_DEDUPE_WARNING.format(count=after_dedupe)
    )

    if before_dedupe >= 100 and after_dedupe < max(50, int(before_dedupe * 0.25)):
        warnings.append(MULTI_SECTOR_REUSED_TABLE_CRITICAL_WARNING)

    return deduped, filter_report_warnings(warnings), True


def _map_browser_columns(df: pd.DataFrame) -> tuple[dict[str, str], list[str]]:
    mapping: dict[str, str] = {}
    warnings: list[str] = []

    for column in df.columns:
        header = _normalize_header(column)
        canonical = resolve_column_name(column)
        if canonical == "date":
            mapping[str(column)] = "date"
            continue
        if header == "name":
            mapping[str(column)] = "symbol"
        elif header == "open":
            mapping[str(column)] = "open"
        elif header == "high":
            mapping[str(column)] = "high"
        elif header == "low":
            mapping[str(column)] = "low"
        elif header == "volume":
            mapping[str(column)] = "volume"

    if not any(value == "volume" for value in mapping.values()):
        warnings.append(VOLUME_MISSING_WARNING)

    return mapping, warnings


def _find_close_source_column(df: pd.DataFrame) -> tuple[str | None, bool]:
    """Return the raw column to use for normalized close and whether it is Last Price."""
    for column in df.columns:
        if _normalize_header(column) == "last price":
            return str(column), True

    for column in df.columns:
        if _normalize_header(column) == "close":
            return str(column), False

    return None, False


def _find_previous_close_column(df: pd.DataFrame) -> str | None:
    """Return the raw P.C./previous-close column when present."""
    for column in df.columns:
        header = _normalize_header(column)
        if header in {"p.c.", "p.c", "pc", "previous close"}:
            return str(column)
    return None


def _contains_non_stock_name(name: object) -> bool:
    text = str(name).strip()
    if not text or text.lower() in {"nan", "none"}:
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in NON_STOCK_NAME_MARKERS)


def _to_numeric_price_series(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.strip().str.replace(",", "", regex=False)
    return pd.to_numeric(cleaned, errors="coerce")


def _filter_valid_stock_rows(
    df: pd.DataFrame,
    *,
    price_columns: tuple[str, ...] = ("open", "high", "low", "close"),
) -> tuple[pd.DataFrame, int]:
    """Keep only rows that look like real EGX stock price entries."""
    filtered = df.copy()
    filtered["symbol"] = filtered["symbol"].astype(str).str.strip()

    for column in price_columns:
        filtered[column] = _to_numeric_price_series(filtered[column])

    valid_mask = (
        ~filtered["symbol"].apply(_contains_non_stock_name)
        & filtered[list(price_columns)].notna().all(axis=1)
    )
    dropped_count = int((~valid_mask).sum())
    return filtered.loc[valid_mask].copy(), dropped_count


def _filter_invalid_ohlc_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop rows where high/low do not bracket open and close."""
    if df.empty:
        return df.copy(), 0

    invalid_high = (df["high"] < df["open"]) | (df["high"] < df["close"])
    invalid_low = (df["low"] > df["open"]) | (df["low"] > df["close"])
    invalid_mask = invalid_high | invalid_low
    dropped_count = int(invalid_mask.sum())
    return df.loc[~invalid_mask].copy(), dropped_count


def normalize_browser_stocks_csv(
    input_csv: Path,
    output_csv: Path,
    live_snapshot_csv: Path | None = None,
) -> BrowserStocksNormalizationResult:
    """Convert a browser-extracted EGX stocks CSV into normalized OHLCV format."""
    validator = EgxCsvImportValidator()
    live_validator = EgxLiveSnapshotValidator()
    snapshot_path = live_snapshot_csv or settings.EGX_LIVE_SNAPSHOT_PATH

    def _empty_result(errors: list[str]) -> BrowserStocksNormalizationResult:
        return BrowserStocksNormalizationResult(
            ohlcv=validator._empty_result(errors),
            live_snapshot=None,
            live_snapshot_csv=None,
            validation_warnings=[],
            valid_symbol_count=0,
        )

    if not input_csv.exists():
        return _empty_result([f"File not found: {input_csv}"])

    try:
        raw_df = pd.read_csv(input_csv)
    except Exception as exc:  # noqa: BLE001
        return _empty_result([f"Unable to read browser stocks CSV: {exc}"])

    if raw_df.empty:
        return _empty_result(["Browser stocks CSV is empty"])

    column_mapping, warnings = _map_browser_columns(raw_df)
    if "symbol" not in column_mapping.values():
        return _empty_result(
            ["Unable to detect Name/symbol column for browser stocks normalization"]
        )

    close_source, uses_last_price = _find_close_source_column(raw_df)
    if close_source is None:
        return _empty_result(
            ["Missing required close column for browser normalization: close"]
        )

    previous_close_source = _find_previous_close_column(raw_df)

    renamed = raw_df.rename(columns=column_mapping)
    available = set(renamed.columns)

    missing_ohlc = [
        field for field in ("open", "high", "low") if field not in available
    ]
    if missing_ohlc:
        return _empty_result(
            [
                "Missing required OHLC columns for browser normalization: "
                + ", ".join(missing_ohlc)
            ]
        )

    renamed["close"] = _to_numeric_price_series(raw_df[close_source])
    if uses_last_price:
        warnings.append(LAST_PRICE_CLOSE_WARNING)

    normalized = renamed.copy()
    if previous_close_source is not None:
        normalized["previous_close"] = _to_numeric_price_series(
            raw_df[previous_close_source]
        )
    if "volume" not in available:
        normalized["volume"] = 0
    if "date" not in available:
        normalized["date"] = date.today()
    else:
        normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce").dt.date
        if normalized["date"].isna().any():
            normalized.loc[normalized["date"].isna(), "date"] = date.today()

    price_columns: tuple[str, ...] = ("open", "high", "low", "close")
    if previous_close_source is not None:
        price_columns = ("previous_close", "open", "high", "low", "close")

    normalized, dropped_count = _filter_valid_stock_rows(
        normalized, price_columns=price_columns
    )
    if dropped_count > 0:
        warnings.append(
            f"Dropped {dropped_count} non-stock/header rows during normalization."
        )

    normalized, invalid_ohlc_count = _filter_invalid_ohlc_rows(normalized)
    if invalid_ohlc_count > 0:
        warnings.append(
            INVALID_OHLC_RANGE_WARNING.format(count=invalid_ohlc_count)
        )

    if normalized.empty:
        return _empty_result(
            ["No valid stock rows remained after browser normalization filtering"]
        )

    valid_symbol_count = int(normalized["symbol"].nunique())
    validation_warnings: list[str] = []
    symbol_mapping: MappingResult | None = None

    ohlcv_df = normalized[REQUIRED_COLUMNS].copy()
    ohlcv_df["volume"] = (
        _to_numeric_price_series(ohlcv_df["volume"]).fillna(0).astype(int)
    )
    ohlcv_df = ohlcv_df.sort_values(["date", "symbol"]).reset_index(drop=True)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    ohlcv_df.to_csv(output_csv, index=False)

    ohlcv_result = validator.validate_csv(output_csv)

    live_result: DataImportValidationResult | None = None
    if previous_close_source is None:
        live_result = live_validator._empty_result(
            ["Missing required previous_close column for live snapshot: P.C."]
        )
    else:
        snapshot_df = normalized[LIVE_SNAPSHOT_REQUIRED_COLUMNS].copy()
        snapshot_df["volume"] = (
            _to_numeric_price_series(snapshot_df["volume"]).fillna(0).astype(int)
        )
        snapshot_df, symbol_mapping = apply_symbol_mapping_to_snapshot_dataframe(
            snapshot_df
        )
        warnings.extend(symbol_mapping.warnings)
        valid_symbol_count = int(snapshot_df["symbol"].nunique())
        snapshot_df = snapshot_df.sort_values(["date", "symbol"]).reset_index(drop=True)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_df.to_csv(snapshot_path, index=False)
        live_result = live_validator.validate_csv(snapshot_path)

    validation_warnings = build_symbol_count_warnings(valid_symbol_count)
    ohlcv_result.warnings.extend(warnings + validation_warnings)
    if live_result is not None:
        live_result.warnings.extend(warnings + validation_warnings)

    return BrowserStocksNormalizationResult(
        ohlcv=ohlcv_result,
        live_snapshot=live_result,
        live_snapshot_csv=snapshot_path if previous_close_source is not None else None,
        validation_warnings=validation_warnings,
        valid_symbol_count=valid_symbol_count,
        symbol_mapping=symbol_mapping,
    )


class EgxPublicBrowserStocksReader:
    """Load the public EGX prices page in Chromium and save the visible stocks table."""

    def __init__(self, downloads_dir: Path, headless: bool = True) -> None:
        self.downloads_dir = downloads_dir
        self.headless = headless
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

    def _timestamp(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    def _launch_browser(self, playwright: object) -> object:
        """Launch installed Chrome when available, otherwise bundled Chromium."""
        launch_kwargs = {
            "headless": self.headless,
            "args": EGX_BROWSER_LAUNCH_ARGS,
        }
        print("Trying installed Chrome channel...")
        try:
            browser = playwright.chromium.launch(channel="chrome", **launch_kwargs)
            print("EGX browser stocks: using installed Chrome channel")
            return browser
        except Exception as exc:  # noqa: BLE001
            print("Installed Chrome failed, falling back to bundled Chromium...")
            print(f"EGX browser stocks: installed Chrome failed ({exc})")
            return playwright.chromium.launch(**launch_kwargs)

    def _fetch_stocks_page_html(self, page: object, warnings: list[str]) -> tuple[str | None, str | None]:
        """Try each public EGX prices URL until one loads."""
        last_error: Exception | None = None

        for url in EGX_BROWSER_STOCKS_URLS:
            print(f"EGX browser stocks: trying URL {url}")
            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=PAGE_LOAD_TIMEOUT_MS,
                )
                try:
                    page.wait_for_load_state(
                        "networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS
                    )
                except Exception as exc:
                    if exc.__class__.__name__ != "TimeoutError":
                        raise
                    warnings.append(
                        f"Network idle timeout for {url}; continuing with current page content."
                    )

                page.wait_for_selector("table", timeout=PAGE_LOAD_TIMEOUT_MS)
                html = page.content()
                print(f"EGX browser stocks: loaded URL {url}")
                return html, url
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                warnings.append(f"Failed to load {url}: {exc}")
                print(f"EGX browser stocks: failed URL {url} ({exc})")

        if last_error is not None:
            warnings.append(f"All EGX browser URLs failed: {last_error}")
        return None, None

    def read_stocks_page(self) -> EgxBrowserReadResult:
        """Open the public EGX prices page and extract the visible stocks table."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return EgxBrowserReadResult(
                success=False,
                errors=[
                    "Playwright is not installed. Run: "
                    "python -m pip install -r requirements.txt && "
                    "python -m playwright install chromium"
                ],
            )

        warnings: list[str] = []
        html: str | None = None
        loaded_url: str | None = None

        try:
            with sync_playwright() as playwright:
                browser = self._launch_browser(playwright)
                try:
                    context = browser.new_context()
                    try:
                        page = context.new_page()
                        html, loaded_url = self._fetch_stocks_page_html(page, warnings)
                        if html is not None:
                            warnings.extend(prepare_full_market_stocks_view(page))
                            html = page.content()
                    finally:
                        context.close()
                finally:
                    browser.close()
        except Exception as exc:  # noqa: BLE001
            return EgxBrowserReadResult(
                success=False,
                errors=[f"Browser read failed: {exc}"],
                warnings=warnings,
            )

        if html is None or loaded_url is None:
            return EgxBrowserReadResult(
                success=False,
                errors=["Browser read failed: no page content was captured."],
                warnings=warnings,
            )

        tables = extract_tables_from_html(html)
        if not tables:
            return EgxBrowserReadResult(
                success=False,
                errors=["No tables found on EGX prices page."],
                warnings=warnings,
            )

        stocks_table = select_stocks_table(tables)
        if stocks_table is None or stocks_table.empty:
            return EgxBrowserReadResult(
                success=False,
                errors=["No stocks table found on EGX prices page."],
                warnings=warnings,
            )

        saved_csv = self.downloads_dir / f"browser_stocks_{self._timestamp()}.csv"
        stocks_table.to_csv(saved_csv, index=False)

        row_count = len(stocks_table)
        if _is_low_row_count(row_count):
            warnings.append(_build_low_row_count_warning(row_count))

        return EgxBrowserReadResult(
            success=True,
            saved_csv=saved_csv,
            rows=len(stocks_table),
            columns=[str(column) for column in stocks_table.columns],
            warnings=warnings,
        )


class EgxAttachedChromeStocksReader:
    """Attach to user-started Chrome via CDP and read the visible EGX stocks table."""

    def __init__(
        self, downloads_dir: Path, cdp_url: str = DEFAULT_CHROME_CDP_URL
    ) -> None:
        self.downloads_dir = downloads_dir
        self.cdp_url = cdp_url
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

    def _timestamp(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    def _wait_for_stocks_table(self, page: object) -> None:
        """Soft wait for stocks table in DOM; do not fail if below the fold."""
        table = wait_for_stocks_table_in_dom(page, timeout_ms=15_000)
        if table is not None:
            return
        wait_for_selector = getattr(page, "wait_for_selector", None)
        if callable(wait_for_selector):
            try:
                wait_for_selector("table", timeout=5_000)
            except Exception:  # noqa: BLE001
                pass

    def _open_or_select_prices_page(
        self, browser: object
    ) -> tuple[object | None, str | None, list[str], list[str]]:
        """Select an existing EGX prices tab or open a new one."""
        warnings: list[str] = []
        errors: list[str] = []

        page = select_egx_prices_page(collect_browser_pages(browser))
        if page is not None:
            return page, "selected", warnings, errors

        contexts = getattr(browser, "contexts", [])
        if not contexts:
            errors.append(NO_BROWSER_CONTEXT_ERROR)
            return None, None, warnings, errors

        context = contexts[0]
        new_page = context.new_page()
        try:
            new_page.goto(
                EGX_BROWSER_STOCKS_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT_MS,
            )
            return new_page, "opened", warnings, errors
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{EGX_PAGE_OPEN_FAILED_ERROR} {exc}")
            return None, "opened", warnings, errors

    def open_or_select_prices_page(self) -> tuple[object | None, str | None]:
        """Attach over CDP and return an EGX prices page ready for table read."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run: "
                "python -m pip install -r requirements.txt && "
                "python -m playwright install chromium"
            ) from exc

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(self.cdp_url)
            try:
                page, action, _warnings, errors = self._open_or_select_prices_page(
                    browser
                )
                if page is None:
                    message = errors[0] if errors else EGX_PAGE_OPEN_FAILED_ERROR
                    raise RuntimeError(message)
                return page, action
            finally:
                browser.close()

    def _save_stocks_table_from_dataframe(
        self,
        stocks_table: pd.DataFrame,
        warnings: list[str],
        page_action: str | None,
    ) -> EgxBrowserReadResult:
        """Persist a stocks table dataframe extracted from the EGX page."""
        if stocks_table.empty:
            return EgxBrowserReadResult(
                success=False,
                errors=[NO_STOCKS_TABLE_ERROR],
                warnings=warnings,
                page_action=page_action,
            )

        saved_csv = (
            self.downloads_dir / f"attached_chrome_stocks_{self._timestamp()}.csv"
        )
        stocks_table.to_csv(saved_csv, index=False)

        row_count = len(stocks_table)
        if _is_low_row_count(row_count) and not _has_successful_multi_filter_collection(
            warnings
        ):
            warnings.append(_build_low_row_count_warning(row_count))

        return EgxBrowserReadResult(
            success=True,
            saved_csv=saved_csv,
            rows=len(stocks_table),
            columns=[str(column) for column in stocks_table.columns],
            warnings=filter_report_warnings(warnings),
            page_action=page_action,
        )

    def _read_stocks_table_from_page(
        self,
        page: object,
        warnings: list[str],
        page_action: str | None,
    ) -> EgxBrowserReadResult:
        """Try multi-sector collection first, then fall back to single-table read."""
        _scroll_to_stocks_table_area(page)
        wait_for_stocks_table_in_dom(page, warnings=warnings)

        multi_table, multi_warnings, multi_attempted = collect_stocks_by_all_sectors(
            page
        )
        warnings.extend(multi_warnings)

        if multi_table is not None and not multi_table.empty:
            return self._save_stocks_table_from_dataframe(
                multi_table,
                warnings,
                page_action,
            )

        fallback_table = extract_stocks_table_from_page(page, warnings=warnings)
        if fallback_table is not None and not fallback_table.empty:
            if multi_attempted:
                warnings.append(MULTI_SECTOR_FAILED_VISIBLE_TABLE_FALLBACK_WARNING)
            else:
                warnings.append(MULTI_SECTOR_UNAVAILABLE_FALLBACK_WARNING)
            return self._save_stocks_table_from_dataframe(
                fallback_table,
                warnings,
                page_action,
            )

        if multi_attempted:
            warnings.append(MULTI_SECTOR_FAILED_VISIBLE_TABLE_FALLBACK_WARNING)
        else:
            warnings.append(MULTI_SECTOR_UNAVAILABLE_FALLBACK_WARNING)

        print_table_detection_diagnostics(page)
        html = _get_page_html(page)
        if html is None:
            return EgxBrowserReadResult(
                success=False,
                errors=["Attached Chrome read failed: no page content was captured."],
                warnings=filter_report_warnings(warnings),
                page_action=page_action,
            )
        return self._save_stocks_table_from_html(
            html,
            warnings,
            page_action,
            page=page,
        )

    def _save_stocks_table_from_html(
        self,
        html: str,
        warnings: list[str],
        page_action: str | None,
        *,
        page: object | None = None,
    ) -> EgxBrowserReadResult:
        stocks_table = extract_stocks_table_from_html(html)
        if stocks_table is None or stocks_table.empty:
            if page is not None:
                print_table_detection_diagnostics(page)
            return EgxBrowserReadResult(
                success=False,
                errors=[NO_STOCKS_TABLE_ERROR],
                warnings=warnings,
                page_action=page_action,
            )

        saved_csv = (
            self.downloads_dir / f"attached_chrome_stocks_{self._timestamp()}.csv"
        )
        stocks_table.to_csv(saved_csv, index=False)

        row_count = len(stocks_table)
        if _is_low_row_count(row_count):
            warnings.append(_build_low_row_count_warning(row_count))

        return EgxBrowserReadResult(
            success=True,
            saved_csv=saved_csv,
            rows=len(stocks_table),
            columns=[str(column) for column in stocks_table.columns],
            warnings=warnings,
            page_action=page_action,
        )

    def _read_with_playwright(self, sync_playwright: object) -> EgxBrowserReadResult:
        """Connect over CDP and read the current EGX prices page HTML."""
        warnings: list[str] = []

        print(f"EGX attach Chrome: connecting to {self.cdp_url}")
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(self.cdp_url)
            try:
                page = select_egx_prices_page(collect_browser_pages(browser))
                if page is None:
                    return EgxBrowserReadResult(
                        success=False,
                        errors=[NO_EGX_PRICES_PAGE_ERROR],
                        warnings=warnings,
                    )

                page_url = getattr(page, "url", "")
                print(f"EGX attach Chrome: reading page {page_url}")
                warnings.extend(prepare_full_market_stocks_view(page))
                return self._read_stocks_table_from_page(page, warnings, page_action=None)
            finally:
                browser.close()

    def _read_or_open_with_playwright(
        self, sync_playwright: object
    ) -> EgxBrowserReadResult:
        """Connect over CDP, open/select EGX prices page, and read the stocks table."""
        warnings: list[str] = []
        page_action: str | None = None

        print(f"EGX attach Chrome: connecting to {self.cdp_url}")
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(self.cdp_url)
            try:
                page, page_action, page_warnings, page_errors = (
                    self._open_or_select_prices_page(browser)
                )
                warnings.extend(page_warnings)
                if page is None:
                    return EgxBrowserReadResult(
                        success=False,
                        errors=page_errors or [EGX_PAGE_OPEN_FAILED_ERROR],
                        warnings=warnings,
                        page_action=page_action,
                    )

                page_url = getattr(page, "url", "")
                print(f"EGX attach Chrome: {page_action} page {page_url}")
                warnings.extend(prepare_full_market_stocks_view(page))
                return self._read_stocks_table_from_page(
                    page,
                    warnings,
                    page_action,
                )
            finally:
                browser.close()

    def read_or_open_stocks_page(self) -> EgxBrowserReadResult:
        """Attach to Chrome, open/select EGX prices page, and read the stocks table."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return EgxBrowserReadResult(
                success=False,
                errors=[
                    "Playwright is not installed. Run: "
                    "python -m pip install -r requirements.txt && "
                    "python -m playwright install chromium"
                ],
            )

        try:
            return self._read_or_open_with_playwright(sync_playwright)
        except Exception as exc:  # noqa: BLE001
            return EgxBrowserReadResult(
                success=False,
                errors=[f"Attached Chrome read failed: {exc}"],
            )

    def read_current_stocks_page(self) -> EgxBrowserReadResult:
        """Attach to Chrome over CDP and read the current visible EGX stocks table."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return EgxBrowserReadResult(
                success=False,
                errors=[
                    "Playwright is not installed. Run: "
                    "python -m pip install -r requirements.txt && "
                    "python -m playwright install chromium"
                ],
            )

        try:
            return self._read_with_playwright(sync_playwright)
        except Exception as exc:  # noqa: BLE001
            return EgxBrowserReadResult(
                success=False,
                errors=[f"Attached Chrome read failed: {exc}"],
            )
