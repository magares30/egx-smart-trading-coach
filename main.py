"""EGX Smart Trading Coach — market analysis CLI."""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from config import settings
from config.watchlist import DEFAULT_WATCHLIST, MARKET_INDEX_SYMBOLS
from core.backtester import (
    BacktestClosedTrade,
    BacktestConfig,
    BacktestReport,
    DailyBacktester,
)
from core.chrome_launcher import ChromeRemoteDebugLauncher
from core.daily_report import (
    DailyReportBuilder,
    format_daily_report_text,
    save_daily_report,
)
from core.candidate_filters import (
    CandidateFilters,
    DEFAULT_TOP_CANDIDATES,
    build_candidate_filters_from_cli,
    filter_candidates_for_display,
    filter_strategy_report,
    ranked_strategy_signals_for_display,
)
from core.candidate_ranking import (
    CandidateRankingConfig,
    build_candidate_ranking_config_from_cli,
    build_candidate_ranking_dataframe,
)
from core.technical_confirmation import (
    TechnicalConfirmationConfig,
    build_technical_confirmation_config_from_cli,
)
from core.talib_technical import (
    TalibTechnicalConfig,
    build_talib_technical_config_from_cli,
)
from core.multi_timeframe import (
    MultiTimeframeConfig,
    build_multi_timeframe_config_from_cli,
)
from core.scanner_universe import (
    DEFAULT_SCANNER_UNIVERSE,
    SCANNER_UNIVERSE_WATCHLIST,
    format_scanner_universe_label,
    is_full_market_universe,
)
from core.egx_debug_analyzer import EgxDebugHtmlAnalysis, EgxDebugHtmlAnalyzer
from core.egx_browser_reader import (
    EgxAttachedChromeStocksReader,
    EgxBrowserReadResult,
    EgxPublicBrowserStocksReader,
    normalize_browser_stocks_csv,
)
from core.egx_company_prices_reader import (
    DEFAULT_EGX_COMPANY_PREFIXES,
    EgxCompanyPricesReadResult,
    EgxCompanyPricesReader,
)
from core.egx_endpoint_probe import EgxEndpointProbe, EgxEndpointProbeResult
from core.egx_probe_analyzer import EgxProbeBodyAnalysis, EgxProbeBodyAnalyzer
from core.egx_type_probe import EgxTypeEndpointProbe, EgxTypeProbeResult
from core.egx_public_reader import (
    EgxPublicMarketWatchReader,
    EgxPublicPageType,
    EgxPublicReadResult,
    normalize_stocks_table_to_ohlcv,
)
from core.data_downloader import DownloadResult, SafeDataDownloader
from core.data_import import (
    DailyEgxDataImporter,
    DataImportValidationResult,
    EgxCsvImportValidator,
    is_only_insufficient_history_failure,
)
from core.live_ingest import load_ingest_warnings, save_ingest_warnings
from core.live_paper_monitor import LivePaperMonitor, LivePaperMonitorReport
from core.live_paper_trader import LivePaperTrader, LivePaperTradingReport
from core.live_scanner_adapter import build_live_market_snapshot
from core.live_snapshot import EgxLiveSnapshotProvider, LiveMarketSnapshot
from core.live_volume import LiveVolumeHistoryStore
from core.market_data import CsvMarketDataProvider, MarketDataProvider, MarketSnapshot
from core.market_quality_filters import (
    MarketQualityFilterResult,
    MarketQualityFilters,
    allowed_symbols_from_quality_result,
    apply_market_quality_filters,
    build_market_quality_filter_summary_lines,
    build_market_quality_filters_from_cli,
    build_quality_filter_dataframe,
)
from core.market_data_providers import (
    AUTO_FALLBACK_TO_EGX_WARNING,
    DATA_PROVIDER_AUTO,
    DATA_PROVIDER_EGX,
    DATA_PROVIDER_TRADINGVIEW,
    DEFAULT_DATA_PROVIDER,
    format_data_provider_label,
)
from core.tradingview_data_provider import (
    TradingViewQueryFilterConfig,
    TradingViewQueryPrefilterDiagnostics,
    build_tradingview_query_filter_config_from_cli,
    build_tradingview_query_prefilter_summary_lines,
    fetch_and_save_tradingview_snapshot,
    print_tradingview_snapshot_summary,
    tradingview_snapshot_is_usable,
)
from core.market_hours import (
    detect_egx_market_session,
    format_market_session_report_lines,
)
from core.market_mood import MarketMood, MarketMoodDetector, MarketMoodResult
from core.market_breadth_mood import MarketBreadthMoodResult
from core.models import SignalType, TradeSide, TradeSignal
from core.portfolio_report import (
    PortfolioReportBuilder,
    build_latest_prices_from_snapshot,
    format_portfolio_report_text,
    save_portfolio_report,
)
from core.paper_monitor import PaperMonitorReport, PaperTradeMonitor
from core.paper_trader import AutoPaperTrader, PaperTradingReport, PaperTradeResult
from core.portfolio import VirtualPortfolio
from core.risk import RiskManager
from core.scanner import EgyptianMomentumScanner, ScannerReport, ScannerResult
from core.strategy import (
    StrategyDecision,
    StrategyReport,
    StrategyResult,
    TrendJoinLongStrategy,
)
from core.warning_formatting import summarize_live_scan_warnings
from core.trade_journal import TradeJournal

SCENARIO_PATHS: dict[str, Path] = {
    "default": settings.EGX_DAILY_SAMPLE_PATH,
    "bull": settings.EGX_BULL_SAMPLE_PATH,
    "mixed": settings.EGX_MIXED_SAMPLE_PATH,
    "weak": settings.EGX_WEAK_SAMPLE_PATH,
}

DEMO_FIXTURE_LABEL = "DEMO/FIXTURE (not real EGX market data)"
REAL_DATA_LABEL = "USER-PROVIDED REAL MARKET DATA FILE"
REAL_DATA_MODE_LABEL = "REAL LOCAL CSV"
REAL_SCENARIO_LABEL = "real"

REAL_CSV_NOT_FOUND_MESSAGE = (
    "Real data CSV not found. Put a normalized CSV at "
    f"{settings.DEFAULT_REAL_EGX_CSV_PATH.relative_to(settings.PROJECT_ROOT)} "
    "or pass --real-csv PATH."
)


@dataclass
class LiveScanPipelineResult:
    live_snapshot: LiveMarketSnapshot
    market_snapshot: MarketSnapshot
    mood_result: MarketMoodResult
    scanner_report: ScannerReport
    strategy_report: StrategyReport
    warnings: list[str]
    snapshot_path: Path
    lookback_days: int
    min_history_days: int
    scanner_universe: str = DEFAULT_SCANNER_UNIVERSE
    candidate_filters: CandidateFilters = CandidateFilters()
    ranking_config: CandidateRankingConfig = field(default_factory=CandidateRankingConfig)
    technical_config: TechnicalConfirmationConfig = field(
        default_factory=TechnicalConfirmationConfig
    )
    multi_timeframe_config: MultiTimeframeConfig = field(
        default_factory=MultiTimeframeConfig
    )
    data_provider: str | None = None
    quality_filters: MarketQualityFilters = field(
        default_factory=MarketQualityFilters
    )
    quality_filter_result: MarketQualityFilterResult | None = None
    tv_query_filter_config: TradingViewQueryFilterConfig = field(
        default_factory=TradingViewQueryFilterConfig
    )
    tv_query_prefilter_diagnostics: TradingViewQueryPrefilterDiagnostics | None = None
    watchlist_scanner_results: dict[str, ScannerResult] = field(default_factory=dict)
    market_breadth_mood_result: MarketBreadthMoodResult | None = None


def resolve_scenario_path(scenario: str) -> Path:
    """Return the CSV path for a named demo scenario."""
    return SCENARIO_PATHS[scenario]


def resolve_data_csv_path(
    data_source: str,
    scenario: str,
    real_csv: Path | None,
) -> Path:
    """Return the CSV path for demo or real local data mode."""
    if data_source == "demo":
        return resolve_scenario_path(scenario)

    csv_path = real_csv or settings.DEFAULT_REAL_EGX_CSV_PATH
    if not csv_path.exists():
        raise FileNotFoundError(REAL_CSV_NOT_FOUND_MESSAGE)
    return csv_path


def display_scenario_label(data_source: str, scenario: str) -> str:
    """Return the scenario label shown in market snapshot output."""
    if data_source == "real":
        return REAL_SCENARIO_LABEL
    return scenario


def data_type_label(data_source: str) -> str:
    """Return the data type label for console output."""
    if data_source == "real":
        return REAL_DATA_LABEL
    return DEMO_FIXTURE_LABEL


def create_data_provider(csv_path: Path) -> MarketDataProvider:
    """Create a market data provider for the given CSV path."""
    return CsvMarketDataProvider(csv_path)


def load_latest_prices(provider: MarketDataProvider) -> dict[str, float]:
    """Load latest close prices for the default watchlist."""
    provider.load_data()
    prices: dict[str, float] = {}
    for symbol in DEFAULT_WATCHLIST:
        try:
            prices[symbol] = provider.get_latest_bar(symbol).close
        except ValueError:
            continue
    return prices


def format_egp(amount: float) -> str:
    """Format a monetary value in EGP."""
    sign = "+" if amount > 0 else ""
    return f"{sign}{amount:,.2f} {settings.BASE_CURRENCY}"


def _print_scanner_report_results(
    report: ScannerReport,
    *,
    candidate_filters: CandidateFilters | None = None,
    live_snapshot: LiveMarketSnapshot | None = None,
    snapshot_path: Path | None = None,
    ranking_config: CandidateRankingConfig | None = None,
    strategy_report: StrategyReport | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
) -> None:
    print("=== Scanner A: Egyptian Momentum Watchlist ===")
    print()

    def _print_group(label: str, items: list[ScannerResult]) -> None:
        if not items:
            print(f"{label}:")
            print("  (none)")
            print()
            return
        print(f"{label}:")
        for item in items:
            print(
                f"  {item.symbol} | Score {item.score} | "
                f"Change {item.change_percent:+.2f}% | "
                f"Volume {item.volume_ratio:.2f}x"
            )
            if item.reasons:
                print(f"    Reasons: {', '.join(item.reasons)}")
            if item.blockers:
                print(f"    Blockers: {', '.join(item.blockers)}")
        print()

    filters = candidate_filters or CandidateFilters()
    ranking_values = ranking_config or CandidateRankingConfig()
    technical_values = technical_config or TechnicalConfirmationConfig()
    snapshot_df = None
    if live_snapshot is not None:
        snapshot_df = build_candidate_ranking_dataframe(live_snapshot, snapshot_path)
    display_candidates = filter_candidates_for_display(
        report.candidates,
        filters,
        snapshot_df=snapshot_df,
        ranking_config=ranking_values,
        strategy_report=strategy_report,
        technical_config=technical_values,
    )
    _print_group("CANDIDATES", display_candidates)
    _print_group("WATCH", report.watchlist)
    _print_group("BLOCKED", report.blocked)


def print_scanner_report(
    mood_result: MarketMoodResult, market_snapshot: MarketSnapshot
) -> ScannerReport:
    """Run Scanner A and print ranked results."""
    report = generate_scanner_report(mood_result, market_snapshot)
    _print_scanner_report_results(report)
    return report


def generate_scanner_report(
    mood_result: MarketMoodResult, market_snapshot: MarketSnapshot
) -> ScannerReport:
    """Run Scanner A without printing."""
    return EgyptianMomentumScanner(mood_result).scan(market_snapshot)


def _print_strategy_report_results(
    report: StrategyReport,
    *,
    scanner_report: ScannerReport | None = None,
    candidate_filters: CandidateFilters | None = None,
    snapshot_df=None,
    ranking_config: CandidateRankingConfig | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
) -> None:
    print("=== Strategy Scanner B: Trend Join Long Setups ===")
    print()

    def _print_setup(item: StrategyResult) -> None:
        if item.entry_price is not None and item.stop_loss is not None:
            tp = item.take_profit if item.take_profit is not None else 0.0
            rr = f" | R:R 1:{item.risk_reward:.1f}" if item.risk_reward else ""
            print(
                f"  {item.symbol} | Confidence {item.confidence_score} | "
                f"Entry {item.entry_price:.2f} | Stop {item.stop_loss:.2f} | "
                f"TP {tp:.2f}{rr}"
            )
        else:
            print(f"  {item.symbol} | Confidence {item.confidence_score}")
        if item.reasons:
            print(f"    Reasons: {', '.join(item.reasons)}")
        if item.blockers:
            print(f"    Blockers: {', '.join(item.blockers)}")

    if scanner_report is not None and candidate_filters is not None:
        ranked = ranked_strategy_signals_for_display(
            report,
            scanner_report,
            candidate_filters,
            snapshot_df,
            ranking_config,
            technical_config,
        )
        buy_setups = [
            item for item in ranked if item.decision == StrategyDecision.BUY_SETUP
        ]
        watch = [item for item in ranked if item.decision == StrategyDecision.WATCH]
    else:
        buy_setups = report.buy_setups
        watch = report.watch

    print("BUY SETUPS:")
    if buy_setups:
        for item in buy_setups:
            _print_setup(item)
    else:
        print("  (none)")
    print()

    print("WATCH:")
    if watch:
        for item in watch:
            _print_setup(item)
    else:
        print("  (none)")
    print()

    print("BLOCKED:")
    if report.blocked:
        for item in report.blocked:
            _print_setup(item)
    else:
        print("  (none)")
    print()


def print_strategy_report(
    scanner_report: ScannerReport, market_snapshot: MarketSnapshot
) -> StrategyReport:
    """Run Strategy Scanner B and print trade plans."""
    report = generate_strategy_report(scanner_report, market_snapshot)
    _print_strategy_report_results(report)
    return report


def generate_strategy_report(
    scanner_report: ScannerReport, market_snapshot: MarketSnapshot
) -> StrategyReport:
    """Run Strategy Scanner B without printing."""
    return TrendJoinLongStrategy().generate_signals(scanner_report, market_snapshot)


def _filtered_strategy_report(pipeline: LiveScanPipelineResult) -> StrategyReport:
    """Apply candidate filters to strategy signals."""
    snapshot_df = build_candidate_ranking_dataframe(
        pipeline.live_snapshot,
        pipeline.snapshot_path,
    )
    return filter_strategy_report(
        pipeline.strategy_report,
        pipeline.scanner_report,
        pipeline.candidate_filters,
        snapshot_df,
    )


def _open_live_paper_trades_from_pipeline(
    pipeline: LiveScanPipelineResult,
    *,
    max_trades: int,
    min_confidence: int,
    ignore_market_hours: bool = False,
) -> LivePaperTradingReport:
    """Open paper trades from filtered strategy BUY_SETUP signals."""
    if is_full_market_universe(pipeline.scanner_universe):
        print(f"Full-market paper trading enabled; max trades limit is {max_trades}.")
        print()

    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    trader = LivePaperTrader(
        portfolio=portfolio,
        trade_journal=journal,
        risk_manager=RiskManager(),
        max_trades_per_run=max_trades,
        min_confidence_score=min_confidence,
        ignore_market_hours=ignore_market_hours,
    )
    return trader.trade_from_strategy_report(_filtered_strategy_report(pipeline))


