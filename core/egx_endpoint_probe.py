"""Probe public EGX endpoints discovered from official page HTML."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import time

import requests
from pydantic import BaseModel, Field

from core.egx_public_reader import EGX_REQUEST_HEADERS, REQUEST_TIMEOUT_SECONDS

EGX_DISCOVERED_ENDPOINTS: dict[str, str] = {
    "GetPricesJson": "https://egx.com.eg/en/GetPricesJson.aspx",
    "MarketDataService": "https://egx.com.eg/api/MarketDataService.ashx",
    "MarketFrame": "https://egx.com.eg/en/MarketFrame.aspx",
}

PREVIEW_CHAR_LIMIT = 500


class EgxEndpointProbeResult(BaseModel):
    name: str
    url: str
    success: bool
    status_code: int | None = None
    content_type: str | None = None
    content_length: int = 0
    preview: str = ""
    saved_path: Path | None = None
    errors: list[str] = Field(default_factory=list)


class EgxEndpointProbe:
    """Request public EGX endpoints and save raw response bodies for discovery."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _timestamp(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    def _request_headers(self) -> dict[str, str]:
        return dict(EGX_REQUEST_HEADERS)

    def _decode_body(self, content: bytes) -> str:
        return content.decode("utf-8", errors="replace")

    def _save_response(self, name: str, body: str) -> Path:
        saved_path = self.output_dir / f"probe_{name}_{self._timestamp()}.txt"
        saved_path.write_text(body, encoding="utf-8")
        return saved_path

    def probe_endpoint(self, name: str, url: str) -> EgxEndpointProbeResult:
        """Request one public EGX endpoint and save the raw response body."""
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
                saved_path = self._save_response(name, body)
                return EgxEndpointProbeResult(
                    name=name,
                    url=url,
                    success=True,
                    status_code=response.status_code,
                    content_type=response.headers.get("Content-Type", "unknown"),
                    content_length=len(response.content),
                    preview=body[:PREVIEW_CHAR_LIMIT],
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

        return EgxEndpointProbeResult(
            name=name,
            url=url,
            success=False,
            errors=[f"Failed to probe EGX endpoint: {last_exc}"],
        )

    def probe_all(self) -> list[EgxEndpointProbeResult]:
        """Probe all discovered public EGX endpoints."""
        return [
            self.probe_endpoint(name, url)
            for name, url in EGX_DISCOVERED_ENDPOINTS.items()
        ]
