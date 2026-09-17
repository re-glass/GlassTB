#!/usr/bin/env python3
"""
FAANG Mean Reversion Scalping Bot - Futures Edition
Trades futures via Schwab API: YM=F, GC=F, ES=F, NQ=F, CL=F, SI=F
Futures trade nearly 24/7 — no market hours restriction
Supports: Mean Reversion, Trend Following, Swing
"""

import pandas as pd
import numpy as np
import requests
import json
import time
from datetime import datetime, timedelta
import os
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

# ============================================================
# CONFIGURATION
# ============================================================

SCHWAB_CONFIG = {
    'app_key': 'QbOoVDpyv2AqFwfSWgr2W3mrEB7hdBqJl0A4lEGHKXwZJLow',
    'app_secret': 'sJhulVwHFITG0mU8lvXA5P3n9BV9tpaaUlKTzGWiWcenuorVOGn6bdxR8hyIGoFi',
    'redirect_uri': 'https://127.0.0.1:8080',
    'token_path': 'tokens.json',
    'paper_trading': True,
}

# Futures tickers via Schwab (use / prefix for futures)
FUTURES_TICKERS = ['/YM', '/GC', '/ES', '/NQ', '/CL', '/SI']

# Strategy selection: 'mean_reversion', 'trend_following', 'swing'
ACTIVE_STRATEGY = 'mean_reversion'

MAX_POSITIONS = 2
RISK_PCT = 0.02
MAX_DAILY_LOSS_PCT = 0.05
ATR_SL = 1.0
ATR_TP = 1.5
RSI_OS = 30
RSI_OB = 65

# ============================================================
# SCHWAB API CLIENT
# ============================================================

