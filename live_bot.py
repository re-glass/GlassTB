#!/usr/bin/env python3
"""
FAANG Mean Reversion Scalping Bot - Schwab Version
Optimized for 1-minute bars, paper trading ready
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
import threading

# ============================================================
# CONFIGURATION - FILL THESE IN
# ============================================================

SCHWAB_CONFIG = {
    'app_key': 'QbOoVDpyv2AqFwfSWgr2W3mrEB7hdBqJl0A4lEGHKXwZJLow',
    'app_secret': 'sJhulVwHFITG0mU8lvXA5P3n9BV9tpaaUlKTzGWiWcenuorVOGn6bdxR8hyIGoFi',
    'redirect_uri': 'https://127.0.0.1:8080',
    'token_path': 'tokens.json',
    'paper_trading': True,                # Set to False when ready for live
}

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
        
        # Try to load existing tokens
        self.load_tokens()
    
    def load_tokens(self):
        """Load saved tokens from file."""
        if os.path.exists(self.config['token_path']):
            try:
                with open(self.config['token_path'], 'r') as f:
                    tokens = json.load(f)
                self.access_token = tokens.get('access_token')
                self.refresh_token = tokens.get('refresh_token')
                self.token_expiry = datetime.fromisoformat(tokens.get('expiry', '2000-01-01'))
                self.account_id = tokens.get('account_id')
                print("Loaded saved tokens.")
            except Exception as e:
                print(f"Error loading tokens: {e}")
    
    def save_tokens(self):
        """Save tokens to file."""
        tokens = {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'expiry': self.token_expiry.isoformat() if self.token_expiry else None,
            'account_id': self.account_id,
        }
        with open(self.config['token_path'], 'w') as f:
            json.dump(tokens, f, indent=2)
        print("Tokens saved.")
    
    def is_authenticated(self):
        """Check if we have valid tokens."""
        if not self.access_token:
            return False
        if self.token_expiry and datetime.now() > self.token_expiry:
            return False
        return True
    
    def authenticate(self):
        """OAuth2 authentication flow."""
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
        print("After logging in, you'll be redirected to localhost.")
        print("Copy the 'code' parameter from the URL and paste it here.\n")
        
        # Open browser
        webbrowser.open(auth_url)
        
        # Start local server to capture callback (may fail if HTTPS redirect)
        code = self._capture_oauth_code()
        
        if not code:
            # Manual fallback
            print("\nIf you see an error page in your browser, look at the URL bar.")
            print("You should see something like:")
            print("  https://localhost:8080/?code=SOMECODE&session=...")
            print("Copy the value after 'code=' and paste it below.\n")
            raw = input("Enter the authorization code: ").strip()
            # Extract code from URL if user pasted full URL
            if 'code=' in raw:
                from urllib.parse import urlparse, parse_qs
                if raw.startswith('http'):
                    query = urlparse(raw).query
                else:
                    query = raw.split('?')[-1] if '?' in raw else raw
                params = parse_qs(query)
                code = params.get('code', [''])[0]
            else:
                # Strip any trailing &session=... if present
                code = raw.split('&')[0].strip()
            print(f"Using code: {code[:20]}...")
        
        # Exchange code for tokens
        self._exchange_code_for_tokens(code)
    
    def _capture_oauth_code(self, timeout=120):
        """Start a local HTTP server to capture the OAuth callback."""
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
                    handler_self.wfile.write(b'<html><body><h1>Authentication successful!</h1><p>You can close this window.</p></body></html>')
                else:
                    handler_self.send_response(400)
                    handler_self.end_headers()
            
            def log_message(self, format, *args):
                pass  # Suppress logs
        
        try:
            server = HTTPServer(('localhost', 8080), Handler)
            server.timeout = timeout
            
            print("Waiting for authentication callback...")
            server.handle_request()
        except OSError as e:
            print(f"Local server couldn't start: {e}")
            print("You'll need to paste the code manually.")
        
        return code[0]
    
    def _exchange_code_for_tokens(self, code):
        """Exchange authorization code for access/refresh tokens."""
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
            print(response.text)
            return False
        
        data = response.json()
        self.access_token = data['access_token']
        self.refresh_token = data['refresh_token']
        self.token_expiry = datetime.now() + timedelta(seconds=data.get('expires_in', 1800))
        
        self.save_tokens()
        print("Authentication successful!")
        return True
    
    def refresh_tokens(self):
        """Refresh access token using refresh token."""
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
            print(f"Error refreshing tokens: {response.status_code}")
            return False
        
        data = response.json()
        self.access_token = data['access_token']
        self.refresh_token = data['refresh_token']
        self.token_expiry = datetime.now() + timedelta(seconds=data.get('expires_in', 1800))
        
        self.save_tokens()
        print("Tokens refreshed.")
        return True
    
    def get_headers(self):
        """Get request headers with auth token."""
        if self.token_expiry and datetime.now() > self.token_expiry:
            self.refresh_tokens()
        
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json',
        }
    
    def get_account_id(self):
        """Get the account hash for API calls and store account number."""
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
        
        print("Error getting account ID:")
        print(response.status_code, response.text)
        return None
    
    def get_account_info(self):
        """Get account details (balance, buying power, etc.)."""
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
        
        print("Error getting account info:")
        print(response.status_code, response.text)
        return None
    
    def get_price_history(self, symbol, period_type='day', period=1, frequency_type='minute', frequency=1):
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
        
        print(f"Error getting price history for {symbol}:")
        print(response.status_code, response.text)
        return None
    
    def get_quote(self, symbol):
        """Get current quote for a symbol."""
        response = requests.get(
            f"{self.market_data_url}/{symbol}/quotes",
            headers=self.get_headers()
        )
        
        if response.status_code == 200:
            return response.json()
        
        print(f"Error getting quote for {symbol}:")
        print(response.status_code, response.text)
        return None
    
    def place_order(self, symbol, side, quantity, order_type='MARKET', limit_price=None):
        """Place an order.
        
        Args:
            symbol: Stock ticker
            side: 'BUY' or 'SELL'
            quantity: Number of shares
            order_type: 'MARKET', 'LIMIT', etc.
            limit_price: Required for LIMIT orders
        """
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
        
        print(f"Error placing order:")
        print(response.status_code, response.text)
        return None
    
    def get_orders(self):
        """Get current orders."""
        account_id = self.get_account_id()
        if not account_id:
            return None
        
        response = requests.get(
            f"{self.trader_url}/accounts/{account_id}/orders",
            headers=self.get_headers()
        )
        
        if response.status_code == 200:
            return response.json()
        
        print("Error getting orders:")
        print(response.status_code, response.text)
        return None


# ============================================================
# STRATEGY (SAME AS OPTIMIZED BACKTEST)
# ============================================================

def compute_indicators(df):
    """Compute all indicators for the strategy."""
    df = df.copy()
    
    # VWAP (simpler implementation)
    df['_date'] = df.index.date
    df['CumVol'] = df.groupby('_date')['Volume'].cumsum()
    df['TypicalPrice'] = (df['High'] + df['Low'] + df['Close']) / 3
    df['CumVolPrice'] = (df['Volume'] * df['TypicalPrice']).groupby(df['_date']).cumsum()
    df['VWAP'] = df['CumVolPrice'] / df['CumVol']
    df = df.drop(columns=['CumVol', 'CumVolPrice', 'TypicalPrice', '_date'])
    
    # Bollinger Bands
    df['BB_Mid'] = df['Close'].rolling(20).mean()
    bb_std = df['Close'].rolling(20).std()
    df['BB_Upper'] = df['BB_Mid'] + (2.0 * bb_std)
    df['BB_Lower'] = df['BB_Mid'] - (2.0 * bb_std)
    df['BB_PctB'] = (df['Close'] - df['BB_Lower']) / (df['BB_Upper'] - df['BB_Lower'])
    
    # RSI
    delta = df['Close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # ATR
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    
    return df


def generate_signal(row):
    """Generate mean reversion signal (same rules as backtest)."""
    if pd.isna(row['VWAP']) or pd.isna(row['RSI']) or pd.isna(row['BB_PctB']) or pd.isna(row['ATR']):
        return 0
    
    deviation = (row['Close'] - row['VWAP']) / row['VWAP'] * 100
    
    # Long signal
    if deviation < -0.3 and row['RSI'] < 30 and row['BB_PctB'] < 0.1:
        return 1
    
    # Short signal
    if deviation > 0.3 and row['RSI'] > 65 and row['BB_PctB'] > 0.9:
        return -1
    
    return 0


# ============================================================
# MAIN TRADING LOOP
# ============================================================

def main():
    print("="*70)
    print("FAANG MEAN REVERSION SCALPING BOT")
    print("="*70)
    
    # Initialize API
    api = SchwabAPI(SCHWAB_CONFIG)
    
    if SCHWAB_CONFIG['paper_trading']:
        print("\n*** PAPER TRADING MODE ***")
    
    if not api.is_authenticated():
        api.authenticate()
    
    # Get account info
    account = api.get_account_info()
    if account:
        print(f"\nAccount: {account.get('securitiesAccount', {}).get('accountNumber')}")
        balance = account.get('securitiesAccount', {}).get('currentBalances', {})
        print(f"Cash: ${balance.get('cashBalance', 0):,.2f}")
        print(f"Buying Power: ${balance.get('buyingPower', 0):,.2f}")
    
    # Trading parameters
    tickers = ['AAPL', 'GOOGL', 'META', 'AMZN', 'NFLX']
    max_positions = 2
    risk_pct = 0.02
    max_daily_loss_pct = 0.05
    
    print(f"\nTickers: {tickers}")
    print(f"Max Positions: {max_positions}")
    print(f"Risk per Trade: {risk_pct*100}%")
    print(f"Max Daily Loss: {max_daily_loss_pct*100}%")
    print("\nStarting main loop...\n")
    
    # State tracking
    daily_pnl = 0.0
    today = datetime.now().date()
    open_positions = {}  # ticker -> {'side': 'long'/'short', 'qty': int, 'entry': float, 'sl': float, 'tp': float, 'entry_time': datetime}
    paper_positions = {}  # Same structure for paper trading
    total_pnl = 0.0
    account_value = 1000.0  # Will be updated from API
    
    try:
        while True:
            # Reset daily tracking at market open
            if datetime.now().date() != today:
                today = datetime.now().date()
                daily_pnl = 0.0
                open_positions.clear()
                paper_positions.clear()
                print(f"\n--- New Day: {today} ---\n")
            
            # Check market hours (9:30 AM - 4:00 PM ET)
            now = datetime.now()
            market_open = now.replace(hour=9, minute=30, second=0)
            market_close = now.replace(hour=16, minute=0, second=0)
            
            if now < market_open or now > market_close:
                print(f"Outside market hours ({now.strftime('%H:%M')}). Waiting...")
                time.sleep(60)
                continue
            
            # Check daily loss limit
            daily_loss_limit = -(account_value * max_daily_loss_pct)
            if daily_pnl < daily_loss_limit:
                print(f"Daily loss limit hit (${daily_pnl:.2f}). Stopping for today.")
                time.sleep(60)
                continue
            
            # Check max positions
            if len(open_positions) >= max_positions and len(paper_positions) >= max_positions:
                time.sleep(30)
                continue
            
            # Fetch data and check each ticker
            for ticker in tickers:
                try:
                    # Skip if already in position for this ticker
                    if ticker in open_positions or ticker in paper_positions:
                        continue
                    
                    # Get price history for indicators
                    history = api.get_price_history(
                        ticker,
                        period_type='day',
                        period=5,
                        frequency_type='minute',
                        frequency=1
                    )
                    
                    if not history or 'candles' not in history:
                        continue
                    
                    # Convert to DataFrame
                    df = pd.DataFrame(history['candles'])
                    df['datetime'] = pd.to_datetime(df['datetime'], unit='ms', utc=True).dt.tz_convert('US/Eastern')
                    df.set_index('datetime', inplace=True)
                    df = df[['open', 'high', 'low', 'close', 'volume']].rename(columns={
                        'open': 'Open',
                        'high': 'High',
                        'low': 'Low',
                        'close': 'Close',
                        'volume': 'Volume'
                    })
                    
                    # Compute indicators
                    df = compute_indicators(df)
                    df = df.dropna()
                    
                    if df.empty:
                        continue
                    
                    # Generate signal
                    latest = df.iloc[-1]
                    signal = generate_signal(latest)
                    
                    if signal == 0:
                        continue
                    
                    # Get current price
                    quote = api.get_quote(ticker)
                    if not quote:
                        continue
                    
                    # Extract LIVE quote data (not stale extended)
                    ticker_data = quote.get(ticker, {})
                    bid = ticker_data.get('quote', {}).get('bidPrice', 0)
                    ask = ticker_data.get('quote', {}).get('askPrice', 0)
                    
                    if signal == 1:
                        entry_price = ask
                    else:
                        entry_price = bid
                    
                    # Calculate position size
                    atr = latest['ATR']
                    if pd.isna(atr) or atr == 0:
                        continue
                    
                    # Update account value periodically
                    account_info = api.get_account_info()
                    if account_info:
                        account_value = account_info.get('securitiesAccount', {}).get('currentBalances', {}).get('liquidationValue', 1000.0)
                    
                    risk_amount = account_value * risk_pct
                    stop_distance = atr * 1.0
                    
                    if stop_distance == 0:
                        continue
                    
                    qty = max(1, int(risk_amount / stop_distance))
                    
                    if signal == 1:
                        side = 'BUY'
                        sl_price = entry_price - stop_distance
                        tp_price = entry_price + (stop_distance * 1.5)
                    else:
                        side = 'SELL'
                        sl_price = entry_price + stop_distance
                        tp_price = entry_price - (stop_distance * 1.5)
                    
                    print(f"\n[{now.strftime('%H:%M:%S')}] Signal for {ticker}:")
                    print(f"  Side: {side}")
                    print(f"  Price: ${entry_price:.2f}")
                    print(f"  SL: ${sl_price:.2f} (${stop_distance:.2f} away)")
                    print(f"  TP: ${tp_price:.2f}")
                    print(f"  Qty: {qty}")
                    print(f"  Risk: ${risk_amount:.2f} ({risk_pct*100}% of ${account_value:.2f})")
                    
                    if not SCHWAB_CONFIG['paper_trading']:
                        # Place market order
                        order = api.place_order(ticker, side, qty)
                        if order:
                            print(f"  ORDER PLACED")
                            # Track position (in real system, poll for fill first)
                            open_positions[ticker] = {
                                'side': 'long' if signal == 1 else 'short',
                                'qty': qty,
                                'entry': entry_price,
                                'sl': sl_price,
                                'tp': tp_price,
                                'entry_time': now
                            }
                            # TODO: Place SL/TP bracket order
                        else:
                            print(f"  ORDER FAILED")
                    else:
                        print(f"  [PAPER TRADE] Order would be placed")
                        paper_positions[ticker] = {
                            'side': 'long' if signal == 1 else 'short',
                            'qty': qty,
                            'entry': entry_price,
                            'sl': sl_price,
                            'tp': tp_price,
                            'entry_time': now
                        }
                
                except Exception as e:
                    print(f"Error processing {ticker}: {e}")
            
            # Monitor paper positions for exit conditions
            positions_to_close = []
            for ticker, pos in paper_positions.items():
                quote = api.get_quote(ticker)
                if not quote:
                    continue
                # Get LIVE price for exit check
                ticker_data = quote.get(ticker, {})
                last_price = ticker_data.get('quote', {}).get('lastPrice', 0)
                if last_price == 0:
                    last_price = ticker_data.get('extended', {}).get('lastPrice', 0)
                
                if pos['side'] == 'long':
                    # Check stop loss
                    if last_price <= pos['sl']:
                        pnl = (pos['sl'] - pos['entry']) * pos['qty']
                        print(f"\n  [PAPER SL] {ticker}: ${pos['sl']:.2f} hit, P&L: ${pnl:.2f}")
                        daily_pnl += pnl
                        total_pnl += pnl
                        positions_to_close.append(ticker)
                    # Check take profit
                    elif last_price >= pos['tp']:
                        pnl = (pos['tp'] - pos['entry']) * pos['qty']
                        print(f"\n  [PAPER TP] {ticker}: ${pos['tp']:.2f} hit, P&L: ${pnl:.2f}")
                        daily_pnl += pnl
                        total_pnl += pnl
                        positions_to_close.append(ticker)
                else:  # short
                    # Check stop loss
                    if last_price >= pos['sl']:
                        pnl = (pos['entry'] - pos['sl']) * pos['qty']
                        print(f"\n  [PAPER SL] {ticker}: ${pos['sl']:.2f} hit, P&L: ${pnl:.2f}")
                        daily_pnl += pnl
                        total_pnl += pnl
                        positions_to_close.append(ticker)
                    # Check take profit
                    elif last_price <= pos['tp']:
                        pnl = (pos['entry'] - pos['tp']) * pos['qty']
                        print(f"\n  [PAPER TP] ${pos['tp']:.2f} hit, P&L: ${pnl:.2f}")
                        daily_pnl += pnl
                        total_pnl += pnl
                        positions_to_close.append(ticker)
            
            # Close exited positions
            for ticker in positions_to_close:
                paper_positions.pop(ticker, None)
            
            # Market close: close all paper positions at 4:00 PM
            if now.hour == 16 and now.minute == 0:
                for ticker, pos in list(paper_positions.items()):
                    quote = api.get_quote(ticker)
                    if quote:
                        # Get LIVE price for market close
                        ticker_data = quote.get(ticker, {})
                        last_price = ticker_data.get('quote', {}).get('lastPrice', pos['entry'])
                        if last_price == 0:
                            last_price = ticker_data.get('extended', {}).get('lastPrice', pos['entry'])
                        if pos['side'] == 'long':
                            pnl = (last_price - pos['entry']) * pos['qty']
                        else:
                            pnl = (pos['entry'] - last_price) * pos['qty']
                        print(f"\n  [PAPER MOC] {ticker} closed at ${last_price:.2f}, P&L: ${pnl:.2f}")
                        daily_pnl += pnl
                        total_pnl += pnl
                paper_positions.clear()
                print(f"\n--- Market Closed. Daily P&L: ${daily_pnl:.2f}, Total P&L: ${total_pnl:.2f} ---\n")
            
            # Print status line every loop
            now = datetime.now()
            status = f"[{now.strftime('%H:%M:%S')}] "
            for ticker in tickers:
                if ticker in paper_positions:
                    pos = paper_positions[ticker]
                    status += f"{ticker}:{pos['side'][0].upper()}@{pos['entry']:.0f} "
                else:
                    # Get last known price from recent API call
                    quote = api.get_quote(ticker)
                    if quote and ticker in quote:
                        # Get LIVE price from quote.lastPrice, fallback to extended
                        ticker_data = quote[ticker]
                        last_price = ticker_data.get('quote', {}).get('lastPrice', 0)
                        if last_price == 0:
                            last_price = ticker_data.get('extended', {}).get('lastPrice', 0)
                        status += f"{ticker}:${last_price:.2f} "
            print(status)
            
            # Wait before next iteration
            time.sleep(30)
    
    except KeyboardInterrupt:
        print("\n\nBot stopped by user.")
        print(f"Final Daily P&L: ${daily_pnl:.2f}")
        print(f"Total Session P&L: ${total_pnl:.2f}")
        print("Goodbye!")


if __name__ == '__main__':
    main()