def print_market_snapshot(
    scenario: str,
    csv_path: Path,
    provider: MarketDataProvider,
    *,
    data_source: str = "demo",
) -> tuple[MarketMood, MarketMoodResult, MarketSnapshot]:
    """Load market data, print snapshot summary, and return mood + snapshot."""
    market_snapshot = provider.build_market_snapshot(
        DEFAULT_WATCHLIST, MARKET_INDEX_SYMBOLS
    )
    mood_result = MarketMoodDetector().evaluate(market_snapshot.index_snapshots)

    print("=== Market Snapshot ===")
    if data_source == "real":
        print(f"Data source mode: {REAL_DATA_MODE_LABEL}")
    print(f"Scenario: {scenario}")
    print(f"Data source: {csv_path.relative_to(settings.PROJECT_ROOT)}")
    print(f"Data type: {data_type_label(data_source)}")
    print()

    top_change = sorted(
        market_snapshot.symbols, key=lambda s: s.change_percent, reverse=True
    )[:5]
    print("Top 5 by change %:")
    for snap in top_change:
        print(f"  {snap.symbol}: {snap.change_percent:+.2f}% (close {snap.latest_close:.2f})")
    print()

    top_volume = sorted(
        market_snapshot.symbols, key=lambda s: s.volume_ratio, reverse=True
    )[:5]
    print("Top 5 by volume ratio:")
    for snap in top_volume:
        print(f"  {snap.symbol}: {snap.volume_ratio:.2f}x (vol {snap.latest_volume:,})")
    print()

    print("=== Market Mood ===")
    print(f"Mood: {mood_result.mood.value} | Score: {mood_result.score}/100")
    if mood_result.reasons:
        print("Reasons:")
        for reason in mood_result.reasons:
            print(f"  + {reason}")
    if mood_result.blockers:
        print("Blockers:")
        for blocker in mood_result.blockers:
            print(f"  - {blocker}")
    print()

    print("=== Index Summaries ===")
    for snap in market_snapshot.index_snapshots:
        print(
            f"  {snap.symbol}: close {snap.latest_close:,.2f} | "
            f"change {snap.change_percent:+.2f}% | "
            f"above SMA5: {snap.above_sma_5}"
        )
    print()

    return mood_result.mood, mood_result, market_snapshot


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="EGX Smart Trading Coach — market analysis (paper trading only)"
    )
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIO_PATHS.keys()),
        default="default",
        help="Demo market data scenario to use (default: default)",
    )
    parser.add_argument(
        "--data-source",
        choices=["demo", "real"],
        default="demo",
        help="Data source mode: demo fixtures or real local CSV (default: demo)",
    )
    parser.add_argument(
        "--real-csv",
        type=Path,
        default=None,
        help="Path to a real local EGX CSV file (used with --data-source real)",
    )
    parser.add_argument(
        "--validate-real-csv",
        action="store_true",
        help="Validate a real local CSV and exit (requires --real-csv)",
    )
    parser.add_argument(
        "--normalize-real-csv",
        action="store_true",
        help="Normalize a real local CSV and exit (requires --real-csv)",
    )
    parser.add_argument(
        "--normalized-output",
        type=Path,
        default=None,
        help="Output path for --normalize-real-csv "
        f"(default: {settings.DEFAULT_REAL_EGX_CSV_PATH.name})",
    )
    parser.add_argument(
        "--import-daily-real-csv",
        type=Path,
        default=None,
        help="Import a daily CSV/XLSX file into the real data master file",
    )
    parser.add_argument(
        "--download-data",
        choices=["direct-url", "kaggle", "eodhd"],
        default=None,
        help="Download market data from a safe explicit source and exit",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Direct CSV/XLSX/ZIP file URL for --download-data direct-url",
    )
    parser.add_argument(
        "--kaggle-dataset",
        default=None,
        help="Kaggle dataset slug (owner/dataset) for --download-data kaggle",
    )
    parser.add_argument(
        "--eodhd-symbol",
        default=None,
        help="Symbol for --download-data eodhd (e.g. COMI)",
    )
    parser.add_argument(
        "--eodhd-api-key",
        default=None,
        help="EODHD API key for --download-data eodhd",
    )
    parser.add_argument(
        "--import-after-download",
        action="store_true",
        help="Import downloaded CSV/XLSX files into the real data master file",
    )
    parser.add_argument(
        "--egx-public-update",
        action="store_true",
        help="Read public EGX market-watch pages and save CSV tables",
    )
    parser.add_argument(
        "--egx-public-page",
        choices=["all", "market_summary", "indices", "sectors", "stocks"],
        default="all",
        help="EGX public page to read with --egx-public-update (default: all)",
    )
    parser.add_argument(
        "--import-egx-stocks",
        action="store_true",
        help="Normalize and import EGX stocks table into the real data master file",
    )
    parser.add_argument(
        "--analyze-egx-debug-html",
        type=Path,
        default=None,
        help="Analyze a saved EGX debug HTML file for embedded URLs and endpoints",
    )
    parser.add_argument(
        "--probe-egx-endpoints",
        action="store_true",
        help="Probe discovered public EGX endpoints and save raw response bodies",
    )
    parser.add_argument(
        "--analyze-egx-probe-file",
        type=Path,
        default=None,
        help="Analyze a saved EGX probe response file for embedded JS and endpoint hints",
    )
    parser.add_argument(
        "--probe-egx-types",
        action="store_true",
        help="Probe public EGX endpoint variants using discovered type values",
    )
    parser.add_argument(
        "--egx-company-prices",
        action="store_true",
        help="Fetch public EGX company prices via GetCompanyPricesList",
    )
    parser.add_argument(
        "--egx-company-prefixes",
        default=None,
        help=(
            "Comma-separated search prefixes for --egx-company-prices "
            '(default: "bank,cement,egypt,development")'
        ),
    )
    parser.add_argument(
        "--egx-browser-stocks-update",
        action="store_true",
        help="Read visible EGX stocks table from the public prices page via browser",
    )
    parser.add_argument(
        "--egx-browser-headful",
        action="store_true",
        help="Run the EGX browser reader with a visible Chromium window",
    )
    parser.add_argument(
        "--import-egx-browser-stocks",
        action="store_true",
        help="Normalize and import browser-extracted EGX stocks into the real data master file",
    )
    parser.add_argument(
        "--egx-attach-chrome-stocks",
        action="store_true",
        help="Attach to user-started Chrome and read the visible EGX stocks table",
    )
    parser.add_argument(
        "--chrome-cdp-url",
        default="http://127.0.0.1:9222",
        help="Chrome DevTools Protocol URL for --egx-attach-chrome-stocks",
    )
    parser.add_argument(
        "--import-egx-attached-stocks",
        action="store_true",
        help="Normalize and import attached Chrome EGX stocks into the real data master file",
    )
    parser.add_argument(
        "--egx-live-scan",
        action="store_true",
        help="Run Scanner A and Strategy Scanner B from the live EGX snapshot CSV",
    )
    parser.add_argument(
        "--egx-live-snapshot",
        type=Path,
        default=None,
        help=(
            "Path to live EGX snapshot CSV for --egx-live-scan "
            f"(default: {settings.EGX_LIVE_SNAPSHOT_PATH.name})"
        ),
    )
    parser.add_argument(
        "--egx-update-and-live-scan",
        action="store_true",
        help=(
            "Attach to Chrome, read EGX stocks table, save live snapshot, "
            "and run live scan"
        ),
    )
    parser.add_argument(
        "--egx-one-click-live-scan",
        action="store_true",
        help=(
            "Launch Chrome if needed, open EGX prices page, save live snapshot, "
            "and run live scan"
        ),
    )
    parser.add_argument(
        "--chrome-profile-dir",
        type=Path,
        default=None,
        help="Isolated Chrome profile directory for one-click live scan",
    )
    parser.add_argument(
        "--live-volume-lookback-days",
        type=int,
        default=settings.DEFAULT_LIVE_VOLUME_LOOKBACK_DAYS,
        help="Days of live snapshot history to use for volume ratio (default: 20)",
    )
    parser.add_argument(
        "--live-volume-min-history-days",
        type=int,
        default=settings.DEFAULT_LIVE_VOLUME_MIN_HISTORY_DAYS,
        help="Minimum saved live history days before volume ratio is used (default: 3)",
    )
    parser.add_argument(
        "--save-live-history-only",
        action="store_true",
        help="Save current live snapshot into live history and exit",
    )
    parser.add_argument(
        "--egx-daily-report",
        action="store_true",
        help="Build and save a daily report from the live EGX snapshot",
    )
    parser.add_argument(
        "--egx-one-click-daily-report",
        action="store_true",
        help=(
            "Run one-click EGX update, live scan, and save a daily report"
        ),
    )
    parser.add_argument(
        "--egx-live-paper-trade",
        action="store_true",
        help="Open paper trades from live EGX strategy BUY_SETUP signals",
    )
    parser.add_argument(
        "--egx-one-click-paper-trade",
        action="store_true",
        help=(
            "Run one-click EGX update, live scan, and open paper trades "
            "from BUY_SETUP signals"
        ),
    )
    parser.add_argument(
        "--live-paper-max-trades",
        type=int,
        default=3,
        help="Maximum paper trades to open per live scan run (default: 3)",
    )
    parser.add_argument(
        "--live-paper-min-confidence",
        type=int,
        default=75,
        help="Minimum confidence score for live paper trades (default: 75)",
    )
    parser.add_argument(
        "--egx-live-paper-monitor",
        action="store_true",
        help="Monitor open paper trades against the EGX live snapshot",
    )
    parser.add_argument(
        "--egx-one-click-paper-monitor",
        action="store_true",
        help=(
            "Run one-click EGX update and monitor open paper trades "
            "against live snapshot TP/SL levels"
        ),
    )
    parser.add_argument(
        "--egx-one-click-paper-cycle",
        action="store_true",
        help=(
            "Run one-click EGX update, monitor open trades, live scan, "
            "and open new paper trades from BUY_SETUP signals"
        ),
    )
    parser.add_argument(
        "--egx-workflow",
        choices=["report", "scan", "monitor", "trade", "cycle", "portfolio"],
        help=(
            "Unified EGX workflow: report, scan, monitor, trade, cycle, or portfolio "
            "(one-click Chrome update unless --egx-local is set)"
        ),
    )
    parser.add_argument(
        "--egx-local",
        action="store_true",
        help="Use local egx_live_snapshot.csv with --egx-workflow (skip one-click update)",
    )
    parser.add_argument(
        "--data-provider",
        choices=[DATA_PROVIDER_EGX, DATA_PROVIDER_TRADINGVIEW, DATA_PROVIDER_AUTO],
        default=DEFAULT_DATA_PROVIDER,
        help=(
            "Live snapshot data provider: EGX Chrome reader, TradingView screener, "
            f"or auto (default: {DEFAULT_DATA_PROVIDER})"
        ),
    )
    parser.add_argument(
        "--scanner-universe",
        choices=["watchlist", "full-market"],
        default=DEFAULT_SCANNER_UNIVERSE,
        help=(
            "Scanner symbol universe: configured watchlist or full live snapshot "
            f"(default: {DEFAULT_SCANNER_UNIVERSE})"
        ),
    )
    parser.add_argument(
        "--top-candidates",
        type=int,
        default=None,
        help=(
            "Maximum Top Candidates shown in report/scan output "
            f"(default: {DEFAULT_TOP_CANDIDATES})"
        ),
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="Only include candidates with scanner score >= N when set",
    )
    parser.add_argument(
        "--min-volume-ratio",
        type=float,
        default=None,
        help=(
            "Only include candidates with TradingView relative volume >= X when set "
            "(uses volume_ratio / 10-day relative volume when available)"
        ),
    )
    parser.add_argument(
        "--max-rank-change",
        type=float,
        default=None,
        help="Candidate ranking extreme-change threshold (default: 12.0)",
    )
    parser.add_argument(
        "--prefer-change-min",
        type=float,
        default=None,
        help="Candidate ranking preferred minimum daily change %% (default: 0.5)",
    )
    parser.add_argument(
        "--prefer-change-max",
        type=float,
        default=None,
        help="Candidate ranking preferred maximum daily change %% (default: 7.0)",
    )
    parser.add_argument(
        "--enable-technical-confirmation",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable TradingView technical confirmation for candidate ranking (default: true)",
    )
    parser.add_argument(
        "--rsi-min",
        type=float,
        default=None,
        help="Technical confirmation minimum RSI (default: 45.0)",
    )
    parser.add_argument(
        "--rsi-max",
        type=float,
        default=None,
        help="Technical confirmation maximum RSI (default: 70.0)",
    )
    parser.add_argument(
        "--rsi-caution",
        type=float,
        default=None,
        help="Technical confirmation RSI caution threshold (default: 75.0)",
    )
    parser.add_argument(
        "--adx-min",
        type=float,
        default=None,
        help="Technical confirmation minimum ADX (default: 20.0)",
    )
    parser.add_argument(
        "--min-market-cap-quality",
        type=float,
        default=None,
        help=(
            "Candidate filter: minimum market cap when fundamental data is available "
            "(distinct from --min-market-cap market quality filter)"
        ),
    )
    parser.add_argument(
        "--max-pe",
        type=float,
        default=None,
        help="Candidate filter: maximum P/E ratio when fundamental data is available",
    )
    parser.add_argument(
        "--max-pb",
        type=float,
        default=None,
        help="Candidate filter: maximum P/B ratio when fundamental data is available",
    )
    parser.add_argument(
        "--require-fundamentals",
        action="store_true",
        help="Candidate filter: exclude candidates with unknown fundamental fields",
    )
    parser.add_argument(
        "--enable-multi-timeframe",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable multi-timeframe entry timing for Top Candidates (default: true)",
    )
    parser.add_argument(
        "--disable-multi-timeframe",
        dest="enable_multi_timeframe",
        action="store_false",
        help="Disable multi-timeframe entry timing for Top Candidates",
    )
    parser.add_argument(
        "--entry-timeframes",
        default="1h,15m",
        help='Comma-separated entry timing timeframes to check (default: "1h,15m")',
    )
    parser.add_argument(
        "--enable-tv-prefilter",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Enable TradingView query-level pre-filters using market quality thresholds "
            "(default: false)"
        ),
    )
    parser.add_argument(
        "--disable-tv-prefilter",
        dest="enable_tv_prefilter",
        action="store_false",
        help="Disable TradingView query-level pre-filters",
    )
    parser.add_argument(
        "--enable-portfolio-marking",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include paper portfolio mark-to-market in daily reports (default: true)",
    )
    parser.add_argument(
        "--disable-portfolio-marking",
        dest="enable_portfolio_marking",
        action="store_false",
        help="Exclude paper portfolio mark-to-market from daily reports",
    )
    parser.add_argument(
        "--disable-performance-analytics",
        dest="enable_performance_analytics",
        action="store_false",
        default=True,
        help="Exclude paper trading performance analytics from daily reports",
    )
    parser.add_argument(
        "--ignore-market-hours",
        action="store_true",
        help="Allow paper trade entries outside EGX continuous session hours",
    )
    parser.add_argument(
        "--market-hours-status",
        action="store_true",
        help="Print current EGX market session status and exit",
    )
    parser.add_argument(
        "--enable-talib-engine",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include TA-Lib technical engine in daily reports (default: true)",
    )
    parser.add_argument(
        "--disable-talib-engine",
        dest="enable_talib_engine",
        action="store_false",
        help="Exclude TA-Lib technical engine from daily reports",
    )
    parser.add_argument(
        "--talib-min-history-days",
        type=int,
        default=None,
        help=(
            "Minimum saved OHLCV history bars required for TA-Lib indicators "
            f"(default: {settings.DEFAULT_TALIB_MIN_HISTORY_DAYS})"
        ),
    )
    parser.add_argument(
        "--min-price",
        type=float,
        default=None,
        help="Full-market quality filter: minimum close price (default: 1.0)",
    )
    parser.add_argument(
        "--min-volume",
        type=int,
        default=None,
        help="Full-market quality filter: minimum share volume (default: 50000)",
    )
    parser.add_argument(
        "--min-market-cap",
        type=float,
        default=None,
        help="Full-market quality filter: minimum market cap when column is available",
    )
    parser.add_argument(
        "--exclude-zero-volume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Full-market quality filter: exclude zero-volume symbols (default: true)",
    )
    parser.add_argument(
        "--include-illiquid",
        action="store_true",
        help="Full-market quality filter: skip min-volume and zero-volume exclusions",
    )
    parser.add_argument(
        "--show-egx-symbol-mapping",
        action="store_true",
        help="Print configured EGX company-name to ticker mappings and exit",
    )
    parser.add_argument(
        "--demo-trade",
        action="store_true",
        help="Run the separate hardcoded COMI paper-trade demo after analysis",
    )
    parser.add_argument(
        "--auto-paper-trade",
        action="store_true",
        help="Open paper trades from Strategy Scanner B BUY_SETUP signals",
    )
    parser.add_argument(
        "--reset-paper-state",
        action="store_true",
        help="Reset portfolio and trade journal before running",
    )
    parser.add_argument(
        "--monitor-paper-trades",
        action="store_true",
        help="Review and close open paper trades using latest prices",
    )
    parser.add_argument(
        "--force-eod-exit",
        action="store_true",
        help="Force close all open paper trades at latest prices (requires monitor)",
    )
    parser.add_argument(
        "--telegram-bot",
        action="store_true",
        help="Run Telegram interactive bot (reads latest saved report JSON)",
    )
    parser.add_argument(
        "--egx-cloud-readiness-check",
        action="store_true",
        help="Verify Cloud Run dependencies, paths, and report command (no report run)",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="Run daily backtest simulation after market analysis",
    )
    return parser.parse_args(argv)


