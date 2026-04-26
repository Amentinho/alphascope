"""
AlphaScope -- Trade Simulator v2.1 FINAL
Complete clean rewrite fixing all accumulated issues.
"""

import sqlite3
import json
import time
import argparse
import threading
import requests
from datetime import datetime, timezone, timedelta

# ── Configuration ─────────────────────────────────────────────────────────────
STARTING_BALANCE_USD = 200.0
STOP_LOSS_PCT        = -30.0
TAKE_PROFIT_PCT      = 150.0
MIN_SIGNAL_CONF      = 65

CHAINS = ['solana', 'bsc', 'base', 'arbitrum', 'ethereum']
ETH_BUDGET_USD = 800.0   # ETH mainnet gets more — real gems live here
NATIVE_TOKENS = {
    'solana':   ('SOL', 'solana'),
    'bsc':      ('BNB', 'binancecoin'),
    'base':     ('ETH', 'ethereum'),
    'arbitrum': ('ETH', 'ethereum'),
}

REAL_PORTFOLIO = {
    'ethereum': [
        {'symbol': 'LINK', 'coin_id': 'chainlink',   'amount': 90.9252, 'entry_price': 9.33},
        {'symbol': 'ETH',  'coin_id': 'ethereum',    'amount': 0.0338,  'entry_price': 2333.18},
    ],
    'bitcoin': [
        {'symbol': 'BTC',  'coin_id': 'bitcoin',     'amount': 0.1,     'entry_price': 75000},
    ],
    'solana': [
        {'symbol': 'SOL',  'coin_id': 'solana',      'amount': 20,      'entry_price': 85},
    ],
    'arbitrum': [
        {'symbol': 'HYPE', 'coin_id': 'hyperliquid', 'amount': 10,      'entry_price': 38},
    ],
}

CG_IDS = {
    'BTC':'bitcoin','ETH':'ethereum','SOL':'solana','BNB':'binancecoin',
    'LINK':'chainlink','HYPE':'hyperliquid','AAVE':'aave','UNI':'uniswap',
    'ATOM':'cosmos','DOGE':'dogecoin','XRP':'ripple','ADA':'cardano',
    'ARB':'arbitrum','OP':'optimism','AVAX':'avalanche-2',
}

# ── Price resolver ────────────────────────────────────────────────────────────
_price_cache = {}

def resolve_price(symbol, coin_id='', chain='', use_cache=True):
    """Fetch live price. Returns 0.0 if unavailable."""
    sym = symbol.upper()
    cache_key = f"{sym}_{chain}"
    
    # Cache for 90 seconds to avoid hammering APIs
    if use_cache and cache_key in _price_cache:
        cached_price, cached_time = _price_cache[cache_key]
        if time.time() - cached_time < 90 and cached_price > 0:
            return cached_price

    price = 0.0

    # 1. CoinGecko for majors
    cg_id = CG_IDS.get(sym, '')
    if not cg_id and coin_id and len(coin_id) < 30 and '-' in coin_id:
        cg_id = coin_id
    if cg_id:
        try:
            r = requests.get(
                f'https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd',
                timeout=6)
            if r.status_code == 200:
                price = float(r.json().get(cg_id, {}).get('usd', 0) or 0)
        except Exception:
            pass

    # 2. DexScreener by symbol
    if not price:
        try:
            r = requests.get(
                f'https://api.dexscreener.com/latest/dex/search?q={symbol}',
                timeout=8)
            if r.status_code == 200:
                pairs = r.json().get('pairs', [])
                # Filter to chain
                if chain and chain not in ('ethereum', 'bitcoin'):
                    cp = [p for p in pairs if p.get('chainId','') == chain]
                    pairs = cp or pairs
                # Exact symbol match with min $100 liquidity
                exact = [p for p in pairs
                         if p.get('baseToken',{}).get('symbol','').upper() == sym
                         and float(p.get('liquidity',{}).get('usd',0) or 0) >= 100]
                pool = exact or [p for p in pairs
                                 if float(p.get('liquidity',{}).get('usd',0) or 0) >= 100]
                if pool:
                    best = max(pool, key=lambda p: float(p.get('liquidity',{}).get('usd',0) or 0))
                    price = float(best.get('priceUsd', 0) or 0)
        except Exception:
            pass

    # 3. DexScreener by contract address
    if not price and coin_id:
        # Extract contract if coin_id is a dexscreener URL
        contract = coin_id
        if 'dexscreener.com/' in coin_id:
            contract = coin_id.rstrip('/').split('/')[-1]
        if len(contract) > 20:
            try:
                r = requests.get(
                    f'https://api.dexscreener.com/latest/dex/tokens/{contract}',
                    timeout=6)
                if r.status_code == 200:
                    pairs = r.json().get('pairs', [])
                    # Filter to correct chain if possible
                    if chain and chain not in ('ethereum', 'bitcoin'):
                        cp = [p for p in pairs if p.get('chainId', '') == chain]
                        pairs = cp or pairs
                    if pairs:
                        best = max(pairs, key=lambda p: float(p.get('liquidity', {}).get('usd', 0) or 0))
                        price = float(best.get('priceUsd', 0) or 0)
            except Exception:
                pass

    if price > 0:
        _price_cache[cache_key] = (price, time.time())
    return price


