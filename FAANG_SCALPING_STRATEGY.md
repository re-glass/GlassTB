# FAANG Mean Reversion Scalping Strategy
## Complete Reference Document
### Last Updated: 2026-09-17

---

## Overview

**GlassTB** — a production-ready automated trading bot with a native cross-platform GUI:
- **Markets:** FAANG stocks and Futures (YM, GC, ES, NQ, CL, SI)
- **Strategies:** Mean Reversion (primary), Trend Following, Swing
- **Paper & Live trading**
- **Native GUI** — Flask + pywebview, dark dashboard, Start/Stop control, trade log
- **Cross-platform** — WebKitGTK (Linux), WebKit (macOS), Edge (Windows)

Run it: `cd GlassTB && ./launch.sh`
- **Safety:** Position persistence, graceful shutdown, risk management, GUI auto-continue on daily loss
- **Visualization:** Native GUI (Flask + pywebview) with live dashboard — Start/Stop bot, trade log, candlestick charts, positions, account overview

---

## Project Structure

```
scalping_bot/
├── trading_bot.py           ← UNIFIED BOT (USE THIS)
├── tui.py                   ← Terminal dashboard (live prices, positions, charts)
├── optimize.py              ← Parameter optimization tool
├── strategy_comparison.py   ← Backtest all strategies on futures + stocks
├── futures_backtest.py      ← Mean reversion backtest on futures
├── enhanced_backtest.py     ← Original FAANG backtest + optimization
├── live_bot_futures.py      ← Older futures-specific version
├── FAANG_SCALPING_STRATEGY.md ← This document
├── README.md                ← Quick start guide
├── requirements.txt         ← Python dependencies
└── positions.json           ← Active position tracking (gitignored)
```

---

## Quick Start

### Install

```bash
cd /home/reg/scalping_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure

Edit `trading_bot.py`, class `Config`:

```python
# For stocks:
TICKERS = ['AAPL', 'GOOGL', 'META', 'AMZN', 'NFLX']

# For futures (default):
TICKERS = ['/YM', '/GC', '/ES', '/NQ', '/CL', '/SI']

# Strategy: 'mean_reversion', 'trend_following', 'swing'
STRATEGY = 'mean_reversion'
```

### Run

```bash
# Interactive TUI — visualizes live data, positions, P&L, sparkline charts
python3 -u tui.py