def reset_paper_state() -> None:
    """Reset portfolio and trade journal to initial empty state."""
    portfolio = VirtualPortfolio()
    portfolio.reset()
    journal = TradeJournal()
    journal.clear()
    print("Paper trading state reset.")
    print()


def maybe_reset_paper_state(reset_flag: bool) -> None:
    """Reset paper trading state when requested."""
    if reset_flag:
        reset_paper_state()


def build_latest_prices(market_snapshot: MarketSnapshot) -> dict[str, float]:
    """Build a symbol-to-price map from market snapshot data."""
    prices: dict[str, float] = {}
    for snap in market_snapshot.symbols + market_snapshot.index_snapshots:
        prices[snap.symbol] = snap.latest_close
    return prices


def print_validation_result(
    result: DataImportValidationResult,
    csv_path: Path,
    *,
    title: str = "=== Real EGX CSV Validation ===",
) -> None:
    """Print CSV validation summary."""
    print(title)
    print(f"File: {csv_path}")
    print(f"Valid: {'yes' if result.valid else 'no'}")
    print(f"Rows: {result.rows}")
    print(f"Symbols: {result.symbols_count}")
    if result.date_min and result.date_max:
        print(f"Date range: {result.date_min} to {result.date_max}")
    else:
        print("Date range: n/a")
    print()
    print("Errors:")
    if result.errors:
        for error in result.errors:
            print(f"  - {error}")
    else:
        print("  (none)")
    print()
    print("Warnings:")
    if result.warnings:
        for warning in result.warnings:
            print(f"  - {warning}")
    else:
        print("  (none)")
    print()


def run_validate_real_csv(csv_path: Path) -> int:
    """Validate a real CSV file and return an exit code."""
    validator = EgxCsvImportValidator()
    result = validator.validate_csv(csv_path)
    print_validation_result(result, csv_path)
    return 0 if result.valid else 1


def run_normalize_real_csv(input_path: Path, output_path: Path) -> int:
    """Normalize a real CSV file and return an exit code."""
    validator = EgxCsvImportValidator()
    result = validator.normalize_csv(input_path, output_path)
    print("=== Real EGX CSV Normalization ===")
    print(f"Input: {input_path}")
    print(f"Output: {output_path}")
    print()
    print_validation_result(result, output_path)
    return 0 if result.valid else 1


def run_import_daily_real_csv(input_path: Path, master_path: Path) -> int:
    """Import a daily file into the real data master and return an exit code."""
    importer = DailyEgxDataImporter()
    result = importer.import_daily_file(input_path, master_path)
    print("=== Daily Real Data Import ===")
    print(f"Input file: {input_path}")
    try:
        master_display = master_path.relative_to(settings.PROJECT_ROOT)
    except ValueError:
        master_display = master_path
    print(f"Master file: {master_display}")
    print(f"Valid: {'yes' if result.valid else 'no'}")
    print(f"Rows: {result.rows}")
    print(f"Symbols: {result.symbols_count}")
    if result.date_min and result.date_max:
        print(f"Date range: {result.date_min} to {result.date_max}")
    else:
        print("Date range: n/a")
    print()
    print("Errors:")
    if result.errors:
        for error in result.errors:
            print(f"  - {error}")
    else:
        print("  (none)")
    print()
    print("Warnings:")
    if result.warnings:
        for warning in result.warnings:
            print(f"  - {warning}")
    else:
        print("  (none)")
    print()
    return 0 if result.valid else 1


def print_download_result(result: DownloadResult) -> None:
    """Print safe data download summary."""
    print("=== Safe Data Download ===")
    print(f"Provider: {result.provider.value}")
    print(f"Success: {'yes' if result.success else 'no'}")
    print()
    print("Saved files:")
    if result.saved_files:
        for path in result.saved_files:
            try:
                display = path.relative_to(settings.PROJECT_ROOT)
            except ValueError:
                display = path
            print(f"  - {display}")
    else:
        print("  (none)")
    print()
    print("Errors:")
    if result.errors:
        for error in result.errors:
            print(f"  - {error}")
    else:
        print("  (none)")
    print()
    print("Warnings:")
    if result.warnings:
        for warning in result.warnings:
            print(f"  - {warning}")
    else:
        print("  (none)")
    print()


def run_download_data(
    download_mode: str,
    *,
    url: str | None,
    kaggle_dataset: str | None,
    eodhd_symbol: str | None,
    eodhd_api_key: str | None,
    import_after_download: bool,
) -> int:
    """Run a safe download flow and optionally import results."""
    downloader = SafeDataDownloader()
    downloads_dir = settings.DOWNLOADS_DIR

    if download_mode == "direct-url":
        if not url:
            print("Error: --url is required for --download-data direct-url.")
            return 1
        result = downloader.download_direct_url(url, downloads_dir)
    elif download_mode == "kaggle":
        if not kaggle_dataset:
            print("Error: --kaggle-dataset is required for --download-data kaggle.")
            return 1
        result = downloader.download_kaggle_dataset(kaggle_dataset, downloads_dir)
    else:
        if not eodhd_symbol:
            print("Error: --eodhd-symbol is required for --download-data eodhd.")
            return 1
        result = downloader.download_eodhd_symbol(
            eodhd_symbol,
            eodhd_api_key or "",
            downloads_dir,
        )

    print_download_result(result)
    if not result.success:
        return 1

    if import_after_download:
        importable = [
            path
            for path in result.saved_files
            if path.suffix.lower() in {".csv", ".xlsx"}
        ]
        if not importable:
            print("No CSV/XLSX files available to import.")
            return 1

        exit_code = 0
        for file_path in importable:
            import_code = run_import_daily_real_csv(
                file_path,
                settings.DEFAULT_REAL_EGX_CSV_PATH,
            )
            exit_code = max(exit_code, import_code)
        return exit_code

    return 0


def _print_item_group(label: str, items: list[str]) -> None:
    print(label)
    if items:
        for item in items:
            print(f"  - {item}")
    else:
        print("  (none)")
    print()


def _print_url_group(label: str, urls: list[str]) -> None:
    print(label)
    if urls:
        for url in urls:
            print(f"  - {url}")
    else:
        print("  (none)")
    print()


def print_egx_debug_html_analysis(result: EgxDebugHtmlAnalysis) -> None:
    """Print EGX debug HTML analysis results."""
    print("=== EGX Debug HTML Analysis ===")
    try:
        file_display = result.html_path.relative_to(settings.PROJECT_ROOT)
    except ValueError:
        file_display = result.html_path
    print(f"File: {file_display}")
    print(f"Title: {result.title or '(none)'}")
    print()
    _print_url_group("Script src URLs:", result.script_src_urls)
    _print_url_group("Link href URLs:", result.link_href_urls)
    _print_url_group("Iframe src URLs:", result.iframe_src_urls)
    _print_url_group("Form action URLs:", result.form_action_urls)
    _print_url_group("Interesting URLs:", result.interesting_urls)
    print("Note: URLs are listed for analysis only. No network requests were made.")


def run_analyze_egx_debug_html(html_path: Path) -> int:
    """Analyze a local EGX debug HTML file."""
    analyzer = EgxDebugHtmlAnalyzer()
    try:
        result = analyzer.analyze(html_path)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1
    except OSError as exc:
        print(f"Unable to read debug HTML file: {exc}")
        return 1

    print_egx_debug_html_analysis(result)
    return 0


def print_egx_endpoint_probe_result(result: EgxEndpointProbeResult) -> None:
    """Print one EGX endpoint probe result."""
    print(f"=== EGX Endpoint Probe: {result.name} ===")
    print(f"URL: {result.url}")
    if result.status_code is not None:
        print(f"HTTP status: {result.status_code}")
    else:
        print("HTTP status: (none)")
    print(f"Content-Type: {result.content_type or '(none)'}")
    print(f"Content length: {result.content_length}")
    if result.saved_path is not None:
        try:
            saved_display = result.saved_path.relative_to(settings.PROJECT_ROOT)
        except ValueError:
            saved_display = result.saved_path
        print(f"Saved body: {saved_display}")
    else:
        print("Saved body: (none)")
    print("Preview (first 500 chars):")
    if result.preview:
        print(result.preview)
    else:
        print("(empty)")
    if result.errors:
        print("Errors:")
        for error in result.errors:
            print(f"  - {error}")
    print()


def run_probe_egx_endpoints() -> int:
    """Probe discovered public EGX endpoints."""
    probe = EgxEndpointProbe(settings.EGX_PUBLIC_DOWNLOADS_DIR)
    results = probe.probe_all()

    print("=== EGX Public Endpoint Probe ===")
    print("Discovery only — no parsing or import.")
    print()
    for result in results:
        print_egx_endpoint_probe_result(result)

    return 0 if all(result.success for result in results) else 1


def print_egx_type_probe_result(result: EgxTypeProbeResult) -> None:
    """Print one EGX type endpoint probe result."""
    print(f"=== EGX Type Probe: {result.endpoint} (type={result.type_value}) ===")
    print(f"URL: {result.url}")
    if result.status_code is not None:
        print(f"HTTP status: {result.status_code}")
    else:
        print("HTTP status: (none)")
    print(f"Content-Type: {result.content_type or '(none)'}")
    print(f"Content length: {result.content_length}")
    print(f"Response kind: {result.response_kind.value}")
    if result.saved_path is not None:
        try:
            saved_display = result.saved_path.relative_to(settings.PROJECT_ROOT)
        except ValueError:
            saved_display = result.saved_path
        print(f"Saved body: {saved_display}")
    else:
        print("Saved body: (none)")
    print("Preview (first 500 chars):")
    if result.preview:
        print(result.preview)
    else:
        print("(empty)")
    if result.errors:
        print("Errors:")
        for error in result.errors:
            print(f"  - {error}")
    print()


def run_probe_egx_types() -> int:
    """Probe public EGX endpoint variants using discovered type values."""
    probe = EgxTypeEndpointProbe(settings.EGX_PUBLIC_DOWNLOADS_DIR)
    results = probe.probe_all()

    print("=== EGX Public Type Endpoint Probe ===")
    print("Discovery only — no parsing or import.")
    print()
    for result in results:
        print_egx_type_probe_result(result)

    return 0 if all(result.success for result in results) else 1


def print_egx_browser_read_result(result: EgxBrowserReadResult) -> None:
    """Print EGX browser stocks read results."""
    print("=== EGX Browser Stocks Update ===")
    print(f"Success: {'yes' if result.success else 'no'}")
    if result.saved_csv is not None:
        try:
            saved_display = result.saved_csv.relative_to(settings.PROJECT_ROOT)
        except ValueError:
            saved_display = result.saved_csv
        print(f"Saved CSV: {saved_display}")
    else:
        print("Saved CSV: (none)")
    print(f"Rows: {result.rows}")
    if result.columns:
        print(f"Columns: {', '.join(result.columns)}")
    else:
        print("Columns: (none)")
    print("Errors:")
    if result.errors:
        for error in result.errors:
            print(f"  - {error}")
    else:
        print("  (none)")
    print("Warnings:")
    if result.warnings:
        for warning in result.warnings:
            print(f"  - {warning}")
    else:
        print("  (none)")
    print()


def run_egx_browser_stocks_update(headful: bool, import_stocks: bool) -> int:
    """Read visible EGX stocks via browser and optionally import OHLCV data."""
    reader = EgxPublicBrowserStocksReader(
        settings.EGX_PUBLIC_DOWNLOADS_DIR,
        headless=not headful,
    )
    result = reader.read_stocks_page()
    print_egx_browser_read_result(result)

    exit_code = 0 if result.success else 1
    if not import_stocks or not result.success or result.saved_csv is None:
        return exit_code

    normalized_path = (
        settings.EGX_PUBLIC_DOWNLOADS_DIR / "browser_stocks_normalized_latest.csv"
    )
    norm_result = normalize_browser_stocks_csv(result.saved_csv, normalized_path)

    print("=== EGX Browser Stocks OHLCV Normalization ===")
    print(f"Input: {result.saved_csv}")
    print(f"Output: {normalized_path}")
    print(f"Valid: {'yes' if norm_result.ohlcv.valid else 'no'}")
    print("Errors:")
    if norm_result.ohlcv.errors:
        for error in norm_result.ohlcv.errors:
            print(f"  - {error}")
    else:
        print("  (none)")
    print("Warnings:")
    if norm_result.ohlcv.warnings:
        for warning in norm_result.ohlcv.warnings:
            print(f"  - {warning}")
    else:
        print("  (none)")
    print()

    if not norm_result.ohlcv.valid:
        return 1

    import_code = run_import_daily_real_csv(
        normalized_path,
        settings.DEFAULT_REAL_EGX_CSV_PATH,
    )
    return max(exit_code, import_code)


def print_egx_attached_chrome_read_result(result: EgxBrowserReadResult) -> None:
    """Print attached Chrome EGX stocks read results."""
    print("=== EGX Attached Chrome Stocks Read ===")
    print(f"Success: {'yes' if result.success else 'no'}")
    if result.saved_csv is not None:
        try:
            saved_display = result.saved_csv.relative_to(settings.PROJECT_ROOT)
        except ValueError:
            saved_display = result.saved_csv
        print(f"Saved CSV: {saved_display}")
    else:
        print("Saved CSV: (none)")
    print(f"Rows: {result.rows}")
    if result.columns:
        print(f"Columns: {', '.join(result.columns)}")
    else:
        print("Columns: (none)")
    print("Errors:")
    if result.errors:
        for error in result.errors:
            print(f"  - {error}")
    else:
        print("  (none)")
    print("Warnings:")
    if result.warnings:
        for warning in result.warnings:
            print(f"  - {warning}")
    else:
        print("  (none)")
    print()


