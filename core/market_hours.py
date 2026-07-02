"""EGX market session detection and paper-trading hours guard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from zoneinfo import ZoneInfo

from config import settings

CAIRO_TIMEZONE = "Africa/Cairo"
CAIRO_TZ = ZoneInfo(CAIRO_TIMEZONE)

PREOPEN_START = time(9, 30)
OPEN_START = time(10, 0)
CLOSING_AUCTION_START = time(14, 15)
TRADE_AT_CLOSE_START = time(14, 25)
SESSION_END = time(14, 30)

# Python weekday(): Monday=0 ... Sunday=6
EGX_TRADING_WEEKDAYS = frozenset({6, 0, 1, 2, 3})  # Sunday through Thursday


class EgxSessionStatus(str, Enum):
    PREOPEN = "PREOPEN"
    OPEN = "OPEN"
    CLOSING_AUCTION = "CLOSING_AUCTION"
    TRADE_AT_CLOSE = "TRADE_AT_CLOSE"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class EgxMarketSession:
    """Current EGX market session state in Cairo local time."""

    is_trading_day: bool
    session_status: EgxSessionStatus
    is_open_for_new_entries: bool
    is_after_close: bool
    cairo_time: str
    note: str
    paper_entries_enabled: bool
    guard_enabled: bool = True

    def to_dict(self) -> dict[str, object]:
        """Serialize market session for JSON report output."""
        return {
            "timezone": CAIRO_TIMEZONE,
            "cairo_time": self.cairo_time,
            "trading_day": self.is_trading_day,
            "status": self.session_status.value,
            "paper_entries_enabled": self.paper_entries_enabled,
            "is_open_for_new_entries": self.is_open_for_new_entries,
            "is_after_close": self.is_after_close,
            "guard_enabled": self.guard_enabled,
            "note": self.note,
        }


def is_egx_trading_weekday(day: date) -> bool:
    """Return True for EGX default trading weekdays (Sunday-Thursday)."""
    return day.weekday() in EGX_TRADING_WEEKDAYS


def is_egx_holiday(day: date, holidays: frozenset[date] | None = None) -> bool:
    """Return True when the date is a configured EGX holiday."""
    holiday_set = holidays if holidays is not None else settings.EGX_TRADING_HOLIDAYS
    return day in holiday_set


def is_egx_trading_day(day: date, holidays: frozenset[date] | None = None) -> bool:
    """Return True on configured EGX trading days."""
    return is_egx_trading_weekday(day) and not is_egx_holiday(day, holidays)


def _session_status_for_time(
    current_time: time,
    *,
    trading_day: bool,
) -> EgxSessionStatus:
    if not trading_day:
        return EgxSessionStatus.CLOSED
    if PREOPEN_START <= current_time < OPEN_START:
        return EgxSessionStatus.PREOPEN
    if OPEN_START <= current_time < CLOSING_AUCTION_START:
        return EgxSessionStatus.OPEN
    if CLOSING_AUCTION_START <= current_time < TRADE_AT_CLOSE_START:
        return EgxSessionStatus.CLOSING_AUCTION
    if TRADE_AT_CLOSE_START <= current_time < SESSION_END:
        return EgxSessionStatus.TRADE_AT_CLOSE
    return EgxSessionStatus.CLOSED


def _session_note(
    status: EgxSessionStatus,
    *,
    trading_day: bool,
    holiday: bool,
) -> str:
    if not trading_day:
        if holiday:
            return "EGX holiday. Market is closed today."
        return "EGX is closed today."
    if status == EgxSessionStatus.PREOPEN:
        return "Pre-open session. New paper entries are disabled."
    if status == EgxSessionStatus.OPEN:
        return "Continuous trading session is active."
    if status == EgxSessionStatus.CLOSING_AUCTION:
        return "Closing auction. New paper entries are disabled."
    if status == EgxSessionStatus.TRADE_AT_CLOSE:
        return "Trade-at-close window. New paper entries are disabled."
    return "Market is closed. Signals are next-session watchlist ideas only."


def detect_egx_market_session(
    *,
    now: datetime | None = None,
    ignore_market_hours: bool = False,
    holidays: frozenset[date] | None = None,
) -> EgxMarketSession:
    """Detect the current EGX market session using Cairo local time."""
    moment = now.astimezone(CAIRO_TZ) if now is not None else datetime.now(CAIRO_TZ)
    local_date = moment.date()
    local_time = moment.time().replace(microsecond=0)
    cairo_time = local_time.strftime("%H:%M")

    holiday = is_egx_holiday(local_date, holidays)
    trading_day = is_egx_trading_day(local_date, holidays)
    status = _session_status_for_time(local_time, trading_day=trading_day)
    is_open_for_new_entries = trading_day and status == EgxSessionStatus.OPEN
    is_after_close = trading_day and local_time >= SESSION_END
    guard_enabled = not ignore_market_hours
    paper_entries_enabled = is_open_for_new_entries or ignore_market_hours
    note = _session_note(status, trading_day=trading_day, holiday=holiday)
    if ignore_market_hours and not is_open_for_new_entries:
        note = f"{note} Market-hours guard ignored for paper entries."

    return EgxMarketSession(
        is_trading_day=trading_day,
        session_status=status,
        is_open_for_new_entries=is_open_for_new_entries,
        is_after_close=is_after_close,
        cairo_time=cairo_time,
        note=note,
        paper_entries_enabled=paper_entries_enabled,
        guard_enabled=guard_enabled,
    )


def format_market_session_report_lines(session: EgxMarketSession) -> list[str]:
    """Render compact Market Session lines for the daily report."""
    lines = [
        f"- Status: {session.session_status.value}",
    ]
    if session.is_trading_day:
        lines.append(f"- Cairo Time: {session.cairo_time}")
    lines.extend(
        [
            f"- Trading Day: {'yes' if session.is_trading_day else 'no'}",
            (
                "- Paper Entries: "
                + ("enabled" if session.paper_entries_enabled else "disabled")
            ),
            f"- Note: {session.note}",
        ]
    )
    return lines


def paper_entries_allowed(
    *,
    ignore_market_hours: bool = False,
    now: datetime | None = None,
) -> bool:
    """Return True when new paper entries are allowed under the hours guard."""
    session = detect_egx_market_session(
        now=now,
        ignore_market_hours=ignore_market_hours,
    )
    return session.paper_entries_enabled


def sample_open_market_datetime() -> datetime:
    """Return a fixed Cairo datetime during continuous trading for tests."""
    return datetime(2026, 7, 7, 11, 30, tzinfo=CAIRO_TZ)


def sample_closed_market_datetime() -> datetime:
    """Return a fixed Cairo datetime after the session close for tests."""
    return datetime(2026, 7, 7, 17, 10, tzinfo=CAIRO_TZ)


def sample_weekend_market_datetime() -> datetime:
    """Return a fixed Cairo datetime on an EGX non-trading day for tests."""
    return datetime(2026, 7, 10, 12, 0, tzinfo=CAIRO_TZ)
