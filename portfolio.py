"""
AlphaScope — Portfolio Manager v1.0
Tracks your holdings across chains.
Buy/sell/hold signals based on: price momentum + sentiment + buzz + macro.
"""

import sqlite3
import requests
from datetime import datetime


# Estimated gas costs per swap by chain (USD)
# Static fallback gas costs (USD) -- used when live fetch fails
_GAS_FALLBACK = {
    'ethereum':   3.00,  # ETH mainnet -- dynamic, fetched live
    'arbitrum':   0.25,  # ARB L2
    'base':       0.10,  # Base L2
    'optimism':   0.15,  # OP L2
    'polygon':    0.05,  # MATIC
    'bsc':        0.20,  # BNB Chain
    'solana':     0.001, # SOL -- near zero
    'avalanche':  0.50,  # AVAX
    'sui':        0.01,  # SUI
    'bitcoin':    2.00,  # BTC on-chain
    'default':    1.00,  # Unknown chain
}
_eth_gas_cache = {'cost': None, 'ts': 0}  # cache live gas 5 min

def get_eth_gas_usd():
    """Fetch live ETH gas cost via Blocknative (free, no auth)."""
    import time, requests as _req
    now = time.time()
    if _eth_gas_cache['cost'] and now - _eth_gas_cache['ts'] < 300:
        return _eth_gas_cache['cost']
    try:
        res = _req.get('https://api.blocknative.com/gasprices/blockprices',
                       timeout=5)
        if res.status_code == 200:
            bp = res.json().get('blockPrices', [{}])[0]
            prices = bp.get('estimatedPrices', [{}])
            # Use 90% confidence price
            gwei = float(next((p['price'] for p in prices
                               if p.get('confidence', 0) >= 90),
                              prices[0].get('price', 20) if prices else 20))
            # Get ETH price
            ep = _req.get(
                'https://api.coingecko.com/api/v3/simple/price'
                '?ids=ethereum&vs_currencies=usd', timeout=4)
            eth_usd = ep.json().get('ethereum', {}).get('usd', 2300)
            gas_usd = round((gwei * 1e-9) * 150_000 * eth_usd, 3)
            _eth_gas_cache['cost'] = gas_usd
            _eth_gas_cache['ts'] = now
            return gas_usd
    except Exception:
        pass
    return _GAS_FALLBACK['ethereum']

def GAS_COST_USD_for(chain):
    """Get gas cost for chain -- live for ETH, static for others."""
    if chain == 'ethereum':
        return get_eth_gas_usd()
    return _GAS_FALLBACK.get(chain, _GAS_FALLBACK['default'])

# Keep GAS_COST_USD dict for backward compat
GAS_COST_USD = _GAS_FALLBACK
DEX_FEE_PCT = 0.003
MIN_TRADE_USD = 50


def init_portfolio_table():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS portfolio (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coin_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        name TEXT,
        chain TEXT DEFAULT 'ethereum',
        wallet_address TEXT DEFAULT '',
        amount REAL DEFAULT 0,
        entry_price_usd REAL DEFAULT 0,
        entry_date TEXT,
        notes TEXT DEFAULT '',
        status TEXT DEFAULT 'HOLDING',
        updated_at TEXT,
        UNIQUE(coin_id, chain, wallet_address))''')
    c.execute('''CREATE TABLE IF NOT EXISTS portfolio_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coin_id TEXT,
        symbol TEXT,
        signal TEXT,
        confidence INTEGER,
        reasons TEXT,
        price_usd REAL,
        change_24h REAL,
        change_7d REAL,
        score INTEGER,
        generated_at TEXT)''')
    conn.commit()
    conn.close()


def add_position(coin_id, symbol, amount, entry_price,
                 chain='ethereum', wallet='', name='', notes=''):
    init_portfolio_table()
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    now = datetime.now().isoformat()
    try:
        c.execute('''INSERT OR REPLACE INTO portfolio
            (coin_id, symbol, name, chain, wallet_address, amount,
             entry_price_usd, entry_date, notes, status, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (coin_id, symbol.upper(), name or symbol, chain, wallet,
             amount, entry_price, now, notes, 'HOLDING', now))
        conn.commit()
        print(f"  Added {amount} {symbol.upper()} @ ${entry_price}")
    except Exception as e:
        print(f"  Failed: {e}")
    finally:
        conn.close()


