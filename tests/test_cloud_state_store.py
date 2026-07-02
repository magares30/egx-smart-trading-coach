"""Tests for optional cloud persistent state storage."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.cloud_state_store import (
    EGX_STATE_GCS_BUCKET_ENV,
    GcsStateStore,
    LATEST_REPORT_JSON_KEY,
    LATEST_REPORT_TXT_KEY,
    LocalStateStore,
    PORTFOLIO_STATE_KEY,
    TRADES_KEY,
    enrich_report_payload_with_stored_portfolio,
    get_state_store,
    hydrate_local_storage_from_cloud,
    is_gcs_state_enabled,
    load_latest_report_json_payload,
    persist_latest_report,
    sync_local_storage_to_cloud,
)
from core.daily_report import save_daily_report
from core.telegram_bot import load_latest_report_payload
from tests.test_daily_report import _fake_scan_bundle
from tests.test_telegram_bot import _sample_payload


class FakeBlob:
    def __init__(self, store: dict[str, str], name: str) -> None:
        self.name = name
        self._store = store

    def exists(self) -> bool:
        return self.name in self._store

    def download_as_text(self, encoding: str = "utf-8") -> str:
        return self._store[self.name]

    def upload_from_string(self, data: str, content_type: str | None = None) -> None:
        self._store[self.name] = data


class FakeBucket:
    def __init__(self, objects: dict[str, str] | None = None) -> None:
        self._objects = objects or {}

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self._objects, name)


def test_get_state_store_falls_back_to_local() -> None:
    with patch.dict(os.environ, {}, clear=True):
        store = get_state_store()

    assert store.backend_name == "local"
    assert isinstance(store, LocalStateStore)


def test_get_state_store_uses_gcs_when_env_set() -> None:
    with patch.dict(os.environ, {EGX_STATE_GCS_BUCKET_ENV: "egx-state-test"}, clear=False):
        store = get_state_store()

    assert store.backend_name == "gcs"
    assert isinstance(store, GcsStateStore)
    assert store.bucket_name == "egx-state-test"


def test_local_state_store_reads_latest_report(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    json_path = reports_dir / "egx_daily_report_20260703_120000.json"
    txt_path = reports_dir / "egx_daily_report_20260703_120000.txt"
    payload = {"report_date": "2026-07-03"}
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    txt_path.write_text("report text", encoding="utf-8")

    store = LocalStateStore(reports_dir=reports_dir)

    assert store.read_latest_report_payload() == payload
    assert store.read_latest_report_txt() == "report text"


def test_local_state_store_writes_portfolio_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    portfolio_path = tmp_path / "storage" / "portfolio_state.json"
    monkeypatch.setattr(
        "core.cloud_state_store.settings.PORTFOLIO_STATE_PATH",
        portfolio_path,
    )
    monkeypatch.setattr(
        "core.cloud_state_store._STATE_KEY_TO_LOCAL_PATH",
        {
            PORTFOLIO_STATE_KEY: portfolio_path,
            TRADES_KEY: tmp_path / "storage" / "trades.json",
        },
    )

    store = LocalStateStore()
    store.write_text(PORTFOLIO_STATE_KEY, '{"cash": 1000}')

    assert portfolio_path.read_text(encoding="utf-8") == '{"cash": 1000}'


def test_gcs_state_store_read_write_with_fake_bucket() -> None:
    bucket = FakeBucket()
    store = GcsStateStore("egx-state-test", bucket=bucket)

    store.write_text(LATEST_REPORT_JSON_KEY, '{"report_date":"2026-07-03"}')
    store.write_text(LATEST_REPORT_TXT_KEY, "report text")

    assert store.read_text(LATEST_REPORT_JSON_KEY) == '{"report_date":"2026-07-03"}'
    assert store.read_text(LATEST_REPORT_TXT_KEY) == "report text"
    assert store.read_latest_report_payload() == {"report_date": "2026-07-03"}


def test_load_latest_report_json_payload_prefers_cloud_then_local(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    local_json = reports_dir / "egx_daily_report_20260703_120000.json"
    local_json.write_text(json.dumps({"report_date": "local"}), encoding="utf-8")

    cloud_bucket = FakeBucket(
        {
            LATEST_REPORT_JSON_KEY: json.dumps({"report_date": "cloud"}),
        }
    )
    cloud_store = GcsStateStore("egx-state-test", bucket=cloud_bucket)

    with patch.dict(os.environ, {EGX_STATE_GCS_BUCKET_ENV: "egx-state-test"}, clear=False):
        with patch("core.cloud_state_store.get_state_store", return_value=cloud_store):
            payload = load_latest_report_json_payload(reports_dir=reports_dir)

    assert payload is not None
    assert payload["report_date"] == "cloud"


def test_load_latest_report_json_payload_falls_back_to_local(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    local_json = reports_dir / "egx_daily_report_20260703_120000.json"
    local_json.write_text(json.dumps({"report_date": "local"}), encoding="utf-8")

    with patch.dict(os.environ, {}, clear=True):
        payload = load_latest_report_json_payload(reports_dir=reports_dir)

    assert payload is not None
    assert payload["report_date"] == "local"


def test_persist_latest_report_uploads_only_when_gcs_enabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bucket = FakeBucket()
    store = GcsStateStore("egx-state-test", bucket=bucket)

    with patch.dict(os.environ, {}, clear=True):
        persist_latest_report("txt", '{"ok": true}')

    assert bucket._objects == {}

    with patch.dict(os.environ, {EGX_STATE_GCS_BUCKET_ENV: "egx-state-test"}, clear=False):
        with patch("core.cloud_state_store.get_state_store", return_value=store):
            with caplog.at_level("INFO"):
                persist_latest_report("txt body", '{"report_date":"2026-07-03"}')

    assert bucket._objects[LATEST_REPORT_TXT_KEY] == "txt body"
    assert "1234567890:AA" not in caplog.text
    assert "Uploaded cloud state object: reports/latest_report.txt" in caplog.text


def test_hydrate_local_storage_from_cloud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    portfolio_path = tmp_path / "storage" / "portfolio_state.json"
    trades_path = tmp_path / "storage" / "trades.json"
    monkeypatch.setattr(
        "core.cloud_state_store._STATE_KEY_TO_LOCAL_PATH",
        {
            PORTFOLIO_STATE_KEY: portfolio_path,
            TRADES_KEY: trades_path,
        },
    )

    bucket = FakeBucket(
        {
            PORTFOLIO_STATE_KEY: '{"cash": 5000}',
            TRADES_KEY: "[]",
        }
    )
    store = GcsStateStore("egx-state-test", bucket=bucket)

    with patch.dict(os.environ, {EGX_STATE_GCS_BUCKET_ENV: "egx-state-test"}, clear=False):
        with patch("core.cloud_state_store.get_state_store", return_value=store):
            hydrate_local_storage_from_cloud()

    assert portfolio_path.read_text(encoding="utf-8") == '{"cash": 5000}'
    assert trades_path.read_text(encoding="utf-8") == "[]"


def test_sync_local_storage_to_cloud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    portfolio_path = tmp_path / "storage" / "portfolio_state.json"
    portfolio_path.parent.mkdir(parents=True)
    portfolio_path.write_text('{"cash": 9000}', encoding="utf-8")
    monkeypatch.setattr(
        "core.cloud_state_store._STATE_KEY_TO_LOCAL_PATH",
        {
            PORTFOLIO_STATE_KEY: portfolio_path,
            TRADES_KEY: tmp_path / "storage" / "trades.json",
        },
    )

    bucket = FakeBucket()
    store = GcsStateStore("egx-state-test", bucket=bucket)

    with patch.dict(os.environ, {EGX_STATE_GCS_BUCKET_ENV: "egx-state-test"}, clear=False):
        with patch("core.cloud_state_store.get_state_store", return_value=store):
            sync_local_storage_to_cloud()

    assert bucket._objects[PORTFOLIO_STATE_KEY] == '{"cash": 9000}'


def test_save_daily_report_persists_to_cloud_when_configured(tmp_path: Path) -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()
    from core.daily_report import DailyReportBuilder

    built = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
    )

    bucket = FakeBucket()
    store = GcsStateStore("egx-state-test", bucket=bucket)

    with patch.dict(os.environ, {EGX_STATE_GCS_BUCKET_ENV: "egx-state-test"}, clear=False):
        with patch("core.cloud_state_store.get_state_store", return_value=store):
            txt_path, json_path = save_daily_report(built, tmp_path)

    assert txt_path.exists()
    assert json_path.exists()
    assert LATEST_REPORT_JSON_KEY in bucket._objects
    assert LATEST_REPORT_TXT_KEY in bucket._objects


def test_telegram_load_latest_report_payload_uses_cloud_state_loader(
    tmp_path: Path,
) -> None:
    expected = _sample_payload()
    with patch(
        "core.telegram_bot.load_latest_report_json_payload",
        return_value=expected,
    ) as loader:
        payload = load_latest_report_payload(tmp_path)

    loader.assert_called_once_with(reports_dir=tmp_path)
    assert payload == expected


def test_enrich_report_payload_does_not_create_fake_portfolio() -> None:
    payload = {
        "report_date": "2026-07-03",
        "paper_portfolio": {"available": False},
        "paper_trading_performance": {"available": False},
    }

    with patch("core.cloud_state_store.hydrate_local_storage_from_cloud"):
        with patch(
            "core.portfolio_report.paper_portfolio_storage_exists",
            return_value=False,
        ):
            enriched = enrich_report_payload_with_stored_portfolio(payload)

    assert enriched == payload


def test_is_gcs_state_enabled() -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert is_gcs_state_enabled() is False
    with patch.dict(os.environ, {EGX_STATE_GCS_BUCKET_ENV: "bucket"}, clear=False):
        assert is_gcs_state_enabled() is True
