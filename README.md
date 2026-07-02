# EGX Smart Trading Coach

A safe, **paper-trading-only** Python assistant for the Egyptian Stock Exchange (EGX). This project simulates trades with virtual money — it does not connect to real brokerage accounts and does not place real orders.

## What This Project Is

EGX Smart Trading Coach is the foundation for an AI-assisted trading workflow inspired by:

- Scanner A
- Scanner B
- Strategy filter
- Backtesting
- Telegram alerts (planned)

## Patch History

### Patch 1 — Foundation

Core building blocks: configuration, data models, virtual portfolio, risk management, and trade journal.

### Patch 3 — EGX CSV Data Provider + Market Snapshot + Market Mood

Adds a local market data layer that reads OHLCV data from CSV files and generates market snapshots and mood detection.

**Important:** `data/egx_daily_sample.csv` contains **fake/sample data for development only**. These are not real EGX prices. There is no live EGX data feed yet and no Thndr integration.

Features added:

- `CsvMarketDataProvider` — loads daily OHLCV from local CSV
- `SymbolSnapshot` / `MarketSnapshot` — per-symbol and market-wide summaries
- `MarketMoodDetector` — scores index momentum as STRONG, NEUTRAL, or WEAK
- Enhanced `main.py` — prints market overview before the COMI paper trade demo

### Patch 4 — Scanner A: Egyptian Momentum Watchlist

Adds `EgyptianMomentumScanner` — the first scanner that ranks symbols from the local watchlist using momentum, volume, breakout behavior, and market mood.

- Ranks `DEFAULT_WATCHLIST` symbols as **CANDIDATE**, **WATCH**, or **BLOCKED**
- Uses demo CSV data from `data/egx_daily_sample.csv` only
- Does **not** execute trades — scanning only
- First step before Strategy Scanner B

### Patch 4.5 — Realistic Sample Scenarios

Adds isolated demo CSV fixtures under `data/samples/` to test Scanner A under different market conditions:

| Scenario | File | Expected behavior |
|----------|------|-------------------|
| `default` | `data/egx_daily_sample.csv` | Original demo (backward compatible) |
| `bull` | `data/samples/egx_bull_sample.csv` | STRONG mood, multiple CANDIDATES |
| `mixed` | `data/samples/egx_mixed_sample.csv` | NEUTRAL mood, mix of CANDIDATE / WATCH / BLOCKED |
| `weak` | `data/samples/egx_weak_sample.csv` | WEAK mood, all symbols BLOCKED |

**Important:**

- Files under `data/samples/` are **fake demo/test fixtures** — not real EGX market data.
- They are used only to test scanner behavior under bull, mixed, and weak conditions.
- They can be **deleted later** without affecting scanner, portfolio, or risk logic.
- Scanner logic does **not** depend on fake sample data — it reads whatever CSV path is provided.
- Future real data will come through a separate data provider.

Run scenarios:

```bash
python main.py --scenario default
python main.py --scenario bull
python main.py --scenario mixed
python main.py --scenario weak
```

### Patch 5 — Strategy Scanner B

Adds `TrendJoinLongStrategy` — converts Scanner A candidates into `TradeSignal` trade plans with entry, stop loss, take profit, and risk/reward.

- Scanner A finds strong symbols from the watchlist.
- Strategy Scanner B converts candidates into structured trade plans.
- It creates entry, stop loss, take profit, and risk/reward targets.
- It does **not** execute trades — paper execution remains separate for now.
- A future patch will connect Strategy Scanner B to `RiskManager` and `VirtualPortfolio` for automatic paper trades.
- Strategy logic depends only on `ScannerReport` and `MarketSnapshot` — not on CSV file names.

### Patch 5.5 — Demo Cleanup and Data Source Separation

Separates analysis-only runs from the optional COMI paper-trade demo.

- **Normal runs are analysis-only** — market snapshot, mood, Scanner A, Strategy Scanner B.
- The COMI paper-trade demo is **opt-in** via `--demo-trade`.
- Files under `data/samples/` are fake scenario fixtures only — not real EGX data.
- Scanner and strategy depend on `MarketSnapshot`, not on fake CSV file names.
- `MarketDataProvider` protocol abstracts data access; `CsvMarketDataProvider` implements it today.
- Future real EGX data will use `RealEgxDataProvider` implementing the same interface.

