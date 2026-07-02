"""Read public EGX market-watch pages and save extracted tables as CSV."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from io import StringIO
from pathlib import Path
import time

import pandas as pd
import requests
from pydantic import BaseModel, Field

from core.data_import import (
    REQUIRED_COLUMNS,
    DataImportValidationResult,
    EgxCsvImportValidator,
    resolve_column_name,
)

REQUEST_TIMEOUT_SECONDS = 30
EGX_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Connection": "keep-alive",
}
EGX_BASE_DOMAIN = "https://egx.com.eg/en/"

EGX_PUBLIC_PAGE_URLS: dict[str, str] = {
    "market_summary": f"{EGX_BASE_DOMAIN}MarketSummary.aspx",
    "indices": f"{EGX_BASE_DOMAIN}Indices.aspx",
    "sectors": f"{EGX_BASE_DOMAIN}Sectors.aspx",
    "stocks": f"{EGX_BASE_DOMAIN}Prices.aspx",
}

STOCK_TABLE_KEYWORDS = {
    "stock",
    "security",
    "symbol",
    "code",
    "name",
    "last",
    "close",
    "volume",
    "open",
    "high",
    "low",
}
INDEX_TABLE_KEYWORDS = {"index", "indices", "last", "change", "value", "close"}
SECTOR_TABLE_KEYWORDS = {"sector", "value", "change", "last", "index"}

STOCK_SYMBOL_ALIASES = (
    "symbol",
    "ticker",
    "code",
    "security",
    "security code",
    "stock",
    "stock code",
    "name",
)
STOCK_CLOSE_ALIASES = ("close", "last", "last price", "closing", "closing price")
STOCK_VOLUME_ALIASES = ("volume", "quantity", "traded volume", "qty")


class EgxPublicPageType(str, Enum):
    MARKET_SUMMARY = "market_summary"
    INDICES = "indices"
    SECTORS = "sectors"
    STOCKS = "stocks"


class EgxPublicReadResult(BaseModel):
    success: bool
    page_type: EgxPublicPageType
    url: str
    saved_csv: Path | None = None
    debug_html: Path | None = None
    rows: int = 0
    columns: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _normalize_header(column: object) -> str:
    return str(column).strip().lower()


def _column_text(columns: list[object]) -> str:
    return " ".join(_normalize_header(column) for column in columns)


def _score_table(columns: list[object], keywords: set[str]) -> int:
    text = _column_text(columns)
    return sum(1 for keyword in keywords if keyword in text)


def _pick_best_table(
    tables: list[pd.DataFrame], page_type: EgxPublicPageType
) -> pd.DataFrame | None:
    if not tables:
        return None

    keyword_map = {
        EgxPublicPageType.STOCKS: STOCK_TABLE_KEYWORDS,
        EgxPublicPageType.INDICES: INDEX_TABLE_KEYWORDS,
        EgxPublicPageType.SECTORS: SECTOR_TABLE_KEYWORDS,
        EgxPublicPageType.MARKET_SUMMARY: STOCK_TABLE_KEYWORDS
        | INDEX_TABLE_KEYWORDS
        | SECTOR_TABLE_KEYWORDS,
    }
    keywords = keyword_map[page_type]

    best_table: pd.DataFrame | None = None
    best_score = -1
    for table in tables:
        if table.empty:
            continue
        score = _score_table(list(table.columns), keywords)
        size_bonus = min(len(table), 1000) / 1000
        total_score = score + size_bonus
        if total_score > best_score:
            best_score = total_score
            best_table = table

    if page_type == EgxPublicPageType.MARKET_SUMMARY and best_table is None:
        non_empty = [table for table in tables if not table.empty]
        if non_empty:
            return max(non_empty, key=len)

    return best_table


def _match_stock_column(column: object, aliases: tuple[str, ...]) -> bool:
    header = _normalize_header(column)
    return any(alias in header or header == alias for alias in aliases)


def _map_stock_columns(df: pd.DataFrame) -> tuple[dict[str, str], list[str]]:
    mapping: dict[str, str] = {}
    errors: list[str] = []

    for column in df.columns:
        canonical = resolve_column_name(column)
        if canonical in REQUIRED_COLUMNS:
            mapping[str(column)] = canonical
            continue

        header = _normalize_header(column)
        if _match_stock_column(column, STOCK_SYMBOL_ALIASES):
            mapping[str(column)] = "symbol"
        elif canonical == "open" or "open" in header:
            mapping[str(column)] = "open"
        elif canonical == "high" or "high" in header:
            mapping[str(column)] = "high"
        elif canonical == "low" or "low" in header:
            mapping[str(column)] = "low"
        elif _match_stock_column(column, STOCK_CLOSE_ALIASES):
            mapping[str(column)] = "close"
        elif _match_stock_column(column, STOCK_VOLUME_ALIASES):
            mapping[str(column)] = "volume"
        elif canonical == "date" or "date" in header:
            mapping[str(column)] = "date"

    return mapping, errors


def normalize_stocks_table_to_ohlcv(
    input_csv: Path, output_csv: Path
) -> DataImportValidationResult:
    """Convert an EGX stocks table CSV into normalized OHLCV format."""
    validator = EgxCsvImportValidator()

    if not input_csv.exists():
        return validator._empty_result([f"File not found: {input_csv}"])

    try:
        raw_df = pd.read_csv(input_csv)
    except Exception as exc:  # noqa: BLE001
        return validator._empty_result([f"Unable to read stocks CSV: {exc}"])

    if raw_df.empty:
        return validator._empty_result(["Stocks CSV is empty"])

    column_mapping, _ = _map_stock_columns(raw_df)
    if not column_mapping:
        return validator._empty_result(
            ["Unable to detect stock table columns for OHLCV normalization"]
        )

    renamed = raw_df.rename(columns=column_mapping)
    available = set(renamed.columns)

    missing_ohlc = [
        field
        for field in ("open", "high", "low", "close", "volume")
        if field not in available
    ]
    if missing_ohlc:
        return validator._empty_result(
            [
                "Missing required OHLCV columns for normalization: "
                + ", ".join(missing_ohlc)
            ]
        )

    if "symbol" not in available:
        return validator._empty_result(
            ["Missing symbol/security column for OHLCV normalization"]
        )

    normalized = renamed.copy()
    if "date" not in available:
        normalized["date"] = date.today()
    else:
        normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce").dt.date
        if normalized["date"].isna().any():
            normalized.loc[normalized["date"].isna(), "date"] = date.today()

    normalized = normalized[REQUIRED_COLUMNS].copy()
    normalized["symbol"] = normalized["symbol"].astype(str).str.strip()
    normalized = normalized[normalized["symbol"] != ""]

    for col in ("open", "high", "low", "close", "volume"):
        normalized[col] = pd.to_numeric(normalized[col], errors="coerce")

    if normalized[["open", "high", "low", "close", "volume"]].isna().any().any():
        return validator._empty_result(
            ["One or more rows have non-numeric OHLCV values after normalization"]
        )

    normalized["volume"] = normalized["volume"].fillna(0).astype(int)
    normalized = normalized.sort_values(["date", "symbol"]).reset_index(drop=True)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(output_csv, index=False)

    return validator.validate_csv(output_csv)


class EgxPublicMarketWatchReader:
    """Fetch and parse public EGX market-watch HTML tables."""

    ALL_PAGE_TYPES = (
        EgxPublicPageType.MARKET_SUMMARY,
        EgxPublicPageType.INDICES,
        EgxPublicPageType.SECTORS,
        EgxPublicPageType.STOCKS,
    )

    def __init__(self, downloads_dir: Path) -> None:
        self._downloads_dir = downloads_dir
        self._downloads_dir.mkdir(parents=True, exist_ok=True)

    def _page_url(self, page_type: EgxPublicPageType) -> str:
        return EGX_PUBLIC_PAGE_URLS[page_type.value]

    def _timestamp(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    def _default_request_headers(self) -> dict[str, str]:
        return dict(EGX_REQUEST_HEADERS)

    def _response_metadata_warnings(self, metadata: dict[str, object]) -> list[str]:
        return [
            f"HTTP status code: {metadata['status_code']}",
            f"Final URL: {metadata['final_url']}",
            f"Content length: {metadata['content_length']}",
            f"Content-Type: {metadata['content_type']}",
        ]

    def _save_debug_html(self, page_type: EgxPublicPageType, html: str) -> Path:
        timestamp = self._timestamp()
        debug_path = self._downloads_dir / f"debug_{page_type.value}_{timestamp}.html"
        debug_path.write_text(html, encoding="utf-8")
        return debug_path

    def _fetch_html(
        self, url: str
    ) -> tuple[str | None, dict[str, object], list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        headers = self._default_request_headers()
        last_exc: Exception | None = None

        for attempt in range(2):
            try:
                response = requests.get(
                    url,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    headers=headers,
                )
                response.raise_for_status()
                metadata = {
                    "status_code": response.status_code,
                    "final_url": response.url,
                    "content_length": len(response.content),
                    "content_type": response.headers.get("Content-Type", "unknown"),
                }
                return response.text, metadata, errors, warnings
            except (requests.exceptions.ConnectionError, ConnectionResetError) as exc:
                last_exc = exc
                if attempt == 0:
                    time.sleep(2)
                    continue
            except requests.RequestException as exc:
                last_exc = exc
                break

        errors.append(f"Failed to fetch EGX page: {last_exc}")
        warnings.append(
            "The EGX public page URL may have changed. "
            "Update EGX_PUBLIC_PAGE_URLS in core/egx_public_reader.py."
        )
        return None, {}, errors, warnings

    def _parse_tables(self, html: str) -> tuple[list[pd.DataFrame], list[str]]:
        try:
            tables = pd.read_html(StringIO(html))
        except ValueError as exc:
            return [], [f"No HTML tables found: {exc}"]
        except Exception as exc:  # noqa: BLE001
            return [], [f"Unable to parse HTML tables: {exc}"]

        non_empty = [table for table in tables if not table.empty]
        if not non_empty:
            return [], ["No non-empty tables found in EGX page HTML"]
        return non_empty, []

    def read_page(self, page_type: EgxPublicPageType) -> EgxPublicReadResult:
        """Read one public EGX page and save the selected table as CSV."""
        url = self._page_url(page_type)
        html, metadata, fetch_errors, warnings = self._fetch_html(url)
        if metadata:
            warnings.extend(self._response_metadata_warnings(metadata))
        if html is None:
            return EgxPublicReadResult(
                success=False,
                page_type=page_type,
                url=url,
                errors=fetch_errors,
                warnings=warnings,
            )

        tables, parse_errors = self._parse_tables(html)
        if parse_errors:
            debug_path = self._save_debug_html(page_type, html)
            warnings.append(f"Saved debug HTML: {debug_path}")
            return EgxPublicReadResult(
                success=False,
                page_type=page_type,
                url=url,
                debug_html=debug_path,
                errors=parse_errors,
                warnings=warnings,
            )

        selected = _pick_best_table(tables, page_type)
        if selected is None:
            debug_path = self._save_debug_html(page_type, html)
            warnings.append(f"Saved debug HTML: {debug_path}")
            return EgxPublicReadResult(
                success=False,
                page_type=page_type,
                url=url,
                debug_html=debug_path,
                errors=["No suitable table found on EGX page"],
                warnings=warnings,
            )

        timestamp = self._timestamp()
        saved_csv = self._downloads_dir / f"{page_type.value}_{timestamp}.csv"
        selected.to_csv(saved_csv, index=False)

        return EgxPublicReadResult(
            success=True,
            page_type=page_type,
            url=url,
            saved_csv=saved_csv,
            rows=len(selected),
            columns=[str(column) for column in selected.columns],
            warnings=warnings,
        )

    def read_all(self) -> list[EgxPublicReadResult]:
        """Read all supported public EGX pages."""
        return [self.read_page(page_type) for page_type in self.ALL_PAGE_TYPES]
