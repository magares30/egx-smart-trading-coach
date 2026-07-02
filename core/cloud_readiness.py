"""Cloud Run readiness checks before enabling market-hours auto refresh."""

from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from config import settings
from core.cloud_report_runner import (
    REDACTED_TELEGRAM_TOKEN,
    build_default_report_command,
    find_latest_report_json,
    sanitize_log_text,
)
from core.talib_technical import TALIB_NOT_INSTALLED_WARNING, is_talib_engine_available
from core.telegram_bot import TELEGRAM_BOT_TOKEN_ENV

READINESS_HEADER = "=== EGX Cloud Run Readiness ==="
EXPECTED_CLOUD_MODULES = (
    "core.cloud_report_runner",
    "core.cloud_readiness",
    "core.telegram_bot",
    "core.talib_technical",
)


class CheckStatus(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: CheckStatus
    detail: str


def check_expected_code_modules() -> ReadinessCheck:
    missing: list[str] = []
    for module_name in EXPECTED_CLOUD_MODULES:
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(module_name)

    if missing:
        return ReadinessCheck(
            "expected_code",
            CheckStatus.ERROR,
            f"missing modules: {', '.join(missing)}",
        )
    return ReadinessCheck(
        "expected_code",
        CheckStatus.OK,
        "cloud report modules importable",
    )


def check_tradingview_screener_import() -> ReadinessCheck:
    try:
        importlib.import_module("tradingview_screener")
    except ImportError as exc:
        return ReadinessCheck(
            "tradingview_screener",
            CheckStatus.ERROR,
            f"import failed: {exc}",
        )
    return ReadinessCheck(
        "tradingview_screener",
        CheckStatus.OK,
        "import succeeded",
    )


def check_talib_optional() -> ReadinessCheck:
    if is_talib_engine_available():
        return ReadinessCheck("talib", CheckStatus.OK, "TA-Lib available")
    return ReadinessCheck("talib", CheckStatus.WARNING, TALIB_NOT_INSTALLED_WARNING)


def check_directory(path: Path, name: str) -> ReadinessCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ReadinessCheck(name, CheckStatus.ERROR, f"cannot create {path}: {exc}")

    if not path.is_dir():
        return ReadinessCheck(name, CheckStatus.ERROR, f"{path} is not a directory")
    return ReadinessCheck(name, CheckStatus.OK, f"{path} ready")


def check_telegram_bot_token() -> ReadinessCheck:
    token = os.environ.get(TELEGRAM_BOT_TOKEN_ENV)
    if not token:
        return ReadinessCheck(
            "telegram_bot_token",
            CheckStatus.ERROR,
            f"{TELEGRAM_BOT_TOKEN_ENV} is not set",
        )
    return ReadinessCheck(
        "telegram_bot_token",
        CheckStatus.OK,
        f"{TELEGRAM_BOT_TOKEN_ENV}={REDACTED_TELEGRAM_TOKEN}",
    )


def check_report_command(project_root: Path | None = None) -> ReadinessCheck:
    root = project_root or settings.PROJECT_ROOT
    command = build_default_report_command(root)
    if len(command) < 3:
        return ReadinessCheck(
            "report_command",
            CheckStatus.ERROR,
            "default report command is incomplete",
        )

    main_path = Path(command[1])
    if not main_path.is_file():
        return ReadinessCheck(
            "report_command",
            CheckStatus.ERROR,
            f"missing main entrypoint: {main_path}",
        )

    argv = command[2:]
    if "tradingview" not in argv:
        return ReadinessCheck(
            "report_command",
            CheckStatus.ERROR,
            "default report command must use --data-provider tradingview",
        )

    try:
        from main import parse_args

        parse_args(argv)
    except SystemExit:
        return ReadinessCheck(
            "report_command",
            CheckStatus.ERROR,
            "default report CLI args rejected by parser",
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        return ReadinessCheck(
            "report_command",
            CheckStatus.ERROR,
            f"default report CLI validation failed: {exc}",
        )

    safe_command = sanitize_log_text(" ".join(command))
    return ReadinessCheck(
        "report_command",
        CheckStatus.OK,
        f"CLI args validated (dry-run): {safe_command}",
    )


def check_latest_report_json(reports_dir: Path | None = None) -> ReadinessCheck:
    latest = find_latest_report_json(reports_dir)
    if latest is None:
        return ReadinessCheck(
            "latest_report",
            CheckStatus.WARNING,
            "no saved report yet (use Telegram refresh button)",
        )

    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ReadinessCheck(
            "latest_report",
            CheckStatus.ERROR,
            f"unreadable report JSON: {exc}",
        )

    if not isinstance(payload, dict):
        return ReadinessCheck(
            "latest_report",
            CheckStatus.ERROR,
            "report JSON root must be an object",
        )
    return ReadinessCheck(
        "latest_report",
        CheckStatus.OK,
        f"readable: {latest.name}",
    )


def run_cloud_readiness_checks(
    *,
    project_root: Path | None = None,
    reports_dir: Path | None = None,
    storage_dir: Path | None = None,
    check_token: bool = True,
) -> list[ReadinessCheck]:
    """Run all Cloud Run readiness checks without executing a full report."""
    root = project_root or settings.PROJECT_ROOT
    reports = reports_dir or settings.REPORTS_DIR
    storage = storage_dir or settings.STORAGE_DIR

    checks = [
        check_expected_code_modules(),
        check_tradingview_screener_import(),
        check_talib_optional(),
        check_directory(reports, "reports_dir"),
        check_directory(storage, "storage_dir"),
    ]
    if check_token:
        checks.append(check_telegram_bot_token())
    checks.extend(
        [
            check_report_command(root),
            check_latest_report_json(reports),
        ]
    )
    return checks


def readiness_exit_code(checks: list[ReadinessCheck]) -> int:
    if any(check.status == CheckStatus.ERROR for check in checks):
        return 1
    return 0


def summarize_readiness_result(checks: list[ReadinessCheck]) -> str:
    errors = sum(1 for check in checks if check.status == CheckStatus.ERROR)
    warnings = sum(1 for check in checks if check.status == CheckStatus.WARNING)
    if errors:
        return f"RESULT: NOT READY ({errors} error(s), {warnings} warning(s))"
    if warnings:
        return f"RESULT: READY WITH WARNINGS ({warnings} warning(s))"
    return "RESULT: READY"


def format_readiness_report(checks: list[ReadinessCheck]) -> str:
    lines = [READINESS_HEADER]
    for check in checks:
        lines.append(f"[{check.status.value}] {check.name}: {check.detail}")
    lines.append(summarize_readiness_result(checks))
    return sanitize_log_text("\n".join(lines))


def format_cloud_readiness_telegram_summary(checks: list[ReadinessCheck]) -> str:
    """Safe short readiness summary for Telegram help/admin text."""
    icons = {
        CheckStatus.OK: "✅",
        CheckStatus.WARNING: "⚠️",
        CheckStatus.ERROR: "❌",
    }
    lines = ["☁️ جاهزية Cloud Run:", ""]
    for check in checks:
        if check.name == "telegram_bot_token":
            detail = f"{TELEGRAM_BOT_TOKEN_ENV}={REDACTED_TELEGRAM_TOKEN}"
            if check.status == CheckStatus.ERROR:
                detail = f"{TELEGRAM_BOT_TOKEN_ENV} missing"
        elif check.name == "latest_report" and check.status == CheckStatus.WARNING:
            detail = "no saved report yet"
        elif check.name == "talib" and check.status == CheckStatus.WARNING:
            detail = "TA-Lib optional / unavailable"
        else:
            detail = check.status.value.lower()

        lines.append(f"{icons[check.status]} {check.name}: {detail}")

    lines.append("")
    lines.append(summarize_readiness_result(checks))
    return sanitize_log_text("\n".join(lines))


def run_cloud_readiness_check(
    *,
    project_root: Path | None = None,
    reports_dir: Path | None = None,
    storage_dir: Path | None = None,
    check_token: bool = True,
) -> int:
    """Print readiness report and return process exit code."""
    checks = run_cloud_readiness_checks(
        project_root=project_root,
        reports_dir=reports_dir,
        storage_dir=storage_dir,
        check_token=check_token,
    )
    print(format_readiness_report(checks))
    return readiness_exit_code(checks)
