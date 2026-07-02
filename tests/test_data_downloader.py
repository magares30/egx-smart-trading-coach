"""Tests for the safe market data downloader."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from core.data_downloader import (
    DownloadProvider,
    SafeDataDownloader,
)


@pytest.fixture
def downloader() -> SafeDataDownloader:
    return SafeDataDownloader()


def test_direct_url_rejects_unsupported_extension(
    downloader: SafeDataDownloader, tmp_path: Path
) -> None:
    result = downloader.download_direct_url(
        "https://example.com/file.html", tmp_path
    )

    assert result.success is False
    assert result.provider == DownloadProvider.DIRECT_URL
    assert any("Unsupported URL extension" in error for error in result.errors)


def test_direct_url_saves_csv(
    downloader: SafeDataDownloader, tmp_path: Path
) -> None:
    mock_response = MagicMock()
    mock_response.content = b"date,symbol,open,high,low,close,volume\n"
    mock_response.raise_for_status = MagicMock()

    with patch("core.data_downloader.requests.get", return_value=mock_response):
        result = downloader.download_direct_url(
            "https://example.com/market.csv", tmp_path
        )

    assert result.success is True
    assert len(result.saved_files) == 1
    assert result.saved_files[0].name == "market.csv"
    assert result.saved_files[0].read_bytes() == mock_response.content


def test_direct_url_handles_network_error(
    downloader: SafeDataDownloader, tmp_path: Path
) -> None:
    with patch(
        "core.data_downloader.requests.get",
        side_effect=requests.RequestException("network down"),
    ):
        result = downloader.download_direct_url(
            "https://example.com/market.csv", tmp_path
        )

    assert result.success is False
    assert any("Download failed" in error for error in result.errors)


def test_eodhd_missing_api_key_returns_error(
    downloader: SafeDataDownloader, tmp_path: Path
) -> None:
    result = downloader.download_eodhd_symbol("COMI", "", tmp_path)

    assert result.success is False
    assert result.provider == DownloadProvider.EODHD
    assert any("API key is required" in error for error in result.errors)


def test_eodhd_saves_csv(
    downloader: SafeDataDownloader, tmp_path: Path
) -> None:
    mock_response = MagicMock()
    mock_response.content = b"Date,Open,High,Low,Close,Volume\n"
    mock_response.raise_for_status = MagicMock()

    with patch("core.data_downloader.requests.get", return_value=mock_response) as mock_get:
        result = downloader.download_eodhd_symbol("COMI", "test-key", tmp_path)

    assert result.success is True
    assert result.saved_files[0].name == "eodhd_COMI.csv"
    assert result.saved_files[0].read_bytes() == mock_response.content
    mock_get.assert_called_once()
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["params"]["api_token"] == "test-key"
    assert call_kwargs["params"]["fmt"] == "csv"


def test_kaggle_missing_config_returns_clear_error(
    downloader: SafeDataDownloader, tmp_path: Path
) -> None:
    with patch("core.data_downloader.KAGGLE_CONFIG_PATH") as mock_config:
        mock_config.exists.return_value = False
        result = downloader.download_kaggle_dataset("owner/dataset", tmp_path)

    assert result.success is False
    assert any("credentials not configured" in error for error in result.errors)


def test_kaggle_missing_cli_and_package_returns_clear_error(
    downloader: SafeDataDownloader, tmp_path: Path
) -> None:
    with (
        patch("core.data_downloader.KAGGLE_CONFIG_PATH") as mock_config,
        patch("core.data_downloader.shutil.which", return_value=None),
        patch.object(
            downloader,
            "_download_kaggle_with_package",
            return_value=(False, "Kaggle CLI or kaggle package is not installed."),
        ),
    ):
        mock_config.exists.return_value = True
        result = downloader.download_kaggle_dataset("owner/dataset", tmp_path)

    assert result.success is False
    assert any("not installed" in error for error in result.errors)
