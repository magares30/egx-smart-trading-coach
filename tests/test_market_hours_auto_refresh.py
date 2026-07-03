"""Tests for market-hours automatic cloud report refresh."""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import patch

import pytest

from core.cloud_report_runner import ReportRunResult, report_run_lock
from core.market_hours import (
    CAIRO_TZ,
    sample_open_market_datetime,
    sample_weekend_market_datetime,
)
from core.market_hours_auto_refresh import (
    EGX_AUTO_REFRESH_ENABLED_ENV,
    EGX_AUTO_REFRESH_INTERVAL_SECONDS_ENV,
    AUTO_REFRESH_SUCCESS_LOG,
    AutoRefreshDecision,
    AutoRefreshDayState,
    MarketHoursAutoRefreshWorker,
    get_auto_refresh_interval_seconds,
    is_auto_refresh_enabled,
    is_egx_friday_or_saturday,
    run_report_once_with_lock,
    should_allow_auto_refresh_now,
    start_market_hours_auto_refresh_worker,
)
from core.telegram_bot import BTN_REFRESH_REPORT, format_help


def _cairo_dt(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=CAIRO_TZ)


def test_is_auto_refresh_enabled() -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert is_auto_refresh_enabled() is False
    with patch.dict(os.environ, {EGX_AUTO_REFRESH_ENABLED_ENV: "true"}, clear=False):
        assert is_auto_refresh_enabled() is True


def test_get_auto_refresh_interval_seconds_defaults_and_configurable() -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert get_auto_refresh_interval_seconds() == 180
    with patch.dict(os.environ, {EGX_AUTO_REFRESH_INTERVAL_SECONDS_ENV: "240"}, clear=False):
        assert get_auto_refresh_interval_seconds() == 240


def test_friday_and_saturday_do_not_refresh() -> None:
    friday = sample_weekend_market_datetime()
    assert is_egx_friday_or_saturday(friday.date()) is True

    worker = MarketHoursAutoRefreshWorker(enabled=True)
    assert worker.evaluate(now=friday) == AutoRefreshDecision.SKIP_NON_TRADING_DAY
    assert should_allow_auto_refresh_now(friday, enabled=True) is False


def test_saturday_no_refresh() -> None:
    saturday = _cairo_dt(2026, 7, 11, 11, 0)
    assert is_egx_friday_or_saturday(saturday.date()) is True
    worker = MarketHoursAutoRefreshWorker(enabled=True)
    assert worker.evaluate(now=saturday) == AutoRefreshDecision.SKIP_NON_TRADING_DAY


def test_outside_market_hours_no_refresh() -> None:
    worker = MarketHoursAutoRefreshWorker(enabled=True)
    before_preopen = _cairo_dt(2026, 7, 7, 8, 30)
    assert worker.evaluate(now=before_preopen) == AutoRefreshDecision.SKIP_OUTSIDE_WINDOW

    late_night = _cairo_dt(2026, 7, 7, 20, 0)
    worker.state.postclose_date = late_night.date()
    assert worker.evaluate(now=late_night) == AutoRefreshDecision.SKIP_ALREADY_DONE


def test_during_market_hours_refresh_allowed() -> None:
    worker = MarketHoursAutoRefreshWorker(enabled=True, interval_seconds=180)
    open_moment = sample_open_market_datetime()
    assert worker.evaluate(now=open_moment) == AutoRefreshDecision.RUN_INTRADAY

    preopen = _cairo_dt(2026, 7, 7, 9, 35)
    assert worker.evaluate(now=preopen) == AutoRefreshDecision.RUN_PREOPEN

    postclose = _cairo_dt(2026, 7, 7, 14, 35)
    assert worker.evaluate(now=postclose) == AutoRefreshDecision.RUN_POSTCLOSE


def test_preopen_runs_once_per_day() -> None:
    worker = MarketHoursAutoRefreshWorker(enabled=True)
    preopen = _cairo_dt(2026, 7, 7, 9, 35)
    assert worker.evaluate(now=preopen) == AutoRefreshDecision.RUN_PREOPEN
    worker.mark_success(AutoRefreshDecision.RUN_PREOPEN, now=preopen)
    assert worker.evaluate(now=preopen) == AutoRefreshDecision.SKIP_ALREADY_DONE


def test_intraday_respects_interval() -> None:
    state = AutoRefreshDayState()
    open_moment = sample_open_market_datetime()
    state.last_intraday_refresh_at = open_moment
    worker = MarketHoursAutoRefreshWorker(
        enabled=True,
        interval_seconds=180,
        state=state,
    )
    soon_after = open_moment.replace(minute=open_moment.minute + 1)
    assert worker.evaluate(now=soon_after) == AutoRefreshDecision.SKIP_WAIT_INTERVAL

    later = open_moment.replace(minute=open_moment.minute + 4)
    assert worker.evaluate(now=later) == AutoRefreshDecision.RUN_INTRADAY


def test_lock_prevents_overlapping_refresh() -> None:
    worker = MarketHoursAutoRefreshWorker(enabled=True)
    assert report_run_lock.try_acquire() is True
    try:
        decision = worker.tick(now=sample_open_market_datetime())
        assert decision == AutoRefreshDecision.SKIP_LOCK_BUSY
    finally:
        report_run_lock.release()


def test_tick_logs_success_and_uses_report_runner(
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = MarketHoursAutoRefreshWorker(enabled=True)
    success = ReportRunResult(
        success=True,
        returncode=0,
        stdout_tail="",
        stderr_tail="",
        error=None,
        latest_report_path="/tmp/report.json",
    )

    with patch("core.market_hours_auto_refresh.run_report_once", return_value=success):
        with caplog.at_level("INFO"):
            decision = worker.tick(now=sample_open_market_datetime())

    assert decision == AutoRefreshDecision.RUN_INTRADAY
    assert AUTO_REFRESH_SUCCESS_LOG in caplog.text
    assert worker.state.last_intraday_refresh_at is not None


def test_run_report_once_with_lock_returns_none_when_busy() -> None:
    assert report_run_lock.try_acquire() is True
    try:
        assert run_report_once_with_lock() is None
    finally:
        report_run_lock.release()


def test_manual_refresh_uses_same_lock_as_auto_refresh() -> None:
    from core.cloud_report_runner import report_run_lock as cloud_lock

    assert report_run_lock is cloud_lock


def test_start_worker_only_when_enabled() -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert start_market_hours_auto_refresh_worker() is None

    with patch.dict(os.environ, {EGX_AUTO_REFRESH_ENABLED_ENV: "true"}, clear=False):
        worker = start_market_hours_auto_refresh_worker()
        assert worker is not None
        worker.stop()


def test_format_help_mentions_auto_refresh_when_enabled() -> None:
    with patch.dict(os.environ, {EGX_AUTO_REFRESH_ENABLED_ENV: "true"}, clear=False):
        help_text = format_help()
    assert BTN_REFRESH_REPORT in help_text
    assert "التحديث التلقائي" in help_text
    assert "الجمعة/السبت" in help_text