Analysis only:

```bash
python main.py --scenario bull
python main.py --scenario mixed
python main.py --scenario weak
```

Analysis + demo paper trade:

```bash
python main.py --scenario bull --demo-trade
```

### Patch 6 — Auto Paper Trading from Strategy Signals

Connects Strategy Scanner B to `RiskManager`, `VirtualPortfolio`, and `TradeJournal`.

- Strategy Scanner B creates `BUY_SETUP` trade plans.
- `AutoPaperTrader` opens paper trades from the best signals (up to 3 per run, confidence ≥ 70).
- Every trade passes through `RiskManager` before opening.
- Trades are recorded in `TradeJournal`.
- Does **not** close trades yet.
- Does **not** place real orders or connect to Thndr.

Analysis only:

```bash
python main.py --scenario bull
```

Auto paper trading:

```bash
python main.py --scenario bull --auto-paper-trade
```

Clean auto paper trading run:

```bash
python main.py --scenario bull --reset-paper-state --auto-paper-trade
```

Old COMI demo (separate, hardcoded):

```bash
python main.py --scenario bull --demo-trade
```

### Patch 7 — Auto Exit / Paper Trade Monitor

Adds `PaperTradeMonitor` to review open paper trades and close them when:

- Take profit is hit (closes at TP price)
- Stop loss is hit (closes at SL price)
- End-of-day exit is forced (closes at latest price)

- Checks open paper trades only — does not open new trades.
- Updates `TradeJournal` when trades are closed.
- Paper trading only — no real orders, no Thndr.

Open paper trades:

```bash
python main.py --scenario bull --reset-paper-state --auto-paper-trade
```

Monitor open trades:

```bash
python main.py --scenario bull --monitor-paper-trades
```

Open then monitor:

```bash
python main.py --scenario bull --reset-paper-state --auto-paper-trade --monitor-paper-trades
```

Force close all open paper trades:

```bash
python main.py --scenario bull --monitor-paper-trades --force-eod-exit
```

### Patch 8 — Backtesting Engine V1

Adds `DailyBacktester` — a simple daily backtesting engine that runs the same pipeline on local demo CSV data:

- `MarketDataProvider` → `MarketSnapshot` (as-of each date)
- `MarketMoodDetector` → `EgyptianMomentumScanner` → `TrendJoinLongStrategy`
- Simulated entries, stop loss, take profit, and end-of-test exits
- Backtest report with equity curve and performance metrics

**Important:**

- Backtesting uses **local demo CSV data only** — not real EGX prices.
- It runs the same market mood, Scanner A, and Strategy Scanner B as live analysis.
- It simulates entries, stops, targets, and end-of-test exits using daily OHLC bars.
- It does **not** use real EGX data yet.
- It does **not** connect to Thndr or any broker.
- It is **not proof of real profitability** — only a technical simulation to validate strategy logic.

Run examples:

```bash
python main.py --scenario bull --backtest
python main.py --scenario mixed --backtest
python main.py --scenario weak --backtest
```

### Patch 9 — Real EGX Data Import Provider V1

Prepares the project to use **manually provided** real EGX market data from local CSV files.

**Important:**

- This patch does **not** scrape, fetch, or download data from the internet.
- You manually place real CSV exports under `data/real/`.
- Expected normalized CSV format:

  ```csv
  date,symbol,open,high,low,close,volume
  ```

- `data/real/*.csv` and `data/real/*.xlsx` are gitignored — do not commit licensed/private data.
- Scanner, strategy, backtester, and paper trading logic are unchanged — they read whatever CSV path you provide via `CsvMarketDataProvider`.

Validate a raw file:

```bash
python main.py --validate-real-csv --real-csv data/real/my_egx_file.csv
```

Normalize into the standard format:

```bash
python main.py --normalize-real-csv --real-csv data/real/my_egx_file.csv
```

Run analysis on real local data:

```bash
python main.py --data-source real --real-csv data/real/egx_real_normalized.csv
```

Backtest real local data:

