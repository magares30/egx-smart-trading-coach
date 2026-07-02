"""Daily backtesting engine for the EGX paper-trading pipeline."""

from datetime import UTC, date, datetime
from enum import Enum

from pydantic import BaseModel, Field

from core.market_data import CsvMarketDataProvider, MarketBar
from core.market_mood import MarketMoodDetector
from core.scanner import EgyptianMomentumScanner
from core.strategy import TrendJoinLongStrategy


class BacktestExitReason(str, Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    END_OF_TEST = "END_OF_TEST"
    MANUAL_CLOSE = "MANUAL_CLOSE"


class BacktestTradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class BacktestConfig(BaseModel):
    initial_capital: float = 100_000
    risk_per_trade_percent: float = 1.0
    max_open_positions: int = 5
    max_trades_per_day: int = 3
    min_confidence_score: int = 70
    risk_reward_target: float = 2.0
    close_open_positions_at_end: bool = True
    stop_first_when_tp_and_sl_same_day: bool = True


class BacktestPosition(BaseModel):
    symbol: str
    quantity: int
    entry_price: float
    stop_loss: float
    take_profit: float
    opened_at: date
    confidence_score: int
    reasons: list[str] = Field(default_factory=list)


class BacktestClosedTrade(BaseModel):
    symbol: str
    quantity: int
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    opened_at: date
    closed_at: date
    exit_reason: BacktestExitReason
    pnl: float
    pnl_percent: float
    confidence_score: int
    reasons: list[str] = Field(default_factory=list)


class BacktestDailySnapshot(BaseModel):
    date: date
    cash: float
    open_positions_value: float
    equity: float
    open_positions_count: int
    closed_trades_count: int


class BacktestMetrics(BaseModel):
    starting_capital: float
    ending_equity: float
    net_pnl: float
    net_pnl_percent: float
    total_closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    max_drawdown_percent: float
    open_positions_count: int


class BacktestReport(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    strategy_name: str
    config: BacktestConfig
    metrics: BacktestMetrics
    closed_trades: list[BacktestClosedTrade]
    open_positions: list[BacktestPosition]
    equity_curve: list[BacktestDailySnapshot]
    notes: list[str] = Field(default_factory=list)


def evaluate_position_exit(
    bar: MarketBar,
    position: BacktestPosition,
    *,
    opened_on_same_day: bool,
    stop_first_when_both_hit: bool,
) -> tuple[float, BacktestExitReason] | None:
    """Return exit price and reason if the daily bar triggers an exit."""
    if opened_on_same_day:
        return None

    stop_hit = bar.low <= position.stop_loss
    tp_hit = bar.high >= position.take_profit

    if stop_hit and tp_hit:
        if stop_first_when_both_hit:
            return position.stop_loss, BacktestExitReason.STOP_LOSS
        return position.take_profit, BacktestExitReason.TAKE_PROFIT
    if stop_hit:
        return position.stop_loss, BacktestExitReason.STOP_LOSS
    if tp_hit:
        return position.take_profit, BacktestExitReason.TAKE_PROFIT
    return None


class DailyBacktester:
    """Runs a day-by-day simulation of the scanner/strategy pipeline."""

    STRATEGY_NAME = "Trend Join Long"

    def __init__(
        self,
        provider: CsvMarketDataProvider,
        symbols: list[str],
        index_symbols: list[str],
        config: BacktestConfig | None = None,
    ) -> None:
        self._provider = provider
        self._symbols = symbols
        self._index_symbols = index_symbols
        self._config = config or BacktestConfig()

    def _close_position(
        self,
        position: BacktestPosition,
        exit_price: float,
        closed_at: date,
        exit_reason: BacktestExitReason,
        cash: float,
        closed_trades: list[BacktestClosedTrade],
    ) -> float:
        pnl = (exit_price - position.entry_price) * position.quantity
        pnl_percent = (pnl / (position.entry_price * position.quantity)) * 100
        closed_trades.append(
            BacktestClosedTrade(
                symbol=position.symbol,
                quantity=position.quantity,
                entry_price=position.entry_price,
                exit_price=exit_price,
                stop_loss=position.stop_loss,
                take_profit=position.take_profit,
                opened_at=position.opened_at,
                closed_at=closed_at,
                exit_reason=exit_reason,
                pnl=pnl,
                pnl_percent=pnl_percent,
                confidence_score=position.confidence_score,
                reasons=position.reasons,
            )
        )
        return cash + (position.quantity * exit_price)

    def _process_exits(
        self,
        current_date: date,
        open_positions: dict[str, BacktestPosition],
        cash: float,
        closed_trades: list[BacktestClosedTrade],
    ) -> tuple[dict[str, BacktestPosition], float]:
        remaining: dict[str, BacktestPosition] = {}
        for symbol, position in open_positions.items():
            if position.opened_at == current_date:
                remaining[symbol] = position
                continue
            try:
                bar = self._provider.get_bar_for_date(symbol, current_date)
            except ValueError:
                remaining[symbol] = position
                continue

            exit_result = evaluate_position_exit(
                bar,
                position,
                opened_on_same_day=False,
                stop_first_when_both_hit=self._config.stop_first_when_tp_and_sl_same_day,
            )
            if exit_result is None:
                remaining[symbol] = position
                continue

            exit_price, exit_reason = exit_result
            cash = self._close_position(
                position, exit_price, current_date, exit_reason, cash, closed_trades
            )
        return remaining, cash

    def _open_positions_value(
        self, open_positions: dict[str, BacktestPosition], current_date: date
    ) -> float:
        total = 0.0
        for symbol, position in open_positions.items():
            try:
                bar = self._provider.get_bar_for_date(symbol, current_date)
                price = bar.close
            except ValueError:
                price = position.entry_price
            total += price * position.quantity
        return total

    def _record_snapshot(
        self,
        current_date: date,
        cash: float,
        open_positions: dict[str, BacktestPosition],
        closed_trades: list[BacktestClosedTrade],
        equity_curve: list[BacktestDailySnapshot],
    ) -> None:
        open_value = self._open_positions_value(open_positions, current_date)
        equity_curve.append(
            BacktestDailySnapshot(
                date=current_date,
                cash=cash,
                open_positions_value=open_value,
                equity=cash + open_value,
                open_positions_count=len(open_positions),
                closed_trades_count=len(closed_trades),
            )
        )

    def _try_open_positions(
        self,
        current_date: date,
        strategy_report,
        open_positions: dict[str, BacktestPosition],
        cash: float,
    ) -> tuple[dict[str, BacktestPosition], float]:
        cfg = self._config
        equity = cash + self._open_positions_value(open_positions, current_date)
        opened_today = 0

        setups = sorted(
            strategy_report.buy_setups,
            key=lambda s: (-s.confidence_score, -(s.risk_reward or 0.0)),
        )

        for setup in setups:
            if opened_today >= cfg.max_trades_per_day:
                break
            if setup.signal is None:
                continue
            if setup.confidence_score < cfg.min_confidence_score:
                continue
            if setup.symbol in open_positions:
                continue
            if len(open_positions) >= cfg.max_open_positions:
                break

            signal = setup.signal
            risk_per_share = signal.entry_price - signal.stop_loss
            if risk_per_share <= 0:
                continue

            risk_amount = equity * (cfg.risk_per_trade_percent / 100)
            quantity = int(risk_amount / risk_per_share)
            if quantity < 1:
                continue

            cost = quantity * signal.entry_price
            if cost > cash:
                continue

            open_positions[setup.symbol] = BacktestPosition(
                symbol=setup.symbol,
                quantity=quantity,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                opened_at=current_date,
                confidence_score=setup.confidence_score,
                reasons=list(setup.reasons),
            )
            cash -= cost
            opened_today += 1
            equity = cash + self._open_positions_value(open_positions, current_date)

        return open_positions, cash

    def _calculate_metrics(
        self,
        closed_trades: list[BacktestClosedTrade],
        open_positions: dict[str, BacktestPosition],
        equity_curve: list[BacktestDailySnapshot],
    ) -> BacktestMetrics:
        cfg = self._config
        ending_equity = equity_curve[-1].equity if equity_curve else cfg.initial_capital
        net_pnl = ending_equity - cfg.initial_capital
        net_pnl_percent = (net_pnl / cfg.initial_capital) * 100 if cfg.initial_capital else 0.0

        winners = [t for t in closed_trades if t.pnl > 0]
        losers = [t for t in closed_trades if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in winners)
        gross_loss = sum(t.pnl for t in losers)
        profit_factor: float | None
        if gross_loss != 0:
            profit_factor = gross_profit / abs(gross_loss)
        else:
            profit_factor = None

        peak = cfg.initial_capital
        max_drawdown = 0.0
        for snap in equity_curve:
            peak = max(peak, snap.equity)
            if peak > 0:
                drawdown = ((peak - snap.equity) / peak) * 100
                max_drawdown = max(max_drawdown, drawdown)

        total = len(closed_trades)
        win_rate = (len(winners) / total * 100) if total else 0.0

        return BacktestMetrics(
            starting_capital=cfg.initial_capital,
            ending_equity=ending_equity,
            net_pnl=net_pnl,
            net_pnl_percent=net_pnl_percent,
            total_closed_trades=total,
            winning_trades=len(winners),
            losing_trades=len(losers),
            win_rate=win_rate,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            profit_factor=profit_factor,
            max_drawdown_percent=max_drawdown,
            open_positions_count=len(open_positions),
        )

    def run(self) -> BacktestReport:
        """Execute the daily backtest and return a full report."""
        cfg = self._config
        dates = self._provider.get_available_dates()
        notes = [
            "Backtest uses local DEMO/FIXTURE daily data.",
            "V1 uses daily OHLC approximation, not intraday ticks.",
            "If TP and SL hit in the same daily candle, V1 uses the configured conservative rule.",
        ]

        cash = cfg.initial_capital
        open_positions: dict[str, BacktestPosition] = {}
        closed_trades: list[BacktestClosedTrade] = []
        equity_curve: list[BacktestDailySnapshot] = []

        strategy = TrendJoinLongStrategy(risk_reward_target=cfg.risk_reward_target)

        for current_date in dates[1:]:
            open_positions, cash = self._process_exits(
                current_date, open_positions, cash, closed_trades
            )

            market_snapshot = self._provider.build_market_snapshot_as_of(
                self._symbols, self._index_symbols, current_date
            )
            mood_result = MarketMoodDetector().evaluate(market_snapshot.index_snapshots)
            scanner_report = EgyptianMomentumScanner(mood_result).scan(market_snapshot)
            strategy_report = strategy.generate_signals(scanner_report, market_snapshot)

            open_positions, cash = self._try_open_positions(
                current_date, strategy_report, open_positions, cash
            )
            self._record_snapshot(
                current_date, cash, open_positions, closed_trades, equity_curve
            )

        if cfg.close_open_positions_at_end and open_positions and dates:
            final_date = dates[-1]
            remaining: dict[str, BacktestPosition] = {}
            for symbol, position in open_positions.items():
                try:
                    bar = self._provider.get_bar_for_date(symbol, final_date)
                    exit_price = bar.close
                except ValueError:
                    exit_price = position.entry_price
                cash = self._close_position(
                    position,
                    exit_price,
                    final_date,
                    BacktestExitReason.END_OF_TEST,
                    cash,
                    closed_trades,
                )
            open_positions = remaining
            if equity_curve and equity_curve[-1].date == final_date:
                equity_curve[-1] = BacktestDailySnapshot(
                    date=final_date,
                    cash=cash,
                    open_positions_value=0.0,
                    equity=cash,
                    open_positions_count=0,
                    closed_trades_count=len(closed_trades),
                )
            elif dates:
                self._record_snapshot(
                    final_date, cash, open_positions, closed_trades, equity_curve
                )

        if not equity_curve:
            equity_curve.append(
                BacktestDailySnapshot(
                    date=dates[-1] if dates else date.today(),
                    cash=cfg.initial_capital,
                    open_positions_value=0.0,
                    equity=cfg.initial_capital,
                    open_positions_count=0,
                    closed_trades_count=0,
                )
            )

        metrics = self._calculate_metrics(closed_trades, open_positions, equity_curve)

        return BacktestReport(
            strategy_name=self.STRATEGY_NAME,
            config=cfg,
            metrics=metrics,
            closed_trades=closed_trades,
            open_positions=list(open_positions.values()),
            equity_curve=equity_curve,
            notes=notes,
        )
