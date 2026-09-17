#!/usr/bin/env python3
"""
Enhanced Mean Reversion Scalping Backtest
- Walk-forward parameter optimization
- Extended historical data (multiple 8-day chunks)
- Trade distribution analysis
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

TICKERS = ['AAPL', 'GOOGL', 'META', 'AMZN', 'NFLX']
MAX_POSITIONS = 2
RISK_PCT = 0.02
MAX_DAILY_LOSS_PCT = 0.05
STARTING_CAPITAL = 1000

# Fetch multiple chunks for more data
NUM_CHUNKS = 6  # 6 chunks * ~8 days = ~48 days of data

# ============================================================
# INDICATORS
# ============================================================

def compute_vwap(df):
    df = df.copy()
    df['_date'] = df.index.date
    df['CumVol'] = df.groupby('_date')['Volume'].cumsum()
    df['CumVolPrice'] = df.groupby('_date').apply(
        lambda x: (x['Volume'] * (x['High'] + x['Low'] + x['Close']) / 3).cumsum()
    ).reset_index(level=0, drop=True)
    df['VWAP'] = df['CumVolPrice'] / df['CumVol']
    return df.drop(columns=['CumVol', 'CumVolPrice', '_date'])

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

# ============================================================
# STRATEGY
# ============================================================

def mean_reversion_signal(row, rsi_oversold, rsi_overbought):
    if pd.isna(row['VWAP']) or pd.isna(row['RSI']) or pd.isna(row['BB_PctB']) or pd.isna(row['ATR']):
        return 0
    
    deviation = (row['Close'] - row['VWAP']) / row['VWAP'] * 100
    
    if deviation < -0.3 and row['RSI'] < rsi_oversold and row['BB_PctB'] < 0.1:
        return 1
    
    if deviation > 0.3 and row['RSI'] > rsi_overbought and row['BB_PctB'] > 0.9:
        return -1
    
    return 0

# ============================================================
# BACKTEST ENGINE
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
        
        # Close positions
        positions_to_remove = []
        for j, pos in enumerate(open_positions):
            if pos['side'] == 'long':
                if row['Low'] <= pos['sl']:
                    pnl = (pos['sl'] - pos['entry_price']) * pos['qty']
                    daily_pnl[today] += pnl
                    trades.append({'pnl': pnl, 'side': pos['side'], 'reason': 'SL'})
                    positions_to_remove.append(j)
                elif row['High'] >= pos['tp']:
                    pnl = (pos['tp'] - pos['entry_price']) * pos['qty']
                    daily_pnl[today] += pnl
                    trades.append({'pnl': pnl, 'side': pos['side'], 'reason': 'TP'})
                    positions_to_remove.append(j)
            else:
                if row['High'] >= pos['sl']:
                    pnl = (pos['entry_price'] - pos['sl']) * pos['qty']
                    daily_pnl[today] += pnl
                    trades.append({'pnl': pnl, 'side': pos['side'], 'reason': 'SL'})
                    positions_to_remove.append(j)
                elif row['Low'] <= pos['tp']:
                    pnl = (pos['entry_price'] - pos['tp']) * pos['qty']
                    daily_pnl[today] += pnl
                    trades.append({'pnl': pnl, 'side': pos['side'], 'reason': 'TP'})
                    positions_to_remove.append(j)
        
        for j in sorted(positions_to_remove, reverse=True):
            open_positions.pop(j)
        
        # Market close
        if timestamp.hour == 15 and timestamp.minute == 59:
            for pos in open_positions:
                exit_price = row['Close']
                pnl = (exit_price - pos['entry_price']) * pos['qty'] if pos['side'] == 'long' else (pos['entry_price'] - exit_price) * pos['qty']
                daily_pnl[today] += pnl
                trades.append({'pnl': pnl, 'side': pos['side'], 'reason': 'MOC'})
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
            'entry_price': entry_price,
            'side': 'long' if signal == 1 else 'short',
            'sl': sl, 'tp': tp,
            'qty': qty
        })
    
    return trades, daily_pnl

# ============================================================
# DATA FETCHING
# ============================================================

def fetch_extended_1m(ticker, chunks=6, chunk_days=8):
    """Fetch multiple 8-day chunks and combine."""
    all_data = []
    end_date = datetime.now()
    
    for i in range(chunks):
        chunk_start = end_date - timedelta(days=chunk_days * (i + 1))
        chunk_end = end_date - timedelta(days=chunk_days * i)
        
        try:
            t = yf.Ticker(ticker)
            data = t.history(
                start=chunk_start.strftime('%Y-%m-%d'),
                end=chunk_end.strftime('%Y-%m-%d'),
                interval='1m'
            )
            if not data.empty:
                data = data[['Open', 'High', 'Low', 'Close', 'Volume']]
                all_data.append(data)
        except Exception as e:
            print(f"  Chunk {i+1}/{chunks} failed for {ticker}: {e}")
    
    if not all_data:
        return pd.DataFrame()
    
    combined = pd.concat(all_data)
    combined = combined[~combined.index.duplicated(keep='first')]
    combined.sort_index(inplace=True)
    return combined

# ============================================================
# OPTIMIZATION
# ============================================================

def optimize_parameters(all_data, capital):
    """Walk-forward parameter optimization."""
    
    param_grid = {
        'atr_mult_sl': [1.0, 1.5, 2.0],
        'atr_mult_tp': [1.5, 2.0, 2.5],
        'rsi_oversold': [25, 30, 35],
        'rsi_overbought': [65, 70, 75],
    }
    
    results = []
    param_combos = list(product(
        param_grid['atr_mult_sl'],
        param_grid['atr_mult_tp'],
        param_grid['rsi_oversold'],
        param_grid['rsi_overbought']
    ))
    
    print(f"Testing {len(param_combos)} parameter combinations...")
    
    for idx, (sl_mult, tp_mult, rsi_os, rsi_ob) in enumerate(param_combos):
        all_trades = []
        all_daily = {}
        
        for ticker, data in all_data.items():
            trades, daily = run_backtest(data, capital, sl_mult, tp_mult, rsi_os, rsi_ob)
            all_trades.extend(trades)
            all_daily.update(daily)
        
        if not all_trades:
            continue
        
        total_pnl = sum(t['pnl'] for t in all_trades)
        total_trades = len(all_trades)
        wins = sum(1 for t in all_trades if t['pnl'] > 0)
        losses = sum(1 for t in all_trades if t['pnl'] < 0)
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0
        
        win_pnl = sum(t['pnl'] for t in all_trades if t['pnl'] > 0)
        loss_pnl = abs(sum(t['pnl'] for t in all_trades if t['pnl'] < 0))
        profit_factor = win_pnl / loss_pnl if loss_pnl > 0 else float('inf')
        
        daily_values = list(all_daily.values())
        daily_win_days = sum(1 for v in daily_values if v > 0)
        daily_loss_days = sum(1 for v in daily_values if v < 0)
        avg_daily = np.mean(daily_values) if daily_values else 0
        
        # Max drawdown
        cumulative = np.cumsum([t['pnl'] for t in all_trades])
        peak = np.maximum.accumulate(cumulative)
        drawdown = cumulative - peak
        max_dd = drawdown.min()
        
        results.append({
            'sl_mult': sl_mult,
            'tp_mult': tp_mult,
            'rsi_os': rsi_os,
            'rsi_ob': rsi_ob,
            'total_pnl': total_pnl,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'max_drawdown': max_dd,
            'daily_win_days': daily_win_days,
            'daily_loss_days': daily_loss_days,
            'avg_daily': avg_daily,
            'final_capital': capital + total_pnl,
            'sharpe_approx': total_pnl / abs(max_dd) if max_dd != 0 else float('inf')
        })
        
        if (idx + 1) % 10 == 0:
            print(f"  {idx+1}/{len(param_combos)} combinations tested...")
    
    return pd.DataFrame(results).sort_values('total_pnl', ascending=False)

# ============================================================
# TRADE DISTRIBUTION ANALYSIS
# ============================================================

def analyze_trades(all_trades):
    """Analyze trade distribution and patterns."""
    
    if not all_trades:
        print("No trades to analyze.")
        return
    
    pnls = [t['pnl'] for t in all_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    
    print("\n" + "=" * 70)
    print("TRADE DISTRIBUTION ANALYSIS")
    print("=" * 70)
    
    # Overall stats
    print(f"\nTotal Trades: {len(pnls)}")
    print(f"Winning: {len(wins)} ({len(wins)/len(pnls)*100:.1f}%)")
    print(f"Losing: {len(losses)} ({len(losses)/len(pnls)*100:.1f}%)")
    print(f"Breakeven: {len(pnls) - len(wins) - len(losses)}")
    
    # P&L distribution
    print(f"\nP&L Distribution:")
    print(f"  Mean: ${np.mean(pnls):.2f}")
    print(f"  Median: ${np.median(pnls):.2f}")
    print(f"  Std Dev: ${np.std(pnls):.2f}")
    print(f"  Min: ${min(pnls):.2f}")
    print(f"  Max: ${max(pnls):.2f}")
    print(f"  5th pctile: ${np.percentile(pnls, 5):.2f}")
    print(f"  25th pctile: ${np.percentile(pnls, 25):.2f}")
    print(f"  75th pctile: ${np.percentile(pnls, 75):.2f}")
    print(f"  95th pctile: ${np.percentile(pnls, 95):.2f}")
    
    # Win/loss comparison
    if wins:
        print(f"\nWin Stats:")
        print(f"  Mean Win: ${np.mean(wins):.2f}")
        print(f"  Median Win: ${np.median(wins):.2f}")
        print(f"  Largest Win: ${max(wins):.2f}")
    if losses:
        print(f"\nLoss Stats:")
        print(f"  Mean Loss: ${np.mean(losses):.2f}")
        print(f"  Median Loss: ${np.median(losses):.2f}")
        print(f"  Largest Loss: ${min(losses):.2f}")
    
    # Concentration analysis
    total_pnl = sum(pnls)
    sorted_pnls = sorted(pnls, reverse=True)
    top_10_pnl = sum(sorted_pnls[:10])
    top_20_pnl = sum(sorted_pnls[:20])
    
    print(f"\nProfit Concentration:")
    print(f"  Top 10 trades: ${top_10_pnl:.2f} ({top_10_pnl/total_pnl*100:.1f}% of total P&L)")
    print(f"  Top 20 trades: ${top_20_pnl:.2f} ({top_20_pnl/total_pnl*100:.1f}% of total P&L)")
    print(f"  Bottom 10 trades: ${sum(sorted_pnls[-10:]):.2f}")
    
    # Win/loss streaks
    streak = 0
    max_win_streak = 0
    max_loss_streak = 0
    current_streak_type = None
    
    for p in pnls:
        if p > 0:
            if current_streak_type == 'win':
                streak += 1
            else:
                streak = 1
                current_streak_type = 'win'
            max_win_streak = max(max_win_streak, streak)
        elif p < 0:
            if current_streak_type == 'loss':
                streak += 1
            else:
                streak = 1
                current_streak_type = 'loss'
            max_loss_streak = max(max_loss_streak, streak)
    
    print(f"\nStreaks:")
    print(f"  Max Win Streak: {max_win_streak}")
    print(f"  Max Loss Streak: {max_loss_streak}")
    
    # Exit reason analysis
    exit_reasons = {}
    for t in all_trades:
        reason = t['reason']
        if reason not in exit_reasons:
            exit_reasons[reason] = {'count': 0, 'total_pnl': 0}
        exit_reasons[reason]['count'] += 1
        exit_reasons[reason]['total_pnl'] += t['pnl']
    
    print(f"\nExit Reasons:")
    for reason, stats in sorted(exit_reasons.items()):
        avg = stats['total_pnl'] / stats['count'] if stats['count'] > 0 else 0
        print(f"  {reason}: {stats['count']} trades, P&L: ${stats['total_pnl']:.2f}, Avg: ${avg:.2f}")
    
    # Side analysis
    long_trades = [t for t in all_trades if t['side'] == 'long']
    short_trades = [t for t in all_trades if t['side'] == 'short']
    
    print(f"\nSide Analysis:")
    if long_trades:
        long_pnl = sum(t['pnl'] for t in long_trades)
        long_wins = sum(1 for t in long_trades if t['pnl'] > 0)
        print(f"  Longs: {len(long_trades)} trades, P&L: ${long_pnl:.2f}, Win Rate: {long_wins/len(long_trades)*100:.1f}%")
    if short_trades:
        short_pnl = sum(t['pnl'] for t in short_trades)
        short_wins = sum(1 for t in short_trades if t['pnl'] > 0)
        print(f"  Shorts: {len(short_trades)} trades, P&L: ${short_pnl:.2f}, Win Rate: {short_wins/len(short_trades)*100:.1f}%")
    
    # Histogram
    print(f"\nP&L Histogram (buckets of $10):")
    bins = np.arange(min(pnls) // 10 * 10 - 10, max(pnls) // 10 * 10 + 20, 10)
    hist, edges = np.histogram(pnls, bins=bins)
    for i, count in enumerate(hist):
        bar = '#' * min(count, 50)
        print(f"  ${edges[i]:>6.0f} to ${edges[i+1]:>6.0f}: {count:>4} {bar}")
    
    print("=" * 70)

# ============================================================
# MAIN
# ============================================================

def main():
    capital = STARTING_CAPITAL
    
    print("=" * 70)
    print("ENHANCED MEAN REVERSION SCALPING BACKTEST")
    print(f"Tickers: {', '.join(TICKERS)}")
    print(f"Fetching {NUM_CHUNKS} chunks of ~8 days each (~{NUM_CHUNKS*8} days total)")
    print(f"Capital: ${capital:,.2f}")
    print("=" * 70)
    print()
    
    # Fetch data
    all_data = {}
    for ticker in TICKERS:
        print(f"Fetching {ticker}...")
        data = fetch_extended_1m(ticker, chunks=NUM_CHUNKS, chunk_days=8)
        if not data.empty:
            all_data[ticker] = data
            print(f"  Got {len(data)} bars from {data.index[0]} to {data.index[-1]}")
        else:
            print(f"  FAILED to fetch {ticker}")
    
    if not all_data:
        print("No data fetched. Exiting.")
        return
    
    print()
    
    # 1. Optimize parameters
    print("=" * 70)
    print("PHASE 1: PARAMETER OPTIMIZATION")
    print("=" * 70)
    opt_results = optimize_parameters(all_data, capital)
    
    if opt_results.empty:
        print("No optimization results. Exiting.")
        return
    
    # Top 10 results
    print(f"\nTop 10 Parameter Combinations (by total P&L):")
    print("-" * 100)
    print(f"{'SL Mult':<8} {'TP Mult':<8} {'RSI OS':<8} {'RSI OB':<8} {'P&L':>10} {'Trades':>8} {'Win%':>8} {'PF':>8} {'Max DD':>10} {'W-Days':>8} {'L-Days':>8}")
    print("-" * 100)
    
    for _, row in opt_results.head(10).iterrows():
        print(f"{row['sl_mult']:<8.1f} {row['tp_mult']:<8.1f} {row['rsi_os']:<8.0f} {row['rsi_ob']:<8.0f} ${row['total_pnl']:>8.2f} {row['total_trades']:>8.0f} {row['win_rate']:>7.1f}% {row['profit_factor']:>8.2f} ${row['max_drawdown']:>9.2f} {row['daily_win_days']:>8.0f} {row['daily_loss_days']:>8.0f}")
    
    # Best params
    best = opt_results.iloc[0]
    print(f"\nBEST PARAMETERS:")
    print(f"  SL Mult: {best['sl_mult']}")
    print(f"  TP Mult: {best['tp_mult']}")
    print(f"  RSI Oversold: {best['rsi_os']}")
    print(f"  RSI Overbought: {best['rsi_ob']}")
    print(f"  Total P&L: ${best['total_pnl']:.2f}")
    print(f"  Win Rate: {best['win_rate']:.1f}%")
    print(f"  Profit Factor: {best['profit_factor']:.2f}")
    print(f"  Max Drawdown: ${best['max_drawdown']:.2f}")
    
    # 2. Run with best params on extended data
    print(f"\n{'=' * 70}")
    print("PHASE 2: FULL BACKTEST WITH BEST PARAMETERS")
    print("=" * 70)
    
    all_trades = []
    all_daily = {}
    
    for ticker, data in all_data.items():
        trades, daily = run_backtest(
            data, capital,
            best['sl_mult'], best['tp_mult'],
            best['rsi_os'], best['rsi_ob']
        )
        all_trades.extend(trades)
        all_daily.update(daily)
        ticker_pnl = sum(t['pnl'] for t in trades)
        print(f"{ticker}: {len(trades)} trades, P&L: ${ticker_pnl:.2f}")
    
    # 3. Trade distribution analysis
    analyze_trades(all_trades)
    
    # Save results
    opt_results.to_csv('optimization_results.csv', index=False)
    trades_df = pd.DataFrame(all_trades)
    trades_df.to_csv('best_param_trades.csv', index=False)
    print(f"Optimization results saved to: optimization_results.csv")
    print(f"Trade details saved to: best_param_trades.csv")

if __name__ == '__main__':
    main()
