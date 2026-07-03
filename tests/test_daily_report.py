"""Tests for EGX daily report builder and persistence."""

from datetime import date
from pathlib import Path

import pytest

from config import settings
from core.candidate_filters import CandidateFilters
from core.daily_report import (
    DailyReportBuilder,
    format_daily_report_text,
    save_daily_report,
)
from core.warning_formatting import (
    SMA5_WATCHLIST_SUMMARY,
    VOLUME_HISTORY_SUMMARY,
    WATCHLIST_VOLUME_HISTORY_SUMMARY,
    summarize_daily_report_warnings,
)
from core.live_scanner_adapter import SMA5_HISTORY_WARNING
from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from core.live_volume import NOT_ENOUGH_VOLUME_HISTORY_WARNING
from core.market_mood import MarketMood, MarketMoodResult
from core.market_hours import (
    sample_closed_market_datetime,
    sample_open_market_datetime,
)
from core.scanner import ScannerDecision, ScannerReport, ScannerResult
from core.strategy import StrategyDecision, StrategyReport, StrategyResult
from core.models import TradeSide
from core.talib_technical import TalibTechnicalConfig, TALIB_NOT_INSTALLED_WARNING
from core.portfolio import VirtualPortfolio
from core.trade_journal import TradeJournal


def _live_row(
    symbol: str,
    close: float,
    previous_close: float,
    volume: float,
    volume_ratio: float,
) -> LiveSymbolSnapshot:
    return LiveSymbolSnapshot(
        symbol=symbol,
        date=date(2026, 7, 1),
        previous_close=previous_close,
        open=previous_close,
        high=max(close, previous_close) + 0.5,
        low=min(close, previous_close) - 0.5,
        close=close,
        volume=volume,
        change_percent=((close - previous_close) / previous_close) * 100,
        volume_ratio=volume_ratio,
        broke_previous_high=close > previous_close,
    )


def _scanner_result(
    symbol: str,
    decision: ScannerDecision,
    score: int,
    change_percent: float,
    volume_ratio: float,
    *,
    reasons: list[str] | None = None,
    blockers: list[str] | None = None,
) -> ScannerResult:
    return ScannerResult(
        symbol=symbol,
        decision=decision,
        score=score,
        latest_close=100.0,
        change_percent=change_percent,
        volume_ratio=volume_ratio,
        broke_previous_high=True,
        above_sma_5=True,
        reasons=reasons or [],
        blockers=blockers or [],
    )


def _strategy_result(
    symbol: str,
    decision: StrategyDecision,
    *,
    blockers: list[str] | None = None,
) -> StrategyResult:
    return StrategyResult(
        symbol=symbol,
        decision=decision,
        entry_price=23.10,
        stop_loss=22.60,
        take_profit=24.10,
        risk_reward=2.0,
        confidence_score=85,
        reasons=["Scanner marked symbol as candidate"],
        blockers=blockers or [],
    )


def _fake_scan_bundle() -> tuple[
    LiveMarketSnapshot,
    MarketMoodResult,
    ScannerReport,
    StrategyReport,
    list[str],
]:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            f"SYM{i}": _live_row(
                f"SYM{i}",
                close=100.0 + i,
                previous_close=99.0,
                volume=1000.0 * i,
                volume_ratio=1.0 + (i * 0.1),
            )
            for i in range(15)
        },
    )
    mood = MarketMoodResult(
        mood=MarketMood.NEUTRAL,
        score=50,
        blockers=["EGX30/EGX70 missing from live snapshot"],
    )
    candidates = [
        _scanner_result(
            f"CAND{i}",
            ScannerDecision.CANDIDATE,
            90 - i,
            1.0 + i,
            2.0 + (i * 0.1),
            reasons=["Positive price change", "Broke previous high"],
        )
        for i in range(12)
    ]
    watchlist = [
        _scanner_result(
            f"WATCH{i}",
            ScannerDecision.WATCH,
            60,
            0.5,
            1.0,
        )
        for i in range(3)
    ]
    blocked = [
        _scanner_result(
            f"BLOCK{i}",
            ScannerDecision.BLOCKED,
            30,
            -1.0,
            0.7,
            blockers=["Weak volume ratio"] if i % 2 == 0 else ["Negative price change"],
        )
        for i in range(6)
    ]
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=candidates + watchlist + blocked,
        candidates=candidates,
        watchlist=watchlist,
        blocked=blocked,
    )
    strategy_report = StrategyReport(
        strategy_name="Trend Join Long",
        results=[
            _strategy_result("HRHO", StrategyDecision.WATCH, blockers=["Volume confirmation is weak"])
        ],
        buy_setups=[],
        watch=[
            _strategy_result(
                "HRHO",
                StrategyDecision.WATCH,
                blockers=["Volume confirmation is weak"],
            )
        ],
        blocked=[],
    )
    warnings = [
        VOLUME_HISTORY_SUMMARY.format(count=20, min_history_days=3),
    ]
    return live_snapshot, mood, scanner_report, strategy_report, warnings


