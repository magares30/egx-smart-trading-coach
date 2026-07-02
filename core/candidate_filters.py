"""Candidate filtering for EGX live scan reports and paper trading."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.candidate_ranking import (
    CandidateRankingConfig,
    order_strategy_signals_by_rank,
    rank_candidates,
)
from core.scanner import ScannerReport, ScannerResult
from core.strategy import StrategyReport, StrategyResult
from core.technical_confirmation import TechnicalConfirmationConfig, row_for_symbol
from core.relative_volume import resolve_volume_ratio
from core.fundamental_quality import (
    FundamentalQualityConfig,
    passes_fundamental_filters,
)

DEFAULT_TOP_CANDIDATES = 10


@dataclass(frozen=True)
class CandidateFilters:
    """Display and trade filters applied after scanner scoring."""

    top_candidates: int = DEFAULT_TOP_CANDIDATES
    min_score: int | None = None
    min_volume_ratio: float | None = None
    min_market_cap_quality: float | None = None
    max_pe: float | None = None
    max_pb: float | None = None
    require_fundamentals: bool = False

    def has_score_or_volume_filters(self) -> bool:
        return self.min_score is not None or self.min_volume_ratio is not None

    def has_fundamental_filters(self) -> bool:
        return (
            self.min_market_cap_quality is not None
            or self.max_pe is not None
            or self.max_pb is not None
            or self.require_fundamentals
        )

    def has_any_filters(self) -> bool:
        return self.has_score_or_volume_filters() or self.has_fundamental_filters()


def _effective_volume_ratio(
    candidate: ScannerResult,
    snapshot_df: pd.DataFrame | None,
) -> float:
    """Prefer TradingView relative volume from snapshot rows when filtering."""
    if snapshot_df is None:
        return candidate.volume_ratio
    row = row_for_symbol(snapshot_df, candidate.symbol)
    resolved = resolve_volume_ratio(candidate.volume_ratio, row)
    if resolved is not None:
        return resolved
    return candidate.volume_ratio


def passes_candidate_filters(
    candidate: ScannerResult,
    filters: CandidateFilters,
    *,
    snapshot_df: pd.DataFrame | None = None,
    fundamental_config: FundamentalQualityConfig | None = None,
) -> bool:
    """Return True when a scanner candidate passes optional score/volume filters."""
    if filters.min_score is not None and candidate.score < filters.min_score:
        return False
    if filters.min_volume_ratio is not None:
        volume_ratio = _effective_volume_ratio(candidate, snapshot_df)
        if volume_ratio < filters.min_volume_ratio:
            return False
    if filters.has_fundamental_filters():
        row = row_for_symbol(snapshot_df, candidate.symbol)
        if not passes_fundamental_filters(
            row,
            min_market_cap_quality=filters.min_market_cap_quality,
            max_pe=filters.max_pe,
            max_pb=filters.max_pb,
            require_fundamentals=filters.require_fundamentals,
            config=fundamental_config,
        ):
            return False
    return True


def filter_candidates_for_strategy(
    candidates: list[ScannerResult],
    filters: CandidateFilters,
    snapshot_df: pd.DataFrame | None = None,
    fundamental_config: FundamentalQualityConfig | None = None,
) -> list[ScannerResult]:
    """Apply score/volume filters without the display-only top-candidates limit."""
    if not filters.has_any_filters():
        return list(candidates)
    return [
        candidate
        for candidate in candidates
        if passes_candidate_filters(
            candidate,
            filters,
            snapshot_df=snapshot_df,
            fundamental_config=fundamental_config,
        )
    ]


def _strategy_results_by_symbol(
    strategy_report: StrategyReport | None,
) -> dict[str, StrategyResult]:
    if strategy_report is None:
        return {}
    return {item.symbol: item for item in strategy_report.results}


def prepare_ranked_candidates(
    candidates: list[ScannerResult],
    filters: CandidateFilters,
    snapshot_df: pd.DataFrame | None,
    ranking_config: CandidateRankingConfig | None = None,
    *,
    strategy_report: StrategyReport | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
    fundamental_config: FundamentalQualityConfig | None = None,
) -> tuple[list[ScannerResult], list[ScannerResult]]:
    """Filter, rank, and split into full ranked list and top-N display list."""
    config = ranking_config or CandidateRankingConfig()
    filtered = filter_candidates_for_strategy(
        candidates,
        filters,
        snapshot_df,
        fundamental_config,
    )
    ranked = rank_candidates(
        filtered,
        snapshot_df,
        config,
        strategy_by_symbol=_strategy_results_by_symbol(strategy_report),
        technical_config=technical_config,
    )
    return ranked, ranked[: filters.top_candidates]


def filter_candidates_for_display(
    candidates: list[ScannerResult],
    filters: CandidateFilters,
    *,
    snapshot_df: pd.DataFrame | None = None,
    ranking_config: CandidateRankingConfig | None = None,
    strategy_report: StrategyReport | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
    fundamental_config: FundamentalQualityConfig | None = None,
) -> list[ScannerResult]:
    """Apply score/volume filters, rank tie-breakers, and the display limit."""
    _, display = prepare_ranked_candidates(
        candidates,
        filters,
        snapshot_df,
        ranking_config,
        strategy_report=strategy_report,
        technical_config=technical_config,
        fundamental_config=fundamental_config,
    )
    return display


def ranked_strategy_signals_for_display(
    strategy_report: StrategyReport,
    scanner_report: ScannerReport,
    filters: CandidateFilters,
    snapshot_df: pd.DataFrame | None,
    ranking_config: CandidateRankingConfig | None = None,
    technical_config: TechnicalConfirmationConfig | None = None,
    fundamental_config: FundamentalQualityConfig | None = None,
) -> list[StrategyResult]:
    """Filter strategy signals and order them by ranked candidate priority."""
    filtered = filter_strategy_report(
        strategy_report,
        scanner_report,
        filters,
        snapshot_df,
        fundamental_config=fundamental_config,
    )
    ranked_candidates, _ = prepare_ranked_candidates(
        scanner_report.candidates,
        filters,
        snapshot_df,
        ranking_config,
        strategy_report=filtered,
        technical_config=technical_config,
        fundamental_config=fundamental_config,
    )
    return order_strategy_signals_by_rank(
        filtered.buy_setups + filtered.watch,
        ranked_candidates,
    )


def eligible_candidate_symbols(
    scanner_report: ScannerReport,
    filters: CandidateFilters,
    snapshot_df: pd.DataFrame | None = None,
    fundamental_config: FundamentalQualityConfig | None = None,
) -> set[str]:
    """Symbols from scanner candidates that pass strategy/trade filters."""
    return {
        candidate.symbol
        for candidate in filter_candidates_for_strategy(
            scanner_report.candidates,
            filters,
            snapshot_df,
            fundamental_config,
        )
    }


def filter_strategy_report(
    strategy_report: StrategyReport,
    scanner_report: ScannerReport,
    filters: CandidateFilters,
    snapshot_df: pd.DataFrame | None = None,
    fundamental_config: FundamentalQualityConfig | None = None,
) -> StrategyReport:
    """Keep strategy signals only for candidates that pass score/volume filters."""
    if not filters.has_any_filters():
        return strategy_report

    eligible_symbols = eligible_candidate_symbols(
        scanner_report,
        filters,
        snapshot_df,
        fundamental_config,
    )
    buy_setups = [
        item for item in strategy_report.buy_setups if item.symbol in eligible_symbols
    ]
    watch = [item for item in strategy_report.watch if item.symbol in eligible_symbols]
    blocked = [
        item for item in strategy_report.blocked if item.symbol in eligible_symbols
    ]
    results = [
        item for item in strategy_report.results if item.symbol in eligible_symbols
    ]
    return StrategyReport(
        strategy_name=strategy_report.strategy_name,
        results=results,
        buy_setups=buy_setups,
        watch=watch,
        blocked=blocked,
    )


def build_candidate_filter_summary_lines(filters: CandidateFilters) -> list[str]:
    """Build report summary lines describing active candidate filters."""
    min_score = "none" if filters.min_score is None else str(filters.min_score)
    min_relative_volume = (
        "none"
        if filters.min_volume_ratio is None
        else f"{filters.min_volume_ratio:g}"
    )
    min_market_cap_quality = (
        "none"
        if filters.min_market_cap_quality is None
        else f"{filters.min_market_cap_quality:g}"
    )
    max_pe = "none" if filters.max_pe is None else f"{filters.max_pe:g}"
    max_pb = "none" if filters.max_pb is None else f"{filters.max_pb:g}"
    require_fundamentals = "yes" if filters.require_fundamentals else "no"
    return [
        f"- Top candidates limit: {filters.top_candidates}",
        f"- Min score: {min_score}",
        f"- Min relative volume: {min_relative_volume}",
        f"- Min market cap quality: {min_market_cap_quality}",
        f"- Max P/E: {max_pe}",
        f"- Max P/B: {max_pb}",
        f"- Require fundamentals: {require_fundamentals}",
    ]


def build_candidate_filters_from_cli(
    *,
    top_candidates: int | None = None,
    min_score: int | None = None,
    min_volume_ratio: float | None = None,
    min_market_cap_quality: float | None = None,
    max_pe: float | None = None,
    max_pb: float | None = None,
    require_fundamentals: bool = False,
) -> CandidateFilters:
    """Build candidate filters from optional CLI values."""
    return CandidateFilters(
        top_candidates=(
            top_candidates
            if top_candidates is not None
            else DEFAULT_TOP_CANDIDATES
        ),
        min_score=min_score,
        min_volume_ratio=min_volume_ratio,
        min_market_cap_quality=min_market_cap_quality,
        max_pe=max_pe,
        max_pb=max_pb,
        require_fundamentals=require_fundamentals,
    )
