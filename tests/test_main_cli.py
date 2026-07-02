"""Tests for main CLI argument parsing and scenario resolution."""

import pytest

from pathlib import Path

import pytest

from config import settings
from core.market_data import CsvMarketDataProvider, MarketDataProvider
from main import parse_args, resolve_scenario_path


def test_parse_args_default() -> None:
    args = parse_args([])

    assert args.scenario == "default"
    assert args.data_source == "demo"
    assert args.real_csv is None
    assert args.validate_real_csv is False
    assert args.normalize_real_csv is False
    assert args.normalized_output is None
    assert args.demo_trade is False
    assert args.auto_paper_trade is False
    assert args.reset_paper_state is False
    assert args.monitor_paper_trades is False
    assert args.force_eod_exit is False
    assert args.backtest is False


def test_parse_args_with_demo_trade() -> None:
    args = parse_args(["--scenario", "bull", "--demo-trade"])

    assert args.scenario == "bull"
    assert args.demo_trade is True


def test_parse_args_auto_paper_trade_defaults_false() -> None:
    args = parse_args([])

    assert args.auto_paper_trade is False


def test_parse_args_auto_paper_trade_sets_true() -> None:
    args = parse_args(["--auto-paper-trade"])

    assert args.auto_paper_trade is True


def test_parse_args_reset_paper_state_defaults_false() -> None:
    args = parse_args([])

    assert args.reset_paper_state is False


def test_parse_args_reset_paper_state_sets_true() -> None:
    args = parse_args(["--reset-paper-state"])

    assert args.reset_paper_state is True


def test_parse_args_monitor_paper_trades_defaults_false() -> None:
    args = parse_args([])

    assert args.monitor_paper_trades is False


def test_parse_args_monitor_paper_trades_sets_true() -> None:
    args = parse_args(["--monitor-paper-trades"])

    assert args.monitor_paper_trades is True


def test_parse_args_force_eod_exit_defaults_false() -> None:
    args = parse_args([])

    assert args.force_eod_exit is False


def test_parse_args_force_eod_exit_sets_true() -> None:
    args = parse_args(["--force-eod-exit"])

    assert args.force_eod_exit is True


def test_parse_args_backtest_defaults_false() -> None:
    args = parse_args([])

    assert args.backtest is False


def test_parse_args_backtest_sets_true() -> None:
    args = parse_args(["--backtest"])

    assert args.backtest is True


def test_parse_args_data_source_defaults_demo() -> None:
    args = parse_args([])

    assert args.data_source == "demo"


def test_parse_args_data_source_real() -> None:
    args = parse_args(["--data-source", "real"])

    assert args.data_source == "real"


def test_parse_args_real_csv() -> None:
    args = parse_args(["--real-csv", "data/real/my_egx_file.csv"])

    assert args.real_csv == Path("data/real/my_egx_file.csv")


def test_parse_args_validate_real_csv() -> None:
    args = parse_args(["--validate-real-csv", "--real-csv", "data/real/raw.csv"])

    assert args.validate_real_csv is True
    assert args.real_csv == Path("data/real/raw.csv")


def test_parse_args_normalize_real_csv() -> None:
    args = parse_args(["--normalize-real-csv", "--real-csv", "data/real/raw.csv"])

    assert args.normalize_real_csv is True
    assert args.real_csv == Path("data/real/raw.csv")


def test_parse_args_normalized_output() -> None:
    args = parse_args(
        [
            "--normalize-real-csv",
            "--real-csv",
            "data/real/raw.csv",
            "--normalized-output",
            "data/real/custom.csv",
        ]
    )

    assert args.normalized_output == Path("data/real/custom.csv")


def test_parse_args_import_daily_real_csv() -> None:
    args = parse_args(
        ["--import-daily-real-csv", "data/real/daily_2026_07_01.csv"]
    )

    assert args.import_daily_real_csv == Path("data/real/daily_2026_07_01.csv")


def test_parse_args_download_data_direct_url() -> None:
    args = parse_args(["--download-data", "direct-url"])

    assert args.download_data == "direct-url"


def test_parse_args_url() -> None:
    args = parse_args(
        ["--download-data", "direct-url", "--url", "https://example.com/file.csv"]
    )

    assert args.url == "https://example.com/file.csv"


def test_parse_args_kaggle_dataset() -> None:
    args = parse_args(
        ["--download-data", "kaggle", "--kaggle-dataset", "owner/dataset"]
    )

    assert args.kaggle_dataset == "owner/dataset"


def test_parse_args_eodhd_symbol() -> None:
    args = parse_args(
        ["--download-data", "eodhd", "--eodhd-symbol", "COMI"]
    )

    assert args.eodhd_symbol == "COMI"


def test_parse_args_eodhd_api_key() -> None:
    args = parse_args(
        [
            "--download-data",
            "eodhd",
            "--eodhd-symbol",
            "COMI",
            "--eodhd-api-key",
            "secret",
        ]
    )

    assert args.eodhd_api_key == "secret"


def test_parse_args_import_after_download() -> None:
    args = parse_args(
        [
            "--download-data",
            "direct-url",
            "--url",
            "https://example.com/file.csv",
            "--import-after-download",
        ]
    )

    assert args.import_after_download is True


def test_parse_args_egx_public_update() -> None:
    args = parse_args(["--egx-public-update"])

    assert args.egx_public_update is True