class SchwabAPI:
    def __init__(self, config):
        self.config = config
        self.base_url = 'https://api.schwabapi.com/v1'
        self.market_data_url = 'https://api.schwabapi.com/marketdata/v1'
        self.trader_url = 'https://api.schwabapi.com/trader/v1'
        self.access_token = None
        self.refresh_token = None
        self.token_expiry = None
        self.account_id = None
        self.account_number = None
        self.load_tokens()
    
    def load_tokens(self):
        if os.path.exists(self.config['token_path']):
            try:
                with open(self.config['token_path'], 'r') as f:
                    tokens = json.load(f)
                self.access_token = tokens.get('access_token')
                self.refresh_token = tokens.get('refresh_token')
                self.token_expiry = datetime.fromisoformat(tokens.get('expiry', '2000-01-01'))
                self.account_id = tokens.get('account_id')
                self.account_number = tokens.get('account_number')
                print("Loaded saved tokens.")
            except Exception as e:
                print(f"Error loading tokens: {e}")
    
    def save_tokens(self):
        tokens = {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'expiry': self.token_expiry.isoformat() if self.token_expiry else None,
            'account_id': self.account_id,
            'account_number': self.account_number,
        }
        with open(self.config['token_path'], 'w') as f:
            json.dump(tokens, f, indent=2)
        print("Tokens saved.")
    
    def is_authenticated(self):
        if not self.access_token:
            return False
        if self.token_expiry and datetime.now() > self.token_expiry:
            return False
        return True
    
    def authenticate(self):
        auth_url = (
            f"{self.base_url}/oauth/authorize"
            f"?client_id={self.config['app_key']}"
            f"&redirect_uri={urllib.parse.quote(self.config['redirect_uri'])}"
            f"&response_type=code"
        )
        
        print("\n" + "="*50)
        print("AUTHENTICATION REQUIRED")
        print("="*50)
        print(f"\nOpening browser to: {auth_url}")
        print("After logging in, copy the 'code' parameter from the URL.\n")
        
        webbrowser.open(auth_url)
        
        code = self._capture_oauth_code()
        
        if not code:
            raw = input("\nEnter the authorization code: ").strip()
            if 'code=' in raw:
                if raw.startswith('http'):
                    query = urllib.parse.urlparse(raw).query
                else:
                    query = raw.split('?')[-1] if '?' in raw else raw
                params = urllib.parse.parse_qs(query)
                code = params.get('code', [''])[0]
            else:
                code = raw.split('&')[0].strip()
        
        self._exchange_code_for_tokens(code)
    
    def _capture_oauth_code(self, timeout=120):
        code = [None]
        
        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler_self):
                if 'code=' in handler_self.path:
                    query = urllib.parse.urlparse(handler_self.path).query
                    params = urllib.parse.parse_qs(query)
                    code[0] = params.get('code', [None])[0]
                    handler_self.send_response(200)
                    handler_self.send_header('Content-type', 'text/html')
                    handler_self.end_headers()
                    handler_self.wfile.write(b'<h1>Success!</h1>')
            def log_message(self, format, *args):
                pass
        
        try:
            server = HTTPServer(('localhost', 8080), Handler)
            server.timeout = timeout
            print("Waiting for authentication callback...")
            server.handle_request()
        except OSError:
            pass
        
        return code[0]
    
    def _exchange_code_for_tokens(self, code):
        response = requests.post(
            f"{self.base_url}/oauth/token",
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'client_id': self.config['app_key'],
                'client_secret': self.config['app_secret'],
                'redirect_uri': self.config['redirect_uri'],
            }
        )
        
        if response.status_code != 200:
            print(f"Error getting tokens: {response.status_code}")
            return False
        
        data = response.json()
        self.access_token = data['access_token']
        self.refresh_token = data['refresh_token']
        self.token_expiry = datetime.now() + timedelta(seconds=data.get('expires_in', 1800))
        self.save_tokens()
        print("Authentication successful!")
        return True
    
    def refresh_tokens(self):
        response = requests.post(
            f"{self.base_url}/oauth/token",
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data={
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token,
                'client_id': self.config['app_key'],
                'client_secret': self.config['app_secret'],
            }
        )
        if response.status_code != 200:
            return False
        data = response.json()
        self.access_token = data['access_token']
        self.refresh_token = data['refresh_token']
        self.token_expiry = datetime.now() + timedelta(seconds=data.get('expires_in', 1800))
        self.save_tokens()
        return True
    
    def get_headers(self):
        if self.token_expiry and datetime.now() > self.token_expiry:
            self.refresh_tokens()
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json',
        }
    
    def get_account_id(self):
        if self.account_id:
            return self.account_id
        
        response = requests.get(
            f"{self.trader_url}/accounts/accountNumbers",
            headers=self.get_headers()
        )
        
        if response.status_code == 200:
            accounts = response.json()
            if accounts:
                self.account_id = accounts[0].get('hashValue')
                self.account_number = accounts[0].get('accountNumber')
                self.save_tokens()
                return self.account_id
        
        print(f"Error getting account ID: {response.status_code} {response.text}")
        return None
    
    def get_account_info(self):
        account_id = self.get_account_id()
        if not account_id:
            return None
        
        response = requests.get(
            f"{self.trader_url}/accounts/{account_id}",
            headers=self.get_headers(),
            params={'fields': 'positions'}
        )
        
        if response.status_code == 200:
            return response.json()
        
        print(f"Error getting account info: {response.status_code} {response.text}")
        return None
    def get_price_history(self, symbol, period_type='day', period=5, frequency_type='minute', frequency=1):
        """Get price history for a symbol."""
        response = requests.get(
            f"{self.market_data_url}/pricehistory",
            headers=self.get_headers(),
            params={
                'symbol': symbol,
                'periodType': period_type,
                'period': period,
                'frequencyType': frequency_type,
                'frequency': frequency,
            }
        )
        
        if response.status_code == 200:
            return response.json()
        
        print(f"Error getting price history for {symbol}: {response.status_code} {response.text}")
        return None
    
    def get_latest_price(self, symbol):
        """Get latest price from most recent candle in price history."""
        history = self.get_price_history(symbol, period_type='day', period=1, frequency_type='minute', frequency=1)
        if not history or 'candles' not in history or not history['candles']:
            return None, None, None
        
        candles = history['candles']
        latest = candles[-1]
        last_price = latest.get('close', 0)
        
        # Get previous candle for bid/ask approximation
        if len(candles) >= 2:
            prev = candles[-2]
            bid = prev.get('close', last_price)
            ask = last_price
        else:
            bid = last_price
            ask = last_price
        
        return last_price, bid, ask
    def place_order(self, symbol, side, quantity, order_type='MARKET', limit_price=None):
        account_id = self.get_account_id()
        if not account_id:
            return None
        
        order = {
            'session': 'NORMAL',
            'duration': 'DAY',
            'orderType': order_type,
            'quantity': quantity,
            'symbol': symbol,
        }
        
        if side == 'BUY':
            order['instruction'] = 'BUY'
        elif side == 'SELL':
            order['instruction'] = 'SELL'
        
        if order_type == 'LIMIT' and limit_price:
            order['price'] = limit_price
        
        response = requests.post(
            f"{self.trader_url}/accounts/{account_id}/orders",
            headers={**self.get_headers(), 'Content-Type': 'application/json'},
            json=order
        )
        
        if response.status_code in [200, 201]:
            print(f"Order placed: {side} {quantity} {symbol}")
            return response.json()
        
        print(f"Error placing order: {response.status_code} {response.text}")
        return None


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
# STRATEGIES
# ============================================================