def test_builds_report_from_fake_live_scan() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        now=sample_open_market_datetime(),
        talib_config=TalibTechnicalConfig(enabled=False),
        enable_performance_analytics=False,
    )
    assert report.sections[1].title == "Summary"
    assert report.sections[2].title == "Market Session"
    assert report.sections[3].title == "Candidate Filters"
    assert report.market_session["status"] in {"OPEN", "CLOSED", "PREOPEN", "CLOSING_AUCTION", "TRADE_AT_CLOSE"}
    assert report.executive_summary
    assert report.warnings == warnings
    assert report.report_metadata["data_provider"] is not None
    assert "paper_portfolio_present" in report.report_metadata
    assert "paper_portfolio_storage_on_server" in report.report_metadata


def test_includes_summary_section() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        now=sample_open_market_datetime(),
    )

    summary = next(section for section in report.sections if section.title == "Summary")
    assert summary.title == "Summary"
    assert "- Data Provider: Local snapshot (cached)" in summary.lines
    assert "- Scanner Universe: watchlist" in summary.lines
    assert "- Symbols scanned: 15" in summary.lines
    assert "- Candidates: 12" in summary.lines


def test_includes_candidate_filters_section() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
    )

    filters = next(
        section for section in report.sections if section.title == "Candidate Filters"
    )
    assert "- Top candidates limit: 10" in filters.lines
    assert "- Min score: none" in filters.lines
    assert "- Min relative volume: none" in filters.lines


def test_limits_top_candidates_to_ten() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
    )

    candidates = next(section for section in report.sections if section.title == "Top Candidates")
    numbered_lines = [
        line for line in candidates.lines if line[:1].isdigit() and ". " in line[:4]
    ]
    assert len(numbered_lines) == 10
    assert "11." not in candidates.lines


def test_min_score_filters_top_candidates_in_report() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        candidate_filters=CandidateFilters(min_score=86),
    )

    candidates = next(section for section in report.sections if section.title == "Top Candidates")
    numbered_lines = [
        line for line in candidates.lines if line[:1].isdigit() and ". " in line[:4]
    ]
    assert len(numbered_lines) == 5
    assert "CAND5" not in "\n".join(candidates.lines)


def test_blocked_summary_counts_reasons() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
    )

    blocked = next(
        section for section in report.sections if section.title == "Blocked Summary"
    )
    assert any("Weak volume ratio: 3" in line for line in blocked.lines)
    assert any("Negative price change: 3" in line for line in blocked.lines)


def test_format_text_contains_major_headings() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
    )
    text = format_daily_report_text(report)

    assert "=== EGX Daily Report ===" in text
    assert "Executive Summary:" in text
    assert "Summary:" in text
    assert "Market Session:" in text
    assert "Candidate Filters:" in text
    assert "Market Mood:" in text
    assert "Sector Momentum:" in text
    assert "Top Candidates:" in text
    assert "Strategy Signals:" in text
    assert "Warnings:" in text


def test_save_daily_report_writes_txt_and_json(tmp_path: Path) -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()
    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
    )

    txt_path, json_path = save_daily_report(report, tmp_path)

    assert txt_path.exists()
    assert json_path.exists()
    assert txt_path.suffix == ".txt"
    assert json_path.suffix == ".json"
    assert "=== EGX Daily Report ===" in txt_path.read_text(encoding="utf-8")
    assert '"report_date"' in json_path.read_text(encoding="utf-8")


def _volume_warning(symbol: str) -> str:
    return f"{symbol}: {NOT_ENOUGH_VOLUME_HISTORY_WARNING}"


def _sma5_warning(symbol: str) -> str:
    return f"{symbol}: {SMA5_HISTORY_WARNING}"


def test_summarize_volume_history_warnings() -> None:
    raw_warnings = [_volume_warning("COMI"), _volume_warning("HRHO")] + [
        _volume_warning(f"SYM{i}") for i in range(167)
    ]

    summarized = summarize_daily_report_warnings(raw_warnings, watchlist=["COMI", "HRHO"])

    assert len(summarized) == 2
    assert summarized[0] == VOLUME_HISTORY_SUMMARY.format(count=169, min_history_days=3)
    assert summarized[1] == WATCHLIST_VOLUME_HISTORY_SUMMARY.format(symbols="COMI, HRHO")


def test_watchlist_insufficient_history_summary_is_short() -> None:
    watchlist = ["COMI", "HRHO", "FWRY", "TMGH", "ORAS"]
    raw_warnings = [_volume_warning(symbol) for symbol in watchlist] + [
        _volume_warning(f"OTHER{i}") for i in range(20)
    ]

    summarized = summarize_daily_report_warnings(raw_warnings, watchlist=watchlist)

    watchlist_line = next(
        line
        for line in summarized
        if line.startswith("Watchlist symbols with insufficient volume history:")
    )
    assert watchlist_line == WATCHLIST_VOLUME_HISTORY_SUMMARY.format(
        symbols="COMI, HRHO, FWRY, TMGH, ORAS"
    )
    assert len(summarized) == 2


