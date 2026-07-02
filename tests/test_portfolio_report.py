"""Tests for EGX paper portfolio report builder and workflow."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from config import settings
from core.data_import import LIVE_SNAPSHOT_COLUMNS
from core.live_snapshot import EgxLiveSnapshotProvider
from core.models import TradeSide, TradeStatus
from core.paper_engine import close_paper_trade
from core.portfolio import VirtualPortfolio
from core.portfolio_report import (
    EMPTY_PORTFOLIO_MESSAGE,
    EMPTY_TRADES_MESSAGE,
    SAFETY_NOTICE,
    PortfolioReportBuilder,
    build_daily_report_paper_trading_performance,
    format_portfolio_report_text,
    save_portfolio_report,
)
from core.trade_journal import TradeJournal
from main import parse_args, resolve_portfolio_price_map, run_egx_portfolio_report


@pytest.fixture
def tmp_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    portfolio_path = tmp_path / "portfolio_state.json"
    trades_path = tmp_path / "trades.json"
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(settings, "PORTFOLIO_STATE_PATH", portfolio_path)
    monkeypatch.setattr(settings, "TRADES_PATH", trades_path)
    monkeypatch.setattr(settings, "REPORTS_DIR", reports_dir)
    portfolio = VirtualPortfolio()
    portfolio.reset()
    journal = TradeJournal()
    journal.clear()
    return tmp_path


def _write_live_snapshot(path: Path, rows: list[dict[str, object]]) -> Path:
    pd.DataFrame(rows, columns=LIVE_SNAPSHOT_COLUMNS).to_csv(path, index=False)
    return path


def _open_trade(
    portfolio: VirtualPortfolio,
    journal: TradeJournal,
    *,
    symbol: str,
    entry: float,
    stop: float,
    target: float,
    quantity: int = 100,
) -> None:
    trade = portfolio.open_trade(
        symbol=symbol,
        side=TradeSide.BUY,
        quantity=quantity,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        reason="test setup",
    )
    journal.append_trade(trade)


def test_empty_portfolio_report(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()

    report = PortfolioReportBuilder().build(portfolio, journal)
    text = format_portfolio_report_text(report)

    assert report.is_empty is True
    assert EMPTY_TRADES_MESSAGE in text
    assert EMPTY_PORTFOLIO_MESSAGE in text
    assert SAFETY_NOTICE in text
    assert "Open Positions:" in text
    assert "- (none)" in text


def test_open_positions_report_with_current_prices(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    _open_trade(
        portfolio,
        journal,
        symbol="COMI",
        entry=80.0,
        stop=78.0,
        target=84.0,
        quantity=500,
    )

    latest_prices = {"COMI": 82.0}
    report = PortfolioReportBuilder().build(
        portfolio,
        journal,
        latest_prices=latest_prices,
        price_source="data/real/egx_live_snapshot.csv",
        snapshot_date=date(2026, 7, 2),
    )
    text = format_portfolio_report_text(report)
    open_section = next(
        section for section in report.sections if section.title == "Open Positions"
    )

    assert report.is_empty is False
    assert "COMI" in text
    assert "Current price: 82.00" in "\n".join(open_section.lines)
    assert "Unrealized PnL: +1,000.00 EGP (+2.50%)" in "\n".join(open_section.lines)
    assert "Open risk:" in "\n".join(open_section.lines)


def test_closed_trades_and_performance_stats(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    _open_trade(
        portfolio,
        journal,
        symbol="WIN",
        entry=10.0,
        stop=9.5,
        target=11.0,
        quantity=100,
    )
    win_trade = portfolio.get_open_trades()[0]
    close_paper_trade(portfolio, journal, win_trade.id, exit_price=11.0)

    _open_trade(
        portfolio,
        journal,
        symbol="LOSS",
        entry=20.0,
        stop=19.0,
        target=22.0,
        quantity=50,
    )
    loss_trade = portfolio.get_open_trades()[0]
    close_paper_trade(portfolio, journal, loss_trade.id, exit_price=19.0)

    report = PortfolioReportBuilder().build(portfolio, journal)
    closed_section = next(
        section for section in report.sections if section.title == "Closed Trades"
    )
    performance = next(
        section for section in report.sections if section.title == "Performance Stats"
    )

    assert "WIN" in "\n".join(closed_section.lines)
    assert "LOSS" in "\n".join(closed_section.lines)
    assert "- Win rate: 50.0%" in performance.lines
    assert "- Wins / losses / breakeven: 1 / 1 / 0" in performance.lines
    assert "- Total closed PnL:" in performance.lines[6]


def test_win_rate_calculation_all_wins(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    for symbol, exit_price in [("A", 11.0), ("B", 11.0)]:
        _open_trade(
            portfolio,
            journal,
            symbol=symbol,
            entry=10.0,
            stop=9.5,
            target=11.0,
            quantity=100,
        )
        trade = next(item for item in portfolio.get_open_trades() if item.symbol == symbol)
        close_paper_trade(portfolio, journal, trade.id, exit_price=exit_price)

    report = PortfolioReportBuilder().build(portfolio, journal)
    performance = next(
        section for section in report.sections if section.title == "Performance Stats"
    )

    assert "- Win rate: 100.0%" in performance.lines
    assert "- Wins / losses / breakeven: 2 / 0 / 0" in performance.lines


def test_missing_current_price_handling(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    _open_trade(
        portfolio,
        journal,
        symbol="COMI",
        entry=80.0,
        stop=78.0,
        target=84.0,
    )

    report = PortfolioReportBuilder().build(portfolio, journal, latest_prices=None)
    summary = next(
        section for section in report.sections if section.title == "Portfolio Summary"
    )
    open_section = next(
        section for section in report.sections if section.title == "Open Positions"
    )

    assert "- Total unrealized PnL: unavailable" in summary.lines
    assert "Current price: unavailable" in "\n".join(open_section.lines)
    assert "Unrealized PnL: unavailable" in "\n".join(open_section.lines)


def test_save_portfolio_report_writes_txt_and_json(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    report = PortfolioReportBuilder().build(portfolio, journal)

    txt_path, json_path = save_portfolio_report(report, settings.REPORTS_DIR)

    assert txt_path.exists()
    assert json_path.exists()
    assert txt_path.name.startswith("egx_portfolio_report_")
    assert json_path.name.startswith("egx_portfolio_report_")
    assert "=== EGX Paper Portfolio Report ===" in txt_path.read_text(encoding="utf-8")
    assert SAFETY_NOTICE in json_path.read_text(encoding="utf-8")


def test_parse_args_egx_workflow_portfolio() -> None:
    args = parse_args(["--egx-workflow", "portfolio"])
    assert args.egx_workflow == "portfolio"

    local_args = parse_args(["--egx-workflow", "portfolio", "--egx-local"])
    assert local_args.egx_workflow == "portfolio"
    assert local_args.egx_local is True


def test_resolve_portfolio_price_map_uses_snapshot(tmp_storage: Path) -> None:
    snapshot_path = _write_live_snapshot(
        tmp_storage / "egx_live_snapshot.csv",
        [
            {
                "date": "2026-07-02",
                "symbol": "COMI",
                "previous_close": 79.0,
                "open": 79.5,
                "high": 82.0,
                "low": 79.0,
                "close": 81.0,
                "volume": 1000,
            }
        ],
    )

    prices, source, snapshot_date = resolve_portfolio_price_map(
        snapshot_path,
        use_local_snapshot=True,
    )

    assert prices == {"COMI": 81.0}
    assert source is not None
    assert snapshot_date == date(2026, 7, 2)


def test_run_egx_portfolio_report_no_broker_calls(
    tmp_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_path = _write_live_snapshot(
        tmp_storage / "egx_live_snapshot.csv",
        [
            {
                "date": "2026-07-02",
                "symbol": "COMI",
                "previous_close": 79.0,
                "open": 79.5,
                "high": 82.0,
                "low": 79.0,
                "close": 81.0,
                "volume": 1000,
            }
        ],
    )

    def fail_broker(*_args, **_kwargs):
        raise AssertionError("broker APIs must not be called")

    monkeypatch.setattr(
        "main.EgxLiveSnapshotProvider",
        lambda csv_path: EgxLiveSnapshotProvider(csv_path),
    )
    monkeypatch.setattr("main.ChromeRemoteDebugLauncher", fail_broker)

    exit_code = run_egx_portfolio_report(
        snapshot_path,
        use_local_snapshot=True,
    )

    assert exit_code == 0
    saved_reports = list(settings.REPORTS_DIR.glob("egx_portfolio_report_*.txt"))
    assert saved_reports


def test_run_egx_portfolio_report_with_open_trades(
    tmp_storage: Path,
    capsys,
) -> None:
    portfolio = VirtualPortfolio()
    journal = TradeJournal()
    _open_trade(
        portfolio,
        journal,
        symbol="COMI",
        entry=80.0,
        stop=78.0,
        target=84.0,
    )
    snapshot_path = _write_live_snapshot(
        tmp_storage / "egx_live_snapshot.csv",
        [
            {
                "date": "2026-07-02",
                "symbol": "COMI",
                "previous_close": 79.0,
                "open": 79.5,
                "high": 82.0,
                "low": 79.0,
                "close": 82.0,
                "volume": 1000,
            }
        ],
    )

    exit_code = run_egx_portfolio_report(
        snapshot_path,
        use_local_snapshot=True,
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "=== EGX Paper Portfolio Report ===" in output
    assert SAFETY_NOTICE in output
    assert "COMI" in output
    assert "Portfolio report saved:" in output


def test_daily_report_performance_without_storage(tmp_path: Path) -> None:
    lines, payload = build_daily_report_paper_trading_performance(
        None,
        None,
        storage_available=False,
    )

    assert "No paper portfolio data found." in lines[0]
    assert payload["available"] is False


def test_daily_report_performance_empty_portfolio(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()

    lines, payload = build_daily_report_paper_trading_performance(
        portfolio,
        TradeJournal(),
        storage_available=True,
    )

    assert payload["available"] is True
    assert payload["closed_trades_count"] == 0
    assert payload["open_positions_count"] == 0
    assert "Win Rate: n/a" in "\n".join(lines)


def test_daily_report_performance_inferred_initial_capital(
    tmp_storage: Path,
) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    portfolio.initial_capital = 0.0
    portfolio.save()
    trade = portfolio.open_trade(
        symbol="COMI",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=50.0,
        stop_loss=48.0,
        take_profit=55.0,
    )
    journal = TradeJournal()
    journal.append_trade(trade)

    lines, payload = build_daily_report_paper_trading_performance(
        portfolio,
        journal,
        latest_prices={"COMI": 52.0},
        storage_available=True,
    )

    assert payload["initial_capital_inferred"] is True
    assert "(inferred)" in "\n".join(lines)
    assert payload["unrealized_pnl"] == pytest.approx(200.0)


def test_daily_report_performance_without_storage(tmp_path: Path) -> None:
    lines, payload = build_daily_report_paper_trading_performance(
        None,
        None,
        storage_available=False,
    )

    assert "No paper portfolio data found." in lines[0]
    assert payload["available"] is False


def test_daily_report_performance_empty_portfolio(tmp_storage: Path) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()

    lines, payload = build_daily_report_paper_trading_performance(
        portfolio,
        TradeJournal(),
        storage_available=True,
    )

    assert payload["available"] is True
    assert payload["closed_trades_count"] == 0
    assert payload["open_positions_count"] == 0
    assert "Win Rate: n/a" in "\n".join(lines)


def test_daily_report_performance_inferred_initial_capital(
    tmp_storage: Path,
) -> None:
    portfolio = VirtualPortfolio()
    portfolio.reset()
    portfolio.initial_capital = 0.0
    portfolio.save()
    trade = portfolio.open_trade(
        symbol="COMI",
        side=TradeSide.BUY,
        quantity=100,
        entry_price=50.0,
        stop_loss=48.0,
        take_profit=55.0,
    )
    journal = TradeJournal()
    journal.append_trade(trade)

    lines, payload = build_daily_report_paper_trading_performance(
        portfolio,
        journal,
        latest_prices={"COMI": 52.0},
        storage_available=True,
    )

    assert payload["initial_capital_inferred"] is True
    assert "(inferred)" in "\n".join(lines)
    assert payload["unrealized_pnl"] == pytest.approx(200.0)