def mean_reversion_signal(row):
    if pd.isna(row.get('VWAP')) or pd.isna(row.get('RSI')) or pd.isna(row.get('BB_PctB')):
        return 0
    deviation = (row['Close'] - row['VWAP']) / row['VWAP'] * 100
    if deviation < -0.3 and row['RSI'] < RSI_OS and row['BB_PctB'] < 0.1:
        return 1
    if deviation > 0.3 and row['RSI'] > RSI_OB and row['BB_PctB'] > 0.9:
        return -1
    return 0

def trend_following_signal(row):
    if pd.isna(row.get('EMA_Fast')) or pd.isna(row.get('EMA_Slow')):
        return 0
    uptrend = row['EMA_Fast'] > row['EMA_Slow'] and row['Close'] > row.get('EMA_Trend', row['EMA_Slow'])
    downtrend = row['EMA_Fast'] < row['EMA_Slow'] and row['Close'] < row.get('EMA_Trend', row['EMA_Slow'])
    if uptrend and row['Low'] <= row['EMA_Fast'] and row['Close'] >= row['EMA_Fast']:
        return 1
    if downtrend and row['High'] >= row['EMA_Fast'] and row['Close'] <= row['EMA_Fast']:
        return -1
    return 0

def swing_signal(row):
    if pd.isna(row.get('EMA_Fast')) or pd.isna(row.get('RSI')):
        return 0
    if row['EMA_Fast'] > row['EMA_Slow'] and 40 < row['RSI'] < 60:
        return 1
    if row['EMA_Fast'] < row['EMA_Slow'] and 40 < row['RSI'] < 60:
        return -1
    return 0


# ============================================================
# MAIN TRADING LOOP
# ============================================================

