#!/usr/bin/env python3
"""
Futures Strategy Comparison
1. Mean Reversion (scalping)
2. Trend Following (breakout/pullback)
3. Swing Trading (multi-day holds)
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# CONFIGURATION
# ============================================================

FUTURES = ['ES=F', 'NQ=F', 'CL=F', 'GC=F', 'SI=F', 'YM=F']
STOCKS = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'TSLA']

MAX_POSITIONS = 2
RISK_PCT = 0.02
MAX_DAILY_LOSS_PCT = 0.05
STARTING_CAPITAL = 1000

# ============================================================
# INDICATORS
# ============================================================

def compute_vwap(df):
    df = df.copy()
    df['_date'] = df.index.date
    df['CumVol'] = df.groupby('_date')['Volume'].cumsum()
    df['TypicalPrice'] = (df['High'] + df['Low'] + df['Close']) / 3
    df['CumVolPrice'] = (df['Volume'] * df['TypicalPrice']).groupby(df['_date']).cumsum()
    df['VWAP'] = df['CumVolPrice'] / df['CumVol']
    return df.drop(columns=['CumVol', 'CumVolPrice', 'TypicalPrice', '_date'])

def compute_bbands(df, period=20, std=2.0):
    df = df.copy()
    df['BB_Mid'] = df['Close'].rolling(period).mean()
    bb_std = df['Close'].rolling(period).std()
    df['BB_Upper'] = df['BB_Mid'] + (std * bb_std)
    df['BB_Lower'] = df['BB_Mid'] - (std * bb_std)
    df['BB_PctB'] = (df['Close'] - df['BB_Lower']) / (df['BB_Upper'] - df['BB_Lower'])
    return df

def compute_rsi(df, period=14):
    df = df.copy()
    delta = df['Close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))
    return df

def compute_atr(df, period=14):
    df = df.copy()
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(period).mean()
    return df

def compute_emas(df, fast=9, slow=21):
    df = df.copy()
    df['EMA_Fast'] = df['Close'].ewm(span=fast).mean()
    df['EMA_Slow'] = df['Close'].ewm(span=slow).mean()
    df['EMA_Trend'] = df['Close'].ewm(span=50).mean()
    return df

# ============================================================
# STRATEGY 1: MEAN REVERSION (Scalping)
# ============================================================

def mean_reversion_signal(row):
    """Mean Reversion: buy dips, sell rips."""
    if pd.isna(row.get('VWAP')) or pd.isna(row.get('RSI')) or pd.isna(row.get('BB_PctB')):
        return 0
    deviation = (row['Close'] - row['VWAP']) / row['VWAP'] * 100
    if deviation < -0.3 and row['RSI'] < 30 and row['BB_PctB'] < 0.1:
        return 1  # Long
    if deviation > 0.3 and row['RSI'] > 65 and row['BB_PctB'] > 0.9:
        return -1  # Short
    return 0

# ============================================================
# STRATEGY 2: TREND FOLLOWING (Breakout/Pullback)
# ============================================================

def trend_following_signal(row):
    """Trend Following: trade in direction of EMA trend on pullbacks."""
    if pd.isna(row.get('EMA_Fast')) or pd.isna(row.get('EMA_Slow')):
        return 0
    
    # Uptrend: Fast EMA > Slow EMA, price > 50 EMA
    uptrend = row['EMA_Fast'] > row['EMA_Slow'] and row['Close'] > row.get('EMA_Trend', row['EMA_Slow'])
    # Downtrend
    downtrend = row['EMA_Fast'] < row['EMA_Slow'] and row['Close'] < row.get('EMA_Trend', row['EMA_Slow'])
    
    # Entry: pullback to fast EMA in direction of trend
    if uptrend and row['Low'] <= row['EMA_Fast'] and row['Close'] >= row['EMA_Fast']:
        return 1  # Long on pullback
    if downtrend and row['High'] >= row['EMA_Fast'] and row['Close'] <= row['EMA_Fast']:
        return -1  # Short on pullback
    
    return 0

# ============================================================
# STRATEGY 3: SWING TRADING (Multi-day holds)
# ============================================================

def swing_signal(row):
    """Swing: hold for 1-5 days, use wider stops, trend + momentum."""
    if pd.isna(row.get('EMA_Fast')) or pd.isna(row.get('RSI')):
        return 0
    
    # Strong uptrend + RSI reset
    if row['EMA_Fast'] > row['EMA_Slow'] and 40 < row['RSI'] < 60:
        return 1
    # Strong downtrend + RSI reset
    if row['EMA_Fast'] < row['EMA_Slow'] and 40 < row['RSI'] < 60:
        return -1
    
    return 0

# ============================================================
# BACKTEST ENGINE
# ============================================================

def run_backtest(df, strategy='mean_reversion', atr_sl=1.0, atr_tp=1.5, capital=STARTING_CAPITAL):
    """Run backtest for any strategy."""
    df.index = df.index.tz_localize(None)
    
    # Compute all indicators
    df = compute_vwap(df)
    df = compute_bbands(df)
    df = compute_rsi(df)
    df = compute_atr(df)
    df = compute_emas(df)
    df = df.dropna()
    
    # Select signal function
    if strategy == 'mean_reversion':
        signal_fn = mean_reversion_signal
    elif strategy == 'trend_following':
        signal_fn = trend_following_signal
    elif strategy == 'swing':
        signal_fn = swing_signal
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
    
    trades = []
    open_positions = []
    daily_pnl = {}
    
    for i in range(1, len(df)):
        row = df.iloc[i]
        timestamp = df.index[i]
        today = timestamp.date()
        
        if today not in daily_pnl:
            daily_pnl[today] = 0.0
        if daily_pnl[today] < -capital * MAX_DAILY_LOSS_PCT:
            continue
        
        # Check exits
        positions_to_remove = []
        for j, pos in enumerate(open_positions):
            # Time-based exit for swing trades
            if strategy == 'swing' and (timestamp - pos['entry_time']).days >= 3:
                exit_price = row['Close']
                if pos['side'] == 'long':
                    pnl = (exit_price - pos['entry']) * pos['qty']
                else:
                    pnl = (pos['entry'] - exit_price) * pos['qty']
                daily_pnl[today] += pnl
                trades.append({'pnl': pnl, 'side': pos['side'], 'exit': 'TIME'})
                positions_to_remove.append(j)
                continue
            
            # SL/TP exits
            if pos['side'] == 'long':
                if row['Low'] <= pos['sl']:
                    pnl = (pos['sl'] - pos['entry']) * pos['qty']
                    daily_pnl[today] += pnl
                    trades.append({'pnl': pnl, 'side': 'long', 'exit': 'SL'})
                    positions_to_remove.append(j)
                elif row['High'] >= pos['tp']:
                    pnl = (pos['tp'] - pos['entry']) * pos['qty']
                    daily_pnl[today] += pnl
                    trades.append({'pnl': pnl, 'side': 'long', 'exit': 'TP'})
                    positions_to_remove.append(j)
            else:
                if row['High'] >= pos['sl']:
                    pnl = (pos['entry'] - pos['sl']) * pos['qty']
                    daily_pnl[today] += pnl
                    trades.append({'pnl': pnl, 'side': 'short', 'exit': 'SL'})
                    positions_to_remove.append(j)
                elif row['Low'] <= pos['tp']:
                    pnl = (pos['entry'] - pos['tp']) * pos['qty']
                    daily_pnl[today] += pnl
                    trades.append({'pnl': pnl, 'side': 'short', 'exit': 'TP'})
                    positions_to_remove.append(j)
        
        for j in sorted(positions_to_remove, reverse=True):
            open_positions.pop(j)
        
        # Market close exit for scalping only
        if strategy != 'swing' and timestamp.hour == 15 and timestamp.minute == 59:
            for pos in open_positions:
                exit_price = row['Close']
                if pos['side'] == 'long':
                    pnl = (exit_price - pos['entry']) * pos['qty']
                else:
                    pnl = (pos['entry'] - exit_price) * pos['qty']
                daily_pnl[today] += pnl
                trades.append({'pnl': pnl, 'side': pos['side'], 'exit': 'MOC'})
            open_positions = []
            continue
        
        if len(open_positions) >= MAX_POSITIONS:
            continue
        
        # Generate signal
        signal = signal_fn(row)
        if signal == 0:
            continue
        
        atr = row['ATR']
        if pd.isna(atr) or atr == 0:
            continue
        
        risk_amount = capital * RISK_PCT
        stop_distance = atr * atr_sl
        if stop_distance == 0:
            continue
        
        qty = max(1, int(risk_amount / stop_distance))
        
        if signal == 1:
            entry_price = row['Close']
            sl = entry_price - stop_distance
            tp = entry_price + (stop_distance * atr_tp / atr_sl)
        else:
            entry_price = row['Close']
            sl = entry_price + stop_distance
            tp = entry_price - (stop_distance * atr_tp / atr_sl)
        
        open_positions.append({
            'side': 'long' if signal == 1 else 'short',
            'entry': entry_price, 'sl': sl, 'tp': tp, 'qty': qty,
            'entry_time': timestamp
        })
    
    return trades, daily_pnl

# ============================================================
# MAIN
# ============================================================

def main():
    capital = STARTING_CAPITAL
    all_tickers = FUTURES + STOCKS
    strategies = ['mean_reversion', 'trend_following', 'swing']
    
    print("=" * 80)
    print("FUTURES & STOCKS — STRATEGY COMPARISON")
    print("=" * 80)
    print(f"Capital: ${capital:,.2f}")
    print(f"Markets: {len(all_tickers)} | Strategies: {len(strategies)}")
    print()
    
    results = []
    
    for strategy in strategies:
        print(f"\n{'=' * 80}")
        print(f"STRATEGY: {strategy.upper()}")
        print(f"{'=' * 80}")
        print(f"{'Ticker':<10} {'Trades':>8} {'Win%':>8} {'P&L':>12} {'PF':>8} {'MaxDD':>10} {'W-Days':>8} {'L-Days':>8}")
        print("-" * 80)
        
        for ticker in all_tickers:
            try:
                # Different periods for different strategies
                if strategy == 'swing':
                    data = yf.download(ticker, period='60d', interval='1h', progress=False)
                else:
                    data = yf.download(ticker, period='14d', interval='5m', progress=False)
                
                if data.empty:
                    continue
                
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                
                trades, daily = run_backtest(data, strategy, 1.5, 2.0, capital)
                
                if not trades:
                    continue
                
                total_trades = len(trades)
                wins = sum(1 for t in trades if t['pnl'] > 0)
                win_rate = wins / total_trades * 100
                total_pnl = sum(t['pnl'] for t in trades)
                
                win_pnl = sum(t['pnl'] for t in trades if t['pnl'] > 0)
                loss_pnl = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
                pf = win_pnl / loss_pnl if loss_pnl > 0 else float('inf')
                
                # Max drawdown
                cumulative = np.cumsum([t['pnl'] for t in trades])
                peak = np.maximum.accumulate(cumulative)
                max_dd = (cumulative - peak).min()
                
                daily_values = list(daily.values())
                w_days = sum(1 for v in daily_values if v > 0)
                l_days = sum(1 for v in daily_values if v < 0)
                
                results.append({
                    'strategy': strategy, 'ticker': ticker,
                    'trades': total_trades, 'win_rate': win_rate,
                    'pnl': total_pnl, 'pf': pf, 'max_dd': max_dd,
                    'w_days': w_days, 'l_days': l_days
                })
                
                print(f"{ticker:<10} {total_trades:>8} {win_rate:>7.1f}% ${total_pnl:>10,.2f} {pf:>8.2f} ${max_dd:>9.2f} {w_days:>8} {l_days:>8}")
                
            except Exception as e:
                pass
    
    # Summary by strategy
    print(f"\n{'=' * 80}")
    print("SUMMARY BY STRATEGY")
    print(f"{'=' * 80}")
    
    for strategy in strategies:
        strat_results = [r for r in results if r['strategy'] == strategy]
        if not strat_results:
            continue
        
        avg_pnl = np.mean([r['pnl'] for r in strat_results])
        avg_pf = np.mean([r['pf'] for r in strat_results if r['pf'] != float('inf')])
        best = max(strat_results, key=lambda x: x['pnl'])
        
        print(f"\n{strategy.upper()}:")
        print(f"  Avg P&L: ${avg_pnl:,.2f} | Avg PF: {avg_pf:.2f}")
        print(f"  Best: {best['ticker']} — P&L: ${best['pnl']:,.2f}, PF: {best['pf']:.2f}")
    
    # Top 5 overall
    print(f"\n{'=' * 80}")
    print("TOP 5 STRATEGY + MARKET COMBINATIONS")
    print(f"{'=' * 80}")
    
    top5 = sorted(results, key=lambda x: x['pnl'], reverse=True)[:5]
    for i, r in enumerate(top5, 1):
        print(f"{i}. {r['strategy']:<18} {r['ticker']:<10} P&L: ${r['pnl']:>10,.2f}  PF: {r['pf']:.2f}  Win%: {r['win_rate']:.1f}%")
    
    print(f"\n{'=' * 80}")

if __name__ == '__main__':
    main()
