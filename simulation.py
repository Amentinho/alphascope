"""
AlphaScope -- Trade Simulator v2.1 FINAL
Complete clean rewrite fixing all accumulated issues.
"""

import sqlite3
import json
import time
import os
import resource
import argparse
import threading
import requests
from datetime import datetime, timezone, timedelta

# Raise file descriptor limit to prevent "Too many open files" after long runs
try:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(8192, hard), hard))
except Exception:
    pass

# ── Configuration ─────────────────────────────────────────────────────────────
STARTING_BALANCE_USD = 50.0
STOP_LOSS_PCT        = -20.0
TAKE_PROFIT_PCT      = 25.0
MIN_SIGNAL_CONF      = 65

CHAINS = ['solana', 'base', 'ethereum']  # BSC/ARB removed — no wallet configured
ETH_BUDGET_USD = 100.0   # ETH paper budget
NATIVE_TOKENS = {
    'solana':   ('SOL', 'solana'),
    'bsc':      ('BNB', 'binancecoin'),
    'base':     ('ETH', 'ethereum'),
    'arbitrum': ('ETH', 'ethereum'),
}

REAL_PORTFOLIO = {
    'ethereum': [
        {'symbol': 'LINK', 'coin_id': 'chainlink', 'amount': 60.9266, 'entry_price': 10.58},
        {'symbol': 'ETH',  'coin_id': 'ethereum',  'amount': 0.1281,  'entry_price': 2329.01},
    ],
    'base': [
        {'symbol': 'ETH',  'coin_id': 'ethereum',  'amount': 0.0594,  'entry_price': 2329.01},
    ],
    'solana': [
        {'symbol': 'SOL',  'coin_id': 'solana',    'amount': 0.46,    'entry_price': 93.70},
    ],
}
# Note: entry_price = your actual purchase price (cost basis), never changes
# T=0 price = live price at sim launch, used for session P&L tracking

CG_IDS = {
    'BTC':'bitcoin','ETH':'ethereum','SOL':'solana','BNB':'binancecoin',
    'LINK':'chainlink','HYPE':'hyperliquid','AAVE':'aave','UNI':'uniswap',
    'ATOM':'cosmos','DOGE':'dogecoin','XRP':'ripple','ADA':'cardano',
    'ARB':'arbitrum','OP':'optimism','AVAX':'avalanche-2',
}

# ── Price resolver ────────────────────────────────────────────────────────────
_price_cache = {}

def _db_price(symbol):
    """Read latest price from token_data table — populated by fetcher. Fast, no API call."""
    try:
        import sqlite3 as _sq
        conn = _sq.connect(MAIN_DB, timeout=10)
        # Try symbol match first, then coin_id match (fetcher stores both)
        row = conn.execute(
            """SELECT price_usd FROM token_data
               WHERE (UPPER(symbol)=UPPER(?) OR UPPER(coin_id)=UPPER(?))
               AND price_usd > 0
               ORDER BY fetched_at DESC LIMIT 1""",
            (symbol, symbol)).fetchone()
        conn.close()
        if row and row[0] and float(row[0]) > 0:
            return float(row[0])
    except Exception:
        pass
    return 0.0


# Binance symbols for direct price feed (no rate limit)
BINANCE_SYMBOLS = {
    'BTC':'BTCUSDT','ETH':'ETHUSDT','SOL':'SOLUSDT','BNB':'BNBUSDT',
    'LINK':'LINKUSDT','HYPE':'HYPEUSDT','XRP':'XRPUSDT','ADA':'ADAUSDT',
    'DOGE':'DOGEUSDT','AVAX':'AVAXUSDT','ATOM':'ATOMUSDT','NEAR':'NEARUSDT',
    'ARB':'ARBUSDT','AAVE':'AAVEUSDT','UNI':'UNIUSDT','LTC':'LTCUSDT',
}


def _dex_pair_price(chain, dex_url='', symbol=''):
    """Resolve price from the exact DexScreener pair URL/address we bought."""
    if not dex_url:
        return 0.0
    try:
        pair_id = str(dex_url).rstrip('/').split('/')[-1]
        if not pair_id or len(pair_id) < 20:
            return 0.0
        chain_id = {'ethereum': 'ethereum', 'base': 'base',
                    'solana': 'solana', 'bsc': 'bsc',
                    'arbitrum': 'arbitrum'}.get(chain, chain)
        r = requests.get(
            f'https://api.dexscreener.com/latest/dex/pairs/{chain_id}/{pair_id}',
            timeout=6)
        if r.status_code == 200:
            pairs = r.json().get('pairs', []) or []
            if not pairs:
                pair = r.json().get('pair')
                pairs = [pair] if pair else []
            if pairs:
                p = pairs[0]
                if symbol:
                    base_sym = p.get('baseToken', {}).get('symbol', '').upper()
                    if base_sym and base_sym != symbol.upper():
                        return 0.0
                return float(p.get('priceUsd', 0) or 0)
    except Exception:
        pass
    return 0.0


def resolve_price(symbol, coin_id='', chain='', use_cache=True, dex_url=''):
    """Fetch live price. Multiple sources with fallback chain."""
    sym = symbol.upper()
    cache_key = f"{sym}_{chain}_{str(coin_id)[:16]}_{str(dex_url)[-16:]}"

    # Cache for 4 minutes within a cycle
    if use_cache and cache_key in _price_cache:
        cached_price, cached_time = _price_cache[cache_key]
        if time.time() - cached_time < 30 and cached_price > 0:
            return cached_price

    # 0. Exact pair first for microcaps. This avoids symbol collisions such as
    # SHEKEL/NO/NOGUY where a different pair can report a wildly different price.
    if dex_url:
        price = _dex_pair_price(chain, dex_url, sym)
        if price > 0:
            _price_cache[cache_key] = (price, time.time())
            return price

    # 1. DB cache only when caller allows cached prices. Fresh calls
    # (use_cache=False) must hit live APIs so T=0 never equals stale cost basis.
    if use_cache and sym in BINANCE_SYMBOLS:
        price = _db_price(sym)
        if price > 0:
            _price_cache[cache_key] = (price, time.time())
            return price

    price = 0.0

    # 1. Binance — free, no rate limit, instant for majors
    if sym in BINANCE_SYMBOLS:
        try:
            r = requests.get(
                f'https://api.binance.com/api/v3/ticker/price?symbol={BINANCE_SYMBOLS[sym]}',
                timeout=5)
            if r.status_code == 200:
                price = float(r.json().get('price', 0) or 0)
        except Exception:
            pass

    # 2. CoinGecko for majors (rate-limited — use as fallback)
    cg_id = CG_IDS.get(sym, '')
    if not cg_id and coin_id and len(coin_id) < 30 and '-' in coin_id:
        cg_id = coin_id
    if not price and cg_id:
        try:
            r = requests.get(
                f'https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd',
                timeout=6)
            if r.status_code == 200:
                price = float(r.json().get(cg_id, {}).get('usd', 0) or 0)
        except Exception:
            pass

    # 3. GeckoTerminal — good for new tokens, no auth needed
    if not price and chain and chain not in ('bitcoin',):
        gt_chain = {'solana':'solana','ethereum':'eth','bsc':'bsc',
                    'base':'base','arbitrum':'arbitrum'}.get(chain,'')
        if gt_chain and coin_id and len(coin_id) > 20:
            try:
                r = requests.get(
                    f'https://api.geckoterminal.com/api/v2/networks/{gt_chain}/tokens/{coin_id}',
                    timeout=6)
                if r.status_code == 200:
                    p = r.json().get('data',{}).get('attributes',{}).get('price_usd')
                    price = float(p or 0)
            except Exception:
                pass

    # 2. DexScreener by symbol — only if no contract available
    # Symbol search is unreliable (YUNOGUY matches wrong token)
    # Only use when coin_id is not a contract address
    if not price and (not coin_id or len(coin_id) < 20):
        try:
            r = requests.get(
                f'https://api.dexscreener.com/latest/dex/search?q={symbol}',
                timeout=8)
            if r.status_code == 200:
                pairs = r.json().get('pairs', [])
                if chain and chain not in ('ethereum', 'bitcoin'):
                    cp = [p for p in pairs if p.get('chainId','') == chain]
                    pairs = cp or pairs
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


SIM_DB = 'sim.db'      # sim writes here (no contention with fetcher)
MAIN_DB = 'alphascope.db'  # read-only: prices, dex_gems, signals


def _env(key, default=''):
    val = os.environ.get(key, '')
    if val:
        return val
    try:
        with open('.env') as f:
            for line in f:
                line = line.strip()
                if line.startswith(f'{key}='):
                    return line.split('=', 1)[1].strip()
    except Exception:
        pass
    return default


MIN_WATCHLIST_SEEN = int(_env('SIM_MIN_WATCHLIST_SEEN', '2'))
MIN_WATCHLIST_AGE_MIN = float(_env('SIM_MIN_WATCHLIST_AGE_MIN', '5'))
ENABLE_AI_RISK_VETO = _env('ENABLE_AI_RISK_VETO', 'false').lower().strip() == 'true'
AI_MIN_TOTAL_SCORE = int(_env('AI_MIN_TOTAL_SCORE', '16'))
ESTABLISHED_ALLOW_TOPUPS = _env('SIM_ESTABLISHED_ALLOW_TOPUPS', 'false').lower().strip() == 'true'
ESTABLISHED_MAX_PROPOSALS = int(_env('SIM_ESTABLISHED_MAX_PROPOSALS', '3'))
TARGET_ESTABLISHED_PCT = float(_env('SIM_TARGET_ESTABLISHED_PCT', '0.60'))
TARGET_GEMS_PCT = float(_env('SIM_TARGET_GEMS_PCT', '0.25'))
TARGET_LISTINGS_PCT = float(_env('SIM_TARGET_LISTINGS_PCT', '0.15'))
SIM_ESTABLISHED_MAX_USD = float(_env('SIM_ESTABLISHED_MAX_USD', '2.0'))
SIM_GEM_MAX_USD = float(_env('SIM_GEM_MAX_USD', '0.75'))
SIM_LISTING_MAX_USD = float(_env('SIM_LISTING_MAX_USD', '1.0'))
SIM_MIN_ALLOC_SCORE = float(_env('SIM_MIN_ALLOC_SCORE', '58'))
SIM_MAX_NEW_BUYS_PER_CYCLE = int(_env('SIM_MAX_NEW_BUYS_PER_CYCLE', '4'))

# ── Single-writer DB queue ─────────────────────────────────────────────────────
import queue as _queue
_db_write_queue = _queue.Queue()
_db_conn = None  # single persistent connection, main thread only

def _db_writer_loop():
    """Dedicated DB writer thread — sole writer to sim.db. Auto-reconnects on error."""
    global _db_conn

    def _connect():
        global _db_conn
        c = sqlite3.connect(SIM_DB, timeout=60, check_same_thread=False)
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA synchronous=NORMAL')
        c.execute('PRAGMA busy_timeout=10000')
        _db_conn = c
        return c

    conn = None
    while True:
        try:
            if conn is None:
                conn = _connect()
            item = _db_write_queue.get(timeout=5)
            if item is None:  # shutdown signal
                break
            sql, params = item
            try:
                conn.execute(sql, params)
                conn.commit()
            except sqlite3.ProgrammingError:
                # Connection closed — reconnect and retry
                try:
                    conn = _connect()
                    conn.execute(sql, params)
                    conn.commit()
                except Exception as e2:
                    print(f"  DB write error (retry): {e2}")
            except Exception as e:
                print(f"  DB write error: {e}")
        except _queue.Empty:
            continue
        except Exception as e:
            print(f"  DB writer loop error: {e} — reconnecting")
            conn = None
            time.sleep(1)

def _db_exec(sql, params=()):
    """Queue a write to sim.db. Non-blocking."""
    _db_write_queue.put((sql, params))

def _start_db_writer():
    t = threading.Thread(target=_db_writer_loop, daemon=True, name='db_writer')
    t.start()
    return t

