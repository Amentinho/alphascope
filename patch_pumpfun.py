"""
AlphaScope — patch_pumpfun.py
Adds pump.fun and Birdeye sources to dex_scanner.py
Pump.fun catches SOL tokens in first 5 minutes — before DexScreener
Birdeye provides richer Solana token metadata
"""
import ast, re

with open('dex_scanner.py', 'r') as f:
    ds = f.read()

# ── Add pump.fun fetcher ──────────────────────────────────────────────────────
PUMPFUN_FUNC = '''

def fetch_pumpfun_new():
    """
    Fetch newly created tokens from pump.fun API.
    These are 0-30 minutes old — the earliest possible signal on Solana.
    Pump.fun is where most Solana meme coins originate.
    Free, no auth required.
    """
    print("    Pump.fun new tokens...")
    pairs = []
    try:
        # Pump.fun public API — latest coins sorted by creation time
        res = requests.get(
            'https://frontend-api.pump.fun/coins?offset=0&limit=20&sort=created_timestamp&order=DESC&includeNsfw=false',
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                'Accept': 'application/json',
                'Referer': 'https://pump.fun/',
            },
            timeout=12,
        )
        if res.status_code == 200:
            coins = res.json()
            if isinstance(coins, list):
                now_ts = datetime.now(timezone.utc).timestamp()
                for coin in coins[:20]:
                    created = coin.get('created_timestamp', 0)
                    if created > 1e12:
                        created = created / 1000  # ms to s
                    age_hours = (now_ts - created) / 3600 if created else 999
                    if age_hours > 2:  # only very new tokens
                        continue
                    mint = coin.get('mint', '')
                    symbol = coin.get('symbol', '')
                    name = coin.get('name', '')
                    if not mint or not symbol:
                        continue
                    if not symbol.isascii() or not name.isascii():
                        continue
                    # Pump.fun metrics
                    market_cap = float(coin.get('usd_market_cap', 0) or 0)
                    reply_count = int(coin.get('reply_count', 0) or 0)
                    # Only include if showing real traction
                    if market_cap < 10_000 and reply_count < 5:
                        continue
                    pairs.append({
                        'address': mint,
                        'chain': 'solana',
                        'pair_data': {
                            'chainId': 'solana',
                            'dexId': 'pump.fun',
                            'pairAddress': mint,
                            'baseToken': {
                                'address': mint,
                                'name': name,
                                'symbol': symbol,
                            },
                            'priceUsd': str(coin.get('price', 0) or 0),
                            'liquidity': {'usd': market_cap * 0.1},  # estimate
                            'volume': {'h24': float(coin.get('volume', 0) or 0)},
                            'txns': {'h24': {'buys': reply_count, 'sells': 0}},
                            'pairCreatedAt': int(created * 1000),
                            'url': f'https://pump.fun/{mint}',
                            'info': {
                                'websites': [{'url': coin.get('website', '')}] if coin.get('website') else [],
                                'socials': [
                                    {'type': 'twitter', 'url': coin.get('twitter', '')}
                                ] if coin.get('twitter') else [],
                            },
                            'priceChange': {'h24': 0},
                        }
                    })
            print(f"      Found {len(pairs)} pump.fun tokens under 2h old")
        else:
            print(f"      HTTP {res.status_code}")
    except Exception as e:
        print(f"      Failed: {e}")
    return pairs


def fetch_birdeye_trending():
    """
    Fetch trending Solana tokens from Birdeye (free public API).
    Better data quality than DexScreener for SOL tokens.
    """
    print("    Birdeye trending...")
    pairs = []
    try:
        res = requests.get(
            'https://public-api.birdeye.so/public/tokenlist?sort_by=v24hUSD&sort_type=desc&offset=0&limit=20&min_liquidity=20000',
            headers={'X-API-KEY': 'public', 'Accept': 'application/json'},
            timeout=10,
        )
        if res.status_code == 200:
            data = res.json()
            tokens = data.get('data', {}).get('tokens', []) if isinstance(data.get('data'), dict) else []
            now_ts = datetime.now(timezone.utc).timestamp()
            for token in tokens[:15]:
                addr = token.get('address', '')
                symbol = token.get('symbol', '')
                name = token.get('name', '')
                if not addr or not symbol:
                    continue
                if not symbol.isascii() or not name.isascii():
                    continue
                liq = float(token.get('liquidity', 0) or 0)
                vol = float(token.get('v24hUSD', 0) or 0)
                price = float(token.get('price', 0) or 0)
                price_change = float(token.get('priceChange24hPercent', 0) or 0)
                if liq < MIN_LIQUIDITY_USD:
                    continue
                pairs.append({
                    'address': addr,
                    'chain': 'solana',
                    'pair_data': {
                        'chainId': 'solana',
                        'dexId': 'birdeye',
                        'pairAddress': addr,
                        'baseToken': {'address': addr, 'name': name, 'symbol': symbol},
                        'priceUsd': str(price),
                        'liquidity': {'usd': liq},
                        'volume': {'h24': vol},
                        'txns': {'h24': {'buys': int(token.get('trade24h', 0) or 0), 'sells': 0}},
                        'pairCreatedAt': int(now_ts * 1000) - 3600000,  # assume 1h old if unknown
                        'url': f'https://birdeye.so/token/{addr}',
                        'priceChange': {'h24': price_change},
                    }
                })
            print(f"      Found {len(pairs)} Birdeye trending tokens")
        else:
            print(f"      HTTP {res.status_code}")
    except Exception as e:
        print(f"      Failed: {e}")
    return pairs

'''

