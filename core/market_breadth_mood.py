"""Market breadth mood from TradingView stock snapshot rows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from statistics import median

import pandas as pd

from config.watchlist import MARKET_INDEX_SYMBOLS
from core.live_snapshot import LiveMarketSnapshot
from core.market_mood import MarketMood, MarketMoodResult
from core.market_quality_filters import MarketQualityFilterResult
from core.relative_volume import resolve_volume_ratio
from core.sector_momentum import SectorStatus, build_sector_momentum

BREADTH_MOOD_INFO_WARNING = (
    "Market mood calculated from TradingView stock breadth because "
    "EGX30/EGX70 rows are unavailable."
)
BREADTH_MOOD_SOURCE_LABEL = "TradingView stock breadth"
NOT_ENOUGH_BREADTH_ROWS_WARNING = "Not enough stock rows for market breadth mood"


class MarketBreadthMood(str, Enum):
    BULLISH = "BULLISH"
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    WEAK = "WEAK"
    BEARISH = "BEARISH"


@dataclass(frozen=True)
class MarketBreadthMoodConfig:
    """Thresholds for market breadth mood scoring."""

    min_rows: int = 5


@dataclass(frozen=True)
class MarketBreadthMoodResult:
    """Breadth-based market mood derived from stock snapshot rows."""

    mood: MarketBreadthMood
    score: int
    symbols_count: int
    advancers_count: int
    decliners_count: int
    flat_count: int
    advancers_ratio: float
    decliners_ratio: float
    avg_change_percent: float
    median_change_percent: float
    avg_relative_volume: float | None
    hot_sectors_count: int
    notes: tuple[str, ...] = ()
    warning: str | None = None
    source: str | None = None

    def to_market_mood_result(self) -> MarketMoodResult:
        """Map breadth mood to scanner-compatible market mood."""
        if self.score >= 70:
            scanner_mood = MarketMood.STRONG
        elif self.score <= 40:
            scanner_mood = MarketMood.WEAK
        else:
            scanner_mood = MarketMood.NEUTRAL

        blockers = [self.warning] if self.warning else []
        return MarketMoodResult(
            mood=scanner_mood,
            score=self.score,
            reasons=list(self.notes),
            blockers=blockers,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "mood": self.mood.value,
            "score": self.score,
            "symbols_count": self.symbols_count,
            "advancers_count": self.advancers_count,
            "decliners_count": self.decliners_count,
            "flat_count": self.flat_count,
            "advancers_ratio": self.advancers_ratio,
            "decliners_ratio": self.decliners_ratio,
            "avg_change_percent": self.avg_change_percent,
            "median_change_percent": self.median_change_percent,
            "avg_relative_volume": self.avg_relative_volume,
            "hot_sectors_count": self.hot_sectors_count,
            "notes": list(self.notes),
            "warning": self.warning,
            "source": self.source,
        }


def _safe_float(value: object, default: float = 0.0) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:
        return default
    return parsed


def _resolve_change_percent(row: pd.Series) -> float:
    if "change_percent" in row.index:
        return _safe_float(row.get("change_percent"))
    close = _safe_float(row.get("close"))
    previous_close = _safe_float(row.get("previous_close"))
    if previous_close > 0:
        return ((close - previous_close) / previous_close) * 100
    return 0.0


def _resolve_relative_volume(row: pd.Series) -> float | None:
    if "volume_ratio" in row.index or "tv_relative_volume_10d" in row.index:
        return resolve_volume_ratio(
            _safe_float(row.get("volume_ratio"), 0.0),
            row,
        )
    return None


def build_breadth_snapshot_dataframe(
    live_snapshot: LiveMarketSnapshot,
    *,
    quality_filter_result: MarketQualityFilterResult | None = None,
    snapshot_path: Path | None = None,
    index_symbols: list[str] | None = None,
) -> pd.DataFrame:
    """Build stock rows for breadth mood, excluding index symbols."""
    excluded = set(index_symbols or MARKET_INDEX_SYMBOLS)
    allowed_symbols: set[str] | None = None
    if quality_filter_result is not None and not quality_filter_result.filtered_df.empty:
        allowed_symbols = {
            str(symbol)
            for symbol in quality_filter_result.filtered_df["symbol"].astype(str)
        }

    rows: list[dict[str, object]] = []
    for symbol, snap in live_snapshot.symbols.items():
        if symbol in excluded:
            continue
        if allowed_symbols is not None and symbol not in allowed_symbols:
            continue
        rows.append(
            {
                "symbol": symbol,
                "change_percent": snap.change_percent,
                "volume_ratio": snap.volume_ratio,
                "close": snap.close,
                "previous_close": snap.previous_close,
                "volume": snap.volume,
            }
        )

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame

    if snapshot_path is not None and snapshot_path.exists():
        try:
            csv_frame = pd.read_csv(snapshot_path)
        except Exception:  # noqa: BLE001
            return frame
        merge_columns = [
            column for column in ("sector", "tv_relative_volume_10d") if column in csv_frame.columns
        ]
        if merge_columns and "symbol" in csv_frame.columns:
            extras = csv_frame[["symbol", *merge_columns]].drop_duplicates(subset=["symbol"])
            frame = frame.merge(extras, on="symbol", how="left")

    return frame


def _classify_breadth_mood(score: int) -> MarketBreadthMood:
    if score >= 70:
        return MarketBreadthMood.BULLISH
    if score >= 60:
        return MarketBreadthMood.POSITIVE
    if score >= 45:
        return MarketBreadthMood.NEUTRAL
    if score >= 30:
        return MarketBreadthMood.WEAK
    return MarketBreadthMood.BEARISH


def _format_change_note(value: float) -> str:
    return f"{value:+.1f}%"


def format_market_breadth_mood_report_lines(
    result: MarketBreadthMoodResult,
) -> list[str]:
    """Build Market Mood section lines for breadth-based mood."""
    lines = [
        f"- {result.mood.value}",
        f"- Score: {result.score}/100",
        f"- Breadth: Advancers {result.advancers_count}/{result.symbols_count}",
        f"- Avg change: {_format_change_note(result.avg_change_percent)}",
        f"- Median change: {_format_change_note(result.median_change_percent)}",
    ]
    if result.avg_relative_volume is not None:
        lines.append(f"- Avg RelVol: {result.avg_relative_volume:.1f}x")
    if result.source:
        lines.append(f"- Source: {result.source}")
    return lines


def calculate_market_breadth_mood(
    snapshot_df: pd.DataFrame,
    config: MarketBreadthMoodConfig | None = None,
) -> MarketBreadthMoodResult:
    """Score market mood from stock breadth metrics."""
    values = config or MarketBreadthMoodConfig()
    if snapshot_df is None or snapshot_df.empty:
        return MarketBreadthMoodResult(
            mood=MarketBreadthMood.NEUTRAL,
            score=50,
            symbols_count=0,
            advancers_count=0,
            decliners_count=0,
            flat_count=0,
            advancers_ratio=0.0,
            decliners_ratio=0.0,
            avg_change_percent=0.0,
            median_change_percent=0.0,
            avg_relative_volume=None,
            hot_sectors_count=0,
            warning=NOT_ENOUGH_BREADTH_ROWS_WARNING,
        )

    working = snapshot_df.copy()
    changes = [_resolve_change_percent(row) for _, row in working.iterrows()]
    if len(changes) < values.min_rows:
        return MarketBreadthMoodResult(
            mood=MarketBreadthMood.NEUTRAL,
            score=50,
            symbols_count=len(changes),
            advancers_count=0,
            decliners_count=0,
            flat_count=0,
            advancers_ratio=0.0,
            decliners_ratio=0.0,
            avg_change_percent=0.0,
            median_change_percent=0.0,
            avg_relative_volume=None,
            hot_sectors_count=0,
            warning=NOT_ENOUGH_BREADTH_ROWS_WARNING,
        )

    advancers_count = sum(1 for change in changes if change > 0)
    decliners_count = sum(1 for change in changes if change < 0)
    flat_count = sum(1 for change in changes if change == 0)
    symbols_count = len(changes)
    advancers_ratio = advancers_count / symbols_count
    decliners_ratio = decliners_count / symbols_count
    avg_change_percent = sum(changes) / symbols_count
    median_change_percent = float(median(changes))

    relative_volumes: list[float] = []
    for _, row in working.iterrows():
        resolved = _resolve_relative_volume(row)
        if resolved is not None and resolved > 0:
            relative_volumes.append(resolved)
    avg_relative_volume = (
        sum(relative_volumes) / len(relative_volumes) if relative_volumes else None
    )

    hot_sectors_count = 0
    if "sector" in working.columns:
        sector_result = build_sector_momentum(working)
        hot_sectors_count = sum(
            1 for sector in sector_result.sectors if sector.status == SectorStatus.HOT
        )

    score = 50
    if advancers_ratio >= 0.60:
        score += 15
    if advancers_ratio >= 0.50:
        score += 10
    if avg_change_percent > 1.0:
        score += 10
    if median_change_percent > 0.5:
        score += 5
    if avg_relative_volume is not None and avg_relative_volume >= 1.5:
        score += 5
    if decliners_ratio >= 0.60:
        score -= 15
    if avg_change_percent < -1.0:
        score -= 10
    if median_change_percent < -0.5:
        score -= 5
    score = max(0, min(100, score))

    notes = (
        f"Advancers {advancers_count}/{symbols_count}",
        f"Avg change {_format_change_note(avg_change_percent)}",
        f"Median change {_format_change_note(median_change_percent)}",
    )
    if avg_relative_volume is not None:
        notes = notes + (f"Avg RelVol {avg_relative_volume:.1f}x",)

    return MarketBreadthMoodResult(
        mood=_classify_breadth_mood(score),
        score=score,
        symbols_count=symbols_count,
        advancers_count=advancers_count,
        decliners_count=decliners_count,
        flat_count=flat_count,
        advancers_ratio=advancers_ratio,
        decliners_ratio=decliners_ratio,
        avg_change_percent=avg_change_percent,
        median_change_percent=median_change_percent,
        avg_relative_volume=avg_relative_volume,
        hot_sectors_count=hot_sectors_count,
        notes=notes,
        source=BREADTH_MOOD_SOURCE_LABEL,
    )