def get_db():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_sim_tables():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS sim_portfolio (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sim_id TEXT, symbol TEXT, chain TEXT,
        amount_tokens REAL, buy_price_usd REAL, buy_time TEXT,
        sell_price_usd REAL, sell_time TEXT,
        pnl_usd REAL, pnl_pct REAL,
        status TEXT DEFAULT 'HOLDING',
        signal_source TEXT, notes TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS sim_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sim_id TEXT UNIQUE, mode TEXT,
        start_time TEXT, end_time TEXT,
        starting_usd REAL, ending_usd REAL,
        total_pnl_usd REAL, total_pnl_pct REAL,
        trades_total INTEGER, trades_won INTEGER, trades_lost INTEGER,
        best_trade TEXT, worst_trade TEXT, summary TEXT)''')
    conn.commit()
    conn.close()


# ── Portfolio ─────────────────────────────────────────────────────────────────
class SimPortfolio:
    def __init__(self, sim_id):
        self.sim_id = sim_id
        self.cash = {ch: STARTING_BALANCE_USD for ch in CHAINS}
        self.cash['ethereum'] = ETH_BUDGET_USD
        self.holdings = {}
        self.trades = []
        self._saved_count = 0
        self._seed_real()
        # Capture T=0 live prices for intra-sim PnL reference
        self.t0_prices = self._snapshot_prices()
        self.starting_real = self._real_cost_basis()  # Fixed reference: your original purchase cost
        self.starting_trading = (STARTING_BALANCE_USD * (len(CHAINS) - 1)) + ETH_BUDGET_USD
        self.starting_total = self.starting_trading + self.starting_real

    def _seed_real(self):
        for chain, positions in REAL_PORTFOLIO.items():
            for pos in positions:
                key = f"{pos['symbol']}_{chain}"
                self.holdings[key] = {
                    'symbol': pos['symbol'], 'chain': chain,
                    'amount': pos['amount'], 'buy_price': pos['entry_price'],
                    'buy_time': 'real', 'usd_spent': pos['amount'] * pos['entry_price'],
                    'source': 'real', 'is_real': True, '_zero_count': 0,
                }

    def _snapshot_prices(self):
        """Capture live prices at sim launch (T=0). Used as intra-sim reference."""
        snapshot = {}
        for chain, positions in REAL_PORTFOLIO.items():
            for pos in positions:
                p = resolve_price(pos['symbol'], pos['coin_id'], chain)
                if p and p > 0:
                    snapshot[pos['symbol']] = p
                    print(f"    T=0 price: {pos['symbol']} = ${p:,.4f}")
        return snapshot

    def _real_cost_basis(self):
        """Fixed reference: what you originally paid for your real portfolio."""
        total = 0
        for chain, positions in REAL_PORTFOLIO.items():
            for pos in positions:
                total += pos['amount'] * pos['entry_price']
        return total

    def _real_value(self):
        """Current live value of real portfolio. Falls back to T=0 snapshot, never to entry prices."""
        total = 0
        for chain, positions in REAL_PORTFOLIO.items():
            for pos in positions:
                p = resolve_price(pos['symbol'], pos['coin_id'], chain)
                if not p or p <= 0:
                    # Fall back to T=0 snapshot price (not stale entry price)
                    p = self.t0_prices.get(pos['symbol'], 0)
                    if p > 0:
                        print(f"    WARN: {pos['symbol']} live price unavailable, using T=0 snapshot ${p:,.4f}")
                    else:
                        print(f"    WARN: {pos['symbol']} price unavailable, excluded from total")
                        continue
                total += pos['amount'] * p
        return total

    def _trading_value(self):
        total = sum(self.cash.values())
        for key, pos in self.holdings.items():
            if pos.get('is_real'):
                continue
            p = resolve_price(pos['symbol'], chain=pos['chain'])
            total += pos['amount'] * (p or pos['buy_price'])
        return total

    def can_buy(self, chain, usd):
        return self.cash.get(chain, 0) >= usd

    def buy(self, symbol, chain, usd, price, source='agent'):
        if price <= 0:
            return False, f"price is zero"
        if not self.can_buy(chain, usd):
            return False, f"insufficient cash (${self.cash.get(chain,0):.2f})"
        tokens = usd / price
        self.cash[chain] -= usd
        key = f"{symbol}_{chain}"
        self.holdings[key] = {
            'symbol': symbol, 'chain': chain, 'amount': tokens,
            'buy_price': price, 'buy_time': datetime.now().isoformat(),
            'usd_spent': usd, 'source': source,
            'is_real': False, '_zero_count': 0,
        }
        self.trades.append({
            'action': 'BUY', 'symbol': symbol, 'chain': chain,
            'usd': usd, 'price': price, 'tokens': tokens,
            'time': datetime.now().isoformat(), 'source': source,
        })
        return True, f"bought {tokens:.4f} {symbol} @ ${price:.8f}"

    def sell(self, symbol, chain, price, reason='signal'):
        key = f"{symbol}_{chain}"
        if key not in self.holdings:
            return False, "not holding"
        pos = self.holdings[key]
        if pos.get('is_real'):
            return False, "real portfolio"
        if price <= 0:
            return False, "price is zero"
        tokens = pos['amount']
        sell_val = tokens * price
        buy_val = tokens * pos['buy_price']
        pnl = sell_val - buy_val
        pnl_pct = (pnl / buy_val * 100) if buy_val > 0 else 0
        self.cash[chain] = self.cash.get(chain, 0) + sell_val
        del self.holdings[key]
        self.trades.append({
            'action': 'SELL', 'symbol': symbol, 'chain': chain,
            'usd': sell_val, 'price': price, 'tokens': tokens,
            'buy_price': pos['buy_price'],
            'pnl': pnl, 'pnl_pct': pnl_pct, 'reason': reason,
            'time': datetime.now().isoformat(),
        })
        return True, f"sold {symbol} @ ${price:.8f} | P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)"

    def check_exits(self, stop_loss=STOP_LOSS_PCT, take_profit=TAKE_PROFIT_PCT):
        actions = 0
        for key in list(self.holdings.keys()):
            pos = self.holdings.get(key)
            if not pos or pos.get('is_real'):
                continue
            sym, chain = pos['symbol'], pos['chain']
            price = resolve_price(sym, chain=chain, use_cache=False)
            if not price or price <= 0:
                pos['_zero_count'] = pos.get('_zero_count', 0) + 1
                if pos['_zero_count'] >= 3:
                    print(f"    WARNING: {sym} price=0 x3 -- assuming rug, force stop-loss")
                    price = pos['buy_price'] * 0.001
                else:
                    continue
            else:
                pos['_zero_count'] = 0
            pnl_pct = (price - pos['buy_price']) / pos['buy_price'] * 100
            effective_stop = -20 if chain in ('solana', 'bsc') else stop_loss
            if pnl_pct <= effective_stop:
                ok, msg = self.sell(sym, chain, price, 'stop_loss')
                if ok:
                    print(f"    STOP-LOSS {sym}: {pnl_pct:.1f}% | {msg}")
                    actions += 1
            elif pnl_pct >= take_profit:
                ok, msg = self.sell(sym, chain, price, 'take_profit')
                if ok:
                    print(f"    TAKE-PROFIT {sym}: +{pnl_pct:.1f}% | {msg}")
                    actions += 1
        return actions

    def print_status(self):
        tv = self._trading_value()
        rv = self._real_value()
        t0_rv = sum(self.t0_prices.get(pos['symbol'], 0) * pos['amount']
                    for positions in REAL_PORTFOLIO.values() for pos in positions
                    if self.t0_prices.get(pos['symbol'], 0) > 0)
        rv_delta = rv - t0_rv  # change vs T=0 (start of this session)
        tp = tv - self.starting_trading
        sells = [t for t in self.trades if t['action'] == 'SELL']
        wins   = sum(1 for t in sells if t.get('pnl', 0) > 0)
        losses = sum(1 for t in sells if t.get('pnl', 0) <= 0)
        best  = max(sells, key=lambda t: t.get('pnl_pct', 0), default=None)
        worst = min(sells, key=lambda t: t.get('pnl_pct', 0), default=None)
        best_str  = f"{best['symbol']} {best['pnl_pct']:+.0f}%"  if best  else 'none'
        worst_str = f"{worst['symbol']} {worst['pnl_pct']:+.0f}%" if worst else 'none'
        print(f"\n  {'='*52}")
        print(f"  {self.sim_id} | {datetime.now().strftime('%H:%M:%S')}")
        print(f"  Real portfolio:   ${rv:>10,.2f}  ({rv_delta:+.2f} this session)")
        print(f"  Trading capital:  ${tv:>10,.2f}  ({tp:+.2f} | {tp/max(self.starting_trading,1)*100:+.1f}%)")
        print(f"  Trades: {len(self.trades)} | W:{wins} L:{losses} | Best: {best_str} | Worst: {worst_str}")
        cash_str = ' | '.join(f"{c}:${v:.0f}" for c, v in self.cash.items() if v > 0)
        print(f"  Cash: {cash_str}")
        open_pos = [(k, v) for k, v in self.holdings.items() if not v.get('is_real')]
        if open_pos:
            print(f"  Open positions:")
            for key, pos in open_pos:
                p = resolve_price(pos['symbol'], chain=pos['chain'])
                pct = (p - pos['buy_price']) / pos['buy_price'] * 100 if p and pos['buy_price'] else 0
                val = pos['amount'] * (p or pos['buy_price'])
                direction = 'UP' if pct >= 0 else 'DN'
                print(f"    {direction} {pos['symbol']} ({pos['chain']}) ${val:.2f} | {pct:+.1f}%")
        print(f"  {'='*52}")

    def save(self):
        init_sim_tables()
        conn = get_db()
        c = conn.cursor()
        new_trades = self.trades[self._saved_count:]
        for t in new_trades:
            if t['action'] == 'BUY':
                try:
                    c.execute('''INSERT INTO sim_portfolio
                        (sim_id,symbol,chain,amount_tokens,buy_price_usd,buy_time,
                         sell_price_usd,pnl_usd,pnl_pct,status,signal_source)
                        VALUES(?,?,?,?,?,?,0,0,0,'HOLDING',?)''',
                        (self.sim_id,t['symbol'],t['chain'],
                         t['tokens'],t['price'],t['time'],t.get('source','')))
                except Exception:
                    pass
            else:
                try:
                    c.execute('''UPDATE sim_portfolio
                        SET sell_price_usd=?,sell_time=?,pnl_usd=?,pnl_pct=?,status='CLOSED'
                        WHERE sim_id=? AND symbol=? AND chain=? AND status='HOLDING'
                        ORDER BY id DESC LIMIT 1''',
                        (t['price'],t['time'],t.get('pnl',0),t.get('pnl_pct',0),
                         self.sim_id,t['symbol'],t['chain']))
                except Exception:
                    pass
        self._saved_count = len(self.trades)
        tv = self._trading_value()
        tp = tv - self.starting_trading
        sells = [t for t in self.trades if t['action'] == 'SELL']
        wins = sum(1 for t in sells if t.get('pnl',0) > 0)
        losses = sum(1 for t in sells if t.get('pnl',0) <= 0)
        best = max(sells, key=lambda t: t.get('pnl_pct',0), default=None)
        worst = min(sells, key=lambda t: t.get('pnl_pct',0), default=None)
        try:
            c.execute('''INSERT OR REPLACE INTO sim_runs
                (sim_id,mode,start_time,end_time,starting_usd,ending_usd,
                 total_pnl_usd,total_pnl_pct,trades_total,trades_won,trades_lost,
                 best_trade,worst_trade,summary)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (self.sim_id,'PAPER',
                 self.trades[0]['time'] if self.trades else datetime.now().isoformat(),
                 datetime.now().isoformat(),
                 self.starting_trading, tv, tp,
                 tp/max(self.starting_trading,1)*100,
                 len(self.trades),wins,losses,
                 f"{best['symbol']} {best['pnl_pct']:+.1f}%" if best else 'none',
                 f"{worst['symbol']} {worst['pnl_pct']:+.1f}%" if worst else 'none',
                 json.dumps({'trading_pnl':tp,'real_value':self._real_value()})))
        except Exception:
            pass
        conn.commit()
        conn.close()


