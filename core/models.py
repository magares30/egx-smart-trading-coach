"""Pydantic models for trades, signals, and portfolio state."""

from datetime import UTC, datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class SignalType(str, Enum):
    BUY_SETUP = "BUY_SETUP"
    WATCH = "WATCH"
    BLOCKED = "BLOCKED"


class TradeSignal(BaseModel):
    symbol: str
    signal_type: SignalType
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence_score: float = Field(ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_buy_levels(self) -> "TradeSignal":
        if self.signal_type == SignalType.BUY_SETUP:
            if not (self.stop_loss < self.entry_price < self.take_profit):
                raise ValueError(
                    "BUY_SETUP requires stop_loss < entry_price < take_profit"
                )
        return self


class Position(BaseModel):
    symbol: str
    quantity: int
    avg_entry_price: float
    stop_loss: float
    take_profit: float
    opened_at: datetime


class Trade(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    symbol: str
    side: TradeSide
    quantity: int
    entry_price: float
    exit_price: Optional[float] = None
    stop_loss: float
    take_profit: float
    status: TradeStatus = TradeStatus.OPEN
    opened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    closed_at: Optional[datetime] = None
    pnl: Optional[float] = None
    pnl_percent: Optional[float] = None
    reason: str = ""
    notes: str = ""

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("quantity must be at least 1")
        return value


class PortfolioSnapshot(BaseModel):
    cash: float
    equity: float
    open_positions: int
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float


class RiskDecision(BaseModel):
    approved: bool
    quantity: int = 0
    message: str = ""
    rejection_reasons: list[str] = Field(default_factory=list)
