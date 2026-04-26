"""
AlphaScope — Wallet Reader v1.0
Read-only EVM wallet balance importer.
No private keys. Public address only.
Reads ETH + ERC-20 token balances via free public RPC.
Auto-populates portfolio table.

Usage:
  from wallet_reader import import_evm_wallet
  import_evm_wallet('0xYourAddress')
  
  # Or from CLI:
  python3 wallet_reader.py 0xYourAddress
"""

import requests
import json
import sqlite3
import time
from datetime import datetime

# Free public RPC endpoints — no API key needed
RPC_ENDPOINTS = {
    'ethereum': 'https://1rpc.io/eth',
    'arbitrum': 'https://arb1.arbitrum.io/rpc',
    'base':     'https://mainnet.base.org',
    'optimism': 'https://mainnet.optimism.io',
    'bsc':      'https://bsc-dataseed1.defibit.io',
    'polygon':  'https://polygon.llamarpc.com',
}

# Well-known ERC-20 tokens to check (address -> symbol, name, decimals, coingecko_id)
KNOWN_TOKENS = {
    'ethereum': {
        '0xdac17f958d2ee523a2206206994597c13d831ec7': ('USDT', 'Tether',        6,  'tether'),
        '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48': ('USDC', 'USD Coin',      6,  'usd-coin'),
        '0x514910771af9ca656af840dff83e8264ecf986ca': ('LINK', 'Chainlink',    18,  'chainlink'),
        '0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9': ('AAVE', 'Aave',         18,  'aave'),
        '0x1f9840a85d5af5bf1d1762f925bdaddc4201f984': ('UNI',  'Uniswap',      18,  'uniswap'),
        '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599': ('WBTC', 'Wrapped BTC',  8,  'wrapped-bitcoin'),
        '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2': ('WETH', 'Wrapped ETH',  18,  'weth'),
        '0x6b175474e89094c44da98b954eedeac495271d0f': ('DAI',  'Dai',          18,  'dai'),
        '0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce': ('SHIB', 'Shiba Inu',   18,  'shiba-inu'),
        '0x0d8775f648430679a709e98d2b0cb6250d2887ef': ('BAT',  'Basic Attn',  18,  'basic-attention-token'),
        '0x4d224452801aced8b2f0aebe155379bb5d594381': ('APE',  'ApeCoin',      18,  'apecoin'),
        '0xd533a949740bb3306d119cc777fa900ba034cd52': ('CRV',  'Curve DAO',   18,  'curve-dao-token'),
        '0xc00e94cb662c3520282e6f5717214004a7f26888': ('COMP', 'Compound',    18,  'compound-governance-token'),
        '0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2': ('MKR',  'Maker',       18,  'maker'),
    },
    'arbitrum': {
        '0x912ce59144191c1204e64559fe8253a0e49e6548': ('ARB',  'Arbitrum',    18,  'arbitrum'),
        '0xaf88d065e77c8cc2239327c5edb3a432268e5831': ('USDC', 'USD Coin',     6,  'usd-coin'),
        '0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9': ('USDT', 'Tether',       6,  'tether'),
        '0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f': ('WBTC', 'Wrapped BTC',  8,  'wrapped-bitcoin'),
    },
    'base': {
        '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913': ('USDC', 'USD Coin',     6,  'usd-coin'),
        '0x50c5725949a6f0c72e6c4a641f24049a917db0cb': ('DAI',  'Dai',         18,  'dai'),
    },
    'bsc': {
        '0x55d398326f99059ff775485246999027b3197955': ('USDT', 'Tether',      18,  'tether'),
        '0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d': ('USDC', 'USD Coin',    18,  'usd-coin'),
        '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c': ('WBNB', 'Wrapped BNB', 18,  'wbnb'),
        '0x2170ed0880ac9a755fd29b2688956bd959f933f8': ('ETH',  'Ethereum',    18,  'ethereum'),
    },
}

MIN_VALUE_USD = 1.0  # Ignore dust positions below $1


def rpc_call(endpoint, method, params):
    """Make a JSON-RPC call."""
    try:
        res = requests.post(endpoint, json={
            'jsonrpc': '2.0', 'id': 1,
            'method': method, 'params': params,
        }, timeout=10, headers={'Content-Type': 'application/json'})
        data = res.json()
        return data.get('result')
    except Exception as e:
        return None


def get_eth_balance(address, rpc):
    """Get native token balance (ETH/BNB/MATIC etc.) in wei."""
    result = rpc_call(rpc, 'eth_getBalance', [address, 'latest'])
    if result:
        return int(result, 16) / 1e18
    return 0


def get_token_balance(address, token_address, decimals, rpc):
    """Get ERC-20 token balance using balanceOf(address) call."""
    # balanceOf(address) selector = 0x70a08231
    padded = address[2:].zfill(64)  # remove 0x, pad to 32 bytes
    data = '0x70a08231' + padded
    result = rpc_call(rpc, 'eth_call', [
        {'to': token_address, 'data': data},
        'latest'
    ])
    if result and result != '0x':
        try:
            return int(result, 16) / (10 ** decimals)
        except Exception:
            return 0
    return 0


def get_token_prices(coin_ids):
    """Fetch USD prices for a list of CoinGecko coin IDs."""
    if not coin_ids:
        return {}
    try:
        res = requests.get(
            'https://api.coingecko.com/api/v3/simple/price',
            params={'ids': ','.join(coin_ids), 'vs_currencies': 'usd'},
            timeout=12,
        )
        if res.status_code == 200:
            return {k: v.get('usd', 0) for k, v in res.json().items()}
    except Exception:
        pass
    return {}