# ── Price monitor (background thread) ────────────────────────────────────────
def run_price_monitor(portfolio, stop_loss=STOP_LOSS_PCT, take_profit=TAKE_PROFIT_PCT,
                      duration_minutes=370, interval_seconds=60):
    """Checks open positions every 60s -- catches rugs before next cycle."""
    def _loop():
        end = time.time() + duration_minutes * 60
        while time.time() < end:
            time.sleep(interval_seconds)
            open_pos = [(k,v) for k,v in portfolio.holdings.items()
                        if not v.get('is_real')]
            if not open_pos:
                continue
            for key, pos in list(open_pos):
                sym, chain = pos['symbol'], pos['chain']
                price = resolve_price(sym, chain=chain, use_cache=False)
                if not price or price <= 0:
                    pos['_zero_count'] = pos.get('_zero_count', 0) + 1
                    if pos['_zero_count'] >= 2:
                        print(f"\n    [MONITOR] {sym} price=0 x{pos['_zero_count']} -- FORCE STOP-LOSS")
                        # Use last known price * 0.01 to trigger stop-loss
                        price = pos['buy_price'] * 0.01
                    else:
                        continue
                else:
                    pos['_zero_count'] = 0
                pnl_pct = (price - pos['buy_price']) / pos['buy_price'] * 100
                effective_stop = -20 if chain in ('solana', 'bsc') else stop_loss
                if pnl_pct <= effective_stop:
                    ok, msg = portfolio.sell(sym, chain, price, 'stop_loss')
                    if ok:
                        print(f"\n    [MONITOR] STOP-LOSS {sym}: {pnl_pct:.1f}%")
                        portfolio.save()
                elif pnl_pct >= take_profit:
                    ok, msg = portfolio.sell(sym, chain, price, 'take_profit')
                    if ok:
                        print(f"\n    [MONITOR] TAKE-PROFIT {sym}: +{pnl_pct:.1f}%")
                        portfolio.save()
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t


