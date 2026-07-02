# EGX Smart Trading Coach V1 — Final Project Report

**Status:** V1 complete  
**Audience:** Project owner and future Cursor chats  
**Last updated:** July 2026

---

## 1. Project Overview

**EGX Smart Trading Coach** is a Python-based market analysis and paper-trading coach for the Egyptian Exchange (EGX). It reads public market data, scores symbols, builds a daily report, and can run simulated (paper) trades — without connecting to a broker or placing real orders.

The system is designed as a **decision-support coach**, not an execution bot. It helps you scan the market, rank candidates, check technical and fundamental context, and review strategy-style entry ideas before you make your own trading decisions.

---

## 2. Core Safety Constraints

These rules are fixed for V1 and must not be broken in normal use:

| Constraint | Meaning |
|---|---|
| **Public market data only** | Data comes from public sources (TradingView screener, EGX public pages). No paid broker feeds required. |
| **Paper trading only** | Trades are simulated and stored locally. No real money moves. |
| **No Thndr** | No Thndr integration. |
| **No broker APIs** | No broker login, order API, or account API. |
| **No real buy/sell execution** | The bot never sends live orders. |
| **No credentials, tokens, or cookies** | No secrets stored for trading accounts. Chrome-based EGX reading uses a local browser session only when you choose that path. |

---

## 3. Final Data Provider Stack

### Primary: TradingView Screener

TradingView is the **recommended primary** data source for V1. It provides a broad Egypt market snapshot with price, volume, technical fields, fundamentals, and sector data in one fetch.

Normalized output is saved to:

`data/real/egx_live_snapshot.csv`

### Fallback: EGX Chrome reader

The original EGX public-page reader (via Chrome) remains available as a fallback when TradingView is unavailable or when you explicitly choose EGX mode.

### CLI provider flags

| Flag | Behavior |
|---|---|
| `--data-provider tradingview` | Use TradingView screener (recommended). |
| `--data-provider egx` | Use EGX public reader path. |
| `--data-provider auto` | Try TradingView first; fall back to EGX if the TradingView snapshot is not usable. |

---

## 4. Completed Patch Summary

### Patch 18 — TradingView Data Provider

- TradingView Screener provider added.
- EGX symbols normalized (e.g. `EGX:COMI` → `COMI`).
- Snapshot saved to `data/real/egx_live_snapshot.csv`.

### Patch 19 — Market Quality Filters

- Minimum price (`--min-price`).
- Minimum volume (`--min-volume`).
- Minimum market cap (`--min-market-cap-quality`).
- Exclude zero volume (`--exclude-zero-volume`).
- Include illiquid override (`--include-illiquid`).

### Patch 19.1 — Report Section Filtering

- **Strongest Movers**, **Volume Leaders**, and **Top Candidates** use the quality-filtered universe in full-market mode.

### Patch 20 — Candidate Ranking

- Smarter tie-breakers for candidate ordering.
- Clean change preferred over extreme spike.
- Volume, relative volume, and market cap used in ranking.

### Patch 20.1 — Volume Leaders Sorting

- **Volume Leaders** sorted by raw volume descending (not volume ratio).

### Patch 21 — Technical Confirmation

- RSI, MACD, EMA/SMA, ADX, TradingView recommendations.
- Technical line shown in **Top Candidates**.

### Patch 22 — Relative Volume Intelligence

- TradingView 10-day relative volume support.
- Labels: HIGH / VERY HIGH / NORMAL / LOW.
- `--min-volume-ratio` can use TradingView relative volume.

### Patch 22.1 — Relative Volume Consistency

- Top Candidates, filters, Strategy Signals, and display all use the same resolved relative volume logic.

### Patch 23 — Sector Momentum

- Sector status: HOT / WARM / NEUTRAL / WEAK.
- Sector scores and report section.
- Sector status added to rank factors.

### Patch 24 — Fundamental Quality

- Market cap, P/E, P/B, dividend yield.
- Fundamentals line in candidates.
- Optional candidate filters (`--max-pe`, `--max-pb`, `--require-fundamentals`, etc.).