def remove_position(coin_id, chain='ethereum', wallet=''):
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute("DELETE FROM portfolio WHERE coin_id=? AND chain=? AND wallet_address=?",
              (coin_id, chain, wallet))
    conn.commit()
    conn.close()


def load_portfolio():
    init_portfolio_table()
    conn = sqlite3.connect('alphascope.db', timeout=30)
    import pandas as pd
    df = pd.read_sql_query(
        "SELECT * FROM portfolio WHERE status != 'CLOSED' ORDER BY entry_date DESC",
        conn)
    conn.close()
    return df


def get_current_prices(coin_ids):
    """Fetch current prices from CoinGecko for portfolio coins."""
    if not coin_ids:
        return {}
    try:
        ids_str = ','.join(coin_ids)
        res = requests.get(
            'https://api.coingecko.com/api/v3/simple/price',
            params={
                'ids': ids_str,
                'vs_currencies': 'usd',
                'include_24hr_change': 'true',
                'include_7d_change': 'true',
                'include_market_cap': 'true',
                'include_24hr_vol': 'true',
            },
            timeout=15,
        )
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"  Price fetch failed: {e}")
    return {}


def generate_signal(coin_id, symbol, price_data, buzz_data, sentiment_data,
                    fear_greed, macro_data, chain='ethereum', position_value_usd=0):
    """
    Generate BUY / HOLD / SELL / WATCH signal for a coin.
    Returns dict with signal, confidence (0-100), reasons list, score.
    """
    score = 0
    reasons = []

    # ── Security check — overrides everything if hacked ─────────────────────
    try:
        from security_monitor import get_security_flags
        sec = get_security_flags(coin_id=coin_id, protocol_name=symbol)
        if sec.get('hacked'):
            days = sec.get('days_ago', 0)
            amt  = sec.get('amount_usd', 0)
            sev  = sec.get('severity', 'HIGH')
            resolved = sec.get('resolved', False)
            amt_str = f'${amt/1e6:.1f}M' if amt >= 1e6 else f'${amt/1e3:.0f}K'
            if not resolved and days <= 30 and sec.get('amount_usd', 0) >= 100_000:
                # Active hack with confirmed losses — force SELL
                return {
                    'signal': 'SELL',
                    'confidence': 95,
                    'reasons': [f'HACKED {days}d ago ({amt_str})', f'Severity: {sev}', 'Active exploit — exit position'],
                    'score': -10,
                    'price_usd': price_data.get('usd', 0),
                    'change_24h': price_data.get('usd_24h_change', 0) or 0,
                    'change_7d':  price_data.get('usd_7d_change', 0) or 0,
                }
            elif resolved and days <= 90:
                score -= 3
                reasons.append(f'Prior hack {days}d ago ({amt_str}) — resolved')
    except ImportError:
        pass

    price = price_data.get('usd', 0)
    change_24h = price_data.get('usd_24h_change', 0) or 0
    change_7d  = price_data.get('usd_7d_change', 0) or 0
    mcap       = price_data.get('usd_market_cap', 0) or 0
    vol        = price_data.get('usd_24h_vol', 0) or 0

    # ── Price momentum ────────────────────────────────────────────────────────
    if change_24h > 10:
        score += 3; reasons.append(f"+{change_24h:.1f}% today")
    elif change_24h > 3:
        score += 1; reasons.append(f"+{change_24h:.1f}% today")
    elif change_24h < -15:
        score -= 3; reasons.append(f"{change_24h:.1f}% today (dump)")
    elif change_24h < -5:
        score -= 1; reasons.append(f"{change_24h:.1f}% today")

    if change_7d > 20:
        score += 2; reasons.append(f"+{change_7d:.1f}% this week")
    elif change_7d > 5:
        score += 1
    elif change_7d < -20:
        score -= 2; reasons.append(f"{change_7d:.1f}% this week")

    # ── Volume / market cap ratio (momentum indicator) ────────────────────────
    if mcap > 0 and vol / mcap > 0.3:
        score += 2; reasons.append("high vol/mcap ratio")
    elif mcap > 0 and vol / mcap > 0.1:
        score += 1

    # ── Social buzz ───────────────────────────────────────────────────────────
    sym_upper = symbol.upper()
    buzz = buzz_data.get(sym_upper, {})
    mentions  = buzz.get('mentions', 0)
    sentiment = buzz.get('sentiment', 0)

    if mentions >= 10:
        score += 3; reasons.append(f"{mentions} social mentions")
    elif mentions >= 5:
        score += 2; reasons.append(f"{mentions} social mentions")
    elif mentions >= 2:
        score += 1

    if sentiment > 0.3:
        score += 2; reasons.append("bullish sentiment")
    elif sentiment > 0.1:
        score += 1
    elif sentiment < -0.3:
        score -= 2; reasons.append("bearish sentiment")
    elif sentiment < -0.1:
        score -= 1

    # ── X/Twitter sentiment ───────────────────────────────────────────────────
    x_sent = sentiment_data.get(sym_upper, 0)
    if x_sent > 0.2:
        score += 1; reasons.append("positive X sentiment")
    elif x_sent < -0.2:
        score -= 1; reasons.append("negative X sentiment")

    # ── Market environment ────────────────────────────────────────────────────
    fg = fear_greed or 50
    if fg >= 75:
        score += 1; reasons.append(f"greed market (F&G:{fg})")
    elif fg <= 25:
        score -= 1; reasons.append(f"fear market (F&G:{fg})")

    # Macro: VIX high = risk off
    vix = macro_data.get('VIX', 0)
    if vix > 30:
        score -= 2; reasons.append(f"VIX {vix:.0f} — high volatility")
    elif vix > 20:
        score -= 1

    # 10Y-2Y spread (inverted = recession warning)
    spread = macro_data.get('10Y-2Y', 0)
    if spread < 0:
        score -= 1; reasons.append("inverted yield curve")

    # ── Gas cost reality check ───────────────────────────────────────────────
    gas_usd = GAS_COST_USD_for(chain.lower())
    dex_fee_usd = position_value_usd * DEX_FEE_PCT if position_value_usd > 0 else 0
    total_cost_usd = gas_usd + dex_fee_usd
    if position_value_usd > 0:
        cost_pct = total_cost_usd / position_value_usd * 100
        if cost_pct > 5:
            score -= 3
            reasons.append(f'gas+fee ~{cost_pct:.0f}% of trade (${total_cost_usd:.2f})')
        elif cost_pct > 2:
            score -= 1
            reasons.append(f'gas+fee ~{cost_pct:.0f}% of trade')
        elif cost_pct < 0.5:
            reasons.append(f'low gas ({chain})')

    # ── Signal mapping ────────────────────────────────────────────────────────
    if score >= 6:
        signal = 'BUY'
        confidence = min(95, 60 + score * 5)
    elif score >= 3:
        signal = 'ACCUMULATE'
        confidence = min(80, 50 + score * 5)
    elif score >= 1:
        signal = 'HOLD'
        confidence = 55
    elif score >= -2:
        signal = 'WATCH'
        confidence = 50
    elif score >= -4:
        signal = 'REDUCE'
        confidence = min(80, 50 + abs(score) * 5)
    else:
        signal = 'SELL'
        confidence = min(90, 55 + abs(score) * 4)

    return {
        'signal': signal,
        'confidence': confidence,
        'reasons': reasons[:4],
        'score': score,
        'price_usd': price,
        'change_24h': change_24h,
        'change_7d': change_7d,
    }