# ── Agent cycle ───────────────────────────────────────────────────────────────
def _fallback_signals():
    """
    Fallback signal generator: uses live CoinGecko trending + DexScreener
    when the DB is empty (fetcher hasn't run yet or tables are stale).
    Returns proposals in the same format as wallet_agent.evaluate_signals().
    """
    proposals = []
    # Try CoinGecko trending coins
    try:
        r = requests.get('https://api.coingecko.com/api/v3/search/trending', timeout=8)
        if r.status_code == 200:
            coins = r.json().get('coins', [])[:7]
            for c in coins:
                item = c.get('item', {})
                sym = item.get('symbol', '').upper()
                coin_id = item.get('id', '')
                if not sym or sym in ('BTC','ETH','SOL','BNB','USDT','USDC'):
                    continue
                proposals.append({
                    'action': 'BUY',
                    'symbol': sym,
                    'coin_id': coin_id,
                    'chain': 'solana',  # default to SOL for low gas
                    'trade_usd': 40,
                    'alpha_score': 70,
                    'reasons': 'CoinGecko trending (fallback)',
                    'sources': 'fallback',
                    'category': 'TRENDING',
                })
    except Exception:
        pass

    # Try DexScreener hot pairs on SOL
    try:
        r = requests.get('https://api.dexscreener.com/latest/dex/tokens/solana', timeout=8)
        if r.status_code == 200:
            pairs = r.json().get('pairs', [])
            for p in pairs[:5]:
                sym = p.get('baseToken', {}).get('symbol', '').upper()
                liq = float(p.get('liquidity', {}).get('usd', 0) or 0)
                vol = float(p.get('volume', {}).get('h24', 0) or 0)
                price = float(p.get('priceUsd', 0) or 0)
                if not sym or liq < 20000 or vol < 10000 or price <= 0:
                    continue
                if sym in ('SOL', 'ETH', 'BTC', 'USDT', 'USDC', 'WSOL'):
                    continue
                proposals.append({
                    'action': 'BUY',
                    'symbol': sym,
                    'coin_id': p.get('baseToken', {}).get('address', ''),
                    'chain': 'solana',
                    'trade_usd': 35,
                    'alpha_score': 68,
                    'reasons': f'DexScreener SOL hot liq:${liq/1000:.0f}k vol:${vol/1000:.0f}k',
                    'sources': 'fallback_dex',
                    'category': 'DEX_GEM',
                })
    except Exception:
        pass

    return proposals[:8]  # cap to 8 fallback proposals


