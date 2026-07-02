"""Application settings for EGX Smart Trading Coach."""

from datetime import date
from pathlib import Path

# Trading parameters
INITIAL_CAPITAL_EGP: float = 100_000
RISK_PER_TRADE_PERCENT: float = 1.0
MAX_DAILY_LOSS_PERCENT: float = 3.0
MAX_OPEN_POSITIONS: int = 5

# Safety flag — must remain True for paper trading only
PAPER_TRADING_ONLY: bool = True

# Market metadata
MARKET_NAME: str = "EGX"
BASE_CURRENCY: str = "EGP"

# Storage paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORAGE_DIR = PROJECT_ROOT / "storage"
PORTFOLIO_STATE_PATH = STORAGE_DIR / "portfolio_state.json"
TRADES_PATH = STORAGE_DIR / "trades.json"
DATA_DIR = PROJECT_ROOT / "data"
SAMPLES_DIR = DATA_DIR / "samples"
SAMPLE_PRICES_PATH = DATA_DIR / "sample_prices.csv"
EGX_DAILY_SAMPLE_PATH = DATA_DIR / "egx_daily_sample.csv"
EGX_BULL_SAMPLE_PATH = SAMPLES_DIR / "egx_bull_sample.csv"
EGX_MIXED_SAMPLE_PATH = SAMPLES_DIR / "egx_mixed_sample.csv"
EGX_WEAK_SAMPLE_PATH = SAMPLES_DIR / "egx_weak_sample.csv"
REAL_DATA_DIR = DATA_DIR / "real"
DEFAULT_REAL_EGX_CSV_PATH = REAL_DATA_DIR / "egx_real_normalized.csv"
EGX_LIVE_SNAPSHOT_PATH = REAL_DATA_DIR / "egx_live_snapshot.csv"
EGX_LIVE_INGEST_WARNINGS_PATH = REAL_DATA_DIR / "egx_live_ingest_warnings.json"
LIVE_HISTORY_DIR = REAL_DATA_DIR / "live_history"
MIN_VALID_SYMBOL_COUNT_WARN = 150
MIN_VALID_SYMBOL_COUNT_CRITICAL = 80
DEFAULT_LIVE_VOLUME_LOOKBACK_DAYS = 20
DEFAULT_LIVE_VOLUME_MIN_HISTORY_DAYS = 3
DEFAULT_TALIB_MIN_HISTORY_DAYS = 50
# Official EGX holiday dates can be added here (YYYY-MM-DD).
EGX_TRADING_HOLIDAYS: frozenset[date] = frozenset()
REPORTS_DIR = DATA_DIR / "reports"
DOWNLOADS_DIR = DATA_DIR / "downloads"
EGX_PUBLIC_DOWNLOADS_DIR = DOWNLOADS_DIR / "egx_public"
