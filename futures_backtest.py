#!/usr/bin/env python3
"""
Futures Mean Reversion Scalping Strategy
Tests same mean reversion logic on futures markets
More trading hours = more opportunities
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from itertools import product
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# CONFIGURATION
# ============================================================

# Futures tickers (via yfinance)
FUTURES_TICKERS = ['ES=F', 'NQ=F', 'YM=F', 'CL=F', 'GC=F', 'SI=F']

# Alternative: stock indices for comparison
STOCK_INDICES = ['SPY', 'QQQ', 'DIA', 'IWM']

MAX_POSITIONS = 2
RISK_PCT = 0.02
MAX_DAILY_LOSS_PCT = 0.05
STARTING_CAPITAL = 1000

# ============================================================
# INDICATORS (SAME AS FAANG STRATEGY)
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

def mean_reversion_signal(row, rsi_oversold=30, rsi_overbought=65):
    if pd.isna(row['VWAP']) or pd.isna(row['RSI']) or pd.isna(row['BB_PctB']) or pd.isna(row['ATR']):
        return 0
    deviation = (row['Close'] - row['VWAP']) / row['VWAP'] * 100
    if deviation < -0.3 and row['RSI'] < rsi_oversold and row['BB_PctB'] < 0.1:
        return 1
    if deviation > 0.3 and row['RSI'] > rsi_overbought and row['BB_PctB'] > 0.9:
        return -1
    return 0

# ============================================================
# BACKTEST ENGINE (SAME AS FAANG)
# ============================================================

def run_backtest(df, capital, atr_mult_sl, atr_mult_tp, rsi_oversold, rsi_overbought):
    df.index = df.index.tz_localize(None)
    df = compute_vwap(df)
    df = compute_bbands(df)
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
        if daily_pnl[today] < -capital * MAX_DAILY_LOSS_PCT:
            continue
        
        positions_to_remove = []
        for j, pos in enumerate(open_positions):
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
        
        if timestamp.hour == 15 and timestamp.minute == 59:
            for pos in open_positions:
                exit_price = row['Close']
                pnl = (exit_price - pos['entry']) * pos['qty'] if pos['side'] == 'long' else (pos['entry'] - exit_price) * pos['qty']
                daily_pnl[today] += pnl
                trades.append({'pnl': pnl, 'side': pos['side'], 'exit': 'MOC'})
            open_positions = []
            continue
        
        if len(open_positions) >= MAX_POSITIONS:
            continue
        
        signal = mean_reversion_signal(row, rsi_oversold, rsi_overbought)
        if signal == 0:
            continue
        
        atr = row['ATR']
        if pd.isna(atr) or atr == 0:
            continue
        
        risk_amount = capital * RISK_PCT
        stop_distance = atr * atr_mult_sl
        if stop_distance == 0:
            continue
        qty = max(1, int(risk_amount / stop_distance))
        
        if signal == 1:
            entry_price = row['Close']
            sl = entry_price - stop_distance
            tp = entry_price + (stop_distance * atr_mult_tp / atr_mult_sl)
        else:
            entry_price = row['Close']
            sl = entry_price + stop_distance
            tp = entry_price - (stop_distance * atr_mult_tp / atr_mult_sl)
        
        open_positions.append({
            'side': 'long' if signal == 1 else 'short',
            'entry': entry_price, 'sl': sl, 'tp': tp, 'qty': qty
        })
    
    return trades, daily_pnl

# ============================================================
# MAIN
# ============================================================

def main():
    capital = STARTING_CAPITAL
    
    print("=" * 70)
    print("FUTURES MEAN REVERSION STRATEGY BACKTEST")
    print("=" * 70)
    print(f"Capital: ${capital:,.2f}")
    print(f"Risk per Trade: {RISK_PCT*100}%")
    print(f"Max Daily Loss: {MAX_DAILY_LOSS_PCT*100}%")
    print()
    
    # Test both futures and stock indices
    all_tickers = FUTURES_TICKERS + STOCK_INDICES
    
    all_results = []
    
    for ticker in all_tickers:
        print(f"Testing {ticker}...")
        try:
            data = yf.download(ticker, period='7d', interval='1m', progress=False)
            if data.empty:
                print(f"  No data available")
                continue
            
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
            
            # Run with optimized parameters
            trades, daily = run_backtest(data, capital, 1.0, 1.5, 30, 65)
            
            if not trades:
                print(f"  No trades generated")
                continue
            
            total_trades = len(trades)
            wins = sum(1 for t in trades if t['pnl'] > 0)
            losses = sum(1 for t in trades if t['pnl'] < 0)
            win_rate = wins / total_trades * 100 if total_trades > 0 else 0
            total_pnl = sum(t['pnl'] for t in trades)
            
            win_pnl = sum(t['pnl'] for t in trades if t['pnl'] > 0)
            loss_pnl = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
            profit_factor = win_pnl / loss_pnl if loss_pnl > 0 else float('inf')
            
            daily_values = list(daily.values())
            daily_win_days = sum(1 for v in daily_values if v > 0)
            daily_loss_days = sum(1 for v in daily_values if v < 0)
            
            # Trading hours analysis
            trading_hours = len(data) / 390  # 390 = minutes in a stock day
            
            all_results.append({
                'ticker': ticker,
                'type': 'Futures' if ticker in FUTURES_TICKERS else 'Stock/ETF',
                'trades': total_trades,
                'win_rate': win_rate,
                'pnl': total_pnl,
                'profit_factor': profit_factor,
                'win_days': daily_win_days,
                'loss_days': daily_loss_days,
                'approx_hours': trading_hours
            })
            
            print(f"  Trades: {total_trades}, Win Rate: {win_rate:.1f}%, P&L: ${total_pnl:,.2f}, PF: {profit_factor:.2f}")
            
        except Exception as e:
            print(f"  Error: {e}")
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"{'Ticker':<10} {'Type':<12} {'Trades':>8} {'Win%':>8} {'P&L':>12} {'PF':>8} {'W-Days':>8} {'L-Days':>8}")
    print("-" * 70)
    
    for r in sorted(all_results, key=lambda x: x['pnl'], reverse=True):
        print(f"{r['ticker']:<10} {r['type']:<12} {r['trades']:>8} {r['win_rate']:>7.1f}% ${r['pnl']:>10,.2f} {r['profit_factor']:>8.2f} {r['win_days']:>8} {r['loss_days']:>8}")
    
    print("=" * 70)
    print("\nFutures = more trading hours per day = more opportunities")
    print("Best candidates for live testing: highest PF + most trades")

if __name__ == '__main__':
    main()