def import_evm_wallet(address, chains=None, dry_run=False):
    """
    Read balances from an EVM wallet address across specified chains.
    Upserts non-dust positions into the portfolio table.
    
    Args:
        address: Public EVM address (0x...)
        chains: List of chains to check. Default: all supported.
        dry_run: If True, print results without writing to DB.
    """
    if not address.startswith('0x') or len(address) != 42:
        print(f"❌ Invalid address: {address}")
        return []

    address = address.lower()
    chains = chains or list(RPC_ENDPOINTS.keys())
    print(f"\n  Reading wallet {address[:8]}...{address[-6:]}")

    found_positions = []
    all_cg_ids = set()

    for chain in chains:
        rpc = RPC_ENDPOINTS.get(chain)
        if not rpc:
            continue

        native_symbols = {
            'ethereum': ('ETH',  'Ethereum',  'ethereum'),
            'arbitrum': ('ETH',  'Ethereum',  'ethereum'),
            'base':     ('ETH',  'Ethereum',  'ethereum'),
            'optimism': ('ETH',  'Ethereum',  'ethereum'),
            'bsc':      ('BNB',  'BNB',       'binancecoin'),
            'polygon':  ('MATIC','Polygon',   'matic-network'),
        }

        # Native balance
        sym, name, cg_id = native_symbols.get(chain, ('ETH', 'Ethereum', 'ethereum'))
        bal = get_eth_balance(address, rpc)
        if bal > 0.0001:
            found_positions.append({
                'symbol': sym, 'name': name, 'coin_id': cg_id,
                'amount': bal, 'chain': chain, 'token_address': 'native',
            })
            all_cg_ids.add(cg_id)

        # ERC-20 tokens
        chain_tokens = KNOWN_TOKENS.get(chain, {})
        for token_addr, (tok_sym, tok_name, decimals, tok_cg_id) in chain_tokens.items():
            bal = get_token_balance(address, token_addr, decimals, rpc)
            if bal > 0.001:
                found_positions.append({
                    'symbol': tok_sym, 'name': tok_name, 'coin_id': tok_cg_id,
                    'amount': bal, 'chain': chain, 'token_address': token_addr,
                })
                all_cg_ids.add(tok_cg_id)
            time.sleep(0.05)  # rate limit

        time.sleep(0.5)

    if not found_positions:
        print("  No balances found (all dust or zero)")
        return []

    # Get prices to filter dust
    prices = get_token_prices(list(all_cg_ids))

    # Filter and display
    valid = []
    print(f"\n  {'Symbol':<8} {'Chain':<10} {'Amount':>12}  {'Value USD':>10}")
    print(f"  {'-'*8} {'-'*10} {'-'*12}  {'-'*10}")
    for pos in found_positions:
        price = prices.get(pos['coin_id'], 0)
        value = pos['amount'] * price
        if value < MIN_VALUE_USD:
            continue
        valid.append({**pos, 'price': price, 'value': value})
        print(f"  {pos['symbol']:<8} {pos['chain']:<10} {pos['amount']:>12.4f}  ${value:>9,.2f}")

    print(f"\n  Total positions: {len(valid)}")
    total = sum(p['value'] for p in valid)
    print(f"  Estimated total: ${total:,.2f}")

    if dry_run:
        print("\n  (dry_run=True — not writing to DB)")
        return valid

    # Write to portfolio DB
    from portfolio import init_portfolio_table
    init_portfolio_table()
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    now = datetime.now().isoformat()
    written = 0
    for pos in valid:
        try:
            # Use INSERT OR IGNORE — don't overwrite manually set entry prices
            c.execute('''INSERT OR IGNORE INTO portfolio
                (coin_id, symbol, name, chain, wallet_address, amount,
                 entry_price_usd, entry_date, notes, status, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (pos['coin_id'], pos['symbol'], pos['name'],
                 pos['chain'], address, pos['amount'],
                 pos['price'],  # current price as entry if new
                 now,
                 f'Auto-imported from wallet {address[:8]}...',
                 'HOLDING', now))
            if c.rowcount:
                written += 1
            else:
                # Update amount for existing positions
                c.execute('''UPDATE portfolio SET amount=?, updated_at=?
                             WHERE coin_id=? AND chain=? AND wallet_address=?''',
                    (pos['amount'], now, pos['coin_id'], pos['chain'], address))
        except Exception as e:
            print(f"  DB error for {pos['symbol']}: {e}")

    conn.commit()
    conn.close()
    print(f"  ✅ {written} new positions imported, {len(valid)-written} updated")
    return valid


def sync_wallet(address, chains=None):
    """Sync wallet balances (update amounts without changing entry prices)."""
    return import_evm_wallet(address, chains=chains, dry_run=False)


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 wallet_reader.py <0xAddress> [chain1,chain2,...]")
        print("Example: python3 wallet_reader.py 0xAbCd... ethereum,arbitrum")
        print("\nSupported chains:", ', '.join(RPC_ENDPOINTS.keys()))
        sys.exit(1)

    addr = sys.argv[1]
    chains = sys.argv[2].split(',') if len(sys.argv) > 2 else None
    import_evm_wallet(addr, chains=chains)
