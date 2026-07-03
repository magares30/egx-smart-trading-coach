"""Compact confirmation summary from existing TradingView, timing, and TA-Lib statuses."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.multi_timeframe import EntryTimingStatus
from core.technical_confirmation import TechnicalStatus
from core.talib_technical import TalibOverallStatus, TALIB_STATUS_FALLBACK

CONFIRMATION_SUMMARY_NOTE = (
    "Uses existing TradingView, timing, and TA-Lib statuses; no new indicators"
)
EXEC_CONFIRMATION_NONE = "no actionable confirmations today"


class ConfirmationLabel(str, Enum):
    STRONG_CONFIRMATION = "STRONG_CONFIRMATION"
    GOOD_CONFIRMATION = "GOOD_CONFIRMATION"
    MIXED_CONFIRMATION = "MIXED_CONFIRMATION"
    WEAK_CONFIRMATION = "WEAK_CONFIRMATION"
    WAITING_FOR_HISTORY = "WAITING_FOR_HISTORY"


_TV_STRONG = {TechnicalStatus.STRONG, TechnicalStatus.OK}
_TV_WEAK = {TechnicalStatus.CAUTION, TechnicalStatus.WEAK}
_TIMING_READY = {EntryTimingStatus.READY}
_TIMING_MIXED = {
    EntryTimingStatus.WATCH,
    EntryTimingStatus.WAIT,
    EntryTimingStatus.AVOID,
    EntryTimingStatus.UNKNOWN,
}
_TALIB_POSITIVE = {TalibOverallStatus.STRONG, TalibOverallStatus.OK}


def _coerce_tv_status(status: TechnicalStatus | str | None) -> TechnicalStatus | None:
    if status is None:
        return None
    if isinstance(status, TechnicalStatus):
        return status
    try:
        return TechnicalStatus(str(status))
    except ValueError:
        return None


def _coerce_timing_status(
    status: EntryTimingStatus | str | None,
) -> EntryTimingStatus | None:
    if status is None:
        return None
    if isinstance(status, EntryTimingStatus):
        return status
    try:
        return EntryTimingStatus(str(status))
    except ValueError:
        return None


def _coerce_talib_status(
    status: TalibOverallStatus | str | None,
) -> TalibOverallStatus | None:
    if status is None:
        return None
    if isinstance(status, TalibOverallStatus):
        return status
    try:
        return TalibOverallStatus(str(status))
    except ValueError:
        return None


def _display_label(label: ConfirmationLabel) -> str:
    return {
        ConfirmationLabel.STRONG_CONFIRMATION: "STRONG",
        ConfirmationLabel.GOOD_CONFIRMATION: "GOOD",
        ConfirmationLabel.MIXED_CONFIRMATION: "MIXED",
        ConfirmationLabel.WEAK_CONFIRMATION: "WEAK",
        ConfirmationLabel.WAITING_FOR_HISTORY: "WAITING",
    }[label]


def _format_tv_phrase(status: TechnicalStatus | None) -> str:
    if status in _TV_STRONG:
        return "TV strong"
    if status == TechnicalStatus.CAUTION:
        return "TV caution"
    if status == TechnicalStatus.WEAK:
        return "TV weak"
    return "TV n/a"


def _format_timing_phrase(status: EntryTimingStatus | None) -> str:
    if status == EntryTimingStatus.READY:
        return "Timing ready"
    if status == EntryTimingStatus.WATCH:
        return "Timing watch"
    if status == EntryTimingStatus.WAIT:
        return "Timing wait"
    if status == EntryTimingStatus.AVOID:
        return "Timing avoid"
    return "Timing n/a"


def _format_talib_phrase(
    status: TalibOverallStatus | str | None,
    *,
    talib_enabled: bool,
) -> str:
    if not talib_enabled:
        return "TA-Lib n/a"
    if status == TALIB_STATUS_FALLBACK:
        return "TA-Lib fallback"
    if not isinstance(status, TalibOverallStatus):
        status = _coerce_talib_status(status)
    if status in _TALIB_POSITIVE:
        return "TA-Lib aligned"
    if status == TalibOverallStatus.INSUFFICIENT_HISTORY:
        return "TA-Lib waiting history"
    if status == TalibOverallStatus.CAUTION:
        return "TA-Lib caution"
    if status == TalibOverallStatus.WEAK:
        return "TA-Lib weak"
    return "TA-Lib n/a"


def classify_confirmation_label(
    *,
    tv_status: TechnicalStatus | str | None,
    timing_status: EntryTimingStatus | str | None,
    talib_status: TalibOverallStatus | str | None = None,
    talib_enabled: bool = True,
) -> ConfirmationLabel:
    """Classify one signal using existing confirmation layer statuses only."""
    tv = _coerce_tv_status(tv_status)
    timing = _coerce_timing_status(timing_status)
    talib = _coerce_talib_status(talib_status)

    if tv in _TV_WEAK:
        return ConfirmationLabel.WEAK_CONFIRMATION

    if tv in _TV_STRONG and timing in _TIMING_READY:
        if talib_enabled and talib == TalibOverallStatus.INSUFFICIENT_HISTORY:
            return ConfirmationLabel.GOOD_CONFIRMATION
        if talib_enabled and talib in _TALIB_POSITIVE:
            return ConfirmationLabel.STRONG_CONFIRMATION
        if not talib_enabled or talib is None:
            return ConfirmationLabel.GOOD_CONFIRMATION
        if talib in {TalibOverallStatus.CAUTION, TalibOverallStatus.WEAK}:
            return ConfirmationLabel.MIXED_CONFIRMATION
        return ConfirmationLabel.GOOD_CONFIRMATION

    if tv in _TV_STRONG and (timing in _TIMING_MIXED or timing is None):
        return ConfirmationLabel.MIXED_CONFIRMATION

    return ConfirmationLabel.MIXED_CONFIRMATION


def build_confirmation_text(
    label: ConfirmationLabel,
    *,
    tv_status: TechnicalStatus | str | None,
    timing_status: EntryTimingStatus | str | None,
    talib_status: TalibOverallStatus | str | None = None,
    talib_enabled: bool = True,
) -> str:
    """Render one compact confirmation line for TXT reports."""
    tv = _coerce_tv_status(tv_status)
    timing = _coerce_timing_status(timing_status)
    talib = _coerce_talib_status(talib_status)
    parts = [
        _format_tv_phrase(tv),
        _format_timing_phrase(timing),
        _format_talib_phrase(talib_status, talib_enabled=talib_enabled),
    ]
    return f"Confirmation: {_display_label(label)} | {' | '.join(parts)}"


@dataclass(frozen=True)
class SignalConfirmationSummary:
    """Confirmation summary for one strategy signal."""

    symbol: str
    label: ConfirmationLabel
    confirmation_text: str
    tv_status: str | None
    timing_status: str | None
    talib_status: str | None
    waiting_for_history: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "confirmation_label": self.label.value,
            "confirmation_text": self.confirmation_text,
            "tv_status": self.tv_status,
            "timing_status": self.timing_status,
            "talib_status": self.talib_status,
        }


def build_signal_confirmation_summary(
    symbol: str,
    *,
    tv_status: TechnicalStatus | str | None,
    timing_status: EntryTimingStatus | str | None,
    talib_status: TalibOverallStatus | str | None = None,
    talib_enabled: bool = True,
) -> SignalConfirmationSummary:
    """Build confirmation summary for one strategy signal."""
    talib = _coerce_talib_status(talib_status)
    label = classify_confirmation_label(
        tv_status=tv_status,
        timing_status=timing_status,
        talib_status=talib_status,
        talib_enabled=talib_enabled,
    )
    text = build_confirmation_text(
        label,
        tv_status=tv_status,
        timing_status=timing_status,
        talib_status=talib_status,
        talib_enabled=talib_enabled,
    )
    if talib_enabled and talib is None and talib_status == TALIB_STATUS_FALLBACK:
        serialized_talib_status: str | None = TALIB_STATUS_FALLBACK
    else:
        serialized_talib_status = talib.value if talib is not None else None
    return SignalConfirmationSummary(
        symbol=symbol,
        label=label,
        confirmation_text=text,
        tv_status=tv.value if (tv := _coerce_tv_status(tv_status)) else None,
        timing_status=(
            timing.value if (timing := _coerce_timing_status(timing_status)) else None
        ),
        talib_status=serialized_talib_status,
        waiting_for_history=(
            talib_enabled and talib == TalibOverallStatus.INSUFFICIENT_HISTORY
        ),
    )


@dataclass(frozen=True)
class ConfirmationSummary:
    """Aggregate confirmation buckets for JSON and executive summary."""

    strong: list[str]
    good: list[str]
    mixed: list[str]
    weak: list[str]
    waiting_for_history: list[str]
    signals: list[SignalConfirmationSummary]
    note: str = CONFIRMATION_SUMMARY_NOTE

    def to_dict(self) -> dict[str, object]:
        return {
            "strong": list(self.strong),
            "good": list(self.good),
            "mixed": list(self.mixed),
            "weak": list(self.weak),
            "waiting_for_history": list(self.waiting_for_history),
            "note": self.note,
            "signals": [signal.to_dict() for signal in self.signals],
        }


def build_confirmation_summary(
    signal_summaries: list[SignalConfirmationSummary],
) -> ConfirmationSummary:
    """Bucket per-signal confirmation summaries for JSON output."""
    strong: list[str] = []
    good: list[str] = []
    mixed: list[str] = []
    weak: list[str] = []
    waiting_for_history: list[str] = []

    for summary in signal_summaries:
        if summary.label == ConfirmationLabel.STRONG_CONFIRMATION:
            strong.append(summary.symbol)
        elif summary.label == ConfirmationLabel.GOOD_CONFIRMATION:
            good.append(summary.symbol)
        elif summary.label == ConfirmationLabel.MIXED_CONFIRMATION:
            mixed.append(summary.symbol)
        elif summary.label == ConfirmationLabel.WEAK_CONFIRMATION:
            weak.append(summary.symbol)
        elif summary.label == ConfirmationLabel.WAITING_FOR_HISTORY:
            waiting_for_history.append(summary.symbol)
        if summary.waiting_for_history and summary.symbol not in waiting_for_history:
            waiting_for_history.append(summary.symbol)

    return ConfirmationSummary(
        strong=strong,
        good=good,
        mixed=mixed,
        weak=weak,
        waiting_for_history=waiting_for_history,
        signals=signal_summaries,
    )


def build_executive_confirmation_line(
    signal_summaries: list[SignalConfirmationSummary],
) -> str:
    """Build one compact executive-summary confirmation line."""
    if not signal_summaries:
        return EXEC_CONFIRMATION_NONE

    strong_count = sum(
        1
        for summary in signal_summaries
        if summary.label == ConfirmationLabel.STRONG_CONFIRMATION
    )
    good_count = sum(
        1
        for summary in signal_summaries
        if summary.label == ConfirmationLabel.GOOD_CONFIRMATION
    )
    mixed_count = sum(
        1
        for summary in signal_summaries
        if summary.label == ConfirmationLabel.MIXED_CONFIRMATION
    )
    weak_count = sum(
        1
        for summary in signal_summaries
        if summary.label == ConfirmationLabel.WEAK_CONFIRMATION
    )
    waiting = any(summary.waiting_for_history for summary in signal_summaries)

    if strong_count:
        line = f"{strong_count} strong setup{'s' if strong_count != 1 else ''}"
    elif good_count:
        line = f"{good_count} good setup{'s' if good_count != 1 else ''}"
    elif mixed_count:
        line = f"{mixed_count} mixed setup{'s' if mixed_count != 1 else ''}"
    elif weak_count:
        line = f"{weak_count} weak setup{'s' if weak_count != 1 else ''}"
    else:
        line = f"{len(signal_summaries)} setup{'s' if len(signal_summaries) != 1 else ''}"

    if waiting:
        return f"{line}; TA-Lib still waiting for history"
    return line
