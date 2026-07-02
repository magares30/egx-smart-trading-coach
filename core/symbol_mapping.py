"""Normalize EGX company names to ticker symbols for live snapshots."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from config.egx_symbol_map import EGX_COMPANY_NAME_TO_SYMBOL
from core.data_import import LIVE_SNAPSHOT_OUTPUT_COLUMNS, LIVE_SNAPSHOT_REQUIRED_COLUMNS

SYMBOL_MAPPING_SUMMARY = (
    "EGX symbol mapping: mapped {mapped_rows} rows, unmapped {unmapped_rows} rows."
)


class MappingResult(BaseModel):
    total_rows: int = 0
    mapped_rows: int = 0
    unmapped_rows: int = 0
    mapped_symbols: list[str] = Field(default_factory=list)
    unmapped_names: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def normalize_company_name(name: str) -> str:
    """Normalize a company name for fuzzy dictionary lookup."""
    text = str(name).strip().lower()
    text = re.sub(r"[.,\-_/()&']", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _build_normalized_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for company_name, symbol in EGX_COMPANY_NAME_TO_SYMBOL.items():
        lookup[normalize_company_name(company_name)] = symbol
    return lookup


_NORMALIZED_LOOKUP = _build_normalized_lookup()


def map_egx_name_to_symbol(name: str) -> str:
    """Map an EGX company name to a ticker symbol when known."""
    raw = str(name).strip()
    if not raw:
        return raw

    if raw in EGX_COMPANY_NAME_TO_SYMBOL:
        return EGX_COMPANY_NAME_TO_SYMBOL[raw]

    normalized = normalize_company_name(raw)
    if normalized in _NORMALIZED_LOOKUP:
        return _NORMALIZED_LOOKUP[normalized]

    return raw


def _map_snapshot_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, MappingResult]:
    if df.empty:
        return df.copy(), MappingResult()

    working = df.copy()
    if "symbol" not in working.columns:
        raise ValueError("Live snapshot CSV is missing required column: symbol")

    company_names = working["symbol"].astype(str).str.strip()
    mapped_symbols: list[str] = []
    unmapped_names: list[str] = []
    mapped_rows = 0
    unmapped_rows = 0

    new_symbols: list[str] = []
    for original_name in company_names:
        ticker = map_egx_name_to_symbol(original_name)
        new_symbols.append(ticker)
        if ticker != original_name:
            mapped_rows += 1
            if ticker not in mapped_symbols:
                mapped_symbols.append(ticker)
        else:
            unmapped_rows += 1
            if original_name and original_name not in unmapped_names:
                unmapped_names.append(original_name)

    working["company_name"] = company_names
    working["symbol"] = new_symbols

    output_columns = [
        column for column in LIVE_SNAPSHOT_OUTPUT_COLUMNS if column in working.columns
    ]
    mapped_df = working[output_columns].copy()

    result = MappingResult(
        total_rows=len(mapped_df),
        mapped_rows=mapped_rows,
        unmapped_rows=unmapped_rows,
        mapped_symbols=sorted(mapped_symbols),
        unmapped_names=sorted(unmapped_names),
        warnings=[
            SYMBOL_MAPPING_SUMMARY.format(
                mapped_rows=mapped_rows,
                unmapped_rows=unmapped_rows,
            )
        ],
    )
    return mapped_df, result


def apply_symbol_mapping_to_snapshot_dataframe(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, MappingResult]:
    """Map company names to ticker symbols in a live snapshot dataframe."""
    return _map_snapshot_dataframe(df)


def apply_symbol_mapping_to_snapshot_csv(
    input_csv: Path,
    output_csv: Path,
) -> MappingResult:
    """Read a live snapshot CSV, map company names to tickers, and save output."""
    if not input_csv.exists():
        return MappingResult(
            warnings=[f"Live snapshot file not found: {input_csv}"],
        )

    try:
        frame = pd.read_csv(input_csv)
    except Exception as exc:  # noqa: BLE001
        return MappingResult(warnings=[f"Unable to read live snapshot CSV: {exc}"])

    missing = [
        column for column in LIVE_SNAPSHOT_REQUIRED_COLUMNS if column not in frame.columns
    ]
    if missing:
        return MappingResult(
            warnings=[
                "Missing required live snapshot columns: " + ", ".join(missing)
            ],
        )

    mapped_df, result = _map_snapshot_dataframe(frame)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    mapped_df.to_csv(output_csv, index=False)
    return result