def test_summarize_sma5_cold_start_warnings() -> None:
    watchlist = ["COMI", "HRHO", "FWRY", "TMGH", "ORAS"]
    raw_warnings = [_sma5_warning(symbol) for symbol in watchlist[:4]]

    summarized = summarize_daily_report_warnings(raw_warnings, watchlist=watchlist)

    assert summarized == [SMA5_WATCHLIST_SUMMARY.format(count=4)]


def test_critical_warnings_are_not_removed() -> None:
    critical_warnings = [
        "Low valid symbol count after dedupe: 12",
        "Very low valid symbol count after dedupe: 4",
        "Partial EGX snapshot: only 80 rows collected",
        "Multi-sector collection unavailable; using visible table fallback",
        "Symbol mapping: 5 mapped, 2 unresolved, 1 duplicate",
        "Watchlist symbol COMI missing from live snapshot",
    ]
    raw_warnings = critical_warnings + [_volume_warning("COMI")]

    summarized = summarize_daily_report_warnings(raw_warnings, watchlist=["COMI"])

    assert summarized[: len(critical_warnings)] == critical_warnings
    assert summarized[-2] == VOLUME_HISTORY_SUMMARY.format(count=1, min_history_days=3)
    assert summarized[-1] == WATCHLIST_VOLUME_HISTORY_SUMMARY.format(symbols="COMI")


def test_report_warnings_remain_readable() -> None:
    watchlist = ["COMI", "HRHO", "FWRY", "TMGH", "ORAS"]
    raw_warnings = [
        "Low valid symbol count after dedupe: 12",
        *[_volume_warning(symbol) for symbol in watchlist],
        *[_volume_warning(f"SYM{i}") for i in range(164)],
        *[_sma5_warning(symbol) for symbol in watchlist],
        "Watchlist symbol ABUK missing from live snapshot",
    ]
    live_snapshot, mood, scanner_report, strategy_report, _ = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=raw_warnings,
    )
    warnings_section = next(
        section for section in report.sections if section.title == "Warnings"
    )

    assert len(report.warnings) <= 6
    assert len(warnings_section.lines) <= 6
    assert any("Low valid symbol count after dedupe: 12" in line for line in warnings_section.lines)
    assert any(
        VOLUME_HISTORY_SUMMARY.format(count=169, min_history_days=3) in line
        for line in warnings_section.lines
    )
    assert any(
        WATCHLIST_VOLUME_HISTORY_SUMMARY.format(
            symbols="COMI, HRHO, FWRY, TMGH, ORAS"
        )
        in line
        for line in warnings_section.lines
    )
    assert any(
        SMA5_WATCHLIST_SUMMARY.format(count=5) in line for line in warnings_section.lines
    )
    assert any("Watchlist symbol ABUK missing from live snapshot" in line for line in warnings_section.lines)
    assert not any("SYM0: Not enough volume history" in line for line in warnings_section.lines)


def test_full_market_movers_and_volume_respect_quality_filters() -> None:
    import pandas as pd

    from core.market_quality_filters import MarketQualityFilters, apply_market_quality_filters
    from core.scanner_universe import SCANNER_UNIVERSE_FULL_MARKET

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            "GOOD": _live_row("GOOD", 10.0, 9.0, 500_000, 3.0),
            "LOWP": _live_row("LOWP", 2.10, 2.0, 500_000, 2.5),
            "LOWV": _live_row("LOWV", 10.0, 9.5, 91_899, 4.0),
        },
    )
    quality_result = apply_market_quality_filters(
        pd.DataFrame(
            [
                {"symbol": "GOOD", "close": 10.0, "volume": 500_000},
                {"symbol": "LOWP", "close": 2.10, "volume": 500_000},
                {"symbol": "LOWV", "close": 10.0, "volume": 91_899},
            ]
        ),
        MarketQualityFilters(min_price=3.0, min_volume=300_000),
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    scanner_report = ScannerReport(
        market_mood=MarketMood.NEUTRAL.value,
        results=[],
        candidates=[],
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[],
        buy_setups=[],
        watch=[],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
        quality_filter_result=quality_result,
        configured_watchlist=["LOWV"],
        watchlist_scanner_results={
            "LOWV": _scanner_result(
                "LOWV",
                ScannerDecision.WATCH,
                60,
                5.0,
                4.0,
                reasons=["Watch symbol"],
            ),
        },
    )
    movers_section = next(
        section for section in report.sections if section.title == "Strongest Movers"
    )
    volume_section = next(
        section for section in report.sections if section.title == "Volume Leaders"
    )
    watch_section = next(
        section for section in report.sections if section.title == "Watch List"
    )

    movers_text = "\n".join(movers_section.lines)
    volume_text = "\n".join(volume_section.lines)
    watch_text = "\n".join(watch_section.lines)

    assert "LOWP" not in movers_text
    assert "LOWV" not in volume_text
    assert "GOOD" in movers_text
    assert "LOWV" in watch_text


def test_volume_leaders_sorted_by_raw_volume_descending() -> None:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            "LOW": _live_row("LOW", 10.0, 9.0, 100_000, 5.0),
            "HIGH": _live_row("HIGH", 10.0, 9.0, 5_627_677, 1.0),
            "MID": _live_row("MID", 10.0, 9.0, 2_015_953, 2.0),
            "ZERO": _live_row("ZERO", 10.0, 9.0, 0.0, 3.0),
        },
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    scanner_report = ScannerReport(
        market_mood=MarketMood.NEUTRAL.value,
        results=[],
        candidates=[],
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[],
        buy_setups=[],
        watch=[],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
    )
    volume_section = next(
        section for section in report.sections if section.title == "Volume Leaders"
    )

    assert volume_section.lines[0].startswith("1. HIGH")
    assert volume_section.lines[1].startswith("2. MID")
    assert volume_section.lines[2].startswith("3. LOW")
    assert volume_section.lines[3].startswith("4. ZERO")


