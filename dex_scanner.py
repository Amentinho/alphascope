"""
AlphaScope — DEX Scanner v1.0
Finds coins in their first hours/days of trading on DEXes.
These are the true hidden gems — not yet on CoinGecko top 500,
trading on Uniswap/Raydium/PancakeSwap/BaseSwap etc.

Cross-signal scoring:
  +3  Mentioned in social buzz (Reddit/Telegram)
  +3  Appears in pre_launch_gems (ICO/IDO source)
  +2  DEX liquidity > $50k (real project, not rug)
  +2  Volume/liquidity ratio > 0.5 (trading activity)
  +2  Age < 48h (very early)
  +1  Age 48h–7d (still early)
  +2  Multi-chain presence
  -3  Liquidity < $10k (likely rug)
  Max score: 15
"""

import requests
import sqlite3
import time
from datetime import datetime, timezone

# Minimum thresholds to avoid obvious rugs
MIN_LIQUIDITY_USD = 15_000     # at least $15k liquidity
MIN_VOLUME_24H    = 5_000      # at least $5k daily volume
MAX_AGE_DAYS      = 14         # only coins < 14 days old
MIN_TXNS_24H      = 30         # at least 30 transactions (lowered for pump.fun)
MIN_TXNS_PUMPFUN  = 10         # pump.fun tokens can have fewer early on

# Chains we care about
TARGET_CHAINS = {
    'ethereum', 'solana', 'bsc', 'base', 'arbitrum',
    'polygon', 'avalanche', 'sui', 'ton',
}