```bash
python main.py --data-source real --real-csv data/real/egx_real_normalized.csv --backtest
```

Auto paper trade on real local data:

```bash
python main.py --data-source real --real-csv data/real/egx_real_normalized.csv --auto-paper-trade
```

Demo mode remains the default:

```bash
python main.py --scenario bull
python main.py --data-source demo --scenario bull
```

### Patch 9.5 — Daily Real Data Importer V1

Makes it easy to append a new daily EGX file into the master normalized real data file without manual CSV editing.

**Daily workflow:**

1. Download the daily market file from your chosen data source.
2. Place it in `data/real/` (CSV or XLSX).
3. Import it into the master file:

   ```bash
   python main.py --import-daily-real-csv data/real/daily_file.csv
   ```

   Or for Excel:

   ```bash
   python main.py --import-daily-real-csv data/real/daily_2026_07_01.xlsx
   ```

4. Run the bot on the master file:

   ```bash
   python main.py --data-source real
   ```

**How it works:**

- Appends new rows into `data/real/egx_real_normalized.csv`
- Removes duplicate `date + symbol` rows, keeping the latest imported row
- Sorts all data by date then symbol
- Supports CSV and XLSX with English and Arabic column aliases
- Does **not** fetch data from the internet

### Patch 9.6 — Safe Data Downloader V1

Adds a **safe downloader layer** that can fetch market data files into `data/downloads/`, then optionally import them into `data/real/egx_real_normalized.csv`.

**Important:**

- Safe downloader only — no scraping, no Thndr, no browser automation, no hidden APIs.
- Supported modes:
  - Direct CSV/XLSX/ZIP file URL
  - Kaggle dataset (requires your own `~/.kaggle/kaggle.json` credentials)
  - EODHD API with your own API key

Examples:

```bash
python main.py --download-data direct-url --url "DIRECT_FILE_LINK.csv"
python main.py --download-data direct-url --url "DIRECT_FILE_LINK.csv" --import-after-download
python main.py --download-data kaggle --kaggle-dataset "owner/dataset"
python main.py --download-data eodhd --eodhd-symbol COMI --eodhd-api-key YOUR_KEY
```

Kaggle support is optional — install the Kaggle CLI or `kaggle` Python package and configure credentials locally. It is not required for the rest of the project.

### Patch 9.7 — EGX Public Market Watch Reader V1

Reads **public EGX market-watch pages** from the official EGX website and saves extracted tables as CSV files under `data/downloads/egx_public/`.

**Important:**

- Public EGX pages only — no login, no Thndr, no broker data.
- No Selenium or browser automation.
- Saves raw CSV snapshots for market summary, indices, sectors, and stocks.
- Stocks can optionally be normalized/imported if the table has enough OHLCV columns.
- Requires `lxml`, `html5lib`, and `beautifulsoup4` for EGX HTML table parsing with `pandas.read_html`. Install dependencies with:

  ```bash
  python -m pip install -r requirements.txt
  ```

Examples:

```bash
python main.py --egx-public-update
python main.py --egx-public-update --egx-public-page stocks
python main.py --egx-public-update --egx-public-page stocks --import-egx-stocks
```

### Patch 9.8 — EGX Public Browser Reader V1

Reads the **visible stocks table** from the official public EGX prices page using Playwright/Chromium when plain HTTP requests cannot return usable data.

**Important:**

- Public EGX website only — no login, no Thndr, no broker, no stored cookies or credentials.
- Uses a temporary in-memory browser session (no profile or cookie files saved).
- Saves raw visible table CSV under `data/downloads/egx_public/browser_stocks_{timestamp}.csv`.
- Optional normalization/import into `data/real/egx_real_normalized.csv`.
- Volume may be missing from the visible table; normalization saves it as `0` with a warning.

Setup:

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Examples:

```bash
python main.py --egx-browser-stocks-update
python main.py --egx-browser-stocks-update --egx-browser-headful
python main.py --egx-browser-stocks-update --import-egx-browser-stocks
```

### Patch 9.8.3 — EGX Attached Chrome Reader

When Playwright-launched browsers are blocked, attach to **Chrome that you start manually** with remote debugging enabled.

