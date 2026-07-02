"""Tests for Chrome remote debugging launcher."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.chrome_launcher import (
    CHROME_NOT_FOUND_ERROR,
    DEFAULT_CDP_URL,
    ChromeRemoteDebugLauncher,
)


@pytest.fixture
def launcher() -> ChromeRemoteDebugLauncher:
    return ChromeRemoteDebugLauncher()


def test_is_cdp_available_returns_true_on_success(
    launcher: ChromeRemoteDebugLauncher,
) -> None:
    response = MagicMock()
    response.status = 200
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch("core.chrome_launcher.urlopen", return_value=response):
        assert launcher.is_cdp_available(DEFAULT_CDP_URL) is True


def test_launch_returns_already_running_when_cdp_available(
    launcher: ChromeRemoteDebugLauncher,
) -> None:
    with patch.object(launcher, "is_cdp_available", return_value=True):
        result = launcher.launch_chrome_remote_debugging()

    assert result.success is True
    assert result.already_running is True
    assert result.cdp_url == DEFAULT_CDP_URL


def test_launch_returns_clear_error_when_chrome_missing(
    launcher: ChromeRemoteDebugLauncher,
) -> None:
    with (
        patch.object(launcher, "is_cdp_available", return_value=False),
        patch.object(launcher, "find_chrome_executable", return_value=None),
    ):
        result = launcher.launch_chrome_remote_debugging()

    assert result.success is False
    assert result.errors == [CHROME_NOT_FOUND_ERROR]


def test_find_chrome_executable_uses_chrome_path_env(
    launcher: ChromeRemoteDebugLauncher,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chrome_path = tmp_path / "custom_chrome.exe"
    chrome_path.write_text("stub", encoding="utf-8")
    monkeypatch.setenv("CHROME_PATH", str(chrome_path))

    assert launcher.find_chrome_executable() == chrome_path


def test_launch_calls_subprocess_with_expected_args(
    launcher: ChromeRemoteDebugLauncher,
    tmp_path: Path,
) -> None:
    chrome_path = tmp_path / "chrome.exe"
    chrome_path.write_text("stub", encoding="utf-8")
    profile_dir = tmp_path / "profile"

    popen_mock = MagicMock()
    availability = iter([False, True])

    with (
        patch.object(
            launcher,
            "is_cdp_available",
            side_effect=lambda _url: next(availability),
        ),
        patch.object(launcher, "find_chrome_executable", return_value=chrome_path),
        patch("core.chrome_launcher.subprocess.Popen", popen_mock),
        patch("core.chrome_launcher.time.sleep"),
    ):
        result = launcher.launch_chrome_remote_debugging(
            cdp_port=9222,
            user_data_dir=profile_dir,
        )

    assert result.success is True
    assert result.already_running is False
    assert result.chrome_path == chrome_path
    assert result.user_data_dir == profile_dir
    popen_mock.assert_called_once_with(
        [
            str(chrome_path),
            "--remote-debugging-port=9222",
            f"--user-data-dir={profile_dir}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
