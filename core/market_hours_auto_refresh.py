"""Market-hours automatic cloud report refresh for the Telegram bot process."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from core.cloud_report_runner import (
    ReportRunResult,
    report_run_lock,
    run_report_once,
)
from core.market_hours import (
    CAIRO_TZ,
    OPEN_START,
    PREOPEN_START,
    SESSION_END,
    is_egx_trading_day,
)

logger = logging.getLogger(__name__)

EGX_AUTO_REFRESH_ENABLED_ENV = "EGX_AUTO_REFRESH_ENABLED"
EGX_AUTO_REFRESH_INTERVAL_SECONDS_ENV = "EGX_AUTO_REFRESH_INTERVAL_SECONDS"
DEFAULT_AUTO_REFRESH_INTERVAL_SECONDS = 180
AUTO_REFRESH_SUCCESS_LOG = "Auto refresh completed"
AUTO_REFRESH_SKIPPED_BUSY_LOG = "Auto refresh skipped: report already running"
AUTO_REFRESH_FAILED_LOG = "Auto refresh failed"
OUTSIDE_WINDOW_SLEEP_SECONDS = 300
WAIT_POLL_SECONDS = 30


class AutoRefreshDecision(str, Enum):
    SKIP_NOT_ENABLED = "skip_not_enabled"
    SKIP_NON_TRADING_DAY = "skip_non_trading_day"
    SKIP_OUTSIDE_WINDOW = "skip_outside_window"
    SKIP_WAIT_INTERVAL = "skip_wait_interval"
    SKIP_ALREADY_DONE = "skip_already_done"
    SKIP_LOCK_BUSY = "skip_lock_busy"
    RUN_PREOPEN = "run_preopen"
    RUN_INTRADAY = "run_intraday"
    RUN_POSTCLOSE = "run_postclose"


@dataclass
class AutoRefreshDayState:
    """In-memory auto-refresh markers for the current Cairo trading day."""

    preopen_date: date | None = None
    postclose_date: date | None = None
    last_intraday_refresh_at: datetime | None = None

    def reset_for_date(self, local_date: date) -> None:
        if self.preopen_date not in (None, local_date):
            self.preopen_date = None
        if self.postclose_date not in (None, local_date):
            self.postclose_date = None
        if (
            self.last_intraday_refresh_at is not None
            and self.last_intraday_refresh_at.astimezone(CAIRO_TZ).date() != local_date
        ):
            self.last_intraday_refresh_at = None


@dataclass
class MarketHoursAutoRefreshWorker:
    """Background worker that refreshes reports during EGX market hours only."""

    interval_seconds: int = DEFAULT_AUTO_REFRESH_INTERVAL_SECONDS
    enabled: bool = True
    state: AutoRefreshDayState = field(default_factory=AutoRefreshDayState)
    _stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run_loop,
            name="egx-market-hours-auto-refresh",
            daemon=True,
        )
        self._thread.start()
        logger.info("Market-hours auto refresh worker started.")

    def stop(self) -> None:
        self._stop_event.set()

    def evaluate(self, now: datetime | None = None) -> AutoRefreshDecision:
        if not self.enabled:
            return AutoRefreshDecision.SKIP_NOT_ENABLED

        moment = (now or datetime.now(CAIRO_TZ)).astimezone(CAIRO_TZ)
        local_date = moment.date()
        local_time = moment.time().replace(microsecond=0)
        self.state.reset_for_date(local_date)

        if not is_egx_trading_day(local_date):
            return AutoRefreshDecision.SKIP_NON_TRADING_DAY

        if local_time < PREOPEN_START:
            return AutoRefreshDecision.SKIP_OUTSIDE_WINDOW

        if PREOPEN_START <= local_time < OPEN_START:
            if self.state.preopen_date == local_date:
                return AutoRefreshDecision.SKIP_ALREADY_DONE
            return AutoRefreshDecision.RUN_PREOPEN

        if OPEN_START <= local_time < SESSION_END:
            last_refresh = self.state.last_intraday_refresh_at
            if last_refresh is not None:
                elapsed = (moment - last_refresh.astimezone(CAIRO_TZ)).total_seconds()
                if elapsed < self.interval_seconds:
                    return AutoRefreshDecision.SKIP_WAIT_INTERVAL
            return AutoRefreshDecision.RUN_INTRADAY

        if self.state.postclose_date == local_date:
            return AutoRefreshDecision.SKIP_ALREADY_DONE
        return AutoRefreshDecision.RUN_POSTCLOSE

    def sleep_seconds_for(self, decision: AutoRefreshDecision, now: datetime | None = None) -> int:
        moment = (now or datetime.now(CAIRO_TZ)).astimezone(CAIRO_TZ)
        local_time = moment.time().replace(microsecond=0)

        if decision in {
            AutoRefreshDecision.SKIP_NON_TRADING_DAY,
            AutoRefreshDecision.SKIP_OUTSIDE_WINDOW,
            AutoRefreshDecision.SKIP_ALREADY_DONE,
            AutoRefreshDecision.SKIP_NOT_ENABLED,
        }:
            return OUTSIDE_WINDOW_SLEEP_SECONDS

        if decision == AutoRefreshDecision.SKIP_WAIT_INTERVAL:
            last_refresh = self.state.last_intraday_refresh_at
            if last_refresh is None:
                return WAIT_POLL_SECONDS
            elapsed = (moment - last_refresh.astimezone(CAIRO_TZ)).total_seconds()
            remaining = max(int(self.interval_seconds - elapsed), WAIT_POLL_SECONDS)
            return min(remaining, self.interval_seconds)

        return WAIT_POLL_SECONDS

    def mark_success(self, decision: AutoRefreshDecision, now: datetime | None = None) -> None:
        moment = (now or datetime.now(CAIRO_TZ)).astimezone(CAIRO_TZ)
        local_date = moment.date()
        if decision == AutoRefreshDecision.RUN_PREOPEN:
            self.state.preopen_date = local_date
        elif decision == AutoRefreshDecision.RUN_INTRADAY:
            self.state.last_intraday_refresh_at = moment
        elif decision == AutoRefreshDecision.RUN_POSTCLOSE:
            self.state.postclose_date = local_date

    def tick(self, now: datetime | None = None) -> AutoRefreshDecision:
        decision = self.evaluate(now=now)
        if decision not in {
            AutoRefreshDecision.RUN_PREOPEN,
            AutoRefreshDecision.RUN_INTRADAY,
            AutoRefreshDecision.RUN_POSTCLOSE,
        }:
            return decision

        if not report_run_lock.try_acquire():
            logger.info(AUTO_REFRESH_SKIPPED_BUSY_LOG)
            return AutoRefreshDecision.SKIP_LOCK_BUSY

        try:
            result = run_report_once()
        finally:
            report_run_lock.release()

        if result.success:
            self.mark_success(decision, now=now)
            logger.info(AUTO_REFRESH_SUCCESS_LOG)
            return decision

        logger.warning(AUTO_REFRESH_FAILED_LOG)
        return decision

    def run_loop(self) -> None:
        while not self._stop_event.is_set():
            decision = self.tick()
            sleep_seconds = self.sleep_seconds_for(decision)
            if self._stop_event.wait(sleep_seconds):
                break


def is_auto_refresh_enabled() -> bool:
    value = os.environ.get(EGX_AUTO_REFRESH_ENABLED_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def get_auto_refresh_interval_seconds() -> int:
    raw = os.environ.get(
        EGX_AUTO_REFRESH_INTERVAL_SECONDS_ENV,
        str(DEFAULT_AUTO_REFRESH_INTERVAL_SECONDS),
    ).strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = DEFAULT_AUTO_REFRESH_INTERVAL_SECONDS
    return max(60, parsed)


def is_egx_friday_or_saturday(day: date) -> bool:
    """Return True on EGX weekend days (Friday/Saturday in Cairo)."""
    return day.weekday() in {4, 5}


def should_allow_auto_refresh_now(
    now: datetime,
    *,
    enabled: bool = True,
    state: AutoRefreshDayState | None = None,
    interval_seconds: int = DEFAULT_AUTO_REFRESH_INTERVAL_SECONDS,
) -> bool:
    worker = MarketHoursAutoRefreshWorker(
        enabled=enabled,
        interval_seconds=interval_seconds,
        state=state or AutoRefreshDayState(),
    )
    decision = worker.evaluate(now=now)
    return decision in {
        AutoRefreshDecision.RUN_PREOPEN,
        AutoRefreshDecision.RUN_INTRADAY,
        AutoRefreshDecision.RUN_POSTCLOSE,
    }


def start_market_hours_auto_refresh_worker() -> MarketHoursAutoRefreshWorker | None:
    """Start the background auto-refresh worker when enabled by env var."""
    if not is_auto_refresh_enabled():
        return None
    worker = MarketHoursAutoRefreshWorker(
        enabled=True,
        interval_seconds=get_auto_refresh_interval_seconds(),
    )
    worker.start()
    return worker


def run_report_once_with_lock() -> ReportRunResult | None:
    """Run one cloud report when the in-process lock is available."""
    if not report_run_lock.try_acquire():
        return None
    try:
        return run_report_once()
    finally:
        report_run_lock.release()