def get_db():
    """Returns the shared DB connection (read or batch operations)."""
    if _db_conn:
        return _db_conn
    conn = sqlite3.connect(SIM_DB, timeout=30, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=5000')
    return conn

def get_main_db():
    """Main DB — read-only access for prices/gems."""
    import time as _time
    for attempt in range(3):
        try:
            conn = sqlite3.connect(MAIN_DB, timeout=10)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA query_only=ON')
            return conn
        except sqlite3.OperationalError:
            if attempt < 2:
                _time.sleep(1)
    return None


def init_sim_tables():
    # Wait up to 3s for DB writer thread to initialize
    for _ in range(30):
        if _db_conn is not None:
            break
        time.sleep(0.1)
    # Ensure coin_buzz has source column for token_intelligence
    try:
        import sqlite3 as _sq
        _c = _sq.connect(MAIN_DB, timeout=5)
        _c.execute("ALTER TABLE coin_buzz ADD COLUMN source TEXT DEFAULT 'manual'")
        _c.commit()
        _c.close()
    except Exception:
        pass  # already exists
    _db_exec('''CREATE TABLE IF NOT EXISTS sim_portfolio (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sim_id TEXT, symbol TEXT, chain TEXT,
        amount_tokens REAL, buy_price_usd REAL, buy_time TEXT,
        sell_price_usd REAL, sell_time TEXT,
        pnl_usd REAL, pnl_pct REAL,
        status TEXT DEFAULT "HOLDING",
        signal_source TEXT, notes TEXT)''', ())
    _db_exec('''CREATE TABLE IF NOT EXISTS sim_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sim_id TEXT UNIQUE, mode TEXT,
        start_time TEXT, end_time TEXT,
        starting_usd REAL, ending_usd REAL,
        total_pnl_usd REAL, total_pnl_pct REAL,
        trades_total INTEGER, trades_won INTEGER, trades_lost INTEGER,
        best_trade TEXT, worst_trade TEXT, summary TEXT)''', ())


# ── Portfolio ─────────────────────────────────────────────────────────────────
class SimPortfolio:
    @classmethod
    def restore(cls, sim_id):
        """
        Restore portfolio state from sim.db after a crash.
        Re-seeds real holdings and rebuilds open sim positions from DB.
        """
        port = cls.__new__(cls)
        port.sim_id = sim_id
        port.cash = {ch: STARTING_BALANCE_USD for ch in CHAINS}
        port.cash['ethereum'] = ETH_BUDGET_USD
        port.holdings = {}
        port.trades = []
        port._saved_count = 0
        port._seed_real()
        port.t0_prices = {}
        port.starting_real = port._real_cost_basis()
        port.starting_trading = (STARTING_BALANCE_USD * 2) + ETH_BUDGET_USD
        port.starting_total = port.starting_trading + port.starting_real
        port.wallet_balances = {}
        port.wallet_balances_t0 = {}

        # Restore open sim positions from DB
        try:
            conn = sqlite3.connect(SIM_DB, timeout=10)
            rows = conn.execute("""
                SELECT symbol, chain, amount_tokens, buy_price_usd, buy_time, signal_source
                FROM sim_portfolio
                WHERE sim_id=? AND status='HOLDING' AND buy_price_usd > 0
            """, (sim_id,)).fetchall()
            conn.close()
            for sym, chain, tokens, buy_price, buy_time, src in rows:
                if chain not in CHAINS:
                    continue
                key = f"{sym}_{chain}"
                port.holdings[key] = {
                    'symbol': sym, 'chain': chain, 'amount': tokens,
                    'buy_price': buy_price, 'buy_time': buy_time or '',
                    'usd_spent': tokens * buy_price, 'source': src or 'restored',
                    'coin_id': '', 'is_real': False, '_zero_count': 0,
                }
                # Deduct the position cost from cash
                port.cash[chain] = max(0, port.cash.get(chain, 0) - (tokens * buy_price))
            if rows:
                print(f"  ♻️  Restored {len(rows)} open positions from DB (sim_id={sim_id})")
        except Exception as e:
            print(f"  ⚠️  Restore failed: {e} — starting fresh")

        return port

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
        self.starting_real = self._real_cost_basis()
        # SOL + BASE each get STARTING_BALANCE_USD, ETH gets ETH_BUDGET_USD
        self.starting_trading = (STARTING_BALANCE_USD * 2) + ETH_BUDGET_USD
        self.starting_total = self.starting_trading + self.starting_real
        # Snapshot real wallet balances at T=0
        self.wallet_balances = self._snapshot_wallet_balances()
        self.wallet_balances_t0 = dict(self.wallet_balances)

    def _snapshot_wallet_balances(self) -> dict:
        """Get real on-chain wallet balances. Called at start and after each tx."""
        balances = {}
        try:
            from executor import _sol_keypair, _sol_price, _eth_price, _w3, EVM_WALLET, RPCS
            # SOL balance
            kp = _sol_keypair()
            if kp:
                r = requests.get(
                    'https://api.mainnet-beta.solana.com',
                    timeout=5)
                r2 = requests.post('https://api.mainnet-beta.solana.com', json={
                    'jsonrpc':'2.0','id':1,'method':'getBalance',
                    'params':[str(kp.pubkey())]}, timeout=6)
                sol_bal = r2.json().get('result',{}).get('value',0)/1e9
                balances['solana'] = {'amount': sol_bal, 'symbol': 'SOL',
                                      'usd': sol_bal * _sol_price()}
            # EVM balances (BASE + ETH same wallet)
            if EVM_WALLET:
                from web3 import Web3
                for chain in ('ethereum', 'base'):
                    try:
                        w3 = Web3(Web3.HTTPProvider(RPCS.get(chain,'')))
                        bal = w3.eth.get_balance(w3.to_checksum_address(EVM_WALLET))/1e18
                        balances[chain] = {'amount': bal, 'symbol': 'ETH',
                                           'usd': bal * _eth_price()}
                    except Exception:
                        pass
        except Exception as e:
            print(f"    wallet snapshot error: {e}")
        return balances

    def _refresh_wallet_balance(self, chain):
        """Refresh single chain balance after a transaction."""
        try:
            new_bals = self._snapshot_wallet_balances()
            if chain in new_bals:
                old = self.wallet_balances.get(chain, {}).get('usd', 0)
                new = new_bals[chain]['usd']
                diff = new - old
                self.wallet_balances[chain] = new_bals[chain]
                return diff
        except Exception:
            pass
        return 0

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
        """Capture live prices at sim launch. DB is too stale for T=0."""
        snapshot = {}
        for chain, positions in REAL_PORTFOLIO.items():
            for pos in positions:
                sym = pos['symbol']
                p = resolve_price(sym, pos['coin_id'], chain, use_cache=False)
                source = 'live'
                if not p:
                    p = _db_price(sym)
                    source = 'db-fallback'
                if p and p > 0:
                    snapshot[sym] = p
                    print(f"    T=0 {sym} = ${p:,.4f} ({source})")
        return snapshot

    def _real_cost_basis(self):
        """Fixed reference: what you originally paid for your real portfolio."""
        total = 0
        for chain, positions in REAL_PORTFOLIO.items():
            for pos in positions:
                total += pos['amount'] * pos['entry_price']
        return total

    def _real_value(self):
        """Current live value."""
        BINANCE_IDS = {
            'LINK':'LINKUSDT','ETH':'ETHUSDT','BTC':'BTCUSDT',
            'SOL':'SOLUSDT','HYPE':'HYPEUSDT','BNB':'BNBUSDT',
        }
        total = 0
        for chain, positions in REAL_PORTFOLIO.items():
            for pos in positions:
                sym = pos['symbol']
                p = 0
                # 1. Binance direct — no cache, always fresh
                if sym in BINANCE_IDS:
                    try:
                        r = requests.get(
                            f'https://api.binance.com/api/v3/ticker/price?symbol={BINANCE_IDS[sym]}',
                            timeout=5)
                        if r.status_code == 200:
                            p = float(r.json().get('price', 0) or 0)
                    except Exception:
                        pass
                # 2. Fallback to CoinGecko
                if not p:
                    try:
                        r = requests.get(
                            f'https://api.coingecko.com/api/v3/simple/price?ids={pos["coin_id"]}&vs_currencies=usd',
                            timeout=6)
                        if r.status_code == 200:
                            p = float(r.json().get(pos['coin_id'],{}).get('usd',0) or 0)
                    except Exception:
                        pass
                # 3. T=0 snapshot last resort
                if not p:
                    p = self.t0_prices.get(sym, 0)
                if p > 0:
                    total += pos['amount'] * p
        return total

    def _trading_value(self):
        total = sum(self.cash.values())
        for key, pos in self.holdings.items():
            if pos.get('is_real'):
                continue
            p = resolve_price(pos['symbol'], coin_id=pos.get('coin_id', ''),
                              chain=pos['chain'], dex_url=pos.get('dex_url', ''))
            total += pos['amount'] * (p or pos['buy_price'])
        return total

    def can_buy(self, chain, usd):
        return self.cash.get(chain, 0) >= usd

    def buy(self, symbol, chain, usd, price, source='agent', contract='',
            dex_url='', category='', allocation_score=0):
        if price <= 0:
            return False, f"price is zero"
        if not self.can_buy(chain, usd):
            return False, f"insufficient cash (${self.cash.get(chain,0):.2f})"
        tokens = usd / price
        try:
            from executor import on_buy, _is_dry_run
            is_dry = _is_dry_run()
            if not is_dry:
                real_bal = self.wallet_balances.get(chain, {})
                cash_left = real_bal.get('usd', sum(self.cash.values()))
                result = on_buy(symbol, chain, usd, price, source, contract,
                                cash_left=cash_left)
                if result and not result.get('success') and result.get('mode') != 'paper':
                    _write_persistent_ban(symbol, chain, result.get('error', 'tx failed'))
                    return False, f"real buy failed: {result.get('error', 'unknown')}"
        except Exception as e:
            if not _executor_dry_run():
                return False, f"executor buy error: {e}"
            is_dry = True
        self.cash[chain] -= usd
        key = f"{symbol}_{chain}"
        self.holdings[key] = {
            'symbol': symbol, 'chain': chain, 'amount': tokens,
            'buy_price': price, 'buy_time': datetime.now().isoformat(),
            'usd_spent': usd, 'source': source,
            'coin_id': contract,  # mint address for real execution
            'dex_url': dex_url,
            'category': category or _proposal_family({'category': '', 'sources': source}),
            'allocation_score': allocation_score,
            'is_real': False, '_zero_count': 0,
        }
        self.trades.append({
            'action': 'BUY', 'symbol': symbol, 'chain': chain,
            'usd': usd, 'price': price, 'tokens': tokens,
            'time': datetime.now().isoformat(), 'source': source,
            'coin_id': contract, 'dex_url': dex_url,
            'category': category, 'allocation_score': allocation_score,
        })
        try:
            if is_dry:
                from executor import on_buy
                on_buy(symbol, chain, usd, price, source, contract,
                       cash_left=sum(self.cash.values()))
            # Refresh wallet balance after real tx
            if not is_dry:
                diff = self._refresh_wallet_balance(chain)
                if diff != 0:
                    print(f"    Wallet {chain}: {diff:+.4f} change")
        except Exception as e:
            print(f"    executor buy alert/balance error: {e}")
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

        try:
            from executor import on_sell, _is_dry_run
            is_dry = _is_dry_run()
            trading_total = self._trading_value()
            trading_pct = (trading_total - self.starting_trading) / max(self.starting_trading, 1) * 100
            result = on_sell(symbol, chain, price, pnl_pct, reason,
                    token_amount=pos.get('amount', 0),
                    contract=pos.get('coin_id', ''),
                    pnl_usd=pnl,
                    trading_total=trading_total,
                    trading_pct=trading_pct)
            if not is_dry and result and not result.get('success') and result.get('mode') != 'paper':
                return False, f"real sell failed: {result.get('error', 'unknown')}"
        except Exception as e:
            if not _executor_dry_run():
                return False, f"executor sell error: {e}"
            is_dry = True

        self.cash[chain] = self.cash.get(chain, 0) + sell_val
        del self.holdings[key]
        self.trades.append({
            'action': 'SELL', 'symbol': symbol, 'chain': chain,
            'usd': sell_val, 'price': price, 'tokens': tokens,
            'buy_price': pos['buy_price'],
            'pnl': pnl, 'pnl_pct': pnl_pct, 'reason': reason,
            'time': datetime.now().isoformat(),
        })
        try:
            # Refresh wallet balance after real sell
            if not is_dry:
                diff = self._refresh_wallet_balance(chain)
                if diff != 0:
                    print(f"    Wallet {chain} after sell: {diff:+.4f} change")
        except Exception as e:
            print(f"    executor sell balance error: {e}")
        return True, f"sold {symbol} @ ${price:.8f} | P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)"

    def check_exits(self, stop_loss=STOP_LOSS_PCT, take_profit=TAKE_PROFIT_PCT):
        actions = 0
        for key in list(self.holdings.keys()):
            pos = self.holdings.get(key)
            if not pos or pos.get('is_real'):
                continue
            sym, chain = pos['symbol'], pos['chain']
            buy_price = pos.get('buy_price', 0)
            if not buy_price:
                continue
            try:
                price = resolve_price(sym, coin_id=pos.get('coin_id', ''),
                                      chain=chain, use_cache=False,
                                      dex_url=pos.get('dex_url', ''))
            except Exception:
                price = 0
            if not price or price <= 0:
                pos['_zero_count'] = pos.get('_zero_count', 0) + 1
                if pos['_zero_count'] >= 1:
                    # Price unavailable = rug. Sell at near-zero.
                    price = buy_price * 0.001
                    print(f"    RUG {sym}: price=0, force stop-loss")
                else:
                    continue
            else:
                pos['_zero_count'] = 0
                pos['_last_price'] = price
            pnl_pct = (price - buy_price) / buy_price * 100
            # SOL/BSC: fast rugs, tight stop. BASE/ETH/ARB: more volatile, wider stop
            pos_data = self.holdings.get(key, {})
            if 'stop_loss_override' in pos_data:
                effective_stop = pos_data['stop_loss_override']
            else:
                effective_stop = {
                    'solana': -20.0, 'bsc': -20.0,
                    'base': -40.0, 'ethereum': -40.0, 'arbitrum': -35.0
                }.get(chain, -30.0)
            if pnl_pct <= effective_stop:
                ok, msg = self.sell(sym, chain, price, 'stop_loss')
                if ok:
                    print(f"    STOP-LOSS {sym}: {pnl_pct:.1f}%")
                    actions += 1
            elif pnl_pct >= take_profit:
                ok, msg = self.sell(sym, chain, price, 'take_profit')
                if ok:
                    print(f"    TAKE-PROFIT {sym}: +{pnl_pct:.1f}%")
                    actions += 1
        return actions

    def print_status(self):
        tv = self._trading_value()
        rv = self._real_value()
        rv_delta = rv - self.starting_real if self.starting_real > 0 else 0
        # Session P&L = sum of all realized trade P&L this session
        sells = [t for t in self.trades if t['action'] == 'SELL']
        session_pnl = sum(t.get('pnl', 0) for t in sells)
        wins   = sum(1 for t in sells if t.get('pnl', 0) > 0)
        losses = sum(1 for t in sells if t.get('pnl', 0) <= 0)
        best  = max(sells, key=lambda t: t.get('pnl_pct', 0), default=None)
        worst = min(sells, key=lambda t: t.get('pnl_pct', 0), default=None)
        best_str  = f"{best['symbol']} {best['pnl_pct']:+.0f}%"  if best  else 'none'
        worst_str = f"{worst['symbol']} {worst['pnl_pct']:+.0f}%" if worst else 'none'
        print(f"\n  {'='*52}")
        print(f"  {self.sim_id} | {datetime.now().strftime('%H:%M:%S')}")
        print(f"  Real portfolio:   ${rv:>10,.2f}  ({rv_delta:+.2f} this session)")
        pnl_str = f"{session_pnl:+.2f}" if sells else "no closed trades"
        print(f"  Session P&L:      ${session_pnl:>10.2f}  (realized: {len(sells)} trades)")
        print(f"  Trades: {len(self.trades)} | W:{wins} L:{losses} | Best: {best_str} | Worst: {worst_str}")
        cash_str = ' | '.join(f"{c}:${v:.0f}" for c, v in self.cash.items() if v > 0)
        print(f"  Cash: {cash_str}")
        open_pos = [(k, v) for k, v in self.holdings.items() if not v.get('is_real')]
        if open_pos:
            print(f"  Open positions:")
            for key, pos in open_pos:
                p = resolve_price(pos['symbol'], coin_id=pos.get('coin_id', ''),
                                  chain=pos['chain'],
                                  dex_url=pos.get('dex_url', ''))
                pct = (p - pos['buy_price']) / pos['buy_price'] * 100 if p and pos['buy_price'] else 0
                val = pos['amount'] * (p or pos['buy_price'])
                direction = 'UP' if pct >= 0 else 'DN'
                print(f"    {direction} {pos['symbol']} ({pos['chain']}) ${val:.2f} | {pct:+.1f}%")
        print(f"  {'='*52}")

    def save(self):
        """Non-blocking save — queues all writes to the dedicated DB writer thread."""
        try:
            init_sim_tables()
        except Exception:
            pass
        new_trades = self.trades[self._saved_count:]
        for t in new_trades:
            if t['action'] == 'BUY':
                _db_exec(
                    "INSERT OR IGNORE INTO sim_portfolio "
                    "(sim_id,symbol,chain,amount_tokens,buy_price_usd,buy_time,"
                    "sell_price_usd,pnl_usd,pnl_pct,status,signal_source) "
                    "VALUES(?,?,?,?,?,?,0,0,0,?,?)",
                    (self.sim_id,t['symbol'],t['chain'],
                     t['tokens'],t['price'],t['time'],'HOLDING',t.get('source','')))
            else:
                _db_exec(
                    "UPDATE sim_portfolio "
                    "SET sell_price_usd=?,sell_time=?,pnl_usd=?,pnl_pct=?,status=? "
                    "WHERE sim_id=? AND symbol=? AND chain=? AND status=?",
                    (t['price'],t['time'],t.get('pnl',0),t.get('pnl_pct',0),
                     'CLOSED',self.sim_id,t['symbol'],t['chain'],'HOLDING'))
        self._saved_count = len(self.trades)
        tv = self._trading_value()
        tp = tv - self.starting_trading
        sells = [t for t in self.trades if t['action'] == 'SELL']
        wins = sum(1 for t in sells if t.get('pnl',0) > 0)
        losses = sum(1 for t in sells if t.get('pnl',0) <= 0)
        best  = max(sells, key=lambda t: t.get('pnl_pct',0), default=None)
        worst = min(sells, key=lambda t: t.get('pnl_pct',0), default=None)
        _db_exec(
            "INSERT OR REPLACE INTO sim_runs "
            "(sim_id,mode,start_time,end_time,starting_usd,ending_usd,"
            "total_pnl_usd,total_pnl_pct,trades_total,trades_won,trades_lost,"
            "best_trade,worst_trade,summary) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.sim_id,'PAPER',
             self.trades[0]['time'] if self.trades else datetime.now().isoformat(),
             datetime.now().isoformat(),
             self.starting_trading, tv, tp,
             tp/max(self.starting_trading,1)*100,
             len(self.trades),wins,losses,
             f"{best['symbol']} {best['pnl_pct']:+.1f}%" if best else 'none',
             f"{worst['symbol']} {worst['pnl_pct']:+.1f}%" if worst else 'none',
             json.dumps({'trading_pnl':tp,'real_value':self._real_value()})))