def init_dex_table():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS dex_gems (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        symbol TEXT,
        contract_address TEXT,
        chain TEXT,
        dex TEXT,
        price_usd REAL,
        liquidity_usd REAL,
        volume_24h REAL,
        price_change_24h REAL,
        txns_24h INTEGER,
        age_hours REAL,
        social_buzz INTEGER,
        pre_launch_match INTEGER,
        cross_score INTEGER,
        dex_url TEXT,
        fetched_at TEXT,
        UNIQUE(contract_address, chain))''')
    conn.commit()
    conn.close()


def fetch_new_dex_pairs():
    """Pull recently added pairs from DexScreener across all chains."""
    print("    DexScreener new pairs...")
    pairs = []

    # Endpoint: latest tokens — returns pairs sorted by creation time
    try:
        res = requests.get(
            'https://api.dexscreener.com/token-profiles/latest/v1',
            headers={'Accept': 'application/json'},
            timeout=15,
        )
        if res.status_code == 200:
            data = res.json()
            tokens = data if isinstance(data, list) else data.get('data', data.get('pairs', []))
            for t in tokens[:50]:
                addr = t.get('tokenAddress', t.get('address', ''))
                chain = t.get('chainId', '').lower()
                if addr and chain in TARGET_CHAINS:
                    pairs.append({'address': addr, 'chain': chain})
    except Exception as e:
        print(f"      Latest endpoint failed: {e}")

    # Pump.fun — catches SOL tokens in first 5 minutes
    pf_pairs = fetch_pumpfun_new()
    pairs.extend(pf_pairs)
    time.sleep(1)

    # Birdeye — better Solana data quality
    be_pairs = fetch_birdeye_trending()
    # Only add Birdeye tokens not already found by DexScreener
    existing_addrs = {p['address'] for p in pairs}
    pairs.extend([p for p in be_pairs if p['address'] not in existing_addrs])
    time.sleep(1)

    # Fallback: search for newly created pairs with high momentum keywords
    if not pairs:
        try:
            for query in ['new gem', 'stealth launch', 'fair launch']:
                res = requests.get(
                    f'https://api.dexscreener.com/latest/dex/search?q={query}',
                    headers={'Accept': 'application/json'},
                    timeout=12,
                )
                if res.status_code == 200:
                    for p in res.json().get('pairs', [])[:20]:
                        addr = p.get('baseToken', {}).get('address', '')
                        chain = p.get('chainId', '').lower()
                        if addr and chain in TARGET_CHAINS:
                            pairs.append({'address': addr, 'chain': chain,
                                          'pair_data': p})
                time.sleep(0.5)
        except Exception as e:
            print(f"      Search fallback failed: {e}")

    print(f"      Found {len(pairs)} candidate addresses")
    return pairs


def enrich_pairs(pairs):
    """Fetch full pair data for addresses, filter by quality thresholds."""
    print("    Enriching pair data...")
    enriched = []
    now_ts = datetime.now(timezone.utc).timestamp()

    # Batch addresses by chain for efficiency
    by_chain = {}
    for p in pairs:
        if 'pair_data' in p:
            # Already have data from search
            process_pair(p['pair_data'], now_ts, enriched)
            continue
        chain = p['chain']
        by_chain.setdefault(chain, []).append(p['address'])

    for chain, addresses in by_chain.items():
        # DexScreener allows up to 30 addresses per call
        for i in range(0, len(addresses), 30):
            batch = addresses[i:i+30]
            addrs_str = ','.join(batch)
            try:
                res = requests.get(
                    f'https://api.dexscreener.com/latest/dex/tokens/{addrs_str}',
                    headers={'Accept': 'application/json'},
                    timeout=12,
                )
                if res.status_code != 200:
                    continue
                for pair in res.json().get('pairs', []):
                    process_pair(pair, now_ts, enriched)
                time.sleep(0.3)
            except Exception as e:
                print(f"      Enrich batch failed ({chain}): {e}")

    print(f"      {len(enriched)} pairs passed quality filters")
    return enriched


def process_pair(pair, now_ts, enriched):
    """Apply quality filters and extract structured data from a DexScreener pair."""
    try:
        chain = pair.get('chainId', '').lower()
        if chain not in TARGET_CHAINS:
            return

        liq = float(pair.get('liquidity', {}).get('usd', 0) or 0)
        vol = float(pair.get('volume', {}).get('h24', 0) or 0)
        txns = int(pair.get('txns', {}).get('h24', {}).get('buys', 0) or 0) + \
               int(pair.get('txns', {}).get('h24', {}).get('sells', 0) or 0)

        if liq < MIN_LIQUIDITY_USD:
            return
        if vol < MIN_VOLUME_24H:
            return
        if txns < MIN_TXNS_24H:
            return

        # Age check
        pair_created = pair.get('pairCreatedAt')
        age_hours = 999
        if pair_created:
            created_ts = int(pair_created) / 1000 if pair_created > 1e10 else int(pair_created)
            age_hours = (now_ts - created_ts) / 3600

        if age_hours > MAX_AGE_DAYS * 24:
            return

        base = pair.get('baseToken', {})
        name   = base.get('name', '')
        symbol = base.get('symbol', '').upper()
        addr   = base.get('address', '')

        if not name or not addr:
            return

        # Skip stablecoins, wrapped tokens, and non-ASCII symbols
        if not symbol.isascii() or not name.isascii():
            return
        if any(x in symbol for x in ['USD', 'BTC', 'ETH', 'WETH', 'WBTC', 'DAI']):
            return

        price_usd = float(pair.get('priceUsd', 0) or 0)
        price_change_24h = float(
            pair.get('priceChange', {}).get('h24', 0) or 0
        )
        dex = pair.get('dexId', '')
        url = pair.get('url', f'https://dexscreener.com/{chain}/{addr}')

        enriched.append({
            'name': name,
            'symbol': symbol,
            'address': addr,
            'chain': chain,
            'dex': dex,
            'price_usd': price_usd,
            'liquidity_usd': liq,
            'volume_24h': vol,
            'price_change_24h': price_change_24h,
            'txns_24h': txns,
            'age_hours': round(age_hours, 1),
            'url': url,
        })
    except Exception:
        pass


def cross_score(pair, buzz_coins, pre_launch_names):
    """
    Score a DEX pair based on cross-source signals.
    buzz_coins: set of uppercase tickers from coin_buzz
    pre_launch_names: set of lowercase project names from pre_launch_gems
    """
    score = 0
    symbol = pair['symbol'].upper()
    name_lower = pair['name'].lower()
    liq = pair['liquidity_usd']
    vol = pair['volume_24h']
    age = pair['age_hours']

    # Social buzz match
    if symbol in buzz_coins or any(symbol in b for b in buzz_coins):
        score += 3
    # Pre-launch source match (ICO/IDO listing + DEX = strong signal)
    if name_lower in pre_launch_names or symbol.lower() in pre_launch_names:
        score += 3
    # Liquidity quality
    if liq >= 100_000:
        score += 2
    elif liq >= 50_000:
        score += 1
    elif liq < 10_000:
        score -= 3
    # Volume/liquidity ratio (trading activity vs pool size)
    if liq > 0 and vol / liq > 1.0:
        score += 2
    elif liq > 0 and vol / liq > 0.3:
        score += 1
    # Age bonus (earlier = more upside)
    if age <= 24:
        score += 2
    elif age <= 48:
        score += 1
    # Price momentum
    if pair['price_change_24h'] > 50:
        score += 1

    return max(score, 0)




def fetch_pumpfun_new():
    """
    Early SOL token scanner — replaces pump.fun API (blocked April 2026).
    Uses DexScreener filtered to Solana pairs under 1 hour old.
    Catches new pump.fun tokens 5-10 min after they hit DexScreener.
    """
    print("    Early SOL pairs (< 1h)...")
    pairs = []
    try:
        import time as _time
        res = requests.get(
            'https://api.dexscreener.com/latest/dex/search?q=solana+new',
            headers={'Accept': 'application/json'},
            timeout=10,
        )
        if res.status_code == 200:
            raw = res.json().get('pairs', [])
            now_ts = _time.time()
            seen = set()
            for p in raw:
                if p.get('chainId') != 'solana':
                    continue
                created = p.get('pairCreatedAt', 0)
                if created > 1e12:
                    created = created / 1000
                age_h = (now_ts - created) / 3600 if created else 999
                if age_h > 1:
                    continue
                liq = float(p.get('liquidity', {}).get('usd', 0) or 0)
                if liq < 8_000:
                    continue
                addr = p.get('baseToken', {}).get('address', '')
                if addr in seen:
                    continue
                seen.add(addr)
                sym = p.get('baseToken', {}).get('symbol', '')
                name = p.get('baseToken', {}).get('name', '')
                if not sym or not sym.isascii() or not name.isascii():
                    continue
                pairs.append({'address': addr, 'chain': 'solana', 'pair_data': p})
            print(f"      Found {len(pairs)} early SOL pairs")
        else:
            print(f"      HTTP {res.status_code}")
    except Exception as e:
        print(f"      Failed: {e}")
    return pairs


def fetch_birdeye_trending():
    """
    Fetch trending tokens using GeckoTerminal API (free, no auth).
    Covers Solana, Ethereum, BSC, Base new pools with real liquidity data.
    """
    print("    GeckoTerminal trending new pools...")
    pairs = []
    networks = [
        ('solana', 'solana'),
        ('eth', 'ethereum'),
        ('bsc', 'bsc'),
        ('base', 'base'),
    ]
    try:
        for network, chain in networks:
            res = requests.get(
                f'https://api.geckoterminal.com/api/v2/networks/{network}/new_pools?page=1',
                headers={'Accept': 'application/json;version=20230302'},
                timeout=8,
            )
            if res.status_code != 200:
                continue
            pools = res.json().get('data', [])
            for pool in pools[:8]:
                attrs = pool.get('attributes', {})
                liq = float(attrs.get('reserve_in_usd', 0) or 0)
                if liq < MIN_LIQUIDITY_USD:
                    continue
                base_token = attrs.get('name', '').split(' / ')[0]
                symbol = base_token.upper()[:10]
                if not symbol.isascii():
                    continue
                # Build pair_data compatible with process_pair()
                created = attrs.get('pool_created_at', '')
                pairs.append({
                    'address': pool.get('id', '').split('_')[-1],
                    'chain': chain,
                    'pair_data': {
                        'chainId': chain,
                        'dexId': attrs.get('dex_id', 'unknown'),
                        'pairAddress': pool.get('id', '').split('_')[-1],
                        'baseToken': {'address': '', 'name': base_token, 'symbol': symbol},
                        'priceUsd': str(attrs.get('base_token_price_usd', 0) or 0),
                        'liquidity': {'usd': liq},
                        'volume': {'h24': float(attrs.get('volume_usd', {}).get('h24', 0) or 0)},
                        'txns': {'h24': {'buys': int(attrs.get('transactions', {}).get('h24', {}).get('buys', 0) or 0), 'sells': 0}},
                        'pairCreatedAt': 0,
                        'priceChange': {'h24': float(attrs.get('price_change_percentage', {}).get('h24', 0) or 0)},
                        'url': f"https://www.geckoterminal.com/{network}/pools/{pool.get('id','').split('_')[-1]}",
                    }
                })
            time.sleep(0.3)
        print(f"      Found {len(pairs)} GeckoTerminal new pools")
    except Exception as e:
        print(f"      Failed: {e}")
    return pairs


def fetch_dex_gems():
    """Main function: scan DEXes for hidden gems with cross-signal validation."""
    init_dex_table()
    print("  Scanning DEX new pairs...")
    now = datetime.now().isoformat()

    # Load reference data for cross-scoring
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute("SELECT DISTINCT coin FROM coin_buzz WHERE coin IS NOT NULL")
    buzz_coins = {row[0].upper() for row in c.fetchall()}
    c.execute("SELECT DISTINCT project_name FROM pre_launch_gems WHERE status != 'DISMISSED'")
    pre_launch_names = {row[0].lower() for row in c.fetchall()}
    conn.close()

    # Fetch and filter pairs
    raw_pairs = fetch_new_dex_pairs()
    if not raw_pairs:
        print("  DEX scanner: no pairs fetched")
        return

    enriched = enrich_pairs(raw_pairs)
    if not enriched:
        print("  DEX scanner: no pairs passed quality filters")
        return

    # Score and deduplicate by address
    seen_addrs = set()
    scored = []
    for pair in enriched:
        addr = pair['address']
        if addr in seen_addrs:
            continue
        seen_addrs.add(addr)
        score = cross_score(pair, buzz_coins, pre_launch_names)
        pair['cross_score'] = score
        pair['social_buzz'] = 1 if pair['symbol'].upper() in buzz_coins else 0
        pair['pre_launch_match'] = 1 if pair['name'].lower() in pre_launch_names else 0
        scored.append(pair)

    scored.sort(key=lambda x: -x['cross_score'])

    # Store in DB
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    stored = 0
    for pair in scored:
        try:
            c.execute('''INSERT OR REPLACE INTO dex_gems
                (name, symbol, contract_address, chain, dex,
                 price_usd, liquidity_usd, volume_24h, price_change_24h,
                 txns_24h, age_hours, social_buzz, pre_launch_match,
                 cross_score, dex_url, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (pair['name'], pair['symbol'], pair['address'], pair['chain'],
                 pair['dex'], pair['price_usd'], pair['liquidity_usd'],
                 pair['volume_24h'], pair['price_change_24h'], pair['txns_24h'],
                 pair['age_hours'], pair['social_buzz'], pair['pre_launch_match'],
                 pair['cross_score'], pair['url'], now))
            stored += 1
        except Exception:
            pass
    conn.commit()
    conn.close()

    # Print top gems
    top = scored[:8]
    for p in top:
        if p['cross_score'] >= 2:
            age_str = f"{p['age_hours']:.0f}h" if p['age_hours'] < 48 else f"{p['age_hours']/24:.1f}d"
            liq_str = f"${p['liquidity_usd']/1000:.0f}k"
            chain_short = p['chain'][:3].upper()
            cross_tag = ''
            if p['social_buzz']: cross_tag += ' 🔥social'
            if p['pre_launch_match']: cross_tag += ' 📋ICO'
            emoji = '💎' if p['cross_score'] >= 6 else '🔍'
            print(f"    {emoji} {p['symbol']} ({p['name'][:20]}) "
                  f"| {chain_short} | liq:{liq_str} | age:{age_str} "
                  f"| score:{p['cross_score']}{cross_tag}")

    print(f"  DEX gems: {stored} stored, {len([x for x in scored if x['cross_score'] >= 4])} high-signal")


