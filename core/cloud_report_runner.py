"""On-demand EGX daily report runner for Cloud Run / Telegram."""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

DEFAULT_REPORT_TIMEOUT_SECONDS = 300
OUTPUT_TAIL_MAX_CHARS = 4000
REPORT_ALREADY_RUNNING_MESSAGE = "في تقرير بيتحدّث دلوقتي، استنى لما يخلص."
REPORT_STARTING_MESSAGE = "تمام، بحدّث التقرير دلوقتي من السيرفر... استنى دقيقة."
REPORT_SUCCESS_FOOTER = "التقرير اتحدّث من السيرفر ✅"
REPORT_SUCCESS_CLOSED_DIGEST_FOOTER = (
    "التقرير اتحدث كـ Closed Market Digest من آخر بيانات متاحة ✅"
)
REPORT_TIMEOUT_MESSAGE = "التقرير خد وقت زيادة ومكملش. جرّبه تاني بعد شوية."
REPORT_FAILURE_PREFIX = "التقرير فشل من السيرفر. السبب اتسجل في اللوج بشكل آمن."
REDACTED_TELEGRAM_TOKEN = "[REDACTED_TELEGRAM_TOKEN]"
CLOUD_REPORT_FAILURE_MARKER = "CLOUD_REPORT_FAILURE_DETAILS"

