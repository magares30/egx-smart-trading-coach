"""TradingView relative volume classification for candidate ranking."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

import pandas as pd

DEFAULT_LOW_THRESHOLD = 0.8
DEFAULT_NORMAL_HIGH = 1.5
DEFAULT_HIGH_HIGH = 3.0


class RelativeVolumeStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


@dataclass(frozen=True)
class RelativeVolumeConfig:
    """Thresholds for classifying 10-day relative volume."""

    low_threshold: float = DEFAULT_LOW_THRESHOLD
    normal_high: float = DEFAULT_NORMAL_HIGH
    high_high: float = DEFAULT_HIGH_HIGH


@dataclass(frozen=True)
class RelativeVolumeResult:
    """Relative volume classification for one symbol."""

    status: RelativeVolumeStatus
    value: float | None
    score_bonus: int
    note: str


def _safe_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed <= 0:
        return None
    return parsed


def format_relative_volume_display(value: float) -> str:
    """Format relative volume for compact report output."""
    return f"{value:.1f}x"


def resolve_volume_ratio(
    candidate_volume_ratio: float,
    row: Mapping[str, object] | None = None,
) -> float | None:
    """Prefer TradingView relative volume from snapshot rows when available."""
    if row is not None:
        tv_relative = _safe_float(row.get("tv_relative_volume_10d"))
        if tv_relative is not None:
            return tv_relative
        snapshot_ratio = _safe_float(row.get("volume_ratio"))
        if snapshot_ratio is not None:
            return snapshot_ratio
    if candidate_volume_ratio > 0:
        return candidate_volume_ratio
    return None


def classify_relative_volume(
    value: float | None,
    config: RelativeVolumeConfig | None = None,
) -> RelativeVolumeResult:
    """Classify relative volume and return ranking bonus metadata."""
    cfg = config or RelativeVolumeConfig()
    normalized = _safe_float(value)
    if normalized is None:
        return RelativeVolumeResult(
            status=RelativeVolumeStatus.UNKNOWN,
            value=None,
            score_bonus=0,
            note="rel vol unknown",
        )

    display = format_relative_volume_display(normalized)
    if normalized < cfg.low_threshold:
        return RelativeVolumeResult(
            status=RelativeVolumeStatus.LOW,
            value=normalized,
            score_bonus=-3,
            note=f"rel vol LOW {display}",
        )
    if normalized < cfg.normal_high:
        return RelativeVolumeResult(
            status=RelativeVolumeStatus.NORMAL,
            value=normalized,
            score_bonus=0,
            note=f"rel vol NORMAL {display}",
        )
    if normalized < cfg.high_high:
        return RelativeVolumeResult(
            status=RelativeVolumeStatus.HIGH,
            value=normalized,
            score_bonus=5,
            note=f"rel vol HIGH {display}",
        )
    return RelativeVolumeResult(
        status=RelativeVolumeStatus.VERY_HIGH,
        value=normalized,
        score_bonus=8,
        note=f"rel vol VERY HIGH {display}",
    )
