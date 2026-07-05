"""Automatic paper trade entry after daily report generation when EGX is OPEN."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.cloud_state_store import hydrate_local_storage_from_cloud, sync_local_storage_to_cloud
from core.live_paper_trader import LivePaperTradeDecision, LivePaperTrader
from core.market_hours import EgxMarketSession, detect_egx_market_session
from core.portfolio import VirtualPortfolio
from core.risk import RiskManager
from core.strategy import StrategyReport
from core.trade_journal import TradeJournal

logger = logging.getLogger(__name__)

DEFAULT_MAX_TRADES_PER_RUN = 3
DEFAULT_MIN_CONFIDENCE_SCORE = 75


@dataclass(frozen=True)
class PaperEntryExecutionResult:
    checked: bool
    market_status: str
    buy_setups_count: int
    open_positions_count: int
    opened_count: int
    skipped_count: int
    rejected_count: int
    skip_reason: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "paper_entry_execution_checked": self.checked,
            "paper_entry_execution_market_status": self.market_status,
            "paper_entry_execution_buy_setups_count": self.buy_setups_count,
            "paper_entry_execution_open_positions_count": self.open_positions_count,
            "paper_entry_execution_opened_count": self.opened_count,
            "paper_entry_execution_skipped_count": self.skipped_count,
            "paper_entry_execution_rejected_count": self.rejected_count,
            "paper_entry_execution_skip_reason": self.skip_reason,
        }


def _normalize_skip_log_reason(reason: str) -> str:
    lowered = reason.lower()
    if "market closed" in lowered:
        return "market closed"
    if "already open" in lowered:
        return "existing position"
    if "insufficient cash" in lowered:
        return "no cash"
    if "confidence below" in lowered:
        return "low confidence"
    if "no trade signal" in lowered:
        return "missing signal"
    if "missing price" in lowered or "entry price" in lowered:
        return "missing price"
    return reason


def _log_trade_results(
    *,
    market_status: str,
    buy_setups_count: int,
    open_positions_count: int,
    trade_report,
) -> None:
    logger.info(
        "Paper entry evaluation: market=%s buy_setups=%s open_positions=%s",
        market_status,
        buy_setups_count,
        open_positions_count,
    )

    for result in trade_report.results:
        symbol = result.symbol
        reason = _normalize_skip_log_reason(result.reason)
        if result.decision == LivePaperTradeDecision.OPENED:
            price = result.entry_price if result.entry_price is not None else "n/a"
            qty = result.quantity if result.quantity is not None else "n/a"
            value = result.cost if result.cost is not None else "n/a"
            logger.info(
                "Paper entry opened: %s price=%s qty=%s value=%s",
                symbol,
                price,
                qty,
                value,
            )
        elif result.decision == LivePaperTradeDecision.SKIPPED:
            logger.info("Paper entry skipped: %s reason=%s", symbol, reason)
        elif result.decision == LivePaperTradeDecision.REJECTED:
            logger.info("Paper entry rejected: %s reason=%s", symbol, reason)

    logger.info(
        "Paper entry evaluation completed: opened=%s skipped=%s rejected=%s",
        trade_report.opened_count,
        trade_report.skipped_count,
        trade_report.rejected_count,
    )


def execute_paper_entries_after_report(
    strategy_report: StrategyReport,
    *,
    max_trades_per_run: int = DEFAULT_MAX_TRADES_PER_RUN,
    min_confidence_score: int = DEFAULT_MIN_CONFIDENCE_SCORE,
    ignore_market_hours: bool = False,
    market_session: EgxMarketSession | None = None,
) -> PaperEntryExecutionResult:
    """Evaluate BUY_SETUP strategy signals and open paper trades when market is OPEN."""
    session = market_session or detect_egx_market_session()
    market_status = session.session_status.value
    buy_setups = list(strategy_report.buy_setups)

    hydrate_local_storage_from_cloud()
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    open_positions_count = len(portfolio.positions)

    if not ignore_market_hours and not session.is_open_for_new_entries:
        logger.info("Paper entry skipped: market=%s", market_status)
        return PaperEntryExecutionResult(
            checked=True,
            market_status=market_status,
            buy_setups_count=len(buy_setups),
            open_positions_count=open_positions_count,
            opened_count=0,
            skipped_count=len(buy_setups),
            rejected_count=0,
            skip_reason=f"market={market_status}",
        )

    trader = LivePaperTrader(
        portfolio=portfolio,
        trade_journal=journal,
        risk_manager=RiskManager(),
        max_trades_per_run=max_trades_per_run,
        min_confidence_score=min_confidence_score,
        ignore_market_hours=ignore_market_hours,
        market_session=session,
    )
    trade_report = trader.trade_from_strategy_report(strategy_report)
    _log_trade_results(
        market_status=market_status,
        buy_setups_count=len(buy_setups),
        open_positions_count=open_positions_count,
        trade_report=trade_report,
    )

    if trade_report.opened_count > 0:
        sync_local_storage_to_cloud()

    skip_reason = None
    if trade_report.opened_count == 0 and buy_setups:
        skip_reason = f"opened=0 skipped={trade_report.skipped_count}"

    return PaperEntryExecutionResult(
        checked=True,
        market_status=market_status,
        buy_setups_count=len(buy_setups),
        open_positions_count=open_positions_count,
        opened_count=trade_report.opened_count,
        skipped_count=trade_report.skipped_count,
        rejected_count=trade_report.rejected_count,
        skip_reason=skip_reason,
    )


def patch_saved_report_with_entry_metadata(
    json_path: Path,
    execution: PaperEntryExecutionResult,
) -> None:
    """Attach paper entry execution metadata to a saved daily report JSON file."""
    if not execution.checked or not json_path.is_file():
        return

    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read report JSON for paper entry metadata: %s", json_path)
        return

    metadata = dict(payload.get("report_metadata") or {})
    metadata.update(execution.to_metadata())
    payload["report_metadata"] = metadata

    try:
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("Failed to write paper entry metadata to report JSON: %s", json_path)
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
        logger.warning("Failed to persist report JSON after paper entry metadata update.")
