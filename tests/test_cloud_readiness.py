"""Tests for Cloud Run readiness checks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.cloud_readiness import (
    READINESS_HEADER,
    CheckStatus,
    check_directory,
    check_latest_report_json,
    check_report_command,
    check_talib_optional,
    check_telegram_bot_token,
    check_tradingview_screener_import,
    format_cloud_readiness_telegram_summary,
    format_readiness_report,
    readiness_exit_code,
    run_cloud_readiness_check,
    run_cloud_readiness_checks,
)
from core.cloud_report_runner import REDACTED_TELEGRAM_TOKEN
from core.telegram_bot import TELEGRAM_BOT_TOKEN_ENV, format_help
from main import parse_args


def test_parse_args_egx_cloud_readiness_check() -> None:
    args = parse_args(["--egx-cloud-readiness-check"])
    assert args.egx_cloud_readiness_check is True


def test_check_tradingview_screener_import_ok() -> None:
    check = check_tradingview_screener_import()
    assert check.status is CheckStatus.OK


def test_check_tradingview_screener_import_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(_name: str):
        raise ImportError("missing tradingview_screener")

    monkeypatch.setattr("core.cloud_readiness.importlib.import_module", _fail)
    check = check_tradingview_screener_import()
    assert check.status is CheckStatus.ERROR
    assert "import failed" in check.detail


def test_check_talib_optional_warning_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.talib_technical.TALIB_AVAILABLE", False)
    check = check_talib_optional()
    assert check.status is CheckStatus.WARNING
    assert "FALLBACK" in check.detail
    assert "talib package not installed" in check.detail


def test_check_directory_creates_path(tmp_path: Path) -> None:
    target = tmp_path / "reports"
    check = check_directory(target, "reports_dir")
    assert check.status is CheckStatus.OK
    assert target.is_dir()


def test_check_telegram_bot_token_redacted() -> None:
    with patch.dict(os.environ, {TELEGRAM_BOT_TOKEN_ENV: "1234567890:AAFakeTokenValue"}, clear=False):
        check = check_telegram_bot_token()

    assert check.status is CheckStatus.OK
    assert REDACTED_TELEGRAM_TOKEN in check.detail
    assert "1234567890:AAFakeTokenValue" not in check.detail


def test_check_telegram_bot_token_missing_is_error() -> None:
    env = os.environ.copy()
    env.pop(TELEGRAM_BOT_TOKEN_ENV, None)
    with patch.dict(os.environ, env, clear=True):
        check = check_telegram_bot_token()

    assert check.status is CheckStatus.ERROR


def test_check_report_command_validates_default_cli() -> None:
    check = check_report_command()
    assert check.status is CheckStatus.OK
    assert "tradingview" in check.detail
    assert "dry-run" in check.detail


def test_check_latest_report_missing_is_warning(tmp_path: Path) -> None:
    check = check_latest_report_json(tmp_path / "reports")
    assert check.status is CheckStatus.WARNING


def test_check_latest_report_readable(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    report_path = reports_dir / "egx_daily_report_20260703_010000.json"
    report_path.write_text(
        json.dumps({"report_date": "2026-07-03", "sections": []}),
        encoding="utf-8",
    )

    check = check_latest_report_json(reports_dir)
    assert check.status is CheckStatus.OK
    assert report_path.name in check.detail


def test_readiness_exit_code_treats_warnings_as_success() -> None:
    from core.cloud_readiness import ReadinessCheck, readiness_exit_code

    checks = [
        ReadinessCheck("talib", CheckStatus.WARNING, "optional"),
        ReadinessCheck("latest_report", CheckStatus.WARNING, "none yet"),
    ]
    assert readiness_exit_code(checks) == 0

    checks_with_error = checks + [
        ReadinessCheck("tradingview_screener", CheckStatus.ERROR, "missing"),
    ]
    assert readiness_exit_code(checks_with_error) == 1


def test_format_readiness_report_includes_talib_runtime_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("core.talib_technical.TALIB_AVAILABLE", False)
    checks = run_cloud_readiness_checks(
        reports_dir=tmp_path / "reports",
        storage_dir=tmp_path / "storage",
        check_token=False,
    )
    report = format_readiness_report(checks)
    assert "TA-Lib runtime: FALLBACK" in report
    assert "talib package not installed" in report


def test_format_readiness_report_never_leaks_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("core.talib_technical.TALIB_AVAILABLE", False)
    with patch.dict(os.environ, {TELEGRAM_BOT_TOKEN_ENV: "1234567890:AAFakeTokenValue"}, clear=False):
        checks = run_cloud_readiness_checks(
            reports_dir=tmp_path / "reports",
            storage_dir=tmp_path / "storage",
            check_token=True,
        )
        report = format_readiness_report(checks)

    assert READINESS_HEADER in report
    assert "1234567890:AAFakeTokenValue" not in report
    assert REDACTED_TELEGRAM_TOKEN in report


def test_format_cloud_readiness_telegram_summary_is_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("core.talib_technical.TALIB_AVAILABLE", False)
    with patch.dict(os.environ, {TELEGRAM_BOT_TOKEN_ENV: "1234567890:AAFakeTokenValue"}, clear=False):
        checks = run_cloud_readiness_checks(
            reports_dir=tmp_path / "reports",
            storage_dir=tmp_path / "storage",
            check_token=True,
        )
        summary = format_cloud_readiness_telegram_summary(checks)

    assert "1234567890:AAFakeTokenValue" not in summary
    assert REDACTED_TELEGRAM_TOKEN in summary
    assert "☁️" in summary


def test_run_cloud_readiness_check_returns_nonzero_on_missing_tradingview(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _fail(name: str):
        if name == "tradingview_screener":
            raise ImportError("missing")
        return __import__(name)

    monkeypatch.setattr("core.cloud_readiness.importlib.import_module", _fail)

    exit_code = run_cloud_readiness_check(
        reports_dir=tmp_path / "reports",
        storage_dir=tmp_path / "storage",
        check_token=False,
    )
    captured = capsys.readouterr().out

    assert exit_code == 1
    assert "[ERROR] tradingview_screener" in captured
    assert "NOT READY" in captured


def test_format_help_mentions_cloud_readiness() -> None:
    help_text = format_help()
    assert "Cloud Run" in help_text or "السيرفر" in help_text