**Important:**

- Public EGX only — no login, no Thndr, no broker, no stored cookies or credentials.
- Does **not** launch a new automated browser.
- Does **not** navigate, reload, or click — reads the current visible DOM only.
- Saves CSV under `data/downloads/egx_public/attached_chrome_stocks_{timestamp}.csv`.

**Step 1 — Close all Chrome windows.**

**Step 2 — Start Chrome with remote debugging:**

Windows PowerShell/CMD:

```bat
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\egx_chrome_profile"
```

Alternative path:

```bat
"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\egx_chrome_profile"
```

**Step 3 — In that Chrome window, open:**

`https://egx.com.eg/en/prices.aspx`

**Step 4 — Wait until the stocks table is visible.**

**Step 5 — Run:**

```bash
python main.py --egx-attach-chrome-stocks
```

Optional import:

```bash
python main.py --egx-attach-chrome-stocks --import-egx-attached-stocks
```

Optional custom CDP URL:

```bash
python main.py --egx-attach-chrome-stocks --chrome-cdp-url http://127.0.0.1:9222
```

### Patch 9.9 — Live EGX Snapshot Scanner

Runs **Scanner A** and **Strategy Scanner B** directly from the single-day live snapshot saved at `data/real/egx_live_snapshot.csv`.

**Important:**

- Public EGX only — no login, no Thndr, no broker, no real trades.
- Live scan uses **P.C.** as `previous_close` and **Last Price** as `close`.
- `volume_ratio` is neutral (`1.0`) in V1 because only one trading day is available.
- This is **not** a historical backtest and **not** real trading.
- Paper/simulation output only — no auto paper trade in this command.

**Workflow:**

**Step 1 — Start Chrome with remote debugging** (see Patch 9.8.3 above).

**Step 2 — Open:**

`https://egx.com.eg/en/prices.aspx`

**Step 3 — Save the live snapshot:**

```bash
python main.py --egx-attach-chrome-stocks --import-egx-attached-stocks
```

**Step 4 — Run the live scan:**

```bash
python main.py --egx-live-scan
```

Optional custom snapshot path:

```bash
python main.py --egx-live-scan --egx-live-snapshot data/real/egx_live_snapshot.csv
```

**One command after Chrome is open** (read table, save snapshot, scan):

```bash
python main.py --egx-update-and-live-scan
```

### Patch 9.10 — EGX One-Click Live Scan

Runs the full live EGX workflow in one command:

```bash
python main.py --egx-one-click-live-scan
```

Optional isolated Chrome profile directory:

```bash
python main.py --egx-one-click-live-scan --chrome-profile-dir C:/egx_chrome_profile
```

**What it does:**

- Launches Chrome with remote debugging in an isolated profile if CDP is not already available (`C:/egx_chrome_profile` by default).
- Opens the public EGX prices page when needed.
- Waits for the visible stocks table.
- Reads the table and saves `data/real/egx_live_snapshot.csv`.
- Runs Scanner A and Strategy Scanner B on that snapshot.

**Important:**

- Public EGX only — no login, no Thndr, no broker, no real trading.
- Uses an isolated Chrome profile — does not store cookies in your normal Chrome profile.
- Paper/simulation output only.

**Manual fallback** (if Chrome cannot be auto-launched):

```bash
python main.py --egx-attach-chrome-stocks --import-egx-attached-stocks
python main.py --egx-live-scan
```

### Patch 10 — Live Volume Intelligence V1

Uses stored previous live snapshots to calculate meaningful `volume_ratio` values during live scanning.

**How it works:**

- Each one-click run saves a dated copy to `data/real/live_history/egx_live_snapshot_YYYYMMDD.csv`
- Live scan calculates:
  `volume_ratio = today's volume / average previous volume`
- Only snapshots **before** the current date are used
- If fewer than 3 saved history days exist, `volume_ratio` stays `1.0` with warning:
  `Not enough volume history`

**Daily workflow:**

```bash
python main.py --egx-one-click-live-scan
```

This automatically saves live history and runs the scanner with volume intelligence enabled.

**Optional controls:**