def test_volume_leaders_with_quality_filters_excludes_low_volume() -> None:
    import pandas as pd

    from core.market_quality_filters import MarketQualityFilters, apply_market_quality_filters
    from core.scanner_universe import SCANNER_UNIVERSE_FULL_MARKET

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            "BIG": _live_row("BIG", 10.0, 9.0, 5_000_000, 2.0),
            "SMALL": _live_row("SMALL", 10.0, 9.0, 50_000, 4.0),
        },
    )
    quality_result = apply_market_quality_filters(
        pd.DataFrame(
            [
                {"symbol": "BIG", "close": 10.0, "volume": 5_000_000},
                {"symbol": "SMALL", "close": 10.0, "volume": 50_000},
            ]
        ),
        MarketQualityFilters(min_volume=300_000),
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    scanner_report = ScannerReport(
        market_mood=MarketMood.NEUTRAL.value,
        results=[],
        candidates=[],
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[],
        buy_setups=[],
        watch=[],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
        quality_filter_result=quality_result,
    )
    volume_section = next(
        section for section in report.sections if section.title == "Volume Leaders"
    )
    volume_text = "\n".join(volume_section.lines)

    assert "BIG" in volume_text
    assert "SMALL" not in volume_text


def test_summarize_daily_report_warnings_dedupes_identical_lines() -> None:
    from core.live_scanner_adapter import MISSING_INDEX_MOOD_WARNING

    duplicated = [MISSING_INDEX_MOOD_WARNING, MISSING_INDEX_MOOD_WARNING]
    summarized = summarize_daily_report_warnings(duplicated)

    assert summarized.count(MISSING_INDEX_MOOD_WARNING) == 1


def test_top_candidates_include_technical_line_when_fields_available(
    tmp_path: Path,
) -> None:
    import pandas as pd

    from core.technical_confirmation import TechnicalConfirmationConfig

    snapshot_path = tmp_path / "egx_live_snapshot.csv"
    pd.DataFrame(
        [
            {
                "symbol": "COMI",
                "close": 110.0,
                "volume": 500_000,
                "volume_ratio": 2.0,
                "rsi": 58.0,
                "ema20": 100.0,
                "macd": 1.5,
                "macd_signal": 1.0,
                "adx": 24.0,
            }
        ]
    ).to_csv(snapshot_path, index=False)

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            "COMI": _live_row("COMI", 110.0, 100.0, 500_000, 2.0),
        },
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    scanner_report = ScannerReport(
        market_mood=MarketMood.NEUTRAL.value,
        results=[
            _scanner_result(
                "COMI",
                ScannerDecision.CANDIDATE,
                95,
                5.0,
                2.0,
                reasons=["Positive price change"],
            )
        ],
        candidates=[
            _scanner_result(
                "COMI",
                ScannerDecision.CANDIDATE,
                95,
                5.0,
                2.0,
                reasons=["Positive price change"],
            )
        ],
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[],
        buy_setups=[],
        watch=[],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        technical_config=TechnicalConfirmationConfig(),
        snapshot_path=snapshot_path,
    )
    top_section = next(
        section for section in report.sections if section.title == "Top Candidates"
    )
    top_text = "\n".join(top_section.lines)

    assert "Technical:" in top_text
    assert "RSI 58" in top_text


