"""Tests for EGX live ingest warning persistence."""

from pathlib import Path

from core.live_ingest import load_ingest_warnings, save_ingest_warnings


def test_save_and_load_ingest_warnings_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "egx_live_ingest_warnings.json"
    warnings = ["Only 120 valid symbols after normalization", "Traded Stocks filter may be active"]

    save_ingest_warnings(path, warnings)

    assert path.exists()
    assert load_ingest_warnings(path) == warnings


def test_load_ingest_warnings_returns_empty_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    assert load_ingest_warnings(path) == []


def test_load_ingest_warnings_returns_empty_for_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_ingest_warnings(path) == []
