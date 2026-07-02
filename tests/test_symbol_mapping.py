"""Tests for EGX company-name to ticker symbol mapping."""

from pathlib import Path

import pandas as pd

from core.data_import import LIVE_SNAPSHOT_OUTPUT_COLUMNS
from core.symbol_mapping import (
    MappingResult,
    apply_symbol_mapping_to_snapshot_csv,
    apply_symbol_mapping_to_snapshot_dataframe,
    map_egx_name_to_symbol,
    normalize_company_name,
)


def test_exact_company_name_maps_to_comi() -> None:
    name = "Commercial International Bank-Egypt (CIB)"
    assert map_egx_name_to_symbol(name) == "COMI"


def test_alias_maps_to_fwry() -> None:
    assert map_egx_name_to_symbol("Fawry") == "FWRY"


def test_unknown_name_returns_original() -> None:
    assert map_egx_name_to_symbol("Unknown Company XYZ") == "Unknown Company XYZ"


def test_normalize_company_name_collapses_whitespace_and_punctuation() -> None:
    assert (
        normalize_company_name("  Commercial   International Bank-Egypt (CIB)  ")
        == normalize_company_name("Commercial International Bank-Egypt (CIB)")
    )


def test_csv_mapping_creates_company_name_column(tmp_path: Path) -> None:
    input_csv = tmp_path / "input.csv"
    output_csv = tmp_path / "output.csv"
    pd.DataFrame(
        [
            {
                "date": "2026-01-07",
                "symbol": "Commercial International Bank-Egypt (CIB)",
                "previous_close": 80.0,
                "open": 79.5,
                "high": 81.0,
                "low": 79.0,
                "close": 80.5,
                "volume": 1000,
            }
        ]
    ).to_csv(input_csv, index=False)

    result = apply_symbol_mapping_to_snapshot_csv(input_csv, output_csv)

    assert isinstance(result, MappingResult)
    assert output_csv.exists()
    frame = pd.read_csv(output_csv)
    assert "company_name" in frame.columns
    assert frame.loc[0, "symbol"] == "COMI"
    assert (
        frame.loc[0, "company_name"]
        == "Commercial International Bank-Egypt (CIB)"
    )


def test_mapped_symbol_replaces_company_name_in_dataframe() -> None:
    frame = pd.DataFrame(
        [
            {
                "date": "2026-01-07",
                "symbol": "Fawry For Banking Technology And Electronic Payment",
                "previous_close": 6.0,
                "open": 5.9,
                "high": 6.2,
                "low": 5.8,
                "close": 6.1,
                "volume": 500,
            }
        ]
    )

    mapped, result = apply_symbol_mapping_to_snapshot_dataframe(frame)

    assert mapped.loc[0, "symbol"] == "FWRY"
    assert result.mapped_rows == 1
    assert result.unmapped_rows == 0
    assert "FWRY" in result.mapped_symbols


def test_unmapped_row_remains_unchanged() -> None:
    frame = pd.DataFrame(
        [
            {
                "date": "2026-01-07",
                "symbol": "Some Unknown Listed Company",
                "previous_close": 10.0,
                "open": 9.8,
                "high": 10.2,
                "low": 9.7,
                "close": 10.1,
                "volume": 100,
            }
        ]
    )

    mapped, result = apply_symbol_mapping_to_snapshot_dataframe(frame)

    assert mapped.loc[0, "symbol"] == "Some Unknown Listed Company"
    assert mapped.loc[0, "company_name"] == "Some Unknown Listed Company"
    assert result.mapped_rows == 0
    assert result.unmapped_rows == 1
    assert "Some Unknown Listed Company" in result.unmapped_names


def test_mapping_result_counts_mapped_and_unmapped() -> None:
    frame = pd.DataFrame(
        [
            {
                "date": "2026-01-07",
                "symbol": "Fawry",
                "previous_close": 6.0,
                "open": 5.9,
                "high": 6.2,
                "low": 5.8,
                "close": 6.1,
                "volume": 500,
            },
            {
                "date": "2026-01-07",
                "symbol": "Unknown Co",
                "previous_close": 10.0,
                "open": 9.8,
                "high": 10.2,
                "low": 9.7,
                "close": 10.1,
                "volume": 100,
            },
        ]
    )

    mapped, result = apply_symbol_mapping_to_snapshot_dataframe(frame)

    assert result.total_rows == 2
    assert result.mapped_rows == 1
    assert result.unmapped_rows == 1
    assert list(mapped.columns) == LIVE_SNAPSHOT_OUTPUT_COLUMNS
