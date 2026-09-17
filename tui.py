#!/usr/bin/env python3
"""
Trading Bot TUI — real-time dashboard.

Layout (top → bottom):
  1. Top box: "No open positions" or position summary
  2. Status bar: refresh / uptime / quit
  3. SCALPING BOT panel: title + 2-line body
  4. BID/ASK table: Symbol | Price | Chart | 7c
  5. POSITIONS panel: header + box
  6. Status bar (bottom)

Dark background, thin pale borders, light text, green PnL.
"""

import json, os, time, sys, requests
from datetime import datetime
from rich.console import Console, Group
from rich.text import Text
from rich.table import Table
from rich.box import ROUNDED, SQUARE
from rich.panel import Panel
from rich.live import Live

# ── config ──────────────────────────────────────────────────────────────

TICKERS = ['/YM', '/GC', '/ES', '/NQ', '/CL', '/SI']
M = 'https://api.schwabapi.com/marketdata/v1'
TR = 'https://api.schwabapi.com/trader/v1'

TOKENS_FILE = 'tokens.json'
POSITIONS_FILE = 'positions.json'
PRICES_CACHE = 'current_prices_cache.json'
HIST_CACHE = 'historical_cache.json'
TRADE_LOG = 'trade_log.json'

REFRESH_INTERVAL = 3  # seconds

# palette
WHITE      = 'white'
LIGHT_GRAY = 'bright_black'
LIGHT_BLUE = 'bright_blue'
LIGHT_CYAN = 'cyan'
DIM_GRAY   = 'grey50'
GREEN      = 'green'
RED        = 'red'

# ── token / api ─────────────────────────────────────────────────────────

def load_token():
    if os.path.exists(TOKENS_FILE):
        try:
            with open(TOKENS_FILE) as f:
                return json.load(f).get('access_token')
        except Exception:
            pass
    return None


def hdr(token):
    return {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}


def get_prices(token):
    prices = {}
    candles_by_ticker = {}
    for t in TICKERS:
        try:
            r = requests.get(
                M + '/pricehistory',
                headers=hdr(token),
                params={
                    'symbol': t,
                    'periodType': 'day',
                    'period': 1,
                    'frequencyType': 'minute',
                    'frequency': 1,
                },
                timeout=5,
            )
            if r.status_code == 200:
                c = r.json().get('candles', [])
                prices[t] = c[-1]['close'] if c else None
                candles_by_ticker[t] = c
            else:
                prices[t] = None
                candles_by_ticker[t] = []
        except Exception:
            prices[t] = None
            candles_by_ticker[t] = []
    return prices, candles_by_ticker


def get_account(token):
    res = {'cash': 0.0, 'bp': 0.0, 'value': 0.0}
    try:
        r = requests.get(
            TR + '/accounts/accountNumbers',
            headers=hdr(token), timeout=5,
        )
        if r.status_code == 200:
            a = r.json()
            if a:
                h = a[0]['hashValue']
                r2 = requests.get(
                    TR + '/accounts/' + h,
                    headers=hdr(token),
                    params={'fields': 'positions'},
                    timeout=5,
                )
                if r2.status_code == 200:
                    b = r2.json()['securitiesAccount']['currentBalances']
                    res['cash'] = b.get('cashBalance', 0)
                    res['bp'] = b.get('buyingPower', 0)
                    res['value'] = b.get('liquidationValue', 0)
    except Exception:
        pass
    return res


def get_positions_from_file():
    if os.path.exists(POSITIONS_FILE):
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    return {'positions': {}, 'daily_pnl': 0.0, 'total_pnl': 0.0}


# ── candle bar ──────────────────────────────────────────────────────────

def candle_bar(p, max_price):
    """Horizontal green bar scaled to price/max_price."""
    if max_price <= 0:
        return Text('-', style=LIGHT_GRAY)
    ratio = min(1.0, p / max_price) if max_price > 0 else 0.0
    width = max(1, int(ratio * 22))
    return Text('\u2588' * width, style=GREEN)


