"""GlassTB — trading bot + GUI (Flask + pywebview).

Cross-platform: WebKitGTK (Linux), WebKit (macOS), Edge (Windows).
Flask serves a dark dashboard; pywebview opens a native window.
The JS frontend polls /api/scene every 3s for live data + bot state.
TradingBot runs in a background thread; GUI shows Start/Stop + trade log.
"""
import sys
import os

# ── import fix: system python3 finds flask + webview from venv ──
VENV_SITE_PACKAGES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'venv', 'lib', 'python3.14', 'site-packages'
)
if os.path.isdir(VENV_SITE_PACKAGES):
    sys.path.insert(0, VENV_SITE_PACKAGES)

import json
import time
import signal
import atexit
import logging
import threading
from datetime import datetime

from flask import Flask, Response, jsonify

flask_logger = logging.getLogger('werkzeug')
flask_logger.setLevel(logging.ERROR)

APP = Flask(__name__, static_folder=None)
APP.debug = False
APP.config['PROPAGATE_EXCEPTIONS'] = False

TICKERS = ['/YM', '/GC', '/ES', '/NQ', '/CL', '/SI']

# ── paths (relative to THIS file) ──
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
TOKENS_FILE = os.path.join(BASE, 'tokens.json')
POSITIONS_FILE = os.path.join(BASE, 'positions.json')
DASHBOARD_FILE = os.path.join(HERE, 'dashboard.html')
STYLES_FILE = os.path.join(HERE, 'styles.css')
TRADE_LOG_FILE = os.path.join(BASE, 'trade_log.json')

# ── application state ──
state = {
    'app':          None,
    'templates':    {'style': ''},
    'shell':        '',
    'timer':        None,
    'up_since':     None,
    'uptime':       0.0,
    'cash':         0.0,
    'bp':           0.0,
    'value':        0.0,
    'daily_pnl':    0.0,
    'total_pnl':    0.0,
    'timestamp':    '',
    'prices':       {},
    'history':      {},
    'max_price':    0.0,
    'refresh_count': 0,
    'positions':    [],
    'trade_log':    [],
    'bot_running':  False,
    'bot_status':   'idle',     # idle | starting | running | stopping | error
    'bot_strategy': 'mean_reversion',
    'bot_paper':    True,
    'bot_error':    '',
}
WINDOW = None
BOT_THREAD = None
BOT_INSTANCE = None
_shutdown = False

# ── helpers ──

def signed(v):
    s = '+' if v >= 0 else ''
    return f'{s}{v:,.2f}'

def pnl_cls(v):
    return 'pos' if v >= 0 else 'neg'

def fmt_time(ts=None):
    if ts is None:
        ts = time.time()
    try:
        return datetime.fromtimestamp(float(ts)).strftime('%H:%M:%S')
    except Exception:
        return '—'

def fmt_ts_short(ts=None):
    if ts is None:
        ts = time.time()
    try:
        return datetime.fromtimestamp(float(ts)).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return '—'

# ── token loading ──