### Patch 24.1 — Expensive P/E Fix

- Expensive P/E caps fundamental status at CAUTION (not OK/STRONG).

### Patch 25 — Multi-Timeframe Entry Check

- 1H and 15m timing checks.
- Entry Timing: READY / WATCH / WAIT / AVOID.
- Strategy Signals include timing status.

### Patch 25.1 — Multi-Timeframe CLI Alias

- `--disable-multi-timeframe` added as a convenient off switch.

### Patch 26 — TradingView Query Prefilters

- Optional query-level prefilter before full normalization.
- `--enable-tv-prefilter` for faster, cleaner TradingView fetch.
- Falls back to unfiltered fetch if prefilter returns too few rows.

### Patch 26.1 — Watchlist Repair

- Preserves watchlist symbols excluded by query prefilter.
- Repairs symbols like **SWDY** and **ORAS** so the Watch List stays informative.

### Patch 27 — Market Breadth Mood

- When EGX30/EGX70 index rows are missing (common with TradingView), mood is calculated from stock breadth instead of forcing NEUTRAL only.
- Report shows advancers ratio, average/median change, and average relative volume.
- Index-based mood still used when EGX30/EGX70 rows exist.

---

## 5. Current Main Report Features

A full daily report (`--egx-workflow report`) includes these sections:

1. **Summary** — snapshot date, provider, universe, symbol counts.
2. **Candidate Filters** — active filter thresholds and how many candidates passed.
3. **Candidate Ranking** — ranking factors and sort logic summary.
4. **Market Quality Filters** — how many symbols passed min price/volume/cap filters.
5. **TradingView Query Prefilter** — prefilter used/fallback, rows fetched, watchlist repair.
6. **Market Mood** — mood score and context (index-based or breadth-based).
7. **Sector Momentum** — hottest sectors and sector scores.
8. **Top Candidates** — best scanner candidates after filters and ranking.
9. **Strategy Signals** — paper-trade-style setups with entry, stop, target, timing.
10. **Strongest Movers** — biggest % change (quality-filtered universe).
11. **Volume Leaders** — highest raw volume (quality-filtered universe).
12. **Watch List** — configured watchlist symbols (informational).
13. **Blocked Summary** — symbols blocked and why.
14. **Warnings** — data quality, history, and provider notes.

Reports are saved under `data/reports/` as `.txt` and `.json`.

---

## 6. Meaning of Main Sections

| Section | What it means |
|---|---|
| **Top Candidates** | Best opportunities after quality filters, scanner scoring, and ranking. These are the main ideas to review first. |
| **Strategy Signals** | Paper-trade-ready watch entries with suggested entry, stop, target, confidence, and entry timing. Still simulated — not automatic real trades. |
| **Watch List** | Monitored important symbols only. Informational diagnostics — **not** automatic buy signals. Symbols can appear here even if filtered out of Top Candidates. |
| **Strongest Movers** | Biggest price change % only. Can include volatile names; does not mean “buy”. |
| **Volume Leaders** | Highest raw trading volume only. Activity leader board, not a buy list. |
| **Sector Momentum** | Which sectors are hot or weak today. Helps context for candidates. |
| **Fundamentals** | Company quality context (cap, P/E, P/B, yield). Shown on candidates when data exists. |
| **Entry Timing** | Short timeframe (1H / 15m) entry timing check: READY, WATCH, WAIT, or AVOID. |

---

## 7. Recommended Main Command

Primary daily workflow with TradingView, query prefilter, full market, and sensible quality gates:

```bash
python main.py --egx-workflow report --data-provider tradingview --enable-tv-prefilter --scanner-universe full-market --top-candidates 10 --min-score 75 --min-volume 300000 --min-price 3 --min-volume-ratio 1.5 --max-pb 10
```

This is the intended “production-style” V1 report command for regular use.

---

## 8. Safer / Debug Commands

### Without query prefilter (broader fetch, easier debugging)

