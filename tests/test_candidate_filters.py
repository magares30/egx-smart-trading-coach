"""Tests for EGX candidate filtering and top-N controls."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from config import settings
from core.candidate_filters import (
    CandidateFilters,
    DEFAULT_TOP_CANDIDATES,
    build_candidate_filter_summary_lines,
    build_candidate_filters_from_cli,
    filter_candidates_for_display,
    filter_candidates_for_strategy,
    filter_strategy_report,
    passes_candidate_filters,
)
from core.daily_report import DailyReportBuilder, format_daily_report_text
from core.live_snapshot import LiveMarketSnapshot, LiveSymbolSnapshot
from core.market_data import MarketSnapshot
from core.market_mood import MarketMood, MarketMoodResult
from core.models import SignalType, TradeSignal
from core.portfolio import VirtualPortfolio
from core.risk import RiskManager
from core.scanner import ScannerDecision, ScannerReport, ScannerResult
from core.scanner_universe import SCANNER_UNIVERSE_FULL_MARKET
from core.strategy import StrategyDecision, StrategyReport, StrategyResult
from core.trade_journal import TradeJournal
from main import (
    LiveScanPipelineResult,
    _filtered_strategy_report,
    _open_live_paper_trades_from_pipeline,
    _print_scanner_report_results,
    parse_args,
)


def _scanner_result(
    symbol: str,
    *,
    score: int = 80,
    volume_ratio: float = 1.5,
) -> ScannerResult:
    return ScannerResult(
        symbol=symbol,
        decision=ScannerDecision.CANDIDATE,
        score=score,
        latest_close=100.0,
        change_percent=1.0,
        volume_ratio=volume_ratio,
        broke_previous_high=True,
        above_sma_5=True,
        reasons=["Positive price change"],
        blockers=[],
    )


def _buy_setup(symbol: str, confidence: int = 85) -> StrategyResult:
    signal = TradeSignal(
        symbol=symbol,
        signal_type=SignalType.BUY_SETUP,
        entry_price=10.0,
        stop_loss=9.5,
        take_profit=11.0,
        confidence_score=confidence,
        reasons=["Scanner marked symbol as candidate"],
    )
    return StrategyResult(
        symbol=symbol,
        decision=StrategyDecision.BUY_SETUP,
        signal=signal,
        entry_price=10.0,
        stop_loss=9.5,
        take_profit=11.0,
        risk_reward=2.0,
        confidence_score=confidence,
        reasons=["Scanner marked symbol as candidate"],
    )


def _many_candidates(count: int) -> list[ScannerResult]:
    return [
        _scanner_result(f"CAND{i}", score=90 - i, volume_ratio=1.0 + (i * 0.1))
        for i in range(count)
    ]


def test_default_candidate_filters_from_cli() -> None:
    filters = build_candidate_filters_from_cli()
    assert filters.top_candidates == DEFAULT_TOP_CANDIDATES
    assert filters.min_score is None
    assert filters.min_volume_ratio is None


def test_parse_args_candidate_filter_flags() -> None:
    args = parse_args(
        [
            "--top-candidates",
            "20",
            "--min-score",
            "75",
            "--min-volume-ratio",
            "1.2",
        ]
    )
    filters = build_candidate_filters_from_cli(
        top_candidates=args.top_candidates,
        min_score=args.min_score,
        min_volume_ratio=args.min_volume_ratio,
    )
    assert filters.top_candidates == 20
    assert filters.min_score == 75
    assert filters.min_volume_ratio == 1.2


def test_top_candidates_limits_display_only() -> None:
    candidates = _many_candidates(15)
    filters = CandidateFilters(top_candidates=5)

    displayed = filter_candidates_for_display(candidates, filters)
    strategy_pool = filter_candidates_for_strategy(candidates, filters)

    assert len(displayed) == 5
    assert len(strategy_pool) == 15
    assert displayed[0].symbol == "CAND0"


def test_min_score_filters_candidates() -> None:
    candidates = [
        _scanner_result("HIGH", score=85),
        _scanner_result("LOW", score=60),
    ]
    filters = CandidateFilters(min_score=75)

    filtered = filter_candidates_for_strategy(candidates, filters)

    assert [item.symbol for item in filtered] == ["HIGH"]
    assert passes_candidate_filters(candidates[0], filters) is True
    assert passes_candidate_filters(candidates[1], filters) is False


def test_min_volume_ratio_filters_candidates() -> None:
    candidates = [
        _scanner_result("STRONG", volume_ratio=2.0),
        _scanner_result("WEAK", volume_ratio=0.8),
    ]
    filters = CandidateFilters(min_volume_ratio=1.2)

    filtered = filter_candidates_for_strategy(candidates, filters)

    assert [item.symbol for item in filtered] == ["STRONG"]


def test_full_market_report_with_filters_shows_fewer_candidates() -> None:
    live_snapshot = LiveMarketSnapshot(
        as_of_date=date(2026, 7, 1),
        symbols={
            f"SYM{i}": LiveSymbolSnapshot(
                symbol=f"SYM{i}",
                date=date(2026, 7, 1),
                previous_close=99.0,
                open=99.0,
                high=101.0,
                low=98.0,
                close=100.0,
                volume=1000.0,
                change_percent=1.0,
                volume_ratio=1.0 + (i * 0.05),
                broke_previous_high=True,
            )
            for i in range(20)
        },
    )
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, reasons=[], blockers=[])
    candidates = _many_candidates(20)
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=candidates,
        candidates=candidates,
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="Trend Join Long",
        results=[],
        buy_setups=[],
        watch=[],
        blocked=[],
    )
    filters = CandidateFilters(top_candidates=10, min_score=75)

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=[],
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
        candidate_filters=filters,
    )

    summary = next(section for section in report.sections if section.title == "Summary")
    top_candidates = next(
        section for section in report.sections if section.title == "Top Candidates"
    )
    numbered_lines = [
        line for line in top_candidates.lines if line[:1].isdigit() and ". " in line[:4]
    ]

    assert "- Candidates: 20" in summary.lines
    assert len(numbered_lines) == 10
    assert all("Score 7" in line or "Score 8" in line or "Score 9" in line for line in numbered_lines)
    assert "CAND15" not in "\n".join(top_candidates.lines)


def test_report_includes_candidate_filters_summary() -> None:
    live_snapshot = LiveMarketSnapshot(as_of_date=date(2026, 7, 1), symbols={})
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, reasons=[], blockers=[])
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=[],
        candidates=[],
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="Trend Join Long",
        results=[],
        buy_setups=[],
        watch=[],
        blocked=[],
    )
    filters = CandidateFilters(top_candidates=10, min_score=75, min_volume_ratio=1.2)

    report = DailyReportBuilder().build_from_live_scan(
        live_snapshot,
        mood,
        scanner_report,
        strategy_report,
        warnings=[],
        candidate_filters=filters,
    )
    text = format_daily_report_text(report)
    filter_section = next(
        section for section in report.sections if section.title == "Candidate Filters"
    )

    assert "Candidate Filters:" in text
    expected_filter_lines = build_candidate_filter_summary_lines(filters)
    assert filter_section.lines[:len(expected_filter_lines)] == expected_filter_lines
    assert any("Candidate Ranking" in line for line in filter_section.lines)
    assert any("Tie-breakers" in line for line in filter_section.lines)
    assert "- Top candidates limit: 10" in filter_section.lines
    assert "- Min score: 75" in filter_section.lines
    assert "- Min relative volume: 1.2" in filter_section.lines


def test_filter_strategy_report_respects_min_score() -> None:
    candidates = [
        _scanner_result("KEEP", score=85),
        _scanner_result("DROP", score=60),
    ]
    scanner_report = ScannerReport(
        market_mood="NEUTRAL",
        results=candidates,
        candidates=candidates,
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="Trend Join Long",
        results=[],
        buy_setups=[_buy_setup("KEEP"), _buy_setup("DROP")],
        watch=[],
        blocked=[],
    )
    filters = CandidateFilters(min_score=75)

    filtered = filter_strategy_report(strategy_report, scanner_report, filters)

    assert [item.symbol for item in filtered.buy_setups] == ["KEEP"]


def test_filter_candidates_max_pe_excludes_expensive_candidate() -> None:
    import pandas as pd

    candidates = [
        _scanner_result("CHEAP", score=85),
        _scanner_result("RICH", score=85),
    ]
    snapshot_df = pd.DataFrame(
        [
            {
                "symbol": "CHEAP",
                "pe_ratio": 12.0,
                "pb_ratio": 1.5,
                "market_cap": 2_000_000_000,
            },
            {
                "symbol": "RICH",
                "pe_ratio": 40.0,
                "pb_ratio": 1.5,
                "market_cap": 2_000_000_000,
            },
        ]
    )
    filters = CandidateFilters(max_pe=25.0)

    kept = filter_candidates_for_strategy(candidates, filters, snapshot_df)

    assert [item.symbol for item in kept] == ["CHEAP"]


def test_filter_candidates_require_fundamentals_excludes_unknown() -> None:
    import pandas as pd

    candidates = [
        _scanner_result("KNOWN", score=85),
        _scanner_result("UNKNOWN", score=85),
    ]
    snapshot_df = pd.DataFrame(
        [
            {
                "symbol": "KNOWN",
                "market_cap": 2_000_000_000,
                "pe_ratio": 10.0,
            },
            {"symbol": "UNKNOWN"},
        ]
    )
    filters = CandidateFilters(require_fundamentals=True)

    kept = filter_candidates_for_strategy(candidates, filters, snapshot_df)

    assert [item.symbol for item in kept] == ["KNOWN"]


def test_print_scanner_report_results_respects_top_candidates(capsys) -> None:
    scanner_report = ScannerReport(
        market_mood="NEUTRAL",
        results=_many_candidates(12),
        candidates=_many_candidates(12),
        watchlist=[],
        blocked=[],
    )

    _print_scanner_report_results(
        scanner_report,
        candidate_filters=CandidateFilters(top_candidates=3),
    )
    output = capsys.readouterr().out

    assert "CANDIDATES:" in output
    assert "CAND0" in output
    assert "CAND2" in output
    assert "CAND3" not in output


@pytest.fixture
def tmp_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    portfolio_path = tmp_path / "portfolio_state.json"
    trades_path = tmp_path / "trades.json"
    monkeypatch.setattr(settings, "PORTFOLIO_STATE_PATH", portfolio_path)
    monkeypatch.setattr(settings, "TRADES_PATH", trades_path)
    return tmp_path


def _pipeline_with_buy_setups(
    *,
    candidates: list[ScannerResult],
    buy_symbols: list[str],
    scanner_universe: str = "watchlist",
    filters: CandidateFilters | None = None,
) -> LiveScanPipelineResult:
    mood = MarketMoodResult(mood=MarketMood.NEUTRAL, score=50, reasons=[], blockers=[])
    scanner_report = ScannerReport(
        market_mood=mood.mood.value,
        results=candidates,
        candidates=candidates,
        watchlist=[],
        blocked=[],
    )
    strategy_report = StrategyReport(
        strategy_name="Trend Join Long",
        results=[],
        buy_setups=[_buy_setup(symbol) for symbol in buy_symbols],
        watch=[],
        blocked=[],
    )
    return LiveScanPipelineResult(
        live_snapshot=LiveMarketSnapshot(as_of_date=date(2026, 7, 1), symbols={}),
        market_snapshot=MarketSnapshot(symbols=[], index_snapshots=[]),
        mood_result=mood,
        scanner_report=scanner_report,
        strategy_report=strategy_report,
        warnings=[],
        snapshot_path=Path("data/live/egx_live_snapshot.csv"),
        lookback_days=5,
        min_history_days=3,
        scanner_universe=scanner_universe,
        candidate_filters=filters or CandidateFilters(),
    )


def test_paper_trade_respects_min_score_and_max_trades(tmp_storage: Path) -> None:
    candidates = [
        _scanner_result("HIGH1", score=90),
        _scanner_result("HIGH2", score=85),
        _scanner_result("LOW", score=60),
    ]
    pipeline = _pipeline_with_buy_setups(
        candidates=candidates,
        buy_symbols=["HIGH1", "HIGH2", "LOW"],
        filters=CandidateFilters(min_score=75),
    )

    VirtualPortfolio().reset()
    TradeJournal().clear()
    report = _open_live_paper_trades_from_pipeline(
        pipeline,
        max_trades=2,
        min_confidence=75,
        ignore_market_hours=True,
    )

    opened_symbols = [
        result.symbol
        for result in report.results
        if result.decision.value == "OPENED"
    ]
    assert opened_symbols == ["HIGH1", "HIGH2"]
    assert "LOW" not in opened_symbols


def test_paper_trade_full_market_prints_safety_notice(
    tmp_storage: Path,
    capsys,
) -> None:
    candidates = [_scanner_result("HIGH1", score=90)]
    pipeline = _pipeline_with_buy_setups(
        candidates=candidates,
        buy_symbols=["HIGH1"],
        scanner_universe=SCANNER_UNIVERSE_FULL_MARKET,
    )

    VirtualPortfolio().reset()
    TradeJournal().clear()
    _open_live_paper_trades_from_pipeline(
        pipeline,
        max_trades=3,
        min_confidence=75,
        ignore_market_hours=True,
    )
    output = capsys.readouterr().out

    assert "Full-market paper trading enabled; max trades limit is 3." in output


def test_filtered_strategy_report_ignores_top_candidates_limit() -> None:
    candidates = _many_candidates(12)
    pipeline = _pipeline_with_buy_setups(
        candidates=candidates,
        buy_symbols=["CAND0", "CAND11"],
        filters=CandidateFilters(top_candidates=3, min_score=70),
    )

    filtered = _filtered_strategy_report(pipeline)

    assert {item.symbol for item in filtered.buy_setups} == {"CAND0", "CAND11"}