# ── Price monitor (background thread) ────────────────────────────────────────
def run_price_monitor(portfolio, stop_loss=STOP_LOSS_PCT, take_profit=TAKE_PROFIT_PCT,
                      duration_minutes=370, interval_seconds=10):
    """Checks open positions every 60s -- catches rugs before next cycle."""
    def _loop():
        end = time.time() + duration_minutes * 60
        while time.time() < end:
            try:
                time.sleep(interval_seconds)
                open_pos = [(k,v) for k,v in list(portfolio.holdings.items())
                            if not v.get('is_real')]
                if not open_pos:
                    continue
                for key, pos in open_pos:
                    if key not in portfolio.holdings:
                        continue
                    sym, chain = pos['symbol'], pos['chain']
                    buy_price = pos.get('buy_price', 0)
                    if not buy_price:
                        continue
                    try:
                        # Pass contract so resolve_price uses exact address not symbol search
                        contract = pos.get('contract', pos.get('coin_id', ''))
                        price = resolve_price(sym, coin_id=contract, chain=chain,
                                              use_cache=True,
                                              dex_url=pos.get('dex_url', ''))
                    except Exception:
                        price = 0
                    if not price or price <= 0:
                        pos['_zero_count'] = pos.get('_zero_count', 0) + 1
                        # Fire stop-loss after 1 failed price fetch (rug detected)
                        if pos['_zero_count'] >= 1:
                            price = buy_price * 0.001  # treat as near-zero
                            print(f"\n    [MONITOR] {sym} price=0 -- RUG detected, stop-loss")
                        else:
                            continue
                    else:
                        pos['_zero_count'] = 0
                        pos['_last_price'] = price  # track last known price
                    pnl_pct = (price - buy_price) / buy_price * 100
                    # Use per-position override if set (established coins have tight stops)
                    pos_data = portfolio.holdings.get(f"{sym}_{chain}", {})
                    if 'stop_loss_override' in pos_data:
                        effective_stop = pos_data['stop_loss_override']
                    else:
                        effective_stop = {
                            'solana': -20.0, 'bsc': -20.0,
                            'base': -40.0, 'ethereum': -40.0, 'arbitrum': -35.0
                        }.get(chain, stop_loss)
                    if pnl_pct <= effective_stop:
                        ok, msg = portfolio.sell(sym, chain, price, 'stop_loss')
                        if ok:
                            print(f"\n    [MONITOR] STOP-LOSS {sym}: {pnl_pct:.1f}% | {msg}")
                            try:
                                portfolio.save()
                            except Exception:
                                pass
                    elif pnl_pct >= take_profit:
                        ok, msg = portfolio.sell(sym, chain, price, 'take_profit')
                        if ok:
                            print(f"\n    [MONITOR] TAKE-PROFIT {sym}: +{pnl_pct:.1f}% | {msg}")
                            try:
                                portfolio.save()
                            except Exception:
                                pass
            except Exception as e:
                print(f"\n    [MONITOR] error: {e}")
                time.sleep(5)
    t = threading.Thread(target=_loop, daemon=True, name='price_monitor')
    t.start()
    return t


# ── Agent cycle ───────────────────────────────────────────────────────────────
def _load_dex_proposals(portfolio):
    """
    Load DEX gem proposals directly from dex_gems table.
    Authoritative source for chain + contract. No dedup confusion with buzz/social.
    """
    import sqlite3 as _sq
    proposals = []
    try:
        conn = _sq.connect(MAIN_DB, timeout=10)
        try:
            rows = conn.execute("""
                SELECT symbol, chain, contract_address, dex_url, price_usd,
                       liquidity_usd, age_hours, cross_score, price_change_24h
                FROM dex_gems
                WHERE fetched_at >= datetime('now', '-24 hours')
                AND cross_score >= 5
                ORDER BY cross_score DESC, liquidity_usd DESC
                LIMIT 20
            """).fetchall()
        finally:
            conn.close()

        # Load ban list
        # Ban by symbol across ALL chains — if MUSK rugged on SOL, don't buy on ETH
        stop_lossed_syms = {t['symbol'] for t in portfolio.trades
                            if t['action'] == 'SELL' and t.get('reason') == 'stop_loss'}
        try:
            import json as _j
            with open('sim_ban_list.json') as _f:
                for entry in _j.load(_f):
                    key = entry.split('|')[0]   # strip |date suffix if present
                    stop_lossed_syms.add(key.split('_')[0])
        except Exception:
            pass
        # Also load from DB auto_ban table
        try:
            _bc = sqlite3.connect(MAIN_DB, timeout=3)
            for (bsym,) in _bc.execute("SELECT symbol FROM auto_ban").fetchall():
                stop_lossed_syms.add(bsym.upper())
            _bc.close()
        except Exception:
            pass

        seen = set()
        for sym, chain, contract, dex_url, price_db, liq, age, score, *_extra in rows:
            sym = sym.upper()
            chain = (chain or 'solana').lower()
            if sym in seen:
                continue
            seen.add(sym)
            key = f"{sym}_{chain}"
            if sym in stop_lossed_syms:
                continue
            if key in portfolio.holdings:
                continue
            # Skip majors
            if sym in ('BTC','ETH','SOL','BNB','USDT','USDC','WETH','WSOL','WBTC'):
                continue
            # BSC excluded — no wallet configured
            if chain == 'bsc':
                continue
            liq = liq or 0
            age = age or 99
            # Liquidity minimums per chain
            liq_min = {'solana':25000,'base':35000,'ethereum':50000}.get(chain, 35000)
            if liq < liq_min:
                continue
            # Size by chain
            # Phase 2 trade sizes — based on available wallet balance per chain
            if chain == 'solana':
                trade_usd = 1    # SOL gems: $1
            else:
                trade_usd = 2    # BASE/ETH gems: $2
            price_chg_24h = float(_extra[0]) if _extra and _extra[0] is not None else 0.0
            proposals.append({
                'action': 'BUY',
                'symbol': sym,
                'coin_id': contract or dex_url or sym.lower(),
                'chain': chain,
                'trade_usd': trade_usd,
                'alpha_score': min(100, score * 12 + 20),
                'reasons': f'DEX liq:${liq/1000:.0f}k age:{age:.0f}h score:{score}',
                'sources': 'dex_direct',
                'category': 'DEX_GEM',
                'price_change_24h': price_chg_24h,
                'age_hours': age,
                'dex_url': dex_url,
                'liquidity_usd': liq,
            })
    except Exception as e:
        print(f"    dex_proposals error: {e}")
    return proposals


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
                    'trade_usd': 2,
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
                    'trade_usd': 2,
                    'alpha_score': 68,
                    'reasons': f'DexScreener SOL hot liq:${liq/1000:.0f}k vol:${vol/1000:.0f}k',
                    'sources': 'fallback_dex',
                    'category': 'DEX_GEM',
                })
    except Exception:
        pass

    return proposals[:8]  # cap to 8 fallback proposals