def test_daily_report_includes_sector_momentum_section(tmp_path: Path) -> None:
    import pandas as pd

    from core.scanner_universe import SCANNER_UNIVERSE_FULL_MARKET

    snapshot_path = tmp_path / "egx_live_snapshot.csv"
    pd.DataFrame(
        [
            {
                "symbol": f"RE{i}",
                "sector": "Real Estate",
                "change_percent": 2.0 + (i * 0.1),
                "volume": 1_000_000,
                "tv_relative_volume_10d": 1.8,
                "market_cap": 500_000_000,
            }
            for i in range(10)
        ]
    ).to_csv(snapshot_path, index=False)

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            f"RE{i}": _live_row(
                f"RE{i}",
                close=10.0 + i * 0.2,
                previous_close=9.8,
                volume=1_000_000,
                volume_ratio=1.8,
            )
            for i in range(10)
        },
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    candidates = [
        _scanner_result(
            "RE0",
            ScannerDecision.CANDIDATE,
            90,
            2.0,
            1.8,
            reasons=["Positive price change"],
        )
    ]
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=candidates,
        candidates=candidates,
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[],
        buy_setups=[],
        watch=[],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
        snapshot_path=snapshot_path,
    )
    section_titles = [section.title for section in report.sections]
    mood_index = section_titles.index("Market Mood")
    sector_index = section_titles.index("Sector Momentum")
    sector_intelligence_index = section_titles.index("Sector Intelligence Summary")
    candidates_index = section_titles.index("Top Candidates")
    sector_section = report.sections[sector_index]

    assert mood_index < sector_index < sector_intelligence_index < candidates_index
    assert any("Real Estate" in line for line in sector_section.lines)
    assert any("HOT" in line for line in sector_section.lines)
    assert report.sector_momentum
    assert report.sector_momentum[0]["sector"] == "Real Estate"
    assert report.report_metadata["sector_intelligence_available"] is True
    assert report.sector_intelligence_summary["available"] is True
    assert report.sector_intelligence_context["RE0"]["sector"] == "Real Estate"


def test_top_candidates_rank_factors_include_sector_status(tmp_path: Path) -> None:
    import pandas as pd

    from core.scanner_universe import SCANNER_UNIVERSE_FULL_MARKET

    snapshot_path = tmp_path / "egx_live_snapshot.csv"
    pd.DataFrame(
        [
            {
                "symbol": f"RE{i}",
                "sector": "Real Estate",
                "change_percent": 2.0 + (i * 0.1),
                "volume": 1_000_000,
                "tv_relative_volume_10d": 1.8,
                "market_cap": 500_000_000,
            }
            for i in range(10)
        ]
    ).to_csv(snapshot_path, index=False)

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            f"RE{i}": _live_row(
                f"RE{i}",
                close=10.0 + i * 0.2,
                previous_close=9.8,
                volume=1_000_000,
                volume_ratio=1.8,
            )
            for i in range(10)
        },
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    candidates = [
        _scanner_result(
            "RE0",
            ScannerDecision.CANDIDATE,
            90,
            2.0,
            1.8,
            reasons=["Positive price change"],
        )
    ]
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=candidates,
        candidates=candidates,
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[],
        buy_setups=[],
        watch=[],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
        snapshot_path=snapshot_path,
    )
    top_section = next(
        section for section in report.sections if section.title == "Top Candidates"
    )
    top_text = "\n".join(top_section.lines)

    assert "sector HOT" in top_text
    assert "Sector:" in top_text
    assert "Rank factors:" in top_text


def test_top_candidates_include_fundamentals_line(tmp_path: Path) -> None:
    import pandas as pd

    from core.scanner_universe import SCANNER_UNIVERSE_FULL_MARKET

    snapshot_path = tmp_path / "egx_live_snapshot.csv"
    pd.DataFrame(
        [
            {
                "symbol": "COMI",
                "market_cap": 12_500_000_000,
                "pe_ratio": 9.4,
                "pb_ratio": 1.8,
                "dividend_yield": 3.2,
                "volume": 500_000,
                "volume_ratio": 2.0,
            }
        ]
    ).to_csv(snapshot_path, index=False)

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            "COMI": _live_row("COMI", 110.0, 100.0, 500_000, 2.0),
        },
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    candidates = [
        _scanner_result(
            "COMI",
            ScannerDecision.CANDIDATE,
            90,
            2.0,
            2.0,
            reasons=["Positive price change"],
        )
    ]
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=candidates,
        candidates=candidates,
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[],
        buy_setups=[],
        watch=[],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
        snapshot_path=snapshot_path,
    )
    top_section = next(
        section for section in report.sections if section.title == "Top Candidates"
    )
    top_text = "\n".join(top_section.lines)

    assert "Fundamentals:" in top_text
    assert "P/E 9.4" in top_text
    assert report.candidate_fundamentals
    assert report.candidate_fundamentals[0]["symbol"] == "COMI"


@pytest.fixture
def isolated_paper_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    portfolio_path = tmp_path / "portfolio_state.json"
    trades_path = tmp_path / "trades.json"
    monkeypatch.setattr(settings, "PORTFOLIO_STATE_PATH", portfolio_path)
    monkeypatch.setattr(settings, "TRADES_PATH", trades_path)
    return tmp_path


def _build_minimal_daily_report(
    live_snapshot: LiveMarketSnapshot,
    *,
    enable_portfolio_marking: bool = True,
) -> object:
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, blockers=[])
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=[],
        candidates=[],
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="test",
        results=[],
        buy_setups=[],
        watch=[],
        blocked=[],
    )
    return DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        enable_portfolio_marking=enable_portfolio_marking,
    )


