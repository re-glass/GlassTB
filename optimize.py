#!/usr/bin/env python3
"""
Parameter Optimization for Futures Mean Reversion
Tests different ATR, RSI, and VWAP settings to find the best strategy
"""

import pandas as pd
import numpy as np
import yfinance as yf
from itertools import product
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# CONFIGURATION
# ============================================================
# Futures tickers for yfinance (different from Schwab format)
FUTURES = ['ES=F', 'NQ=F', 'YM=F', 'CL=F', 'GC=F', 'SI=F']
CAPITAL = 1000
RISK_PCT = 0.02
MAX_DAILY_LOSS = 0.05

# Parameter grid
PARAM_GRID = {
    'atr_sl': [0.8, 1.0, 1.2, 1.5],
    'atr_tp': [1.2, 1.5, 1.8, 2.0],
    'rsi_os': [25, 30, 35],
    'rsi_ob': [65, 70, 75],
    'vwap_dev': [0.2, 0.3, 0.4],
    'bb_std': [1.5, 2.0, 2.5],
}

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

def mean_reversion_signal(row, rsi_os, rsi_ob, vwap_dev):
    if pd.isna(row.get('VWAP')) or pd.isna(row.get('RSI')) or pd.isna(row.get('BB_PctB')):
        return 0
    deviation = (row['Close'] - row['VWAP']) / row['VWAP'] * 100
    if deviation < -vwap_dev and row['RSI'] < rsi_os and row['BB_PctB'] < 0.1:
        return 1
    if deviation > vwap_dev and row['RSI'] > rsi_ob and row['BB_PctB'] > 0.9:
        return -1
    return 0

# ============================================================
# BACKTEST ENGINE
# ============================================================

def run_backtest(df, atr_sl, atr_tp, rsi_os, rsi_ob, vwap_dev, bb_std):
    df.index = df.index.tz_localize(None)
    df = compute_vwap(df)
    df = compute_bbands(df, std=bb_std)
    df = compute_rsi(df)
    df = compute_atr(df)
    df = df.dropna()
    
    trades = []
    open_positions = []
    daily_pnl = {}
    
    for i in range(1, len(df)):
        row = df.iloc[i]
        timestamp = df.index[i]
        today = timestamp.date()
        
        if today not in daily_pnl:
            daily_pnl[today] = 0.0
        if daily_pnl[today] < -CAPITAL * MAX_DAILY_LOSS:
            continue
        
        positions_to_remove = []
        for j, pos in enumerate(open_positions):
            if pos['side'] == 'long':
                if row['Low'] <= pos['sl']:
                    pnl = (pos['sl'] - pos['entry']) * pos['qty']
                    daily_pnl[today] += pnl
                    trades.append({'pnl': pnl, 'exit': 'SL'})
                    positions_to_remove.append(j)
                elif row['High'] >= pos['tp']:
                    pnl = (pos['tp'] - pos['entry']) * pos['qty']
                    daily_pnl[today] += pnl
                    trades.append({'pnl': pnl, 'exit': 'TP'})
                    positions_to_remove.append(j)
            else:
                if row['High'] >= pos['sl']:
                    pnl = (pos['entry'] - pos['sl']) * pos['qty']
                    daily_pnl[today] += pnl
                    trades.append({'pnl': pnl, 'exit': 'SL'})
                    positions_to_remove.append(j)
                elif row['Low'] <= pos['tp']:
                    pnl = (pos['entry'] - pos['tp']) * pos['qty']
                    daily_pnl[today] += pnl
                    trades.append({'pnl': pnl, 'exit': 'TP'})
                    positions_to_remove.append(j)
        
        for j in sorted(positions_to_remove, reverse=True):
            open_positions.pop(j)
        
        if timestamp.hour == 15 and timestamp.minute == 59:
            for pos in open_positions:
                exit_price = row['Close']
                pnl = (exit_price - pos['entry']) * pos['qty'] if pos['side'] == 'long' else (pos['entry'] - exit_price) * pos['qty']
                daily_pnl[today] += pnl
                trades.append({'pnl': pnl, 'exit': 'MOC'})
            open_positions = []
            continue
        
        if len(open_positions) >= 2:
            continue
        
        signal = mean_reversion_signal(row, rsi_os, rsi_ob, vwap_dev)
        if signal == 0:
            continue
        
        atr = row['ATR']
        if pd.isna(atr) or atr == 0:
            continue
        
        risk_amount = CAPITAL * RISK_PCT
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
            'entry': entry_price, 'sl': sl, 'tp': tp, 'qty': qty
        })
    
    return trades, daily_pnl

# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 80)
    print("PARAMETER OPTIMIZATION — FUTURES MEAN REVERSION")
    print("=" * 80)
    
    # Fetch data once
    data_cache = {}
    for ticker in FUTURES:
        try:
            data = yf.download(ticker, period='7d', interval='1m', progress=False)
            if not data.empty:
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                data_cache[ticker] = data
                print(f"Fetched {ticker}: {len(data)} bars")
        except Exception as e:
            print(f"Failed to fetch {ticker}: {e}")
    
    if not data_cache:
        print("No data available!")
        return
    
    # Generate parameter combinations
    param_combos = list(product(
        PARAM_GRID['atr_sl'],
        PARAM_GRID['atr_tp'],
        PARAM_GRID['rsi_os'],
        PARAM_GRID['rsi_ob'],
        PARAM_GRID['vwap_dev'],
        PARAM_GRID['bb_std'],
    ))
    
    print(f"\nTesting {len(param_combos)} parameter combinations...")
    print(f"Across {len(data_cache)} tickers\n")
    
    results = []
    
    for idx, (atr_sl, atr_tp, rsi_os, rsi_ob, vwap_dev, bb_std) in enumerate(param_combos):
        all_trades = []
        all_daily = {}
        
        for ticker, data in data_cache.items():
            trades, daily = run_backtest(data, atr_sl, atr_tp, rsi_os, rsi_ob, vwap_dev, bb_std)
            all_trades.extend(trades)
            all_daily.update(daily)
        
        if not all_trades:
            continue
        
        total_trades = len(all_trades)
        wins = sum(1 for t in all_trades if t['pnl'] > 0)
        win_rate = wins / total_trades * 100
        total_pnl = sum(t['pnl'] for t in all_trades)
        
        win_pnl = sum(t['pnl'] for t in all_trades if t['pnl'] > 0)
        loss_pnl = abs(sum(t['pnl'] for t in all_trades if t['pnl'] < 0))
        profit_factor = win_pnl / loss_pnl if loss_pnl > 0 else float('inf')
        
        daily_values = list(all_daily.values())
        w_days = sum(1 for v in daily_values if v > 0)
        l_days = sum(1 for v in daily_values if v < 0)
        
        # Max drawdown
        cumulative = np.cumsum([t['pnl'] for t in all_trades])
        peak = np.maximum.accumulate(cumulative)
        max_dd = (cumulative - peak).min()
        
        results.append({
            'atr_sl': atr_sl, 'atr_tp': atr_tp,
            'rsi_os': rsi_os, 'rsi_ob': rsi_ob,
            'vwap_dev': vwap_dev, 'bb_std': bb_std,
            'trades': total_trades, 'win_rate': win_rate,
            'pnl': total_pnl, 'pf': profit_factor,
            'max_dd': max_dd, 'w_days': w_days, 'l_days': l_days
        })
        
        if (idx + 1) % 50 == 0:
            print(f"  {idx + 1}/{len(param_combos)} combinations tested...")
    
    # Sort by profit factor (most robust metric)
    results.sort(key=lambda x: x['pf'], reverse=True)
    
    # Top 10
    print(f"\n{'=' * 80}")
    print("TOP 10 PARAMETER COMBINATIONS (by Profit Factor)")
    print(f"{'=' * 80}")
    print(f"{'ATR SL':<8} {'ATR TP':<8} {'RSI OS':<8} {'RSI OB':<8} {'VWAP':<8} {'BB':<6} {'Trades':>8} {'Win%':>8} {'P&L':>12} {'PF':>8} {'MaxDD':>10}")
    print("-" * 80)
    
    for r in results[:10]:
        print(f"{r['atr_sl']:<8.1f} {r['atr_tp']:<8.1f} {r['rsi_os']:<8.0f} {r['rsi_ob']:<8.0f} {r['vwap_dev']:<8.1f} {r['bb_std']:<6.1f} {r['trades']:>8} {r['win_rate']:>7.1f}% ${r['pnl']:>10,.2f} {r['pf']:>8.2f} ${r['max_dd']:>9.2f}")
    
    # Best by P&L
    best_pnl = max(results, key=lambda x: x['pnl'])
    print(f"\nBEST BY P&L:")
    print(f"  ATR SL={best_pnl['atr_sl']}, TP={best_pnl['atr_tp']}, RSI={best_pnl['rsi_os']}/{best_pnl['rsi_ob']}, VWAP={best_pnl['vwap_dev']}, BB={best_pnl['bb_std']}")
    print(f"  P&L: ${best_pnl['pnl']:,.2f} | PF: {best_pnl['pf']:.2f} | Win%: {best_pnl['win_rate']:.1f}%")
    
    # Best by Profit Factor
    best_pf = results[0]
    print(f"\nBEST BY PROFIT FACTOR:")
    print(f"  ATR SL={best_pf['atr_sl']}, TP={best_pf['atr_tp']}, RSI={best_pf['rsi_os']}/{best_pf['rsi_ob']}, VWAP={best_pf['vwap_dev']}, BB={best_pf['bb_std']}")
    print(f"  P&L: ${best_pf['pnl']:,.2f} | PF: {best_pf['pf']:.2f} | Win%: {best_pf['win_rate']:.1f}%")
    
    # Save results
    import json
    with open('optimization_results.json', 'w') as f:
        json.dump(results[:20], f, indent=2)
    print(f"\nTop 20 results saved to optimization_results.json")

if __name__ == '__main__':
    main()
