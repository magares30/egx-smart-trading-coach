"""Read public EGX company prices via GetCompanyPricesList web service."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
import time

import pandas as pd
import requests
from pydantic import BaseModel, Field

from core.egx_public_reader import REQUEST_TIMEOUT_SECONDS

EGX_WARMUP_URL = "https://www.egx.com.eg/en/prices.aspx"
EGX_COMPANY_PRICES_URL = (
    "https://www.egx.com.eg/WebService.asmx/GetCompanyPricesList"
)
DEFAULT_EGX_COMPANY_PREFIXES = ("bank", "cement", "egypt", "development")
DEBUG_PREVIEW_CHAR_LIMIT = 300

EGX_WARMUP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Connection": "keep-alive",
}

EGX_COMPANY_PRICES_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Content-Type": "application/json; charset=UTF-8",
    "Origin": "https://www.egx.com.eg",
    "Referer": EGX_WARMUP_URL,
    "X-Requested-With": "XMLHttpRequest",
}

RECORD_IDENTITY_FIELDS = (
    "Symbol",
    "symbol",
    "Code",
    "code",
    "ISIN",
    "isin",
    "CompanyName",
    "companyName",
    "Name",
    "name",
)


class EgxCompanyPricesFetchResult(BaseModel):
    prefix_text: str
    success: bool
    raw_json: dict | list | str | None = None
    records: list[dict[str, object]] = Field(default_factory=list)
    warmup_status_code: int | None = None
    status_code: int | None = None
    content_type: str | None = None
    content_length: int = 0
    debug_path: Path | None = None
    errors: list[str] = Field(default_factory=list)


class EgxCompanyPricesReadResult(BaseModel):
    prefixes: list[str]
    records: list[dict[str, object]] = Field(default_factory=list)
    saved_csv: Path | None = None
    fetch_results: list[EgxCompanyPricesFetchResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def _extract_records(raw_json: object) -> list[dict[str, object]]:
    if raw_json is None:
        return []
    if isinstance(raw_json, list):
        return [item for item in raw_json if isinstance(item, dict)]
    if isinstance(raw_json, dict):
        if "d" in raw_json:
            return _extract_records(raw_json["d"])
        return [raw_json]
    if isinstance(raw_json, str):
        stripped = raw_json.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return []
        return _extract_records(parsed)
    return []


def _record_identity(record: dict[str, object]) -> str:
    parts: list[str] = []
    for field in RECORD_IDENTITY_FIELDS:
        value = record.get(field)
        if value not in (None, ""):
            parts.append(str(value).strip().lower())
    if parts:
        return "|".join(parts)
    return json.dumps(record, sort_keys=True, default=str)


def _safe_prefix_name(prefix_text: str) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", prefix_text.strip().lower())
    return cleaned or "unknown"


class EgxCompanyPricesReader:
    """Fetch public EGX company price rows by search prefix."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _timestamp(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    def _warmup_headers(self) -> dict[str, str]:
        return dict(EGX_WARMUP_HEADERS)

    def _request_headers(self) -> dict[str, str]:
        return dict(EGX_COMPANY_PRICES_HEADERS)

    def _build_payload(self, prefix_text: str, count: int) -> dict[str, object]:
        return {"prefixText": prefix_text, "count": count}

    def _create_session(self) -> requests.Session:
        return requests.Session()

    def _warmup_session(
        self, session: requests.Session, prefix_text: str
    ) -> int:
        response = session.get(
            EGX_WARMUP_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers=self._warmup_headers(),
        )
        response.raise_for_status()
        warmup_status = response.status_code
        print(
            "EGX company prices warmup "
            f"[{prefix_text}]: status={warmup_status}"
        )
        return warmup_status

    def _print_post_metadata(
        self,
        prefix_text: str,
        *,
        status_code: int,
        content_type: str,
        content_length: int,
    ) -> None:
        print(
            "EGX company prices POST "
            f"[{prefix_text}]: status={status_code}, "
            f"content-type={content_type}, length={content_length}"
        )

    def _save_debug_response(
        self,
        prefix_text: str,
        body: str,
        *,
        warmup_status_code: int | None,
        status_code: int,
        content_type: str,
        content_length: int,
    ) -> Path:
        debug_path = (
            self.output_dir
            / f"company_prices_debug_{_safe_prefix_name(prefix_text)}_{self._timestamp()}.txt"
        )
        debug_path.write_text(
            "\n".join(
                [
                    f"Warmup HTTP status: {warmup_status_code if warmup_status_code is not None else '(none)'}",
                    f"POST HTTP status: {status_code}",
                    f"Content-Type: {content_type}",
                    f"Content length: {content_length}",
                    "",
                    body,
                ]
            ),
            encoding="utf-8",
        )
        return debug_path

    def _parse_response_body(
        self,
        prefix_text: str,
        body: str,
        *,
        warmup_status_code: int | None,
        status_code: int,
        content_type: str,
        content_length: int,
    ) -> tuple[object | None, Path | None, str | None]:
        try:
            return json.loads(body), None, None
        except json.JSONDecodeError as exc:
            debug_path = self._save_debug_response(
                prefix_text,
                body,
                warmup_status_code=warmup_status_code,
                status_code=status_code,
                content_type=content_type,
                content_length=content_length,
            )
            preview = body[:DEBUG_PREVIEW_CHAR_LIMIT]
            error = (
                f"JSON parse failed for prefix '{prefix_text}': {exc}. "
                f"warmup status={warmup_status_code}, POST status={status_code}, "
                f"content-type={content_type}, content length={content_length}, "
                f"debug={debug_path}, preview={preview!r}"
            )
            return None, debug_path, error

    def fetch_by_prefix(
        self, prefix_text: str, count: int = 20
    ) -> EgxCompanyPricesFetchResult:
        """Warm up a session, POST to GetCompanyPricesList, and parse safely."""
        payload = self._build_payload(prefix_text, count)
        headers = self._request_headers()
        last_exc: Exception | None = None
        last_warmup_status: int | None = None
        last_status_code: int | None = None
        last_content_type: str | None = None
        last_content_length = 0
        last_debug_path: Path | None = None

        for attempt in range(2):
            session = self._create_session()
            try:
                warmup_status = self._warmup_session(session, prefix_text)
                last_warmup_status = warmup_status

                response = session.post(
                    EGX_COMPANY_PRICES_URL,
                    json=payload,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    headers=headers,
                )
                response.raise_for_status()

                status_code = response.status_code
                content_type = response.headers.get("Content-Type", "unknown")
                content_length = len(response.content)
                body = response.text
                last_status_code = status_code
                last_content_type = content_type
                last_content_length = content_length

                self._print_post_metadata(
                    prefix_text,
                    status_code=status_code,
                    content_type=content_type,
                    content_length=content_length,
                )

                raw_json, debug_path, parse_error = self._parse_response_body(
                    prefix_text,
                    body,
                    warmup_status_code=warmup_status,
                    status_code=status_code,
                    content_type=content_type,
                    content_length=content_length,
                )
                if parse_error is not None:
                    last_debug_path = debug_path
                    return EgxCompanyPricesFetchResult(
                        prefix_text=prefix_text,
                        success=False,
                        warmup_status_code=warmup_status,
                        status_code=status_code,
                        content_type=content_type,
                        content_length=content_length,
                        debug_path=debug_path,
                        errors=[parse_error],
                    )

                records = _extract_records(raw_json)
                return EgxCompanyPricesFetchResult(
                    prefix_text=prefix_text,
                    success=True,
                    raw_json=raw_json if isinstance(raw_json, (dict, list, str)) else None,
                    records=records,
                    warmup_status_code=warmup_status,
                    status_code=status_code,
                    content_type=content_type,
                    content_length=content_length,
                )
            except (requests.exceptions.ConnectionError, ConnectionResetError) as exc:
                last_exc = exc
                if attempt == 0:
                    time.sleep(2)
                    continue
            except requests.RequestException as exc:
                last_exc = exc
                break
            finally:
                session.close()

        return EgxCompanyPricesFetchResult(
            prefix_text=prefix_text,
            success=False,
            warmup_status_code=last_warmup_status,
            status_code=last_status_code,
            content_type=last_content_type,
            content_length=last_content_length,
            debug_path=last_debug_path,
            errors=[
                f"Failed to fetch company prices for prefix '{prefix_text}': {last_exc}"
            ],
        )

    def _merge_records(
        self, fetch_results: list[EgxCompanyPricesFetchResult]
    ) -> list[dict[str, object]]:
        merged: list[dict[str, object]] = []
        seen: set[str] = set()

        for result in fetch_results:
            for record in result.records:
                identity = _record_identity(record)
                if identity in seen:
                    continue
                seen.add(identity)
                merged.append(record)

        return merged

    def fetch_many_prefixes(
        self, prefixes: list[str], count: int = 20
    ) -> list[dict[str, object]]:
        """Fetch multiple prefixes and merge/deduplicate company rows."""
        fetch_results = [
            self.fetch_by_prefix(prefix.strip(), count=count)
            for prefix in prefixes
            if prefix.strip()
        ]
        return self._merge_records(fetch_results)

    def save_results_csv(
        self, results: list[dict[str, object]], output_path: Path
    ) -> Path:
        """Save raw returned fields to CSV."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(results)
        frame.to_csv(output_path, index=False)
        return output_path

    def read_and_save(
        self, prefixes: list[str], count: int = 20
    ) -> EgxCompanyPricesReadResult:
        """Fetch prefixes, merge results, and save CSV snapshot."""
        cleaned_prefixes = [prefix.strip() for prefix in prefixes if prefix.strip()]
        fetch_results = [
            self.fetch_by_prefix(prefix, count=count) for prefix in cleaned_prefixes
        ]
        records = self._merge_records(fetch_results)
        errors = [
            error
            for result in fetch_results
            for error in result.errors
        ]

        saved_csv: Path | None = None
        if records:
            saved_csv = self.save_results_csv(
                records,
                self.output_dir / f"company_prices_{self._timestamp()}.csv",
            )

        return EgxCompanyPricesReadResult(
            prefixes=cleaned_prefixes,
            records=records,
            saved_csv=saved_csv,
            fetch_results=fetch_results,
            errors=errors,
        )