# ── panels ──────────────────────────────────────────────────────────────

def build_top_box(positions):
    """Top thin-bordered box: 'No open positions' or position summary."""
    if not positions:
        text = Text('No open positions', style=LIGHT_GRAY, justify='left')
    else:
        lines = []
        for ticker, pos in positions.items():
            side = pos.get('side', '?').upper()
            qty = pos.get('qty', 0)
            entry = pos.get('entry', 0)
            lines.append(f"{ticker}  {side}  {qty}@{entry:.2f}")
        text = Text('\n'.join(lines), style=LIGHT_GRAY)
    return Panel(text, border_style=LIGHT_GRAY, padding=(0, 1))


def build_status_bar(uptime):
    s = (
        f'  refresh in {REFRESH_INTERVAL}s'
        f'   \u2022   uptime {uptime:.0f}s'
        f'   \u2022   ctrl+c quit  '
    )
    return Panel(
        Text(s, style=LIGHT_GRAY),
        border_style=LIGHT_GRAY,
        padding=(0, 1),
    )


def build_scalping_bot_panel(account, dpnl, tpnl, now):
    """SCALPING BOT panel: title + 2-line body.

    Line 1: Cash BP Value Day PnL  (PnL value coloured)
    Line 2: Total PnL  timestamp            (PnL value coloured)
    """
    dnl_style = GREEN if dpnl >= 0 else RED
    tnl_style = GREEN if tpnl >= 0 else RED

    line1 = Text.assemble(
        Text(f'Cash: ${account["cash"]:,.2f}  ', style=LIGHT_GRAY),
        Text(f'BP: ${account["bp"]:,.2f}  ', style=LIGHT_GRAY),
        Text(f'Value: ${account["value"]:,.2f}  ', style=LIGHT_GRAY),
        Text('Day PnL: ', style=LIGHT_GRAY),
        Text(f'{dpnl:+,.2f}', style=dnl_style),
    )
    line2 = Text.assemble(
        Text('Total PnL: ', style=LIGHT_GRAY),
        Text(f'{tpnl:+,.2f}', style=tnl_style),
        Text(f'  {now.strftime("%Y-%m-%d %H:%M:%S")}', style=DIM_GRAY),
    )

    return Panel(
        Group(line1, line2),
        title=Text('SCALPING BOT', style=LIGHT_BLUE),
        border_style=LIGHT_GRAY,
        padding=(0, 1),
    )


def build_bid_ask_table(prices, candles_by_ticker):
    max_price = max((p for p in prices.values() if p is not None), default=1.0)
    title = Text('BID/ASK', style=LIGHT_BLUE)
    t = Table(show_header=True, header_style=LIGHT_BLUE,
              box=ROUNDED, title=title, border_style=LIGHT_GRAY,
              pad_edge=False)
    t.add_column('Symbol', style=LIGHT_CYAN, justify='center')
    t.add_column('Price', justify='right', style=LIGHT_GRAY)
    t.add_column('Chart', justify='center', style=LIGHT_GRAY)
    t.add_column('7c', justify='center', style=LIGHT_GRAY)

    for ticker in TICKERS:
        p = prices.get(ticker)
        if p is None:
            bar = Text('-', style=LIGHT_GRAY)
            price_str = 'N/A'
        else:
            price_str = f'${p:,.2f}'
            bar = candle_bar(p, max_price)
        t.add_row(ticker, price_str, bar, '-')
    return t


def build_positions_panel(positions):
    """POSITIONS panel: title + box with '-' row + content."""
    if not positions:
        body = Text('No open positions', style=LIGHT_GRAY, justify='center')
    else:
        lines = []
        for ticker, pos in positions.items():
            side = pos.get('side', '?').upper()
            qty = pos.get('qty', 0)
            entry = pos.get('entry', 0)
            lines.append(f"{ticker:6s} {side:6s} {qty:5d} {entry:>10.2f}")
        body = Text('\n'.join(lines) if lines else 'No open positions',
                    style=LIGHT_GRAY)
    inner = Table(box=SQUARE, show_header=False, border_style=LIGHT_GRAY,
                  pad_edge=False, width=40)
    inner.add_column('.', style=DIM_GRAY, justify='left')
    inner.add_row('-')
    inner.add_row(body)
    return Panel(inner, title=Text('POSITIONS', style=LIGHT_BLUE),
                 border_style=LIGHT_GRAY, padding=(0, 1))