def main():
    print("="*70)
    print("FUTURES SCALPING BOT — Mean Reversion + Trend + Swing")
    print("="*70)
    print(f"Strategy: {ACTIVE_STRATEGY}")
    print(f"Tickers: {FUTURES_TICKERS}")
    print(f"Capital: $1,000 | Paper Trading: {SCHWAB_CONFIG['paper_trading']}")
    
    api = SchwabAPI(SCHWAB_CONFIG)
    
    if not api.is_authenticated():
        api.authenticate()
    
    account = api.get_account_info()
    if account:
        balance = account.get('securitiesAccount', {}).get('currentBalances', {})
        print(f"\nCash: ${balance.get('cashBalance', 0):,.2f}")
        print(f"Buying Power: ${balance.get('buyingPower', 0):,.2f}")
    
    print(f"\nStarting main loop...\n")
    
    daily_pnl = 0.0
    today = datetime.now().date()
    paper_positions = {}
    total_pnl = 0.0
    account_value = 1000.0
    
    while True:
        if datetime.now().date() != today:
            today = datetime.now().date()
            daily_pnl = 0.0
            paper_positions.clear()
            print(f"\n--- New Day: {today} ---\n")
        
        daily_loss_limit = -(account_value * MAX_DAILY_LOSS_PCT)
        if daily_pnl < daily_loss_limit:
            print(f"Daily loss limit hit (${daily_pnl:.2f})")
            time.sleep(60)
            continue
        
        if len(paper_positions) >= MAX_POSITIONS:
            time.sleep(30)
            continue
        
        for ticker in FUTURES_TICKERS:
            try:
                if ticker in paper_positions:
                    continue
                
                history = api.get_price_history(ticker, period_type='day', period=5, frequency_type='minute', frequency=1)
                if not history or 'candles' not in history:
                    continue
                
                df = pd.DataFrame(history['candles'])
                df['datetime'] = pd.to_datetime(df['datetime'], unit='ms', utc=True).dt.tz_convert('US/Eastern')
                df.set_index('datetime', inplace=True)
                df = df[['open', 'high', 'low', 'close', 'volume']].rename(columns={
                    'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'
                })
                
                df = compute_vwap(df)
                df = compute_bbands(df)
                df = compute_rsi(df)
                df = compute_atr(df)
                df = compute_emas(df)
                df = df.dropna()
                
                if df.empty:
                    continue
                
                latest = df.iloc[-1]
                
                if ACTIVE_STRATEGY == 'mean_reversion':
                    signal = mean_reversion_signal(latest)
                elif ACTIVE_STRATEGY == 'trend_following':
                    signal = trend_following_signal(latest)
                elif ACTIVE_STRATEGY == 'swing':
                    signal = swing_signal(latest)
                else:
                    signal = 0
                
                if signal == 0:
                    continue
                
                last_price, bid, ask = api.get_latest_price(ticker)
                if last_price is None:
                    continue
                
                if signal == 1:
                    entry_price = ask
                else:
                    entry_price = bid
                
                atr = latest['ATR']
                if pd.isna(atr) or atr == 0:
                    continue
                
                account_info = api.get_account_info()
                if account_info:
                    account_value = account_info.get('securitiesAccount', {}).get('currentBalances', {}).get('liquidationValue', 1000.0)
                
                risk_amount = account_value * RISK_PCT
                stop_distance = atr * ATR_SL
                if stop_distance == 0:
                    continue
                
                qty = max(1, int(risk_amount / stop_distance))
                
                if signal == 1:
                    side = 'BUY'
                    sl_price = entry_price - stop_distance
                    tp_price = entry_price + (stop_distance * ATR_TP / ATR_SL)
                else:
                    side = 'SELL'
                    sl_price = entry_price + stop_distance
                    tp_price = entry_price - (stop_distance * ATR_TP / ATR_SL)
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Signal: {side} {qty} {ticker} @ ${entry_price:.2f}")
                print(f"  SL: ${sl_price:.2f} | TP: ${tp_price:.2f} | Risk: ${risk_amount:.2f}")
                
                if not SCHWAB_CONFIG['paper_trading']:
                    order = api.place_order(ticker, side, qty)
                    if order:
                        print(f"  ORDER PLACED")
                else:
                    print(f"  [PAPER TRADE]")
                
                paper_positions[ticker] = {
                    'side': 'long' if signal == 1 else 'short',
                    'qty': qty, 'entry': entry_price,
                    'sl': sl_price, 'tp': tp_price,
                    'entry_time': datetime.now()
                }
            
            except Exception as e:
                print(f"Error processing {ticker}: {e}")
        
        # Monitor exits
        positions_to_close = []
        for ticker, pos in paper_positions.items():
            last_price, bid, ask = api.get_latest_price(ticker)
            if last_price is None:
                continue
            
            if ACTIVE_STRATEGY == 'swing' and (datetime.now() - pos['entry_time']).days >= 3:
                if pos['side'] == 'long':
                    pnl = (last_price - pos['entry']) * pos['qty']
                else:
                    pnl = (pos['entry'] - last_price) * pos['qty']
                print(f"\n  [PAPER TIME EXIT] {ticker}: ${last_price:.2f}, P&L: ${pnl:.2f}")
                daily_pnl += pnl
                total_pnl += pnl
                positions_to_close.append(ticker)
                continue
            
            if pos['side'] == 'long':
                if last_price <= pos['sl']:
                    pnl = (pos['sl'] - pos['entry']) * pos['qty']
                    print(f"\n  [PAPER SL] {ticker}: ${pos['sl']:.2f}, P&L: ${pnl:.2f}")
                    daily_pnl += pnl
                    total_pnl += pnl
                    positions_to_close.append(ticker)
                elif last_price >= pos['tp']:
                    pnl = (pos['tp'] - pos['entry']) * pos['qty']
                    print(f"\n  [PAPER TP] ${pos['tp']:.2f}, P&L: ${pnl:.2f}")
                    daily_pnl += pnl
                    total_pnl += pnl
                    positions_to_close.append(ticker)
            else:
                if last_price >= pos['sl']:
                    pnl = (pos['entry'] - pos['sl']) * pos['qty']
                    print(f"\n  [PAPER SL] {ticker}: ${pos['sl']:.2f}, P&L: ${pnl:.2f}")
                    daily_pnl += pnl
                    total_pnl += pnl
                    positions_to_close.append(ticker)
                elif last_price <= pos['tp']:
                    pnl = (pos['entry'] - pos['tp']) * pos['qty']
                    print(f"\n  [PAPER TP] ${pos['tp']:.2f}, P&L: ${pnl:.2f}")
                    daily_pnl += pnl
                    total_pnl += pnl
                    positions_to_close.append(ticker)
        
        for ticker in positions_to_close:
            paper_positions.pop(ticker, None)
        
        # Status line
        status = f"[{datetime.now().strftime('%H:%M:%S')}] "
        for ticker in FUTURES_TICKERS:
            if ticker in paper_positions:
                pos = paper_positions[ticker]
                status += f"{ticker}:{pos['side'][0].upper()}@{pos['entry']:.0f} "
            else:
                last_price, bid, ask = api.get_latest_price(ticker)
                if last_price is not None:
                    status += f"{ticker}:${last_price:.0f} "
                else:
                    status += f"{ticker}:? "
        print(status)
        
        time.sleep(30)


if __name__ == '__main__':
    main()
