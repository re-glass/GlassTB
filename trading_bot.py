#!/usr/bin/env python3
"""
Refined Trading Bot Framework
Supports: Stocks (FAANG) and Futures
Strategies: Mean Reversion, Trend Following, Swing
"""

import pandas as pd
import numpy as np
import requests
import json
import time
import os
import sys
import signal
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import urllib.parse

# ============================================================
# CONFIGURATION
# ============================================================

class Config:
    """Central configuration for the trading bot."""
    
    # Schwab API
    APP_KEY = 'QbOoVDpyv2AqFwfSWgr2W3mrEB7hdBqJl0A4lEGHKXwZJLow'
    APP_SECRET = 'sJhulVwHFITG0mU8lvXA5P3n9BV9tpaaUlKTzGWiWcenuorVOGn6bdxR8hyIGoFi'
    REDIRECT_URI = 'https://127.0.0.1:8080'
    TOKEN_PATH = 'tokens.json'
    
    # GUI Mode — disable blocking prompts (auto-continue on daily loss)
    GUI_MODE = False
    
    # Paper trading
    PAPER_TRADING = True
    
    # Markets (uncomment one)
    # TICKERS = ['AAPL', 'GOOGL', 'META', 'AMZN', 'NFLX']  # Stocks
    TICKERS = ['/YM', '/GC', '/ES', '/NQ', '/CL', '/SI']  # Futures
    
    # Strategy: 'mean_reversion', 'trend_following', 'swing'
    STRATEGY = 'mean_reversion'
    
    # Risk Management
    MAX_POSITIONS = 2
    RISK_PER_TRADE = 0.02  # 2% of account
    MAX_DAILY_LOSS = 0.05  # 5% of account
    RESUME_ON_DAILY_LOSS = True  # Continue trading after hitting daily loss limit
    MAX_POSITION_SIZE = 0.10  # 10% of account per position
    
    # Strategy Parameters (OPTIMIZED)
    ATR_SL_MULT = 1.0
    ATR_TP_MULT = 2.0  # Wider TP for futures trends
    RSI_OVERSOLD = 25   # More selective entries
    RSI_OVERBOUGHT = 75
    BB_STD = 1.5        # Tighter bands for futures volatility
    VWAP_DEVIATION = 0.3
    
    # Swing Strategy
    SWING_HOLD_DAYS = 3
    
    # Data
    PRICE_HISTORY_PERIOD = 5  # days
    CANDLE_FREQUENCY = 1  # minutes
    
    # Loop
    LOOP_INTERVAL = 30  # seconds


# ============================================================
# LOGGING
# ============================================================

class Logger:
    """Simple structured logger."""
    
    def __init__(self, name: str = 'bot'):
        self.name = name
    
    def info(self, msg: str):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: {msg}")
    
    def warn(self, msg: str):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] WARN: {msg}")
    
    def error(self, msg: str):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ERROR: {msg}")
    
    def trade(self, msg: str):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] TRADE: {msg}")
    
    def status(self, msg: str):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] STATUS: {msg}")


log = Logger()


# ============================================================
# SCHWAB API CLIENT
# ============================================================