def test_daily_report_without_storage_files_does_not_crash(
    isolated_paper_storage: Path,
) -> None:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            "COMI": _live_row("COMI", 88.5, 85.0, 100_000, 1.5),
        },
    )

    report = _build_minimal_daily_report(live_snapshot)
    section = next(
        section for section in report.sections if section.title == "Paper Portfolio"
    )

    assert "- No paper portfolio data found." in section.lines
    assert report.paper_portfolio["available"] is False


def test_daily_report_empty_portfolio_shows_no_open_positions(
    isolated_paper_storage: Path,
) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    TradeJournal().clear()

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            "COMI": _live_row("COMI", 88.5, 85.0, 100_000, 1.5),
        },
    )
    report = _build_minimal_daily_report(live_snapshot)
    section = next(
        section for section in report.sections if section.title == "Paper Portfolio"
    )

    assert "- Open Positions: 0" in section.lines
    assert "- No open paper positions to mark." in section.lines
    assert report.paper_portfolio["open_positions_count"] == 0


def test_daily_report_marks_open_position_pnl_correctly(
    isolated_paper_storage: Path,
) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    trade = portfolio.open_trade(
        symbol="COMI",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=85.0,
        stop_loss=82.0,
        take_profit=90.0,
        reason="test",
    )
    journal.append_trade(trade)

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            "COMI": _live_row("COMI", 88.5, 85.0, 100_000, 1.5),
        },
    )
    report = _build_minimal_daily_report(live_snapshot)
    section = next(
        section for section in report.sections if section.title == "Paper Portfolio"
    )
    section_text = "\n".join(section.lines)

    assert "COMI" in section_text
    assert "Current 88.50" in section_text
    assert "Value 8,850.00" in section_text
    assert "P&L +350.00 (+4.12%)" in section_text
    assert report.paper_portfolio["unrealized_pnl"] == pytest.approx(350.0)
    assert report.paper_portfolio["unrealized_pnl_pct"] == pytest.approx(4.117647, rel=1e-4)


def test_daily_report_missing_symbol_price_shows_warning(
    isolated_paper_storage: Path,
) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    trade = portfolio.open_trade(
        symbol="FWRY",
        side=TradeSide.BUY,
        quantity=500,
        entry_price=7.2,
        stop_loss=6.8,
        take_profit=8.0,
        reason="test",
    )
    journal.append_trade(trade)

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            "COMI": _live_row("COMI", 88.5, 85.0, 100_000, 1.5),
        },
    )
    report = _build_minimal_daily_report(live_snapshot)
    section = next(
        section for section in report.sections if section.title == "Paper Portfolio"
    )
    section_text = "\n".join(section.lines)

    assert "FWRY" in section_text
    assert "Current n/a" in section_text
    assert "price unavailable" in section_text
    assert report.paper_portfolio["positions"][0]["warning"] == "current price unavailable"


def test_daily_report_json_contains_paper_portfolio_key(
    isolated_paper_storage: Path,
    tmp_path: Path,
) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    trade = portfolio.open_trade(
        symbol="COMI",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=85.0,
        stop_loss=82.0,
        take_profit=90.0,
        reason="test",
    )
    journal.append_trade(trade)

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            "COMI": _live_row("COMI", 88.5, 85.0, 100_000, 1.5),
        },
    )
    report = _build_minimal_daily_report(live_snapshot)
    txt_path, json_path = save_daily_report(report, tmp_path)
    payload = json_path.read_text(encoding="utf-8")

    assert txt_path.exists()
    assert '"paper_portfolio"' in payload
    assert "Paper Portfolio:" in txt_path.read_text(encoding="utf-8")


def test_daily_report_paper_portfolio_section_order() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        enable_portfolio_marking=True,
    )
    section_titles = [section.title for section in report.sections]

    assert section_titles.index("Strategy Signals") < section_titles.index("Paper Portfolio")
    assert section_titles.index("Paper Portfolio") < section_titles.index(
        "Paper Trading Performance"
    )
    assert section_titles.index("Paper Trading Performance") < section_titles.index(
        "Strongest Movers"
    )


def test_disable_portfolio_marking_omits_section() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        enable_portfolio_marking=False,
    )
    section_titles = [section.title for section in report.sections]

    assert "Paper Portfolio" not in section_titles
    assert report.paper_portfolio == {}


def test_disable_performance_analytics_omits_section() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        talib_config=TalibTechnicalConfig(enabled=False),
        enable_performance_analytics=False,
    )
    section_titles = [section.title for section in report.sections]

    assert "Paper Trading Performance" not in section_titles
    assert report.paper_trading_performance == {}


def test_paper_trading_performance_with_open_positions(
    isolated_paper_storage: Path,
) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    trade = portfolio.open_trade(
        symbol="COMI",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=85.0,
        stop_loss=82.0,
        take_profit=90.0,
        reason="test",
    )
    journal.append_trade(trade)

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            "COMI": _live_row("COMI", 88.5, 85.0, 100_000, 1.5),
        },
    )
    report = _build_minimal_daily_report(live_snapshot)
    section = next(
        section
        for section in report.sections
        if section.title == "Paper Trading Performance"
    )
    section_text = "\n".join(section.lines)

    assert "Initial Capital:" in section_text
    assert "Current Equity:" in section_text
    assert "Total P&L:" in section_text
    assert "Realized P&L:" in section_text
    assert "Unrealized P&L: +350.00" in section_text
    assert "Closed Trades: 0" in section_text
    assert "Open Positions: 1" in section_text
    assert report.paper_trading_performance["available"] is True
    assert report.paper_trading_performance["unrealized_pnl"] == pytest.approx(350.0)


