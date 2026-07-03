"""Tests for sanitize_log_text and cloud report runner diagnostics."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.cloud_report_runner import (
    CLOUD_REPORT_FAILURE_MARKER,
    REDACTED_TELEGRAM_TOKEN,
    REPORT_ALREADY_RUNNING_MESSAGE,
    REPORT_FAILURE_PREFIX,
    REPORT_STARTING_MESSAGE,
    REPORT_SUCCESS_CLOSED_DIGEST_FOOTER,
    REPORT_SUCCESS_FOOTER,
    REPORT_TIMEOUT_MESSAGE,
    ReportRunLock,
    ReportRunResult,
    build_default_report_command,
    build_cloud_report_failure_log_message,
    format_report_run_telegram_message,
    report_run_lock,
    run_report_once,
    sanitize_log_text,
)
from core.telegram_bot import BTN_REFRESH_REPORT, build_main_menu


def test_sanitize_log_text_redacts_env_token() -> None:
    text = "Error: TELEGRAM_BOT_TOKEN=123456789:AAFakeTokenValue"
    sanitized = sanitize_log_text(text)

    assert "AAFakeTokenValue" not in sanitized
    assert f"TELEGRAM_BOT_TOKEN={REDACTED_TELEGRAM_TOKEN}" in sanitized


def test_sanitize_log_text_redacts_bot_token_pattern() -> None:
    text = "Authorization failed for bot987654321:AAExampleToken-123"
    sanitized = sanitize_log_text(text)

    assert "AAExampleToken-123" not in sanitized
    assert REDACTED_TELEGRAM_TOKEN in sanitized


def test_sanitize_log_text_redacts_telegram_api_url() -> None:
    text = "GET https://api.telegram.org/bot123456789:AAExampleToken/getUpdates"
    sanitized = sanitize_log_text(text)

    assert "AAExampleToken" not in sanitized
    assert f"https://api.telegram.org/bot{REDACTED_TELEGRAM_TOKEN}" in sanitized


def test_sanitize_log_text_preserves_unrelated_error_lines() -> None:
    text = "TradingView fetch failed with extended fields"
    assert sanitize_log_text(text) == text


def test_build_default_report_command() -> None:
    command = build_default_report_command(Path("/app"))

    assert command[1].endswith("main.py")
    assert "--egx-workflow" in command
    assert "report" in command
    assert "--data-provider" in command
    assert "tradingview" in command
    assert "--scanner-universe" in command
    assert "full-market" in command
    assert "--top-candidates" in command
    assert "10" in command
    assert "--min-score" in command
    assert "75" in command


def test_build_cloud_report_failure_log_message_format() -> None:
    message = build_cloud_report_failure_log_message(
        command=["python", "main.py", "--egx-workflow", "report"],
        returncode=1,
        stdout="line on stdout",
        stderr="TradingView fetch failed",
    )

    assert CLOUD_REPORT_FAILURE_MARKER in message
    assert "command=python main.py --egx-workflow report" in message
    assert "return_code=1" in message
    assert "stdout_tail=\nline on stdout" in message
    assert "stderr_tail=\nTradingView fetch failed" in message


def test_build_cloud_report_failure_log_message_redacts_tokens() -> None:
    message = build_cloud_report_failure_log_message(
        command=["python", "main.py"],
        returncode=1,
        stdout="",
        stderr="TELEGRAM_BOT_TOKEN=123456789:AAFakeTokenValue",
    )

    assert "AAFakeTokenValue" not in message
    assert f"TELEGRAM_BOT_TOKEN={REDACTED_TELEGRAM_TOKEN}" in message


@patch("core.cloud_report_runner.subprocess.run")
def test_run_report_once_failure_emits_single_warning_log_block(
    mock_run: MagicMock,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_run.return_value = subprocess.CompletedProcess(
        args=["python", "main.py"],
        returncode=1,
        stdout="stdout error details",
        stderr="TradingView fetch failed",
    )

    with caplog.at_level(logging.WARNING, logger="core.cloud_report_runner"):
        with patch("core.cloud_report_runner.settings.REPORTS_DIR", tmp_path / "reports"):
            run_report_once(
                project_root=tmp_path,
                command=["python", "main.py", "--egx-workflow", "report"],
            )

    failure_records = [
        record
        for record in caplog.records
        if record.levelname == "WARNING"
        and CLOUD_REPORT_FAILURE_MARKER in record.getMessage()
    ]
    assert len(failure_records) == 1

    log_message = failure_records[0].getMessage()
    assert "return_code=1" in log_message
    assert "stdout_tail=" in log_message
    assert "stderr_tail=" in log_message
    assert "stdout error details" in log_message
    assert "TradingView fetch failed" in log_message
    assert "AAFakeToken" not in log_message


@patch("core.cloud_report_runner.subprocess.run")
def test_run_report_once_success(mock_run: MagicMock, tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    report_path = reports_dir / "egx_daily_report_20260703_120000.json"
    report_path.write_text('{"report_date": "2026-07-03"}', encoding="utf-8")

    mock_run.return_value = subprocess.CompletedProcess(
        args=["python", "main.py"],
        returncode=0,
        stdout="report saved",
        stderr="",
    )

    with patch("core.cloud_report_runner.settings.REPORTS_DIR", reports_dir):
        result = run_report_once(
            project_root=tmp_path,
            command=["python", "main.py", "--egx-workflow", "report"],
        )

    assert result.success is True
    assert result.returncode == 0
    assert result.latest_report_path == str(report_path)


@patch("core.cloud_report_runner.subprocess.run")
def test_run_report_once_failure(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = subprocess.CompletedProcess(
        args=["python", "main.py"],
        returncode=1,
        stdout="",
        stderr="TradingView fetch failed",
    )

    with patch("core.cloud_report_runner.settings.REPORTS_DIR", tmp_path / "reports"):
        result = run_report_once(
            project_root=tmp_path,
            command=["python", "main.py", "--egx-workflow", "report"],
        )

    assert result.success is False
    assert result.returncode == 1
    assert "TradingView fetch failed" in result.stderr_tail


@patch("core.cloud_report_runner.subprocess.run")
def test_run_report_once_timeout(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd=["python", "main.py"],
        timeout=300,
        output="partial",
        stderr="still working",
    )

    result = run_report_once(
        project_root=tmp_path,
        command=["python", "main.py", "--egx-workflow", "report"],
        timeout_seconds=300,
    )

    assert result.success is False
    assert result.error == "timeout"


def test_report_run_lock_prevents_concurrent_runs() -> None:
    lock = ReportRunLock()

    assert lock.try_acquire() is True
    assert lock.try_acquire() is False
    lock.release()
    assert lock.try_acquire() is True
    lock.release()


def test_global_report_run_lock_is_singleton() -> None:
    assert report_run_lock.try_acquire() is True
    assert report_run_lock.try_acquire() is False
    report_run_lock.release()


def test_format_report_run_success_message() -> None:
    result = ReportRunResult(
        success=True,
        returncode=0,
        stdout_tail="ok",
        stderr_tail="",
        error=None,
        latest_report_path="/tmp/report.json",
    )

    message = format_report_run_telegram_message(result, overview_text="📅 التاريخ: 2026-07-03")

    assert "📅 التاريخ: 2026-07-03" in message
    assert REPORT_SUCCESS_FOOTER in message


def test_format_report_run_success_message_closed_market_digest() -> None:
    result = ReportRunResult(
        success=True,
        returncode=0,
        stdout_tail="ok",
        stderr_tail="",
        error=None,
        latest_report_path="/tmp/report.json",
    )

    message = format_report_run_telegram_message(
        result,
        overview_text="📅 التاريخ: 2026-07-03",
        closed_market_digest={"enabled": True, "reason": "weekend"},
    )

    assert REPORT_SUCCESS_CLOSED_DIGEST_FOOTER in message
    assert REPORT_SUCCESS_FOOTER not in message


@patch("core.cloud_report_runner.subprocess.run")
def test_run_report_once_redacts_sensitive_stderr(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = subprocess.CompletedProcess(
        args=["python", "main.py"],
        returncode=1,
        stdout="",
        stderr="TELEGRAM_BOT_TOKEN=secret-value\nTradingView fetch failed",
    )

    with patch("core.cloud_report_runner.settings.REPORTS_DIR", tmp_path / "reports"):
        result = run_report_once(
            project_root=tmp_path,
            command=["python", "main.py", "--egx-workflow", "report"],
        )

    assert "secret-value" not in result.stderr_tail
    assert f"TELEGRAM_BOT_TOKEN={REDACTED_TELEGRAM_TOKEN}" in result.stderr_tail
    assert "TradingView fetch failed" in result.stderr_tail


def test_format_report_run_failure_and_timeout_messages() -> None:
    failure = ReportRunResult(
        success=False,
        returncode=1,
        stdout_tail="",
        stderr_tail="fetch failed",
        error="returncode=1",
        latest_report_path=None,
    )
    timeout = ReportRunResult(
        success=False,
        returncode=None,
        stdout_tail="",
        stderr_tail="",
        error="timeout",
        latest_report_path=None,
    )

    failure_message = format_report_run_telegram_message(failure)
    assert REPORT_FAILURE_PREFIX in failure_message
    assert "fetch failed" in failure_message
    assert format_report_run_telegram_message(timeout) == REPORT_TIMEOUT_MESSAGE


def test_main_menu_includes_refresh_report_button() -> None:
    labels = {button.text for row in build_main_menu().keyboard for button in row}

    assert BTN_REFRESH_REPORT in labels


def test_refresh_report_messages_are_defined() -> None:
    assert "استنى" in REPORT_STARTING_MESSAGE
    assert "استنى" in REPORT_ALREADY_RUNNING_MESSAGE