```bash
python main.py --egx-live-scan --live-volume-lookback-days 20 --live-volume-min-history-days 3
python main.py --save-live-history-only
```

**Note:** The first few days may still show weak volume confirmation until at least 3 live snapshots are saved.

### Patch 11 — EGX Daily Report V1

Builds a readable daily report from the live EGX snapshot scanner.

Commands:

```bash
python main.py --egx-daily-report
python main.py --egx-one-click-daily-report
```

**What it includes:**

- Market mood summary
- Top candidates, watch list, blocked reason counts
- Strongest movers and volume leaders
- Strategy buy setups / watch signals
- Warnings from live scan and volume history

Reports are saved under `data/reports/` as timestamped `.txt` and `.json` files.

**Important:**

- Uses existing EGX live snapshot and volume history only
- No news or articles yet
- No real trading
- Does not open paper trades automatically

### Patch 12 — Paper Trading From Live Scan V1

Opens paper trades from live EGX strategy scan `BUY_SETUP` signals only.

Commands:

```bash
python main.py --egx-live-paper-trade
python main.py --egx-one-click-paper-trade
```

Optional limits:

```bash
python main.py --egx-live-paper-trade --live-paper-max-trades 3 --live-paper-min-confidence 75
```

**What it does:**

- Loads the existing `data/real/egx_live_snapshot.csv` (or runs one-click update first)
- Runs the live scanner with volume history
- Opens paper trades for `BUY_SETUP` signals that pass confidence and risk checks
- Prints an OPENED / SKIPPED / REJECTED report

**Important:**

- Paper trading only — no real broker connection
- Requires `BUY_SETUP`; `WATCH` signals are not traded
- Uses the existing risk manager and virtual portfolio
- `--egx-one-click-daily-report` does not paper trade; use `--egx-live-paper-trade` or `--egx-one-click-paper-trade` explicitly

### Patch 13 — Paper Trade Monitor From Live Snapshot V1

Monitors open paper trades against the latest EGX live snapshot OHLC levels.

Commands:

```bash
python main.py --egx-live-paper-monitor
python main.py --egx-one-click-paper-monitor
python main.py --egx-one-click-paper-cycle
```

**What it does:**

- Loads `data/real/egx_live_snapshot.csv` (or runs one-click update first)
- Checks each open BUY paper trade against live high/low/close
- Closes at take profit when live high or close reaches TP
- Closes at stop loss when live low or close reaches SL (TP checked first)
- Holds otherwise at the current live close

**Paper cycle (`--egx-one-click-paper-cycle`):**

- Updates the EGX live snapshot
- Monitors and closes existing open trades first
- Runs live scan and opens new paper trades from `BUY_SETUP` signals
- Prints both monitor and trading reports

**Important:**

- Paper trading only — no real broker connection
- Monitor commands do not open new trades (except `--egx-one-click-paper-cycle` after monitoring)
- Uses the existing virtual portfolio and trade journal

### Patch 13.1 — EGX Full-Market Filter Reset

Clears sector/index/text filters before reading the EGX stocks table and warns when row counts look too low.

### Patch 14 — EGX Snapshot Validation V2

Hardens snapshot quality checks so partial markets are not treated as full markets silently.

**Changes:**

- Resets the **Traded Stocks** dropdown to the broadest available option
- Validates post-normalization symbol count (warn below 150, critical below 80)
- Persists ingest warnings to `data/real/egx_live_ingest_warnings.json`
- Merges ingest warnings into live scan output and daily report JSON/txt

### Patch 14.1 — EGX Multi-Sector Collector

When the EGX sector dropdown has no reliable **All** option, one-click flows now collect stocks by iterating every sector option and merging the results.

**Changes:**

- Detects the sector dropdown and loops each valid sector option
- Waits for the table refresh, extracts rows, tags them with sector name, merges, and deduplicates by **Name**
- Emits collection warnings: sector count, rows before dedupe, rows after dedupe
- Used automatically in all one-click EGX flows via `run_egx_one_click_snapshot_update`
- Falls back to the previous single visible-table extraction when no sector dropdown is found

### Patch 14.2 — EGX Symbol Mapping V1