def run_portfolio_signals():
    """Generate signals for all portfolio holdings and store them."""
    init_portfolio_table()
    df = load_portfolio()
    if df.empty:
        return []

    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()

    # Load context data
    c.execute("SELECT value FROM fear_greed ORDER BY timestamp DESC LIMIT 1")
    fg_row = c.fetchone()
    fear_greed = int(fg_row[0]) if fg_row else 50

    c.execute("""SELECT coin, mention_count, avg_sentiment FROM coin_buzz
                 ORDER BY fetched_at DESC, mention_count DESC""")
    buzz_raw = c.fetchall()
    buzz_data = {row[0].upper(): {'mentions': row[1], 'sentiment': row[2] or 0}
                 for row in buzz_raw}

    c.execute("""SELECT coin, sentiment_score FROM signals
                 WHERE signal_type='SENTIMENT' ORDER BY fetched_at DESC""")
    sent_raw = c.fetchall()
    sentiment_data = {row[0].upper(): row[1] or 0 for row in sent_raw}

    c.execute("""SELECT indicator, value FROM macro_indicators
                 ORDER BY fetched_at DESC""")
    macro_raw = c.fetchall()
    macro_map = {}
    for ind, val in macro_raw:
        if ind not in macro_map:
            macro_map[ind] = val

    conn.close()

    # Fetch current prices — try DB first (set by fetch_buzzing_prices), then direct API
    coin_ids = df['coin_id'].tolist()
    # Try DB first
    db_prices = {}
    try:
        pc = sqlite3.connect('alphascope.db', timeout=30)
        pcur = pc.cursor()
        pcur.execute("""
            SELECT coin_id, price_usd, change_24h, change_7d, market_cap, volume_24h
            FROM token_data
            WHERE fetched_at >= datetime('now', '-30 minutes')
            GROUP BY coin_id ORDER BY fetched_at DESC
        """)
        for row in pcur.fetchall():
            cid = row[0].lower()
            db_prices[cid] = {
                'usd': row[1], 'usd_24h_change': row[2],
                'usd_7d_change': row[3], 'usd_market_cap': row[4],
                'usd_24h_vol': row[5],
            }
        pc.close()
    except Exception:
        pass
    # For any coin not in DB, fetch directly from CoinGecko
    missing = [cid for cid in coin_ids if cid.lower() not in db_prices]
    api_prices = get_current_prices(missing) if missing else {}
    prices = {**{k.lower(): v for k, v in api_prices.items()}, **db_prices}

    # Generate signals
    results = []
    now = datetime.now().isoformat()
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()

    for _, row in df.iterrows():
        coin_id = row['coin_id']
        symbol  = row['symbol']
        price_data = prices.get(coin_id.lower(), prices.get(coin_id, {}))
        if not price_data:
            continue

        chain = row.get('chain', 'ethereum') or 'ethereum'
        current_px = float(price_data.get('usd', 0) or 0)
        position_value = current_px * (row.get('amount', 0) or 0)
        sig = generate_signal(
            coin_id, symbol, price_data,
            buzz_data, sentiment_data, fear_greed, macro_map,
            chain=chain, position_value_usd=position_value,
        )

        entry_price = row.get('entry_price_usd', 0) or 0
        current_price = sig['price_usd']
        amount = row.get('amount', 0) or 0
        pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
        pnl_usd = (current_price - entry_price) * amount if entry_price > 0 else 0

        result = {
            'coin_id':      coin_id,
            'symbol':       symbol,
            'name':         row.get('name', symbol),
            'chain':        row.get('chain', ''),
            'amount':       amount,
            'entry_price':  entry_price,
            'current_price': current_price,
            'pnl_pct':      round(pnl_pct, 2),
            'pnl_usd':      round(pnl_usd, 2),
            'value_usd':    round(current_price * amount, 2),
            'signal':       sig['signal'],
            'confidence':   sig['confidence'],
            'reasons':      ', '.join(sig['reasons']),
            'score':        sig['score'],
            'change_24h':   sig['change_24h'],
            'change_7d':    sig['change_7d'],
        }
        results.append(result)

        # Store signal
        c.execute('''INSERT INTO portfolio_signals
            (coin_id, symbol, signal, confidence, reasons,
             price_usd, change_24h, change_7d, score, generated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (coin_id, symbol, sig['signal'], sig['confidence'],
             result['reasons'], current_price,
             sig['change_24h'], sig['change_7d'], sig['score'], now))

    conn.commit()
    conn.close()
    return results


if __name__ == '__main__':
    print("AlphaScope — Portfolio Manager v1.0")
    print("=" * 50)
    print("\nExample: add a position")
    print("  from portfolio import add_position")
    print("  add_position('bitcoin', 'BTC', 0.05, 70000)")
    print("  add_position('ethereum', 'ETH', 1.5, 2200)")
    print("  add_position('solana', 'SOL', 20, 80)")
    print("\nThen run fetcher.py to generate signals.")
