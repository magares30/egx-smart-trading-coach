"""Bootstrap fresh paper portfolio and trade journal for Cloud Run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import settings
from core.cloud_state_store import (
    GcsStateStore,
    PORTFOLIO_STATE_KEY,
    TRADES_KEY,
    get_state_store,
    is_gcs_state_enabled,
    sync_local_storage_to_cloud,
)
from core.json_storage import atomic_write_json
from core.portfolio import VirtualPortfolio
from core.trade_journal import TradeJournal


@dataclass(frozen=True)
class CloudPaperPortfolioBootstrapResult:
    """Outcome of a cloud paper portfolio bootstrap run."""

    portfolio_status: str
    journal_status: str
    gcs_upload: str
    initial_capital: float
    portfolio_path: str
    trades_path: str
    open_positions: int
    closed_trades: int

    def summary_lines(self) -> list[str]:
        return [
            "=== EGX Cloud Paper Portfolio Bootstrap ===",
            f"Portfolio: {self.portfolio_status}",
            f"Trade journal: {self.journal_status}",
            f"GCS upload: {self.gcs_upload}",
            f"Initial capital: {self.initial_capital:,.2f} {settings.BASE_CURRENCY}",
            f"Local portfolio file: {self.portfolio_path}",
            f"Local trades file: {self.trades_path}",
            f"Open positions: {self.open_positions}",
            f"Closed trades in journal: {self.closed_trades}",
            "Paper trading only. No broker APIs. No secrets logged.",
        ]


def _paper_state_exists(
    *,
    local_path: Path,
    gcs_key: str,
    store: object | None = None,
) -> bool:
    if local_path.is_file():
        return True
    if not is_gcs_state_enabled():
        return False
    state_store = store or get_state_store()
    if isinstance(state_store, GcsStateStore):
        return state_store.exists(gcs_key)
    return False


def _write_fresh_portfolio_file(portfolio_path: Path) -> None:
    portfolio_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        portfolio_path,
        {
            "cash": settings.INITIAL_CAPITAL_EGP,
            "initial_capital": settings.INITIAL_CAPITAL_EGP,
            "realized_pnl": 0.0,
            "positions": {},
            "trades": {},
        },
    )


def _write_empty_journal_file(trades_path: Path) -> None:
    trades_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(trades_path, [])


def bootstrap_cloud_paper_portfolio(
    *,
    force: bool = False,
    portfolio_path: Path | None = None,
    trades_path: Path | None = None,
    state_store: object | None = None,
) -> CloudPaperPortfolioBootstrapResult:
    """Create fresh local paper portfolio/journal and optionally upload to GCS."""
    portfolio_file = portfolio_path or settings.PORTFOLIO_STATE_PATH
    trades_file = trades_path or settings.TRADES_PATH

    portfolio_exists = _paper_state_exists(
        local_path=portfolio_file,
        gcs_key=PORTFOLIO_STATE_KEY,
        store=state_store,
    )
    journal_exists = _paper_state_exists(
        local_path=trades_file,
        gcs_key=TRADES_KEY,
        store=state_store,
    )

    if not force and portfolio_exists:
        portfolio_status = "already exists (not overwritten)"
    else:
        if force and portfolio_file.is_file():
            portfolio = VirtualPortfolio(state_path=portfolio_file)
            portfolio.reset()
            portfolio_status = "overwritten with fresh paper portfolio"
        else:
            _write_fresh_portfolio_file(portfolio_file)
            portfolio_status = "created fresh paper portfolio"

    if not force and journal_exists:
        journal_status = "already exists (not overwritten)"
    else:
        if force and trades_file.is_file():
            journal = TradeJournal(journal_path=trades_file)
            journal.clear()
            journal_status = "overwritten with empty trade journal"
        else:
            _write_empty_journal_file(trades_file)
            journal_status = "created empty trade journal"

    created_or_overwritten = any(
        status.startswith(("created", "overwritten"))
        for status in (portfolio_status, journal_status)
    )

    if not is_gcs_state_enabled():
        gcs_upload = "skipped (EGX_STATE_GCS_BUCKET not set)"
    elif created_or_overwritten:
        sync_local_storage_to_cloud()
        gcs_upload = "uploaded portfolio/journal to GCS"
    else:
        gcs_upload = "skipped (files already existed)"

    if portfolio_file.is_file():
        portfolio = VirtualPortfolio(state_path=portfolio_file)
        initial_capital = portfolio.initial_capital
        open_positions = len(portfolio.positions)
    else:
        initial_capital = settings.INITIAL_CAPITAL_EGP
        open_positions = 0

    if trades_file.is_file():
        closed_trades = len(TradeJournal(journal_path=trades_file).trades)
    else:
        closed_trades = 0

    return CloudPaperPortfolioBootstrapResult(
        portfolio_status=portfolio_status,
        journal_status=journal_status,
        gcs_upload=gcs_upload,
        initial_capital=initial_capital,
        portfolio_path=str(portfolio_file),
        trades_path=str(trades_file),
        open_positions=open_positions,
        closed_trades=closed_trades,
    )


def run_egx_bootstrap_cloud_paper_portfolio(*, force: bool = False) -> int:
    """CLI entry for bootstrapping Cloud Run paper portfolio state."""
    result = bootstrap_cloud_paper_portfolio(force=force)
    for line in result.summary_lines():
        print(line)
    print()
    return 0