# Insert before fetch_dex_gems()
old_main = 'def fetch_dex_gems():'
if old_main in ds:
    ds = ds.replace(old_main, PUMPFUN_FUNC + old_main)
    print("✅ fetch_pumpfun_new() and fetch_birdeye_trending() added to dex_scanner.py")
else:
    print("❌ insertion point not found")

# ── Wire pump.fun and Birdeye into fetch_new_dex_pairs() ─────────────────────
old_fetch = (
    "    # Fallback: search for newly created pairs with high momentum keywords\n"
    "    if not pairs:"
)
new_fetch = (
    "    # Pump.fun — catches SOL tokens in first 5 minutes\n"
    "    pf_pairs = fetch_pumpfun_new()\n"
    "    pairs.extend(pf_pairs)\n"
    "    time.sleep(1)\n"
    "\n"
    "    # Birdeye — better Solana data quality\n"
    "    be_pairs = fetch_birdeye_trending()\n"
    "    # Only add Birdeye tokens not already found by DexScreener\n"
    "    existing_addrs = {p['address'] for p in pairs}\n"
    "    pairs.extend([p for p in be_pairs if p['address'] not in existing_addrs])\n"
    "    time.sleep(1)\n"
    "\n"
    "    # Fallback: search for newly created pairs with high momentum keywords\n"
    "    if not pairs:"
)
if old_fetch in ds:
    ds = ds.replace(old_fetch, new_fetch)
    print("✅ pump.fun and Birdeye wired into fetch_new_dex_pairs()")
else:
    print("❌ fetch fallback block not matched")

# ── Also add pump.fun to TARGET_CHAINS and lower thresholds for pump.fun tokens
old_txns = "MIN_TXNS_24H      = 50         # at least 50 transactions"
new_txns = (
    "MIN_TXNS_24H      = 30         # at least 30 transactions (lowered for pump.fun)\n"
    "MIN_TXNS_PUMPFUN  = 10         # pump.fun tokens can have fewer early on"
)
if old_txns in ds:
    ds = ds.replace(old_txns, new_txns)
    print("✅ Transaction threshold lowered for pump.fun compatibility")

with open('dex_scanner.py', 'w') as f:
    f.write(ds)

try:
    ast.parse(ds)
    print("✅ dex_scanner.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")

print("\n✅ Done. Run: python3 fetcher.py")