def test_paper_trading_performance_with_closed_trades(
    isolated_paper_storage: Path,
) -> None:
    from core.paper_engine import close_paper_trade

    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    trade = portfolio.open_trade(
        symbol="COMI",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=80.0,
        stop_loss=78.0,
        take_profit=84.0,
        reason="test",
    )
    journal.append_trade(trade)
    close_paper_trade(portfolio, journal, trade.id, exit_price=83.0)

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={},
    )
    report = _build_minimal_daily_report(live_snapshot)
    section = next(
        section
        for section in report.sections
        if section.title == "Paper Trading Performance"
    )
    section_text = "\n".join(section.lines)

    assert "Closed Trades: 1" in section_text
    assert "Winning Trades: 1" in section_text
    assert "Win Rate: 100.00%" in section_text
    assert "Best Trade: COMI +300.00" in section_text
    assert report.paper_trading_performance["realized_pnl"] == pytest.approx(300.0)


def test_daily_report_json_contains_paper_trading_performance_key(
    isolated_paper_storage: Path,
    tmp_path: Path,
) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={},
    )
    report = _build_minimal_daily_report(live_snapshot)
    _, json_path = save_daily_report(report, tmp_path)

    assert '"paper_trading_performance"' in json_path.read_text(encoding="utf-8")


def test_disable_talib_engine_omits_candidate_talib_lines() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        talib_config=TalibTechnicalConfig(enabled=False),
    )
    text = format_daily_report_text(report)

    assert report.candidate_talib_technical == []
    assert "Technical engines:" in text
    assert "TA-Lib: FALLBACK" in text
    assert "talib engine disabled" in text


def test_missing_talib_does_not_crash_daily_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.talib_technical.TALIB_AVAILABLE", False)
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        talib_config=TalibTechnicalConfig(enabled=True, min_history_days=50),
    )

    assert report.report_date == date(2026, 7, 1)
    assert report.sections
    assert TALIB_NOT_INSTALLED_WARNING in report.warnings
    assert report.candidate_talib_technical == []
    metadata = report.report_metadata
    assert metadata["talib_available"] is False
    assert metadata["talib_mode"] == "fallback"
    assert metadata["talib_reason"] == "talib package not installed"
    assert "talib_mode" in metadata
    assert metadata["talib_mode"] is not None

    summary = next(section for section in report.sections if section.title == "Summary")
    summary_text = "\n".join(summary.lines)
    assert "TA-Lib: FALLBACK" in summary_text

    confirmation = report.confirmation_summary
    if confirmation["signals"]:
        assert confirmation["signals"][0]["talib_status"] == "FALLBACK"


def test_talib_insufficient_history_line_in_top_candidates(
    isolated_paper_storage: Path,
) -> None:
    pytest.importorskip("talib")
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        talib_config=TalibTechnicalConfig(enabled=True, min_history_days=50),
    )
    candidates = next(
        section for section in report.sections if section.title == "Top Candidates"
    )
    candidate_text = "\n".join(candidates.lines)

    assert "TA-Lib: INSUFFICIENT_HISTORY" in candidate_text
    assert report.candidate_talib_technical
    assert report.candidate_talib_technical[0]["talib_technical"]["status"] == (
        "INSUFFICIENT_HISTORY"
    )


def test_market_session_section_uses_injected_now() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        now=sample_open_market_datetime(),
        talib_config=TalibTechnicalConfig(enabled=False),
        enable_performance_analytics=False,
    )

    market_session = report.sections[2]
    assert market_session.title == "Market Session"
    assert report.market_session["status"] == "OPEN"
    assert report.market_session["paper_entries_enabled"] is True
    assert "- Status: OPEN" in market_session.lines


def test_strategy_signals_mark_next_session_when_market_closed() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        now=sample_closed_market_datetime(),
        talib_config=TalibTechnicalConfig(enabled=False),
        enable_performance_analytics=False,
    )
    text = format_daily_report_text(report)

    assert report.market_session["paper_entries_enabled"] is False
    assert (
        "Market closed: signal is for next session watchlist, not immediate entry."
        in text
    )


def test_executive_summary_appears_before_summary_in_text() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        talib_config=TalibTechnicalConfig(enabled=False),
        enable_performance_analytics=False,
    )
    text = format_daily_report_text(report)

    assert text.index("Executive Summary:") < text.index("Summary:")