```bash
python main.py --egx-workflow report --data-provider tradingview --scanner-universe full-market --top-candidates 10 --min-score 75 --min-volume 300000 --min-price 3
```

### Without multi-timeframe checks (faster, fewer extra fetches)

```bash
python main.py --egx-workflow report --data-provider tradingview --disable-multi-timeframe --scanner-universe full-market --top-candidates 10 --min-score 75 --min-volume 300000 --min-price 3
```

### Auto provider (TradingView with EGX fallback)

```bash
python main.py --egx-workflow report --data-provider auto --scanner-universe full-market --top-candidates 10 --min-score 75
```

---

## 9. Data Storage

| Path | Purpose |
|---|---|
| `data/real/egx_live_snapshot.csv` | Latest normalized live market snapshot |
| `data/real/live_history/` | Saved daily snapshots for volume history / SMA context |
| `data/reports/` | Generated daily reports (`.txt` + `.json`) |
| `storage/portfolio_state.json` | Paper portfolio state |
| `storage/trades.json` | Paper trade journal |

---

## 10. Important Current Warnings

These warnings are often **normal** and do not always mean the run failed:

| Warning | Meaning |
|---|---|
| **EGX30/EGX70 rows unavailable** | TradingView snapshots usually do not include index rows. With TradingView, mood is calculated from **stock breadth** instead. You may see an info message that breadth was used. |
| **Not enough volume history** | Live volume ratio needs several saved history snapshots. Until enough days exist in `data/real/live_history/`, some symbols may show limited volume history. |
| **SMA5 uses current close only** | Without enough live history days, SMA5 alignment may fall back to a simplified check. Accuracy improves as history builds up. |
| **TradingView prefilter fallback** | If query prefilter returns too few symbols, the system fetches the full market without query filters. Watchlist repair (Patch 26.1) still restores configured watchlist symbols. |
| **Partial snapshot** | Very low symbol count may warn that the snapshot is partial. Check provider connectivity or try `--data-provider auto`. |

---

## 11. What Is Real vs Simulated

| Real (from market data) | Simulated / local |
|---|---|
| Prices, volume, change % | Paper trades and portfolio P&L |
| Technical fields (RSI, MACD, etc.) when provided by TradingView | Entry/stop/target suggestions from strategy scanner |
| Fundamental fields when provided by TradingView | Trade journal outcomes |
| Sector labels when available | Fallback defaults for missing optional fields |

**No real money is used.** Paper trading records what the system *would* have done under its rules.

---

## 12. Best Next Optional Improvements

V1 is complete. These are optional future enhancements — not required for V1:

- Portfolio price marking from TradingView (live mark-to-market for open paper positions).
- Paper trading journal performance analytics (win rate, drawdown, R-multiple summaries).
- Daily auto loop (scheduled fetch + report + optional paper monitor).
- End-of-day “best report” summary (one-page executive digest).
- Web UI / dashboard later (read-only report viewer).

---

## 13. Final V1 Status

**EGX Smart Trading Coach V1 is complete.**

The core intelligence stack for V1:

```
TradingView data
  + market quality filters
  + candidate ranking
  + technical confirmation
  + relative volume intelligence
  + sector momentum
  + fundamental quality
  + multi-timeframe entry timing
  + TradingView query prefilters
  + watchlist repair
  + market breadth mood (when index rows missing)
  → daily report + optional paper trading
```

The project is ready for regular manual use via `--egx-workflow report`, with paper trading available as a separate simulated layer. Future work should extend V1 — not replace its safety constraints.

---

## Quick Reference for Future Cursor Chats

- **Do not** add broker APIs, Thndr, or real execution without an explicit new project phase.
- **Do not** change scanner scoring, ranking, or filter semantics unless a numbered patch says so.
- **Primary command:** see Section 7.
- **Tests:** run `pytest` manually when changing core logic; the owner runs verification.
- **This file:** `docs/EGX_SMART_TRADING_COACH_V1_FINAL_REPORT.md`
