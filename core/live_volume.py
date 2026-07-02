"""Live volume history storage and ratio calculation for EGX snapshots."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from core.data_import import LIVE_SNAPSHOT_COLUMNS

NOT_ENOUGH_VOLUME_HISTORY_WARNING = "Not enough volume history"
ZERO_AVERAGE_VOLUME_WARNING = "Previous average volume is zero"
HISTORY_FILE_PREFIX = "egx_live_snapshot_"
HISTORY_FILE_SUFFIX = ".csv"


class LiveVolumeStats(BaseModel):
    symbol: str
    current_volume: float
    average_volume: float | None
    volume_ratio: float
    history_days: int
    warning: str | None = None


class LiveVolumeHistoryStore:
    """Persist dated live snapshots and compute volume ratios from prior days."""

    def __init__(self, history_dir: Path) -> None:
        self.history_dir = history_dir

    def history_filename(self, snapshot_date: date) -> str:
        return f"{HISTORY_FILE_PREFIX}{snapshot_date.strftime('%Y%m%d')}{HISTORY_FILE_SUFFIX}"

    def save_snapshot(self, snapshot_csv: Path, snapshot_date: date) -> Path:
        """Copy the current live snapshot into dated history storage."""
        if not snapshot_csv.exists():
            raise FileNotFoundError(f"Live snapshot file not found: {snapshot_csv}")

        self.history_dir.mkdir(parents=True, exist_ok=True)
        destination = self.history_dir / self.history_filename(snapshot_date)
        shutil.copy2(snapshot_csv, destination)
        return destination

    def _parse_history_date(self, path: Path) -> date | None:
        stem = path.stem
        if not stem.startswith(HISTORY_FILE_PREFIX):
            return None
        date_part = stem.removeprefix(HISTORY_FILE_PREFIX)
        try:
            return date.fromisoformat(
                f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
            )
        except ValueError:
            return None

    def _list_history_files_before(self, before_date: date) -> list[Path]:
        dated_files: list[tuple[date, Path]] = []
        if not self.history_dir.exists():
            return []

        for path in self.history_dir.glob(f"{HISTORY_FILE_PREFIX}*{HISTORY_FILE_SUFFIX}"):
            file_date = self._parse_history_date(path)
            if file_date is not None and file_date < before_date:
                dated_files.append((file_date, path))

        dated_files.sort(key=lambda item: item[0], reverse=True)
        return [path for _, path in dated_files]

    def _read_symbol_volume(self, history_file: Path, symbol: str) -> float | None:
        try:
            frame = pd.read_csv(history_file)
        except Exception:  # noqa: BLE001
            return None

        if "symbol" not in frame.columns or "volume" not in frame.columns:
            return None

        rows = frame[frame["symbol"].astype(str).str.strip() == symbol]
        if rows.empty:
            return None

        volume = pd.to_numeric(rows.iloc[-1]["volume"], errors="coerce")
        if pd.isna(volume) or float(volume) < 0:
            return None
        return float(volume)

    def _read_symbol_ohlc(
        self, history_file: Path, symbol: str
    ) -> tuple[float, float, float] | None:
        """Return close, high, and low for a symbol from one history file."""
        try:
            frame = pd.read_csv(history_file)
        except Exception:  # noqa: BLE001
            return None

        required = {"symbol", "close", "high", "low"}
        if not required.issubset(frame.columns):
            return None

        rows = frame[frame["symbol"].astype(str).str.strip() == symbol]
        if rows.empty:
            return None

        row = rows.iloc[-1]
        close = pd.to_numeric(row["close"], errors="coerce")
        high = pd.to_numeric(row["high"], errors="coerce")
        low = pd.to_numeric(row["low"], errors="coerce")
        if pd.isna(close) or pd.isna(high) or pd.isna(low):
            return None
        return float(close), float(high), float(low)

    def load_previous_day_high(self, symbol: str, before_date: date) -> float | None:
        """Load the most recent prior session high for a symbol."""
        history_files = self._list_history_files_before(before_date)
        if not history_files:
            return None
        ohlc = self._read_symbol_ohlc(history_files[0], symbol)
        if ohlc is None:
            return None
        return ohlc[1]

    def load_previous_closes(
        self,
        symbol: str,
        before_date: date,
        count: int = 5,
    ) -> list[float]:
        """Load prior session closes for SMA calculations, newest first."""
        closes: list[float] = []
        for history_file in self._list_history_files_before(before_date)[:count]:
            ohlc = self._read_symbol_ohlc(history_file, symbol)
            if ohlc is None:
                continue
            closes.append(ohlc[0])
        return closes

    def load_previous_volumes(
        self,
        symbol: str,
        before_date: date,
        lookback_days: int = 20,
    ) -> list[float]:
        """Load prior-day volumes for a symbol, newest first."""
        volumes: list[float] = []
        for history_file in self._list_history_files_before(before_date)[:lookback_days]:
            volume = self._read_symbol_volume(history_file, symbol)
            if volume is not None:
                volumes.append(volume)
        return volumes

    def _read_symbol_ohlcv(
        self, history_file: Path, symbol: str
    ) -> dict[str, float] | None:
        """Return OHLCV bar for a symbol from one history file."""
        try:
            frame = pd.read_csv(history_file)
        except Exception:  # noqa: BLE001
            return None

        required = {"symbol", "open", "high", "low", "close", "volume"}
        if not required.issubset(frame.columns):
            return None

        rows = frame[frame["symbol"].astype(str).str.strip() == symbol]
        if rows.empty:
            return None

        row = rows.iloc[-1]
        parsed: dict[str, float] = {}
        for field in ("open", "high", "low", "close", "volume"):
            value = pd.to_numeric(row[field], errors="coerce")
            if pd.isna(value) or float(value) < 0:
                return None
            parsed[field] = float(value)
        return parsed

    def load_ohlcv_series(
        self,
        symbol: str,
        before_date: date,
        count: int,
        *,
        current_bar: dict[str, float] | None = None,
    ) -> list[dict[str, float]]:
        """Load chronological OHLCV bars (oldest first) for TA-Lib calculations."""
        bars: list[dict[str, float]] = []
        history_files = list(reversed(self._list_history_files_before(before_date)))
        for history_file in history_files[-count:]:
            bar = self._read_symbol_ohlcv(history_file, symbol)
            if bar is not None:
                bars.append(bar)

        if current_bar is not None:
            bars.append(current_bar)

        return bars

    def calculate_volume_stats(
        self,
        symbol: str,
        current_volume: float,
        current_date: date,
        lookback_days: int = 20,
        min_history_days: int = 3,
    ) -> LiveVolumeStats:
        """Calculate live volume ratio from stored prior snapshots."""
        previous_volumes = self.load_previous_volumes(
            symbol,
            before_date=current_date,
            lookback_days=lookback_days,
        )
        history_days = len(previous_volumes)

        if history_days < min_history_days:
            return LiveVolumeStats(
                symbol=symbol,
                current_volume=current_volume,
                average_volume=None,
                volume_ratio=1.0,
                history_days=history_days,
                warning=NOT_ENOUGH_VOLUME_HISTORY_WARNING,
            )

        average_volume = sum(previous_volumes) / history_days
        if average_volume <= 0:
            return LiveVolumeStats(
                symbol=symbol,
                current_volume=current_volume,
                average_volume=average_volume,
                volume_ratio=1.0,
                history_days=history_days,
                warning=ZERO_AVERAGE_VOLUME_WARNING,
            )

        return LiveVolumeStats(
            symbol=symbol,
            current_volume=current_volume,
            average_volume=average_volume,
            volume_ratio=current_volume / average_volume,
            history_days=history_days,
            warning=None,
        )