class SchwabAPI:
    """Schwab API client with automatic token management."""
    
    def __init__(self):
        self.config = Config
        self.base_url = 'https://api.schwabapi.com/v1'
        self.market_data_url = 'https://api.schwabapi.com/marketdata/v1'
        self.trader_url = 'https://api.schwabapi.com/trader/v1'
        
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.account_hash: Optional[str] = None
        self.account_number: Optional[str] = None
        
        self._load_tokens()
    
    def _load_tokens(self):
        """Load tokens from file."""
        if os.path.exists(self.config.TOKEN_PATH):
            try:
                with open(self.config.TOKEN_PATH, 'r') as f:
                    tokens = json.load(f)
                self.access_token = tokens.get('access_token')
                self.refresh_token = tokens.get('refresh_token')
                expiry = tokens.get('expiry')
                self.token_expiry = datetime.fromisoformat(expiry) if expiry else None
                self.account_hash = tokens.get('account_hash')
                self.account_number = tokens.get('account_number')
                log.info("Tokens loaded.")
            except Exception as e:
                log.error(f"Failed to load tokens: {e}")
    
    def _save_tokens(self):
        """Save tokens to file."""
        tokens = {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'expiry': self.token_expiry.isoformat() if self.token_expiry else None,
            'account_hash': self.account_hash,
            'account_number': self.account_number,
        }
        with open(self.config.TOKEN_PATH, 'w') as f:
            json.dump(tokens, f, indent=2)
    
    def _is_authenticated(self) -> bool:
        """Check if token is valid."""
        if not self.access_token:
            return False
        if self.token_expiry and datetime.now() > self.token_expiry:
            return False
        return True
    
    def _get_headers(self) -> dict:
        """Get authenticated headers, refreshing if needed."""
        if self.token_expiry and datetime.now() > self.token_expiry:
            self._refresh_tokens()
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json',
        }
    
    def authenticate(self):
        """Run OAuth2 flow."""
        auth_url = (
            f"{self.base_url}/oauth/authorize"
            f"?client_id={self.config.APP_KEY}"
            f"&redirect_uri={urllib.parse.quote(self.config.REDIRECT_URI)}"
            f"&response_type=code"
        )
        
        log.info("Opening browser for authentication...")
        print(f"\n{auth_url}\n")
        
        import webbrowser
        webbrowser.open(auth_url)
        
        # Wait for manual code entry
        code = input("Enter the authorization code: ").strip()
        
        # Extract code from URL if needed
        if 'code=' in code:
            if code.startswith('http'):
                query = urllib.parse.urlparse(code).query
            else:
                query = code.split('?')[-1] if '?' in code else code
            params = urllib.parse.parse_qs(query)
            code = params.get('code', [''])[0]
        else:
            code = code.split('&')[0]
        
        self._exchange_code(code)
    
    def _exchange_code(self, code: str):
        """Exchange authorization code for tokens."""
        response = requests.post(
            f"{self.base_url}/oauth/token",
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'client_id': self.config.APP_KEY,
                'client_secret': self.config.APP_SECRET,
                'redirect_uri': self.config.REDIRECT_URI,
            }
        )
        
        if response.status_code != 200:
            raise Exception(f"Token exchange failed: {response.status_code} {response.text}")
        
        data = response.json()
        self.access_token = data['access_token']
        self.refresh_token = data['refresh_token']
        self.token_expiry = datetime.now() + timedelta(seconds=data.get('expires_in', 1800))
        self._save_tokens()
        log.info("Authentication successful!")
    
    def _refresh_tokens(self):
        """Refresh access token."""
        response = requests.post(
            f"{self.base_url}/oauth/token",
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data={
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token,
                'client_id': self.config.APP_KEY,
                'client_secret': self.config.APP_SECRET,
            }
        )
        
        if response.status_code != 200:
            raise Exception(f"Token refresh failed: {response.status_code}")
        
        data = response.json()
        self.access_token = data['access_token']
        self.refresh_token = data['refresh_token']
        self.token_expiry = datetime.now() + timedelta(seconds=data.get('expires_in', 1800))
        self._save_tokens()
        log.info("Tokens refreshed.")
    
    def get_account_hash(self) -> Optional[str]:
        """Get account hash for API calls."""
        if self.account_hash:
            return self.account_hash
        
        response = requests.get(
            f"{self.trader_url}/accounts/accountNumbers",
            headers=self._get_headers()
        )
        
        if response.status_code == 200:
            accounts = response.json()
            if accounts:
                self.account_hash = accounts[0].get('hashValue')
                self.account_number = accounts[0].get('accountNumber')
                self._save_tokens()
                return self.account_hash
        
        log.error(f"Failed to get account: {response.status_code}")
        return None
    
    def get_account_info(self) -> Optional[dict]:
        """Get account balance and positions."""
        account_hash = self.get_account_hash()
        if not account_hash:
            return None
        
        response = requests.get(
            f"{self.trader_url}/accounts/{account_hash}",
            headers=self._get_headers(),
            params={'fields': 'positions'}
        )
        
        if response.status_code == 200:
            return response.json()
        
        log.error(f"Failed to get account info: {response.status_code}")
        return None
    
    def get_price_history(self, symbol: str) -> Optional[pd.DataFrame]:
        """Get price history as DataFrame."""
        response = requests.get(
            f"{self.market_data_url}/pricehistory",
            headers=self._get_headers(),
            params={
                'symbol': symbol,
                'periodType': 'day',
                'period': self.config.PRICE_HISTORY_PERIOD,
                'frequencyType': 'minute',
                'frequency': self.config.CANDLE_FREQUENCY,
            }
        )
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        if 'candles' not in data or not data['candles']:
            return None
        
        df = pd.DataFrame(data['candles'])
        df['datetime'] = pd.to_datetime(df['datetime'], unit='ms', utc=True).dt.tz_convert('US/Eastern')
        df.set_index('datetime', inplace=True)
        df = df[['open', 'high', 'low', 'close', 'volume']].rename(columns={
            'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'
        })
        
        return df
    
    def get_latest_price(self, symbol: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """Get latest price from recent candles."""
        history = self.get_price_history(symbol)
        if history is None or history.empty:
            return None, None, None
        
        last = history.iloc[-1]
        last_price = last['Close']
        
        # Approximate bid/ask from recent candles
        if len(history) >= 2:
            prev = history.iloc[-2]
            bid = prev['Close']
            ask = last_price
        else:
            bid = last_price
            ask = last_price
        
        return last_price, bid, ask
    
    def place_order(self, symbol: str, side: str, quantity: int) -> Optional[dict]:
        """Place a market order."""
        account_hash = self.get_account_hash()
        if not account_hash:
            return None
        
        order = {
            'session': 'NORMAL',
            'duration': 'DAY',
            'orderType': 'MARKET',
            'quantity': quantity,
            'symbol': symbol,
            'instruction': side,
        }
        
        response = requests.post(
            f"{self.trader_url}/accounts/{account_hash}/orders",
            headers={**self._get_headers(), 'Content-Type': 'application/json'},
            json=order
        )
        
        if response.status_code in [200, 201]:
            log.trade(f"ORDER PLACED: {side} {quantity} {symbol}")
            return response.json()
        
        log.error(f"Order failed: {response.status_code} {response.text}")
        return None


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical indicators."""
    df = df.copy()
    
    # VWAP (daily reset)
    df['_date'] = df.index.date
    df['CumVol'] = df.groupby('_date')['Volume'].cumsum()
    df['TypicalPrice'] = (df['High'] + df['Low'] + df['Close']) / 3
    df['CumVolPrice'] = (df['Volume'] * df['TypicalPrice']).groupby(df['_date']).cumsum()
    df['VWAP'] = df['CumVolPrice'] / df['CumVol']
    df = df.drop(columns=['CumVol', 'CumVolPrice', 'TypicalPrice', '_date'])
    
    # Bollinger Bands
    df['BB_Mid'] = df['Close'].rolling(20).mean()
    bb_std = df['Close'].rolling(20).std()
    df['BB_Upper'] = df['BB_Mid'] + (Config.BB_STD * bb_std)
    df['BB_Lower'] = df['BB_Mid'] - (Config.BB_STD * bb_std)
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
    
    # EMAs
    df['EMA_Fast'] = df['Close'].ewm(span=9).mean()
    df['EMA_Slow'] = df['Close'].ewm(span=21).mean()
    df['EMA_Trend'] = df['Close'].ewm(span=50).mean()
    
    return df


# ============================================================
# STRATEGIES
# ============================================================

def mean_reversion_signal(row: pd.Series) -> int:
    """Mean Reversion: buy dips, sell rips."""
    if pd.isna(row.get('VWAP')) or pd.isna(row.get('RSI')) or pd.isna(row.get('BB_PctB')):
        return 0
    
    deviation = (row['Close'] - row['VWAP']) / row['VWAP'] * 100
    
    if deviation < -Config.VWAP_DEVIATION and row['RSI'] < Config.RSI_OVERSOLD and row['BB_PctB'] < 0.1:
        return 1
    if deviation > Config.VWAP_DEVIATION and row['RSI'] > Config.RSI_OVERBOUGHT and row['BB_PctB'] > 0.9:
        return -1
    
    return 0


def trend_following_signal(row: pd.Series) -> int:
    """Trend Following: trade pullbacks in trend direction."""
    if pd.isna(row.get('EMA_Fast')) or pd.isna(row.get('EMA_Slow')):
        return 0
    
    uptrend = row['EMA_Fast'] > row['EMA_Slow'] and row['Close'] > row.get('EMA_Trend', row['EMA_Slow'])
    downtrend = row['EMA_Fast'] < row['EMA_Slow'] and row['Close'] < row.get('EMA_Trend', row['EMA_Slow'])
    
    if uptrend and row['Low'] <= row['EMA_Fast'] and row['Close'] >= row['EMA_Fast']:
        return 1
    if downtrend and row['High'] >= row['EMA_Fast'] and row['Close'] <= row['EMA_Fast']:
        return -1
    
    return 0


def swing_signal(row: pd.Series) -> int:
    """Swing: multi-day holds with wider stops."""
    if pd.isna(row.get('EMA_Fast')) or pd.isna(row.get('RSI')):
        return 0
    
    if row['EMA_Fast'] > row['EMA_Slow'] and 40 < row['RSI'] < 60:
        return 1
    if row['EMA_Fast'] < row['EMA_Slow'] and 40 < row['RSI'] < 60:
        return -1
    
    return 0


def get_signal(row: pd.Series, strategy: str) -> int:
    """Get signal based on active strategy."""
    if strategy == 'mean_reversion':
        return mean_reversion_signal(row)
    elif strategy == 'trend_following':
        return trend_following_signal(row)
    elif strategy == 'swing':
        return swing_signal(row)
    return 0


# ============================================================
# RISK MANAGEMENT
# ============================================================

def calculate_position_size(account_value: float, atr: float) -> int:
    """Calculate position size based on risk."""
    risk_amount = account_value * Config.RISK_PER_TRADE
    stop_distance = atr * Config.ATR_SL_MULT
    
    if stop_distance == 0:
        return 1
    
    qty = int(risk_amount / stop_distance)
    return max(1, qty)


def calculate_sl_tp(entry_price: float, atr: float, side: str) -> Tuple[float, float]:
    """Calculate stop loss and take profit prices."""
    stop_distance = atr * Config.ATR_SL_MULT
    
    if side == 'long':
        sl = entry_price - stop_distance
        tp = entry_price + (stop_distance * Config.ATR_TP_MULT / Config.ATR_SL_MULT)
    else:
        sl = entry_price + stop_distance
        tp = entry_price - (stop_distance * Config.ATR_TP_MULT / Config.ATR_SL_MULT)
    
    return round(sl, 2), round(tp, 2)


# ============================================================
# MAIN TRADING BOT
# ============================================================

class TradingBot:
    """Main trading bot class."""
    
    def __init__(self):
        self.api = SchwabAPI()
        self.config = Config
        
        # State
        self.paper_positions: Dict[str, dict] = {}
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.account_value = 1000.0
        self._stop_requested = False
        self._running = False
        self.today = datetime.now().date()
        
        # Strategy
        self.strategy = self.config.STRATEGY
        
        # Ensure authentication
        if not self.api._is_authenticated():
            self.api.authenticate()
        
        # Load account info
        self._update_account()
        
        # Load saved positions (in case of restart)
        self._load_positions()
        
        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()
    
    def _setup_signal_handlers(self):
        """Setup handlers for graceful shutdown."""
        # GUI mode runs in a thread — skip signal handlers (only main thread can)
        if self.config.GUI_MODE:
            return
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGHUP, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        sig_name = signal.Signals(signum).name
        log.warn(f"Received {sig_name} — initiating graceful shutdown...")
        self._close_all_positions()
        log.info("All positions closed. Exiting.")
        sys.exit(0)

    def stop(self):
        """Signal the bot loop to exit on next iteration."""
        self._stop_requested = True

    @property
    def status_dict(self):
        """Return a dict of current bot state (for GUI display)."""
        return {
            'running': self._running if hasattr(self, '_running') else False,
            'strategy': self.strategy,
            'paper_trading': self.config.PAPER_TRADING,
            'tickers': self.config.TICKERS,
            'positions': {k: v for k, v in self.paper_positions.items()},
            'daily_pnl': self.daily_pnl,
            'total_pnl': self.total_pnl,
            'account_value': self.account_value,
            'max_positions': self.config.MAX_POSITIONS,
            'max_daily_loss': self.config.MAX_DAILY_LOSS,
        }
    
    def _close_all_positions(self):
        """Close all open positions at market price."""
        if not self.paper_positions:
            log.info("No positions to close.")
            return
        
        log.warn(f"Closing {len(self.paper_positions)} position(s)...")
        
        for ticker, pos in list(self.paper_positions.items()):
            try:
                # Get latest price
                last_price, _, _ = self.api.get_latest_price(ticker)
                if last_price is None:
                    last_price = pos['entry']
                
                # Calculate P&L
                pnl = self._calc_pnl(pos, last_price)
                self.daily_pnl += pnl
                self.total_pnl += pnl
                
                log.trade(f"[CLOSED] {ticker}: {pos['side']} @ {pos['entry']:.2f} -> {last_price:.2f}, P&L: ${pnl:.2f}")
                
                # If live trading, place closing order
                if not self.config.PAPER_TRADING:
                    close_side = 'SELL' if pos['side'] == 'long' else 'BUY'
                    self.api.place_order(ticker, close_side, pos['qty'])
                
            except Exception as e:
                log.error(f"Error closing {ticker}: {e}")
        
        self.paper_positions.clear()
        self._save_positions()
        log.info(f"All positions closed. Total P&L: ${self.total_pnl:.2f}")
    
    def _save_positions(self):
        """Save positions to file for persistence."""
        # Convert datetime objects to strings for JSON serialization
        positions_copy = {}
        for ticker, pos in self.paper_positions.items():
            pos_copy = pos.copy()
            if 'entry_time' in pos_copy and isinstance(pos_copy['entry_time'], datetime):
                pos_copy['entry_time'] = pos_copy['entry_time'].isoformat()
            positions_copy[ticker] = pos_copy
        
        data = {
            'positions': positions_copy,
            'daily_pnl': self.daily_pnl,
            'total_pnl': self.total_pnl,
            'account_value': self.account_value,
            'date': self.today.isoformat(),
            'timestamp': datetime.now().isoformat(),
        }
        try:
            with open('positions.json', 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save positions: {e}")
    
    def _load_positions(self):
        """Load positions from file (for restart recovery)."""
        if not os.path.exists('positions.json'):
            return
        
        try:
            with open('positions.json', 'r') as f:
                data = json.load(f)
            
            saved_date = data.get('date')
            if saved_date != self.today.isoformat():
                log.info("Saved positions are from old day, clearing.")
                os.remove('positions.json')
                return
            
            positions = data.get('positions', {})
            if positions:
                # Convert entry_time strings back to datetime
                for ticker, pos in positions.items():
                    if 'entry_time' in pos and isinstance(pos['entry_time'], str):
                        pos['entry_time'] = datetime.fromisoformat(pos['entry_time'])
                self.paper_positions = positions
                self.daily_pnl = data.get('daily_pnl', 0.0)
                self.total_pnl = data.get('total_pnl', 0.0)
                log.info(f"Loaded {len(positions)} saved position(s)")
        
        except Exception as e:
            log.error(f"Failed to load positions: {e}")
    
    def _update_account(self):
        """Update account balance."""
        info = self.api.get_account_info()
        if info:
            balances = info.get('securitiesAccount', {}).get('currentBalances', {})
            self.account_value = balances.get('liquidationValue', 1000.0)
            log.info(f"Account Value: ${self.account_value:,.2f}")
    
    def _reset_daily(self):
        """Reset daily tracking."""
        self.today = datetime.now().date()
        self.daily_pnl = 0.0
        self.paper_positions.clear()
        self._save_positions()
        log.info(f"--- New Day: {self.today} ---")
    
    def _check_daily_loss(self) -> bool:
        """Check if daily loss limit hit."""
        limit = -(self.account_value * self.config.MAX_DAILY_LOSS)
        return self.daily_pnl < limit
    
    def _is_daily_loss_limit_hit(self) -> bool:
        """Check if daily loss limit is hit AND we should stop."""
        if self._check_daily_loss():
            return not self.config.RESUME_ON_DAILY_LOSS
        return False
    
    def _check_max_positions(self) -> bool:
        """Check if at max positions."""
        return len(self.paper_positions) >= self.config.MAX_POSITIONS
    
    def _process_ticker(self, ticker: str):
        """Process a single ticker for signals."""
        try:
            # Skip if already in position
            if ticker in self.paper_positions:
                return
            
            # Get price history
            history = self.api.get_price_history(ticker)
            if history is None or history.empty:
                return
            
            # Compute indicators
            df = compute_indicators(history)
            df = df.dropna()
            if df.empty:
                return
            
            latest = df.iloc[-1]
            
            # Generate signal
            signal = get_signal(latest, self.strategy)
            if signal == 0:
                return
            
            # Get current price
            last_price, bid, ask = self.api.get_latest_price(ticker)
            if last_price is None:
                return
            
            # Calculate entry
            if signal == 1:
                side = 'long'
                entry_price = ask
            else:
                side = 'short'
                entry_price = bid
            
            # Calculate position size and SL/TP
            atr = latest['ATR']
            if pd.isna(atr) or atr == 0:
                return
            
            qty = calculate_position_size(self.account_value, atr)
            sl, tp = calculate_sl_tp(entry_price, atr, side)
            
            # Log signal
            log.trade(f"{side.upper()} {qty} {ticker} @ ${entry_price:.2f}")
            log.info(f"  SL: ${sl:.2f} | TP: ${tp:.2f} | ATR: ${atr:.2f}")
            
            # Execute or paper trade
            if not self.config.PAPER_TRADING:
                order = self.api.place_order(ticker, side.upper(), qty)
                if not order:
                    return
            else:
                log.info("  [PAPER TRADE]")
            
            # Track position
            self.paper_positions[ticker] = {
                'side': side, 'qty': qty, 'entry': entry_price,
                'sl': sl, 'tp': tp, 'entry_time': datetime.now()
            }
            self._save_positions()
            
        except Exception as e:
            log.error(f"Error processing {ticker}: {e}")
    
    def _monitor_positions(self):
        """Monitor open positions for exits."""
        to_close = []
        
        for ticker, pos in self.paper_positions.items():
            try:
                # Time-based exit for swing
                if self.strategy == 'swing':
                    hold_time = datetime.now() - pos['entry_time']
                    if hold_time.days >= self.config.SWING_HOLD_DAYS:
                        last_price, _, _ = self.api.get_latest_price(ticker)
                        if last_price:
                            pnl = self._calc_pnl(pos, last_price)
                            log.trade(f"[TIME EXIT] {ticker}: ${last_price:.2f}, P&L: ${pnl:.2f}")
                            self._close_position(ticker, pnl)
                            to_close.append(ticker)
                        continue
                
                # Get current price
                last_price, _, _ = self.api.get_latest_price(ticker)
                if last_price is None:
                    continue
                
                # Check SL/TP
                if pos['side'] == 'long':
                    if last_price <= pos['sl']:
                        pnl = self._calc_pnl(pos, pos['sl'])
                        log.trade(f"[STOP LOSS] {ticker}: ${pos['sl']:.2f}, P&L: ${pnl:.2f}")
                        self._close_position(ticker, pnl)
                        to_close.append(ticker)
                    elif last_price >= pos['tp']:
                        pnl = self._calc_pnl(pos, pos['tp'])
                        log.trade(f"[TAKE PROFIT] {ticker}: ${pos['tp']:.2f}, P&L: ${pnl:.2f}")
                        self._close_position(ticker, pnl)
                        to_close.append(ticker)
                else:  # short
                    if last_price >= pos['sl']:
                        pnl = self._calc_pnl(pos, pos['sl'])
                        log.trade(f"[STOP LOSS] {ticker}: ${pos['sl']:.2f}, P&L: ${pnl:.2f}")
                        self._close_position(ticker, pnl)
                        to_close.append(ticker)
                    elif last_price <= pos['tp']:
                        pnl = self._calc_pnl(pos, pos['tp'])
                        log.trade(f"[TAKE PROFIT] ${ticker}: ${pos['tp']:.2f}, P&L: ${pnl:.2f}")
                        self._close_position(ticker, pnl)
                        to_close.append(ticker)
            
            except Exception as e:
                log.error(f"Error monitoring {ticker}: {e}")
        
        # Clean up closed positions
        for ticker in to_close:
            self.paper_positions.pop(ticker, None)
        if to_close:
            self._save_positions()
    
    def _calc_pnl(self, pos: dict, exit_price: float) -> float:
        """Calculate P&L for a position."""
        if pos['side'] == 'long':
            return (exit_price - pos['entry']) * pos['qty']
        else:
            return (pos['entry'] - exit_price) * pos['qty']
    
    def _close_position(self, ticker: str, pnl: float):
        """Close a position and update P&L."""
        self.daily_pnl += pnl
        self.total_pnl += pnl
        self._save_positions()
        log.info(f"  Daily P&L: ${self.daily_pnl:.2f} | Total P&L: ${self.total_pnl:.2f}")
    
    def _print_status(self):
        """Print status line."""
        now = datetime.now()
        status = f"[{now.strftime('%H:%M:%S')}] "
        
        for ticker in self.config.TICKERS:
            if ticker in self.paper_positions:
                pos = self.paper_positions[ticker]
                status += f"{ticker}:{pos['side'][0].upper()}@{pos['entry']:.0f} "
            else:
                last_price, _, _ = self.api.get_latest_price(ticker)
                if last_price is not None:
                    status += f"{ticker}:${last_price:.0f} "
                else:
                    status += f"{ticker}:? "
        
        log.status(status)
    
    def run(self):
        """Main trading loop."""
        self._running = True
        log.info("=" * 70)
        log.info("TRADING BOT STARTED")
        self._first_loop = True
        log.info(f"Strategy: {self.strategy}")
        log.info(f"Tickers: {self.config.TICKERS}")
        log.info(f"Paper Trading: {self.config.PAPER_TRADING}")
        log.info("=" * 70)
        
        while not self._stop_requested:
            try:
                # Reset daily tracking
                if datetime.now().date() != self.today:
                    self._reset_daily()
                
                # Check daily loss limit
                if self._check_daily_loss():
                    log.warn(f"Daily loss limit hit (${self.daily_pnl:.2f})")
                    if self.config.GUI_MODE:
                        # In GUI mode, auto-continue (don't block on input)
                        log.info("GUI mode: auto-continuing (limit doubled)")
                        self.config.MAX_DAILY_LOSS *= 2
                    else:
                        response = input("Continue trading? (y/n): ").strip().lower()
                        if response == 'y':
                            log.info("User chose to continue trading")
                            self.config.MAX_DAILY_LOSS *= 2
                        else:
                            log.info("User chose to stop trading for today")
                            time.sleep(60)
                            continue

                if self._first_loop:
                    self._first_loop = False
                    log.info("First loop complete — will enter positions on next loop")
                    self._print_status()
                    time.sleep(self.config.LOOP_INTERVAL)
                    continue

                # Process each ticker (max positions check inside loop)
                for ticker in self.config.TICKERS:
                    if self._check_max_positions():
                        break
                    self._process_ticker(ticker)
                
                # Monitor positions
                self._monitor_positions()
                
            except KeyboardInterrupt:
                log.info("\nBot stopped by user.")
                log.info(f"Final Daily P&L: ${self.daily_pnl:.2f}")
                log.info(f"Total Session P&L: ${self.total_pnl:.2f}")
                break
            
            except Exception as e:
                log.error(f"Unexpected error: {e}")
                time.sleep(60)
            
            # Print status (always runs)
            self._print_status()
            time.sleep(self.config.LOOP_INTERVAL)
        
        self._running = False
        log.info("Trading bot stopped.")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == '__main__':
    bot = TradingBot()
    bot.run()