EGX public pages expose **company names**, while the watchlist and scanner use **ticker symbols** (e.g. `Commercial International Bank-Egypt (CIB)` → `COMI`).

**Changes:**

- Mapping dictionary in `config/egx_symbol_map.py` (extend with more names over time)
- `core/symbol_mapping.py` maps names to tickers during live snapshot save
- Live snapshot CSV includes `company_name` plus mapped `symbol`
- Pipeline prints: `EGX symbol mapping: mapped X rows, unmapped Y rows.`
- View mappings: `python main.py --show-egx-symbol-mapping`

### Patch 14.2.1 — EGX Updating Overlay Timeout Fix

Handles the EGX **Updating...** overlay that can block multi-sector collection.

**Changes:**

- `wait_for_egx_update_complete()` waits for the overlay to clear, then for a stocks table in the DOM
- On timeout: recovery via Escape + 2s wait, then reads the current table if available
- Stuck sectors are skipped with a warning instead of failing the whole collection
- If multi-sector collection fails entirely, falls back to the currently visible/DOM table
- EGX network/table idle wait increased to 60s
- Workflow navigates to **Stocks > Trading Data**, scrolls to the table area, and detects tables from DOM HTML (not viewport visibility)
- When extraction fails, prints EGX table diagnostics (URL, tabs, DOM table previews, filter values)

### Patch 15 — Live Scanner Parity

Aligns live scanning with historical semantics.

**Changes:**

- Computes real `above_sma_5` from stored live history closes
- Breakout uses `close > previous day high` when history is available
- Volume cold-start sets `insufficient_volume_history`; Strategy B blocks `BUY_SETUP` until enough history exists
- Index mood uses computed SMA5 instead of hardcoded passes

### Patch 16 — Paper State Safety

Safer daily paper operations.

**Changes:**

- `--demo-trade` no longer wipes existing live paper state
- `--reset-paper-state` works on EGX live/one-click paper commands
- Portfolio and journal saves use atomic JSON writes
- `storage/trades.json` is gitignored alongside `portfolio_state.json`
- Offline monitor prints a warning when checking open trades (use live monitor for EGX OHLC exits)

### Patch 17 — CLI Workflow Consolidation

Unified entry point for daily EGX workflows.

**Golden commands (recommended daily use):**

```bash
# Morning read-only report
python main.py --egx-one-click-daily-report

# Full paper session (monitor → scan → open)
python main.py --egx-one-click-paper-cycle

# Intraday monitor only
python main.py --egx-one-click-paper-monitor
```

**Unified workflow flag:**

```bash
python main.py --egx-workflow report
python main.py --egx-workflow scan
python main.py --egx-workflow monitor
python main.py --egx-workflow trade
python main.py --egx-workflow cycle
python main.py --egx-workflow scan --egx-local
python main.py --egx-workflow cycle --reset-paper-state
```

Use `--egx-local` to run against the existing `data/real/egx_live_snapshot.csv` without a Chrome update. Legacy flags (`--egx-one-click-*`, `--egx-live-*`) remain supported as aliases.

### Patch 18 — Shared Paper Engine Refactor

Extracts shared open/close logic into `core/paper_engine.py`. `LivePaperTrader`, `AutoPaperTrader`, and both monitors delegate to the same engine without behavior changes.

**Storage layout:**

- `storage/portfolio_state.json` — cash, positions, all trades (source of truth)
- `storage/trades.json` — journal list (synced on open/close)
- Both files are local-only and gitignored

## Safety Disclaimer

**This is simulation only. All trades are fake/paper trades.**

