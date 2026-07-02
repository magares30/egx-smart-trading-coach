"""Atomic JSON file persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write JSON atomically via a temporary file and replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=indent)
    temp_path.replace(path)
