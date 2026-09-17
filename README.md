# GlassTB — Schwab Scalping Bot + Desktop GUI

Automated scalping bot with a native cross-platform GUI. Trades FAANG stocks and futures (/YM, /GC, /ES, /NQ, /CL, /SI) on Charles Schwab via OAuth2.

## Quick Start

```bash
cd /home/reg/GlassTB
./launch.sh
```

This opens the GlassTB window: live prices, candlestick/bar charts, account overview, bot Start/Stop control, and a trade log. Data refreshes every 3 seconds.

## Install (fresh clone)

```bash
git clone https://github.com/re-glass/TradingBot-code.git GlassTB
cd GlassTB
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# Linux only — GTK/WebKit backend for the native window
pip install PyGObject       # requires: sudo apt install libwebkit2gtk-4.0-dev gir1.2-webkit2-4.0 (Debian/Ubuntu)
                            # or:      sudo pacman -Sy webkit2gtk (Arch)
./launch.sh
```

macOS needs no extra deps (uses system WebKit). Windows uses Edge (no extra deps).

## Requirements

- Python 3.9+
- Schwab API app with **MarketData + AccountsAndTrading** scopes (see `FAANG_SCALPING_STRATEGY.md`)
- `tokens.json` with a valid `access_token` (bot + GUI both refresh automatically)
- **Linux only:** system GTK + WebKit2GTK for the native window (see Install above)

## File Layout

| File | Purpose |
|------|---------|
| `launch.sh` | One-command launcher (cd to script dir, exec `venv/bin/python gui/app.py`) |
| `gui/app.py` | Flask + pywebview. Serves dark dashboard on localhost, opens native window. Runs TradingBot in a background thread. |
| `gui/dashboard.html` | Dark btop-style UI. Polls `/api/scene` every 3s, renders panels + Start/Stop button + trade log. |
| `gui/styles.css` | Stylesheet (also inlined in dashboard.html as fallback). |
| `trading_bot.py` | TradingBot class — unified OOP design. Supports `GUI_MODE` for non-blocking auto-continue on daily-loss limit. |
| `tui.py` | Legacy terminal dashboard (optional — GUI is primary now). |
| `optimize.py` | Parameter grid search for futures mean reversion. Writes `optimization_results.json`. |
| `FAANG_SCALPING_STRATEGY.md` | Full strategy reference — entry/exit rules, optimized params, API endpoints, safety features. |
| `requirements.txt` | Python deps. |

Legacy/backtest files also present: `live_bot.py`, `live_bot_futures.py`, `scalping_backtest.py`, `enhanced_backtest.py`, `futures_backtest.py`, `strategy_comparison.py`.

## What the GUI Shows

**Top bar** — `No open positions` (or position count).

**Status bar** — refresh countdown, uptime, quit hint, cursor block.

**Bot control panel** — RUNNING / STOPPED / STOPPING label, PAPER / LIVE mode, START BOT / STOP BOT button, strategy name, error display.

**GLASS TB panel** — Cash, Buying Power, Value, Day PnL (all on one line); Total PnL + timestamp (line 2).

**BID/ASK table** — Symbol, Price, Chart, 7c column. `/GC` and `/ES` render as OHLC candlesticks with wicks; `/YM`, `/NQ`, `/CL`, `/SI` render as horizontal bars. All light blue on dark background. `7c` column shows `—` (placeholder).

**POSITIONS panel** — open positions with ticker, side, qty, entry, P&L. Empty state: centered `No open positions` with dot.

**TRADE LOG panel** — last 50 trades with timestamp, symbol, side, qty, price, reason, P&L.

**Bottom status bar** — identical to top.

## Bot Controls

Click **START BOT** → TradingBot launches in a background thread. The dashboard polls every 3s and updates prices, positions, and trade log automatically.

Click **STOP BOT** → TradingBot finishes its current loop iteration and exits gracefully.

