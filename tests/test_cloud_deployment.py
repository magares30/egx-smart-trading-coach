"""Tests for Cloud Run health server and deployment prep files."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from core.health_server import (
    HEALTH_OK_BODY,
    health_response_body,
    resolve_health_port,
)
from core.telegram_bot import (
    NO_REPORT_MESSAGE,
    TELEGRAM_BOT_TOKEN_ENV,
    format_daily_overview,
    validate_telegram_bot_startup,
)


def test_health_response_body_returns_ok_for_health_paths() -> None:
    assert health_response_body("/") == HEALTH_OK_BODY
    assert health_response_body("/health") == HEALTH_OK_BODY
    assert health_response_body("/health?check=1") == HEALTH_OK_BODY


def test_health_response_body_returns_none_for_unknown_path() -> None:
    assert health_response_body("/ready") is None


def test_resolve_health_port_uses_port_env() -> None:
    with patch.dict(os.environ, {"PORT": "9090"}, clear=False):
        assert resolve_health_port() == 9090


def test_resolve_health_port_defaults_to_8080() -> None:
    env = os.environ.copy()
    env.pop("PORT", None)
    with patch.dict(os.environ, env, clear=True):
        assert resolve_health_port() == 8080


def test_validate_telegram_bot_startup_missing_token() -> None:
    with patch.dict(os.environ, {}, clear=True):
        error = validate_telegram_bot_startup()

    assert error == f"{TELEGRAM_BOT_TOKEN_ENV} environment variable is not set."


def test_validate_telegram_bot_startup_with_token() -> None:
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "TEST_PLACEHOLDER_TOKEN"}, clear=False):
        assert validate_telegram_bot_startup() is None


def test_dockerignore_excludes_env_and_secret_patterns() -> None:
    project_root = Path(__file__).resolve().parent.parent
    dockerignore = (project_root / ".dockerignore").read_text(encoding="utf-8")

    assert ".env" in dockerignore
    assert "credentials.json" in dockerignore
    assert "secrets/" in dockerignore


def test_dockerfile_uses_telegram_bot_cmd() -> None:
    project_root = Path(__file__).resolve().parent.parent
    dockerfile = (project_root / "Dockerfile").read_text(encoding="utf-8")

    assert 'CMD ["python", "main.py", "--telegram-bot"]' in dockerfile
    assert "EXPOSE 8080" in dockerfile
    assert "mkdir -p data/reports storage" in dockerfile


def test_deployment_docs_use_placeholders_not_real_tokens() -> None:
    project_root = Path(__file__).resolve().parent.parent
    deployment_text = (project_root / "DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "YOUR_GCP_PROJECT_ID" in deployment_text
    assert "YOUR_TELEGRAM_BOT_TOKEN" in deployment_text
    assert "1234567890:AA" not in deployment_text


def test_missing_report_is_handled_gracefully() -> None:
    assert format_daily_overview(None) == NO_REPORT_MESSAGE


def test_deployment_files_exist() -> None:
    project_root = Path(__file__).resolve().parent.parent

    assert (project_root / "Dockerfile").is_file()
    assert (project_root / ".dockerignore").is_file()
    assert (project_root / "DEPLOYMENT.md").is_file()


def test_requirements_include_tradingview_screener() -> None:
    project_root = Path(__file__).resolve().parent.parent
    requirements_text = (project_root / "requirements.txt").read_text(encoding="utf-8")

    assert "tradingview-screener" in requirements_text


def test_talib_is_optional_for_cloud_run() -> None:
    project_root = Path(__file__).resolve().parent.parent
    requirements_text = (project_root / "requirements.txt").read_text(encoding="utf-8").lower()
    talib_source = (project_root / "core" / "talib_technical.py").read_text(encoding="utf-8")

    assert "ta-lib" not in requirements_text
    assert "talib" not in requirements_text
    assert "except ImportError" in talib_source
    assert "TALIB_AVAILABLE" in talib_source
    assert "TALIB_NOT_INSTALLED_WARNING" in talib_source
