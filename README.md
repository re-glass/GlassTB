# Trading Bot — Schwab API, FAANG + Futures

Automated scalping bot connecting to Charles Schwab via OAuth2. Supports stocks (FAANG) and futures (YM, GC, ES, NQ, CL, SI). Two strategies: mean reversion (primary) and trend following.

## Quick Start

```bash
cd /home/reg/scalping_bot
source venv/bin/activate

# Interactive TUI — live prices, positions, sparkline charts
python3 -u tui.py

# Full bot — places trades, enforces risk limits, handles signals
python3 trading_bot.py
```

## Requirements

- Python 3.9+
- Schwab API app with MarketData + AccountsAndTrading scopes (see `FAANG_SCALPING_STRATEGY.md`)
- `tokens.json` with a valid `access_token` (bot refreshes automatically; TUI reloads each cycle)

Install deps:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## What's in the box

| File | Purpose |
|------|---------|
| `trading_bot.py` | Main bot — unified OOP design with `SchwabAPI`, `RiskManager`, `StrategyEngine`, `TradeManager`, `MarketDataManager`, `Config`. Trades stocks or futures in paper or live mode. |
| `tui.py` | Terminal dashboard — live prices per ticker, ANSI sparkline charts from candle history, open positions with P&L, account overview (cash, buying power, daily/total P&L). Persists `positions.json`, cache files, and trade log each refresh cycle. Reloads `tokens.json` every cycle so bot token refreshes don't break it. |
| `optimize.py` | Parameter grid search over ATR multiplier, RSI thresholds, BB std dev, VWAP deviation for futures mean reversion. Writes `optimization_results.json`. |
| `FAANG_SCALPING_STRATEGY.md` | Full strategy reference — entry/exit rules, optimized parameters, API endpoints, safety features. |
| `requirements.txt` | Python deps (requests, pandas, numpy, yfinance, rich, textual). |

Legacy/backtest files also present: `live_bot.py`, `live_bot_futures.py`, `scalping_backtest.py`, `enhanced_backtest.py`, `futures_backtest.py`, `strategy_comparison.py`.

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
```

Key risk params:

| Param | Value |
|-------|-------|
| Risk per trade | 2% of account |
| Max daily loss | 5% (interactive prompt — choose continue or stop for the day) |
| Max concurrent positions | 2 |
| Max position size | 10% of account |

## Strategy (mean reversion — current default)

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
- All positions closed at market close (stocks)

Optimized parameters are baked into `trading_bot.py`.

## Safety features

- **Signal handlers** for SIGINT, SIGTERM, SIGHUP — graceful shutdown closes all positions and saves state.
- **Position persistence** — every change written to `positions.json`; on restart from the same day, positions are restored.
- **Token refresh resilience** — TUI re-reads `tokens.json` each cycle so a bot-triggered refresh doesn't stop the dashboard.
- **Daily loss prompt** — when the 5% limit is hit, the bot asks "Continue trading? (y/n)" instead of silently stopping or ignoring the limit. `y` doubles the limit and continues; `n` stops for the day.
- **Paper trading** — when `PAPER_TRADING = True`, every order prints `[PAPER TRADE]` and no real order is submitted.

## API notes (Schwab)

| Category | Base URL |
|----------|----------|
| OAuth | `https://api.schwabapi.com/v1/...` |
| Trader (accounts, orders) | `https://api.schwabapi.com/trader/v1/...` |
| Market data (price history) | `https://api.schwabapi.com/marketdata/v1/...` |

- Account IDs: `GET /trader/v1/accounts/accountNumbers` returns `hashValue`; use that hash for subsequent calls, not the plain account number.
- Futures symbols: Schwab uses `/YM`, `/GC`, `/ES`, `/NQ`, `/CL`, `/SI` for the price history API. For yfinance backtests use `YM=F`, `GC=F`, etc.
- Live stock price: prefer `quote.lastPrice`; fall back to `extended.lastPrice` only if live is 0.
- Futures live data: Schwab's trader quote endpoints don't expose futures quotes — use the price history API (`get_latest_price` / `get_price_history`) instead.

## Repos

Two GitHub repos keep things separated:

- `https://github.com/re-glass/TradingBot-code` — trading bot files only.
- `https://github.com/re-glass/glass-agent` — Hermes agent files only (skills, memories, config).

## Disclaimer

Experimental software. Past backtest results do not guarantee future performance. Always paper trade before risking real capital.
