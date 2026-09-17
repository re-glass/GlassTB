# GlassTB — Code Review

## VERDICT: INTEGRATED — BOT + GUI IN ONE APP

GlassTB combines the TradingBot and GUI into a single native application. The critical issues noted in the original review of `live_bot.py` (legacy) have been resolved in the unified `trading_bot.py`.

---

## RESOLVED FROM ORIGINAL REVIEW

### 1. Position Tracking (FIXED)
`trading_bot.py` tracks `paper_positions` dict and monitors for SL/TP exits every loop iteration. `_monitor_positions()` checks SL/TP and closes positions when hit.

### 2. Daily Loss Limit (FIXED)
Now uses `-(account_value * config.MAX_DAILY_LOSS)` — not hardcoded. GUI mode auto-continues; CLI mode prompts.

### 3. Order Management (FIXED)
Paper mode tracks positions in memory with full SL/TP. Live mode would use Schwab's actual orders (PAPER_TRADING default True).

### 4. Position-Aware Signal Blocking (FIXED)
`_process_ticker()` skips any ticker already in `paper_positions`. `_check_max_positions()` enforces `MAX_POSITIONS = 2`.

### 5. Account API Calls (FIXED)
Account info fetched once at startup, not per-ticker per-loop.

---

## CURRENT SAFETY (verified)

- ✅ Paper trading gate (`PAPER_TRADING = True` blocks all real orders)
- ✅ OAuth2 with token refresh
- ✅ Signal handlers for graceful shutdown
- ✅ Position persistence (`positions.json`)
- ✅ Daily loss limit with GUI auto-continue
- ✅ GUI mode skips blocking `input()` prompt
- ✅ GUI mode skips signal handlers (thread-safe)
- ✅ Bot runs as daemon thread; clean stop via `_stop_requested`

---

## FILE STATUS

| File | Status |
|------|--------|
| `trading_bot.py` | Active — main bot logic |
| `gui/app.py` | Active — Flask + pywebview server |
| `gui/dashboard.html` | Active — dark btop-style UI |
| `launch.sh` | Active — one-command launcher |
| `tui.py` | Legacy — use GUI instead |
| `live_bot.py`, `live_bot_futures.py` | Legacy — superseded by `trading_bot.py` |
| `scalping_backtest.py`, `enhanced_backtest.py`, `futures_backtest.py` | Legacy — backtest tools only |

---

## KNOWN LIMITATIONS

- No actual order fill tracking for live trading (would need Schwab order status API)
- Trade log in-memory only on GUI side (full log persisted to `trade_log.json`)
- Futures symbols hardcoded; changing markets requires editing `Config`

---

## RECOMMENDED ACTION

Paper trade first via GUI. The bot logic is sound. Verify fills and behavior before going live.
