"""Local CSV market data provider and snapshot builders."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd
from pydantic import BaseModel, Field


class MarketBar(BaseModel):
    date: date
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class SymbolSnapshot(BaseModel):
    symbol: str
    latest_close: float
    previous_close: float
    change: float
    change_percent: float
    latest_volume: int
    average_volume_5d: float
    volume_ratio: float
    day_high: float
    day_low: float
    broke_previous_high: bool
    above_sma_5: bool
    above_sma_20: bool | None
    insufficient_volume_history: bool = False


class MarketSnapshot(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    symbols: list[SymbolSnapshot]
    index_snapshots: list[SymbolSnapshot]


@runtime_checkable
class MarketDataProvider(Protocol):
    """Interface for market data sources (CSV today, real EGX feed later)."""

    def load_data(self) -> pd.DataFrame: ...

    def get_history(
        self, symbol: str, lookback: int | None = None
    ) -> list[MarketBar]: ...

    def get_latest_bar(self, symbol: str) -> MarketBar: ...

    def get_previous_bar(self, symbol: str) -> MarketBar: ...

    def build_symbol_snapshot(self, symbol: str) -> SymbolSnapshot: ...

    def build_market_snapshot(
        self, symbols: list[str], index_symbols: list[str]
    ) -> MarketSnapshot: ...


class CsvMarketDataProvider:
    """Reads OHLCV data from a local CSV file."""

    # Implements MarketDataProvider

    def __init__(self, csv_path: Path) -> None:
        self._csv_path = csv_path
        self._data: pd.DataFrame | None = None

    def load_data(self) -> pd.DataFrame:
        """Load and cache CSV data sorted by date."""
        if self._data is None:
            df = pd.read_csv(self._csv_path, parse_dates=["date"])
            df["date"] = df["date"].dt.date
            self._data = df.sort_values(["symbol", "date"]).reset_index(drop=True)
        return self._data

    def _get_symbol_rows(self, symbol: str) -> pd.DataFrame:
        df = self.load_data()
        rows = df[df["symbol"] == symbol]
        if rows.empty:
            raise ValueError(f"Symbol '{symbol}' not found in market data")
        if len(rows) < 2:
            raise ValueError(
                f"Symbol '{symbol}' has fewer than 2 rows — cannot build snapshot"
            )
        return rows.reset_index(drop=True)

    def _get_symbol_rows_as_of(self, symbol: str, as_of_date: date) -> pd.DataFrame:
        rows = self._get_symbol_rows(symbol)
        filtered = rows[rows["date"] <= as_of_date]
        if filtered.empty:
            raise ValueError(
                f"Symbol '{symbol}' has no data on or before {as_of_date}"
            )
        if len(filtered) < 2:
            raise ValueError(
                f"Symbol '{symbol}' has fewer than 2 rows as of {as_of_date} "
                "— cannot build snapshot"
            )
        return filtered.reset_index(drop=True)

    def get_available_dates(self) -> list[date]:
        """Return sorted unique trading dates in the dataset."""
        df = self.load_data()
        return sorted(df["date"].unique())

    def get_bar_for_date(self, symbol: str, bar_date: date) -> MarketBar:
        """Return the OHLCV bar for a symbol on an exact date."""
        df = self.load_data()
        rows = df[(df["symbol"] == symbol) & (df["date"] == bar_date)]
        if rows.empty:
            raise ValueError(f"No bar for {symbol} on {bar_date}")
        row = rows.iloc[-1]
        return MarketBar(
            date=row["date"],
            symbol=row["symbol"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=int(row["volume"]),
        )

    def _rows_to_bars(self, rows: pd.DataFrame) -> list[MarketBar]:
        return [
            MarketBar(
                date=row["date"],
                symbol=row["symbol"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=int(row["volume"]),
            )
            for _, row in rows.iterrows()
        ]

    def _build_snapshot_from_history(self, symbol: str, history: list[MarketBar]) -> SymbolSnapshot:
        latest = history[-1]
        previous = history[-2]

        change = latest.close - previous.close
        change_percent = (change / previous.close) * 100 if previous.close else 0.0

        previous_bars = history[:-1]
        volume_window = previous_bars[-5:]
        average_volume_5d = (
            sum(bar.volume for bar in volume_window) / len(volume_window)
            if volume_window
            else 0.0
        )
        volume_ratio = (
            latest.volume / average_volume_5d if average_volume_5d > 0 else 0.0
        )

        closes = [bar.close for bar in history]
        sma_5 = sum(closes[-5:]) / min(len(closes), 5)
        above_sma_5 = latest.close > sma_5

        above_sma_20: bool | None
        if len(closes) >= 20:
            sma_20 = sum(closes[-20:]) / 20
            above_sma_20 = latest.close > sma_20
        else:
            above_sma_20 = None

        return SymbolSnapshot(
            symbol=symbol,
            latest_close=latest.close,
            previous_close=previous.close,
            change=change,
            change_percent=change_percent,
            latest_volume=latest.volume,
            average_volume_5d=average_volume_5d,
            volume_ratio=volume_ratio,
            day_high=latest.high,
            day_low=latest.low,
            broke_previous_high=latest.close > previous.high,
            above_sma_5=above_sma_5,
            above_sma_20=above_sma_20,
        )

    def get_history(
        self, symbol: str, lookback: int | None = None
    ) -> list[MarketBar]:
        """Return historical bars for a symbol, optionally limited by lookback."""
        rows = self._get_symbol_rows(symbol)
        if lookback is not None:
            rows = rows.tail(lookback)
        return [
            MarketBar(
                date=row["date"],
                symbol=row["symbol"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=int(row["volume"]),
            )
            for _, row in rows.iterrows()
        ]

    def get_latest_bar(self, symbol: str) -> MarketBar:
        """Return the most recent bar for a symbol."""
        history = self.get_history(symbol)
        return history[-1]

    def get_previous_bar(self, symbol: str) -> MarketBar:
        """Return the bar immediately before the latest."""
        history = self.get_history(symbol)
        return history[-2]

    def build_symbol_snapshot(self, symbol: str) -> SymbolSnapshot:
        """Build a snapshot with price change, volume, and SMA indicators."""
        return self._build_snapshot_from_history(symbol, self.get_history(symbol))

    def build_symbol_snapshot_as_of(self, symbol: str, as_of_date: date) -> SymbolSnapshot:
        """Build a symbol snapshot using data available on or before as_of_date."""
        rows = self._get_symbol_rows_as_of(symbol, as_of_date)
        history = self._rows_to_bars(rows)
        return self._build_snapshot_from_history(symbol, history)

    def build_market_snapshot(
        self, symbols: list[str], index_symbols: list[str]
    ) -> MarketSnapshot:
        """Build a full market snapshot for watchlist and index symbols."""
        return MarketSnapshot(
            symbols=[self.build_symbol_snapshot(s) for s in symbols],
            index_snapshots=[self.build_symbol_snapshot(s) for s in index_symbols],
        )

    def build_market_snapshot_as_of(
        self,
        symbols: list[str],
        index_symbols: list[str],
        as_of_date: date,
    ) -> MarketSnapshot:
        """Build a market snapshot as of a historical date."""
        return MarketSnapshot(
            symbols=[
                self.build_symbol_snapshot_as_of(s, as_of_date) for s in symbols
            ],
            index_snapshots=[
                self.build_symbol_snapshot_as_of(s, as_of_date) for s in index_symbols
            ],
        )