- Does **not** connect to real brokerage accounts
- Does **not** automate [Thndr](https://thndr.app/) or any broker
- Does **not** use browser cookies, tokens, or credentials
- Does **not** place real orders
- Does **not** scrape live market data
- Uses **local CSV data only** — demo fixtures or user-provided real files under `data/real/`

`PAPER_TRADING_ONLY` is enforced in code and must remain `True`.

## Requirements

- Python 3.11+
- Dependencies: `pandas`, `pydantic`, `pytest`

## Setup

```bash
cd egx_smart_trading_coach
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

python -m pip install -r requirements.txt
python -m playwright install chromium
```

## Windows / Git Bash setup

On Windows, `pytest -v` may fail in Git Bash / MINGW64 with:

```text
bash: pytest: command not found
```

This happens because the `pytest` executable is not always on your shell `PATH`, even when the package is installed in `.venv`.

### Quick setup scripts

**Git Bash / MINGW64:**

```bash
cd egx_smart_trading_coach
bash scripts/setup_gitbash.sh
```

**Windows Command Prompt / PowerShell:**

```bat
cd egx_smart_trading_coach
scripts\setup_windows.bat
```

### Activate the virtual environment in Git Bash

```bash
source .venv/Scripts/activate
```

### Run commands the reliable way

Use `python -m pytest -v` instead of `pytest -v`:

```bash
python main.py
python -m pytest -v
```

If tests still fail, reinstall dependencies:

```bash
python -m pip install -r requirements.txt
```

### Windows shortcut scripts

From the project folder:

```bat
scripts\run_demo_windows.bat
scripts\run_tests_windows.bat
```

## Run the Demo

Analysis only (default):

```bash
python main.py
python main.py --scenario bull
python main.py --scenario mixed
python main.py --scenario weak
```

Optional hardcoded COMI paper-trade demo:

```bash
python main.py --scenario bull --demo-trade
```

Auto paper trading from Strategy Scanner B:

```bash
python main.py --scenario bull --auto-paper-trade
python main.py --scenario bull --reset-paper-state --auto-paper-trade
```

Monitor and close open paper trades:

```bash
python main.py --scenario bull --monitor-paper-trades
python main.py --scenario bull --monitor-paper-trades --force-eod-exit
python main.py --scenario bull --reset-paper-state --auto-paper-trade --monitor-paper-trades
```

Backtest simulation (runs analysis first, then backtest):

```bash
python main.py --scenario bull --backtest
python main.py --scenario mixed --backtest
python main.py --scenario weak --backtest
```

This prints market snapshot, mood, Scanner A, and Strategy Scanner B. Use `--auto-paper-trade` to open paper trades, `--monitor-paper-trades` to review exits, `--demo-trade` for the separate COMI example, or `--backtest` for historical simulation.

## Run Tests

```bash
python -m pytest -v
```

Do not rely on `pytest -v` alone in Git Bash on Windows — use `python -m pytest -v` instead.

## Project Structure

```
egx_smart_trading_coach/
├── README.md
├── requirements.txt
├── pytest.ini
├── main.py
├── scripts/
│   ├── setup_windows.bat
│   ├── setup_gitbash.sh
│   ├── run_demo_windows.bat
│   └── run_tests_windows.bat
├── config/
│   ├── settings.py
│   └── watchlist.py
├── core/
│   ├── models.py
│   ├── portfolio.py
│   ├── trade_journal.py
│   ├── risk.py
│   ├── market_data.py
│   ├── market_mood.py
│   ├── scanner.py
│   ├── strategy.py
│   ├── paper_trader.py
│   ├── paper_monitor.py
│   ├── backtester.py
│   ├── data_import.py
│   ├── data_downloader.py
│   └── egx_public_reader.py
├── data/
│   ├── sample_prices.csv
│   ├── egx_daily_sample.csv
│   ├── samples/
│   │   ├── README.md
│   │   ├── egx_bull_sample.csv
│   │   ├── egx_mixed_sample.csv
│   │   └── egx_weak_sample.csv
│   ├── real/
│   │   └── README.md
│   └── downloads/
│       ├── README.md
│       └── egx_public/
├── storage/
│   ├── trades.json
│   └── portfolio_state.json
└── tests/
    ├── test_portfolio.py
    ├── test_market_data.py
    ├── test_scanner.py
    ├── test_scenarios.py
    ├── test_strategy.py
    ├── test_main_cli.py
    ├── test_paper_trader.py
    ├── test_paper_monitor.py
    ├── test_backtester.py
    ├── test_data_import.py
    ├── test_daily_importer.py
    ├── test_data_downloader.py
    └── test_egx_public_reader.py
```

## Roadmap (Future Patches)

- News/disclosures reader
- Telegram alerts
- AI trade review

## License

For personal educational and simulation use.
