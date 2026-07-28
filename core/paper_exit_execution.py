"""Automatic paper trade exits before daily report generation when EGX is OPEN."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.cloud_state_store import hydrate_local_storage_from_cloud, sync_local_storage_to_cloud
from core.live_paper_monitor import LivePaperMonitor, LivePaperMonitorReport
from core.live_snapshot import LiveMarketSnapshot
from core.market_hours import EgxMarketSession, detect_egx_market_session
from core.portfolio import VirtualPortfolio
from core.trade_journal import TradeJournal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaperExitExecutionResult:
    checked: bool
    market_status: str
    open_trades_checked: int
    closed_count: int
    held_count: int
    error_count: int
    skip_reason: str | None = None

    def to_metadata(self) -> dict[str, object]:
        return {
            "paper_exit_execution_checked": self.checked,
            "paper_exit_execution_market_status": self.market_status,
            "paper_exit_execution_open_trades_checked": self.open_trades_checked,
            "paper_exit_execution_closed_count": self.closed_count,
            "paper_exit_execution_held_count": self.held_count,
            "paper_exit_execution_error_count": self.error_count,
            "paper_exit_execution_skip_reason": self.skip_reason,
        }


def _empty_result(
    *,
    market_status: str,
    skip_reason: str,
    open_trades_checked: int = 0,
) -> PaperExitExecutionResult:
    return PaperExitExecutionResult(
        checked=True,
        market_status=market_status,
        open_trades_checked=open_trades_checked,
        closed_count=0,
        held_count=0,
        error_count=0,
        skip_reason=skip_reason,
    )


def _log_monitor_report(report: LivePaperMonitorReport) -> None:
    logger.info(
        "Paper exit evaluation: checked=%s closed=%s held=%s errors=%s",
        len(report.results),
        report.closed_count,
        report.held_count,
        report.error_count,
    )
    for result in report.results:
        if result.decision.value == "CLOSED":
            logger.info(
                "Paper exit closed: %s reason=%s entry=%s exit=%s pnl=%s",
                result.symbol,
                result.reason.value,
                result.entry_price,
                result.exit_price,
                result.pnl,
            )
        elif result.decision.value == "HELD":
            logger.info(
                "Paper exit held: %s reason=%s",
                result.symbol,
                result.reason.value,
            )
        else:
            logger.info(
                "Paper exit error: %s reason=%s",
                result.symbol,
                result.reason.value,
            )


def execute_paper_exits_before_report(
    live_snapshot: LiveMarketSnapshot | None,
    *,
    ignore_market_hours: bool = False,
    market_session: EgxMarketSession | None = None,
) -> PaperExitExecutionResult:
    """Close open paper trades on TP/SL using LivePaperMonitor before report build."""
    # Temporary diagnostic logs — verify Cloud Run actually enters this path.
    logger.info("Paper exit execution started")

    session = market_session or detect_egx_market_session(
        ignore_market_hours=ignore_market_hours,
    )
    market_status = session.session_status.value

    hydrate_local_storage_from_cloud()
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    open_before = len(portfolio.get_open_trades())

    if live_snapshot is None:
        logger.info(
            "Paper exit execution skipped reason=%s",
            "missing_live_snapshot",
        )
        return _empty_result(
            market_status=market_status,
            skip_reason="missing_live_snapshot",
            open_trades_checked=open_before,
        )

    if not ignore_market_hours and not session.is_open_for_new_entries:
        skip_reason = f"market={market_status}"
        logger.info("Paper exit execution skipped reason=%s", skip_reason)
        return _empty_result(
            market_status=market_status,
            skip_reason=skip_reason,
            open_trades_checked=open_before,
        )

    logger.info("Paper exit monitor running")
    monitor = LivePaperMonitor(portfolio=portfolio, trade_journal=journal)
    report = monitor.monitor_from_live_snapshot(live_snapshot)
    logger.info(
        "Paper exit monitor completed closed=%s held=%s",
        report.closed_count,
        report.held_count,
    )
    _log_monitor_report(report)

    if report.closed_count > 0:
        sync_local_storage_to_cloud()

    return PaperExitExecutionResult(
        checked=True,
        market_status=market_status,
        open_trades_checked=len(report.results),
        closed_count=report.closed_count,
        held_count=report.held_count,
        error_count=report.error_count,
    )