def run_egx_attach_chrome_stocks(cdp_url: str, import_stocks: bool) -> int:
    """Attach to user-started Chrome and optionally import OHLCV data."""
    reader = EgxAttachedChromeStocksReader(
        settings.EGX_PUBLIC_DOWNLOADS_DIR,
        cdp_url=cdp_url,
    )
    result = reader.read_current_stocks_page()
    print_egx_attached_chrome_read_result(result)

    exit_code = 0 if result.success else 1
    if not import_stocks or not result.success or result.saved_csv is None:
        return exit_code

    normalized_path = (
        settings.EGX_PUBLIC_DOWNLOADS_DIR
        / "attached_chrome_stocks_normalized_latest.csv"
    )
    norm_result = normalize_browser_stocks_csv(result.saved_csv, normalized_path)

    print("=== EGX Attached Chrome Stocks OHLCV Normalization ===")
    print(f"Input: {result.saved_csv}")
    print(f"Output: {normalized_path}")
    print(f"Valid: {'yes' if norm_result.ohlcv.valid else 'no'}")
    print("Errors:")
    if norm_result.ohlcv.errors:
        for error in norm_result.ohlcv.errors:
            print(f"  - {error}")
    else:
        print("  (none)")
    print("Warnings:")
    if norm_result.ohlcv.warnings:
        for warning in norm_result.ohlcv.warnings:
            print(f"  - {warning}")
    else:
        print("  (none)")
    print()

    if (
        norm_result.live_snapshot is not None
        and norm_result.live_snapshot.valid
        and norm_result.live_snapshot_csv is not None
    ):
        try:
            snapshot_display = norm_result.live_snapshot_csv.relative_to(
                settings.PROJECT_ROOT
            )
        except ValueError:
            snapshot_display = norm_result.live_snapshot_csv
        print(f"Live snapshot: {snapshot_display}")
        print()

    if not norm_result.ohlcv.valid:
        if (
            norm_result.live_snapshot is not None
            and norm_result.live_snapshot.valid
            and is_only_insufficient_history_failure(norm_result.ohlcv)
        ):
            print("Historical master import skipped: not enough dates yet.")
            print("Live snapshot saved successfully.")
            print()
            return exit_code
        return 1

    import_code = run_import_daily_real_csv(
        normalized_path,
        settings.DEFAULT_REAL_EGX_CSV_PATH,
    )
    return max(exit_code, import_code)


def save_current_live_snapshot_to_history(snapshot_path: Path) -> Path:
    """Copy the current live snapshot CSV into dated live history storage."""
    provider = EgxLiveSnapshotProvider(snapshot_path)
    live_snapshot = provider.load()
    store = LiveVolumeHistoryStore(settings.LIVE_HISTORY_DIR)
    return store.save_snapshot(snapshot_path, live_snapshot.as_of_date)


def run_save_live_history_only(snapshot_path: Path) -> int:
    """Save the current live snapshot into history and exit."""
    if not snapshot_path.exists():
        print(f"Error: Live snapshot not found: {snapshot_path}")
        return 1

    try:
        saved_path = save_current_live_snapshot_to_history(snapshot_path)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}")
        return 1

    try:
        saved_display = saved_path.relative_to(settings.PROJECT_ROOT)
    except ValueError:
        saved_display = saved_path
    print("=== Save Live History ===")
    print(f"Saved: {saved_display}")
    print()
    return 0


def run_live_scan_pipeline(
    snapshot_path: Path,
    *,
    lookback_days: int = settings.DEFAULT_LIVE_VOLUME_LOOKBACK_DAYS,
    min_history_days: int = settings.DEFAULT_LIVE_VOLUME_MIN_HISTORY_DAYS,
    scanner_universe: str = DEFAULT_SCANNER_UNIVERSE,
    candidate_filters: CandidateFilters | None = None,
    data_provider: str | None = None,
    quality_filters: MarketQualityFilters | None = None,
    ranking_config: CandidateRankingConfig | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
    multi_timeframe_config: MultiTimeframeConfig | None = None,
    tv_query_filter_config: TradingViewQueryFilterConfig | None = None,
    tv_query_prefilter_diagnostics: TradingViewQueryPrefilterDiagnostics | None = None,
) -> LiveScanPipelineResult | None:
    """Load live snapshot, run scanners, and return structured results."""
    if not snapshot_path.exists():
        print(f"Error: Live snapshot not found: {snapshot_path}")
        return None

    volume_store = LiveVolumeHistoryStore(settings.LIVE_HISTORY_DIR)
    provider = EgxLiveSnapshotProvider(
        snapshot_path,
        volume_history_store=volume_store,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
    )
    try:
        live_snapshot = provider.load()
    except ValueError as exc:
        print(f"Error: {exc}")
        return None

    filters = quality_filters or MarketQualityFilters()
    quality_result: MarketQualityFilterResult | None = None
    scan_symbols: list[str] | None = None
    watchlist_scanner_results: dict[str, ScannerResult] = {}

    if is_full_market_universe(scanner_universe):
        quality_frame = build_quality_filter_dataframe(live_snapshot, snapshot_path)
        quality_result = apply_market_quality_filters(quality_frame, filters)
        scan_symbols = sorted(allowed_symbols_from_quality_result(quality_result))

    market_snapshot, mood_result, adapter_warnings, market_breadth_mood_result = (
        build_live_market_snapshot(
            live_snapshot,
            volume_history_store=volume_store,
            scanner_universe=scanner_universe,
            scan_symbols=scan_symbols,
            data_provider=data_provider,
            quality_filter_result=quality_result,
            snapshot_path=snapshot_path,
        )
    )

    if is_full_market_universe(scanner_universe):
        watchlist_market_snapshot, _, watchlist_warnings, _ = build_live_market_snapshot(
            live_snapshot,
            volume_history_store=volume_store,
            scanner_universe=SCANNER_UNIVERSE_WATCHLIST,
            compute_market_mood=False,
        )
        watchlist_scanner_report = generate_scanner_report(
            mood_result,
            watchlist_market_snapshot,
        )
        watchlist_scanner_results = {
            item.symbol: item for item in watchlist_scanner_report.results
        }
        adapter_warnings.extend(watchlist_warnings)

    scanner_report = generate_scanner_report(mood_result, market_snapshot)
    strategy_report = generate_strategy_report(scanner_report, market_snapshot)
    ingest_warnings = load_ingest_warnings(settings.EGX_LIVE_INGEST_WARNINGS_PATH)
    warnings = ingest_warnings + provider.warnings + adapter_warnings
    candidate_filter_values = candidate_filters or CandidateFilters()
    ranking_values = ranking_config or CandidateRankingConfig()
    technical_values = technical_config or TechnicalConfirmationConfig()
    multi_timeframe_values = multi_timeframe_config or MultiTimeframeConfig()
    tv_query_filter_values = tv_query_filter_config or TradingViewQueryFilterConfig()

    return LiveScanPipelineResult(
        live_snapshot=live_snapshot,
        market_snapshot=market_snapshot,
        mood_result=mood_result,
        scanner_report=scanner_report,
        strategy_report=strategy_report,
        warnings=warnings,
        snapshot_path=snapshot_path,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        scanner_universe=scanner_universe,
        candidate_filters=candidate_filter_values,
        ranking_config=ranking_values,
        technical_config=technical_values,
        multi_timeframe_config=multi_timeframe_values,
        data_provider=data_provider,
        quality_filters=filters,
        quality_filter_result=quality_result,
        tv_query_filter_config=tv_query_filter_values,
        tv_query_prefilter_diagnostics=tv_query_prefilter_diagnostics,
        watchlist_scanner_results=watchlist_scanner_results,
        market_breadth_mood_result=market_breadth_mood_result,
    )


def print_live_scan_header(pipeline: LiveScanPipelineResult) -> None:
    """Print the live scan summary header."""
    try:
        snapshot_display = pipeline.snapshot_path.relative_to(settings.PROJECT_ROOT)
    except ValueError:
        snapshot_display = pipeline.snapshot_path

    print("=== EGX Live Snapshot Scanner ===")
    print(f"Data Provider: {format_data_provider_label(pipeline.data_provider)}")
    print(f"Snapshot: {snapshot_display}")
    print(f"Date: {pipeline.live_snapshot.as_of_date}")
    print(f"Symbols: {len(pipeline.live_snapshot.symbols)}")
    if pipeline.quality_filter_result is not None:
        quality = pipeline.quality_filter_result
        print(
            "Quality filtered symbols: "
            f"{quality.filtered_count} / {quality.original_count}"
        )
    print(f"Scanner universe: {format_scanner_universe_label(pipeline.scanner_universe)}")
    print(
        "Market mood: "
        f"{pipeline.mood_result.mood.value} | Score: {pipeline.mood_result.score}/100"
    )
    print("Volume history: enabled")
    print(f"Lookback days: {pipeline.lookback_days}")
    print(f"Min history days: {pipeline.min_history_days}")
    print("Warnings:")
    display_warnings = summarize_live_scan_warnings(
        pipeline.warnings,
        min_history_days=pipeline.min_history_days,
    )
    if display_warnings:
        for warning in display_warnings:
            print(f"  - {warning}")
    else:
        print("  (none)")
    print()


def run_market_hours_status(*, ignore_market_hours: bool = False) -> int:
    """Print current EGX market session status."""
    session = detect_egx_market_session(ignore_market_hours=ignore_market_hours)
    print("=== EGX Market Session ===")
    for line in format_market_session_report_lines(session):
        print(line)
    print(f"Note: {session.note}")
    return 0


def print_and_save_daily_report(
    pipeline: LiveScanPipelineResult,
    *,
    enable_portfolio_marking: bool = True,
    talib_config: TalibTechnicalConfig | None = None,
    enable_performance_analytics: bool = True,
    ignore_market_hours: bool = False,
) -> int:
    """Build, print, and save a daily report from live scan results."""
    print_live_scan_header(pipeline)
    report = DailyReportBuilder().build_from_live_scan(
        pipeline.live_snapshot,
        pipeline.mood_result,
        pipeline.scanner_report,
        pipeline.strategy_report,
        warnings=pipeline.warnings,
        scanner_universe=pipeline.scanner_universe,
        candidate_filters=pipeline.candidate_filters,
        data_provider=pipeline.data_provider,
        quality_filter_result=pipeline.quality_filter_result,
        watchlist_scanner_results=pipeline.watchlist_scanner_results,
        ranking_config=pipeline.ranking_config,
        snapshot_path=pipeline.snapshot_path,
        technical_config=pipeline.technical_config,
        multi_timeframe_config=pipeline.multi_timeframe_config,
        tv_query_filter_config=pipeline.tv_query_filter_config,
        tv_query_prefilter_diagnostics=pipeline.tv_query_prefilter_diagnostics,
        market_breadth_mood_result=pipeline.market_breadth_mood_result,
        enable_portfolio_marking=enable_portfolio_marking,
        talib_config=talib_config,
        enable_performance_analytics=enable_performance_analytics,
        ignore_market_hours=ignore_market_hours,
    )
    print(format_daily_report_text(report))
    txt_path, json_path = save_daily_report(report, settings.REPORTS_DIR)
    try:
        txt_display = txt_path.relative_to(settings.PROJECT_ROOT)
        json_display = json_path.relative_to(settings.PROJECT_ROOT)
    except ValueError:
        txt_display = txt_path
        json_display = json_path
    print(f"Report saved: {txt_display}")
    print(f"Report saved: {json_display}")
    print()
    return 0


def run_egx_daily_report(
    snapshot_path: Path,
    *,
    lookback_days: int = settings.DEFAULT_LIVE_VOLUME_LOOKBACK_DAYS,
    min_history_days: int = settings.DEFAULT_LIVE_VOLUME_MIN_HISTORY_DAYS,
    scanner_universe: str = DEFAULT_SCANNER_UNIVERSE,
    candidate_filters: CandidateFilters | None = None,
    data_provider: str | None = None,
    quality_filters: MarketQualityFilters | None = None,
    ranking_config: CandidateRankingConfig | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
    multi_timeframe_config: MultiTimeframeConfig | None = None,
    tv_query_filter_config: TradingViewQueryFilterConfig | None = None,
    tv_query_prefilter_diagnostics: TradingViewQueryPrefilterDiagnostics | None = None,
    enable_portfolio_marking: bool = True,
    talib_config: TalibTechnicalConfig | None = None,
    enable_performance_analytics: bool = True,
    ignore_market_hours: bool = False,
) -> int:
    """Build and save a daily report from the current live snapshot."""
    pipeline = run_live_scan_pipeline(
        snapshot_path,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        scanner_universe=scanner_universe,
        candidate_filters=candidate_filters,
        data_provider=data_provider,
        quality_filters=quality_filters,
        ranking_config=ranking_config,
        technical_config=technical_config,
        multi_timeframe_config=multi_timeframe_config,
        tv_query_filter_config=tv_query_filter_config,
        tv_query_prefilter_diagnostics=tv_query_prefilter_diagnostics,
    )
    if pipeline is None:
        return 1
    return print_and_save_daily_report(
        pipeline,
        enable_portfolio_marking=enable_portfolio_marking,
        talib_config=talib_config,
        enable_performance_analytics=enable_performance_analytics,
        ignore_market_hours=ignore_market_hours,
    )


def resolve_portfolio_price_map(
    snapshot_path: Path,
    *,
    use_local_snapshot: bool,
) -> tuple[dict[str, float] | None, str | None, object | None]:
    """Load latest close prices for portfolio marks when a snapshot is available."""
    if not use_local_snapshot and not snapshot_path.exists():
        return None, None, None

    if use_local_snapshot and not snapshot_path.exists():
        print(f"Error: Live snapshot not found: {snapshot_path}")
        print("Unrealized PnL will be unavailable.")
        print()
        return None, None, None

    live_snapshot = load_egx_live_snapshot(snapshot_path)
    if live_snapshot is None:
        if snapshot_path.exists():
            print("Unrealized PnL will be unavailable.")
            print()
        return None, None, None

    try:
        price_source = str(snapshot_path.relative_to(settings.PROJECT_ROOT))
    except ValueError:
        price_source = str(snapshot_path)

    return (
        build_latest_prices_from_snapshot(live_snapshot),
        price_source,
        live_snapshot.as_of_date,
    )


def run_egx_portfolio_report(
    snapshot_path: Path,
    *,
    use_local_snapshot: bool = False,
) -> int:
    """Build and save a paper portfolio report from local storage files."""
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    latest_prices, price_source, snapshot_date = resolve_portfolio_price_map(
        snapshot_path,
        use_local_snapshot=use_local_snapshot,
    )
    report = PortfolioReportBuilder().build(
        portfolio,
        journal,
        latest_prices=latest_prices,
        price_source=price_source,
        snapshot_date=snapshot_date,
    )
    print(format_portfolio_report_text(report))
    txt_path, json_path = save_portfolio_report(report, settings.REPORTS_DIR)
    try:
        txt_display = txt_path.relative_to(settings.PROJECT_ROOT)
        json_display = json_path.relative_to(settings.PROJECT_ROOT)
    except ValueError:
        txt_display = txt_path
        json_display = json_path
    print(f"Portfolio report saved: {txt_display}")
    print(f"Portfolio report saved: {json_display}")
    print()
    return 0


def print_live_paper_trading_report(
    report: LivePaperTradingReport,
    strategy_report: StrategyReport,
    *,
    max_trades: int,
    min_confidence: int,
) -> None:
    """Print live paper trading results."""
    print("=== EGX Live Paper Trading ===")
    print(f"BUY_SETUP signals: {len(strategy_report.buy_setups)}")
    print(f"Max trades: {max_trades}")
    print(f"Min confidence: {min_confidence}")
    print()

    opened = [item for item in report.results if item.decision.value == "OPENED"]
    skipped = [item for item in report.results if item.decision.value == "SKIPPED"]
    rejected = [item for item in report.results if item.decision.value == "REJECTED"]

    print("OPENED:")
    if opened:
        for item in opened:
            risk_text = (
                f"{item.risk_amount:,.2f}"
                if item.risk_amount is not None
                else "N/A"
            )
            print(
                f"- {item.symbol} | Entry {item.entry_price:.2f} | "
                f"Stop {item.stop_loss:.2f} | TP {item.take_profit:.2f} | "
                f"Qty {item.quantity} | Risk {risk_text}"
            )
    else:
        print("- (none)")
    print()

    print("SKIPPED:")
    if skipped:
        for item in skipped:
            print(f"- {item.symbol} | {item.reason}")
    else:
        print("- (none)")
    print()

    print("REJECTED:")
    if rejected:
        for item in rejected:
            print(f"- {item.symbol} | {item.reason}")
    else:
        print("- (none)")
    print()

    if report.warnings:
        print("Warnings:")
        for warning in report.warnings:
            print(f"- {warning}")
        print()


