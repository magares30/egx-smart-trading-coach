"""Probe public EGX endpoint variants using discovered type values."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
import time

import requests
from pydantic import BaseModel, Field

from core.egx_endpoint_probe import EGX_DISCOVERED_ENDPOINTS
from core.egx_public_reader import EGX_REQUEST_HEADERS, REQUEST_TIMEOUT_SECONDS

EGX_TYPE_VALUES: tuple[int, ...] = (11, 12, 25)
PREVIEW_CHAR_LIMIT = 500


class EgxResponseKind(str, Enum):
    JSON = "json"
    HTML = "html"
    CSV = "csv"
    UNKNOWN = "unknown"


class EgxTypeProbeResult(BaseModel):
    endpoint: str
    type_value: int
    url: str
    success: bool
    status_code: int | None = None
    content_type: str | None = None
    content_length: int = 0
    preview: str = ""
    response_kind: EgxResponseKind = EgxResponseKind.UNKNOWN
    saved_path: Path | None = None
    errors: list[str] = Field(default_factory=list)


def build_type_probe_url(base_url: str, type_value: int) -> str:
    """Build a public EGX endpoint URL with a type query parameter."""
    return f"{base_url}?type={type_value}"


def detect_response_kind(body: str, content_type: str | None) -> EgxResponseKind:
    """Guess whether a response body looks like JSON, HTML, CSV, or unknown."""
    content_type_lower = (content_type or "").lower()
    stripped = body.lstrip()
    stripped_lower = stripped.lower()

    if "json" in content_type_lower:
        return EgxResponseKind.JSON
    if "html" in content_type_lower:
        return EgxResponseKind.HTML
    if "csv" in content_type_lower or "comma-separated" in content_type_lower:
        return EgxResponseKind.CSV

    if stripped.startswith(("{", "[")):
        return EgxResponseKind.JSON
    if (
        stripped_lower.startswith("<!doctype")
        or stripped_lower.startswith("<html")
        or "<html" in stripped_lower[:200]
    ):
        return EgxResponseKind.HTML

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) >= 2 and all("," in line for line in lines[:3]):
        return EgxResponseKind.CSV

    return EgxResponseKind.UNKNOWN


class EgxTypeEndpointProbe:
    """Request public EGX endpoint variants and save raw response bodies."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _timestamp(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    def _request_headers(self) -> dict[str, str]:
        return dict(EGX_REQUEST_HEADERS)

    def _decode_body(self, content: bytes) -> str:
        return content.decode("utf-8", errors="replace")

    def _save_response(self, endpoint: str, type_value: int, body: str) -> Path:
        saved_path = (
            self.output_dir
            / f"type_probe_{endpoint}_{type_value}_{self._timestamp()}.txt"
        )
        saved_path.write_text(body, encoding="utf-8")
        return saved_path

    def probe_variant(self, endpoint: str, type_value: int) -> EgxTypeProbeResult:
        """Request one public EGX endpoint with a type query parameter."""
        base_url = EGX_DISCOVERED_ENDPOINTS[endpoint]
        url = build_type_probe_url(base_url, type_value)
        headers = self._request_headers()
        last_exc: Exception | None = None

        for attempt in range(2):
            try:
                response = requests.get(
                    url,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    headers=headers,
                )
                body = self._decode_body(response.content)
                content_type = response.headers.get("Content-Type", "unknown")
                saved_path = self._save_response(endpoint, type_value, body)
                return EgxTypeProbeResult(
                    endpoint=endpoint,
                    type_value=type_value,
                    url=url,
                    success=True,
                    status_code=response.status_code,
                    content_type=content_type,
                    content_length=len(response.content),
                    preview=body[:PREVIEW_CHAR_LIMIT],
                    response_kind=detect_response_kind(body, content_type),
                    saved_path=saved_path,
                )
            except (requests.exceptions.ConnectionError, ConnectionResetError) as exc:
                last_exc = exc
                if attempt == 0:
                    time.sleep(2)
                    continue
            except requests.RequestException as exc:
                last_exc = exc
                break

        return EgxTypeProbeResult(
            endpoint=endpoint,
            type_value=type_value,
            url=url,
            success=False,
            errors=[f"Failed to probe EGX type endpoint: {last_exc}"],
        )

    def probe_all(self) -> list[EgxTypeProbeResult]:
        """Probe all discovered endpoints for each type value."""
        results: list[EgxTypeProbeResult] = []
        for endpoint in EGX_DISCOVERED_ENDPOINTS:
            for type_value in EGX_TYPE_VALUES:
                results.append(self.probe_variant(endpoint, type_value))
        return results
