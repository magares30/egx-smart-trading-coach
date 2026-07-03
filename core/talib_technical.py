"""TA-Lib technical indicator engine for daily report candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING

import numpy as np

from config import settings

if TYPE_CHECKING:
    from core.live_snapshot import LiveMarketSnapshot
    from core.live_volume import LiveVolumeHistoryStore

try:
    import talib

    TALIB_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised when talib is missing
    talib = None  # type: ignore[assignment]
    TALIB_AVAILABLE = False

TALIB_NOT_INSTALLED_WARNING = "TA-Lib is not installed; local indicator engine disabled"
TALIB_ENGINE_DISABLED_REASON = "talib engine disabled"
TALIB_PACKAGE_NOT_INSTALLED_REASON = "talib package not installed"
TALIB_MODE_ACTIVE = "active"
TALIB_MODE_FALLBACK = "fallback"
TALIB_STATUS_FALLBACK = "FALLBACK"
TALIB_RUNTIME_LOG_PREFIX = "TALIB_RUNTIME_STATUS"
TALIB_INSUFFICIENT_HISTORY_NOTE = "Need more saved history snapshots"
DEFAULT_ATR_HIGH_PCT = 3.0
DEFAULT_ATR_LOW_PCT = 1.0
OBV_LOOKBACK_BARS = 5


class TalibOverallStatus(str, Enum):
    STRONG = "STRONG"
    OK = "OK"
    CAUTION = "CAUTION"
    WEAK = "WEAK"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


class TalibTrendStatus(str, Enum):
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"
    UNKNOWN = "UNKNOWN"


class TalibMomentumStatus(str, Enum):
    STRONG = "STRONG"
    HEALTHY = "HEALTHY"
    HOT = "HOT"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"


class TalibVolatilityStatus(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class TalibVolumeConfirmation(str, Enum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TalibTechnicalConfig:
    """Configuration for the TA-Lib technical engine."""

    enabled: bool = True
    min_history_days: int = settings.DEFAULT_TALIB_MIN_HISTORY_DAYS


@dataclass(frozen=True)
class TalibTechnicalResult:
    """TA-Lib technical outcome for one symbol."""

    talib_available: bool
    status: TalibOverallStatus
    trend_status: TalibTrendStatus
    momentum_status: TalibMomentumStatus
    volatility_status: TalibVolatilityStatus
    volume_confirmation: TalibVolumeConfirmation
    risk_note: str | None
    history_bars: int
    notes: list[str]
    indicators: dict[str, float | None]

    def to_dict(self) -> dict[str, object]:
        """Serialize TA-Lib technical data for JSON report output."""
        return {
            "available": self.talib_available,
            "status": self.status.value,
            "trend_status": self.trend_status.value,
            "momentum_status": self.momentum_status.value,
            "volatility_status": self.volatility_status.value,
            "volume_confirmation": self.volume_confirmation.value,
            "risk_note": self.risk_note,
            "history_bars": self.history_bars,
            "notes": list(self.notes),
            "indicators": dict(self.indicators),
        }


def build_talib_technical_config_from_cli(
    *,
    enabled: bool = True,
    min_history_days: int | None = None,
) -> TalibTechnicalConfig:
    """Build TA-Lib technical config from optional CLI values."""
    return TalibTechnicalConfig(
        enabled=enabled,
        min_history_days=(
            min_history_days
            if min_history_days is not None
            else settings.DEFAULT_TALIB_MIN_HISTORY_DAYS
        ),
    )


def is_talib_engine_available() -> bool:
    """Return True when TA-Lib can be imported."""
    return TALIB_AVAILABLE


@dataclass(frozen=True)
class TalibRuntimeStatus:
    """Runtime availability of the TA-Lib engine for report metadata."""

    talib_available: bool
    talib_mode: str
    talib_reason: str

    def to_metadata(self) -> dict[str, object]:
        return {
            "talib_available": self.talib_available,
            "talib_mode": self.talib_mode,
            "talib_reason": self.talib_reason,
        }


def resolve_talib_runtime_status(
    *,
    enabled: bool = True,
    package_installed: bool | None = None,
) -> TalibRuntimeStatus:
    """Resolve whether TA-Lib is active or in fallback mode for this report run."""
    if not enabled:
        return TalibRuntimeStatus(
            talib_available=False,
            talib_mode=TALIB_MODE_FALLBACK,
            talib_reason=TALIB_ENGINE_DISABLED_REASON,
        )

    installed = TALIB_AVAILABLE if package_installed is None else package_installed
    if not installed:
        return TalibRuntimeStatus(
            talib_available=False,
            talib_mode=TALIB_MODE_FALLBACK,
            talib_reason=TALIB_PACKAGE_NOT_INSTALLED_REASON,
        )

    return TalibRuntimeStatus(
        talib_available=True,
        talib_mode=TALIB_MODE_ACTIVE,
        talib_reason="",
    )


def format_talib_runtime_log_line(status: TalibRuntimeStatus) -> str:
    """Safe single-line log for report startup."""
    if status.talib_available:
        return (
            f"{TALIB_RUNTIME_LOG_PREFIX} available=true mode={status.talib_mode}"
        )
    reason = status.talib_reason or "unknown"
    return (
        f"{TALIB_RUNTIME_LOG_PREFIX} available=false mode={status.talib_mode} "
        f"reason={reason}"
    )


def format_technical_engines_report_lines(
    talib_runtime: TalibRuntimeStatus,
    *,
    tradingview_technical_available: bool,
) -> list[str]:
    """Compact technical-engine status lines for the daily report Summary section."""
    tv_line = (
        "- TradingView technical: ACTIVE ✅"
        if tradingview_technical_available
        else "- TradingView technical: UNAVAILABLE"
    )
    if talib_runtime.talib_available:
        talib_line = "- TA-Lib: ACTIVE ✅"
    else:
        reason = talib_runtime.talib_reason or "unavailable"
        talib_line = f"- TA-Lib: FALLBACK ⚠️ {reason}"
    return ["Technical engines:", tv_line, talib_line]


def format_talib_runtime_telegram_line(
    talib_runtime: TalibRuntimeStatus | dict[str, object] | None,
) -> str:
    """Single Telegram metadata line for TA-Lib runtime."""
    if talib_runtime is None:
        return "TA-Lib: FALLBACK ⚠️ status unknown"

    if isinstance(talib_runtime, dict):
        available = bool(talib_runtime.get("talib_available"))
        reason = str(talib_runtime.get("talib_reason") or "")
    else:
        available = talib_runtime.talib_available
        reason = talib_runtime.talib_reason

    if available:
        return "TA-Lib: ACTIVE ✅"
    reason_text = reason or "unavailable"
    return f"TA-Lib: FALLBACK ⚠️ {reason_text}"


def format_talib_runtime_readiness_line(
    talib_runtime: TalibRuntimeStatus | None = None,
) -> str:
    """Explicit TA-Lib runtime line for cloud readiness output."""
    status = talib_runtime or resolve_talib_runtime_status(enabled=True)
    if status.talib_available:
        return "TA-Lib runtime: ACTIVE ✅"
    reason = status.talib_reason or "unavailable"
    return f"TA-Lib runtime: FALLBACK ⚠️ reason: {reason}"


def _last_valid(values: np.ndarray) -> float | None:
    if values.size == 0:
        return None
    for value in reversed(values):
        if not np.isnan(value):
            return float(value)
    return None


def _insufficient_result(history_bars: int = 0) -> TalibTechnicalResult:
    return TalibTechnicalResult(
        talib_available=False,
        status=TalibOverallStatus.INSUFFICIENT_HISTORY,
        trend_status=TalibTrendStatus.UNKNOWN,
        momentum_status=TalibMomentumStatus.UNKNOWN,
        volatility_status=TalibVolatilityStatus.UNKNOWN,
        volume_confirmation=TalibVolumeConfirmation.UNKNOWN,
        risk_note=None,
        history_bars=history_bars,
        notes=[TALIB_INSUFFICIENT_HISTORY_NOTE],
        indicators={},
    )


def _unavailable_result() -> TalibTechnicalResult:
    return TalibTechnicalResult(
        talib_available=False,
        status=TalibOverallStatus.INSUFFICIENT_HISTORY,
        trend_status=TalibTrendStatus.UNKNOWN,
        momentum_status=TalibMomentumStatus.UNKNOWN,
        volatility_status=TalibVolatilityStatus.UNKNOWN,
        volume_confirmation=TalibVolumeConfirmation.UNKNOWN,
        risk_note=None,
        history_bars=0,
        notes=[TALIB_NOT_INSTALLED_WARNING],
        indicators={},
    )


def evaluate_talib_technical_from_bars(
    bars: list[dict[str, float]],
    config: TalibTechnicalConfig | None = None,
) -> TalibTechnicalResult:
    """Calculate TA-Lib indicators and interpretation from chronological OHLCV bars."""
    values = config or TalibTechnicalConfig()
    if not values.enabled:
        return _insufficient_result(len(bars))
    if not TALIB_AVAILABLE:
        return _unavailable_result()
    if len(bars) < values.min_history_days:
        return _insufficient_result(len(bars))

    opens = np.array([bar["open"] for bar in bars], dtype=float)
    highs = np.array([bar["high"] for bar in bars], dtype=float)
    lows = np.array([bar["low"] for bar in bars], dtype=float)
    closes = np.array([bar["close"] for bar in bars], dtype=float)
    volumes = np.array([bar["volume"] for bar in bars], dtype=float)

    if (
        np.any(np.isnan(opens))
        or np.any(np.isnan(highs))
        or np.any(np.isnan(lows))
        or np.any(np.isnan(closes))
        or np.any(np.isnan(volumes))
    ):
        return _insufficient_result(len(bars))

    sma20 = talib.SMA(closes, timeperiod=20)
    sma50 = talib.SMA(closes, timeperiod=50)
    ema20 = talib.EMA(closes, timeperiod=20)
    ema50 = talib.EMA(closes, timeperiod=50)
    adx = talib.ADX(highs, lows, closes, timeperiod=14)
    rsi = talib.RSI(closes, timeperiod=14)
    macd, macd_signal, _macd_hist = talib.MACD(closes)
    stoch_k, stoch_d = talib.STOCH(highs, lows, closes)
    cci = talib.CCI(highs, lows, closes, timeperiod=14)
    roc = talib.ROC(closes, timeperiod=10)
    atr = talib.ATR(highs, lows, closes, timeperiod=14)
    bb_upper, bb_middle, bb_lower = talib.BBANDS(closes, timeperiod=20)
    obv = talib.OBV(closes, volumes)

    close = _last_valid(closes)
    indicators = {
        "sma20": _last_valid(sma20),
        "sma50": _last_valid(sma50),
        "ema20": _last_valid(ema20),
        "ema50": _last_valid(ema50),
        "rsi": _last_valid(rsi),
        "macd": _last_valid(macd),
        "macd_signal": _last_valid(macd_signal),
        "adx": _last_valid(adx),
        "atr": _last_valid(atr),
        "bb_upper": _last_valid(bb_upper),
        "bb_middle": _last_valid(bb_middle),
        "bb_lower": _last_valid(bb_lower),
        "stoch_k": _last_valid(stoch_k),
        "stoch_d": _last_valid(stoch_d),
        "cci": _last_valid(cci),
        "roc": _last_valid(roc),
        "obv": _last_valid(obv),
    }
    if close is None or indicators["ema20"] is None or indicators["ema50"] is None:
        return _insufficient_result(len(bars))

    notes: list[str] = []
    trend_status = _interpret_trend(close, indicators, notes)
    momentum_status = _interpret_momentum(indicators, notes)
    volatility_status, risk_note = _interpret_volatility(close, indicators, notes)
    volume_confirmation = _interpret_volume_confirmation(obv, closes, notes)
    status = _overall_status(
        trend_status,
        momentum_status,
        volatility_status,
        volume_confirmation,
        indicators,
    )

    return TalibTechnicalResult(
        talib_available=True,
        status=status,
        trend_status=trend_status,
        momentum_status=momentum_status,
        volatility_status=volatility_status,
        volume_confirmation=volume_confirmation,
        risk_note=risk_note,
        history_bars=len(bars),
        notes=notes,
        indicators=indicators,
    )


def _interpret_trend(
    close: float,
    indicators: dict[str, float | None],
    notes: list[str],
) -> TalibTrendStatus:
    ema20 = indicators.get("ema20")
    ema50 = indicators.get("ema50")
    adx = indicators.get("adx")
    if ema20 is None or ema50 is None:
        return TalibTrendStatus.UNKNOWN

    if close > ema20 and ema20 > ema50:
        trend = TalibTrendStatus.BULLISH
    elif close < ema20 and ema20 < ema50:
        trend = TalibTrendStatus.BEARISH
    else:
        trend = TalibTrendStatus.NEUTRAL

    if adx is not None:
        if adx > 25:
            notes.append(f"ADX {adx:.1f} confirms trend")
        elif adx < 20:
            notes.append(f"ADX {adx:.1f} weak trend")
    return trend


def _interpret_momentum(
    indicators: dict[str, float | None],
    notes: list[str],
) -> TalibMomentumStatus:
    rsi = indicators.get("rsi")
    macd = indicators.get("macd")
    macd_signal = indicators.get("macd_signal")
    roc = indicators.get("roc")
    cci = indicators.get("cci")

    momentum = TalibMomentumStatus.UNKNOWN
    if rsi is not None:
        if rsi > 70:
            momentum = TalibMomentumStatus.HOT
            notes.append(f"RSI {rsi:.1f} hot")
        elif rsi < 45:
            momentum = TalibMomentumStatus.WEAK
            notes.append(f"RSI {rsi:.1f} weak")
        elif 50 <= rsi <= 70:
            momentum = TalibMomentumStatus.HEALTHY

    if macd is not None and macd_signal is not None and macd > macd_signal:
        notes.append("MACD above signal")
        if momentum == TalibMomentumStatus.UNKNOWN:
            momentum = TalibMomentumStatus.HEALTHY

    if roc is not None and roc > 0:
        notes.append("ROC positive")
        if momentum in (TalibMomentumStatus.UNKNOWN, TalibMomentumStatus.HEALTHY):
            momentum = TalibMomentumStatus.STRONG if roc > 2 else TalibMomentumStatus.HEALTHY

    if cci is not None and cci > 100:
        notes.append(f"CCI {cci:.1f} strong move")
        if momentum == TalibMomentumStatus.HEALTHY:
            momentum = TalibMomentumStatus.STRONG

    if momentum == TalibMomentumStatus.UNKNOWN:
        momentum = TalibMomentumStatus.HEALTHY
    return momentum


def _interpret_volatility(
    close: float,
    indicators: dict[str, float | None],
    notes: list[str],
) -> tuple[TalibVolatilityStatus, str | None]:
    atr = indicators.get("atr")
    bb_upper = indicators.get("bb_upper")
    bb_lower = indicators.get("bb_lower")
    risk_note: str | None = None

    volatility = TalibVolatilityStatus.NORMAL
    if atr is not None and close > 0:
        atr_pct = (atr / close) * 100
        if atr_pct >= DEFAULT_ATR_HIGH_PCT:
            volatility = TalibVolatilityStatus.HIGH
            risk_note = "Elevated ATR"
            notes.append(f"ATR {atr_pct:.1f}% of price")
        elif atr_pct <= DEFAULT_ATR_LOW_PCT:
            volatility = TalibVolatilityStatus.LOW

    if (
        bb_upper is not None
        and bb_lower is not None
        and close >= bb_upper
    ):
        notes.append("Price at/above upper Bollinger band")
        if volatility != TalibVolatilityStatus.HIGH:
            volatility = TalibVolatilityStatus.NORMAL

    if (
        bb_upper is not None
        and bb_lower is not None
        and bb_upper > bb_lower
    ):
        band_width_pct = ((bb_upper - bb_lower) / close) * 100
        if band_width_pct >= 12:
            volatility = TalibVolatilityStatus.HIGH
            risk_note = risk_note or "Wide Bollinger bands"
            notes.append("Wide Bollinger bands")

    return volatility, risk_note


def _interpret_volume_confirmation(
    obv: np.ndarray,
    closes: np.ndarray,
    notes: list[str],
) -> TalibVolumeConfirmation:
    if obv.size < OBV_LOOKBACK_BARS or closes.size < OBV_LOOKBACK_BARS:
        return TalibVolumeConfirmation.UNKNOWN

    recent_obv = obv[-OBV_LOOKBACK_BARS:]
    if np.any(np.isnan(recent_obv)):
        return TalibVolumeConfirmation.UNKNOWN

    obv_slope = float(recent_obv[-1] - recent_obv[0])
    price_slope = float(closes[-1] - closes[0])

    if obv_slope > 0:
        notes.append("OBV rising")
        return TalibVolumeConfirmation.YES
    if price_slope > 0 and obv_slope <= 0:
        notes.append("OBV flat/down while price rises")
        return TalibVolumeConfirmation.NO
    return TalibVolumeConfirmation.UNKNOWN


def _overall_status(
    trend: TalibTrendStatus,
    momentum: TalibMomentumStatus,
    volatility: TalibVolatilityStatus,
    volume_confirmation: TalibVolumeConfirmation,
    indicators: dict[str, float | None],
) -> TalibOverallStatus:
    rsi = indicators.get("rsi")
    if trend == TalibTrendStatus.BEARISH or momentum == TalibMomentumStatus.WEAK:
        return TalibOverallStatus.WEAK
    if (
        momentum == TalibMomentumStatus.HOT
        or volatility == TalibVolatilityStatus.HIGH
        or (volume_confirmation == TalibVolumeConfirmation.NO and rsi is not None and rsi > 60)
    ):
        return TalibOverallStatus.CAUTION
    if (
        trend == TalibTrendStatus.BULLISH
        and momentum in (TalibMomentumStatus.HEALTHY, TalibMomentumStatus.STRONG)
        and volume_confirmation == TalibVolumeConfirmation.YES
    ):
        return TalibOverallStatus.STRONG
    return TalibOverallStatus.OK


def build_talib_lookup_for_symbols(
    symbols: list[str],
    *,
    history_store: LiveVolumeHistoryStore,
    live_snapshot: LiveMarketSnapshot,
    config: TalibTechnicalConfig | None = None,
) -> tuple[dict[str, TalibTechnicalResult], list[str]]:
    """Build TA-Lib results for a list of symbols using saved live history."""
    values = config or TalibTechnicalConfig()
    warnings: list[str] = []
    if not values.enabled:
        return {}, warnings
    if not TALIB_AVAILABLE:
        return {}, [TALIB_NOT_INSTALLED_WARNING]

    lookup: dict[str, TalibTechnicalResult] = {}
    insufficient_count = 0
    as_of_date = live_snapshot.as_of_date

    for symbol in symbols:
        snap = live_snapshot.symbols.get(symbol)
        current_bar = None
        if snap is not None:
            current_bar = {
                "open": float(snap.open),
                "high": float(snap.high),
                "low": float(snap.low),
                "close": float(snap.close),
                "volume": float(snap.volume),
            }

        bars = history_store.load_ohlcv_series(
            symbol,
            before_date=as_of_date,
            count=values.min_history_days,
            current_bar=current_bar,
        )
        result = evaluate_talib_technical_from_bars(bars, values)
        lookup[symbol] = result
        if result.status == TalibOverallStatus.INSUFFICIENT_HISTORY:
            insufficient_count += 1

    if insufficient_count and symbols:
        if insufficient_count == len(symbols):
            warnings.append(
                "TA-Lib: insufficient saved history for all report candidates"
            )
        elif insufficient_count >= max(3, len(symbols) // 2):
            warnings.append(
                f"TA-Lib: insufficient saved history for {insufficient_count} candidates"
            )

    return lookup, warnings


def format_talib_technical_line(result: TalibTechnicalResult) -> str:
    """Render a compact TA-Lib line for Top Candidates."""
    if not result.talib_available:
        if result.status == TalibOverallStatus.INSUFFICIENT_HISTORY:
            return (
                "TA-Lib: INSUFFICIENT_HISTORY | "
                f"{TALIB_INSUFFICIENT_HISTORY_NOTE}"
            )
        return "TA-Lib: unavailable"

    volume_label = result.volume_confirmation.value
    if result.volume_confirmation == TalibVolumeConfirmation.YES:
        volume_label = "OBV confirmed"
    elif result.volume_confirmation == TalibVolumeConfirmation.NO:
        volume_label = "OBV weak"

    return (
        f"TA-Lib: {result.status.value} | "
        f"Trend {result.trend_status.value} | "
        f"Momentum {result.momentum_status.value} | "
        f"Volatility {result.volatility_status.value} | "
        f"{volume_label}"
    )


def format_talib_strategy_note(result: TalibTechnicalResult) -> str | None:
    """Render a short TA-Lib timing note for Strategy Signals."""
    if not result.talib_available:
        return None
    if result.status == TalibOverallStatus.INSUFFICIENT_HISTORY:
        return None

    if result.volatility_status == TalibVolatilityStatus.HIGH or (
        result.momentum_status == TalibMomentumStatus.HOT
    ):
        risk = "HIGH"
    elif (
        result.volatility_status == TalibVolatilityStatus.LOW
        and result.trend_status == TalibTrendStatus.BULLISH
    ):
        risk = "LOW"
    else:
        risk = "MEDIUM"

    return f"TA-Lib: Trend {result.trend_status.value} | Risk {risk}"
