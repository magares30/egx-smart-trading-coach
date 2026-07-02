"""Load and validate single-day EGX live snapshot CSV files."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from core.data_import import LIVE_SNAPSHOT_REQUIRED_COLUMNS
from core.live_volume import LiveVolumeHistoryStore, NOT_ENOUGH_VOLUME_HISTORY_WARNING


class LiveSymbolSnapshot(BaseModel):
    symbol: str
    date: date
    previous_close: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    change_percent: float
    volume_ratio: float = 1.0
    broke_previous_high: bool
    insufficient_volume_history: bool = False


class LiveMarketSnapshot(BaseModel):
    as_of_date: date
    symbols: dict[str, LiveSymbolSnapshot]


class EgxLiveSnapshotProvider:
    """Read a normalized EGX live snapshot CSV into structured symbol rows."""

    def __init__(
        self,
        csv_path: Path,
        volume_history_store: LiveVolumeHistoryStore | None = None,
        lookback_days: int = 20,
        min_history_days: int = 3,
    ) -> None:
        self.csv_path = csv_path
        self.volume_history_store = volume_history_store
        self.lookback_days = lookback_days
        self.min_history_days = min_history_days
        self.warnings: list[str] = []

    def _skip_row(self, symbol: object, reason: str) -> None:
        label = str(symbol).strip() or "(blank symbol)"
        self.warnings.append(f"Skipped invalid row for {label}: {reason}")

    def _row_is_valid_ohlc(
        self,
        open_price: float,
        high: float,
        low: float,
        close: float,
    ) -> bool:
        if high < open_price or high < close:
            return False
        if low > open_price or low > close:
            return False
        return True

    def _parse_row(self, row: pd.Series) -> LiveSymbolSnapshot | None:
        symbol = str(row["symbol"]).strip()
        if not symbol:
            self._skip_row(row.get("symbol", ""), "empty symbol")
            return None

        try:
            row_date = pd.to_datetime(row["date"], errors="coerce").date()
        except (ValueError, TypeError):
            self._skip_row(symbol, "unparseable date")
            return None
        if row_date is None or pd.isna(row_date):
            self._skip_row(symbol, "unparseable date")
            return None

        numeric_fields = (
            "previous_close",
            "open",
            "high",
            "low",
            "close",
            "volume",
        )
        values: dict[str, float] = {}
        for field in numeric_fields:
            parsed = pd.to_numeric(row[field], errors="coerce")
            if pd.isna(parsed):
                self._skip_row(symbol, f"non-numeric {field}")
                return None
            values[field] = float(parsed)

        if values["previous_close"] <= 0:
            self._skip_row(symbol, "previous_close must be > 0")
            return None

        if not self._row_is_valid_ohlc(
            values["open"],
            values["high"],
            values["low"],
            values["close"],
        ):
            self._skip_row(symbol, "invalid OHLC range")
            return None

        change_percent = (
            (values["close"] - values["previous_close"]) / values["previous_close"]
        ) * 100

        volume_ratio = 1.0
        if "volume_ratio" in row.index:
            parsed_ratio = pd.to_numeric(row["volume_ratio"], errors="coerce")
            if not pd.isna(parsed_ratio) and float(parsed_ratio) > 0:
                volume_ratio = float(parsed_ratio)

        return LiveSymbolSnapshot(
            symbol=symbol,
            date=row_date,
            previous_close=values["previous_close"],
            open=values["open"],
            high=values["high"],
            low=values["low"],
            close=values["close"],
            volume=values["volume"],
            change_percent=change_percent,
            volume_ratio=volume_ratio,
            broke_previous_high=values["high"] > values["previous_close"],
        )

    def load(self) -> LiveMarketSnapshot:
        """Load the live snapshot CSV and return parsed symbol rows."""
        self.warnings = []

        if not self.csv_path.exists():
            raise ValueError(f"Live snapshot file not found: {self.csv_path}")

        try:
            frame = pd.read_csv(self.csv_path)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Unable to read live snapshot CSV: {exc}") from exc

        if frame.empty:
            raise ValueError("Live snapshot CSV is empty")

        missing_columns = [
            column
            for column in LIVE_SNAPSHOT_REQUIRED_COLUMNS
            if column not in frame.columns
        ]
        if missing_columns:
            raise ValueError(
                "Missing required live snapshot columns: "
                + ", ".join(missing_columns)
            )

        symbols: dict[str, LiveSymbolSnapshot] = {}
        for _, row in frame.iterrows():
            parsed = self._parse_row(row)
            if parsed is None:
                continue
            if parsed.symbol in symbols:
                self.warnings.append(
                    f"Duplicate symbol {parsed.symbol}; keeping latest row"
                )
            if self.volume_history_store is not None:
                stats = self.volume_history_store.calculate_volume_stats(
                    symbol=parsed.symbol,
                    current_volume=parsed.volume,
                    current_date=parsed.date,
                    lookback_days=self.lookback_days,
                    min_history_days=self.min_history_days,
                )
                updates: dict[str, object] = {"volume_ratio": stats.volume_ratio}
                if stats.warning == NOT_ENOUGH_VOLUME_HISTORY_WARNING:
                    updates["insufficient_volume_history"] = True
                prev_high = self.volume_history_store.load_previous_day_high(
                    parsed.symbol,
                    parsed.date,
                )
                if prev_high is not None:
                    updates["broke_previous_high"] = parsed.close > prev_high
                parsed = parsed.model_copy(update=updates)
                if stats.warning:
                    self.warnings.append(f"{parsed.symbol}: {stats.warning}")
            symbols[parsed.symbol] = parsed

        if not symbols:
            raise ValueError("No valid rows remained in live snapshot")

        as_of_date = max(snapshot.date for snapshot in symbols.values())
        date_mismatch = {
            symbol
            for symbol, snapshot in symbols.items()
            if snapshot.date != as_of_date
        }
        if date_mismatch:
            self.warnings.append(
                "Live snapshot contains multiple dates; using latest date "
                f"{as_of_date} for as_of_date"
            )

        return LiveMarketSnapshot(as_of_date=as_of_date, symbols=symbols)