# ── persistence ─────────────────────────────────────────────────────────

def save_positions(data):
    with open(POSITIONS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def save_prices_cache(prices, candles_by_ticker):
    data = {
        'timestamp': datetime.now().isoformat(),
        'prices': prices,
        'candles': {
            t: [
                {'open': c['open'], 'high': c['high'],
                 'low': c['low'], 'close': c['close'],
                 'volume': c['volume'], 'datetime': c['datetime']}
                for c in candles
            ]
            for t, candles in candles_by_ticker.items()
        },
    }
    with open(PRICES_CACHE, 'w') as f:
        json.dump(data, f, indent=2)


def save_hist_cache(candles_by_ticker):
    with open(HIST_CACHE, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'candles': {
                t: [
                    {'open': c['open'], 'high': c['high'],
                     'low': c['low'], 'close': c['close'],
                     'volume': c['volume'], 'datetime': c['datetime']}
                    for c in candles
                ]
                for t, candles in candles_by_ticker.items()
            },
        }, f, indent=2)


def append_trade_log(ticker, side, qty, entry, exit_price, pnl, paper):
    entry_time = datetime.now().isoformat()
    record = {
        'timestamp': entry_time,
        'ticker': ticker,
        'side': side,
        'qty': qty,
        'entry': round(entry, 2),
        'exit_price': round(exit_price, 2) if exit_price else None,
        'pnl': round(pnl, 2) if pnl is not None else None,
        'paper': paper,
    }
    trades = []
    if os.path.exists(TRADE_LOG):
        with open(TRADE_LOG) as f:
            trades = json.load(f)
    trades.append(record)
    with open(TRADE_LOG, 'w') as f:
        json.dump(trades, f, indent=2)


# ── main ────────────────────────────────────────────────────────────────

def main():
    token = load_token()
    if not token:
        console = Console()
        console.print('[red]No token found[/]')
        sys.exit(1)

    console = Console()
    start = time.time()

    # ── Live display: true in-place updates ──
    with Live(
        console=console,
        refresh_per_second=4,
        transient=False,
        auto_refresh=False,
        vertical_overflow='visible',
    ) as live:
        while True:
            cycle_start = time.time()
            try:
                token = load_token()
                if token is None:
                    time.sleep(5)
                    continue

                prices, candles_by_ticker = get_prices(token)
                account = get_account(token)
                pd = get_positions_from_file()
                positions = pd.get('positions', {})
                dpnl = pd.get('daily_pnl', 0.0)
                tpnl = pd.get('total_pnl', 0.0)
                uptime = time.time() - start
                now = datetime.now()

                scene = Group(
                    build_top_box(positions),
                    '\n',
                    build_status_bar(uptime),
                    '\n',
                    build_scalping_bot_panel(account, dpnl, tpnl, now),
                    '\n',
                    build_bid_ask_table(prices, candles_by_ticker),
                    '\n',
                    build_positions_panel(positions),
                )

                live.update(scene)
                live.refresh()

                save_positions(pd)
                save_prices_cache(prices, candles_by_ticker)
                save_hist_cache(candles_by_ticker)

                spent = time.time() - cycle_start
                sleep_for = max(0.1, REFRESH_INTERVAL - spent)
                time.sleep(sleep_for)

            except KeyboardInterrupt:
                break
            except Exception:
                time.sleep(REFRESH_INTERVAL)

    console.print(Text('\nTUI closed.', style=DIM_GRAY))


if __name__ == '__main__':
    main()
