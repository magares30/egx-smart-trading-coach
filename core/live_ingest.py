"""Persist and load EGX live snapshot ingest warnings."""

from __future__ import annotations

import json
from pathlib import Path


def save_ingest_warnings(path: Path, warnings: list[str]) -> None:
    """Write ingest warnings to JSON for downstream scan/report pipelines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"warnings": warnings}
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(path)


def load_ingest_warnings(path: Path) -> list[str]:
    """Load ingest warnings saved during the latest snapshot update."""
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list):
        return []
    return [str(item) for item in warnings]
