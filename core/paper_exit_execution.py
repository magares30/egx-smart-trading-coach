"""Automatic paper trade exits before daily report generation when EGX is OPEN."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    closed_symbols: list[str] = field(default_factory=list)
    closed_trades: list[dict[str, Any]] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "paper_exit_execution_checked": self.checked,
            "paper_exit_execution_market_status": self.market_status,
            "paper_exit_execution_open_trades_checked": self.open_trades_checked,
            "paper_exit_execution_closed_count": self.closed_count,
            "paper_exit_execution_held_count": self.held_count,
            "paper_exit_execution_error_count": self.error_count,
            "paper_exit_execution_skip_reason": self.skip_reason,
            "paper_exit_execution_closed_symbols": list(self.closed_symbols),
            "paper_exit_execution_closed_trades": list(self.closed_trades),
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


def _closed_trade_summary(result) -> dict[str, Any]:
    return {
        "symbol": result.symbol,
        "reason": result.reason.value if hasattr(result.reason, "value") else str(result.reason),
        "entry_price": result.entry_price,
        "exit_price": result.exit_price,
        "pnl": result.pnl,
        "pnl_percent": result.pnl_percent,
    }


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

    closed_results = [
        result for result in report.results if result.decision.value == "CLOSED"
    ]
    closed_trades = [_closed_trade_summary(result) for result in closed_results]
    closed_symbols = [str(item["symbol"]) for item in closed_trades]

    if report.closed_count > 0:
        sync_local_storage_to_cloud()
        logger.info(
            "Paper exit state synced to GCS: closed_symbols=%s",
            closed_symbols,
        )

    return PaperExitExecutionResult(
        checked=True,
        market_status=market_status,
        open_trades_checked=len(report.results),
        closed_count=report.closed_count,
        held_count=report.held_count,
        error_count=report.error_count,
        closed_symbols=closed_symbols,
        closed_trades=closed_trades,
    )


def patch_saved_report_with_exit_metadata(
    json_path: Path,
    execution: PaperExitExecutionResult,
) -> None:
    """Attach paper exit execution metadata to a saved daily report JSON file."""
    if not execution.checked or not json_path.is_file():
        return

    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read report JSON for paper exit metadata: %s", json_path)
        return

    metadata = dict(payload.get("report_metadata") or {})
    metadata.update(execution.to_metadata())
    payload["report_metadata"] = metadata

    try:
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("Failed to write paper exit metadata to report JSON: %s", json_path)
        return

    txt_path = json_path.with_suffix(".txt")
    if not txt_path.is_file():
        return

    try:
        from core.cloud_state_store import persist_latest_report

        persist_latest_report(
            txt_path.read_text(encoding="utf-8"),
            json_path.read_text(encoding="utf-8"),
        )
    except OSError:
        logger.warning("Failed to persist report JSON after paper exit metadata update.")


def format_paper_exit_telegram_alert(
    metadata: dict[str, Any] | None,
) -> str | None:
    """Build Arabic Telegram alert when paper exits closed one or more trades."""
    if not metadata:
        return None
    closed_count = int(metadata.get("paper_exit_execution_closed_count") or 0)
    if closed_count <= 0:
        return None

    closed_trades = metadata.get("paper_exit_execution_closed_trades") or []
    lines = [
        "🔔 إغلاق ورقي تلقائي",
        f"اتقفلت {closed_count} صفقة ورقية:",
        "",
    ]
    for item in closed_trades:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol", "?")
        reason = item.get("reason", "?")
        pnl = item.get("pnl")
        pnl_pct = item.get("pnl_percent")
        if pnl is not None and pnl_pct is not None:
            pnl_text = f"{float(pnl):+.2f} ({float(pnl_pct):+.2f}%)"
        elif pnl is not None:
            pnl_text = f"{float(pnl):+.2f}"
        else:
            pnl_text = "n/a"
        lines.append(f"- {symbol} | {reason} | {pnl_text}")

    lines.extend(
        [
            "",
            "ورقي فقط — مفيش تنفيذ حقيقي.",
            "📚 تعلم المحفظة هيتحدث من الصفقات المقفولة.",
        ]
    )
    return "\n".join(lines)