def run_egx_live_paper_trade(
    snapshot_path: Path,
    *,
    lookback_days: int = settings.DEFAULT_LIVE_VOLUME_LOOKBACK_DAYS,
    min_history_days: int = settings.DEFAULT_LIVE_VOLUME_MIN_HISTORY_DAYS,
    max_trades: int = 3,
    min_confidence: int = 75,
    reset_paper_state_flag: bool = False,
    scanner_universe: str = DEFAULT_SCANNER_UNIVERSE,
    candidate_filters: CandidateFilters | None = None,
    data_provider: str | None = None,
    quality_filters: MarketQualityFilters | None = None,
    ranking_config: CandidateRankingConfig | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
    ignore_market_hours: bool = False,
) -> int:
    """Run live scan and open paper trades from BUY_SETUP signals."""
    maybe_reset_paper_state(reset_paper_state_flag)
    pipeline = run_live_scan_pipeline(
        snapshot_path,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        scanner_universe=scanner_universe,
        candidate_filters=candidate_filters,
        data_provider=data_provider,
        quality_filters=quality_filters,
        ranking_config=ranking_config,
        technical_config=technical_config,
    )
    if pipeline is None:
        return 1

    trade_report = _open_live_paper_trades_from_pipeline(
        pipeline,
        max_trades=max_trades,
        min_confidence=min_confidence,
        ignore_market_hours=ignore_market_hours,
    )
    print_live_paper_trading_report(
        trade_report,
        _filtered_strategy_report(pipeline),
        max_trades=max_trades,
        min_confidence=min_confidence,
    )
    return 0


def run_egx_one_click_paper_trade(
    cdp_url: str,
    chrome_profile_dir: Path | None,
    *,
    lookback_days: int = settings.DEFAULT_LIVE_VOLUME_LOOKBACK_DAYS,
    min_history_days: int = settings.DEFAULT_LIVE_VOLUME_MIN_HISTORY_DAYS,
    max_trades: int = 3,
    min_confidence: int = 75,
    reset_paper_state_flag: bool = False,
    scanner_universe: str = DEFAULT_SCANNER_UNIVERSE,
    candidate_filters: CandidateFilters | None = None,
    data_provider: str = DEFAULT_DATA_PROVIDER,
    quality_filters: MarketQualityFilters | None = None,
    ranking_config: CandidateRankingConfig | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
    ignore_market_hours: bool = False,
) -> int:
    """Run one-click EGX update and open paper trades from live scan."""
    maybe_reset_paper_state(reset_paper_state_flag)
    exit_code, provider_used, _prefilter_diag = run_market_snapshot_update(
        data_provider,
        cdp_url,
        chrome_profile_dir,
        header="=== EGX One-Click Paper Trade ===",
    )
    if exit_code != 0:
        return 1

    _save_live_history_or_warn(settings.EGX_LIVE_SNAPSHOT_PATH)

    return run_egx_live_paper_trade(
        settings.EGX_LIVE_SNAPSHOT_PATH,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        max_trades=max_trades,
        min_confidence=min_confidence,
        scanner_universe=scanner_universe,
        candidate_filters=candidate_filters,
        data_provider=provider_used,
        quality_filters=quality_filters,
        ranking_config=ranking_config,
        technical_config=technical_config,
        ignore_market_hours=ignore_market_hours,
    )


def load_egx_live_snapshot(snapshot_path: Path) -> LiveMarketSnapshot | None:
    """Load a live EGX snapshot CSV or print an error and return None."""
    if not snapshot_path.exists():
        print(f"Error: Live snapshot not found: {snapshot_path}")
        return None

    provider = EgxLiveSnapshotProvider(snapshot_path)
    try:
        return provider.load()
    except ValueError as exc:
        print(f"Error: {exc}")
        return None


def print_live_paper_monitor_report(report: LivePaperMonitorReport) -> None:
    """Print live paper trade monitor results."""
    open_trades_checked = len(report.results)
    print("=== EGX Live Paper Monitor ===")
    print(f"Open trades checked: {open_trades_checked}")
    print(f"Closed: {report.closed_count}")
    print(f"Held: {report.held_count}")
    print(f"Errors: {report.error_count}")
    print()

    closed = [
        item for item in report.results if item.decision.value == "CLOSED"
    ]
    held = [item for item in report.results if item.decision.value == "HELD"]
    errors = [item for item in report.results if item.decision.value == "ERROR"]

    print("CLOSED:")
    if closed:
        for item in closed:
            pnl_text = ""
            if item.pnl is not None:
                pnl_text = f" | {format_pnl_line(item.pnl, item.pnl_percent)}"
            print(
                f"- {item.symbol} | {item.reason.value} | "
                f"Entry {item.entry_price:.2f} | Exit {item.exit_price:.2f}{pnl_text}"
            )
    else:
        print("- (none)")
    print()

    print("HELD:")
    if held:
        for item in held:
            current_text = (
                f"{item.current_price:.2f}"
                if item.current_price is not None
                else "N/A"
            )
            print(
                f"- {item.symbol} | {item.reason.value} | "
                f"Entry {item.entry_price:.2f} | Current {current_text} | "
                f"Stop {item.stop_loss:.2f} | TP {item.take_profit:.2f}"
            )
    else:
        print("- (none)")
    print()

    print("ERRORS:")
    if errors:
        for item in errors:
            print(f"- {item.symbol} | monitor error")
    else:
        print("- (none)")
    print()

    if report.warnings:
        print("Warnings:")
        for warning in report.warnings:
            print(f"- {warning}")
        print()


def run_egx_live_paper_monitor(
    snapshot_path: Path,
    *,
    reset_paper_state_flag: bool = False,
) -> int:
    """Monitor open paper trades against the EGX live snapshot."""
    maybe_reset_paper_state(reset_paper_state_flag)
    live_snapshot = load_egx_live_snapshot(snapshot_path)
    if live_snapshot is None:
        return 1

    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    monitor = LivePaperMonitor(portfolio=portfolio, trade_journal=journal)
    report = monitor.monitor_from_live_snapshot(live_snapshot)
    print_live_paper_monitor_report(report)
    return 0


def run_egx_one_click_paper_monitor(
    cdp_url: str,
    chrome_profile_dir: Path | None,
    *,
    reset_paper_state_flag: bool = False,
    data_provider: str = DEFAULT_DATA_PROVIDER,
) -> int:
    """Run one-click EGX update and monitor open paper trades."""
    maybe_reset_paper_state(reset_paper_state_flag)
    exit_code, _provider_used, _prefilter_diag = run_market_snapshot_update(
        data_provider,
        cdp_url,
        chrome_profile_dir,
        header="=== EGX One-Click Paper Monitor ===",
    )
    if exit_code != 0:
        return 1

    _save_live_history_or_warn(settings.EGX_LIVE_SNAPSHOT_PATH)

    return run_egx_live_paper_monitor(settings.EGX_LIVE_SNAPSHOT_PATH)


def run_egx_one_click_paper_cycle(
    cdp_url: str,
    chrome_profile_dir: Path | None,
    *,
    lookback_days: int = settings.DEFAULT_LIVE_VOLUME_LOOKBACK_DAYS,
    min_history_days: int = settings.DEFAULT_LIVE_VOLUME_MIN_HISTORY_DAYS,
    max_trades: int = 3,
    min_confidence: int = 75,
    reset_paper_state_flag: bool = False,
    scanner_universe: str = DEFAULT_SCANNER_UNIVERSE,
    candidate_filters: CandidateFilters | None = None,
    data_provider: str = DEFAULT_DATA_PROVIDER,
    quality_filters: MarketQualityFilters | None = None,
    ranking_config: CandidateRankingConfig | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
    ignore_market_hours: bool = False,
) -> int:
    """Update EGX snapshot, monitor open trades, then open new paper trades."""
    maybe_reset_paper_state(reset_paper_state_flag)
    exit_code, provider_used, _prefilter_diag = run_market_snapshot_update(
        data_provider,
        cdp_url,
        chrome_profile_dir,
        header="=== EGX One-Click Paper Cycle ===",
    )
    if exit_code != 0:
        return 1

    _save_live_history_or_warn(settings.EGX_LIVE_SNAPSHOT_PATH)

    snapshot_path = settings.EGX_LIVE_SNAPSHOT_PATH
    live_snapshot = load_egx_live_snapshot(snapshot_path)
    if live_snapshot is None:
        return 1

    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    monitor = LivePaperMonitor(portfolio=portfolio, trade_journal=journal)
    monitor_report = monitor.monitor_from_live_snapshot(live_snapshot)
    print_live_paper_monitor_report(monitor_report)

    pipeline = run_live_scan_pipeline(
        snapshot_path,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        scanner_universe=scanner_universe,
        candidate_filters=candidate_filters,
        data_provider=provider_used,
        quality_filters=quality_filters,
        ranking_config=ranking_config,
        technical_config=technical_config,
    )
    if pipeline is None:
        return 1

    if is_full_market_universe(pipeline.scanner_universe):
        print(f"Full-market paper trading enabled; max trades limit is {max_trades}.")
        print()

    trader = LivePaperTrader(
        portfolio=portfolio,
        trade_journal=journal,
        risk_manager=RiskManager(),
        max_trades_per_run=max_trades,
        min_confidence_score=min_confidence,
        ignore_market_hours=ignore_market_hours,
    )
    trade_report = trader.trade_from_strategy_report(_filtered_strategy_report(pipeline))
    print_live_paper_trading_report(
        trade_report,
        _filtered_strategy_report(pipeline),
        max_trades=max_trades,
        min_confidence=min_confidence,
    )
    return 0


def run_egx_live_scan(
    snapshot_path: Path,
    *,
    lookback_days: int = settings.DEFAULT_LIVE_VOLUME_LOOKBACK_DAYS,
    min_history_days: int = settings.DEFAULT_LIVE_VOLUME_MIN_HISTORY_DAYS,
    scanner_universe: str = DEFAULT_SCANNER_UNIVERSE,
    candidate_filters: CandidateFilters | None = None,
    data_provider: str | None = None,
    quality_filters: MarketQualityFilters | None = None,
    ranking_config: CandidateRankingConfig | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
) -> int:
    """Run Scanner A and Strategy Scanner B from a live EGX snapshot CSV."""
    pipeline = run_live_scan_pipeline(
        snapshot_path,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        scanner_universe=scanner_universe,
        candidate_filters=candidate_filters,
        data_provider=data_provider,
        quality_filters=quality_filters,
        ranking_config=ranking_config,
        technical_config=technical_config,
    )
    if pipeline is None:
        return 1

    ranking_frame = build_candidate_ranking_dataframe(
        pipeline.live_snapshot,
        pipeline.snapshot_path,
    )
    print_live_scan_header(pipeline)
    _print_scanner_report_results(
        pipeline.scanner_report,
        candidate_filters=pipeline.candidate_filters,
        live_snapshot=pipeline.live_snapshot,
        snapshot_path=pipeline.snapshot_path,
        ranking_config=pipeline.ranking_config,
        strategy_report=pipeline.strategy_report,
        technical_config=pipeline.technical_config,
    )
    filtered_strategy = _filtered_strategy_report(pipeline)
    _print_strategy_report_results(
        filtered_strategy,
        scanner_report=pipeline.scanner_report,
        candidate_filters=pipeline.candidate_filters,
        snapshot_df=ranking_frame,
        ranking_config=pipeline.ranking_config,
        technical_config=pipeline.technical_config,
    )
    return 0


def run_egx_live_scan_after_history_save(
    snapshot_path: Path,
    *,
    lookback_days: int,
    min_history_days: int,
    scanner_universe: str = DEFAULT_SCANNER_UNIVERSE,
    candidate_filters: CandidateFilters | None = None,
    data_provider: str | None = None,
    quality_filters: MarketQualityFilters | None = None,
    ranking_config: CandidateRankingConfig | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
) -> int:
    """Save live snapshot history, then run the live scanner."""
    try:
        saved_path = save_current_live_snapshot_to_history(snapshot_path)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Warning: Live history was not saved: {exc}")
    else:
        try:
            saved_display = saved_path.relative_to(settings.PROJECT_ROOT)
        except ValueError:
            saved_display = saved_path
        print(f"Live history saved: {saved_display}")
        print()

    return run_egx_live_scan(
        snapshot_path,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        scanner_universe=scanner_universe,
        candidate_filters=candidate_filters,
        data_provider=data_provider,
        quality_filters=quality_filters,
        ranking_config=ranking_config,
        technical_config=technical_config,
    )


def run_egx_update_and_live_scan(
    cdp_url: str,
    *,
    lookback_days: int = settings.DEFAULT_LIVE_VOLUME_LOOKBACK_DAYS,
    min_history_days: int = settings.DEFAULT_LIVE_VOLUME_MIN_HISTORY_DAYS,
    scanner_universe: str = DEFAULT_SCANNER_UNIVERSE,
    candidate_filters: CandidateFilters | None = None,
) -> int:
    """Attach to Chrome, refresh live snapshot, and run live scan."""
    reader = EgxAttachedChromeStocksReader(
        settings.EGX_PUBLIC_DOWNLOADS_DIR,
        cdp_url=cdp_url,
    )
    result = reader.read_current_stocks_page()
    print_egx_attached_chrome_read_result(result)

    if not result.success:
        if result.errors:
            print("Error: Attached Chrome read failed.")
            for error in result.errors:
                print(f"  - {error}")
        else:
            print("Error: Attached Chrome read failed.")
        return 1

    if result.saved_csv is None:
        print("Error: No visible EGX stocks table was saved.")
        return 1

    normalized_path = (
        settings.EGX_PUBLIC_DOWNLOADS_DIR
        / "attached_chrome_stocks_normalized_latest.csv"
    )
    norm_result = normalize_browser_stocks_csv(
        result.saved_csv,
        normalized_path,
        settings.EGX_LIVE_SNAPSHOT_PATH,
    )

    print("=== EGX Attached Chrome Live Snapshot Update ===")
    print(f"Input: {result.saved_csv}")
    if norm_result.live_snapshot_csv is not None:
        try:
            snapshot_display = norm_result.live_snapshot_csv.relative_to(
                settings.PROJECT_ROOT
            )
        except ValueError:
            snapshot_display = norm_result.live_snapshot_csv
        print(f"Live snapshot: {snapshot_display}")
    print(
        f"Live snapshot valid: "
        f"{'yes' if norm_result.live_snapshot and norm_result.live_snapshot.valid else 'no'}"
    )
    if norm_result.live_snapshot and norm_result.live_snapshot.errors:
        print("Errors:")
        for error in norm_result.live_snapshot.errors:
            print(f"  - {error}")
    print()

    if norm_result.live_snapshot is None or not norm_result.live_snapshot.valid:
        print("Error: Live snapshot was not saved successfully.")
        return 1

    print("Live snapshot saved successfully.")
    print()
    return run_egx_live_scan_after_history_save(
        settings.EGX_LIVE_SNAPSHOT_PATH,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        scanner_universe=scanner_universe,
        candidate_filters=candidate_filters,
    )


def parse_cdp_port(cdp_url: str) -> int:
    """Extract the remote debugging port from a CDP URL."""
    parsed = urlparse(cdp_url)
    return parsed.port or 9222


