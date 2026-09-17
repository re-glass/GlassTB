# Code Review: live_bot.py

## VERDICT: CRITICAL BUG FOUND — NOT SAFE FOR LIVE TRADING

The code will NOT work correctly in live trading. The main loop has no position or order tracking. Here's the full review:

---

## CRITICAL ISSUES (Must Fix Before Live Trading)

### 1. No Position Tracking (CRITICAL)
The backtest tracks `open_positions` and checks SL/TP every tick. The main loop does NOT. Once an order is placed, it's never tracked.

**Impact:** Unmanaged positions. No SL/TP monitoring. Multiple entries on same ticker.

**Fix:** Track open positions and poll for exits.

### 2. Daily Loss Limit is Hardcoded (Line 483)
```python
if daily_pnl < -100:  # ← hardcoded, ignores account size
```
Should be:
```python
daily_loss_limit = -(account_value * max_daily_loss_pct)
if daily_pnl < daily_loss_limit:
```

### 3. Order Management Missing
The bot places MARKET orders but never checks if they filled, never sets SL/TP orders, and never monitors positions for exit conditions.

**Impact:** SL/TP exist only in the signal display — they're NOT actual orders.

### 4. No Position-Aware Signal Blocking
The bot will keep generating signals for a ticker it's already in, because it doesn't check existing positions.

---

## SAFETY ISSUES

### 5. Market Orders Only
MARKET orders on 1-minute scalping of FAANG stocks can get bad fills during volatility. Consider LIMIT orders near bid/ask.

### 6. Account Info Called Every Tick
`api.get_account_info()` is called for every ticker every 30 seconds. That's 10 API calls per minute just for account info — could hit rate limits.

---

## WHAT'S CORRECT

- ✅ Strategy logic (indicators, signals) — matches backtest
- ✅ Paper trading gate — orders blocked when `paper_trading: True`
- ✅ OAuth2 flow — tokens saved/loaded/refreshed
- ✅ Error handling on API calls
- ✅ 2% risk per trade calculation
- ✅ Max 2 positions cap (but not enforced — see #1)
- ✅ .gitignore excludes tokens.json and venv

---

## RECOMMENDED ACTION

**DO NOT set `paper_trading: False` until the main loop is rewritten to track positions and manage orders.**

The current code is safe in paper trading mode (no real orders), but it's NOT ready for live trading. The signal generation works, but order management is missing entirely.
