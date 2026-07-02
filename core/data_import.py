"""Validate and normalize manually provided real EGX CSV market data."""

from datetime import date
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

REQUIRED_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume"]
LIVE_SNAPSHOT_REQUIRED_COLUMNS = [
    "date",
    "symbol",
    "previous_close",
    "open",
    "high",
    "low",
    "close",
    "volume",
]
LIVE_SNAPSHOT_COLUMNS = LIVE_SNAPSHOT_REQUIRED_COLUMNS
LIVE_SNAPSHOT_OUTPUT_COLUMNS = [
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
INSUFFICIENT_HISTORY_ERROR_FRAGMENT = "fewer than 2 dates"
INDEX_SYMBOLS = {"EGX30", "EGX70"}
MIN_SYMBOLS_WARNING = 10
MIN_DATES_WARNING = 5
SUPPORTED_IMPORT_EXTENSIONS = {".csv", ".xlsx"}

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "date": (
        "date",
        "trade_date",
        "trading_date",
        "session_date",
        "تاريخ",
        "التاريخ",
    ),
    "symbol": (
        "symbol",
        "ticker",
        "code",
        "security_code",
        "رمز",
        "كود",
        "كود السهم",
    ),
    "open": (
        "open",
        "opening",
        "open_price",
        "سعر الفتح",
        "الفتح",
    ),
    "high": (
        "high",
        "highest",
        "high_price",
        "أعلى",
        "الاعلى",
    ),
    "low": (
        "low",
        "lowest",
        "low_price",
        "أدنى",
        "الادنى",
    ),
    "close": (
        "close",
        "last",
        "last_price",
        "closing_price",
        "سعر الإغلاق",
        "الاغلاق",
        "إغلاق",
    ),
    "volume": (
        "volume",
        "traded_volume",
        "qty",
        "quantity",
        "الكمية",
        "كمية التداول",
        "حجم التداول",
    ),
}


def resolve_column_name(column: object) -> str:
    """Map a raw column header to a canonical normalized column name."""
    raw = str(column).strip()
    lowered = raw.lower()
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if raw == alias or lowered == alias.lower():
                return canonical
    return lowered


class DataImportValidationResult(BaseModel):
    valid: bool
    rows: int
    symbols_count: int
    date_min: date | None
    date_max: date | None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def is_only_insufficient_history_failure(result: DataImportValidationResult) -> bool:
    """Return True when validation failed solely due to missing multi-day history."""
    return (
        not result.valid
        and bool(result.errors)
        and all(
            INSUFFICIENT_HISTORY_ERROR_FRAGMENT in error for error in result.errors
        )
    )