def run_show_egx_symbol_mapping() -> int:
    """Print configured EGX company-name to ticker symbol mappings."""
    from collections import defaultdict

    from config.egx_symbol_map import EGX_COMPANY_NAME_TO_SYMBOL

    by_symbol: dict[str, list[str]] = defaultdict(list)
    for company_name, symbol in EGX_COMPANY_NAME_TO_SYMBOL.items():
        by_symbol[symbol].append(company_name)

    print("=== EGX Symbol Mapping ===")
    print()
    for symbol in sorted(by_symbol):
        print(f"{symbol}:")
        for company_name in sorted(by_symbol[symbol]):
            print(f"  - {company_name}")
        print()
    return 0


def _save_live_history_or_warn(snapshot_path: Path) -> None:
    """Save the current live snapshot to dated history or print a warning."""
    try:
        saved_path = save_current_live_snapshot_to_history(snapshot_path)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Warning: Live history was not saved: {exc}")
        return

    try:
        saved_display = saved_path.relative_to(settings.PROJECT_ROOT)
    except ValueError:
        saved_display = saved_path
    print(f"Live history saved: {saved_display}")
    print()


def run_tradingview_snapshot_update(
    *,
    header: str | None = None,
    query_filter_config: TradingViewQueryFilterConfig | None = None,
) -> tuple[int, TradingViewQueryPrefilterDiagnostics | None]:
    """Fetch TradingView Egypt market data and save the normalized live snapshot."""
    if header:
        print(header)
        print()
    print(f"Data Provider: {format_data_provider_label(DATA_PROVIDER_TRADINGVIEW)}")

    result = fetch_and_save_tradingview_snapshot(
        settings.EGX_LIVE_SNAPSHOT_PATH,
        query_filter_config,
    )
    if not result.success:
        for error in result.errors:
            print(f"Error: {error}")
        return 1, result.query_prefilter_diagnostics

    print_tradingview_snapshot_summary(result)
    save_ingest_warnings(settings.EGX_LIVE_INGEST_WARNINGS_PATH, result.warnings)
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    print()
    return 0, result.query_prefilter_diagnostics


def run_market_snapshot_update(
    data_provider: str,
    cdp_url: str,
    chrome_profile_dir: Path | None,
    *,
    header: str = "=== EGX Market Snapshot Update ===",
    query_filter_config: TradingViewQueryFilterConfig | None = None,
) -> tuple[int, str, TradingViewQueryPrefilterDiagnostics | None]:
    """Refresh the live snapshot using the selected market data provider."""
    provider = data_provider or DEFAULT_DATA_PROVIDER

    if provider == DATA_PROVIDER_EGX:
        code = run_egx_one_click_snapshot_update(
            cdp_url,
            chrome_profile_dir,
            header=header,
        )
        return code, DATA_PROVIDER_EGX, None

    if provider == DATA_PROVIDER_TRADINGVIEW:
        code, diagnostics = run_tradingview_snapshot_update(
            header=header,
            query_filter_config=query_filter_config,
        )
        return code, DATA_PROVIDER_TRADINGVIEW, diagnostics

    print(header)
    print()
    print(
        "Data Provider: auto "
        f"(trying {format_data_provider_label(DATA_PROVIDER_TRADINGVIEW)} first)"
    )
    tv_result = fetch_and_save_tradingview_snapshot(
        settings.EGX_LIVE_SNAPSHOT_PATH,
        query_filter_config,
    )
    if tradingview_snapshot_is_usable(tv_result):
        print(f"Data Provider: {format_data_provider_label(DATA_PROVIDER_TRADINGVIEW)}")
        print_tradingview_snapshot_summary(tv_result)
        save_ingest_warnings(settings.EGX_LIVE_INGEST_WARNINGS_PATH, tv_result.warnings)
        if tv_result.warnings:
            print("Warnings:")
            for warning in tv_result.warnings:
                print(f"- {warning}")
        print()
        return 0, DATA_PROVIDER_TRADINGVIEW, tv_result.query_prefilter_diagnostics

    if tv_result.errors:
        for error in tv_result.errors:
            print(f"Error: {error}")
    print(f"Warning: {AUTO_FALLBACK_TO_EGX_WARNING}")
    print()
    code = run_egx_one_click_snapshot_update(
        cdp_url,
        chrome_profile_dir,
        header="=== EGX Chrome Fallback Snapshot Update ===",
    )
    return code, DATA_PROVIDER_EGX, None


def run_egx_one_click_snapshot_update(
    cdp_url: str,
    chrome_profile_dir: Path | None,
    *,
    header: str = "=== EGX One-Click Live Scan ===",
) -> int:
    """Launch Chrome if needed and refresh the live EGX snapshot CSV."""
    print(header)
    print()
    print(f"Data Provider: {format_data_provider_label(DATA_PROVIDER_EGX)}")

    launcher = ChromeRemoteDebugLauncher()
    launch_result = launcher.launch_chrome_remote_debugging(
        cdp_port=parse_cdp_port(cdp_url),
        user_data_dir=chrome_profile_dir,
    )

    if not launch_result.success:
        for error in launch_result.errors:
            print(error)
        return 1

    print(
        "Chrome remote debugging: "
        + ("ready" if launch_result.already_running else "launched")
    )

    reader = EgxAttachedChromeStocksReader(
        settings.EGX_PUBLIC_DOWNLOADS_DIR,
        cdp_url=launch_result.cdp_url,
    )
    result = reader.read_or_open_stocks_page()

    if not result.success:
        if result.page_action == "opened":
            print("EGX page: open failed")
        elif result.page_action == "selected":
            print("EGX page: selected but table missing")
        else:
            print("EGX page: unavailable")
        for error in result.errors:
            print(f"Error: {error}")
        return 1

    page_label = "opened" if result.page_action == "opened" else "selected"
    print(f"EGX page: {page_label}")
    print("Stocks table: read successfully")
    print(f"Rows: {result.rows}")

    if result.saved_csv is None:
        print("Error: No visible EGX stocks table was saved.")
        return 1

    normalized_path = (
        settings.EGX_PUBLIC_DOWNLOADS_DIR
        / "attached_chrome_stocks_normalized_latest.csv"
    )
    norm_result = normalize_browser_stocks_csv(
        result.saved_csv,
        normalized_path,
        settings.EGX_LIVE_SNAPSHOT_PATH,
    )

    if norm_result.live_snapshot is None or not norm_result.live_snapshot.valid:
        print("Error: Live snapshot was not saved successfully.")
        if norm_result.live_snapshot and norm_result.live_snapshot.errors:
            for error in norm_result.live_snapshot.errors:
                print(f"  - {error}")
        return 1

    try:
        snapshot_display = settings.EGX_LIVE_SNAPSHOT_PATH.relative_to(
            settings.PROJECT_ROOT
        )
    except ValueError:
        snapshot_display = settings.EGX_LIVE_SNAPSHOT_PATH
    print(f"Live snapshot: {snapshot_display}")
    print(f"Valid symbols: {norm_result.valid_symbol_count}")
    if norm_result.symbol_mapping is not None:
        mapping = norm_result.symbol_mapping
        print(
            "EGX symbol mapping: "
            f"mapped {mapping.mapped_rows} rows, unmapped {mapping.unmapped_rows} rows."
        )

    combined_warnings = list(result.warnings) + norm_result.validation_warnings
    if norm_result.live_snapshot is not None:
        combined_warnings.extend(norm_result.live_snapshot.warnings)
    save_ingest_warnings(settings.EGX_LIVE_INGEST_WARNINGS_PATH, combined_warnings)

    if combined_warnings:
        print("Warnings:")
        for warning in combined_warnings:
            print(f"- {warning}")
    print()
    return 0


def run_egx_one_click_live_scan(
    cdp_url: str,
    chrome_profile_dir: Path | None,
    *,
    lookback_days: int = settings.DEFAULT_LIVE_VOLUME_LOOKBACK_DAYS,
    min_history_days: int = settings.DEFAULT_LIVE_VOLUME_MIN_HISTORY_DAYS,
    scanner_universe: str = DEFAULT_SCANNER_UNIVERSE,
    candidate_filters: CandidateFilters | None = None,
    data_provider: str = DEFAULT_DATA_PROVIDER,
    quality_filters: MarketQualityFilters | None = None,
    ranking_config: CandidateRankingConfig | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
) -> int:
    """Launch Chrome if needed, refresh live snapshot, and run live scan."""
    exit_code, provider_used, _prefilter_diag = run_market_snapshot_update(
        data_provider,
        cdp_url,
        chrome_profile_dir,
        header="=== EGX One-Click Live Scan ===",
    )
    if exit_code != 0:
        return 1

    return run_egx_live_scan_after_history_save(
        settings.EGX_LIVE_SNAPSHOT_PATH,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        scanner_universe=scanner_universe,
        candidate_filters=candidate_filters,
        data_provider=provider_used,
        quality_filters=quality_filters,
        ranking_config=ranking_config,
        technical_config=technical_config,
    )


def run_egx_one_click_daily_report(
    cdp_url: str,
    chrome_profile_dir: Path | None,
    *,
    lookback_days: int = settings.DEFAULT_LIVE_VOLUME_LOOKBACK_DAYS,
    min_history_days: int = settings.DEFAULT_LIVE_VOLUME_MIN_HISTORY_DAYS,
    scanner_universe: str = DEFAULT_SCANNER_UNIVERSE,
    candidate_filters: CandidateFilters | None = None,
    data_provider: str = DEFAULT_DATA_PROVIDER,
    quality_filters: MarketQualityFilters | None = None,
    ranking_config: CandidateRankingConfig | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
    multi_timeframe_config: MultiTimeframeConfig | None = None,
    tv_query_filter_config: TradingViewQueryFilterConfig | None = None,
    enable_portfolio_marking: bool = True,
    talib_config: TalibTechnicalConfig | None = None,
    enable_performance_analytics: bool = True,
    ignore_market_hours: bool = False,
) -> int:
    """Run one-click EGX update, save history, and build a daily report."""
    exit_code, provider_used, prefilter_diag = run_market_snapshot_update(
        data_provider,
        cdp_url,
        chrome_profile_dir,
        header="=== EGX One-Click Daily Report ===",
        query_filter_config=tv_query_filter_config,
    )
    if exit_code != 0:
        return 1

    _save_live_history_or_warn(settings.EGX_LIVE_SNAPSHOT_PATH)

    return run_egx_daily_report(
        settings.EGX_LIVE_SNAPSHOT_PATH,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        scanner_universe=scanner_universe,
        candidate_filters=candidate_filters,
        data_provider=provider_used,
        quality_filters=quality_filters,
        ranking_config=ranking_config,
        technical_config=technical_config,
        multi_timeframe_config=multi_timeframe_config,
        tv_query_filter_config=tv_query_filter_config,
        tv_query_prefilter_diagnostics=prefilter_diag,
        enable_portfolio_marking=enable_portfolio_marking,
        talib_config=talib_config,
        enable_performance_analytics=enable_performance_analytics,
        ignore_market_hours=ignore_market_hours,
    )


def run_egx_workflow(
    workflow: str,
    *,
    cdp_url: str,
    chrome_profile_dir: Path | None,
    snapshot_path: Path,
    use_local_snapshot: bool,
    lookback_days: int,
    min_history_days: int,
    max_trades: int,
    min_confidence: int,
    reset_paper_state_flag: bool,
    scanner_universe: str = DEFAULT_SCANNER_UNIVERSE,
    candidate_filters: CandidateFilters | None = None,
    data_provider: str = DEFAULT_DATA_PROVIDER,
    quality_filters: MarketQualityFilters | None = None,
    ranking_config: CandidateRankingConfig | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
    multi_timeframe_config: MultiTimeframeConfig | None = None,
    tv_query_filter_config: TradingViewQueryFilterConfig | None = None,
    enable_portfolio_marking: bool = True,
    talib_config: TalibTechnicalConfig | None = None,
    enable_performance_analytics: bool = True,
    ignore_market_hours: bool = False,
) -> int:
    """Run a unified EGX workflow using one-click or local snapshot mode."""
    if workflow == "portfolio":
        return run_egx_portfolio_report(
            snapshot_path,
            use_local_snapshot=use_local_snapshot or snapshot_path.exists(),
        )

    if workflow == "report":
        if use_local_snapshot:
            return run_egx_daily_report(
                snapshot_path,
                lookback_days=lookback_days,
                min_history_days=min_history_days,
                scanner_universe=scanner_universe,
                candidate_filters=candidate_filters,
                data_provider=None,
                quality_filters=quality_filters,
                ranking_config=ranking_config,
                technical_config=technical_config,
                multi_timeframe_config=multi_timeframe_config,
                tv_query_filter_config=tv_query_filter_config,
                enable_portfolio_marking=enable_portfolio_marking,
                talib_config=talib_config,
                enable_performance_analytics=enable_performance_analytics,
                ignore_market_hours=ignore_market_hours,
            )
        return run_egx_one_click_daily_report(
            cdp_url,
            chrome_profile_dir,
            lookback_days=lookback_days,
            min_history_days=min_history_days,
            scanner_universe=scanner_universe,
            candidate_filters=candidate_filters,
            data_provider=data_provider,
            quality_filters=quality_filters,
            ranking_config=ranking_config,
            technical_config=technical_config,
            multi_timeframe_config=multi_timeframe_config,
            tv_query_filter_config=tv_query_filter_config,
            enable_portfolio_marking=enable_portfolio_marking,
            talib_config=talib_config,
            enable_performance_analytics=enable_performance_analytics,
            ignore_market_hours=ignore_market_hours,
        )

    if workflow == "scan":
        if use_local_snapshot:
            return run_egx_live_scan(
                snapshot_path,
                lookback_days=lookback_days,
                min_history_days=min_history_days,
                scanner_universe=scanner_universe,
                candidate_filters=candidate_filters,
                data_provider=None,
                quality_filters=quality_filters,
                ranking_config=ranking_config,
                technical_config=technical_config,
            )
        return run_egx_one_click_live_scan(
            cdp_url,
            chrome_profile_dir,
            lookback_days=lookback_days,
            min_history_days=min_history_days,
            scanner_universe=scanner_universe,
            candidate_filters=candidate_filters,
            data_provider=data_provider,
            quality_filters=quality_filters,
            ranking_config=ranking_config,
            technical_config=technical_config,
        )

    if workflow == "monitor":
        if use_local_snapshot:
            return run_egx_live_paper_monitor(
                snapshot_path,
                reset_paper_state_flag=reset_paper_state_flag,
            )
        return run_egx_one_click_paper_monitor(
            cdp_url,
            chrome_profile_dir,
            reset_paper_state_flag=reset_paper_state_flag,
            data_provider=data_provider,
        )

    if workflow == "trade":
        if use_local_snapshot:
            return run_egx_live_paper_trade(
                snapshot_path,
                lookback_days=lookback_days,
                min_history_days=min_history_days,
                max_trades=max_trades,
                min_confidence=min_confidence,
                reset_paper_state_flag=reset_paper_state_flag,
                scanner_universe=scanner_universe,
                candidate_filters=candidate_filters,
                data_provider=None,
                quality_filters=quality_filters,
                ranking_config=ranking_config,
                technical_config=technical_config,
                ignore_market_hours=ignore_market_hours,
            )
        return run_egx_one_click_paper_trade(
            cdp_url,
            chrome_profile_dir,
            lookback_days=lookback_days,
            min_history_days=min_history_days,
            max_trades=max_trades,
            min_confidence=min_confidence,
            reset_paper_state_flag=reset_paper_state_flag,
            scanner_universe=scanner_universe,
            candidate_filters=candidate_filters,
            data_provider=data_provider,
            quality_filters=quality_filters,
            ranking_config=ranking_config,
            technical_config=technical_config,
            ignore_market_hours=ignore_market_hours,
        )

    return run_egx_one_click_paper_cycle(
        cdp_url,
        chrome_profile_dir,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        max_trades=max_trades,
        min_confidence=min_confidence,
        reset_paper_state_flag=reset_paper_state_flag,
        scanner_universe=scanner_universe,
        candidate_filters=candidate_filters,
        data_provider=data_provider,
        quality_filters=quality_filters,
        ranking_config=ranking_config,
        technical_config=technical_config,
        ignore_market_hours=ignore_market_hours,
    )