def load_dex_gems(min_score=0, limit=50):
    """Load DEX gems for dashboard display."""
    import pandas as pd
    conn = sqlite3.connect('alphascope.db', timeout=30)
    df = pd.read_sql_query(
        """SELECT name, symbol, chain, dex, price_usd, liquidity_usd,
                  volume_24h, price_change_24h, txns_24h, age_hours,
                  social_buzz, pre_launch_match, cross_score, dex_url, fetched_at
           FROM dex_gems
           WHERE cross_score >= ? AND fetched_at >= datetime('now', '-24 hours')
           ORDER BY cross_score DESC, liquidity_usd DESC
           LIMIT ?""",
        conn, params=(min_score, limit))
    conn.close()
    return df


if __name__ == '__main__':
    print("AlphaScope — DEX Scanner v1.0")
    print("=" * 50)
    fetch_dex_gems()
    print()
    df = load_dex_gems(min_score=2)
    if not df.empty:
        print(f"Top {min(10, len(df))} DEX gems:")
        for _, r in df.head(10).iterrows():
            age = f"{r['age_hours']:.0f}h" if r['age_hours'] < 48 else f"{r['age_hours']/24:.1f}d"
            print(f"  {'💎' if r['cross_score'] >= 6 else '🔍'} {r['symbol']} | "
                  f"{r['chain']} | liq:${r['liquidity_usd']/1000:.0f}k | "
                  f"age:{age} | score:{r['cross_score']}")