# ── Contract registry: native tokens on SOL / BASE / ETH only ───────────────
# Only tokens that can actually be swapped on-chain — verified contract addresses.
# BTC → not native to SOL/BASE/ETH, tracked as macro signal only, no execution.
# HYPE → Hyperliquid L1 native, no DEX on supported chains, macro signal only.
# The DECISION to trade any token comes entirely from token_intelligence scores.
_CONTRACT_REGISTRY = {
    # ── SOL ecosystem — Jupiter verified mint addresses ──────────────────────
    'SOL':  {'chain': 'solana',   'coin_id': 'So11111111111111111111111111111111111111112',
             'binance': 'SOLUSDT',  'sl': -6.0, 'tp': 12.0, 'max_usd': 2},
    'JUP':  {'chain': 'solana',   'coin_id': 'JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN',
             'binance': 'JUPUSDT',  'sl': -6.0, 'tp': 12.0, 'max_usd': 2},
    'RAY':  {'chain': 'solana',   'coin_id': '4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R',
             'binance': 'RAYUSDT',  'sl': -6.0, 'tp': 12.0, 'max_usd': 2},
    'BONK': {'chain': 'solana',   'coin_id': 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263',
             'binance': 'BONKUSDT', 'sl': -8.0, 'tp': 15.0, 'max_usd': 2},
    'WIF':  {'chain': 'solana',   'coin_id': 'EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm',
             'binance': 'WIFUSDT',  'sl': -8.0, 'tp': 15.0, 'max_usd': 2},
    'PYTH': {'chain': 'solana',   'coin_id': 'HZ1JovNiVvGrVMdPyDmkuMhkVDmZnMHT1N88pQqCpump',
             'binance': 'PYTHUSDT', 'sl': -6.0, 'tp': 12.0, 'max_usd': 2},
    # ── ETH mainnet — ERC-20 verified contract addresses ─────────────────────
    'ETH':  {'chain': 'ethereum', 'coin_id': '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
             'binance': 'ETHUSDT',  'sl': -5.0, 'tp': 10.0, 'max_usd': 2},
    'LINK': {'chain': 'ethereum', 'coin_id': '0x514910771AF9Ca656af840dff83E8264EcF986CA',
             'binance': 'LINKUSDT', 'sl': -5.0, 'tp': 10.0, 'max_usd': 2},
    'AAVE': {'chain': 'ethereum', 'coin_id': '0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9',
             'binance': 'AAVEUSDT', 'sl': -5.0, 'tp': 10.0, 'max_usd': 2},
    'UNI':  {'chain': 'ethereum', 'coin_id': '0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984',
             'binance': 'UNIUSDT',  'sl': -5.0, 'tp': 10.0, 'max_usd': 2},
    'ONDO': {'chain': 'ethereum', 'coin_id': '0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3',
             'binance': 'ONDOUSDT', 'sl': -7.0, 'tp': 14.0, 'max_usd': 2},
    'ENA':  {'chain': 'ethereum', 'coin_id': '0x57e114B691Db790C35207b2e685D4A43181e6061',
             'binance': 'ENAUSDT',  'sl': -8.0, 'tp': 16.0, 'max_usd': 2},
    'LDO':  {'chain': 'ethereum', 'coin_id': '0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32',
             'binance': 'LDOUSDT',  'sl': -7.0, 'tp': 14.0, 'max_usd': 2},
    'PENDLE': {'chain': 'ethereum', 'coin_id': '0x808507121B80c02388fAd14726482e061B8da827',
             'binance': 'PENDLEUSDT', 'sl': -7.0, 'tp': 14.0, 'max_usd': 2},
    'CRV':  {'chain': 'ethereum', 'coin_id': '0xD533a949740bb3306d119CC777fa900bA034cd52',
             'binance': 'CRVUSDT',  'sl': -8.0, 'tp': 16.0, 'max_usd': 2},
    'FET':  {'chain': 'ethereum', 'coin_id': '0xaea46A60368A7bD060eec7DF8CBa43b7EF41Ad85',
             'binance': 'FETUSDT',  'sl': -8.0, 'tp': 16.0, 'max_usd': 2},
    'NEAR': {'chain': 'ethereum', 'coin_id': '0x85F17Cf997934a597031b2E18a9aB6ebD4B9f6a4',
             'binance': 'NEARUSDT', 'sl': -8.0, 'tp': 16.0, 'max_usd': 2},
    'PEPE': {'chain': 'ethereum', 'coin_id': '0x6982508145454Ce325dDbE47a25d4ec3d2311933',
             'binance': 'PEPEUSDT', 'sl': -10.0, 'tp': 20.0, 'max_usd': 1},
    'SHIB': {'chain': 'ethereum', 'coin_id': '0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE',
             'binance': 'SHIBUSDT', 'sl': -10.0, 'tp': 18.0, 'max_usd': 1},
    # ── BASE — native BASE ecosystem tokens ──────────────────────────────────
    'AERO': {'chain': 'base',     'coin_id': '0x940181a94A35A4569E4529A3CDfB74e38FD98631',
             'binance': 'AEROUSDT', 'sl': -6.0, 'tp': 12.0, 'max_usd': 2},
    'VIRTUAL': {'chain': 'base',  'coin_id': '0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b',
             'binance': 'VIRTUALUSDT', 'sl': -8.0, 'tp': 16.0, 'max_usd': 2},
    # BTC + HYPE → macro sentiment signals only, no on-chain execution
    # (BTC lives on its own chain; HYPE is Hyperliquid L1 native)
    # They stay in token_intelligence TRACKED_TOKENS for regime filtering.
    '_MACRO_SIGNAL_ONLY': {'BTC', 'HYPE', 'SUI', 'AVAX', 'INJ'},
}