def parse_egx_company_prefixes(prefixes_raw: str | None) -> list[str]:
    """Parse comma-separated EGX company search prefixes."""
    if not prefixes_raw:
        return list(DEFAULT_EGX_COMPANY_PREFIXES)
    return [part.strip() for part in prefixes_raw.split(",") if part.strip()]


def print_egx_company_prices_result(result: EgxCompanyPricesReadResult) -> None:
    """Print EGX company prices fetch results."""
    print("=== EGX Company Prices ===")
    print(f"Prefixes: {', '.join(result.prefixes)}")
    print(f"Rows: {len(result.records)}")
    if result.saved_csv is not None:
        try:
            saved_display = result.saved_csv.relative_to(settings.PROJECT_ROOT)
        except ValueError:
            saved_display = result.saved_csv
        print(f"Saved CSV: {saved_display}")
    else:
        print("Saved CSV: (none)")
    if result.errors:
        print("Errors:")
        for error in result.errors:
            print(f"  - {error}")
    print("Note: Saved for discovery only. No import into master file yet.")


def run_egx_company_prices(prefixes_raw: str | None) -> int:
    """Fetch public EGX company prices and save CSV snapshot."""
    prefixes = parse_egx_company_prefixes(prefixes_raw)
    reader = EgxCompanyPricesReader(settings.EGX_PUBLIC_DOWNLOADS_DIR)
    result = reader.read_and_save(prefixes)
    print_egx_company_prices_result(result)

    if result.records:
        return 0
    return 1


def print_egx_probe_body_analysis(result: EgxProbeBodyAnalysis) -> None:
    """Print EGX probe body analysis results."""
    print("=== EGX Probe Body Analysis ===")
    try:
        file_display = result.probe_path.relative_to(settings.PROJECT_ROOT)
    except ValueError:
        file_display = result.probe_path
    print(f"File: {file_display}")
    print()
    _print_item_group("window[...] variable names:", result.window_variable_names)
    _print_item_group("Large JS assignments:", result.large_js_assignments)
    _print_item_group("URLs inside scripts:", result.script_urls)
    _print_item_group("Query parameters:", result.query_parameters)
    _print_item_group("Keyword occurrences:", result.keyword_occurrences)
    print("Note: Local file analysis only. No network requests were made.")


def run_analyze_egx_probe_file(probe_path: Path) -> int:
    """Analyze a local EGX probe response file."""
    analyzer = EgxProbeBodyAnalyzer()
    try:
        result = analyzer.analyze(probe_path)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1
    except OSError as exc:
        print(f"Unable to read probe file: {exc}")
        return 1

    print_egx_probe_body_analysis(result)
    return 0


def print_egx_public_read_result(result: EgxPublicReadResult) -> None:
    """Print one EGX public page read result."""
    print(f"=== EGX Public Page: {result.page_type.value} ===")
    print(f"URL: {result.url}")
    print(f"Success: {'yes' if result.success else 'no'}")
    if result.saved_csv is not None:
        try:
            saved_display = result.saved_csv.relative_to(settings.PROJECT_ROOT)
        except ValueError:
            saved_display = result.saved_csv
        print(f"Saved CSV: {saved_display}")
    else:
        print("Saved CSV: (none)")
    print(f"Rows: {result.rows}")
    if result.columns:
        print(f"Columns: {', '.join(result.columns)}")
    else:
        print("Columns: (none)")
    print("Errors:")
    if result.errors:
        for error in result.errors:
            print(f"  - {error}")
    else:
        print("  (none)")
    print("Warnings:")
    if result.warnings:
        for warning in result.warnings:
            print(f"  - {warning}")
    else:
        print("  (none)")
    print()


def run_egx_public_update(page: str, import_egx_stocks: bool) -> int:
    """Read public EGX pages and optionally import stocks OHLCV data."""
    reader = EgxPublicMarketWatchReader(settings.EGX_PUBLIC_DOWNLOADS_DIR)

    if page == "all":
        results = reader.read_all()
    else:
        page_type = EgxPublicPageType(page)
        results = [reader.read_page(page_type)]

    print("=== EGX Public Market Watch Update ===")
    print()
    for result in results:
        print_egx_public_read_result(result)

    exit_code = 0 if all(result.success for result in results) else 1

    if not import_egx_stocks:
        return exit_code

    stocks_result = next(
        (
            result
            for result in results
            if result.page_type == EgxPublicPageType.STOCKS and result.success
        ),
        None,
    )
    if stocks_result is None or stocks_result.saved_csv is None:
        print("EGX stocks import skipped: no stocks table was saved.")
        return 1

    normalized_path = settings.EGX_PUBLIC_DOWNLOADS_DIR / "stocks_normalized_latest.csv"
    norm_result = normalize_stocks_table_to_ohlcv(
        stocks_result.saved_csv, normalized_path
    )

    print("=== EGX Stocks OHLCV Normalization ===")
    print(f"Input: {stocks_result.saved_csv}")
    print(f"Output: {normalized_path}")
    print(f"Valid: {'yes' if norm_result.valid else 'no'}")
    print("Errors:")
    if norm_result.errors:
        for error in norm_result.errors:
            print(f"  - {error}")
    else:
        print("  (none)")
    print()

    if not norm_result.valid:
        return 1

    import_code = run_import_daily_real_csv(
        normalized_path,
        settings.DEFAULT_REAL_EGX_CSV_PATH,
    )
    return max(exit_code, import_code)


def format_pnl_line(pnl: float, pnl_percent: float | None) -> str:
    """Format PnL for monitor output."""
    sign = "+" if pnl > 0 else ""
    line = f"PnL {sign}{pnl:,.2f} {settings.BASE_CURRENCY}"
    if pnl_percent is not None:
        line += f" ({pnl_percent:+.2f}%)"
    return line


def print_paper_monitor_report(
    report: PaperMonitorReport,
    *,
    enabled: bool,
    force_eod: bool = False,
) -> None:
    """Print paper trade monitor results."""
    print("=== Paper Trade Monitor ===")
    print()

    if not enabled:
        print("Paper trade monitor: disabled")
        print("Use --monitor-paper-trades to review and close open paper trades.")
        print()
        return

    if report.checked_trades > 0:
        print(PaperTradeMonitor.OFFLINE_MONITOR_WARNING)
        print()

    print("Paper trade monitor: enabled")
    print(f"Force end-of-day exit: {'yes' if force_eod else 'no'}")
    print(f"Checked trades: {report.checked_trades}")
    print()

    print("CLOSED:")
    if report.closed_trades:
        for item in report.closed_trades:
            pnl_text = ""
            if item.pnl is not None:
                pnl_text = f" | {format_pnl_line(item.pnl, item.pnl_percent)}"
            print(
                f"  {item.symbol} | {item.exit_reason.value} | "
                f"Exit {item.exit_price:.2f}{pnl_text}"
            )
    else:
        print("  (none)")
    print()

    print("HELD:")
    if report.held_trades:
        for item in report.held_trades:
            reason = item.reasons[0] if item.reasons else "Held"
            print(f"  {item.symbol} | {reason}")
    else:
        print("  (none)")
    print()

    print("ERRORS:")
    if report.errors:
        for item in report.errors:
            error = item.errors[0] if item.errors else "Unknown error"
            print(f"  {item.symbol} | {error}")
    else:
        print("  (none)")
    print()


def format_closed_trade_line(trade: BacktestClosedTrade) -> str:
    """Format one closed backtest trade as a single console line."""
    return (
        f"  {trade.symbol} | Entry {trade.entry_price:.2f} | "
        f"Exit {trade.exit_price:.2f} | {trade.exit_reason.value} | "
        f"PnL {format_egp(trade.pnl)}"
    )


def print_backtest_report(
    report: BacktestReport,
    scenario: str,
    csv_path: Path,
) -> None:
    """Print backtest V1 summary, metrics, and closed trades."""
    metrics = report.metrics
    cfg = report.config

    print("=== Backtesting V1 ===")
    print(f"Scenario: {scenario}")
    print(f"Data source: {csv_path.relative_to(settings.PROJECT_ROOT)}")
    print(f"Initial capital: {cfg.initial_capital:,.2f} {settings.BASE_CURRENCY}")
    print(f"Strategy: {report.strategy_name}")
    print()
    print("Metrics:")
    print(f"  Ending equity: {metrics.ending_equity:,.2f} {settings.BASE_CURRENCY}")
    print(f"  Net PnL: {format_egp(metrics.net_pnl)}")
    print(f"  Net PnL %: {metrics.net_pnl_percent:+.2f}%")
    print(f"  Closed trades: {metrics.total_closed_trades}")
    print(f"  Win rate: {metrics.win_rate:.1f}%")
    pf = (
        f"{metrics.profit_factor:.2f}"
        if metrics.profit_factor is not None
        else "N/A"
    )
    print(f"  Profit factor: {pf}")
    print(f"  Max drawdown: {metrics.max_drawdown_percent:.2f}%")
    print(f"  Open positions at end: {metrics.open_positions_count}")
    print()
    print("Closed trades:")
    if report.closed_trades:
        for trade in report.closed_trades:
            print(format_closed_trade_line(trade))
    else:
        print("  (none)")
    print()
    print("Notes:")
    for note in report.notes:
        print(f"- {note}")
    print()


def run_backtest(provider: CsvMarketDataProvider) -> BacktestReport:
    """Run the daily backtesting engine on the given CSV provider."""
    backtester = DailyBacktester(
        provider=provider,
        symbols=DEFAULT_WATCHLIST,
        index_symbols=MARKET_INDEX_SYMBOLS,
        config=BacktestConfig(initial_capital=settings.INITIAL_CAPITAL_EGP),
    )
    return backtester.run()


def print_paper_trading_report(
    report: PaperTradingReport,
    *,
    enabled: bool,
    max_trades: int = 3,
    min_confidence: int = 70,
) -> None:
    """Print auto paper trading results."""
    print("=== Auto Paper Trading ===")
    print()

    if not enabled:
        print("Auto paper trading: disabled")
        print("Use --auto-paper-trade to open paper trades from Strategy Scanner B.")
        print()
        return

    print("Auto paper trading: enabled")
    print(f"Max trades this run: {max_trades}")
    print(f"Minimum confidence: {min_confidence}")
    print()

    print("OPENED:")
    if report.opened_trades:
        for item in report.opened_trades:
            print(
                f"  {item.symbol} | Qty {item.quantity} | Entry {item.entry_price:.2f} | "
                f"Stop {item.stop_loss:.2f} | TP {item.take_profit:.2f} | "
                f"Trade ID {item.trade_id}"
            )
            if item.reasons:
                print(f"    Reasons: {', '.join(item.reasons)}")
    else:
        print("  (none)")
    print()

    print("REJECTED:")
    if report.rejected_trades:
        for item in report.rejected_trades:
            entry = f"{item.entry_price:.2f}" if item.entry_price is not None else "N/A"
            print(f"  {item.symbol} | Entry {entry}")
            if item.rejection_reasons:
                print(f"    Rejections: {', '.join(item.rejection_reasons)}")
    else:
        print("  (none)")
    print()

    print("SKIPPED:")
    if report.skipped_trades:
        for item in report.skipped_trades:
            print(f"  {item.symbol}")
            if item.reasons:
                print(f"    Reasons: {', '.join(item.reasons)}")
    else:
        print("  (none)")
    print()


def run_comi_demo(
    market_mood: MarketMood,
    provider: MarketDataProvider,
    *,
    ignore_market_hours: bool = False,
) -> None:
    """Run the hardcoded COMI paper-trading demonstration."""
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    risk_manager = RiskManager()

    if market_mood == MarketMood.WEAK:
        print("Trade blocked because market mood is weak.")
        return

    session = detect_egx_market_session(ignore_market_hours=ignore_market_hours)
    if not session.paper_entries_enabled:
        print("Trade blocked because EGX market is closed for new paper entries.")
        print(f"Session: {session.session_status.value} | {session.note}")
        return

    signal = TradeSignal(
        symbol="COMI",
        signal_type=SignalType.BUY_SETUP,
        entry_price=80.0,
        stop_loss=78.0,
        take_profit=84.0,
        confidence_score=72,
        reasons=[
            "Price broke previous high",
            "Volume is above average",
            f"Market mood is {market_mood.value.lower()}",
        ],
        blockers=[],
    )

    print(f"=== Trade Signal: {signal.symbol} ===")
    print(
        f"Entry: {signal.entry_price:.2f} | Stop: {signal.stop_loss:.2f} | "
        f"Take Profit: {signal.take_profit:.2f}"
    )
    print(f"Confidence: {signal.confidence_score:.0f}% | Type: {signal.signal_type.value}")
    print("Reasons:")
    for reason in signal.reasons:
        print(f"  - {reason}")
    print()

    snapshot = portfolio.get_snapshot()
    decision = risk_manager.evaluate(signal, snapshot.equity)

    print("=== Risk Evaluation ===")
    if decision.approved:
        print("Status: APPROVED")
        print(decision.message)
    else:
        print("Status: REJECTED")
        for reason in decision.rejection_reasons:
            print(f"  - {reason}")
        return
    print()

    trade = portfolio.open_trade(
        symbol=signal.symbol,
        side=TradeSide.BUY,
        quantity=decision.quantity,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        reason="; ".join(signal.reasons),
    )
    journal.append_trade(trade)

    latest_prices = load_latest_prices(provider)
    open_snapshot = portfolio.get_snapshot(latest_prices)

    print("=== Portfolio Summary (Open) ===")
    print(f"Cash: {open_snapshot.cash:,.2f} {settings.BASE_CURRENCY}")
    for symbol, position in portfolio.positions.items():
        price = latest_prices.get(symbol, position.avg_entry_price)
        print(
            f"Open positions: {open_snapshot.open_positions} "
            f"({symbol} x{position.quantity} @ {position.avg_entry_price:.2f})"
        )
        print(
            f"Unrealized PnL: {format_egp(open_snapshot.unrealized_pnl)} "
            f"(at {price:.2f})"
        )
    print(f"Equity: {open_snapshot.equity:,.2f} {settings.BASE_CURRENCY}")
    print()

    exit_price = signal.take_profit
    print(f"=== Closing {signal.symbol} at {exit_price:.2f} ===")
    print()

    closed_trade = portfolio.close_trade(trade.id, exit_price)
    journal.update_trade(closed_trade)

    final_snapshot = portfolio.get_snapshot()
    stats = journal.summary()

    print("=== Final Summary ===")
    print(f"Cash: {final_snapshot.cash:,.2f} {settings.BASE_CURRENCY}")
    print(f"Realized PnL: {format_egp(final_snapshot.realized_pnl)}")
    print(
        f"Trade PnL: {format_egp(closed_trade.pnl or 0)} "
        f"({closed_trade.pnl_percent:+.2f}%)"
    )
    print()

    print("=== Trade Journal ===")
    print(
        f"Total trades: {stats['total_trades']} | "
        f"Wins: {stats['winning_trades']} | "
        f"Losses: {stats['losing_trades']} | "
        f"Win rate: {stats['win_rate']:.1f}%"
    )
    print(f"Total PnL: {format_egp(stats['total_pnl'])}")
    if stats["best_trade"]:
        best = stats["best_trade"]
        print(f"Best trade: {best.symbol} {format_egp(best.pnl or 0)}")


