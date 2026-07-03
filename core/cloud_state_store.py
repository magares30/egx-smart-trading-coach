"""Optional persistent cloud state for reports and paper-trading storage."""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from config import settings
from core.cloud_report_runner import find_latest_report_json

logger = logging.getLogger(__name__)

EGX_STATE_GCS_BUCKET_ENV = "EGX_STATE_GCS_BUCKET"
LATEST_REPORT_TXT_KEY = "reports/latest_report.txt"
LATEST_REPORT_JSON_KEY = "reports/latest_report.json"
PORTFOLIO_STATE_KEY = "storage/portfolio_state.json"
TRADES_KEY = "storage/trades.json"
MARKET_MEMORY_KEY = "storage/market_memory.json"

_STATE_KEY_TO_LOCAL_PATH = {
    PORTFOLIO_STATE_KEY: settings.PORTFOLIO_STATE_PATH,
    TRADES_KEY: settings.TRADES_PATH,
    MARKET_MEMORY_KEY: settings.MARKET_MEMORY_PATH,
}


def is_gcs_state_enabled() -> bool:
    """Return True when GCS persistent state is configured."""
    return bool(os.environ.get(EGX_STATE_GCS_BUCKET_ENV, "").strip())


class StateStore(ABC):
    """Abstract storage backend for latest report and paper-trading state."""

    backend_name: str

    @abstractmethod
    def read_text(self, key: str) -> str | None:
        """Read one object as UTF-8 text."""

    @abstractmethod
    def write_text(self, key: str, content: str) -> None:
        """Write one object as UTF-8 text."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True when the object exists."""

    def read_latest_report_json_text(self) -> str | None:
        return self.read_text(LATEST_REPORT_JSON_KEY)

    def read_latest_report_txt(self) -> str | None:
        return self.read_text(LATEST_REPORT_TXT_KEY)

    def write_latest_report(self, txt_content: str, json_content: str) -> None:
        self.write_text(LATEST_REPORT_TXT_KEY, txt_content)
        self.write_text(LATEST_REPORT_JSON_KEY, json_content)

    def read_latest_report_payload(self) -> dict[str, Any] | None:
        json_text = self.read_latest_report_json_text()
        if not json_text:
            return None
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None


class LocalStateStore(StateStore):
    """Local filesystem storage using existing project paths."""

    backend_name = "local"

    def __init__(self, *, reports_dir: Path | None = None) -> None:
        self.reports_dir = reports_dir or settings.REPORTS_DIR

    def read_text(self, key: str) -> str | None:
        if key == LATEST_REPORT_JSON_KEY:
            report_path = find_latest_report_json(self.reports_dir)
            if report_path is None:
                return None
            try:
                return report_path.read_text(encoding="utf-8")
            except OSError:
                return None

        if key == LATEST_REPORT_TXT_KEY:
            json_path = find_latest_report_json(self.reports_dir)
            if json_path is None:
                return None
            txt_path = json_path.with_suffix(".txt")
            if not txt_path.is_file():
                return None
            try:
                return txt_path.read_text(encoding="utf-8")
            except OSError:
                return None

        local_path = _STATE_KEY_TO_LOCAL_PATH.get(key)
        if local_path is None or not local_path.is_file():
            return None
        try:
            return local_path.read_text(encoding="utf-8")
        except OSError:
            return None

    def write_text(self, key: str, content: str) -> None:
        local_path = _STATE_KEY_TO_LOCAL_PATH.get(key)
        if local_path is None:
            return
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(content, encoding="utf-8")

    def exists(self, key: str) -> bool:
        if key == LATEST_REPORT_JSON_KEY:
            return find_latest_report_json(self.reports_dir) is not None
        if key == LATEST_REPORT_TXT_KEY:
            json_path = find_latest_report_json(self.reports_dir)
            return json_path is not None and json_path.with_suffix(".txt").is_file()
        local_path = _STATE_KEY_TO_LOCAL_PATH.get(key)
        return local_path is not None and local_path.is_file()


class GcsStateStore(StateStore):
    """Google Cloud Storage backend for persistent Cloud Run state."""

    backend_name = "gcs"

    def __init__(
        self,
        bucket_name: str,
        *,
        bucket: Any | None = None,
    ) -> None:
        self.bucket_name = bucket_name
        self._bucket = bucket

    def _get_bucket(self) -> Any:
        if self._bucket is not None:
            return self._bucket
        from google.cloud import storage

        client = storage.Client()
        self._bucket = client.bucket(self.bucket_name)
        return self._bucket

    def read_text(self, key: str) -> str | None:
        bucket = self._get_bucket()
        blob = bucket.blob(key)
        if not blob.exists():
            return None
        try:
            return blob.download_as_text(encoding="utf-8")
        except OSError:
            logger.warning("Failed to read cloud state object: %s", key)
            return None

    def write_text(self, key: str, content: str) -> None:
        bucket = self._get_bucket()
        blob = bucket.blob(key)
        content_type = "application/json" if key.endswith(".json") else "text/plain"
        blob.upload_from_string(content, content_type=content_type)
        logger.info("Uploaded cloud state object: %s", key)

    def exists(self, key: str) -> bool:
        bucket = self._get_bucket()
        return bool(bucket.blob(key).exists())


