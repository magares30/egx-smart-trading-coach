"""Tests for the daily backtesting engine."""

from datetime import date

import pytest

from config import settings
from config.watchlist import DEFAULT_WATCHLIST, MARKET_INDEX_SYMBOLS
from core.backtester import (
    BacktestClosedTrade,
    BacktestConfig,
    BacktestExitReason,
    BacktestPosition,
    BacktestReport,
    DailyBacktester,
    evaluate_position_exit,
)
from core.market_data import CsvMarketDataProvider, MarketBar
from main import format_closed_trade_line, print_backtest_report


@pytest.fixture
def bull_provider() -> CsvMarketDataProvider:
    return CsvMarketDataProvider(settings.EGX_BULL_SAMPLE_PATH)


@pytest.fixture
def weak_provider() -> CsvMarketDataProvider:
    return CsvMarketDataProvider(settings.EGX_WEAK_SAMPLE_PATH)


def _run_backtest(
    provider: CsvMarketDataProvider,
    config: BacktestConfig | None = None,
) -> BacktestReport:
    backtester = DailyBacktester(
        provider=provider,
        symbols=DEFAULT_WATCHLIST,
        index_symbols=MARKET_INDEX_SYMBOLS,
        config=config,
    )
    return backtester.run()


def test_backtester_returns_report(bull_provider: CsvMarketDataProvider) -> None:
    report = _run_backtest(bull_provider)

    assert isinstance(report, BacktestReport)
    assert report.metrics.starting_capital == 100_000
    assert report.strategy_name == "Trend Join Long"
    assert len(report.equity_curve) >= 1


def test_weak_scenario_opens_no_trades_or_no_profit(
    weak_provider: CsvMarketDataProvider,
) -> None:
    config = BacktestConfig(close_open_positions_at_end=True)
    report = _run_backtest(weak_provider, config)

    assert report.metrics.total_closed_trades == 0 or all(
        trade.pnl <= 0 for trade in report.closed_trades
    )
    assert report.metrics.open_positions_count == 0


def test_bull_scenario_produces_at_least_one_trade(
    bull_provider: CsvMarketDataProvider,
) -> None:
    report = _run_backtest(bull_provider)

    assert report.metrics.total_closed_trades >= 1


def test_end_of_test_closes_open_positions(
    bull_provider: CsvMarketDataProvider,
) -> None:
    config = BacktestConfig(close_open_positions_at_end=True)
    report = _run_backtest(bull_provider, config)

    assert report.metrics.open_positions_count == 0
    assert report.open_positions == []


def test_max_drawdown_is_non_negative(bull_provider: CsvMarketDataProvider) -> None:
    report = _run_backtest(bull_provider)

    assert report.metrics.max_drawdown_percent >= 0


def test_same_day_exit_is_not_allowed() -> None:
    bar = MarketBar(
        date=date(2026, 7, 2),
        symbol="TEST",
        open=10.0,
        high=15.0,
        low=8.0,
        close=12.0,
        volume=1000,
    )
    position = BacktestPosition(
        symbol="TEST",
        quantity=100,
        entry_price=10.0,
        stop_loss=9.0,
        take_profit=14.0,
        opened_at=date(2026, 7, 2),
        confidence_score=80,
        reasons=["test"],
    )

    result = evaluate_position_exit(
        bar,
        position,
        opened_on_same_day=True,
        stop_first_when_both_hit=True,
    )
    assert result is None

    result_next_day = evaluate_position_exit(
        MarketBar(
            date=date(2026, 7, 3),
            symbol="TEST",
            open=13.0,
            high=15.0,
            low=12.5,
            close=14.5,
            volume=1000,
        ),
        position,
        opened_on_same_day=False,
        stop_first_when_both_hit=True,
    )
    assert result_next_day is not None
    assert result_next_day[1] == BacktestExitReason.TAKE_PROFIT


def test_backtester_no_same_day_closed_trades(
    bull_provider: CsvMarketDataProvider,
) -> None:
    """Integration check: TP/SL exits never occur on the opening date."""
    report = _run_backtest(bull_provider)

    for trade in report.closed_trades:
        if trade.exit_reason in (
            BacktestExitReason.TAKE_PROFIT,
            BacktestExitReason.STOP_LOSS,
        ):
            assert trade.closed_at > trade.opened_at


def test_format_closed_trade_line_is_clean() -> None:
    trade = BacktestClosedTrade(
        symbol="HRHO",
        quantity=100,
        entry_price=49.60,
        exit_price=50.99,
        stop_loss=48.0,
        take_profit=52.0,
        opened_at=date(2026, 7, 3),
        closed_at=date(2026, 7, 7),
        exit_reason=BacktestExitReason.END_OF_TEST,
        pnl=139.0,
        pnl_percent=2.8,
        confidence_score=80,
        reasons=["test"],
    )

    line = format_closed_trade_line(trade)

    assert "`n" not in line
    assert "Exit" in line
    assert "PnL" in line
    assert line == (
        "  HRHO | Entry 49.60 | Exit 50.99 | END_OF_TEST | PnL +139.00 EGP"
    )


def test_print_backtest_report_output(
    bull_provider: CsvMarketDataProvider,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = _run_backtest(bull_provider)

    print_backtest_report(
        report,
        "bull",
        settings.EGX_BULL_SAMPLE_PATH,
    )
    output = capsys.readouterr().out

    assert "`n" not in output
    assert "Exit" in output
    assert "PnL" in output
    assert "Closed trades:" in output
    for trade in report.closed_trades:
        assert format_closed_trade_line(trade) in output