def run(
    scenario: str = "default",
    demo_trade: bool = False,
    auto_paper_trade: bool = False,
    reset_paper_state_flag: bool = False,
    monitor_paper_trades: bool = False,
    force_eod_exit: bool = False,
    backtest: bool = False,
    data_source: str = "demo",
    real_csv: Path | None = None,
    ignore_market_hours: bool = False,
) -> None:
    """Run market analysis and optional paper trading flows."""
    try:
        csv_path = resolve_data_csv_path(data_source, scenario, real_csv)
    except FileNotFoundError as exc:
        print(str(exc))
        return

    provider = create_data_provider(csv_path)
    scenario_label = display_scenario_label(data_source, scenario)

    if reset_paper_state_flag:
        reset_paper_state()

    portfolio = VirtualPortfolio()
    journal = TradeJournal()

    print("=== EGX Smart Trading Coach ===")
    print(f"Market: {settings.MARKET_NAME} | Mode: PAPER TRADING ONLY")
    print(f"Data type: {data_type_label(data_source)}")
    print()

    market_mood, mood_result, market_snapshot = print_market_snapshot(
        scenario_label, csv_path, provider, data_source=data_source
    )
    scanner_report = print_scanner_report(mood_result, market_snapshot)
    strategy_report = print_strategy_report(scanner_report, market_snapshot)

    max_trades = 3
    min_confidence = 70
    paper_report = PaperTradingReport()

    if auto_paper_trade:
        if demo_trade:
            print(
                "Note: Auto Paper Trading is strategy-driven. "
                "Demo trade is separate and hardcoded."
            )
            print()
        trader = AutoPaperTrader(
            portfolio=portfolio,
            journal=journal,
            risk_manager=RiskManager(),
            max_trades_per_run=max_trades,
            min_confidence_score=min_confidence,
            ignore_market_hours=ignore_market_hours,
        )
        paper_report = trader.execute_strategy_report(strategy_report)

    print_paper_trading_report(
        paper_report,
        enabled=auto_paper_trade,
        max_trades=max_trades,
        min_confidence=min_confidence,
    )

    monitor_report = PaperMonitorReport()
    if monitor_paper_trades:
        latest_prices = build_latest_prices(market_snapshot)
        monitor = PaperTradeMonitor(portfolio, journal)
        monitor_report = monitor.monitor_open_trades(
            latest_prices,
            force_end_of_day_exit=force_eod_exit,
        )

    print_paper_monitor_report(
        monitor_report,
        enabled=monitor_paper_trades,
        force_eod=force_eod_exit,
    )

    if demo_trade:
        print("Demo trade: enabled")
        print(
            "Note: this is a separate hardcoded paper-trading demo "
            "and is not connected to Strategy Scanner B."
        )
        if auto_paper_trade:
            print("Auto Paper Trading is strategy-driven. Demo trade is separate and hardcoded.")
        print()
        run_comi_demo(market_mood, provider, ignore_market_hours=ignore_market_hours)
    else:
        print("Demo trade: disabled")
        print("Use --demo-trade to run the separate COMI paper-trade example.")

    if backtest:
        if not isinstance(provider, CsvMarketDataProvider):
            provider = CsvMarketDataProvider(csv_path)
        backtest_report = run_backtest(provider)
        print_backtest_report(backtest_report, scenario_label, csv_path)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    cli_args = parse_args(argv)

    if cli_args.show_egx_symbol_mapping:
        return run_show_egx_symbol_mapping()

    if cli_args.egx_cloud_readiness_check:
        from core.cloud_readiness import run_cloud_readiness_check

        return run_cloud_readiness_check()

    if cli_args.market_hours_status:
        return run_market_hours_status(
            ignore_market_hours=cli_args.ignore_market_hours,
        )

    if cli_args.validate_real_csv:
        if cli_args.real_csv is None:
            print("Error: --validate-real-csv requires --real-csv PATH.")
            return 1
        return run_validate_real_csv(cli_args.real_csv)

    if cli_args.normalize_real_csv:
        if cli_args.real_csv is None:
            print("Error: --normalize-real-csv requires --real-csv PATH.")
            return 1
        output_path = (
            cli_args.normalized_output or settings.DEFAULT_REAL_EGX_CSV_PATH
        )
        return run_normalize_real_csv(cli_args.real_csv, output_path)

    if cli_args.import_daily_real_csv is not None:
        return run_import_daily_real_csv(
            cli_args.import_daily_real_csv,
            settings.DEFAULT_REAL_EGX_CSV_PATH,
        )

    if cli_args.download_data is not None:
        return run_download_data(
            cli_args.download_data,
            url=cli_args.url,
            kaggle_dataset=cli_args.kaggle_dataset,
            eodhd_symbol=cli_args.eodhd_symbol,
            eodhd_api_key=cli_args.eodhd_api_key,
            import_after_download=cli_args.import_after_download,
        )

    if cli_args.egx_public_update:
        return run_egx_public_update(
            cli_args.egx_public_page,
            cli_args.import_egx_stocks,
        )

    if cli_args.analyze_egx_debug_html is not None:
        return run_analyze_egx_debug_html(cli_args.analyze_egx_debug_html)

    if cli_args.probe_egx_endpoints:
        return run_probe_egx_endpoints()

    if cli_args.analyze_egx_probe_file is not None:
        return run_analyze_egx_probe_file(cli_args.analyze_egx_probe_file)

    if cli_args.probe_egx_types:
        return run_probe_egx_types()

    if cli_args.egx_company_prices:
        return run_egx_company_prices(cli_args.egx_company_prefixes)

    if cli_args.egx_browser_stocks_update:
        return run_egx_browser_stocks_update(
            cli_args.egx_browser_headful,
            cli_args.import_egx_browser_stocks,
        )

    if cli_args.save_live_history_only:
        snapshot_path = cli_args.egx_live_snapshot or settings.EGX_LIVE_SNAPSHOT_PATH
        return run_save_live_history_only(snapshot_path)

    candidate_filters = build_candidate_filters_from_cli(
        top_candidates=cli_args.top_candidates,
        min_score=cli_args.min_score,
        min_volume_ratio=cli_args.min_volume_ratio,
        min_market_cap_quality=cli_args.min_market_cap_quality,
        max_pe=cli_args.max_pe,
        max_pb=cli_args.max_pb,
        require_fundamentals=cli_args.require_fundamentals,
    )
    quality_filters = build_market_quality_filters_from_cli(
        min_price=cli_args.min_price,
        min_volume=cli_args.min_volume,
        min_market_cap=cli_args.min_market_cap,
        exclude_zero_volume=cli_args.exclude_zero_volume,
        include_illiquid=cli_args.include_illiquid,
    )
    ranking_config = build_candidate_ranking_config_from_cli(
        max_rank_change=cli_args.max_rank_change,
        prefer_change_min=cli_args.prefer_change_min,
        prefer_change_max=cli_args.prefer_change_max,
    )
    technical_config = build_technical_confirmation_config_from_cli(
        enabled=cli_args.enable_technical_confirmation,
        rsi_min=cli_args.rsi_min,
        rsi_max=cli_args.rsi_max,
        rsi_caution=cli_args.rsi_caution,
        adx_min=cli_args.adx_min,
    )
    multi_timeframe_config = build_multi_timeframe_config_from_cli(
        enabled=cli_args.enable_multi_timeframe,
        entry_timeframes=cli_args.entry_timeframes,
    )
    tv_query_filter_config = build_tradingview_query_filter_config_from_cli(
        enabled=cli_args.enable_tv_prefilter,
        quality_filters=quality_filters,
    )
    talib_config = build_talib_technical_config_from_cli(
        enabled=cli_args.enable_talib_engine,
        min_history_days=cli_args.talib_min_history_days,
    )

    if cli_args.telegram_bot:
        from core.telegram_bot import run_telegram_bot

        return run_telegram_bot()

    if cli_args.egx_workflow:
        snapshot_path = cli_args.egx_live_snapshot or settings.EGX_LIVE_SNAPSHOT_PATH
        return run_egx_workflow(
            cli_args.egx_workflow,
            cdp_url=cli_args.chrome_cdp_url,
            chrome_profile_dir=cli_args.chrome_profile_dir,
            snapshot_path=snapshot_path,
            use_local_snapshot=cli_args.egx_local,
            lookback_days=cli_args.live_volume_lookback_days,
            min_history_days=cli_args.live_volume_min_history_days,
            max_trades=cli_args.live_paper_max_trades,
            min_confidence=cli_args.live_paper_min_confidence,
            reset_paper_state_flag=cli_args.reset_paper_state,
            scanner_universe=cli_args.scanner_universe,
            candidate_filters=candidate_filters,
            data_provider=cli_args.data_provider,
            quality_filters=quality_filters,
            ranking_config=ranking_config,
            technical_config=technical_config,
            multi_timeframe_config=multi_timeframe_config,
            tv_query_filter_config=tv_query_filter_config,
            enable_portfolio_marking=cli_args.enable_portfolio_marking,
            talib_config=talib_config,
            enable_performance_analytics=cli_args.enable_performance_analytics,
            ignore_market_hours=cli_args.ignore_market_hours,
        )

    if cli_args.egx_daily_report:
        snapshot_path = cli_args.egx_live_snapshot or settings.EGX_LIVE_SNAPSHOT_PATH
        return run_egx_daily_report(
            snapshot_path,
            lookback_days=cli_args.live_volume_lookback_days,
            min_history_days=cli_args.live_volume_min_history_days,
            scanner_universe=cli_args.scanner_universe,
            candidate_filters=candidate_filters,
            data_provider=None,
            quality_filters=quality_filters,
            ranking_config=ranking_config,
            technical_config=technical_config,
            multi_timeframe_config=multi_timeframe_config,
            tv_query_filter_config=tv_query_filter_config,
            enable_portfolio_marking=cli_args.enable_portfolio_marking,
            talib_config=talib_config,
            enable_performance_analytics=cli_args.enable_performance_analytics,
            ignore_market_hours=cli_args.ignore_market_hours,
        )

    if cli_args.egx_one_click_daily_report:
        return run_egx_one_click_daily_report(
            cli_args.chrome_cdp_url,
            cli_args.chrome_profile_dir,
            lookback_days=cli_args.live_volume_lookback_days,
            min_history_days=cli_args.live_volume_min_history_days,
            scanner_universe=cli_args.scanner_universe,
            candidate_filters=candidate_filters,
            data_provider=cli_args.data_provider,
            quality_filters=quality_filters,
            ranking_config=ranking_config,
            technical_config=technical_config,
            multi_timeframe_config=multi_timeframe_config,
            tv_query_filter_config=tv_query_filter_config,
            enable_portfolio_marking=cli_args.enable_portfolio_marking,
            talib_config=talib_config,
            enable_performance_analytics=cli_args.enable_performance_analytics,
            ignore_market_hours=cli_args.ignore_market_hours,
        )

    if cli_args.egx_live_paper_monitor:
        snapshot_path = cli_args.egx_live_snapshot or settings.EGX_LIVE_SNAPSHOT_PATH
        return run_egx_live_paper_monitor(
            snapshot_path,
            reset_paper_state_flag=cli_args.reset_paper_state,
        )

    if cli_args.egx_one_click_paper_monitor:
        return run_egx_one_click_paper_monitor(
            cli_args.chrome_cdp_url,
            cli_args.chrome_profile_dir,
            reset_paper_state_flag=cli_args.reset_paper_state,
            data_provider=cli_args.data_provider,
        )

    if cli_args.egx_one_click_paper_cycle:
        return run_egx_one_click_paper_cycle(
            cli_args.chrome_cdp_url,
            cli_args.chrome_profile_dir,
            lookback_days=cli_args.live_volume_lookback_days,
            min_history_days=cli_args.live_volume_min_history_days,
            max_trades=cli_args.live_paper_max_trades,
            min_confidence=cli_args.live_paper_min_confidence,
            reset_paper_state_flag=cli_args.reset_paper_state,
            scanner_universe=cli_args.scanner_universe,
            candidate_filters=candidate_filters,
            data_provider=cli_args.data_provider,
            quality_filters=quality_filters,
            ranking_config=ranking_config,
            technical_config=technical_config,
            ignore_market_hours=cli_args.ignore_market_hours,
        )

    if cli_args.egx_live_paper_trade:
        snapshot_path = cli_args.egx_live_snapshot or settings.EGX_LIVE_SNAPSHOT_PATH
        return run_egx_live_paper_trade(
            snapshot_path,
            lookback_days=cli_args.live_volume_lookback_days,
            min_history_days=cli_args.live_volume_min_history_days,
            max_trades=cli_args.live_paper_max_trades,
            min_confidence=cli_args.live_paper_min_confidence,
            reset_paper_state_flag=cli_args.reset_paper_state,
            scanner_universe=cli_args.scanner_universe,
            candidate_filters=candidate_filters,
            data_provider=None,
            quality_filters=quality_filters,
            ranking_config=ranking_config,
            technical_config=technical_config,
            ignore_market_hours=cli_args.ignore_market_hours,
        )

    if cli_args.egx_one_click_paper_trade:
        return run_egx_one_click_paper_trade(
            cli_args.chrome_cdp_url,
            cli_args.chrome_profile_dir,
            lookback_days=cli_args.live_volume_lookback_days,
            min_history_days=cli_args.live_volume_min_history_days,
            max_trades=cli_args.live_paper_max_trades,
            min_confidence=cli_args.live_paper_min_confidence,
            reset_paper_state_flag=cli_args.reset_paper_state,
            scanner_universe=cli_args.scanner_universe,
            candidate_filters=candidate_filters,
            data_provider=cli_args.data_provider,
            quality_filters=quality_filters,
            ranking_config=ranking_config,
            technical_config=technical_config,
            ignore_market_hours=cli_args.ignore_market_hours,
        )

    if cli_args.egx_live_scan:
        snapshot_path = cli_args.egx_live_snapshot or settings.EGX_LIVE_SNAPSHOT_PATH
        return run_egx_live_scan(
            snapshot_path,
            lookback_days=cli_args.live_volume_lookback_days,
            min_history_days=cli_args.live_volume_min_history_days,
            scanner_universe=cli_args.scanner_universe,
            candidate_filters=candidate_filters,
            data_provider=None,
            quality_filters=quality_filters,
            ranking_config=ranking_config,
            technical_config=technical_config,
        )

    if cli_args.egx_one_click_live_scan:
        return run_egx_one_click_live_scan(
            cli_args.chrome_cdp_url,
            cli_args.chrome_profile_dir,
            lookback_days=cli_args.live_volume_lookback_days,
            min_history_days=cli_args.live_volume_min_history_days,
            scanner_universe=cli_args.scanner_universe,
            candidate_filters=candidate_filters,
            data_provider=cli_args.data_provider,
            quality_filters=quality_filters,
            ranking_config=ranking_config,
            technical_config=technical_config,
        )

    if cli_args.egx_update_and_live_scan:
        return run_egx_update_and_live_scan(
            cli_args.chrome_cdp_url,
            lookback_days=cli_args.live_volume_lookback_days,
            min_history_days=cli_args.live_volume_min_history_days,
            scanner_universe=cli_args.scanner_universe,
            candidate_filters=candidate_filters,
        )

    if cli_args.egx_attach_chrome_stocks:
        return run_egx_attach_chrome_stocks(
            cli_args.chrome_cdp_url,
            cli_args.import_egx_attached_stocks,
        )

    run(
        cli_args.scenario,
        cli_args.demo_trade,
        cli_args.auto_paper_trade,
        cli_args.reset_paper_state,
        cli_args.monitor_paper_trades,
        cli_args.force_eod_exit,
        cli_args.backtest,
        cli_args.data_source,
        cli_args.real_csv,
        cli_args.ignore_market_hours,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