def get_state_store(*, reports_dir: Path | None = None) -> StateStore:
    """Return GCS store when configured, otherwise local filesystem store."""
    bucket_name = os.environ.get(EGX_STATE_GCS_BUCKET_ENV, "").strip()
    if bucket_name:
        return GcsStateStore(bucket_name=bucket_name)
    return LocalStateStore(reports_dir=reports_dir)


def hydrate_local_storage_from_cloud() -> None:
    """Download portfolio/journal objects from GCS when local files are missing."""
    if not is_gcs_state_enabled():
        return

    store = get_state_store()
    if not isinstance(store, GcsStateStore):
        return

    for key, local_path in _STATE_KEY_TO_LOCAL_PATH.items():
        if local_path.exists():
            continue
        content = store.read_text(key)
        if content is None:
            continue
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(content, encoding="utf-8")
        logger.info("Hydrated local state file from cloud: %s", local_path.name)


def sync_local_storage_to_cloud() -> None:
    """Upload local portfolio/journal files to GCS when they exist."""
    if not is_gcs_state_enabled():
        return

    store = get_state_store()
    if not isinstance(store, GcsStateStore):
        return

    for key, local_path in _STATE_KEY_TO_LOCAL_PATH.items():
        if not local_path.is_file():
            continue
        try:
            content = local_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Failed to read local state file for cloud sync: %s", local_path)
            continue
        store.write_text(key, content)


def persist_latest_report(txt_content: str, json_content: str) -> None:
    """Upload latest report text/json to GCS when configured."""
    if not is_gcs_state_enabled():
        return

    store = get_state_store()
    if not isinstance(store, GcsStateStore):
        return

    store.write_latest_report(txt_content, json_content)
    sync_local_storage_to_cloud()


def load_latest_report_json_payload(
    *,
    reports_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Load latest report JSON from cloud first, then local fallback."""
    if is_gcs_state_enabled():
        cloud_store = get_state_store(reports_dir=reports_dir)
        if isinstance(cloud_store, GcsStateStore):
            payload = cloud_store.read_latest_report_payload()
            if payload is not None:
                return enrich_report_payload_with_stored_portfolio(payload)

    local_store = LocalStateStore(reports_dir=reports_dir)
    payload = local_store.read_latest_report_payload()
    if payload is None:
        return None
    return enrich_report_payload_with_stored_portfolio(payload)


def enrich_report_payload_with_stored_portfolio(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Attach live portfolio/P&L blocks when report JSON lacks them but storage exists."""
    portfolio_block = payload.get("paper_portfolio") or {}
    if portfolio_block.get("available"):
        return payload

    hydrate_local_storage_from_cloud()
    from core.portfolio_report import (
        build_daily_report_paper_portfolio,
        build_daily_report_paper_trading_performance,
        load_portfolio_for_marking,
        load_trade_journal_for_report,
        paper_portfolio_storage_exists,
    )

    if not paper_portfolio_storage_exists():
        return payload

    portfolio = load_portfolio_for_marking()
    if portfolio is None:
        return payload

    _, portfolio_payload = build_daily_report_paper_portfolio(
        portfolio,
        latest_prices=None,
        storage_available=True,
    )
    _, performance_payload = build_daily_report_paper_trading_performance(
        portfolio,
        load_trade_journal_for_report(),
        latest_prices=None,
        paper_portfolio_payload=portfolio_payload,
        storage_available=True,
    )
    if not portfolio_payload.get("available") and not performance_payload.get("available"):
        return payload

    enriched = dict(payload)
    enriched["paper_portfolio"] = portfolio_payload
    enriched["paper_trading_performance"] = performance_payload
    metadata = dict(enriched.get("report_metadata") or {})
    metadata["paper_portfolio_present"] = bool(portfolio_payload.get("available"))
    metadata["paper_performance_present"] = bool(performance_payload.get("available"))
    metadata["paper_portfolio_storage_on_server"] = True
    metadata["paper_portfolio_enriched_from_state_store"] = True
    enriched["report_metadata"] = metadata
    return enriched