# Full bot — places trades, enforces risk limits, handles signals
python3 trading_bot.py
```

The TUI reloads `tokens.json` every refresh cycle, so a token refresh from the bot won't stop the dashboard from loading data.

---

## Account & Risk Parameters

| Parameter | Value |
|-----------|-------|
| **Broker** | Schwab (official API, OAuth2) |
| **Account Size** | ~$305 (paper trading) |
| **Pattern Day Trader Rule** | Repealed as of June 4, 2026 |
| **Risk per Trade** | 2% of account |
| **Max Daily Loss** | 5% — triggers an interactive prompt to continue or stop for the day |
| **Max Positions** | 2 concurrent |

---

## Strategy Specifications

### Mean Reversion (Scalping) — Current Default

**Timeframe:** 1-minute bars

**LONG Entry (all conditions must be met):**
1. Price deviation from VWAP < -0.3% (price below VWAP)
2. RSI < 25 (oversold)
3. Bollinger Bands %B < 0.1 (price near lower band)

**SHORT Entry (all conditions must be met):**
1. Price deviation from VWAP > +0.3% (price above VWAP)
2. RSI > 75 (overbought)
3. Bollinger Bands %B > 0.9 (price near upper band)

**Exit Rules:**
- Stop Loss: Entry price ± (1.0 x ATR)
- Take Profit: Entry price ± (2.0 x ATR)
- Market Close: All positions closed at 4:00 PM (stocks only)

### Trend Following (Breakout/Pullback)

**Timeframe:** 5-minute bars

**Logic:**
- Fast EMA > Slow EMA = uptrend
- Enter on pullback to fast EMA in direction of trend
- SL: 1.5x ATR | TP: 2.0x ATR

---

## Optimized Parameters

Found via 1296-combination grid search on 6 futures (14 days of 1-min data):

| Parameter | Original | Optimized |
|-----------|----------|-----------|
| ATR SL | 1.5x | 1.0x |
| ATR TP | 2.0x | 2.0x |
| RSI Oversold | 30 | 25 |
| RSI Overbought | 65 | 75 |
| BB Std Dev | 2.0 | 1.5 |
| VWAP Deviation | 0.3% | 0.3% |

**Results:** PF: 1.39 | P&L: $2,199 | Win%: 40.4% | Max Drawdown: $406

---

## Safety Features

### Graceful Shutdown

On kill (Ctrl+C or `kill` command), the bot:
1. Receives shutdown signal (SIGINT, SIGTERM, or SIGHUP)
2. Closes all open positions at market price
3. Places closing orders (if live trading)
4. Saves final state to `positions.json`
5. Exits cleanly

### Position Persistence

- Every position change saved to `positions.json`
- On restart from same day, positions are restored
- Prevents position loss on crash

### Risk Management

- 2% risk per trade (position sized to ATR)
- 5% daily loss limit with interactive prompt (continue or stop for the day)
- Max 2 concurrent positions
- Duplicate per-ticker blocking
- Market close position closing

### Token Refresh Resilience (TUI)

The TUI re-reads `tokens.json` every refresh cycle and handles a missing token gracefully (shows a warning and retries). This means when the bot refreshes the access token, the TUI keeps loading data without interruption.

---

## API Endpoints (Schwab)

| Category | Endpoint |
|----------|----------|
| OAuth | `https://api.schwabapi.com/v1/oauth/authorize` |
| Market Data | `https://api.schwabapi.com/marketdata/v1/pricehistory` |
| Trader | `https://api.schwabapi.com/trader/v1/accounts/...` |

**Futures Symbol Format:**
- Schwab API: `/YM`, `/GC`, `/ES`, `/NQ`, `/CL`, `/SI`
- yfinance (backtests): `YM=F`, `GC=F`, `ES=F`, `NQ=F`, `CL=F`, `SI=F`

**Account IDs:** `GET /trader/v1/accounts/accountNumbers` returns `hashValue`; use that hash for subsequent calls, not the plain account number.

**Live Price:**
- Stocks: prefer `quote.lastPrice`; fall back to `extended.lastPrice` only if live is 0.
- Futures: Schwab's trader quote endpoints don't expose futures quotes — use the price history API instead.

---

## TUI Dashboard

The TUI (`tui.py`) is a real-time terminal dashboard built with `rich`. It refreshes every 3 seconds and displays:

- **Account Overview** — cash, buying power, liquidation value, daily P&L, total P&L
- **Live Prices** — per ticker with ANSI sparkline charts built from 1-minute candle closes
- **Open Positions** — symbol, side, qty, entry, current price, P&L
- **Status** — last update timestamp, quit hint

Every cycle the TUI persists:
- `positions.json` — positions, daily_pnl, total_pnl
- `current_prices_cache.json` — latest prices + candles
- `historical_cache.json` — full candle history
- `trade_log.json` — trade records (append-only)

Cache files are gitignored. The TUI validates the token on startup and re-reads it each cycle.

---

## Current Performance (Paper Trading)

| Trade | Ticker | Side | Entry | Exit | P&L |
|-------|--------|------|-------|------|-----|
| — | — | — | — | — | — |

Paper trading is ON — no real orders placed. PDT rule no longer applies (repealed June 2026).

---

## Notes

- The bot runs silently — only prints on signals, exits, and status updates
- Status line prints every 30 seconds with live prices
- Paper trading mode is ON — no real orders placed
- PDT rule no longer applies (repealed June 2026)
- Daily loss now prompts interactively instead of silently stopping

---

*Document generated by Hermes Agent on 2026-09-17*
*Strategy is experimental — past backtest results do not guarantee future performance*
*Always paper trade before risking real capital*
