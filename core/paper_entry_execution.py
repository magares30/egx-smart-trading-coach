"""Automatic paper trade entry after daily report generation when EGX is OPEN."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import settings
from core.cloud_state_store import hydrate_local_storage_from_cloud, sync_local_storage_to_cloud
from core.live_paper_trader import LivePaperTradeDecision, LivePaperTrader
from core.market_hours import EgxMarketSession, detect_egx_market_session
from core.models import SignalType, Trade, TradeSide, TradeSignal
from core.portfolio import PortfolioError, VirtualPortfolio
from core.risk import RiskManager
from core.strategy import StrategyReport, StrategyResult
from core.telegram_report_resolver import resolve_executable_opportunity_items
from core.trade_journal import TradeJournal

logger = logging.getLogger(__name__)

DEFAULT_MAX_TRADES_PER_RUN = 3
DEFAULT_MIN_CONFIDENCE_SCORE = 75
DEFAULT_EXECUTABLE_POOL_SIZE = 12
SOURCE_BUY_SETUP = "BUY_SETUP"
SOURCE_BEST_IDEAS_FALLBACK = "BEST_IDEAS_FALLBACK"
SOURCE_NONE = "NONE"
FALLBACK_TRADE_NOTE = "paper_entry_source=BEST_IDEAS_FALLBACK"


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
    execution_source: str = SOURCE_NONE
    fallback_used: bool = False
    fallback_candidates_count: int = 0
    candidate_symbols: list[str] = field(default_factory=list)
    attempted_symbols: list[str] = field(default_factory=list)
    opened_symbols: list[str] = field(default_factory=list)
    skipped_symbols_with_reasons: dict[str, str] = field(default_factory=dict)

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
            "paper_entry_execution_source": self.execution_source,
            "paper_entry_execution_fallback_used": self.fallback_used,
            "paper_entry_execution_fallback_candidates_count": (
                self.fallback_candidates_count
            ),
            "paper_entry_execution_candidate_symbols": list(self.candidate_symbols),
            "paper_entry_execution_attempted_symbols": list(self.attempted_symbols),
            "paper_entry_execution_opened_symbols": list(self.opened_symbols),
            "paper_entry_execution_skipped_symbols_with_reasons": dict(
                self.skipped_symbols_with_reasons
            ),
        }


def _normalize_skip_log_reason(reason: str) -> str:
    lowered = reason.lower()
    if "market closed" in lowered:
        return "market closed"
    if "already open" in lowered:
        return "already open"
    if "insufficient cash" in lowered or "no cash" in lowered:
        return "no cash"
    if "maximum open positions" in lowered or "max positions" in lowered:
        return "max positions"
    if "confidence below" in lowered or "low confidence" in lowered:
        return "low confidence"
    if "no trade signal" in lowered:
        return "missing signal"
    if "missing price" in lowered or "entry price" in lowered:
        return "missing price"
    if "risk" in lowered or "reward" in lowered or "rejected" in lowered:
        return "risk/reward"
    return reason


def _record_skip(
    skipped_symbols_with_reasons: dict[str, str],
    symbol: str,
    reason: str,
) -> None:
    normalized = _normalize_skip_log_reason(reason)
    skipped_symbols_with_reasons[symbol] = normalized


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


def _buy_setup_execution_symbols(strategy_report: StrategyReport) -> list[str]:
    return [item.symbol.upper() for item in strategy_report.buy_setups]


def _buy_setup_execution_tracking(
    strategy_report: StrategyReport,
    trade_report,
) -> tuple[list[str], list[str], list[str], dict[str, str]]:
    candidate_symbols = _buy_setup_execution_symbols(strategy_report)
    attempted_symbols: list[str] = []
    opened_symbols: list[str] = []
    skipped_symbols_with_reasons: dict[str, str] = {}

    for result in trade_report.results:
        symbol = result.symbol.upper()
        reason = _normalize_skip_log_reason(result.reason)
        if result.decision == LivePaperTradeDecision.OPENED:
            attempted_symbols.append(symbol)
            opened_symbols.append(symbol)
        elif result.decision in {
            LivePaperTradeDecision.SKIPPED,
            LivePaperTradeDecision.REJECTED,
        }:
            _record_skip(skipped_symbols_with_reasons, symbol, reason)

    return candidate_symbols, attempted_symbols, opened_symbols, skipped_symbols_with_reasons


def _safe_float(value: object | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed <= 0:
        return None
    return parsed


def _strategy_levels_for_symbol(
    strategy_report: StrategyReport | None,
    symbol: str,
) -> tuple[float | None, float | None, float | None]:
    if strategy_report is None:
        return None, None, None
    for item in strategy_report.results:
        if item.symbol.upper() == symbol.upper():
            return item.entry_price, item.stop_loss, item.take_profit
    return None, None, None


def _resolve_entry_price(
    symbol: str,
    item: dict[str, Any],
    latest_prices: dict[str, float],
) -> float | None:
    for key in ("entry", "entry_price"):
        price = _safe_float(item.get(key))
        if price is not None:
            return price
    return latest_prices.get(symbol.upper())


def _levels_for_fallback(
    symbol: str,
    item: dict[str, Any],
    *,
    latest_prices: dict[str, float],
    strategy_report: StrategyReport | None,
) -> tuple[float, float, float] | None:
    entry = _resolve_entry_price(symbol, item, latest_prices)
    if entry is None:
        return None

    stop = _safe_float(item.get("stop")) or _safe_float(item.get("stop_loss"))
    target = _safe_float(item.get("target")) or _safe_float(item.get("take_profit"))
    if stop is not None and target is not None and stop < entry < target:
        return entry, stop, target

    strategy_entry, strategy_stop, strategy_target = _strategy_levels_for_symbol(
        strategy_report,
        symbol,
    )
    if (
        strategy_entry is not None
        and strategy_stop is not None
        and strategy_target is not None
        and strategy_stop < strategy_entry < strategy_target
    ):
        return float(strategy_entry), float(strategy_stop), float(strategy_target)

    stop_loss = round(entry * 0.97, 4)
    risk = entry - stop_loss
    if risk <= 0:
        return None
    take_profit = round(entry + risk * 2.01, 4)
    return entry, stop_loss, take_profit


def _confidence_for_item(item: dict[str, Any]) -> int:
    label = str(item.get("confidence_label_v2") or "").upper()
    if label == "STRONG":
        return 85
    if label == "GOOD":
        return 78
    score = item.get("confidence_score_v2")
    if score is not None:
        try:
            return max(0, min(100, int(float(score))))
        except (TypeError, ValueError):
            pass
    return DEFAULT_MIN_CONFIDENCE_SCORE


def _collect_fallback_candidates(
    report_payload: dict[str, Any] | None,
    *,
    pool_size: int,
) -> list[dict[str, Any]]:
    if not report_payload:
        return []
    return resolve_executable_opportunity_items(report_payload, limit=pool_size)


@dataclass
class FallbackExecutionOutcome:
    opened_count: int = 0
    skipped_count: int = 0
    rejected_count: int = 0
    candidate_symbols: list[str] = field(default_factory=list)
    attempted_symbols: list[str] = field(default_factory=list)
    opened_symbols: list[str] = field(default_factory=list)
    skipped_symbols_with_reasons: dict[str, str] = field(default_factory=dict)


def _execute_best_ideas_fallback(
    *,
    portfolio: VirtualPortfolio,
    journal: TradeJournal,
    candidates: list[dict[str, Any]],
    latest_prices: dict[str, float],
    strategy_report: StrategyReport | None,
    max_trades_per_run: int,
    min_confidence_score: int,
) -> FallbackExecutionOutcome:
    """Open experimental paper entries from shared executable opportunities."""
    outcome = FallbackExecutionOutcome(
        candidate_symbols=[
            str(item.get("symbol") or "").strip().upper()
            for item in candidates
            if str(item.get("symbol") or "").strip()
        ],
    )

    logger.info(
        "Paper entry fallback activated: buy_setups=0 candidates=%s order=%s",
        len(candidates),
        outcome.candidate_symbols,
    )

    if not candidates:
        return outcome

    risk_manager = RiskManager()
    opens_this_run = 0

    for item in candidates:
        if opens_this_run >= max_trades_per_run:
            break

        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol:
            outcome.skipped_count += 1
            continue

        if symbol in portfolio.positions:
            logger.info(
                "Paper entry fallback skipped: %s reason=already open",
                symbol,
            )
            _record_skip(outcome.skipped_symbols_with_reasons, symbol, "already open")
            outcome.skipped_count += 1
            continue

        if len(portfolio.positions) >= settings.MAX_OPEN_POSITIONS:
            logger.info(
                "Paper entry fallback skipped: %s reason=max positions",
                symbol,
            )
            _record_skip(outcome.skipped_symbols_with_reasons, symbol, "max positions")
            outcome.skipped_count += 1
            continue

        levels = _levels_for_fallback(
            symbol,
            item,
            latest_prices=latest_prices,
            strategy_report=strategy_report,
        )
        if levels is None:
            logger.info(
                "Paper entry fallback skipped: %s reason=missing price",
                symbol,
            )
            _record_skip(outcome.skipped_symbols_with_reasons, symbol, "missing price")
            outcome.skipped_count += 1
            continue

        entry_price, stop_loss, take_profit = levels
        confidence_score = _confidence_for_item(item)
        if confidence_score < min_confidence_score:
            logger.info(
                "Paper entry fallback skipped: %s reason=low confidence",
                symbol,
            )
            _record_skip(outcome.skipped_symbols_with_reasons, symbol, "low confidence")
            outcome.skipped_count += 1
            continue

        signal = TradeSignal(
            symbol=symbol,
            signal_type=SignalType.BUY_SETUP,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence_score=confidence_score,
            reasons=[
                f"BEST_IDEAS_FALLBACK from {item.get('source', 'report')}",
            ],
        )
        equity = portfolio.get_snapshot().equity
        risk_decision = risk_manager.evaluate(signal, equity)
        if not risk_decision.approved:
            reason = ", ".join(risk_decision.rejection_reasons) or "risk rejected"
            normalized = _normalize_skip_log_reason(reason)
            logger.info(
                "Paper entry fallback skipped: %s reason=%s",
                symbol,
                normalized,
            )
            _record_skip(outcome.skipped_symbols_with_reasons, symbol, normalized)
            outcome.rejected_count += 1
            continue

        outcome.attempted_symbols.append(symbol)
        try:
            trade = portfolio.open_trade(
                symbol=symbol,
                side=TradeSide.BUY,
                quantity=risk_decision.quantity,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=SOURCE_BEST_IDEAS_FALLBACK,
                notes=FALLBACK_TRADE_NOTE,
            )
        except PortfolioError as error:
            normalized = _normalize_skip_log_reason(str(error))
            logger.info(
                "Paper entry fallback skipped: %s reason=%s",
                symbol,
                normalized,
            )
            _record_skip(outcome.skipped_symbols_with_reasons, symbol, normalized)
            outcome.rejected_count += 1
            continue

        journal.append_trade(trade)
        outcome.opened_symbols.append(symbol)
        outcome.opened_count += 1
        opens_this_run += 1
        value = trade.quantity * trade.entry_price
        logger.info(
            "Paper entry opened from BEST_IDEAS_FALLBACK: %s price=%s qty=%s value=%s",
            symbol,
            trade.entry_price,
            trade.quantity,
            value,
        )

    logger.info(
        "Paper entry fallback completed: opened=%s skipped=%s rejected=%s",
        outcome.opened_count,
        outcome.skipped_count,
        outcome.rejected_count,
    )
    return outcome


def execute_paper_entries_after_report(
    strategy_report: StrategyReport,
    *,
    report_payload: dict[str, Any] | None = None,
    latest_prices: dict[str, float] | None = None,
    full_strategy_report: StrategyReport | None = None,
    max_trades_per_run: int = DEFAULT_MAX_TRADES_PER_RUN,
    min_confidence_score: int = DEFAULT_MIN_CONFIDENCE_SCORE,
    ignore_market_hours: bool = False,
    market_session: EgxMarketSession | None = None,
) -> PaperEntryExecutionResult:
    """Evaluate BUY_SETUP signals, then best-ideas fallback when BUY_SETUP is empty."""
    session = market_session or detect_egx_market_session()
    market_status = session.session_status.value
    buy_setups = list(strategy_report.buy_setups)
    price_lookup = latest_prices or {}

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
            execution_source=SOURCE_NONE,
            candidate_symbols=_buy_setup_execution_symbols(strategy_report),
        )

    if buy_setups:
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
        if trade_report.opened_count == 0:
            skip_reason = f"opened=0 skipped={trade_report.skipped_count}"

        (
            candidate_symbols,
            attempted_symbols,
            opened_symbols,
            skipped_symbols_with_reasons,
        ) = _buy_setup_execution_tracking(strategy_report, trade_report)

        return PaperEntryExecutionResult(
            checked=True,
            market_status=market_status,
            buy_setups_count=len(buy_setups),
            open_positions_count=open_positions_count,
            opened_count=trade_report.opened_count,
            skipped_count=trade_report.skipped_count,
            rejected_count=trade_report.rejected_count,
            skip_reason=skip_reason,
            execution_source=SOURCE_BUY_SETUP,
            fallback_used=False,
            candidate_symbols=candidate_symbols,
            attempted_symbols=attempted_symbols,
            opened_symbols=opened_symbols,
            skipped_symbols_with_reasons=skipped_symbols_with_reasons,
        )

    pool_size = max(
        max_trades_per_run * 4,
        DEFAULT_MAX_TRADES_PER_RUN,
        DEFAULT_EXECUTABLE_POOL_SIZE,
    )
    fallback_candidates = _collect_fallback_candidates(report_payload, pool_size=pool_size)
    if not report_payload or not fallback_candidates:
        return PaperEntryExecutionResult(
            checked=True,
            market_status=market_status,
            buy_setups_count=0,
            open_positions_count=open_positions_count,
            opened_count=0,
            skipped_count=0,
            rejected_count=0,
            skip_reason="no_buy_setups_or_fallback_candidates",
            execution_source=SOURCE_NONE,
            fallback_used=False,
            fallback_candidates_count=len(fallback_candidates),
            candidate_symbols=[
                str(item.get("symbol") or "").strip().upper()
                for item in fallback_candidates
                if str(item.get("symbol") or "").strip()
            ],
        )

    fallback_outcome = _execute_best_ideas_fallback(
        portfolio=portfolio,
        journal=journal,
        candidates=fallback_candidates,
        latest_prices=price_lookup,
        strategy_report=full_strategy_report or strategy_report,
        max_trades_per_run=max_trades_per_run,
        min_confidence_score=min_confidence_score,
    )

    if fallback_outcome.opened_count > 0:
        sync_local_storage_to_cloud()

    skip_reason = None
    if fallback_outcome.opened_count == 0:
        skip_reason = f"fallback_opened=0 skipped={fallback_outcome.skipped_count}"

    return PaperEntryExecutionResult(
        checked=True,
        market_status=market_status,
        buy_setups_count=0,
        open_positions_count=open_positions_count,
        opened_count=fallback_outcome.opened_count,
        skipped_count=fallback_outcome.skipped_count,
        rejected_count=fallback_outcome.rejected_count,
        skip_reason=skip_reason,
        execution_source=SOURCE_BEST_IDEAS_FALLBACK,
        fallback_used=True,
        fallback_candidates_count=len(fallback_candidates),
        candidate_symbols=fallback_outcome.candidate_symbols,
        attempted_symbols=fallback_outcome.attempted_symbols,
        opened_symbols=fallback_outcome.opened_symbols,
        skipped_symbols_with_reasons=fallback_outcome.skipped_symbols_with_reasons,
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