def _load_listing_proposals(portfolio):
    """
    Load exchange listing signals from signals table (populated by exchange_feeds.py).
    Tier 2 exchanges (KuCoin/Gate/MEXC/OKX) = highest alpha, handled with priority.
    Only proposes if coin has a resolvable SOL mint (safest chain for low-gas listing plays).
    """
    import sqlite3 as _sq
    proposals = []
    try:
        conn = _sq.connect(MAIN_DB, timeout=10)
        rows = conn.execute("""
            SELECT coin, title, engagement, source_detail
            FROM signals
            WHERE source='exchange' AND signal_type='LISTING'
            AND fetched_at >= datetime('now', '-4 hours')
            AND engagement >= 100
            ORDER BY engagement DESC LIMIT 8
        """).fetchall()
        conn.close()
        stop_lossed_syms = {t['symbol'] for t in portfolio.trades
                            if t['action'] == 'SELL' and t.get('reason') == 'stop_loss'}
        try:
            with open('sim_ban_list.json') as _f:
                for entry in json.load(_f):
                    stop_lossed_syms.add(entry.split('_')[0])
        except Exception:
            pass
        for coin, title, priority, exchange_detail in rows:
            if not coin:
                continue
            for sym in coin.split(','):
                sym = sym.strip().upper()
                if not sym or sym in ('USDT', 'USDC', 'USD', 'BTC', 'ETH', 'SOL', 'BNB'):
                    continue
                if sym in stop_lossed_syms:
                    continue
                if f"{sym}_solana" in portfolio.holdings:
                    continue
                # Default to SOL for listing plays (lowest gas, fastest execution)
                proposals.append({
                    'action': 'BUY',
                    'symbol': sym,
                    'coin_id': '',  # will be resolved by resolve_price via DexScreener
                    'chain': 'solana',
                    'trade_usd': 1,
                    'alpha_score': min(90, 50 + priority // 4),
                    'reasons': f'Listing: {exchange_detail} — {title[:50]}',
                    'sources': 'exchange_listing',
                    'category': 'LISTING',
                })
    except Exception as e:
        print(f"    listing proposals error: {e}")
    return proposals



def _established_curve(binance_pair):
    """Multi-timeframe Binance curve for established-token entries."""
    try:
        r = requests.get(
            'https://api.binance.com/api/v3/klines',
            params={'symbol': binance_pair, 'interval': '15m', 'limit': 17},
            timeout=6)
        if r.status_code != 200:
            return None
        k15 = r.json()
        if len(k15) < 5:
            return None
        last = float(k15[-1][4])
        open_15m = float(k15[-2][1])
        open_1h = float(k15[-5][1])
        open_4h = float(k15[0][1])
        vol_recent = sum(float(k[5]) for k in k15[-4:])
        vol_prior = sum(float(k[5]) for k in k15[-8:-4]) if len(k15) >= 8 else 0
        return {
            'price': last,
            'chg_15m': (last - open_15m) / open_15m * 100 if open_15m else 0,
            'chg_1h': (last - open_1h) / open_1h * 100 if open_1h else 0,
            'chg_4h': (last - open_4h) / open_4h * 100 if open_4h else 0,
            'volume_ratio': vol_recent / max(vol_prior, 1),
        }
    except Exception:
        return None


def _load_established_proposals(portfolio):
    """
    Dynamic established token proposals driven entirely by token_intelligence scores.

    Flow:
      1. Read ALL tokens scored in token_intelligence table (last 2 hours)
      2. BTC/HYPE → macro regime signals only (no execution)
         If BTC composite < -0.3 → suppress all buys (bear regime)
      3. Filter: only BUY/STRONG_BUY signals, score >= +0.1
      4. Look up contract in _CONTRACT_REGISTRY — skip if unknown or macro-only
      5. Verify price momentum from Binance (not crashing)
      6. Propose with $2 cap and tight SL/TP from registry

    New tokens get added automatically by updating token_intelligence.py TRACKED_TOKENS.
    No changes to simulation.py needed.
    """
    import sqlite3 as _sq
    proposals = []
    macro_only = _CONTRACT_REGISTRY.get('_MACRO_SIGNAL_ONLY', set())
    real_syms = {pos['symbol'] for positions in REAL_PORTFOLIO.values() for pos in positions}

    try:
        conn = _sq.connect(MAIN_DB, timeout=10)

        # Step 1: read all fresh token_intelligence scores
        rows = conn.execute("""
            SELECT symbol, composite_score, signal, confidence, notes,
                   momentum_score, trending_score, news_score, reddit_score,
                   twitter_score, price_24h_change, price_7d_change
            FROM token_intelligence
            WHERE id IN (
                SELECT MAX(id) FROM token_intelligence
                WHERE fetched_at >= datetime('now', '-2 hours')
                GROUP BY symbol
            )
            ORDER BY composite_score DESC
        """).fetchall()
        conn.close()

        if not rows:
            print("    📊 Established: no fresh token intelligence data")
            return []

        # Step 2: BTC macro regime gate — bearish BTC only permits strongest setups.
        btc_score = next((r[1] for r in rows if r[0].upper() == 'BTC'), None)
        bear_regime = btc_score is not None and btc_score < -0.3
        if bear_regime:
            print(f"    📊 Established: BTC macro score {btc_score:.2f} → bear regime, requiring STRONG_BUY + uptrend")

        ranked = []
        for row in rows:
            (sym, score, signal, conf, notes, mom_score, trend_score,
             news_score, reddit_score, twitter_score, chg_24_db, chg_7d_db) = row
            sym = sym.upper()

            # Skip macro-signal-only tokens — no on-chain execution possible
            if sym in macro_only:
                continue

            # Step 3: intelligence BUYs are primary. Neutral curated tokens can
            # still become small ACCUMULATE entries when the curve confirms.
            intelligence_buy = signal in ('BUY', 'STRONG_BUY') and score >= 0.08

            # Step 4: must have a known tradeable contract — no guessing
            registry = _CONTRACT_REGISTRY.get(sym)
            if not registry or not isinstance(registry, dict):
                continue  # not in registry or is a set (macro-only marker)

            chain = registry['chain']
            if chain not in CHAINS:
                continue
            if sym in real_syms and not ESTABLISHED_ALLOW_TOPUPS:
                continue  # already holding in real wallet
            if f"{sym}_{chain}" in portfolio.holdings:
                continue  # already in sim position

            # Step 5: live price check — not crashing, not overbought, and not rolling over.
            try:
                r = requests.get(
                    f"https://api.binance.com/api/v3/ticker/24hr?symbol={registry['binance']}",
                    timeout=5)
                if r.status_code != 200:
                    continue
                data = r.json()
                price = float(data.get('lastPrice', 0))
                chg = float(data.get('priceChangePercent', 0))
                if price <= 0 or chg > 10 or chg < -12:
                    continue  # skip if pumped >10% or dumping >12%
                curve = _established_curve(registry['binance'])
                if not curve:
                    continue
                if curve['chg_15m'] < -1.5:
                    continue
                if curve['chg_1h'] < -2.0:
                    continue
                if curve['chg_1h'] < 0 and curve['chg_4h'] < 0:
                    continue
                if bear_regime and curve['chg_1h'] <= 0:
                    continue
                technical_buy = (
                    signal == 'NEUTRAL'
                    and -6.0 <= chg <= 6.0
                    and curve['chg_15m'] > -0.5
                    and curve['chg_1h'] > 0.25
                    and curve['chg_4h'] > -1.0
                    and curve.get('volume_ratio', 0) >= 0.6
                )
                if not intelligence_buy and not technical_buy:
                    continue
                if bear_regime and not intelligence_buy:
                    continue
            except Exception:
                continue

            # Step 6: rank across the broader established universe. This lets
            # capital rotate into current sector leaders instead of only buying
            # whatever is already in the wallet.
            source_strength = (
                max(float(news_score or 0), 0) * 12
                + max(float(twitter_score or 0), 0) * 10
                + max(float(reddit_score or 0), 0) * 5
                + max(float(trend_score or 0), 0) * 8
            )
            curve_strength = (
                max(curve['chg_1h'], 0) * 4
                + max(curve['chg_4h'], 0) * 2
                + max(curve['chg_15m'], 0) * 2
                + max(curve.get('volume_ratio', 1) - 1, 0) * 3
            )
            rotation_score = (
                60
                + max(float(score or 0), 0) * 60
                + max(float(mom_score or 0), 0) * 15
                + source_strength
                + curve_strength
            )
            trade_usd = registry['max_usd'] if intelligence_buy else min(1, registry['max_usd'])
            setup_label = signal if intelligence_buy else 'TECHNICAL_ACCUMULATE'
            ranked.append((rotation_score, {
                'action': 'BUY' if intelligence_buy else 'ACCUMULATE',
                'symbol': sym,
                'chain': chain,
                'coin_id': registry['coin_id'],
                'price': price,
                'trade_usd': trade_usd,
                'stop_loss_override': registry['sl'],
                'take_profit_override': registry['tp'],
                'alpha_score': min(95, int(rotation_score)),
                'cross_score': 8,
                'sources': f'intel:score={score:.2f},sig={setup_label}',
                'reasons': [f'intel_{sym}:{setup_label}:{notes[:40]} '
                            f"curve15m:{curve['chg_15m']:+.1f}% "
                            f"curve1h:{curve['chg_1h']:+.1f}% "
                            f"curve4h:{curve['chg_4h']:+.1f}% "
                            f"news:{float(news_score or 0):+.2f} "
                            f"tw:{float(twitter_score or 0):+.2f} "
                            f"rank:{rotation_score:.0f}"],
                'category': 'ESTABLISHED',
                'liquidity_usd': 999_000_000,
                'age_hours': 0,
                'price_change_24h': chg,
                'established_curve': curve,
                'rotation_score': rotation_score,
            }))

        ranked.sort(key=lambda item: item[0], reverse=True)
        proposals = [p for _, p in ranked[:max(1, ESTABLISHED_MAX_PROPOSALS)]]

    except Exception as e:
        print(f'  established error: {e}')

    if proposals:
        syms = ', '.join(f"{p['symbol']}({p['sources'].split('=')[1][:5]})" for p in proposals)
        print(f"    📊 Established signals: {syms}")
    else:
        print("    📊 Established: no BUY signals from intelligence")
    return proposals

# Dry-run state is read fresh so changing .env takes effect after restart/import
# without relying on executor.DRY_RUN's import-time compatibility value.
def _executor_dry_run():
    try:
        from executor import _is_dry_run
        return _is_dry_run()
    except Exception:
        return True


DAILY_LOSS_LIMIT_USD = 200.0   # Stop new buys if trading P&L drops below -$200


def _ensure_strategy_tables():
    """Tables for deferred candidates, skip reasons, and price snapshots."""
    try:
        conn = sqlite3.connect(SIM_DB, timeout=10)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('''CREATE TABLE IF NOT EXISTS buy_candidates (
            key TEXT PRIMARY KEY,
            symbol TEXT,
            chain TEXT,
            category TEXT,
            coin_id TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            seen_count INTEGER DEFAULT 0,
            first_price REAL DEFAULT 0,
            last_price REAL DEFAULT 0,
            last_score REAL DEFAULT 0,
            status TEXT DEFAULT 'WATCHING',
            last_reason TEXT DEFAULT '',
            snapshot_json TEXT DEFAULT ''
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS agent_skips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            chain TEXT,
            category TEXT,
            reason TEXT,
            price_usd REAL DEFAULT 0,
            proposal_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS token_price_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            chain TEXT,
            coin_id TEXT,
            price_usd REAL,
            liquidity_usd REAL,
            volume_24h REAL,
            price_change_m5 REAL,
            price_change_h1 REAL,
            price_change_h6 REAL,
            price_change_24h REAL,
            buy_sell_ratio REAL,
            sampled_at TEXT DEFAULT (datetime('now'))
        )''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"    strategy table init error: {e}")


def _skip_proposal(p, reason, price=0):
    """Log a skipped proposal so dry-run tuning has evidence."""
    sym = p.get('symbol', '')
    chain = p.get('chain', '')
    cat = p.get('category', '')
    print(f"    SKIP {sym} -- {reason}")
    try:
        _ensure_strategy_tables()
        conn = sqlite3.connect(SIM_DB, timeout=5)
        conn.execute(
            "INSERT INTO agent_skips (symbol,chain,category,reason,price_usd,proposal_json) "
            "VALUES (?,?,?,?,?,?)",
            (sym, chain, cat, reason[:240], float(price or 0),
             json.dumps(p, default=str)[:3000]))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _best_dex_pair(contract, chain):
    """Fetch best-liquidity DexScreener pair for a contract on the expected chain."""
    if not contract or len(str(contract)) < 20:
        return None
    try:
        r = requests.get(
            f'https://api.dexscreener.com/latest/dex/tokens/{contract}',
            timeout=8)
        if r.status_code != 200:
            return None
        pairs = r.json().get('pairs', []) or []
        if chain:
            same_chain = [p for p in pairs
                          if p.get('chainId', '').lower() == chain.lower()]
            pairs = same_chain or pairs
        if not pairs:
            return None
        return max(pairs, key=lambda p: float(
            p.get('liquidity', {}).get('usd', 0) or 0))
    except Exception:
        return None


def _market_snapshot(symbol, chain, coin_id, fallback_price=0):
    """Current DEX curve/pressure snapshot used before any speculative buy."""
    pair = _best_dex_pair(coin_id, chain)
    snap = {
        'price': float(fallback_price or 0),
        'liquidity': 0.0,
        'volume_24h': 0.0,
        'm5': 0.0,
        'h1': 0.0,
        'h6': 0.0,
        'h24': 0.0,
        'buy_sell_ratio': 0.0,
        'source': 'none',
        'dex_url': '',
    }
    if pair:
        pc = pair.get('priceChange', {}) or {}
        txns = pair.get('txns', {}) or {}
        h24_tx = txns.get('h24', {}) or {}
        h1_tx = txns.get('h1', {}) or {}
        buys = int(h1_tx.get('buys', 0) or h24_tx.get('buys', 0) or 0)
        sells = int(h1_tx.get('sells', 0) or h24_tx.get('sells', 0) or 0)
        snap.update({
            'price': float(pair.get('priceUsd', 0) or fallback_price or 0),
            'liquidity': float(pair.get('liquidity', {}).get('usd', 0) or 0),
            'volume_24h': float(pair.get('volume', {}).get('h24', 0) or 0),
            'm5': float(pc.get('m5', 0) or 0),
            'h1': float(pc.get('h1', 0) or 0),
            'h6': float(pc.get('h6', 0) or 0),
            'h24': float(pc.get('h24', 0) or 0),
            'buy_sell_ratio': buys / max(sells, 1),
            'source': pair.get('dexId', 'dexscreener'),
            'dex_url': pair.get('url', ''),
        })
    try:
        _ensure_strategy_tables()
        conn = sqlite3.connect(SIM_DB, timeout=5)
        conn.execute(
            "INSERT INTO token_price_samples "
            "(symbol,chain,coin_id,price_usd,liquidity_usd,volume_24h,"
            "price_change_m5,price_change_h1,price_change_h6,price_change_24h,"
            "buy_sell_ratio) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, chain, coin_id, snap['price'], snap['liquidity'],
             snap['volume_24h'], snap['m5'], snap['h1'], snap['h6'],
             snap['h24'], snap['buy_sell_ratio']))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return snap


def _candidate_watchlist_gate(p, price, snapshot):
    """
    Defer speculative buys until the same candidate survives more than one cycle.
    Established coins skip this because Binance/intelligence gates already refresh.
    """
    if p.get('category') == 'ESTABLISHED':
        return True, 'established'
    _ensure_strategy_tables()
    key = f"{p.get('_sim_id','')}_{p.get('symbol','')}_{p.get('chain','')}_{p.get('coin_id','')[:18]}"
    now = datetime.now()
    try:
        conn = sqlite3.connect(SIM_DB, timeout=5)
        row = conn.execute(
            "SELECT first_seen_at, seen_count, first_price FROM buy_candidates WHERE key=?",
            (key,)).fetchone()
        if row:
            first_seen_raw, seen_count, first_price = row
            try:
                first_seen = datetime.fromisoformat(first_seen_raw)
            except Exception:
                first_seen = now
            seen_count = int(seen_count or 0) + 1
            first_price = float(first_price or price or 0)
            age_min = (now - first_seen).total_seconds() / 60
            status = 'READY' if (seen_count >= MIN_WATCHLIST_SEEN or
                                 age_min >= MIN_WATCHLIST_AGE_MIN) else 'WATCHING'
            conn.execute(
                "UPDATE buy_candidates SET last_seen_at=?, seen_count=?, last_price=?, "
                "last_score=?, status=?, last_reason=?, snapshot_json=? WHERE key=?",
                (now.isoformat(), seen_count, price, p.get('alpha_score', 0),
                 status, 'seen_again', json.dumps(snapshot, default=str)[:2000], key))
            conn.commit()
            conn.close()
            if status == 'READY':
                if first_price > 0 and price < first_price * 0.98:
                    return False, f'watchlist price declined {((price-first_price)/first_price*100):.1f}% since first seen'
                return True, f'watchlist ready ({seen_count} sightings, {age_min:.1f}m)'
            return False, f'watching candidate ({seen_count}/{MIN_WATCHLIST_SEEN} sightings, {age_min:.1f}m)'
        conn.execute(
            "INSERT OR REPLACE INTO buy_candidates "
            "(key,symbol,chain,category,coin_id,first_seen_at,last_seen_at,"
            "seen_count,first_price,last_price,last_score,status,last_reason,snapshot_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, p.get('symbol',''), p.get('chain',''), p.get('category',''),
             p.get('coin_id',''), now.isoformat(), now.isoformat(), 1, price,
             price, p.get('alpha_score', 0), 'WATCHING', 'first_seen',
             json.dumps(snapshot, default=str)[:2000]))
        conn.commit()
        conn.close()
    except Exception:
        return True, 'watchlist unavailable'
    return False, 'watching candidate (first sighting)'


def _social_snapshot(symbol, chain):
    try:
        from social_monitor import get_social_signal
        return get_social_signal(symbol, chain)
    except Exception:
        return None


def _speculative_market_gate(p, price):
    """Curve + social confirmation for gems/listings before buy."""
    cat = p.get('category', '')
    if cat == 'ESTABLISHED':
        return True, 'established', {}
    sym = p.get('symbol', '')
    chain = p.get('chain', '')
    coin_id = p.get('coin_id', '')
    snap = _market_snapshot(sym, chain, coin_id, fallback_price=price)
    social = _social_snapshot(sym, chain)
    social_sig = (social or {}).get('signal', 'UNKNOWN')

    h1 = snap.get('h1', 0)
    h24 = float(p.get('price_change_24h', 0) or snap.get('h24', 0) or 0)
    m5 = snap.get('m5', 0)
    ratio = snap.get('buy_sell_ratio', 0)

    if social and social_sig in ('SELL', 'WATCH_OUT'):
        return False, f'social veto {social_sig}', snap
    if h24 < -25:
        return False, f'price curve declining {h24:.1f}%/24h', snap
    if h1 < -5:
        return False, f'price curve declining {h1:.1f}%/1h', snap
    if m5 < -8:
        return False, f'short-term dump {m5:.1f}%/5m', snap
    if ratio and ratio < 0.75:
        return False, f'sell pressure buy/sell={ratio:.2f}', snap

    # If social attention exists, require price confirmation rather than buying mentions alone.
    if social and int(social.get('tweets') or 0) >= 3 and h1 < 0 and h24 < 0:
        return False, 'mentions present but price curve is declining', snap

    # For DEX gems without any cached social confirmation, require stronger market proof.
    if cat in ('DEX_GEM', 'LISTING') and not social:
        if h1 < 0 and h24 <= 0:
            return False, 'no social cache and curve is not positive', snap
        if ratio and ratio < 1.0:
            return False, f'no social cache and weak buy pressure {ratio:.2f}', snap

    return True, 'curve/social ok', snap


def _ai_risk_gate(p):
    """Optional paid OpenAI/token-validator veto after cheap checks pass."""
    if not ENABLE_AI_RISK_VETO:
        return True, 'disabled'
    if p.get('category') not in ('DEX_GEM', 'LISTING'):
        return True, 'not_applicable'
    contract = p.get('coin_id', '')
    if not contract or len(contract) < 20:
        return True, 'no_contract'
    try:
        from token_validator import validate_dex_gem
        row = {
            'symbol': p.get('symbol', ''),
            'contract_address': contract,
            'chain': p.get('chain', ''),
            'name': p.get('symbol', ''),
            'liquidity_usd': p.get('liquidity_usd', 0),
            'volume_24h': p.get('volume_24h', 0),
            'price_change_24h': p.get('price_change_24h', 0),
            'age_hours': p.get('age_hours', 0),
            'dex': p.get('dex', ''),
        }
        result = validate_dex_gem(row, openai_key='')
        verdict = result.get('verdict', 'UNKNOWN')
        total = int(result.get('total_score', 0) or 0)
        if verdict == 'AVOID' or total < AI_MIN_TOTAL_SCORE:
            return False, f'AI/token validator veto {verdict} score:{total}/20'
        return True, f'AI/token validator {verdict} score:{total}/20'
    except Exception as e:
        return False, f'AI/token validator failed closed: {str(e)[:80]}'


def _proposal_family(p):
    """Normalize proposal categories into portfolio allocation buckets."""
    cat = (p.get('category') or '').upper()
    if cat == 'ESTABLISHED' or 'intel:' in str(p.get('sources', '')):
        return 'ESTABLISHED'
    if cat in ('LISTING', 'NEW_LISTING'):
        return 'LISTING'
    if cat in ('DEX_GEM', 'TRENDING') or 'dex' in str(p.get('sources', '')).lower():
        return 'GEM'
    return 'GEM'


def _category_targets():
    total = max(TARGET_ESTABLISHED_PCT + TARGET_GEMS_PCT + TARGET_LISTINGS_PCT, 0.01)
    return {
        'ESTABLISHED': TARGET_ESTABLISHED_PCT / total,
        'GEM': TARGET_GEMS_PCT / total,
        'LISTING': TARGET_LISTINGS_PCT / total,
    }


def _category_exposure(portfolio):
    exposure = {'ESTABLISHED': 0.0, 'GEM': 0.0, 'LISTING': 0.0}
    for pos in portfolio.holdings.values():
        if pos.get('is_real'):
            continue
        family = _proposal_family(pos)
        price = resolve_price(
            pos.get('symbol', ''),
            coin_id=pos.get('coin_id', ''),
            chain=pos.get('chain', ''),
            dex_url=pos.get('dex_url', ''),
        ) or pos.get('buy_price', 0)
        exposure[family] = exposure.get(family, 0) + pos.get('amount', 0) * price
    return exposure


def _risk_adjusted_score(p, exposure, total_value):
    family = _proposal_family(p)
    score = float(p.get('rotation_score', p.get('alpha_score', 0)) or 0)

    if family == 'ESTABLISHED':
        score += 8
        curve = p.get('established_curve') or {}
        score += max(float(curve.get('chg_1h', 0) or 0), 0) * 2
    elif family == 'LISTING':
        score -= 6
    else:
        # Gems can outperform, but they need a bigger discount for rug/price-data risk.
        score -= 14
        liq = float(p.get('liquidity_usd', 0) or 0)
        if liq < 50_000:
            score -= 5
        if float(p.get('price_change_24h', 0) or 0) < 0:
            score -= 4

    targets = _category_targets()
    target_value = total_value * targets.get(family, 0.2)
    current_value = exposure.get(family, 0)
    if target_value > 0:
        underweight = (target_value - current_value) / target_value
        score += max(min(underweight, 1.0), -1.0) * 10
    return round(score, 2)


def _category_trade_cap(family):
    if family == 'ESTABLISHED':
        return SIM_ESTABLISHED_MAX_USD
    if family == 'LISTING':
        return SIM_LISTING_MAX_USD
    return SIM_GEM_MAX_USD


def _rank_and_size_proposals(portfolio, proposals):
    """Professional-style allocator: rank all opportunities before spending."""
    exposure = _category_exposure(portfolio)
    total_value = max(portfolio._trading_value(), 1)
    targets = _category_targets()
    ranked = []
    for p in proposals:
        family = _proposal_family(p)
        score = _risk_adjusted_score(p, exposure, total_value)
        p['_family'] = family
        p['_allocation_score'] = score
        p['_target_pct'] = targets.get(family, 0)

        target_value = total_value * targets.get(family, 0.2)
        category_room = max(target_value - exposure.get(family, 0), 0)
        if category_room <= 0 and score < 90:
            p['_allocator_skip'] = f'{family.lower()} bucket already at target'
            ranked.append((score, p))
            continue
        chain_cash = portfolio.cash.get(p.get('chain', 'solana'), 0)
        desired = min(float(p.get('trade_usd', 0) or 0),
                      _category_trade_cap(family),
                      chain_cash)
        if family != 'ESTABLISHED':
            desired = min(desired, max(category_room, _category_trade_cap(family) * 0.5))
        elif category_room > 0:
            desired = min(desired, category_room)
        p['trade_usd'] = max(0, round(desired, 4))
        ranked.append((score, p))

    ranked.sort(key=lambda item: item[0], reverse=True)
    summary = []
    for score, p in ranked[:8]:
        summary.append(f"{p.get('symbol')}:{p.get('_family')}:{score:.0f}:${p.get('trade_usd',0):.2f}")
    if summary:
        print("    Allocator: " + " | ".join(summary))
    return [p for _, p in ranked]

def run_agent_cycle(portfolio, stop_loss=STOP_LOSS_PCT, take_profit=TAKE_PROFIT_PCT):
    actions = 0
    _ensure_strategy_tables()

    # Circuit breaker — stop new buys if daily loss limit hit
    trading_pnl = portfolio._trading_value() - portfolio.starting_trading
    if trading_pnl < -DAILY_LOSS_LIMIT_USD:
        print(f"    🛑 Daily loss limit hit (${trading_pnl:.0f}) — checking exits only, no new buys")
        portfolio.check_exits(stop_loss, take_profit)
        return 0

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
                trade_usd = min(2, portfolio.cash.get(ch, 0))
                if price > 0 and key not in portfolio.holdings and trade_usd > 0 and portfolio.can_buy(ch, trade_usd):
                    ok, msg = portfolio.buy(sym, ch, trade_usd, price, 'portfolio_signal')
                    if ok:
                        print(f"    SIGNAL BUY {sym} ${trade_usd:.0f} @ ${price:.4f}")
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

    # 3. DEX gem proposals — load directly from DB, bypassing wallet_agent chain confusion
    proposals = _load_dex_proposals(portfolio)
    # Add established coin proposals (sentiment-driven)
    try:
        established = _load_established_proposals(portfolio)
        proposals = proposals + established
    except Exception as _ep:
        print(f"  established proposals error: {_ep}")
    # Add exchange listing proposals (KuCoin/Gate/MEXC tier-2 alpha)
    try:
        listing_props = _load_listing_proposals(portfolio)
        if listing_props:
            print(f"    📢 Listing signals: {len(listing_props)}")
        proposals = proposals + listing_props
    except Exception as _lp:
        print(f"  listing proposals error: {_lp}")

    # 3b. Supplement with wallet_agent for non-DEX signals (listings, exchange alerts etc)
    try:
        from wallet_agent import evaluate_signals
        wa_proposals = evaluate_signals() or []
        dex_syms = {p.get('symbol') for p in proposals}
        for p in wa_proposals:
            if p.get('action') in ('SKIP', None):
                continue
            sym = p.get('symbol', '')
            if not sym:
                continue
            # DEX_GEM: only skip if _load_dex_proposals already has this symbol
            # (avoids dropping valid social/buzz signals that happen to be DEX gems)
            if p.get('category') == 'DEX_GEM' and sym in dex_syms:
                continue
            # Ensure coin_id is resolved — wallet_agent signals without contract
            # are still useful if we can resolve them; drop only if truly empty
            if not p.get('coin_id') and p.get('chain') in ('ethereum', 'base'):
                continue  # EVM without contract → can't execute, skip
            if sym not in dex_syms:
                proposals.append(p)
    except Exception as e:
        print(f"    wallet_agent error: {e}")

    actionable = [p for p in proposals if p.get('action') not in ('SKIP', None)]
    if not actionable:
        print(f"    No proposals this cycle — waiting for DEX gems")
        proposals = []  # disabled: fallback bought CoinGecko trending w/ no Jupiter route
    else:
        print(f"    Proposals: " + " | ".join(
            f"{p['action']} {p['symbol']}({p.get('chain','?')[:3]})" for p in actionable[:10]))
        proposals = _rank_and_size_proposals(portfolio, proposals)

    # Ban by symbol across all chains — strip |date suffix from dated entries
    stop_lossed_syms = {t['symbol'] for t in portfolio.trades
                        if t['action'] == 'SELL' and t.get('reason') == 'stop_loss'}
    closed_this_session = {f"{t['symbol']}_{t['chain']}" for t in portfolio.trades
                           if t['action'] == 'SELL'}
    try:
        with open('sim_ban_list.json') as _f:
            for entry in json.load(_f):
                key = entry.split('|')[0]   # strip |date suffix if present
                stop_lossed_syms.add(key.split('_')[0])
    except Exception:
        pass

    chain_counts = {}
    for key, pos in portfolio.holdings.items():
        if not pos.get('is_real'):
            ch = pos['chain']
            chain_counts[ch] = chain_counts.get(ch, 0) + 1

    new_buys_this_cycle = 0

    for p in proposals:
        if p.get('action') == 'SKIP':
            continue

        sym      = p.get('symbol', '')
        chain    = p.get('chain', 'solana')
        action   = p.get('action', '')
        cat      = p.get('category', '')
        # Per-chain hard caps
        chain_caps = {'solana': 1, 'base': 2, 'ethereum': 2}
        trade_usd = min(p.get('trade_usd', 20), chain_caps.get(chain, 20))

        if not sym or action not in ('BUY', 'ACCUMULATE'):
            continue
        if new_buys_this_cycle >= SIM_MAX_NEW_BUYS_PER_CYCLE:
            _skip_proposal(p, 'max new buys reached for this cycle')
            continue
        if p.get('_allocator_skip'):
            _skip_proposal(p, p['_allocator_skip'])
            continue
        if float(p.get('_allocation_score', p.get('alpha_score', 0)) or 0) < SIM_MIN_ALLOC_SCORE:
            _skip_proposal(p, f"allocation score too low ({p.get('_allocation_score', 0):.0f})")
            continue
        if trade_usd <= 0:
            _skip_proposal(p, 'allocator assigned zero trade size')
            continue

        # PORTFOLIO category = real holdings, agent shouldn't sim-trade these
        if cat == 'PORTFOLIO':
            continue

        # bitcoin has no DEX / sim cash
        if chain == 'bitcoin':
            continue

        # Block offensive/inappropriate token names
        _BLOCKED_TERMS = {'nigga','nigger','negro','nazi','hitler','rape','isis','porn'}
        if any(t in sym.lower() for t in _BLOCKED_TERMS):
            _skip_proposal(p, 'blocked offensive token name')
            continue

        # Block obvious low-quality / scam name patterns
        _SCAM_PATTERNS = ['nocoin','noscam','rugpull','honeypot','scamcoin',
                          'ponzi','fakeusd','fakebtc','fakeeth','fakesol']
        if any(p in sym.lower() for p in _SCAM_PATTERNS):
            _skip_proposal(p, 'blocked scam-like token name')
            continue

        # SOL: PumpFun tokens — executor now handles bonding curve directly
        # Ungraduated tokens go through _pumpfun_buy, graduated through Jupiter
        # No pre-filtering needed here — executor decides the route
        if chain == 'solana' and not _executor_dry_run():
            coin_id = p.get('coin_id', '')
            if coin_id and len(coin_id) > 30:
                try:
                    from executor import _is_pumpfun_graduated as _grad
                    if not _grad(coin_id):
                        print(f"    NOTE {sym} — PumpFun token (bonding curve, will buy direct)")
                except Exception:
                    pass


        key = f"{sym}_{chain}"
        if key in portfolio.holdings:
            continue
        if key in closed_this_session:
            _skip_proposal(p, 'already closed this session; no rebuy loop')
            continue
        if sym in stop_lossed_syms:
            _skip_proposal(p, 'symbol in stop-loss/ban list')
            continue
        chain_limit = 4 if chain in ('solana', 'bsc') else 3
        if chain_counts.get(chain, 0) >= chain_limit:
            _skip_proposal(p, f'chain position limit reached for {chain}')
            continue
        if not portfolio.can_buy(chain, trade_usd):
            _skip_proposal(p, f'insufficient paper cash for {chain}')
            continue

        # Always fetch live price — never trust stale DB price_usd
        price = resolve_price(sym, coin_id=p.get('coin_id', ''), chain=chain,
                              use_cache=False, dex_url=p.get('dex_url', ''))
        if not price or price <= 0:
            _skip_proposal(p, f'price unavailable on {chain}')
            continue
        if price < 1e-9:
            _skip_proposal(p, f'price dust (${price:.2e})', price=price)
            continue

        # ATH/rug check — if token already crashed >80% in 24h, skip
        # This catches post-rug tokens and pump-and-dumps
        price_change_24h = float(p.get('price_change_24h', 0) or 0)
        if price_change_24h < -80:
            _skip_proposal(p, f'crashed {price_change_24h:.0f}% in 24h (likely rug)', price=price)
            _write_persistent_ban(sym, chain, f'crashed {price_change_24h:.0f}% in 24h')
            continue
        # Additional check: if age < 6h and already down >50%, fast dump
        age_hours = float(p.get('age_hours', 999) or 999)
        if age_hours < 6 and price_change_24h < -50:
            _skip_proposal(p, f'fast dump {price_change_24h:.0f}% in {age_hours:.1f}h', price=price)
            _write_persistent_ban(sym, chain, f'fast dump {price_change_24h:.0f}%')
            continue

        market_ok, market_reason, market_snapshot = _speculative_market_gate(p, price)
        if not market_ok:
            _skip_proposal(p, market_reason, price=price)
            continue
        if market_snapshot.get('dex_url') and not p.get('dex_url'):
            p['dex_url'] = market_snapshot['dex_url']

        p['_sim_id'] = portfolio.sim_id
        watch_ok, watch_reason = _candidate_watchlist_gate(p, price, market_snapshot)
        if not watch_ok:
            _skip_proposal(p, watch_reason, price=price)
            continue

        ai_ok, ai_reason = _ai_risk_gate(p)
        if not ai_ok:
            _skip_proposal(p, ai_reason, price=price)
            continue

        ok, msg = portfolio.buy(sym, chain, trade_usd, price, p.get('sources', 'agent'),
                              contract=p.get('coin_id', ''),
                              dex_url=p.get('dex_url', ''),
                              category=p.get('_family') or p.get('category', ''),
                              allocation_score=p.get('_allocation_score', 0))
        # Store override stop-loss/take-profit for established coins
        if ok and p.get('stop_loss_override'):
            key = f"{sym}_{chain}"
            if key in portfolio.holdings:
                portfolio.holdings[key]['stop_loss_override'] = p['stop_loss_override']
                portfolio.holdings[key]['take_profit_override'] = p.get('take_profit_override', TAKE_PROFIT_PCT)
        if ok:
            print(f"    BUY {sym} ${trade_usd:.0f} @ ${price:.8f} | {str(p.get('reasons', ''))[:50]}")
            chain_counts[chain] = chain_counts.get(chain, 0) + 1
            actions += 1
            new_buys_this_cycle += 1
        else:
            _skip_proposal(p, msg, price=price)

    return actions


# ── Main simulation ───────────────────────────────────────────────────────────
def _clean_expired_bans(max_age_days=7):
    """
    Remove ban list entries older than max_age_days.
    Called at the start of each simulation so the list doesn't grow forever.
    A token that rugged 2 weeks ago might have a completely new liquidity pool
    with the same ticker — the old ban shouldn't block it indefinitely.
    """
    removed = 0
    # Clean DB auto_ban table
    try:
        conn = sqlite3.connect(MAIN_DB, timeout=5)
        conn.execute("""CREATE TABLE IF NOT EXISTS auto_ban (
            key TEXT PRIMARY KEY, symbol TEXT, chain TEXT,
            reason TEXT, banned_at TEXT DEFAULT (datetime('now')))""")
        result = conn.execute(
            "DELETE FROM auto_ban WHERE banned_at < datetime('now', ?)",
            (f'-{max_age_days} days',))
        removed += result.rowcount
        conn.commit()
        conn.close()
    except Exception:
        pass
    # Clean sim_ban_list.json — keep only entries without a timestamp
    # (legacy entries have no date so we can't age them — keep them as permanent)
    # New entries written below include a date suffix for future expiry
    try:
        import json as _j
        from datetime import datetime as _dt, timedelta as _td
        bl_file = 'sim_ban_list.json'
        try:
            raw = _j.load(open(bl_file))
        except Exception:
            raw = []
        cutoff = _dt.now() - _td(days=max_age_days)
        kept = []
        for entry in raw:
            # Format: "SYM_chain" (legacy, keep) or "SYM_chain|2025-01-01" (dated)
            if '|' in entry:
                try:
                    date_str = entry.split('|')[1]
                    if _dt.fromisoformat(date_str) < cutoff:
                        removed += 1
                        continue
                except Exception:
                    pass
            kept.append(entry)
        _j.dump(kept, open(bl_file, 'w'))
    except Exception:
        pass
    if removed:
        print(f"  🧹 Expired {removed} old bans (>{max_age_days}d)")


def _write_persistent_ban(symbol, chain, reason=''):
    """Write ban to DB + json. Bans auto-expire after 7 days (via _clean_expired_bans)."""
    key = f"{symbol}_{chain}"
    today = datetime.now().strftime('%Y-%m-%d')
    # Write to DB
    try:
        conn = sqlite3.connect(MAIN_DB, timeout=5)
        conn.execute("""CREATE TABLE IF NOT EXISTS auto_ban (
            key TEXT PRIMARY KEY, symbol TEXT, chain TEXT,
            reason TEXT, banned_at TEXT DEFAULT (datetime('now')))""")
        conn.execute("INSERT OR IGNORE INTO auto_ban (key,symbol,chain,reason) VALUES (?,?,?,?)",
                     (key, symbol, chain, reason[:120]))
        conn.commit()
        conn.close()
    except Exception:
        pass
    # Write to json with date suffix so _clean_expired_bans can expire it
    try:
        import json as _j
        bl_file = 'sim_ban_list.json'
        try:
            bl = _j.load(open(bl_file))
        except Exception:
            bl = []
        dated_key = f"{key}|{today}"
        # Remove any existing undated version of this key
        bl = [e for e in bl if not e.startswith(key)]
        if dated_key not in bl:
            bl.append(dated_key)
            _j.dump(bl, open(bl_file, 'w'))
    except Exception:
        pass
    print(f"    🚫 Auto-banned {key} — expires in 7 days")


def run_simulation(hours=6, cycle_min=5, stop_loss=STOP_LOSS_PCT, take_profit=TAKE_PROFIT_PCT):
    init_sim_tables()

    # Gap 1: Crash recovery — check if there's an active sim to resume
    portfolio = None
    try:
        conn = sqlite3.connect(SIM_DB, timeout=5)
        row = conn.execute("""
            SELECT sim_id FROM sim_runs
            WHERE end_time > datetime('now', '-30 minutes')
            AND start_time > datetime('now', '-7 hours')
            ORDER BY start_time DESC LIMIT 1
        """).fetchone()
        conn.close()
        if row:
            candidate_id = row[0]
            # Check it has open positions worth restoring
            conn2 = sqlite3.connect(SIM_DB, timeout=5)
            open_count = conn2.execute(
                "SELECT COUNT(*) FROM sim_portfolio WHERE sim_id=? AND status='HOLDING'",
                (candidate_id,)).fetchone()[0]
            conn2.close()
            if open_count > 0:
                print(f"  ♻️  Found active sim {candidate_id} with {open_count} open positions — restoring")
                portfolio = SimPortfolio.restore(candidate_id)
    except Exception as _re:
        print(f"  ⚠️  Crash recovery check failed: {_re}")

    if portfolio is None:
        sim_id = f"SIM_{datetime.now().strftime('%Y%m%d_%H%M')}"
        portfolio = SimPortfolio(sim_id)

    # Gap 3: Dynamic cash sizing from live wallet balance (live mode only)
    try:
        if not _executor_dry_run() and portfolio.wallet_balances:
            for chain, bal in portfolio.wallet_balances.items():
                if chain in CHAINS and bal.get('usd', 0) > 5:
                    # Use 15% of wallet per chain for trading, capped at STARTING_BALANCE_USD
                    dynamic_cash = min(bal['usd'] * 0.15, STARTING_BALANCE_USD)
                    if dynamic_cash > 1:
                        portfolio.cash[chain] = dynamic_cash
                        print(f"  💼 {chain}: trading cash set to ${dynamic_cash:.1f} (15% of ${bal['usd']:.0f} wallet)")
    except Exception:
        pass  # dry run or wallet not available — use hardcoded defaults

    end_time = datetime.now(timezone.utc) + timedelta(hours=hours)
    print("  📡 Initializing token intelligence...")
    try:
        from token_intelligence import run_token_intelligence
        run_token_intelligence()
    except ImportError:
        print("  ⚠️  token_intelligence.py not found")
    except Exception as _ti_e:
        print(f"  token_intelligence error: {_ti_e}")
    total_cycles = int(hours * 60 / cycle_min)
    sim_id = portfolio.sim_id

    # Clean expired bans before starting — tokens that rugged >7 days ago
    # may have new pools; don't block them indefinitely
    _clean_expired_bans(max_age_days=7)

    # Start single-writer DB thread (must be first — everything else queues through it)
    _start_db_writer()
    time.sleep(0.5)  # let writer initialize

    # Start background price monitor
    monitor = run_price_monitor(portfolio, stop_loss, take_profit,
                                duration_minutes=int(hours*60)+5)

    # Start opportunity hunter (airdrops, presales, launchpads)
    try:
        from opportunity_hunter import start_hunter_thread
        start_hunter_thread(interval_minutes=60)
        print("  🔍 Opportunity hunter: active (airdrops + presales + launchpads)")
    except Exception as _oh_err:
        print(f"  ⚠️  Opportunity hunter: {_oh_err}")

    # Start PumpFun real-time stream (new token launches + graduations)
    try:
        from pumpfun_stream import start_pumpfun_stream
        start_pumpfun_stream()
    except ImportError:
        print("  ⚠️  pumpfun_stream.py not found — copy it to this folder")
    except Exception as _pf_err:
        print(f"  ⚠️  PumpFun stream: {_pf_err}")

    # Start KOL copy trader (dynamic wallet list — auto-refreshes from Kolscan/GMGN every 24h)
    try:
        from kol_tracker import start_kol_tracker
        start_kol_tracker()
    except ImportError:
        print("  ⚠️  kol_tracker.py not found — copy it to this folder")
    except Exception as _kol_err:
        print(f"  ⚠️  KOL tracker: {_kol_err}")

    # Start source discovery (finds new Telegram/Reddit/Twitter sources weekly)
    try:
        from source_discovery import start_discovery_thread
        start_discovery_thread(interval_hours=168)  # once per week
        print("  📡 Source discovery: active (weekly auto-expand)")
    except Exception as _sd_err:
        print(f"  ⚠️  Source discovery: {_sd_err}")

    # Notify executor of sim start
    try:
        from executor import alert_start
        alert_start(sim_id, hours, portfolio.starting_trading)
    except Exception:
        pass
    print(f"\n{'='*60}")
    print(f"  AlphaScope Trade Simulation v2.3")
    print(f"  Sim ID: {sim_id}")
    try:
        if not _executor_dry_run():
            print(f"  🟢 LIVE chains: SOL + BASE + ETH")
            print(f"  📄 Paper chains: BSC + ARB (no wallet configured)")
        else:
            print(f"  📄 DRY RUN — all chains paper")
    except Exception:
        pass
    print(f"  Real portfolio cost basis: ${portfolio.starting_real:,.2f}")
    print(f"  Real portfolio T=0 value:  ${portfolio._real_value():,.2f}")
    print(f"  Duration: {hours}h | Cycle: {cycle_min}min | "
          f"Stop: {stop_loss}% | TP: +{take_profit}%")
    print(f"  Price monitor: every 10s (fast rug detection)")
    print(f"  End: {end_time.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    # Always show real portfolio holdings from config (wallet_balances may not load)
    print("  Real portfolio holdings:")
    for chain, positions in REAL_PORTFOLIO.items():
        for pos in positions:
            p_now = resolve_price(pos['symbol'], pos['coin_id'], chain, use_cache=False)
            p_show = p_now if p_now else pos['entry_price']
            val = pos['amount'] * p_show
            pnl = (p_show - pos['entry_price']) * pos['amount']
            arrow = '📈' if pnl >= 0 else '📉'
            print(f"    {arrow} {pos['symbol']:<6} ({chain}) "
                  f"{pos['amount']} @ cost ${pos['entry_price']:.2f} → now ${p_show:.2f} "
                  f"= ${val:,.2f} ({pnl:+.2f})")
    print()

    # Print live on-chain wallet balances if available
    if portfolio.wallet_balances:
        print("  On-chain wallet balances (T=0):")
        for chain, b in portfolio.wallet_balances.items():
            print(f"    {chain}: {b['amount']:.4f} {b['symbol']} = ${b['usd']:.2f}")
        total_wallet = sum(b['usd'] for b in portfolio.wallet_balances.values())
        print(f"    Total on-chain: ${total_wallet:.2f}")
        print()

    cycle = 0
    while datetime.now(timezone.utc) < end_time:
        cycle += 1
        elapsed = cycle * cycle_min / 60
        print(f"\n  --- Cycle {cycle}/{total_cycles} | +{elapsed:.1f}h ---")

        # Gap 2: Monitor watchdog — restart if thread died
        if not monitor.is_alive():
            print("  ⚠️  Price monitor died — restarting")
            try:
                from executor import alert_error
                alert_error("Price monitor thread died — restarted automatically")
            except Exception:
                pass
            monitor = run_price_monitor(portfolio, stop_loss, take_profit,
                                        duration_minutes=int(hours * 60) + 5,
                                        interval_seconds=10)

        # Gap 4: Refresh token intelligence every 3 cycles (scores go stale in ~1h)
        if cycle % 3 == 0:
            try:
                from token_intelligence import run_token_intelligence
                run_token_intelligence()
                print("  📊 Token intelligence refreshed")
            except Exception as _tie:
                print(f"  token_intelligence refresh: {_tie}")

        # Clear price cache each cycle — forces fresh API fetch for all prices
        _price_cache.clear()

        # Issue F: Try direct module call first (faster, no subprocess overhead)
        try:
            import fetcher as _fetcher
            if hasattr(_fetcher, 'run_quick_fetch'):
                print("  Refreshing data (direct)...")
                _fetcher.run_quick_fetch()
            else:
                raise AttributeError("run_quick_fetch not found")
        except Exception:
            # Fallback to subprocess (original behaviour)
            try:
                import subprocess
                print("  Refreshing data...")
                r = subprocess.run(
                    ['python3', 'fetcher.py', '--quick'],
                    capture_output=True, text=True, timeout=180,
                    close_fds=True,
                    cwd=os.path.dirname(os.path.abspath(__file__)) or '.')
                if r.stdout:
                    for line in r.stdout.strip().split('\n'):
                        if line.strip():
                            print(f"  {line}")
                if r.returncode != 0 and r.stderr:
                    print(f"  Refresh warning: {r.stderr[:200]}")
            except Exception as e:
                print(f"  Refresh error: {e}")

        actions = run_agent_cycle(portfolio, stop_loss, take_profit)
        print(f"  Actions: {actions}")
        portfolio.print_status()
        try:
            portfolio.save()
        except Exception as _se:
            print(f"  WARN: save failed ({_se}) — continuing")

        if datetime.now(timezone.utc) < end_time:
            print(f"\n  Next cycle in {cycle_min} min... (Ctrl+C to stop)")
            try:
                time.sleep(cycle_min * 60)
            except KeyboardInterrupt:
                print("\n  Stopped by user — selling all open positions...")
                _sell_all_gems(portfolio)
                print(f"\n{'='*60}")
                print(f"  COMPLETE -- {sim_id}")
                portfolio.print_status()
                display_results(sim_id, portfolio)
                portfolio.save()
                return

    # ── Natural end of session ───────────────────────────────────────
    _sell_all_gems(portfolio)
    print(f"\n{'='*60}")
    print(f"  COMPLETE -- {sim_id}")
    portfolio.print_status()
    display_results(sim_id, portfolio)
    portfolio.save()


def _sell_all_gems(portfolio):
    """Sell all open positions at end of session or Ctrl+C.
    portfolio.sell() calls executor.on_sell() internally — no double execution."""
    open_pos = [(k, v) for k, v in portfolio.holdings.items()
                if not v.get('is_real')]
    if not open_pos:
        print("  No open positions to close.")
        return
    print(f"\n  🔚 Closing {len(open_pos)} open position(s)...")
    for key, pos in open_pos:
        sym      = pos['symbol']
        chain    = pos['chain']
        contract = pos.get('coin_id', '') or pos.get('contract', '')
        price    = resolve_price(sym, coin_id=contract, chain=chain,
                                  dex_url=pos.get('dex_url', ''))
        if price <= 0:
            price = pos.get('_last_price', pos.get('buy_price', 0))
        pnl_pct  = ((price - pos['buy_price']) / pos['buy_price'] * 100
                    if pos.get('buy_price') else 0)
        # portfolio.sell() calls executor.on_sell() internally — one execution only
        ok, msg  = portfolio.sell(sym, chain, price, reason='end_of_session')
        arrow    = '📈' if pnl_pct >= 0 else '📉'
        print(f"    {arrow} {'OK' if ok else 'FAIL'} {sym} ({chain}) {pnl_pct:+.1f}% | {msg}")


# ── Results display ───────────────────────────────────────────────────────────
def _display_from_memory(portfolio):
    """Display final results from in-memory portfolio — DB-independent."""
    print(f"\n{'='*65}")
    print(f"  RESULTS: {portfolio.sim_id}")
    print(f"{'='*65}")
    print(f"  {'Symbol':<10} {'Chain':<8} {'Buy':>12} {'Now':>12} {'P&L':>10} {'%':>8} Status")
    print(f"  {'-'*63}")

    sells = [t for t in portfolio.trades if t['action'] == 'SELL']
    buys  = {f"{t['symbol']}_{t['chain']}": t for t in portfolio.trades if t['action'] == 'BUY'}
    total_in = total_now = 0

    # Open positions
    for key, pos in sorted(portfolio.holdings.items(), key=lambda x: -(x[1].get('buy_price',0))):
        if pos.get('is_real'):
            continue
        sym, chain = pos['symbol'], pos['chain']
        buy_p = pos.get('buy_price', 0)
        now_p = resolve_price(sym, coin_id=pos.get('coin_id', ''),
                              chain=chain, dex_url=pos.get('dex_url', '')) or buy_p
        val_now = pos['amount'] * now_p
        val_in  = pos.get('usd_spent', 0)
        pnl = val_now - val_in
        pct = pnl / val_in * 100 if val_in else 0
        d = 'UP' if pnl >= 0 else 'DN'
        total_in += val_in
        total_now += val_now
        print(f"  {d} {sym:<10} {chain:<8} {buy_p:>12.8g} {now_p:>12.8g} {pnl:>10.2f} {pct:>7.1f}% HOLDING")

    # Closed positions
    for t in sorted(sells, key=lambda x: x.get('pnl_pct', 0)):
        sym, chain = t['symbol'], t['chain']
        buy_p = t.get('buy_price', 0)
        sell_p = t.get('price', 0)
        pnl = t.get('pnl', 0)
        pct = t.get('pnl_pct', 0)
        usd = t.get('usd', 0)
        d = 'UP' if pnl >= 0 else 'DN'
        total_in += usd
        total_now += usd + pnl
        reason = t.get('reason', 'SOLD')
        print(f"  {d} {sym:<10} {chain:<8} {buy_p:>12.8g} {sell_p:>12.8g} {pnl:>10.2f} {pct:>7.1f}% {reason.upper()}")

    print(f"  {'-'*63}")
    wins   = sum(1 for t in sells if t.get('pnl',0) > 0)
    losses = sum(1 for t in sells if t.get('pnl',0) <= 0)
    total_pnl = total_now - total_in
    pct_total = total_pnl / total_in * 100 if total_in else 0
    d = 'UP' if total_pnl >= 0 else 'DN'
    print(f"  {d} Trading: ${total_in:.2f} -> ${total_now:.2f} = ${total_pnl:+.2f} ({pct_total:+.1f}%)")
    print(f"  Win rate: {wins}W / {losses}L = {wins/max(wins+losses,1)*100:.0f}%")

    t0 = getattr(portfolio, 't0_prices', {})
    print(f"\n  Real Portfolio (vs T=0):")
    real_total = real_pnl = 0
    for chain, plist in REAL_PORTFOLIO.items():
        for pos in plist:
            p_now = resolve_price(pos['symbol'], pos['coin_id'], chain) or pos['entry_price']
            p_ref = t0.get(pos['symbol'], pos['entry_price'])
            val = pos['amount'] * p_now
            pnl_r = (p_now - p_ref) * pos['amount']
            real_total += val
            real_pnl += pnl_r
            d = 'UP' if pnl_r >= 0 else 'DN'
            print(f"    {d} {pos['symbol']:<6} ${p_ref:.2f}->${p_now:.2f} x{pos['amount']} = ${val:,.2f} ({pnl_r:+.2f})")
    print(f"  Real portfolio total: ${real_total:,.2f} (session pnl: ${real_pnl:+.2f})")
    print(f"{'='*65}\n")


def display_results(sim_id=None, portfolio=None):
    # Prefer in-memory portfolio over DB (DB may be stale if writes failed)
    if portfolio and portfolio.trades:
        _display_from_memory(portfolio)
        return

    # Use a fresh read-only connection — never the shared writer connection
    try:
        conn = sqlite3.connect(SIM_DB, timeout=10)
    except Exception:
        print("  Cannot read sim DB")
        return
    if not sim_id:
        row = conn.execute(
            "SELECT sim_id FROM sim_runs ORDER BY start_time DESC LIMIT 1").fetchone()
        if not row:
            print("No simulations found")
            conn.close()
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

    print(f"\n  Real Portfolio (vs session start):")
    real_total = real_pnl = 0
    BINANCE_IDS = {'LINK':'LINKUSDT','ETH':'ETHUSDT','SOL':'SOLUSDT',
                   'BTC':'BTCUSDT','AAVE':'AAVEUSDT','UNI':'UNIUSDT'}
    t0 = getattr(portfolio, 't0_prices', {}) if portfolio else {}
    for chain, plist in REAL_PORTFOLIO.items():
        for pos in plist:
            sym = pos['symbol']
            # Force fresh price — bypass cache
            p_now = 0
            try:
                if sym in BINANCE_IDS:
                    r = requests.get(
                        f'https://api.binance.com/api/v3/ticker/price?symbol={BINANCE_IDS[sym]}',
                        timeout=5)
                    if r.status_code == 200:
                        p_now = float(r.json().get('price', 0) or 0)
            except Exception:
                pass
            if not p_now:
                p_now = resolve_price(sym, pos['coin_id'], chain, use_cache=False)
            p_now = p_now or pos['entry_price']
            p_ref = t0.get(sym, pos['entry_price'])
            val = pos['amount'] * p_now
            pnl_r = (p_now - p_ref) * pos['amount']
            real_total += val
            real_pnl += pnl_r
            d = 'UP' if pnl_r >= 0 else 'DN'
            print(f"    {d} {sym:<6} ${p_ref:.2f}->${p_now:.2f} "
                  f"x{pos['amount']} = ${val:,.2f} ({pnl_r:+.2f})")
    print(f"  Real portfolio total: ${real_total:,.2f} (session pnl: ${real_pnl:+.2f})")
    print(f"{'='*65}\n")


def run_test():
    run_simulation(hours=3/60, cycle_min=1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AlphaScope Simulator v2.1')
    parser.add_argument('--hours',       type=float, default=6)
    parser.add_argument('--cycle',       type=int,   default=5)
    parser.add_argument('--stop-loss',   type=float, default=STOP_LOSS_PCT)
    parser.add_argument('--take-profit', type=float, default=TAKE_PROFIT_PCT)
    parser.add_argument('--test',        action='store_true')
    parser.add_argument('--results',     action='store_true')
    args = parser.parse_args()

    if args.test:
        run_test()
    elif args.results:
        display_results()
    else:
        run_simulation(args.hours, args.cycle, args.stop_loss, args.take_profit)