def test_executive_summary_json_contains_expected_keys() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        talib_config=TalibTechnicalConfig(enabled=False),
        enable_performance_analytics=False,
    )

    assert set(report.executive_summary) == {
        "market",
        "best_ideas",
        "action",
        "buy_plan",
        "sell_plan",
        "paper_pnl",
        "exit_plan",
        "confirmation",
        "main_risk",
    }


def test_executive_summary_closed_market_action_is_watch_only() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()
    strategy_report = StrategyReport(
        strategy_name="Trend Join Long",
        results=[
            _strategy_result("ELKA", StrategyDecision.BUY_SETUP),
        ],
        buy_setups=[
            _strategy_result("ELKA", StrategyDecision.BUY_SETUP),
        ],
        watch=[],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        now=sample_closed_market_datetime(),
        talib_config=TalibTechnicalConfig(enabled=False),
        enable_performance_analytics=False,
    )

    assert report.executive_summary["action"] == "Watch next session: ELKA"
    assert "Paper entries disabled" in report.executive_summary["market"]
    assert report.executive_summary["buy_plan"] == (
        "No new paper entries while EGX is closed."
    )
    assert "stale" in report.executive_summary["main_risk"].lower()
    assert report.report_metadata["closed_market_digest"]["enabled"] is True
    assert report.report_metadata["confidence_v2_available"] is True
    assert any(
        "market closed" in risk
        for context in report.confidence_v2_context.values()
        for risk in context.get("confidence_risks_v2", [])
    )
    digest_section = next(
        section for section in report.sections if section.title == "Closed Market Digest"
    )
    assert "Paper entries are disabled" in "\n".join(digest_section.lines)


def test_open_market_report_has_closed_digest_disabled() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        now=sample_open_market_datetime(),
        talib_config=TalibTechnicalConfig(enabled=False),
        enable_performance_analytics=False,
    )

    assert report.report_metadata["closed_market_digest"]["enabled"] is False
    assert not any(
        section.title == "Closed Market Digest" for section in report.sections
    )
    assert report.executive_summary["buy_plan"] == (
        "Use listed entry prices only during open market"
    )


def test_daily_report_includes_market_memory_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_path = tmp_path / "storage" / "market_memory.json"
    monkeypatch.setattr(
        "core.cloud_state_store._STATE_KEY_TO_LOCAL_PATH",
        {"storage/market_memory.json": memory_path},
    )
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        now=sample_open_market_datetime(),
        talib_config=TalibTechnicalConfig(enabled=False),
        enable_performance_analytics=False,
    )

    assert report.report_metadata["market_memory_available"] is True
    assert report.market_memory_summary["available"] is True
    assert report.market_memory_context
    assert report.report_metadata["confidence_v2_available"] is True
    assert report.confidence_v2_summary["available"] is True
    confidence_context = next(iter(report.confidence_v2_context.values()))
    assert "confidence_components_v2" in confidence_context
    confidence_section = next(
        section for section in report.sections if section.title == "Confidence V2 Summary"
    )
    assert any(
        "Good confidence" in line or "Strong confidence" in line
        for line in confidence_section.lines
    )
    memory_section = next(
        section for section in report.sections if section.title == "Market Memory"
    )
    assert any("New today" in line for line in memory_section.lines)
    candidates = next(
        section for section in report.sections if section.title == "Top Candidates"
    )
    assert "Confidence V2:" in "\n".join(candidates.lines)
    assert "Memory:" in "\n".join(candidates.lines)


def test_executive_summary_best_ideas_use_strategy_signals() -> None:
    live_snapshot, mood, scanner_report, strategy_report, warnings = _fake_scan_bundle()
    strategy_report = StrategyReport(
        strategy_name="Trend Join Long",
        results=[
            _strategy_result("ELKA", StrategyDecision.BUY_SETUP),
            _strategy_result("LCSW", StrategyDecision.BUY_SETUP),
            _strategy_result("TANM", StrategyDecision.BUY_SETUP),
        ],
        buy_setups=[
            _strategy_result("ELKA", StrategyDecision.BUY_SETUP),
            _strategy_result("LCSW", StrategyDecision.BUY_SETUP),
            _strategy_result("TANM", StrategyDecision.BUY_SETUP),
        ],
        watch=[],
        blocked=[],
    )

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=warnings,
        now=sample_open_market_datetime(),
        talib_config=TalibTechnicalConfig(enabled=False),
        enable_performance_analytics=False,
    )

    assert report.executive_summary["best_ideas"] == ["ELKA", "LCSW", "TANM"]


def test_executive_summary_includes_paper_pnl_when_performance_exists(
    isolated_paper_storage: Path,
) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    trade = portfolio.open_trade(
        symbol="COMI",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=85.0,
        stop_loss=82.0,
        take_profit=90.0,
        reason="test",
    )
    journal.append_trade(trade)

    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            "COMI": _live_row("COMI", 88.5, 85.0, 100_000, 1.5),
        },
    )
    report = _build_minimal_daily_report(live_snapshot)

    assert "Open positions: 1" in report.executive_summary["paper_pnl"]
    assert report.executive_summary["paper_pnl"].startswith("+")
    assert report.paper_trading_performance["available"] is True
