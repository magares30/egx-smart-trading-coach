"""Tests for daily real EGX data import into the master CSV."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.data_import import DailyEgxDataImporter, REQUIRED_COLUMNS

pytest.importorskip("openpyxl")


def _daily_rows(
    day: str,
    symbols: list[str] | None = None,
    *,
    close_overrides: dict[str, float] | None = None,
) -> list[dict[str, object]]:
    symbols = symbols or ["COMI", "HRHO"]
    close_overrides = close_overrides or {}
    rows: list[dict[str, object]] = []
    for symbol in symbols:
        base = 10.0 if symbol == "COMI" else 20.0
        close = close_overrides.get(symbol, base + 0.5)
        high = max(base + 1.0, close)
        rows.append(
            {
                "date": day,
                "symbol": symbol,
                "open": base,
                "high": high,
                "low": base - 0.5,
                "close": close,
                "volume": 1000,
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows, columns=REQUIRED_COLUMNS).to_csv(path, index=False)


def _write_xlsx(path: Path, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows, columns=REQUIRED_COLUMNS).to_excel(path, index=False)


@pytest.fixture
def importer() -> DailyEgxDataImporter:
    return DailyEgxDataImporter()


def test_import_daily_file_creates_master_from_valid_csv(
    importer: DailyEgxDataImporter, tmp_path: Path
) -> None:
    input_path = tmp_path / "day1.csv"
    master_path = tmp_path / "master.csv"
    _write_csv(
        input_path,
        _daily_rows("2026-01-01") + _daily_rows("2026-01-02"),
    )

    result = importer.import_daily_file(input_path, master_path)

    assert master_path.exists()
    assert result.valid is True
    assert result.rows == 4
    master = pd.read_csv(master_path)
    assert list(master.columns) == REQUIRED_COLUMNS


def test_import_daily_file_appends_new_date(
    importer: DailyEgxDataImporter, tmp_path: Path
) -> None:
    master_path = tmp_path / "master.csv"
    _write_csv(
        master_path,
        _daily_rows("2026-01-01") + _daily_rows("2026-01-02"),
    )
    input_path = tmp_path / "day3.csv"
    _write_csv(input_path, _daily_rows("2026-01-03"))

    result = importer.import_daily_file(input_path, master_path)

    assert result.valid is True
    assert result.rows == 6
    master = pd.read_csv(master_path, parse_dates=["date"])
    assert master["date"].max() == pd.Timestamp("2026-01-03")


def test_duplicate_date_symbol_keeps_latest_imported_row(
    importer: DailyEgxDataImporter, tmp_path: Path
) -> None:
    master_path = tmp_path / "master.csv"
    _write_csv(
        master_path,
        _daily_rows("2026-01-01") + _daily_rows("2026-01-02"),
    )
    input_path = tmp_path / "day2_update.csv"
    _write_csv(
        input_path,
        _daily_rows("2026-01-02", close_overrides={"COMI": 99.99}),
    )

    result = importer.import_daily_file(input_path, master_path)

    assert result.valid is True
    master = pd.read_csv(master_path)
    comi_row = master[
        (master["date"] == "2026-01-02") & (master["symbol"] == "COMI")
    ]
    assert len(comi_row) == 1
    assert comi_row.iloc[0]["close"] == pytest.approx(99.99)


def test_unsupported_file_extension_returns_invalid(
    importer: DailyEgxDataImporter, tmp_path: Path
) -> None:
    input_path = tmp_path / "daily.txt"
    input_path.write_text("not a csv", encoding="utf-8")
    master_path = tmp_path / "master.csv"

    result = importer.import_daily_file(input_path, master_path)

    assert result.valid is False
    assert any("Unsupported file extension" in error for error in result.errors)
    assert not master_path.exists()


def test_missing_required_columns_returns_invalid(
    importer: DailyEgxDataImporter, tmp_path: Path
) -> None:
    input_path = tmp_path / "bad.csv"
    pd.DataFrame({"date": ["2026-01-01"], "symbol": ["COMI"]}).to_csv(
        input_path, index=False
    )
    master_path = tmp_path / "master.csv"

    result = importer.import_daily_file(input_path, master_path)

    assert result.valid is False
    assert any("Unable to detect required columns" in error for error in result.errors)


def test_arabic_column_aliases_are_detected(
    importer: DailyEgxDataImporter, tmp_path: Path
) -> None:
    input_path = tmp_path / "arabic.csv"
    rows = _daily_rows("2026-01-01") + _daily_rows("2026-01-02")
    arabic_df = pd.DataFrame(
        {
            "التاريخ": [row["date"] for row in rows],
            "رمز": [row["symbol"] for row in rows],
            "الفتح": [row["open"] for row in rows],
            "أعلى": [row["high"] for row in rows],
            "أدنى": [row["low"] for row in rows],
            "الاغلاق": [row["close"] for row in rows],
            "حجم التداول": [row["volume"] for row in rows],
        }
    )
    arabic_df.to_csv(input_path, index=False)
    master_path = tmp_path / "master.csv"

    result = importer.import_daily_file(input_path, master_path)

    assert result.valid is True
    master = pd.read_csv(master_path)
    assert list(master.columns) == REQUIRED_COLUMNS
    assert set(master["symbol"]) == {"COMI", "HRHO"}


def test_xlsx_input_is_accepted(
    importer: DailyEgxDataImporter, tmp_path: Path
) -> None:
    input_path = tmp_path / "daily.xlsx"
    master_path = tmp_path / "master.csv"
    _write_xlsx(
        input_path,
        _daily_rows("2026-01-01") + _daily_rows("2026-01-02"),
    )

    result = importer.import_daily_file(input_path, master_path)

    assert result.valid is True
    assert master_path.exists()
    assert result.date_min == date(2026, 1, 1)
    assert result.date_max == date(2026, 1, 2)
