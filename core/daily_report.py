"""Build and save daily EGX reports from live scan results."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from config import settings
from config.watchlist import DEFAULT_WATCHLIST
from core.live_snapshot import LiveMarketSnapshot
from core.live_volume import LiveVolumeHistoryStore
from core.market_hours import (
    EgxMarketSession,
    detect_egx_market_session,
    format_market_session_report_lines,
)
from core.market_mood import MarketMoodResult
from core.market_breadth_mood import (
    MarketBreadthMoodResult,
    format_market_breadth_mood_report_lines,
)
from core.scanner import ScannerReport, ScannerResult
from core.scanner_universe import (
    DEFAULT_SCANNER_UNIVERSE,
    format_scanner_universe_label,
    is_full_market_universe,
)
from core.candidate_filters import (
    CandidateFilters,
    DEFAULT_TOP_CANDIDATES,
    build_candidate_filter_summary_lines,
    filter_candidates_for_display,
    ranked_strategy_signals_for_display,
)
from core.candidate_ranking import (
    CandidateRankingConfig,
    build_candidate_ranking_dataframe,
    build_candidate_ranking_summary_lines,
    display_volume_ratio_for_candidate,
    format_candidate_ranking_note,
    format_candidate_technical_line,
)
from core.relative_volume import format_relative_volume_display
from core.technical_confirmation import (
    TechnicalConfirmationConfig,
    evaluate_technical_confirmation,
    row_for_symbol,
    technical_fields_available_in_dataframe,
)
from core.market_data_providers import (
    DATA_PROVIDER_TRADINGVIEW,
    format_data_provider_label,
)
from core.tradingview_data_provider import (
    TradingViewQueryFilterConfig,
    TradingViewQueryPrefilterDiagnostics,
    build_tradingview_query_prefilter_summary_lines,
)
from core.market_quality_filters import (
    MarketQualityFilterResult,
    allowed_symbols_from_quality_result,
    build_market_quality_filter_summary_lines,
    quality_filtered_symbol_snapshots,
)
from core.fundamental_quality import (
    FundamentalQualityConfig,
    evaluate_fundamental_quality,
    format_candidate_fundamental_line,
    fundamental_fields_available_in_dataframe,
)
from core.multi_timeframe import (
    EntryTimingStatus,
    MultiTimeframeConfig,
    MultiTimeframeResult,
    build_entry_timing_lookup,
    evaluate_entry_timing,
    format_entry_timing_line,
    row_for_symbol_timeframes,
)
from core.sector_momentum import (
    build_sector_momentum,
    format_sector_momentum_lines,
    sector_status_for_symbol,
)
from core.executive_summary import build_executive_summary
from core.confirmation_summary import (
    SignalConfirmationSummary,
    build_confirmation_summary,
    build_signal_confirmation_summary,
)
from core.exit_plan import (
    ExitPlanLabel,
    PositionExitPlan,
    build_exit_plan_summary,
)
from core.decision_labels import (
    DecisionLabel,
    PositionDecision,
    SignalDecision,
    build_decision_summary,
    classify_strategy_signal_decision,
    format_strategy_decision_line,
)
from core.portfolio_report import (
    build_daily_report_paper_portfolio,
    build_daily_report_paper_trading_performance,
    load_portfolio_for_marking,
    load_trade_journal_for_report,
    paper_portfolio_storage_exists,
)
from core.talib_technical import (
    TalibTechnicalConfig,
    TalibTechnicalResult,
    build_talib_lookup_for_symbols,
    format_talib_technical_line,
    is_talib_engine_available,
    TALIB_NOT_INSTALLED_WARNING,
)
from core.strategy import StrategyReport, StrategyResult
from core.warning_formatting import summarize_daily_report_warnings

REPORT_SOURCE_LIVE_SNAPSHOT = "EGX Live Snapshot"
MAX_LIST_ITEMS = 10


def _safe_snapshot_volume(volume: float | None) -> float:
    """Coerce snapshot volume to a non-negative float for sorting."""
    if volume is None:
        return 0.0
    try:
        numeric = float(volume)
    except (TypeError, ValueError):
        return 0.0
    if numeric != numeric:
        return 0.0
    return max(numeric, 0.0)


class DailyReportSection(BaseModel):
    title: str
    lines: list[str] = Field(default_factory=list)


class DailyReport(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    report_date: date
    source: str
    sections: list[DailyReportSection] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sector_momentum: list[dict[str, object]] = Field(default_factory=list)
    candidate_fundamentals: list[dict[str, object]] = Field(default_factory=list)
    candidate_entry_timing: list[dict[str, object]] = Field(default_factory=list)
    tv_query_prefilter: dict[str, object] = Field(default_factory=dict)
    market_breadth_mood: dict[str, object] = Field(default_factory=dict)
    paper_portfolio: dict[str, object] = Field(default_factory=dict)
    paper_trading_performance: dict[str, object] = Field(default_factory=dict)
    market_session: dict[str, object] = Field(default_factory=dict)
    executive_summary: dict[str, object] = Field(default_factory=dict)
    decision_summary: dict[str, object] = Field(default_factory=dict)
    exit_plan_summary: dict[str, object] = Field(default_factory=dict)
    confirmation_summary: dict[str, object] = Field(default_factory=dict)
    candidate_talib_technical: list[dict[str, object]] = Field(default_factory=list)


def _resolve_tv_query_prefilter_diagnostics(
    tv_query_filter_config: TradingViewQueryFilterConfig | None,
    tv_query_prefilter_diagnostics: TradingViewQueryPrefilterDiagnostics | None,
) -> TradingViewQueryPrefilterDiagnostics:
    if tv_query_prefilter_diagnostics is not None:
        return tv_query_prefilter_diagnostics
    config = tv_query_filter_config or TradingViewQueryFilterConfig()
    return TradingViewQueryPrefilterDiagnostics(enabled=config.enabled)


class DailyReportBuilder:
    """Build readable daily reports from live scan outputs."""

    def _format_scanner_item(
        self,
        index: int,
        item: ScannerResult,
        *,
        ranking_note: str | None = None,
        technical_note: str | None = None,
        fundamental_note: str | None = None,
        entry_timing_note: str | None = None,
        talib_note: str | None = None,
        display_volume_ratio: float | None = None,
    ) -> list[str]:
        volume_ratio = (
            display_volume_ratio
            if display_volume_ratio is not None
            else item.volume_ratio
        )
        header = (
            f"{index}. {item.symbol} | Score {item.score} | "
            f"Change {item.change_percent:+.2f}% | "
            f"Volume {format_relative_volume_display(volume_ratio)}"
        )
        reason_parts = item.reasons or item.blockers
        reason_text = ", ".join(reason_parts) if reason_parts else "(none)"
        lines = [header, f"   Reasons: {reason_text}"]
        if ranking_note:
            lines.append(f"   {ranking_note}")
        if technical_note:
            lines.append(f"   {technical_note}")
        if fundamental_note:
            lines.append(f"   {fundamental_note}")
        if entry_timing_note:
            lines.append(f"   {entry_timing_note}")
        if talib_note:
            lines.append(f"   {talib_note}")
        return lines

    def _format_strategy_item(
        self,
        index: int,
        item: StrategyResult,
        *,
        entry_timing_status: str | None = None,
        market_closed_note: str | None = None,
        signal_decision: SignalDecision | None = None,
        signal_confirmation: SignalConfirmationSummary | None = None,
    ) -> list[str]:
        decision = item.decision.value
        decision_prefix = (
            f"Decision {signal_decision.label.value} | "
            if signal_decision is not None
            else ""
        )
        if item.entry_price is not None and item.stop_loss is not None:
            target = item.take_profit if item.take_profit is not None else 0.0
            timing_suffix = (
                f" | Timing {entry_timing_status}"
                if entry_timing_status
                else ""
            )
            header = (
                f"{index}. {item.symbol} | {decision} | {decision_prefix}"
                f"Entry {item.entry_price:.2f} | Stop {item.stop_loss:.2f} | "
                f"Target {target:.2f}{timing_suffix}"
            )
        else:
            header = (
                f"{index}. {item.symbol} | {decision}"
                + (f" | {decision_prefix.rstrip(' | ')}" if decision_prefix else "")
            )
        reason_parts = item.reasons or item.blockers
        reason_text = ", ".join(reason_parts) if reason_parts else "(none)"
        lines = [header, f"   Reason: {reason_text}"]
        if signal_decision is not None:
            lines.append(format_strategy_decision_line(signal_decision))
        if signal_confirmation is not None:
            lines.append(f"   {signal_confirmation.confirmation_text}")
        if market_closed_note:
            lines.append(f"   - {market_closed_note}")
        return lines

    def _blocked_reason_counts(self, scanner_report: ScannerReport) -> list[str]:
        counts: Counter[str] = Counter()
        for item in scanner_report.blocked:
            if item.blockers:
                for blocker in item.blockers:
                    counts[blocker] += 1
            else:
                counts["Low scanner score"] += 1

        if not counts:
            return ["- (none)"]

        return [
            f"- {reason}: {count}"
            for reason, count in counts.most_common(MAX_LIST_ITEMS)
        ]

    def _configured_watchlist_lines(
        self,
        scanner_report: ScannerReport,
        live_snapshot: LiveMarketSnapshot,
        watchlist: list[str],
        watchlist_scanner_results: dict[str, ScannerResult] | None = None,
    ) -> list[str]:
        """Show configured watchlist symbol diagnostics without limiting scan universe."""
        lines: list[str] = []
        results_by_symbol = {item.symbol: item for item in scanner_report.results}
        if watchlist_scanner_results:
            results_by_symbol.update(watchlist_scanner_results)
        for index, symbol in enumerate(watchlist[:MAX_LIST_ITEMS], start=1):
            if symbol not in live_snapshot.symbols:
                lines.append(f"{index}. {symbol} | missing from live snapshot")
                continue
            result = results_by_symbol.get(symbol)
            if result is None:
                lines.append(f"{index}. {symbol} | not scanned")
                continue
            lines.extend(self._format_scanner_item(index, result))
        return lines or ["- (none)"]

    def build_from_live_scan(
        self,
        live_snapshot: LiveMarketSnapshot,
        market_mood: MarketMoodResult,
        scanner_report: ScannerReport,
        strategy_report: StrategyReport,
        warnings: list[str] | None = None,
        *,
        scanner_universe: str = DEFAULT_SCANNER_UNIVERSE,
        configured_watchlist: list[str] | None = None,
        candidate_filters: CandidateFilters | None = None,
        data_provider: str | None = None,
        quality_filter_result: MarketQualityFilterResult | None = None,
        watchlist_scanner_results: dict[str, ScannerResult] | None = None,
        ranking_config: CandidateRankingConfig | None = None,
        snapshot_path: Path | None = None,
        technical_config: TechnicalConfirmationConfig | None = None,
        multi_timeframe_config: MultiTimeframeConfig | None = None,
        timeframe_snapshot_df: pd.DataFrame | None = None,
        tv_query_filter_config: TradingViewQueryFilterConfig | None = None,
        tv_query_prefilter_diagnostics: TradingViewQueryPrefilterDiagnostics | None = None,
        market_breadth_mood_result: MarketBreadthMoodResult | None = None,
        enable_portfolio_marking: bool = True,
        enable_performance_analytics: bool = True,
        latest_prices: dict[str, float] | None = None,
        ignore_market_hours: bool = False,
        market_session: EgxMarketSession | None = None,
        now: datetime | None = None,
        talib_config: TalibTechnicalConfig | None = None,
    ) -> DailyReport:
        """Build a daily report from live snapshot scan outputs."""
        report_warnings = summarize_daily_report_warnings(list(warnings or []))
        watchlist_symbols = configured_watchlist or DEFAULT_WATCHLIST
        filters = candidate_filters or CandidateFilters()
        ranking_values = ranking_config or CandidateRankingConfig()
        technical_values = technical_config or TechnicalConfirmationConfig()
        fundamental_values = FundamentalQualityConfig()
        multi_timeframe_values = multi_timeframe_config or MultiTimeframeConfig()
        talib_values = talib_config or TalibTechnicalConfig()
        market_session_result = (
            market_session
            if market_session is not None
            else detect_egx_market_session(
                now=now,
                ignore_market_hours=ignore_market_hours,
            )
        )
        market_session_lines = format_market_session_report_lines(
            market_session_result,
        )
        strategy_market_closed_note = None
        if (
            market_session_result.guard_enabled
            and not market_session_result.is_open_for_new_entries
        ):
            strategy_market_closed_note = (
                "Market closed: signal is for next session watchlist, "
                "not immediate entry."
            )
        ranking_frame = build_candidate_ranking_dataframe(
            live_snapshot,
            snapshot_path,
        )
        technical_available = technical_fields_available_in_dataframe(ranking_frame)
        fundamental_available = fundamental_fields_available_in_dataframe(ranking_frame)
        display_candidates = filter_candidates_for_display(
            scanner_report.candidates,
            filters,
            snapshot_df=ranking_frame,
            ranking_config=ranking_values,
            strategy_report=strategy_report,
            technical_config=technical_values,
            fundamental_config=fundamental_values,
        )

        summary_lines = [
            f"- Data Provider: {format_data_provider_label(data_provider)}",
            f"- Scanner Universe: {format_scanner_universe_label(scanner_universe)}",
            (
                f"- Symbols scanned: {quality_filter_result.filtered_count}"
                if quality_filter_result is not None
                else f"- Symbols scanned: {len(live_snapshot.symbols)}"
            ),
            f"- Candidates: {len(scanner_report.candidates)}",
            f"- Watch: {len(scanner_report.watchlist)}",
            f"- Blocked: {len(scanner_report.blocked)}",
        ]
        filter_lines = (
            build_candidate_filter_summary_lines(filters)
            + build_candidate_ranking_summary_lines(
                ranking_values,
                technical_config=technical_values,
                technical_fields_available=technical_available,
                fundamental_fields_available=fundamental_available,
            )
        )
        quality_lines = (
            build_market_quality_filter_summary_lines(quality_filter_result)
            if quality_filter_result is not None
            else []
        )
        tv_prefilter_diagnostics = _resolve_tv_query_prefilter_diagnostics(
            tv_query_filter_config,
            tv_query_prefilter_diagnostics,
        )
        tv_prefilter_lines = build_tradingview_query_prefilter_summary_lines(
            tv_prefilter_diagnostics,
        )

        mood_lines = (
            format_market_breadth_mood_report_lines(market_breadth_mood_result)
            if market_breadth_mood_result is not None
            else [
                f"- {market_mood.mood.value}",
                f"- Score: {market_mood.score}/100",
            ]
        )
        if market_breadth_mood_result is None:
            for reason in market_mood.reasons[:MAX_LIST_ITEMS]:
                mood_lines.append(f"- Reason: {reason}")
            for blocker in market_mood.blockers[:MAX_LIST_ITEMS]:
                mood_lines.append(f"- Blocker: {blocker}")

        sector_snapshot_df = ranking_frame
        if (
            quality_filter_result is not None
            and not ranking_frame.empty
            and "symbol" in ranking_frame.columns
        ):
            allowed_symbols = allowed_symbols_from_quality_result(quality_filter_result)
            sector_snapshot_df = ranking_frame[
                ranking_frame["symbol"].isin(allowed_symbols)
            ]
        sector_momentum_result = build_sector_momentum(
            sector_snapshot_df,
            candidates=scanner_report.candidates,
        )
        sector_momentum_lines = format_sector_momentum_lines(sector_momentum_result)

        entry_timing_lookup: dict[str, MultiTimeframeResult] = {}
        if multi_timeframe_values.enabled and display_candidates:
            if timeframe_snapshot_df is not None:
                for item in display_candidates:
                    timeframe_row = row_for_symbol_timeframes(
                        timeframe_snapshot_df,
                        item.symbol,
                    )
                    entry_timing_lookup[item.symbol] = evaluate_entry_timing(
                        row_for_symbol(ranking_frame, item.symbol),
                        tf_1h_row=timeframe_row,
                        tf_15m_row=timeframe_row,
                        config=multi_timeframe_values,
                    )
            elif data_provider == DATA_PROVIDER_TRADINGVIEW:
                entry_timing_lookup, timing_warnings = build_entry_timing_lookup(
                    [item.symbol for item in display_candidates],
                    multi_timeframe_values,
                )
                report_warnings.extend(timing_warnings)

        talib_lookup: dict[str, TalibTechnicalResult] = {}
        if talib_values.enabled:
            if not is_talib_engine_available():
                if TALIB_NOT_INSTALLED_WARNING not in report_warnings:
                    report_warnings.append(TALIB_NOT_INSTALLED_WARNING)
            else:
                candidate_symbols = [item.symbol for item in display_candidates]
                strategy_symbols = [
                    item.symbol
                    for item in ranked_strategy_signals_for_display(
                        strategy_report,
                        scanner_report,
                        filters,
                        ranking_frame,
                        ranking_values,
                        technical_config=technical_values,
                        fundamental_config=fundamental_values,
                    )
                ]
                talib_symbols = list(dict.fromkeys(candidate_symbols + strategy_symbols))
                history_store = LiveVolumeHistoryStore(settings.LIVE_HISTORY_DIR)
                talib_lookup, talib_warnings = build_talib_lookup_for_symbols(
                    talib_symbols,
                    history_store=history_store,
                    live_snapshot=live_snapshot,
                    config=talib_values,
                )
                for warning in talib_warnings:
                    if warning not in report_warnings:
                        report_warnings.append(warning)

        candidate_lines: list[str] = []
        candidate_fundamentals: list[dict[str, object]] = []
        candidate_entry_timing: list[dict[str, object]] = []
        candidate_talib_technical: list[dict[str, object]] = []
        for index, item in enumerate(display_candidates, start=1):
            ranking_note = format_candidate_ranking_note(
                item,
                ranking_frame,
                ranking_values,
                sector_status=sector_status_for_symbol(
                    item.symbol,
                    sector_momentum_result,
                ),
            )
            technical_note = format_candidate_technical_line(
                item,
                ranking_frame,
                technical_values,
            )
            fundamental_note = format_candidate_fundamental_line(
                item.symbol,
                ranking_frame,
                fundamental_values,
            )
            fundamental_result = evaluate_fundamental_quality(
                row_for_symbol(ranking_frame, item.symbol),
                fundamental_values,
            )
            candidate_fundamentals.append(
                {
                    "symbol": item.symbol,
                    **fundamental_result.to_dict(),
                }
            )
            entry_timing_note = None
            timing_result = entry_timing_lookup.get(item.symbol)
            if multi_timeframe_values.enabled and timing_result is not None:
                entry_timing_note = (
                    timing_result.summary or format_entry_timing_line(timing_result)
                )
                candidate_entry_timing.append(
                    {
                        "symbol": item.symbol,
                        **timing_result.to_dict(),
                    }
                )
            talib_note = None
            if talib_values.enabled:
                talib_result = talib_lookup.get(item.symbol)
                if talib_result is not None:
                    talib_note = format_talib_technical_line(talib_result)
                    candidate_talib_technical.append(
                        {
                            "symbol": item.symbol,
                            "talib_technical": talib_result.to_dict(),
                        }
                    )
            candidate_lines.extend(
                self._format_scanner_item(
                    index,
                    item,
                    ranking_note=ranking_note,
                    technical_note=technical_note,
                    fundamental_note=fundamental_note,
                    entry_timing_note=entry_timing_note,
                    talib_note=talib_note,
                    display_volume_ratio=display_volume_ratio_for_candidate(
                        item,
                        ranking_frame,
                    ),
                )
            )
        if not candidate_lines:
            candidate_lines = ["- (none)"]

        strategy_items = ranked_strategy_signals_for_display(
            strategy_report,
            scanner_report,
            filters,
            ranking_frame,
            ranking_values,
            technical_config=technical_values,
            fundamental_config=fundamental_values,
        )[:MAX_LIST_ITEMS]
        strategy_lines: list[str] = []
        signal_decisions: list[SignalDecision] = []
        signal_confirmations: list[SignalConfirmationSummary] = []
        for index, item in enumerate(strategy_items, start=1):
            timing_status = None
            if multi_timeframe_values.enabled:
                timing_result = entry_timing_lookup.get(item.symbol)
                if (
                    timing_result is not None
                    and timing_result.status != EntryTimingStatus.UNKNOWN
                ):
                    timing_status = timing_result.status.value
            tv_result = (
                evaluate_technical_confirmation(
                    row_for_symbol(ranking_frame, item.symbol),
                    technical_values,
                )
                if technical_values.enabled
                else None
            )
            talib_result = (
                talib_lookup.get(item.symbol)
                if talib_values.enabled
                else None
            )
            signal_confirmation = build_signal_confirmation_summary(
                item.symbol,
                tv_status=tv_result.status if tv_result is not None else None,
                timing_status=timing_status,
                talib_status=talib_result.status if talib_result is not None else None,
                talib_enabled=talib_values.enabled,
            )
            signal_confirmations.append(signal_confirmation)
            signal_decision = classify_strategy_signal_decision(
                item,
                session=market_session_result,
                entry_timing_status=timing_status,
            )
            signal_decisions.append(signal_decision)
            strategy_lines.extend(
                self._format_strategy_item(
                    index,
                    item,
                    entry_timing_status=timing_status,
                    market_closed_note=strategy_market_closed_note,
                    signal_decision=signal_decision,
                    signal_confirmation=signal_confirmation,
                )
            )
        if not strategy_lines:
            strategy_lines = ["- (none)"]

        paper_portfolio_lines: list[str] = []
        paper_portfolio_payload: dict[str, object] = {}
        paper_performance_lines: list[str] = []
        paper_performance_payload: dict[str, object] = {}
        portfolio = None
        price_map: dict[str, float] | None = None
        if enable_portfolio_marking or enable_performance_analytics:
            price_map = latest_prices or {
                symbol: snap.close for symbol, snap in live_snapshot.symbols.items()
            }
            portfolio = load_portfolio_for_marking()
        if enable_portfolio_marking:
            paper_portfolio_lines, paper_portfolio_payload = (
                build_daily_report_paper_portfolio(
                    portfolio,
                    latest_prices=price_map,
                    storage_available=paper_portfolio_storage_exists(),
                    market_session=market_session_result,
                )
            )
        if enable_performance_analytics:
            journal = load_trade_journal_for_report()
            paper_performance_lines, paper_performance_payload = (
                build_daily_report_paper_trading_performance(
                    portfolio,
                    journal,
                    latest_prices=price_map,
                    paper_portfolio_payload=paper_portfolio_payload or None,
                    storage_available=paper_portfolio_storage_exists(),
                )
            )

        market_symbol_snapshots = quality_filtered_symbol_snapshots(
            live_snapshot,
            quality_filter_result,
        )

        movers = sorted(
            market_symbol_snapshots,
            key=lambda snap: snap.change_percent,
            reverse=True,
        )[:MAX_LIST_ITEMS]
        mover_lines = [
            (
                f"{index}. {snap.symbol} | Change {snap.change_percent:+.2f}% | "
                f"Close {snap.close:.2f}"
            )
            for index, snap in enumerate(movers, start=1)
        ] or ["- (none)"]

        volume_leaders = sorted(
            market_symbol_snapshots,
            key=lambda snap: (-_safe_snapshot_volume(snap.volume), snap.symbol),
        )[:MAX_LIST_ITEMS]
        volume_lines = [
            (
                f"{index}. {snap.symbol} | Volume {snap.volume_ratio:.2f}x | "
                f"Vol {int(snap.volume):,}"
            )
            for index, snap in enumerate(volume_leaders, start=1)
        ] or ["- (none)"]

        watch_lines: list[str] = []
        if is_full_market_universe(scanner_universe):
            watch_lines = self._configured_watchlist_lines(
                scanner_report,
                live_snapshot,
                watchlist_symbols,
                watchlist_scanner_results=watchlist_scanner_results,
            )
        else:
            for index, item in enumerate(
                scanner_report.watchlist[:MAX_LIST_ITEMS],
                start=1,
            ):
                watch_lines.extend(self._format_scanner_item(index, item))
            if not watch_lines:
                watch_lines = ["- (none)"]

        blocked_lines = self._blocked_reason_counts(scanner_report)

        position_decisions: list[PositionDecision] = []
        exit_plans: list[PositionExitPlan] = []
        for position in paper_portfolio_payload.get("positions", []):
            if not isinstance(position, dict):
                continue
            symbol = str(position.get("symbol", ""))
            if not symbol:
                continue
            label = position.get("decision")
            explanation = position.get("decision_explanation")
            if label and explanation:
                position_decisions.append(
                    PositionDecision(
                        symbol=symbol,
                        label=DecisionLabel(str(label)),
                        explanation=str(explanation),
                        executable_now=bool(position.get("executable_now", False)),
                        review_timing=(
                            str(position["review_timing"])
                            if position.get("review_timing") is not None
                            else None
                        ),
                    )
                )
            exit_plan_label = position.get("exit_plan")
            exit_plan_explanation = position.get("exit_plan_explanation")
            if exit_plan_label and exit_plan_explanation:
                exit_plans.append(
                    PositionExitPlan(
                        symbol=symbol,
                        label=ExitPlanLabel(str(exit_plan_label)),
                        explanation=str(exit_plan_explanation),
                        exit_timing=(
                            str(position["exit_timing"])
                            if position.get("exit_timing") is not None
                            else None
                        ),
                        exit_executable_now=bool(
                            position.get("exit_executable_now", False)
                        ),
                    )
                )

        decision_summary = build_decision_summary(
            signal_decisions,
            position_decisions,
        )
        exit_plan_summary = build_exit_plan_summary(exit_plans)
        confirmation_summary = build_confirmation_summary(signal_confirmations)
        open_positions_count = int(
            paper_portfolio_payload.get("open_positions_count", len(exit_plans))
        )

        executive_summary = build_executive_summary(
            report_date=live_snapshot.as_of_date,
            market_session=market_session_result,
            market_mood=market_mood,
            strategy_items=strategy_items,
            display_candidates=display_candidates,
            warnings=report_warnings,
            paper_performance_payload=paper_performance_payload,
            paper_portfolio_payload=paper_portfolio_payload,
            market_breadth_mood_result=market_breadth_mood_result,
            decision_summary=decision_summary,
            exit_plans=exit_plans,
            open_positions_count=open_positions_count,
            signal_confirmations=signal_confirmations,
        )

        sections = [
            DailyReportSection(
                title="Executive Summary",
                lines=executive_summary.to_lines(),
            ),
            DailyReportSection(title="Summary", lines=summary_lines),
            DailyReportSection(title="Market Session", lines=market_session_lines),
            DailyReportSection(title="Candidate Filters", lines=filter_lines),
        ]
        if quality_lines:
            sections.append(
                DailyReportSection(title="Market Quality Filters", lines=quality_lines)
            )
        sections.append(
            DailyReportSection(
                title="TradingView Query Prefilter",
                lines=tv_prefilter_lines,
            )
        )
        sections.extend(
            [
                DailyReportSection(title="Market Mood", lines=mood_lines),
                DailyReportSection(
                    title="Sector Momentum",
                    lines=sector_momentum_lines,
                ),
                DailyReportSection(title="Top Candidates", lines=candidate_lines),
                DailyReportSection(title="Strategy Signals", lines=strategy_lines),
            ]
        )
        if enable_portfolio_marking:
            sections.append(
                DailyReportSection(
                    title="Paper Portfolio",
                    lines=paper_portfolio_lines,
                )
            )
        if enable_performance_analytics:
            sections.append(
                DailyReportSection(
                    title="Paper Trading Performance",
                    lines=paper_performance_lines,
                )
            )
        sections.extend(
            [
                DailyReportSection(title="Strongest Movers", lines=mover_lines),
                DailyReportSection(title="Volume Leaders", lines=volume_lines),
                DailyReportSection(title="Watch List", lines=watch_lines),
                DailyReportSection(title="Blocked Summary", lines=blocked_lines),
                DailyReportSection(
                    title="Warnings",
                    lines=[f"- {warning}" for warning in report_warnings]
                    or ["- (none)"],
                ),
            ]
        )

        return DailyReport(
            report_date=live_snapshot.as_of_date,
            source=REPORT_SOURCE_LIVE_SNAPSHOT,
            sections=sections,
            warnings=report_warnings,
            sector_momentum=sector_momentum_result.to_dict_list(),
            candidate_fundamentals=candidate_fundamentals,
            candidate_entry_timing=candidate_entry_timing,
            tv_query_prefilter=tv_prefilter_diagnostics.to_dict(),
            market_breadth_mood=(
                market_breadth_mood_result.to_dict()
                if market_breadth_mood_result is not None
                else {}
            ),
            paper_portfolio=paper_portfolio_payload,
            paper_trading_performance=paper_performance_payload,
            market_session=market_session_result.to_dict(),
            executive_summary=executive_summary.to_dict(),
            decision_summary=decision_summary.to_dict(),
            exit_plan_summary=exit_plan_summary.to_dict(),
            confirmation_summary=confirmation_summary.to_dict(),
            candidate_talib_technical=candidate_talib_technical,
        )


def format_daily_report_text(report: DailyReport) -> str:
    """Render a daily report as plain text."""
    lines = [
        "=== EGX Daily Report ===",
        f"Date: {report.report_date.isoformat()}",
        f"Source: {report.source}",
        "",
    ]
    for section in report.sections:
        lines.append(f"{section.title}:")
        lines.extend(section.lines)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_daily_report(
    report: DailyReport, reports_dir: Path
) -> tuple[Path, Path]:
    """Save a daily report as timestamped text and JSON files."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = report.created_at.strftime("%Y%m%d_%H%M%S")
    txt_path = reports_dir / f"egx_daily_report_{timestamp}.txt"
    json_path = reports_dir / f"egx_daily_report_{timestamp}.json"

    txt_path.write_text(format_daily_report_text(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return txt_path, json_path
