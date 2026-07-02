"""Tests for real EGX CSV import validation and normalization."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.data_import import EgxCsvImportValidator

REQUIRED_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume"]


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    df = pd.DataFrame(rows, columns=REQUIRED_COLUMNS)
    df.to_csv(path, index=False)


def _valid_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    symbols = [
        "EGX30",
        "EGX70",
        "COMI",
        "HRHO",
        "EFIH",
        "FWRY",
        "TMGH",
        "SWDY",
        "EAST",
        "CIRA",
    ]
    dates = [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
        "2026-01-06",
        "2026-01-07",
    ]
    for symbol in symbols:
        for idx, day in enumerate(dates):
            base = 10.0 + idx
            rows.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "open": base,
                    "high": base + 1.0,
                    "low": base - 0.5,
                    "close": base + 0.5,
                    "volume": 1000 + idx * 100,
                }
            )
    return rows


@pytest.fixture
def validator() -> EgxCsvImportValidator:
    return EgxCsvImportValidator()


def test_valid_csv_passes_validation(
    validator: EgxCsvImportValidator, tmp_path: Path
) -> None:
    csv_path = tmp_path / "valid.csv"
    _write_csv(csv_path, _valid_rows())

    result = validator.validate_csv(csv_path)

    assert result.valid is True
    assert result.rows == 50
    assert result.symbols_count == 10
    assert result.date_min == date(2026, 1, 1)
    assert result.date_max == date(2026, 1, 7)
    assert result.errors == []


def test_missing_required_columns_fails(
    validator: EgxCsvImportValidator, tmp_path: Path
) -> None:
    csv_path = tmp_path / "missing_cols.csv"
    pd.DataFrame({"date": ["2026-01-01"], "symbol": ["COMI"]}).to_csv(
        csv_path, index=False
    )

    result = validator.validate_csv(csv_path)

    assert result.valid is False
    assert any("Missing required columns" in error for error in result.errors)


def test_invalid_ohlc_relationship_fails(
    validator: EgxCsvImportValidator, tmp_path: Path
) -> None:
    csv_path = tmp_path / "invalid_ohlc.csv"
    rows = _valid_rows()
    rows[0]["high"] = 1.0
    rows[0]["low"] = 5.0
    _write_csv(csv_path, rows)

    result = validator.validate_csv(csv_path)

    assert result.valid is False
    assert any("high must be >= low" in error for error in result.errors)


def test_missing_index_symbols_creates_warning(
    validator: EgxCsvImportValidator, tmp_path: Path
) -> None:
    csv_path = tmp_path / "no_indexes.csv"
    rows = [
        {
            "date": "2026-01-01",
            "symbol": "COMI",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "volume": 1000,
        },
        {
            "date": "2026-01-02",
            "symbol": "COMI",
            "open": 10.5,
            "high": 11.5,
            "low": 10.0,
            "close": 11.0,
            "volume": 1100,
        },
    ]
    _write_csv(csv_path, rows)

    result = validator.validate_csv(csv_path)

    assert result.valid is True
    assert any("EGX30" in warning for warning in result.warnings)
    assert any("EGX70" in warning for warning in result.warnings)


def test_normalize_csv_writes_normalized_output(
    validator: EgxCsvImportValidator, tmp_path: Path
) -> None:
    input_path = tmp_path / "raw.csv"
    output_path = tmp_path / "normalized.csv"
    raw = pd.DataFrame(_valid_rows())
    raw.columns = ["Date", "Symbol", "Open", "High", "Low", "Close", "Volume"]
    raw.to_csv(input_path, index=False)

    result = validator.normalize_csv(input_path, output_path)

    assert result.valid is True
    assert output_path.exists()
    normalized = pd.read_csv(output_path)
    assert list(normalized.columns) == REQUIRED_COLUMNS


def test_single_date_fails_historical_validation(
    validator: EgxCsvImportValidator, tmp_path: Path
) -> None:
    csv_path = tmp_path / "single_day.csv"
    _write_csv(
        csv_path,
        [
            {
                "date": "2026-01-01",
                "symbol": "COMI",
                "open": 10.0,
                "high": 11.0,
                "low": 9.5,
                "close": 10.5,
                "volume": 1000,
            }
        ],
    )

    result = validator.validate_csv(csv_path)

    assert result.valid is False
    assert any("fewer than 2 dates" in error for error in result.errors)


def test_normalize_csv_sorts_by_date_then_symbol(
    validator: EgxCsvImportValidator, tmp_path: Path
) -> None:
    input_path = tmp_path / "unsorted.csv"
    output_path = tmp_path / "sorted.csv"
    rows = [
        {
            "date": "2026-01-02",
            "symbol": "HRHO",
            "open": 10.5,
            "high": 11.5,
            "low": 10.0,
            "close": 11.0,
            "volume": 1100,
        },
        {
            "date": "2026-01-01",
            "symbol": "COMI",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "volume": 1000,
        },
        {
            "date": "2026-01-01",
            "symbol": "HRHO",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "volume": 1000,
        },
        {
            "date": "2026-01-02",
            "symbol": "COMI",
            "open": 10.5,
            "high": 11.5,
            "low": 10.0,
            "close": 11.0,
            "volume": 1100,
        },
    ]
    _write_csv(input_path, rows)

    result = validator.normalize_csv(input_path, output_path)

    assert result.valid is True
    normalized = pd.read_csv(output_path, parse_dates=["date"])
    sort_keys = list(
        zip(normalized["date"], normalized["symbol"], strict=True)
    )
    assert sort_keys == sorted(sort_keys)