def load_token():
    if not os.path.isfile(TOKENS_FILE):
        return {}
    try:
        with open(TOKENS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

# ── trade log persistence ──

def load_trade_log():
    if not os.path.isfile(TRADE_LOG_FILE):
        return []
    try:
        with open(TRADE_LOG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

def save_trade_log(log):
    try:
        with open(TRADE_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(log[-500:], f)  # keep last 500
    except Exception:
        pass

def append_trade(entry):
    log = state.get('trade_log', [])
    log.append(entry)
    state['trade_log'] = log[-200:]  # keep last 200 in memory
    save_trade_log(log)

# ── Schwab data fetch ──

def _parse_price_response(resp):
    if not isinstance(resp, dict):
        return 0, []
    candles = resp.get('candles')
    if isinstance(candles, list) and candles:
        prices = [c.get('close') or c.get('high') or c.get('low') or 0 for c in candles]
        prices = [p for p in prices if isinstance(p, (int, float)) and p > 0]
        if not prices:
            return 0, []
        return prices[0], prices
    latest = resp.get('latest', resp.get('candle'))
    if isinstance(latest, dict):
        for k in ('close', 'high', 'low', 'open', 'adjClose', 'closePrice'):
            v = latest.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return v, [v]
    return 0, []

def fetch_data_sync():
    """Refresh state from tokens.json + Schwab API."""
    import requests as req

    tokens = load_token()
    if not tokens:
        return state

    access = tokens.get('access_token')
    if not access:
        return state

    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {access}',
    }

    acct_hash = tokens.get('hashValue') or tokens.get('account_hash')
    total_cash = 0.0
    total_bp = 0.0
    total_value = 0.0
    positions = []

    if acct_hash:
        try:
            r = req.get(
                f'https://api.schwabapi.com/trader/v1/accounts/{acct_hash}',
                headers=headers, params={'fields': 'portfolio'},
                timeout=10
            )
            if r.status_code == 200:
                body = r.json()
                for sec in (body.get('securities') or []):
                    ln = sec.get('currentDaysHoldQuantity') or sec.get('longQuantity') or 0
                    sh = sec.get('shortQuantity') or 0
                    qty = (ln or 0) - (sh or 0)
                    cur = sec.get('currentValue') or 0
                    avg = sec.get('averagePrice') or 0
                    if qty != 0 and cur:
                        positions.append({
                            'symbol': sec.get('symbol') or sec.get('instrument') or '—',
                            'side': 'long' if qty > 0 else 'short',
                            'qty': abs(qty),
                            'avg_entry': avg,
                            'current_value': cur,
                            'pnl': cur - (avg * abs(qty)),
                        })
                    total_value += cur or 0
        except Exception:
            pass

        for key in ('settlementFunds', 'cashAvailableForTrading',
                    'cashAvailableForWithdrawal', 'availableFunds',
                    'buyingPower', 'buyingPowerAmount'):
            try:
                r = req.get(
                    f'https://api.schwabapi.com/trader/v1/accounts/{acct_hash}',
                    headers=headers, params={'fields': key},
                    timeout=10
                )
                if r.status_code == 200:
                    body = r.json()
                    def dig(o):
                        if isinstance(o, dict):
                            for k, v in o.items():
                                if k in ('settlementFunds', 'cashAvailableForTrading',
                                         'cashAvailableForWithdrawal', 'availableFunds',
                                         'buyingPower', 'buyingPowerAmount'):
                                    if isinstance(v, (int, float)):
                                        return float(v)
                                res = dig(v)
                                if res is not None:
                                    return res
                        elif isinstance(o, list):
                            for item in o:
                                res = dig(item)
                                if res is not None:
                                    return res
                        return None
                    f = dig(body)
                    if f is not None and f > 0:
                        total_cash = max(total_cash, f)
            except Exception:
                pass
        total_bp = total_cash * 4.0

    total_pnl = 0.0
    for p in positions:
        entry = p.get('avg_entry', 0)
        cur = p.get('current_value', 0)
        q = p.get('qty', 0)
        if entry and q:
            p['pnl'] = (cur / q - entry) * q if q else 0
        total_pnl += p.get('pnl', 0)

    prices = {}
    history = {}
    max_price = 0.0

    for ticker in TICKERS:
        try:
            r = req.get(
                'https://api.schwabapi.com/marketdata/v1/pricehistory',
                headers=headers,
                params={
                    'symbol': ticker,
                    'interval': 'ONE_MIN',
                    'maxRecords': 20,
                    'periodType': 'day',
                    'period': 1,
                    'extendTradingHours': 'false',
                    'includeAdjustedClose': 'false',
                },
                timeout=10
            )
            if r.status_code == 200:
                lp, hist = _parse_price_response(r.json())
                if lp and lp > 0:
                    prices[ticker] = lp
                    history[ticker] = hist[-16:]
                    if lp > max_price:
                        max_price = lp
        except Exception:
            pass

    if max_price == 0:
        max_price = 1.0

    state['cash'] = total_cash
    state['bp'] = total_bp
    state['value'] = total_value
    state['daily_pnl'] = total_pnl
    state['total_pnl'] = total_pnl
    state['positions'] = positions
    state['prices'] = prices
    state['history'] = history
    state['max_price'] = max_price
    state['timestamp'] = fmt_time()
    return state

# ── data refresh loop (runs on main thread via threading.Timer) ──

def tick():
    """Called every 3s; refresh data, schedule next."""
    global _shutdown
    if _shutdown:
        return
    s = state
    if s.get('up_since') is None:
        s['up_since'] = time.perf_counter()
    s['uptime'] = time.perf_counter() - s['up_since']
    s['timer'] = time.time()
    s['timestamp'] = fmt_time()
    fetch_data_sync()
    s['refresh_count'] += 1
    # schedule next
    t = threading.Timer(3.0, tick)
    t.daemon = True
    t.start()

# ── bot control ──

def start_bot():
    """Start TradingBot in a background thread."""
    global BOT_THREAD, BOT_INSTANCE
    if state['bot_running']:
        return {'ok': False, 'error': 'Bot already running'}
    try:
        sys.path.insert(0, str(BASE))
        from trading_bot import TradingBot, Config
        Config.GUI_MODE = True
        Config.PAPER_TRADING = state.get('bot_paper', True)
        Config.STRATEGY = state.get('bot_strategy', 'mean_reversion')
        bot = TradingBot()
        BOT_INSTANCE = bot
        state['bot_running'] = True
        state['bot_status'] = 'running'
        state['bot_error'] = ''
        t = threading.Thread(target=bot.run, daemon=True)
        t.start()
        BOT_THREAD = t
        return {'ok': True}
    except Exception as e:
        state['bot_status'] = 'error'
        state['bot_error'] = str(e)
        return {'ok': False, 'error': str(e)}

def stop_bot():
    """Signal TradingBot to stop."""
    global BOT_INSTANCE
    if not state['bot_running']:
        return {'ok': False, 'error': 'Bot not running'}
    try:
        if BOT_INSTANCE is not None:
            BOT_INSTANCE.stop()
        state['bot_status'] = 'stopping'
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

# ── HTML builders ──

def price_html(ticker, price, max_price):
    """Render a Symbol/Price/Chart row for one ticker."""
    px = price or 0
    is_candle = ticker in ('/GC', '/ES')
    parts = []

    if is_candle:
        # OHLC candlestick for /GC and /ES
        hist_vals = state.get('history', {}).get(ticker, [])[-5:]
        if len(hist_vals) >= 2:
            o = hist_vals[0]
            h = max(hist_vals)
            l = min(hist_vals)
            c = hist_vals[-1]
        else:
            o = px
            h = px * 1.002
            l = px * 0.998
            c = px

        rng = max(h - l, px * 0.001, 0.01)
        scale = 26.0 / rng

        body_h = max(3, abs(c - o) * scale)
        body_top = (min(o, c) - l) * scale
        wick_top = (h - l) * scale
        wick_bot = (l - l) * scale  # = 0

        wick_css = 'top:' + str(max(0, wick_top)) + 'px;'
        body_css = 'top:' + str(max(0, body_top)) + 'px;height:' + str(max(3, body_h)) + 'px;'

        parts.append('<td>' + ticker + '</td>')
        parts.append('<td>$' + f'{px:,.2f}' + '</td>')
        parts.append('<td><span class="candle">')
        parts.append('<span class="wick" style="' + wick_css + '"></span>')
        parts.append('<span class="cbody" style="' + body_css + '"></span>')
        parts.append('</span></td>')
    else:
        # Horizontal bar for /YM /NQ /CL /SI
        # Use log scale so small-priced assets still show visible bars
        import math
        if px > 0:
            # log scale: map price to 5-74px range
            log_px = math.log10(max(px, 1))
            log_max = math.log10(max(max_price, 10))
            log_min = log_max - 2.0  # 2 orders of magnitude range
            pct = max(0.05, min(0.98, (log_px - log_min) / (log_max - log_min)))
            bar_w = max(4, int(74 * pct))
        else:
            bar_w = 4

        parts.append('<td>' + ticker + '</td>')
        parts.append('<td>$' + f'{px:,.2f}' + '</td>')
        parts.append('<td><span class="bar-track"><span class="bar-fill" style="width:' + str(bar_w) + 'px"></span></span></td>')
    return ''.join(parts)

def positions_html(positions):
    if not positions:
        return '<div class="pos-box"><span class="pos-dot"></span>No open positions</div>'
    parts = []
    for p in positions:
        sym = p.get('symbol', '—')
        side = p.get('side', 'long')
        qty = p.get('qty', 0)
        entry = p.get('avg_entry', 0)
        pnl = p.get('pnl', 0)
        parts.append(
            '<div class="pos-line">'
            '<span class="pos-ticker">' + sym + '</span>'
            '<span class="pos-side">' + side + '</span>'
            '<span class="pos-qty">' + f'{qty:.6g}' + '</span>'
            '<span class="pos-entry">$' + f'{entry:,.2f}' + '</span>'
            '<span class="pos-pnl pos-pnl-' + pnl_cls(pnl) + '">' + signed(pnl) + '</span>'
            '</div>'
        )
    return '<div class="pos-box">' + ''.join(parts) + '</div>'

def bot_status_html():
    """Render the bot control + status panel."""
    st = state
    running = st['bot_running']
    status = st['bot_status']
    strategy = st['bot_strategy']
    paper = st['bot_paper']
    err = st['bot_error']

    if running:
        btn_text = 'STOP BOT'
        btn_class = 'btn btn-stop'
        status_label = 'RUNNING'
        status_class = 'bot-running'
    elif status == 'stopping':
        btn_text = 'STOPPING...'
        btn_class = 'btn btn-stop disabled'
        status_label = 'STOPPING'
        status_class = 'bot-stopping'
    else:
        btn_text = 'START BOT'
        btn_class = 'btn btn-start'
        status_label = 'STOPPED'
        status_class = 'bot-stopped'

    paper_label = 'PAPER' if paper else 'LIVE'
    disabled = ' disabled' if status == 'stopping' else ''

    parts = []
    parts.append('<div class="box bot-control-panel">')
    parts.append('<div class="bot-control-top">')
    parts.append('<span class="bot-status-label ' + status_class + '">' + status_label + '</span>')
    parts.append('<span class="bot-mode-label">' + paper_label + '</span>')
    parts.append('<button class="' + btn_class + '"' + disabled + ' onclick="toggleBot()">' + btn_text + '</button>')
    parts.append('</div>')
    parts.append('<div class="bot-control-info">')
    parts.append('<span class="metric"><span class="label">Strategy:</span> <span class="value">' + strategy + '</span></span>')
    parts.append('<span class="metric"><span class="label">Mode:</span> <span class="value">' + paper_label + '</span></span>')
    parts.append('</div>')
    if err:
        parts.append('<div class="bot-error">' + err + '</div>')
    parts.append('</div>')
    return ''.join(parts)

def trade_log_html():
    """Render the trade log panel."""
    log = state.get('trade_log', [])
    parts = []
    if not log:
        parts.append('<div class="box">')
        parts.append('<div class="market-header">TRADE LOG</div>')
        parts.append('<div class="trade-log-empty">No trades yet</div>')
        parts.append('</div>')
        return ''.join(parts)
    parts.append('<div class="box">')
    parts.append('<div class="market-header">TRADE LOG</div>')
    parts.append('<div class="trade-log">')
    for entry in log[-50:]:
        ts = entry.get('time', '')
        sym = entry.get('symbol', '—')
        side = entry.get('side', '—')
        qty = entry.get('qty', 0)
        price = entry.get('price', 0)
        pnl = entry.get('pnl', 0)
        reason = entry.get('reason', '')
        pnl_html = ' <span class="trade-pnl-' + pnl_cls(pnl) + '">' + signed(pnl) + '</span>' if pnl else ''
        parts.append(
            '<div class="trade-line">'
            '<span class="trade-time">' + ts + '</span>'
            '<span class="trade-symbol">' + sym + '</span>'
            '<span class="trade-side trade-side-' + side + '">' + side.upper() + '</span>'
            '<span class="trade-qty">' + str(qty) + '</span>'
            '<span class="trade-price">$' + f'{price:,.2f}' + '</span>'
            '<span class="trade-reason">' + reason + '</span>'
            + pnl_html
            + '</div>'
        )
    parts.append('</div>')
    parts.append('</div>')
    return ''.join(parts)

def scene_html():
    """Return the dashboard fragment HTML for the current state."""
    st = state
    bot_html = bot_status_html()
    log_html = trade_log_html()

    parts = []
    parts.append('<div class="container">')
    parts.append(bot_html)
    parts.append('<div class="box bot-panel">')
    parts.append('<div class="bot-title-row">')
    parts.append('<span class="bot-title">GLASS TB</span>')
    parts.append('<span class="bot-metrics bot-pnl-' + pnl_cls(st['daily_pnl']) + '">')
    parts.append('<span class="metric"><span class="label">Cash:</span> <span class="value">'
                 + f"${st['cash']:,.2f}" + '</span></span>')
    parts.append('<span class="metric"><span class="label">BP:</span> <span class="value">'
                 + f"${st['bp']:,.2f}" + '</span></span>')
    parts.append('<span class="metric"><span class="label">Value:</span> <span class="value">'
                 + f"${st['value']:,.2f}" + '</span></span>')
    parts.append('<span class="metric"><span class="label">Day PnL:</span> <span class="value">'
                 + signed(st['daily_pnl']) + '</span></span>')
    parts.append('</span>')
    parts.append('</div>')
    parts.append('<div class="bot-second-row">')
    parts.append('<span class="pnl-label">Total PnL:</span>')
    parts.append('<span class="pnl-' + pnl_cls(st['total_pnl']) + '">' + signed(st['total_pnl']) + '</span>')
    parts.append('<span class="ts">' + st['timestamp'] + '</span>')
    parts.append('</div>')
    parts.append('</div>')
    parts.append('<div class="box">')
    parts.append('<div class="market-header">BID/ASK</div>')
    parts.append('<table class="price-table"><thead><tr><th>Symbol</th><th>Price</th><th>Chart</th></tr></thead>')
    parts.append('<tbody>')
    for t in TICKERS:
        parts.append('<tr>' + price_html(t, st['prices'].get(t), st['max_price']) + '</tr>')
    parts.append('</tbody></table>')
    parts.append('</div>')
    parts.append('<div class="box">')
    parts.append('<div class="pos-header">POSITIONS</div>')
    parts.append(positions_html(st['positions']))
    parts.append('</div>')
    parts.append(log_html)
    parts.append('</div>')
    return ''.join(parts)

# ── Flask routes ──

@APP.route('/')
def index():
    shell = state.get('shell', '')
    if shell:
        return Response(shell, mimetype='text/html; charset=utf-8')
    return Response(FALLBACK_SHELL, mimetype='text/html; charset=utf-8')

@APP.route('/api/scene')
def scene():
    return Response(scene_html(), mimetype='text/html; charset=utf-8')

@APP.route('/api/bot/start', methods=['POST'])
def bot_start():
    result = start_bot()
    return jsonify(result)

@APP.route('/api/bot/stop', methods=['POST'])
def bot_stop():
    result = stop_bot()
    return jsonify(result)

@APP.route('/api/bot/status')
def bot_status():
    return jsonify({
        'running': state['bot_running'],
        'status': state['bot_status'],
        'strategy': state['bot_strategy'],
        'paper': state['bot_paper'],
        'error': state['bot_error'],
    })

# ── fallback shell ──
FALLBACK_SHELL = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>GlassTB</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a12;color:#a0b0c8;font-family:"DejaVu Sans Mono",monospace;font-size:13px;line-height:1.45;padding:10px;min-height:100vh;display:flex;flex-direction:column;align-items:center;user-select:none}
.container{width:100%;max-width:800px;display:flex;flex-direction:column;gap:6px}
.box{border:1px solid #5a7a9a;background:#0c0c16;border-radius:2px;padding:5px 10px}
.status-bar{text-align:center;font-size:11px;color:#7a8a9a;letter-spacing:0.5px;display:flex;align-items:center;justify-content:center;gap:5px;padding:0 4px}
.cursor{display:inline-block;width:7px;height:13px;background:#fff;opacity:0.85;flex-shrink:0}
.top-box{text-align:center;font-size:14px;color:#8aa0b8;padding:5px 10px}
.bot-title-row{display:flex;align-items:baseline;flex-wrap:wrap;gap:10px;font-size:13px}
.bot-title{color:#7ec8e3;font-weight:bold;letter-spacing:1px;font-size:14px;white-space:nowrap}
.bot-metrics{display:flex;flex-wrap:wrap;gap:10px;font-size:13px;color:#8aa0b8}
.metric{white-space:nowrap}
.metric .label{color:#6a8098}
.metric .value{color:#7ec8e3;font-weight:bold}
.bot-pnl-pos .value{color:#7ec8e3}
.bot-pnl-neg .value{color:#e06c75}
.bot-second-row{display:flex;align-items:baseline;flex-wrap:wrap;gap:10px;font-size:13px;color:#6a8098;margin-top:2px}
.pnl-label{color:#6a8098}
.pnl-pos{color:#7ec8e3;font-weight:bold}
.pnl-neg{color:#e06c75;font-weight:bold}
.ts{color:#5a6a7a;font-size:11px;margin-left:auto}
.market-header{text-align:center;color:#7ec8e3;font-weight:bold;font-size:14px;letter-spacing:2px;margin-bottom:3px}
table.price-table{width:100%;border-collapse:collapse;font-size:12px}
table.price-table th{color:#7ec8e3;text-align:left;padding:4px 8px;font-weight:bold;letter-spacing:1px;font-size:11px;border-bottom:1px solid #3a5a7a}
table.price-table th:nth-child(2),table.price-table td:nth-child(2){text-align:right}
table.price-table th:nth-child(3),table.price-table td:nth-child(3){text-align:center;width:120px}
table.price-table td{padding:4px 8px;border-bottom:1px solid #1a2a3a;color:#8aa0b8}
table.price-table tbody tr:last-child td{border-bottom:none}
.bar-track{display:inline-block;height:11px;background:#14142a;border:1px solid #3a5a7a;border-radius:1px;width:80px;position:relative;overflow:hidden}
.bar-fill{display:block;height:100%;background:#7ec8e3;transition:width 0.5s}
.candle{display:inline-block;position:relative;width:16px;height:30px}
.wick{position:absolute;left:50%;transform:translateX(-50%);width:1px;background:#7ec8e3;top:0;bottom:0}
.cbody{position:absolute;left:1px;right:1px;background:#7ec8e3;min-height:2px}
.pos-header{text-align:center;color:#7ec8e3;font-weight:bold;font-size:14px;letter-spacing:2px;position:relative;padding-bottom:3px;margin-bottom:1px}
.pos-header::after{content:'';position:absolute;bottom:0;left:5%;right:5%;height:1px;background:#3a5a7a}
.pos-box{margin-top:2px;display:flex;align-items:center;justify-content:center;gap:6px;font-size:13px;color:#6a8098}
.pos-dot{display:inline-block;width:5px;height:11px;background:#3a5a7a;opacity:0.6;flex-shrink:0}
.bot-control-panel{display:flex;flex-direction:column;gap:4px}
.bot-control-top{display:flex;align-items:center;gap:10px}
.bot-status-label{font-weight:bold;font-size:12px;letter-spacing:1px;padding:2px 8px;border-radius:2px}
.bot-running{color:#0a0a12;background:#7ec8e3}
.bot-stopped{color:#0a0a12;background:#6a8098}
.bot-stopping{color:#0a0a12;background:#e0a060}
.bot-mode-label{font-size:11px;color:#7a8a9a;margin-left:auto}
.btn{padding:4px 14px;border:1px solid #5a7a9a;border-radius:2px;background:#0c0c16;color:#7ec8e3;font-family:inherit;font-size:12px;font-weight:bold;letter-spacing:1px;cursor:pointer}
.btn:hover{background:#1a2a3a}
.btn-start{border-color:#7ec8e3;color:#7ec8e3}
.btn-stop{border-color:#e06c75;color:#e06c75}
.btn.disabled{opacity:0.5;cursor:not-allowed}
.bot-control-info{display:flex;wrap:wrap;gap:10px;font-size:12px}
.bot-error{color:#e06c75;font-size:11px;padding:4px 0}
.trade-log{display:flex;flex-direction:column;gap:1px;font-size:11px;max-height:200px;overflow-y:auto}
.trade-line{display:flex;align-items:center;gap:8px;padding:2px 0;border-bottom:1px solid #1a2a3a}
.trade-time{color:#5a6a7a;width:60px;flex-shrink:0}
.trade-symbol{color:#7ec8e3;width:50px;flex-shrink:0;font-weight:bold}
.trade-side{width:40px;flex-shrink:0;text-align:center;border-radius:1px}
.trade-side-long{color:#7ec8e3}
.trade-side-short{color:#e06c75}
.trade-qty{width:40px;text-align:right;color:#8aa0b8;flex-shrink:0}
.trade-price{width:80px;text-align:right;color:#8aa0b8;flex-shrink:0}
.trade-reason{color:#5a6a7a;flex:1}
.trade-pnl-pos{color:#7ec8e3}
.trade-pnl-neg{color:#e06c75}
.trade-log-empty{color:#6a8098;text-align:center;padding:10px 0}
</style>
</head>
<body>
<div class="container" id="root"></div>
<script>
(function(){
  var INTERVAL_MS=3000;
  function sleep(ms){return new Promise(function(r){setTimeout(r,ms)})}
  function fetchScene(){return fetch("/api/scene",{headers:{"Accept":"text/html"}}).then(function(r){return r.text()}).catch(function(){return""})}
  function render(){fetchScene().then(function(html){var root=document.getElementById("root");if(root&&html)root.innerHTML=html})}
  function loop(){render();return sleep(INTERVAL_MS).then(function(){return loop()})}
  loop();
  window.toggleBot=function(){
    var btn=document.querySelector('.btn-start, .btn-stop');
    if(!btn||btn.disabled)return;
    var isStart=btn.classList.contains('btn-start');
    fetch('/api/bot/'+(isStart?'start':'stop'),{method:'POST'})
      .then(function(r){return r.json()})
      .then(function(d){if(!d.ok&&d.error)alert('Bot: '+d.error)})
      .catch(function(e){alert('Request failed: '+e)});
  };
})();
</script>
</body>
</html>'''

# ── server runner ──

def _resolve_port():
    import socket
    for port in range(5000, 5200):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.2)
        try:
            s.bind(('127.0.0.1', port))
            s.close()
            return port
        except OSError:
            continue
    return 5000

_PORT = _resolve_port()

def _run_flask():
    APP.run(
        host='127.0.0.1',
        port=_PORT,
        threaded=True,
        use_reloader=False,
        debug=False,
    )

# ── launch ──

def _quit():
    global _shutdown, WINDOW, BOT_INSTANCE
    _shutdown = True
    state['bot_running'] = False
    state['bot_status'] = 'idle'
    if BOT_INSTANCE is not None:
        try:
            BOT_INSTANCE.stop()
        except Exception:
            pass
    if WINDOW is not None:
        try:
            import webview
            webview.destroy(WINDOW)
        except Exception:
            pass
    sys.exit(0)

def main():
    global WINDOW
    import webview

    # load shell
    if os.path.isfile(DASHBOARD_FILE):
        with open(DASHBOARD_FILE, 'r', encoding='utf-8') as f:
            state['shell'] = f.read()
    if os.path.isfile(STYLES_FILE):
        with open(STYLES_FILE, 'r', encoding='utf-8') as f:
            state['templates']['style'] = f.read()

    # load trade log
    state['trade_log'] = load_trade_log()

    # start Flask in background
    flask_thread = threading.Thread(target=_run_flask, daemon=True)
    flask_thread.start()
    time.sleep(0.4)

    # start data refresh loop
    tick()

    # open native window
    url = f'http://127.0.0.1:{_PORT}/'
    WINDOW = webview.create_window(
        'GlassTB',
        url,
        width=900,
        height=700,
        resizable=True,
        text_select=False,
        confirm_close=False,
        fullscreen=False,
        min_size=(640, 480),
    )
    webview.start(debug=False)

if __name__ == '__main__':
    signal.signal(signal.SIGINT, lambda sig, frame: _quit())
    signal.signal(signal.SIGTERM, lambda sig, frame: _quit())
    atexit.register(_quit)
    print('GlassTB starting - window: GlassTB')
    print(f'Refresh interval: 3s')
    print('Press Ctrl+C to quit.')
    sys.stdout.flush()
    main()