_REPORT_FILENAME_RE = re.compile(r"egx_daily_report_(\d{8})_(\d{6})\.json$")
_BOT_TOKEN_PATTERN = re.compile(r"bot\d+:[A-Za-z0-9_-]+", re.IGNORECASE)
_ENV_TOKEN_PATTERN = re.compile(
    r"TELEGRAM_BOT_TOKEN\s*=\s*[^\s\"']+",
    re.IGNORECASE,
)
_API_BOT_URL_PATTERN = re.compile(
    r"https?://api\.telegram\.org/bot[^/\s\"']+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReportRunResult:
    success: bool
    returncode: int | None
    stdout_tail: str
    stderr_tail: str
    error: str | None
    latest_report_path: str | None


class ReportRunLock:
    """In-process lock to prevent concurrent report runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = False

    def try_acquire(self) -> bool:
        with self._lock:
            if self._active:
                return False
            self._active = True
            return True

    def release(self) -> None:
        with self._lock:
            self._active = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active


report_run_lock = ReportRunLock()


def build_default_report_command(project_root: Path | None = None) -> list[str]:
    """Build the default Cloud Run report CLI command."""
    root = project_root or settings.PROJECT_ROOT
    return [
        sys.executable,
        str(root / "main.py"),
        "--egx-workflow",
        "report",
        "--data-provider",
        "tradingview",
        "--scanner-universe",
        "full-market",
        "--top-candidates",
        "10",
        "--min-score",
        "75",
    ]


def ensure_report_runtime_directories() -> None:
    """Create runtime directories used by report generation."""
    for path in (
        settings.REPORTS_DIR,
        settings.REAL_DATA_DIR,
        settings.STORAGE_DIR,
        settings.LIVE_HISTORY_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def find_latest_report_json(reports_dir: Path | None = None) -> Path | None:
    """Return the newest saved daily report JSON path, if any."""
    target_dir = reports_dir or settings.REPORTS_DIR
    if not target_dir.is_dir():
        return None

    candidates = [
        path
        for path in target_dir.glob("egx_daily_report_*.json")
        if path.is_file()
    ]
    if not candidates:
        return None

    parseable = [path for path in candidates if _REPORT_FILENAME_RE.match(path.name)]
    if parseable:
        return max(parseable, key=_report_filename_sort_key)
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _report_filename_sort_key(path: Path) -> tuple[str, str, str]:
    match = _REPORT_FILENAME_RE.match(path.name)
    if match is None:
        return ("", "", path.name)
    return (match.group(1), match.group(2), path.name)


def _tail_output(text: str | None, *, max_chars: int = OUTPUT_TAIL_MAX_CHARS) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[-max_chars:]


def sanitize_log_text(text: str) -> str:
    """Redact Telegram tokens and sensitive API URLs from log text."""
    if not text:
        return ""

    sanitized = text
    sanitized = _API_BOT_URL_PATTERN.sub(
        f"https://api.telegram.org/bot{REDACTED_TELEGRAM_TOKEN}",
        sanitized,
    )
    sanitized = _BOT_TOKEN_PATTERN.sub(REDACTED_TELEGRAM_TOKEN, sanitized)
    sanitized = _ENV_TOKEN_PATTERN.sub(
        f"TELEGRAM_BOT_TOKEN={REDACTED_TELEGRAM_TOKEN}",
        sanitized,
    )
    return sanitized


def _prepare_output_tail(text: str | None) -> str:
    return sanitize_log_text(_tail_output(text))


def _sanitize_command_for_log(command: list[str]) -> str:
    return sanitize_log_text(" ".join(command))


def build_cloud_report_failure_log_message(
    *,
    command: list[str],
    returncode: int | None,
    stdout: str | None,
    stderr: str | None,
) -> str:
    """Build one sanitized multiline failure log block for Cloud Run."""
    safe_command = _sanitize_command_for_log(command)
    safe_stdout = _prepare_output_tail(stdout) or "<empty>"
    safe_stderr = _prepare_output_tail(stderr) or "<empty>"
    if returncode is None:
        return_code = "timeout"
    else:
        return_code = str(returncode)

    return (
        f"{CLOUD_REPORT_FAILURE_MARKER}\n"
        f"command={safe_command}\n"
        f"return_code={return_code}\n"
        f"stdout_tail=\n{safe_stdout}\n"
        f"stderr_tail=\n{safe_stderr}"
    )


def _log_report_failure_diagnostics(
    *,
    command: list[str],
    returncode: int | None,
    stdout: str | None,
    stderr: str | None,
) -> None:
    logger.warning(
        build_cloud_report_failure_log_message(
            command=command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )
    )


def run_report_once(
    *,
    timeout_seconds: int = DEFAULT_REPORT_TIMEOUT_SECONDS,
    project_root: Path | None = None,
    command: list[str] | None = None,
) -> ReportRunResult:
    """Run one daily report command and return a structured result."""
    ensure_report_runtime_directories()
    report_command = command or build_default_report_command(project_root)
    cwd = str(project_root or settings.PROJECT_ROOT)

    logger.info("Starting cloud report command.")
    try:
        completed = subprocess.run(
            report_command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_raw = exc.stdout if isinstance(exc.stdout, str) else None
        stderr_raw = exc.stderr if isinstance(exc.stderr, str) else None
        _log_report_failure_diagnostics(
            command=report_command,
            returncode=None,
            stdout=stdout_raw,
            stderr=stderr_raw,
        )
        stdout_tail = _prepare_output_tail(stdout_raw)
        stderr_tail = _prepare_output_tail(stderr_raw)
        return ReportRunResult(
            success=False,
            returncode=None,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            error="timeout",
            latest_report_path=None,
        )
    except OSError as exc:
        logger.exception("Cloud report command failed to start.")
        return ReportRunResult(
            success=False,
            returncode=None,
            stdout_tail="",
            stderr_tail="",
            error=str(exc),
            latest_report_path=None,
        )

    latest_path = find_latest_report_json(settings.REPORTS_DIR)
    latest_report_path = str(latest_path) if latest_path is not None else None
    stdout_tail = _prepare_output_tail(completed.stdout)
    stderr_tail = _prepare_output_tail(completed.stderr)
    success = completed.returncode == 0 and latest_report_path is not None

    if success:
        logger.info("Cloud report command completed successfully.")
    else:
        _log_report_failure_diagnostics(
            command=report_command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    error = None
    if not success:
        if completed.returncode != 0:
            error = f"returncode={completed.returncode}"
        elif latest_report_path is None:
            error = "missing_report_json"

    return ReportRunResult(
        success=success,
        returncode=completed.returncode,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        error=error,
        latest_report_path=latest_report_path,
    )


def format_report_run_telegram_message(
    result: ReportRunResult,
    *,
    overview_text: str | None = None,
    closed_market_digest: dict[str, object] | None = None,
) -> str:
    """Build the Telegram reply after a cloud report run."""
    if result.error == "timeout":
        return REPORT_TIMEOUT_MESSAGE

    if result.success:
        overview = overview_text or "تم حفظ التقرير."
        footer = (
            REPORT_SUCCESS_CLOSED_DIGEST_FOOTER
            if closed_market_digest and closed_market_digest.get("enabled")
            else REPORT_SUCCESS_FOOTER
        )
        return f"{overview}\n\n{footer}"

    lines = [REPORT_FAILURE_PREFIX]
    hint_source = result.stderr_tail or result.stdout_tail
    if hint_source:
        first_line = next(
            (line.strip() for line in hint_source.splitlines() if line.strip()),
            "",
        )
        if (
            first_line
            and REDACTED_TELEGRAM_TOKEN not in first_line
            and "TELEGRAM_BOT_TOKEN" not in first_line.upper()
        ):
            lines.append(first_line[:160])
    return "\n".join(lines).strip()