In the GUI, daily-loss limit triggers an automatic continue (limit doubled) instead of a blocking `input()` prompt.

## API Routes (Flask)

| Route | Purpose |
|-------|---------|
| `/` | Serve the JS shell (dashboard.html). Browser loads once. |
| `/api/scene` | Return live dashboard fragment HTML. JS injects into `#root`. |
| `/api/bot/start` | `POST` — start TradingBot in background thread. Returns JSON `{ok, error?}`. |
| `/api/bot/stop` | `POST` — signal TradingBot to stop. Returns JSON `{ok, error?}`. |
| `/api/bot/status` | `GET` — JSON `{running, status, strategy, paper, error}`. |

## Configuration

Edit `trading_bot.py`, class `Config`:

```python
# Markets (uncomment one)
# TICKERS = ['AAPL', 'GOOGL', 'META', 'AMZN', 'NFLX']  # Stocks
TICKERS = ['/YM', '/GC', '/ES', '/NQ', '/CL', '/SI']   # Futures (default)

# Strategy
STRATEGY = 'mean_reversion'   # 'mean_reversion' | 'trend_following' | 'swing'

# Safety
PAPER_TRADING = True          # True = no real orders
GUI_MODE = False              # True = auto-continue on daily loss (GUI sets this)
```

Key risk params:

| Param | Value |
|-------|-------|
| Risk per trade | 2% of account |
| Max daily loss | 5% (auto-continue in GUI mode, doubles the limit) |
| Max concurrent positions | 2 |
| Max position size | 10% of account |

## Strategy (mean reversion — default)

Timeframe: 1-minute bars.

**Long entry** — all must be true:
- Price deviation from VWAP < -0.3%
- RSI < 25
- Bollinger Bands %B < 0.1

**Short entry** — all must be true:
- Price deviation from VWAP > +0.3%
- RSI > 75
- Bollinger Bands %B > 0.9

**Exits:**
- Stop loss: entry ± (1.0 × ATR)
- Take profit: entry ± (2.0 × ATR)

## Safety features

- **Signal handlers** for SIGINT, SIGTERM, SIGHUP — graceful shutdown closes all positions. Skipped in GUI mode (only main thread can set signal handlers).
- **Position persistence** — every change written to `positions.json`.
- **Token refresh resilience** — GUI and bot both read `tokens.json` on each cycle.
- **GUI daily-loss handling** — auto-continues (no blocking prompt) when limit is hit.
- **Paper trading** — `PAPER_TRADING = True` prints `[PAPER TRADE]` and submits no real orders.

## API notes (Schwab)

| Category | Base URL |
|----------|----------|
| OAuth | `https://api.schwabapi.com/v1/...` |
| Trader (accounts, orders) | `https://api.schwabapi.com/trader/v1/...` |
| Market data (price history) | `https://api.schwabapi.com/marketdata/v1/...` |

- Account IDs: `GET /trader/v1/accounts/accountNumbers` returns `hashValue`; use that hash for subsequent calls.
- Futures symbols: Schwab uses `/YM`, `/GC`, etc. for price history. For yfinance backtests use `YM=F`, `GC=F`, etc.
- Live stock price: prefer `quote.lastPrice`; fall back to `extended.lastPrice` only if live is 0.
- Futures live data: use the price history API (Schwab trader quote endpoints don't expose futures quotes).

## Trading PDT Note

FINRA PDT rule was repealed June 2026. The old $25k minimum and 3-per-5-day limit no longer apply. Brokers have until October 20, 2027 to implement new intraday margin standards.

## Repo

`https://github.com/re-glass/GlassTB` — GlassTB (bot + GUI), backtest/analysis files.

Agent files live at `https://github.com/re-glass/glass-agent` (Hermes skills, memories, config).

## Disclaimer

Experimental software. Past backtest results do not guarantee future performance. Always paper trade before risking real capital.