def test_parse_args_egx_public_page_stocks() -> None:
    args = parse_args(["--egx-public-update", "--egx-public-page", "stocks"])

    assert args.egx_public_page == "stocks"


def test_parse_args_import_egx_stocks() -> None:
    args = parse_args(
        [
            "--egx-public-update",
            "--egx-public-page",
            "stocks",
            "--import-egx-stocks",
        ]
    )

    assert args.import_egx_stocks is True


def test_parse_args_analyze_egx_debug_html() -> None:
    args = parse_args(
        [
            "--analyze-egx-debug-html",
            "data/downloads/egx_public/debug_stocks.html",
        ]
    )

    assert args.analyze_egx_debug_html == Path(
        "data/downloads/egx_public/debug_stocks.html"
    )


def test_parse_args_probe_egx_endpoints() -> None:
    args = parse_args(["--probe-egx-endpoints"])

    assert args.probe_egx_endpoints is True


def test_parse_args_analyze_egx_probe_file() -> None:
    args = parse_args(
        [
            "--analyze-egx-probe-file",
            "data/downloads/egx_public/probe_MarketFrame_20260701_120000.txt",
        ]
    )

    assert args.analyze_egx_probe_file == Path(
        "data/downloads/egx_public/probe_MarketFrame_20260701_120000.txt"
    )


def test_parse_args_probe_egx_types() -> None:
    args = parse_args(["--probe-egx-types"])

    assert args.probe_egx_types is True


def test_parse_args_egx_company_prices() -> None:
    args = parse_args(
        [
            "--egx-company-prices",
            "--egx-company-prefixes",
            "bank,cement,egypt,development",
        ]
    )

    assert args.egx_company_prices is True
    assert args.egx_company_prefixes == "bank,cement,egypt,development"


def test_parse_args_egx_browser_stocks_update() -> None:
    args = parse_args(["--egx-browser-stocks-update"])

    assert args.egx_browser_stocks_update is True


def test_parse_args_egx_browser_headful() -> None:
    args = parse_args(
        ["--egx-browser-stocks-update", "--egx-browser-headful"]
    )

    assert args.egx_browser_stocks_update is True
    assert args.egx_browser_headful is True


def test_parse_args_import_egx_browser_stocks() -> None:
    args = parse_args(
        [
            "--egx-browser-stocks-update",
            "--import-egx-browser-stocks",
        ]
    )

    assert args.egx_browser_stocks_update is True
    assert args.import_egx_browser_stocks is True


def test_parse_args_egx_attach_chrome_stocks() -> None:
    args = parse_args(["--egx-attach-chrome-stocks"])

    assert args.egx_attach_chrome_stocks is True
    assert args.chrome_cdp_url == "http://127.0.0.1:9222"


def test_parse_args_chrome_cdp_url() -> None:
    args = parse_args(
        [
            "--egx-attach-chrome-stocks",
            "--chrome-cdp-url",
            "http://127.0.0.1:9333",
        ]
    )

    assert args.chrome_cdp_url == "http://127.0.0.1:9333"


def test_parse_args_import_egx_attached_stocks() -> None:
    args = parse_args(
        [
            "--egx-attach-chrome-stocks",
            "--import-egx-attached-stocks",
        ]
    )

    assert args.egx_attach_chrome_stocks is True
    assert args.import_egx_attached_stocks is True


def test_resolve_scenario_path_default() -> None:
    assert resolve_scenario_path("default") == settings.EGX_DAILY_SAMPLE_PATH


def test_resolve_scenario_path_bull() -> None:
    assert resolve_scenario_path("bull") == settings.EGX_BULL_SAMPLE_PATH


def test_resolve_scenario_path_mixed() -> None:
    assert resolve_scenario_path("mixed") == settings.EGX_MIXED_SAMPLE_PATH


def test_resolve_scenario_path_weak() -> None:
    assert resolve_scenario_path("weak") == settings.EGX_WEAK_SAMPLE_PATH


def test_csv_provider_implements_market_data_provider() -> None:
    provider = CsvMarketDataProvider(settings.EGX_DAILY_SAMPLE_PATH)

    assert isinstance(provider, MarketDataProvider)

    provider.load_data()
    assert provider.get_latest_bar("COMI") is not None
    snapshot = provider.build_market_snapshot(["COMI"], ["EGX30"])
    assert len(snapshot.symbols) == 1
    assert len(snapshot.index_snapshots) == 1


def test_run_comi_demo_does_not_reset_existing_portfolio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.market_mood import MarketMood
    from core.models import TradeSide
    from core.portfolio import VirtualPortfolio
    from main import run_comi_demo

    portfolio_path = tmp_path / "portfolio_state.json"
    trades_path = tmp_path / "trades.json"
    monkeypatch.setattr(settings, "PORTFOLIO_STATE_PATH", portfolio_path)
    monkeypatch.setattr(settings, "TRADES_PATH", trades_path)

    portfolio = VirtualPortfolio()
    portfolio.reset()
    portfolio.open_trade(
        symbol="FWRY",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=6.24,
        stop_loss=6.06,
        take_profit=6.60,
    )

    class _FakeProvider:
        pass

    run_comi_demo(MarketMood.WEAK, _FakeProvider())

    reloaded = VirtualPortfolio()
    assert "FWRY" in reloaded.positions


def test_parse_args_show_egx_symbol_mapping() -> None:
    args = parse_args(["--show-egx-symbol-mapping"])
    assert args.show_egx_symbol_mapping is True