class EgxCsvImportValidator:
    """Validates and normalizes local EGX OHLCV CSV files."""

    def _empty_result(self, errors: list[str]) -> DataImportValidationResult:
        return DataImportValidationResult(
            valid=False,
            rows=0,
            symbols_count=0,
            date_min=None,
            date_max=None,
            errors=errors,
            warnings=[],
        )

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        renamed = df.copy()
        renamed.columns = [resolve_column_name(col) for col in renamed.columns]
        return renamed

    def _missing_required_columns(self, df: pd.DataFrame) -> list[str]:
        return [col for col in REQUIRED_COLUMNS if col not in df.columns]

    def _prepare_dataframe(self, df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
        errors: list[str] = []
        normalized = self._normalize_columns(df)

        missing = self._missing_required_columns(normalized)
        if missing:
            errors.append(f"Missing required columns: {', '.join(missing)}")
            return normalized, errors

        prepared = normalized[REQUIRED_COLUMNS].copy()
        prepared["symbol"] = prepared["symbol"].astype(str).str.strip()
        prepared = prepared[prepared["symbol"] != ""]

        if prepared.empty:
            errors.append("No rows with non-empty symbols")
            return prepared, errors

        try:
            prepared["date"] = pd.to_datetime(prepared["date"], errors="coerce").dt.date
        except (ValueError, TypeError):
            errors.append("Unable to parse date column")
            return prepared, errors

        if prepared["date"].isna().any():
            errors.append("One or more rows have unparseable dates")

        for col in ("open", "high", "low", "close", "volume"):
            prepared[col] = pd.to_numeric(prepared[col], errors="coerce")

        if prepared[["open", "high", "low", "close", "volume"]].isna().any().any():
            errors.append("One or more rows have non-numeric OHLCV values")

        return prepared, errors

    def _validate_ohlc_constraints(self, prepared: pd.DataFrame) -> list[str]:
        row_errors: list[str] = []

        if (prepared["open"] <= 0).any():
            row_errors.append("open must be > 0 for all rows")
        if (prepared["high"] <= 0).any():
            row_errors.append("high must be > 0 for all rows")
        if (prepared["low"] <= 0).any():
            row_errors.append("low must be > 0 for all rows")
        if (prepared["close"] <= 0).any():
            row_errors.append("close must be > 0 for all rows")
        if (prepared["volume"] < 0).any():
            row_errors.append("volume must be >= 0 for all rows")

        if (prepared["high"] < prepared["low"]).any():
            row_errors.append("high must be >= low for all rows")

        invalid_high = (prepared["high"] < prepared["open"]) | (
            prepared["high"] < prepared["close"]
        )
        if invalid_high.any():
            row_errors.append("high must be >= open and close for all rows")

        invalid_low = (prepared["low"] > prepared["open"]) | (
            prepared["low"] > prepared["close"]
        )
        if invalid_low.any():
            row_errors.append("low must be <= open and close for all rows")

        return row_errors

    def _validate_prepared_dataframe(
        self, prepared: pd.DataFrame, errors: list[str]
    ) -> DataImportValidationResult:
        if errors:
            return self._empty_result(errors)

        row_errors = self._validate_ohlc_constraints(prepared)
        if row_errors:
            return self._empty_result(row_errors)

        symbols = prepared["symbol"].unique()
        symbols_count = len(symbols)
        dates = sorted(prepared["date"].dropna().unique())

        for symbol in symbols:
            symbol_dates = prepared.loc[prepared["symbol"] == symbol, "date"].nunique()
            if symbol_dates < 2:
                row_errors.append(
                    f"Symbol '{symbol}' has fewer than 2 dates — need history for snapshots"
                )

        if row_errors:
            return self._empty_result(row_errors)

        warnings: list[str] = []
        present_symbols = set(symbols)
        missing_indexes = sorted(INDEX_SYMBOLS - present_symbols)
        if missing_indexes:
            warnings.append(
                f"Index symbols missing: {', '.join(missing_indexes)}"
            )

        if symbols_count < MIN_SYMBOLS_WARNING:
            warnings.append(
                f"Fewer than {MIN_SYMBOLS_WARNING} symbols ({symbols_count} found)"
            )

        if len(dates) < MIN_DATES_WARNING:
            warnings.append(
                f"Fewer than {MIN_DATES_WARNING} trading dates ({len(dates)} found)"
            )

        date_min = dates[0] if dates else None
        date_max = dates[-1] if dates else None

        return DataImportValidationResult(
            valid=True,
            rows=len(prepared),
            symbols_count=symbols_count,
            date_min=date_min,
            date_max=date_max,
            errors=[],
            warnings=warnings,
        )

    def validate_csv(self, csv_path: Path) -> DataImportValidationResult:
        """Validate a local EGX OHLCV CSV file."""
        if not csv_path.exists():
            return self._empty_result([f"File not found: {csv_path}"])

        try:
            df = pd.read_csv(csv_path)
        except Exception as exc:  # noqa: BLE001 — surface parse errors to user
            return self._empty_result([f"Unable to read CSV: {exc}"])

        if df.empty:
            return self._empty_result(["CSV file is empty"])

        prepared, prep_errors = self._prepare_dataframe(df)
        return self._validate_prepared_dataframe(prepared, prep_errors)

    def normalize_csv(
        self, input_path: Path, output_path: Path
    ) -> DataImportValidationResult:
        """Normalize a local CSV into the standard EGX OHLCV format."""
        if not input_path.exists():
            return self._empty_result([f"File not found: {input_path}"])

        try:
            df = pd.read_csv(input_path)
        except Exception as exc:  # noqa: BLE001
            return self._empty_result([f"Unable to read CSV: {exc}"])

        if df.empty:
            return self._empty_result(["CSV file is empty"])

        prepared, prep_errors = self._prepare_dataframe(df)
        if prep_errors:
            return self._empty_result(prep_errors)

        validation = self._validate_prepared_dataframe(prepared, [])
        if not validation.valid:
            return validation

        normalized = prepared.sort_values(["date", "symbol"]).reset_index(drop=True)
        normalized["volume"] = normalized["volume"].astype(int)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        normalized.to_csv(output_path, index=False)

        return self.validate_csv(output_path)


class EgxLiveSnapshotValidator:
    """Validates single-day EGX live snapshot CSV files."""

    def __init__(self) -> None:
        self._ohlcv_validator = EgxCsvImportValidator()

    def _empty_result(self, errors: list[str]) -> DataImportValidationResult:
        return DataImportValidationResult(
            valid=False,
            rows=0,
            symbols_count=0,
            date_min=None,
            date_max=None,
            errors=errors,
            warnings=[],
        )

    def _missing_required_columns(self, df: pd.DataFrame) -> list[str]:
        return [
            col for col in LIVE_SNAPSHOT_REQUIRED_COLUMNS if col not in df.columns
        ]

    def _prepare_dataframe(self, df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
        errors: list[str] = []
        normalized = self._ohlcv_validator._normalize_columns(df)

        missing = self._missing_required_columns(normalized)
        if missing:
            errors.append(f"Missing required columns: {', '.join(missing)}")
            return normalized, errors

        output_columns = [
            column
            for column in LIVE_SNAPSHOT_OUTPUT_COLUMNS
            if column in normalized.columns
        ]
        if "company_name" not in output_columns:
            output_columns = LIVE_SNAPSHOT_REQUIRED_COLUMNS

        prepared = normalized[output_columns].copy()
        prepared["symbol"] = prepared["symbol"].astype(str).str.strip()
        prepared = prepared[prepared["symbol"] != ""]

        if prepared.empty:
            errors.append("No rows with non-empty symbols")
            return prepared, errors

        try:
            prepared["date"] = pd.to_datetime(prepared["date"], errors="coerce").dt.date
        except (ValueError, TypeError):
            errors.append("Unable to parse date column")
            return prepared, errors

        if prepared["date"].isna().any():
            errors.append("One or more rows have unparseable dates")

        numeric_columns = (
            "previous_close",
            "open",
            "high",
            "low",
            "close",
            "volume",
        )
        for column in numeric_columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")

        if prepared[list(numeric_columns)].isna().any().any():
            errors.append("One or more rows have non-numeric live snapshot values")

        return prepared, errors

    def _validate_prepared_dataframe(
        self, prepared: pd.DataFrame, errors: list[str]
    ) -> DataImportValidationResult:
        if errors:
            return self._empty_result(errors)

        if (prepared["previous_close"] <= 0).any():
            return self._empty_result(["previous_close must be > 0 for all rows"])

        row_errors = self._ohlcv_validator._validate_ohlc_constraints(prepared)
        if row_errors:
            return self._empty_result(row_errors)

        symbols = prepared["symbol"].unique()
        dates = sorted(prepared["date"].dropna().unique())
        date_min = dates[0] if dates else None
        date_max = dates[-1] if dates else None

        return DataImportValidationResult(
            valid=True,
            rows=len(prepared),
            symbols_count=len(symbols),
            date_min=date_min,
            date_max=date_max,
            errors=[],
            warnings=[],
        )

    def validate_csv(self, csv_path: Path) -> DataImportValidationResult:
        """Validate a single-day EGX live snapshot CSV file."""
        if not csv_path.exists():
            return self._empty_result([f"File not found: {csv_path}"])

        try:
            df = pd.read_csv(csv_path)
        except Exception as exc:  # noqa: BLE001
            return self._empty_result([f"Unable to read CSV: {exc}"])

        if df.empty:
            return self._empty_result(["CSV file is empty"])

        prepared, prep_errors = self._prepare_dataframe(df)
        return self._validate_prepared_dataframe(prepared, prep_errors)


class DailyEgxDataImporter:
    """Import daily CSV/XLSX files into the normalized real-data master file."""

    def __init__(self) -> None:
        self._validator = EgxCsvImportValidator()

    def _read_input_file(
        self, input_path: Path
    ) -> tuple[pd.DataFrame | None, DataImportValidationResult | None]:
        if not input_path.exists():
            return None, self._validator._empty_result(
                [f"File not found: {input_path}"]
            )

        suffix = input_path.suffix.lower()
        if suffix not in SUPPORTED_IMPORT_EXTENSIONS:
            return None, self._validator._empty_result(
                [f"Unsupported file extension: {suffix}"]
            )

        try:
            if suffix == ".csv":
                df = pd.read_csv(input_path)
            else:
                df = pd.read_excel(input_path, engine="openpyxl")
        except Exception as exc:  # noqa: BLE001
            return None, self._validator._empty_result(
                [f"Unable to read file: {exc}"]
            )

        if df.empty:
            return None, self._validator._empty_result(["Input file is empty"])

        return df, None

    def _load_master_dataframe(self, master_path: Path) -> pd.DataFrame | None:
        master_df = pd.read_csv(master_path)
        prepared, prep_errors = self._validator._prepare_dataframe(master_df)
        if prep_errors:
            return None
        return prepared[REQUIRED_COLUMNS].copy()

    def import_daily_file(
        self,
        input_path: Path,
        master_path: Path,
    ) -> DataImportValidationResult:
        """Import a daily file and merge it into the normalized master CSV."""
        df, error_result = self._read_input_file(input_path)
        if error_result is not None:
            return error_result

        mapped = self._validator._normalize_columns(df)
        missing = self._validator._missing_required_columns(mapped)
        if missing:
            return self._validator._empty_result(
                [f"Unable to detect required columns: {', '.join(missing)}"]
            )

        prepared, prep_errors = self._validator._prepare_dataframe(mapped)
        if prep_errors:
            return self._validator._empty_result(prep_errors)

        ohlc_errors = self._validator._validate_ohlc_constraints(prepared)
        if ohlc_errors:
            return self._validator._empty_result(ohlc_errors)

        new_rows = prepared[REQUIRED_COLUMNS].copy()

        if master_path.exists():
            try:
                master_rows = self._load_master_dataframe(master_path)
            except Exception as exc:  # noqa: BLE001
                return self._validator._empty_result(
                    [f"Unable to read master file: {exc}"]
                )
            if master_rows is None:
                return self._validator._empty_result(
                    [f"Master file is invalid or unreadable: {master_path}"]
                )
            combined = pd.concat([master_rows, new_rows], ignore_index=True)
        else:
            combined = new_rows

        combined = combined.drop_duplicates(subset=["date", "symbol"], keep="last")
        combined = combined.sort_values(["date", "symbol"]).reset_index(drop=True)
        combined["volume"] = combined["volume"].astype(int)

        master_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_csv(master_path, index=False)

        return self._validator.validate_csv(master_path)
