"""Launch or detect Chrome with remote debugging for EGX live reads."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from pydantic import BaseModel, Field

DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_CDP_PORT = 9222
DEFAULT_CHROME_PROFILE_DIR = Path("C:/egx_chrome_profile")
CDP_STARTUP_TIMEOUT_SECONDS = 10
CDP_POLL_INTERVAL_SECONDS = 0.5
CHROME_NOT_FOUND_ERROR = (
    "Could not find Chrome. Set CHROME_PATH or start Chrome manually with "
    "--remote-debugging-port=9222."
)
CHROME_EXECUTABLE_CANDIDATES: tuple[Path, ...] = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
)


class ChromeLaunchResult(BaseModel):
    success: bool
    cdp_url: str = DEFAULT_CDP_URL
    chrome_path: Path | None = None
    user_data_dir: Path | None = None
    already_running: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ChromeRemoteDebugLauncher:
    """Ensure Chrome remote debugging is available for attached reads."""

    def is_cdp_available(self, cdp_url: str = DEFAULT_CDP_URL) -> bool:
        """Return True when Chrome DevTools Protocol responds on the given URL."""
        version_url = f"{cdp_url.rstrip('/')}/json/version"
        try:
            with urlopen(version_url, timeout=2) as response:
                return response.status == 200
        except (OSError, URLError, ValueError):
            return False

    def find_chrome_executable(self) -> Path | None:
        """Find a local Chrome executable on Windows or via CHROME_PATH."""
        env_path = os.environ.get("CHROME_PATH")
        if env_path:
            candidate = Path(env_path)
            if candidate.exists():
                return candidate

        for candidate in CHROME_EXECUTABLE_CANDIDATES:
            if candidate.exists():
                return candidate
        return None

    def launch_chrome_remote_debugging(
        self,
        cdp_port: int = DEFAULT_CDP_PORT,
        user_data_dir: Path | None = None,
    ) -> ChromeLaunchResult:
        """Launch Chrome with remote debugging or reuse an existing CDP session."""
        cdp_url = f"http://127.0.0.1:{cdp_port}"
        if self.is_cdp_available(cdp_url):
            return ChromeLaunchResult(
                success=True,
                cdp_url=cdp_url,
                already_running=True,
            )

        chrome_path = self.find_chrome_executable()
        if chrome_path is None:
            return ChromeLaunchResult(
                success=False,
                cdp_url=cdp_url,
                errors=[CHROME_NOT_FOUND_ERROR],
            )

        profile_dir = user_data_dir or DEFAULT_CHROME_PROFILE_DIR
        profile_dir.mkdir(parents=True, exist_ok=True)

        launch_args = [
            str(chrome_path),
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={profile_dir}",
        ]
        subprocess.Popen(
            launch_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.monotonic() + CDP_STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self.is_cdp_available(cdp_url):
                return ChromeLaunchResult(
                    success=True,
                    cdp_url=cdp_url,
                    chrome_path=chrome_path,
                    user_data_dir=profile_dir,
                    already_running=False,
                )
            time.sleep(CDP_POLL_INTERVAL_SECONDS)

        return ChromeLaunchResult(
            success=False,
            cdp_url=cdp_url,
            chrome_path=chrome_path,
            user_data_dir=profile_dir,
            errors=[
                "Chrome launched but remote debugging did not become available "
                f"within {CDP_STARTUP_TIMEOUT_SECONDS} seconds."
            ],
        )