def run_agent_cycle(portfolio, stop_loss=STOP_LOSS_PCT, take_profit=TAKE_PROFIT_PCT):
    actions = 0

    # 1. Check exits
    actions += portfolio.check_exits(stop_loss, take_profit)

    # 2. Portfolio signals (BTC/SOL/HYPE/LINK)
    try:
        from portfolio import run_portfolio_signals
        for sig in (run_portfolio_signals() or []):
            sym = sig.get('symbol','')
            ch = sig.get('chain','ethereum')
            action = sig.get('signal','')
            conf = sig.get('confidence',0)
            if action in ('BUY','ACCUMULATE') and conf >= MIN_SIGNAL_CONF:
                price = resolve_price(sym, chain=ch)
                key = f"{sym}_{ch}"
                if price > 0 and key not in portfolio.holdings and portfolio.can_buy(ch, 50):
                    ok, msg = portfolio.buy(sym, ch, 50, price, 'portfolio_signal')
                    if ok:
                        print(f"    SIGNAL BUY {sym} $50 @ ${price:.4f}")
                        actions += 1
            elif action == 'SELL' and conf >= 80:
                price = resolve_price(sym, chain=ch)
                key = f"{sym}_{ch}"
                if price > 0 and key in portfolio.holdings and not portfolio.holdings[key].get('is_real'):
                    ok, msg = portfolio.sell(sym, ch, price, 'portfolio_sell')
                    if ok:
                        print(f"    SIGNAL SELL {sym} | {msg}")
                        actions += 1
    except Exception:
        pass

    # 3. DEX gem proposals (primary agent)
    proposals = []
    try:
        from wallet_agent import evaluate_signals
        proposals = evaluate_signals() or []
    except Exception as e:
        print(f"    wallet_agent error: {e}")

    # 3b. Fallback: if DB returned nothing actionable, use live APIs directly
    actionable = [p for p in proposals if p.get('action') not in ('SKIP', None)]
    if not actionable:
        print(f"    DB proposals empty — using live fallback signals")
        proposals = _fallback_signals()
    else:
        print(f"    wallet_agent: {len(actionable)} proposals — "
              + " | ".join(f"{p['action']} {p['symbol']}({p.get('chain','?')[:3]})" for p in actionable[:8]))

    stop_lossed = {f"{t['symbol']}_{t['chain']}" for t in portfolio.trades
                   if t['action'] == 'SELL' and t.get('reason') == 'stop_loss'}
    try:
        with open('sim_ban_list.json') as _f:
            stop_lossed |= set(json.load(_f))
    except Exception:
        pass

    chain_counts = {}
    for key, pos in portfolio.holdings.items():
        if not pos.get('is_real'):
            ch = pos['chain']
            chain_counts[ch] = chain_counts.get(ch, 0) + 1

    for p in proposals:
        if p.get('action') == 'SKIP':
            continue

        sym      = p.get('symbol', '')
        chain    = p.get('chain', 'solana')
        action   = p.get('action', '')
        cat      = p.get('category', '')
        trade_usd = min(p.get('trade_usd', 40), 75)

        if not sym or action not in ('BUY', 'ACCUMULATE'):
            continue

        # PORTFOLIO category = real holdings, agent shouldn't sim-trade these
        if cat == 'PORTFOLIO':
            continue

        # bitcoin has no DEX / sim cash
        if chain == 'bitcoin':
            continue

        key = f"{sym}_{chain}"
        if key in portfolio.holdings:
            continue
        if key in stop_lossed:
            continue
        chain_limit = 4 if chain in ('solana', 'bsc') else 3
        if chain_counts.get(chain, 0) >= chain_limit:
            continue
        if not portfolio.can_buy(chain, trade_usd):
            continue

        # Use stored price from proposal as fast-path, then verify with live fetch
        price = p.get('price_usd', 0) or 0
        live_price = resolve_price(sym, coin_id=p.get('coin_id', ''), chain=chain, use_cache=False)
        if live_price and live_price > 0:
            price = live_price
        if not price or price <= 0:
            print(f"    SKIP {sym} -- price unavailable on {chain}")
            continue
        if price < 1e-6:
            print(f'    SKIP {sym} -- price dust (${price:.2e})')
            continue

        ok, msg = portfolio.buy(sym, chain, trade_usd, price, p.get('sources', 'agent'))
        if ok:
            print(f"    BUY {sym} ${trade_usd:.0f} @ ${price:.8f} | {str(p.get('reasons', ''))[:50]}")
            chain_counts[chain] = chain_counts.get(chain, 0) + 1
            actions += 1

    return actions


