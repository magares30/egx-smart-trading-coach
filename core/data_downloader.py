"""Safe market data download helpers for explicit allowed sources only."""

from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from enum import Enum
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from pydantic import BaseModel, Field

ALLOWED_DIRECT_EXTENSIONS = {".csv", ".xlsx", ".zip"}
IMPORTABLE_EXTENSIONS = {".csv", ".xlsx"}
REQUEST_TIMEOUT_SECONDS = 30
KAGGLE_CONFIG_PATH = Path.home() / ".kaggle" / "kaggle.json"
EODHD_EOD_URL = "https://eodhd.com/api/eod/{ticker}"


class DownloadProvider(str, Enum):
    DIRECT_URL = "DIRECT_URL"
    KAGGLE = "KAGGLE"
    EODHD = "EODHD"


class DownloadResult(BaseModel):
    success: bool
    provider: DownloadProvider
    saved_files: list[Path] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SafeDataDownloader:
    """Download market data from explicit safe sources only."""

    def _build_result(
        self,
        provider: DownloadProvider,
        *,
        success: bool,
        saved_files: list[Path] | None = None,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> DownloadResult:
        return DownloadResult(
            success=success,
            provider=provider,
            saved_files=saved_files or [],
            errors=errors or [],
            warnings=warnings or [],
        )

    def _url_extension(self, url: str) -> str:
        path = unquote(urlparse(url).path)
        return Path(path).suffix.lower()

    def _filename_from_url(self, url: str) -> str:
        path = unquote(urlparse(url).path)
        name = Path(path).name
        if name:
            return name
        return f"download{self._url_extension(url)}"

    def _extract_zip_files(self, zip_path: Path, downloads_dir: Path) -> list[Path]:
        extract_dir = downloads_dir / f"{zip_path.stem}_extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)
        return sorted(
            path
            for path in extract_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in IMPORTABLE_EXTENSIONS
        )

    def download_direct_url(self, url: str, downloads_dir: Path) -> DownloadResult:
        """Download a direct CSV, XLSX, or ZIP file URL."""
        extension = self._url_extension(url)
        if extension not in ALLOWED_DIRECT_EXTENSIONS:
            return self._build_result(
                DownloadProvider.DIRECT_URL,
                success=False,
                errors=[
                    "Unsupported URL extension. Only .csv, .xlsx, and .zip are allowed."
                ],
            )

        downloads_dir.mkdir(parents=True, exist_ok=True)
        save_path = downloads_dir / self._filename_from_url(url)

        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            save_path.write_bytes(response.content)
        except requests.RequestException as exc:
            return self._build_result(
                DownloadProvider.DIRECT_URL,
                success=False,
                errors=[f"Download failed: {exc}"],
            )

        saved_files = [save_path]
        warnings: list[str] = []
        if extension == ".zip":
            try:
                extracted = self._extract_zip_files(save_path, downloads_dir)
            except zipfile.BadZipFile:
                return self._build_result(
                    DownloadProvider.DIRECT_URL,
                    success=False,
                    errors=["Downloaded file is not a valid ZIP archive."],
                )
            saved_files.extend(extracted)
            if not extracted:
                warnings.append("ZIP archive contained no CSV/XLSX files.")

        return self._build_result(
            DownloadProvider.DIRECT_URL,
            success=True,
            saved_files=saved_files,
            warnings=warnings,
        )

    def _kaggle_configured(self) -> bool:
        return KAGGLE_CONFIG_PATH.exists()

    def _collect_dataset_files(self, target_dir: Path) -> list[Path]:
        return sorted(path for path in target_dir.rglob("*") if path.is_file())

    def _download_kaggle_with_cli(
        self, dataset: str, target_dir: Path
    ) -> tuple[bool, str]:
        command = [
            "kaggle",
            "datasets",
            "download",
            "-d",
            dataset,
            "-p",
            str(target_dir),
            "--unzip",
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "Kaggle CLI failed"
            return False, message
        return True, ""

    def _download_kaggle_with_package(
        self, dataset: str, target_dir: Path
    ) -> tuple[bool, str]:
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
        except ImportError:
            return False, "Kaggle CLI or kaggle package is not installed."

        try:
            api = KaggleApi()
            api.authenticate()
            api.dataset_download_files(dataset, path=str(target_dir), unzip=True)
        except Exception as exc:  # noqa: BLE001 — surface provider errors
            return False, f"Kaggle download failed: {exc}"
        return True, ""

    def download_kaggle_dataset(
        self, dataset: str, downloads_dir: Path
    ) -> DownloadResult:
        """Download a Kaggle dataset when user credentials are configured."""
        if not self._kaggle_configured():
            return self._build_result(
                DownloadProvider.KAGGLE,
                success=False,
                errors=[
                    "Kaggle credentials not configured. "
                    "Place kaggle.json in ~/.kaggle/ before downloading."
                ],
            )

        safe_name = re.sub(r"[^\w\-]+", "_", dataset.replace("/", "_"))
        target_dir = downloads_dir / f"kaggle_{safe_name}"
        target_dir.mkdir(parents=True, exist_ok=True)

        if shutil.which("kaggle"):
            ok, message = self._download_kaggle_with_cli(dataset, target_dir)
        else:
            ok, message = self._download_kaggle_with_package(dataset, target_dir)

        if not ok:
            return self._build_result(
                DownloadProvider.KAGGLE,
                success=False,
                errors=[message],
            )

        saved_files = self._collect_dataset_files(target_dir)
        warnings: list[str] = []
        if not any(path.suffix.lower() in IMPORTABLE_EXTENSIONS for path in saved_files):
            warnings.append("No CSV/XLSX files found in downloaded Kaggle dataset.")

        return self._build_result(
            DownloadProvider.KAGGLE,
            success=True,
            saved_files=saved_files,
            warnings=warnings,
        )

    def download_eodhd_symbol(
        self,
        symbol: str,
        api_key: str,
        downloads_dir: Path,
    ) -> DownloadResult:
        """Download EOD data from the documented EODHD API endpoint."""
        if not api_key:
            return self._build_result(
                DownloadProvider.EODHD,
                success=False,
                errors=["EODHD API key is required. Pass --eodhd-api-key YOUR_KEY."],
            )

        ticker = symbol if "." in symbol else f"{symbol}.EGX"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        save_path = downloads_dir / f"eodhd_{symbol}.csv"
        url = EODHD_EOD_URL.format(ticker=ticker)
        params = {"api_token": api_key, "fmt": "csv"}

        try:
            response = requests.get(
                url, params=params, timeout=REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            save_path.write_bytes(response.content)
        except requests.RequestException as exc:
            return self._build_result(
                DownloadProvider.EODHD,
                success=False,
                errors=[f"EODHD download failed: {exc}"],
            )

        return self._build_result(
            DownloadProvider.EODHD,
            success=True,
            saved_files=[save_path],
        )