# ── Main simulation ───────────────────────────────────────────────────────────
def run_simulation(hours=6, cycle_min=5, stop_loss=STOP_LOSS_PCT, take_profit=TAKE_PROFIT_PCT):
    init_sim_tables()
    sim_id = f"SIM_{datetime.now().strftime('%Y%m%d_%H%M')}"
    portfolio = SimPortfolio(sim_id)
    end_time = datetime.now(timezone.utc) + timedelta(hours=hours)
    total_cycles = int(hours * 60 / cycle_min)

    # Start background price monitor
    monitor = run_price_monitor(portfolio, stop_loss, take_profit,
                                duration_minutes=int(hours*60)+5)

    print(f"\n{'='*60}")
    print(f"  AlphaScope Trade Simulation v2.3")
    print(f"  Sim ID: {sim_id}")
    print(f"  Trading capital: ${portfolio.starting_trading:.0f} "
          f"(SOL/BSC/BASE/ARB: ${STARTING_BALANCE_USD:.0f} each | ETH: ${ETH_BUDGET_USD:.0f})")
    print(f"  Real portfolio cost basis: ${portfolio.starting_real:,.2f}")
    print(f"  Real portfolio T=0 value:  ${portfolio._real_value():,.2f}")
    print(f"  Duration: {hours}h | Cycle: {cycle_min}min | "
          f"Stop: {stop_loss}% | TP: +{take_profit}%")
    print(f"  Price monitor: every 60s (catches rugs fast)")
    print(f"  End: {end_time.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    cycle = 0
    while datetime.now(timezone.utc) < end_time:
        cycle += 1
        elapsed = cycle * cycle_min / 60
        print(f"\n  --- Cycle {cycle}/{total_cycles} | +{elapsed:.1f}h ---")

        try:
            if cycle_min <= 10:
                print("  Fast refresh...")
                from dex_scanner import fetch_dex_gems
                fetch_dex_gems()
                from social_monitor import run_social_monitoring
                run_social_monitoring()
                from portfolio import run_portfolio_signals
                run_portfolio_signals()
            else:
                import subprocess
                print("  Full fetch...")
                r = subprocess.run(['python3','fetcher.py'],
                                   capture_output=True, text=True, timeout=300)
                for line in r.stdout.split('\n'):
                    if any(x in line for x in ['gems','Social','Portfolio','Agent','Security']):
                        print(f"  {line.strip()}")
        except Exception as e:
            print(f"  Refresh error: {e}")

        actions = run_agent_cycle(portfolio, stop_loss, take_profit)
        print(f"  Actions: {actions}")
        portfolio.print_status()
        portfolio.save()

        if datetime.now(timezone.utc) < end_time:
            print(f"\n  Next cycle in {cycle_min} min... (Ctrl+C to stop)")
            try:
                time.sleep(cycle_min * 60)
            except KeyboardInterrupt:
                print("\n  Stopped by user")
                break

    print(f"\n{'='*60}")
    print(f"  COMPLETE -- {sim_id}")
    portfolio.print_status()
    display_results(sim_id)
    portfolio.save()


# ── Results display ───────────────────────────────────────────────────────────
def display_results(sim_id=None):
    conn = get_db()
    if not sim_id:
        row = conn.execute(
            "SELECT sim_id FROM sim_runs ORDER BY start_time DESC LIMIT 1").fetchone()
        if not row:
            print("No simulations found")
            return
        sim_id = row[0]

    positions = conn.execute("""
        SELECT symbol, chain, buy_price_usd, sell_price_usd,
               amount_tokens, pnl_usd, pnl_pct, status, signal_source
        FROM sim_portfolio WHERE sim_id=? AND buy_price_usd > 0
        GROUP BY symbol, chain HAVING MAX(id)
        ORDER BY pnl_pct DESC
    """, (sim_id,)).fetchall()
    conn.close()

    print(f"\n{'='*65}")
    print(f"  RESULTS: {sim_id}")
    print(f"{'='*65}")
    def fmt_price(p):
        if not p: return '$0'
        if p < 0.0001: return f'${p:.2e}'
        if p < 0.01: return f'${p:.8f}'
        return f'${p:.4f}'

    print(f"  {'Symbol':<10} {'Chain':<10} {'Buy':>12} {'Now':>12} "
          f"{'P&L':>8} {'%':>7} Status")
    print(f"  {'-'*63}")

    total_in = total_now = 0
    wins = losses = 0

    # Clear price cache so display shows fresh prices
    _price_cache.clear()

    for sym, ch, buy_px, sell_px, tokens, pnl, pnl_pct, status, src in positions:
        if not tokens or not buy_px:
            continue
        invested = tokens * buy_px
        if status == 'CLOSED':
            now_px = sell_px
            val = tokens * (sell_px or buy_px) + pnl
        else:
            now_px = resolve_price(sym, chain=ch, use_cache=False)
            if now_px and now_px > 0:
                val = tokens * now_px
                pnl = val - invested
                pnl_pct = pnl / invested * 100
            else:
                now_px = buy_px
                val = invested
        total_in += invested
        total_now += val
        if pnl > 0: wins += 1
        elif pnl < 0: losses += 1
        d = 'UP' if pnl >= 0 else 'DN'
        print(f"  {d} {sym:<9} {ch:<10} {fmt_price(buy_px):>12} {fmt_price(now_px):>12} "
              f"${pnl:>7.2f} {pnl_pct:>6.1f}% {status}")

    print(f"  {'-'*63}")
    total_pnl = total_now - total_in
    pct = total_pnl / total_in * 100 if total_in else 0
    d = 'UP' if total_pnl >= 0 else 'DN'
    print(f"  {d} Trading: ${total_in:.2f} -> ${total_now:.2f} = ${total_pnl:+.2f} ({pct:+.1f}%)")
    print(f"  Win rate: {wins}W / {losses}L = {wins/max(wins+losses,1)*100:.0f}%")

    print(f"\n  Real Portfolio (live prices):")
    real_total = real_pnl = 0
    for chain, plist in REAL_PORTFOLIO.items():
        for pos in plist:
            p = resolve_price(pos['symbol'], pos['coin_id'], chain)
            p = p or pos['entry_price']
            val = pos['amount'] * p
            entry = pos['amount'] * pos['entry_price']
            pnl_r = val - entry
            real_total += val
            real_pnl += pnl_r
            d = 'UP' if pnl_r >= 0 else 'DN'
            print(f"    {d} {pos['symbol']:<6} ${pos['entry_price']:.2f}->${p:.2f} "
                  f"x{pos['amount']} = ${val:,.2f} ({pnl_r:+.2f})")
    print(f"  Real portfolio total: ${real_total:,.2f} (pnl: ${real_pnl:+.2f})")
    print(f"{'='*65}\n")


def run_test():
    run_simulation(hours=3/60, cycle_min=1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AlphaScope Simulator v2.1')
    parser.add_argument('--hours',       type=float, default=6)
    parser.add_argument('--cycle',       type=int,   default=5)
    parser.add_argument('--stop-loss',   type=float, default=-30)
    parser.add_argument('--take-profit', type=float, default=150)
    parser.add_argument('--test',        action='store_true')
    parser.add_argument('--results',     action='store_true')
    args = parser.parse_args()

    if args.test:
        run_test()
    elif args.results:
        display_results()
    else:
        run_simulation(args.hours, args.cycle, args.stop_loss, args.take_profit)
