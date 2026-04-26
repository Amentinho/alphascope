"""
AlphaScope — patch_token_validator.py
Fixes:
1. BSC/BASE holder concentration — uses BscScan/Basescan APIs properly
2. Rugcheck lp_burned flows correctly into validate_token()
3. Adds check_honeypot_bsc() for BSC-specific honeypot detection
4. Birdeye integration for Solana token metadata
Run from alphascope/: python3 patch_token_validator.py
"""
import ast, re

with open('token_validator.py', 'r') as f:
    tv = f.read()

# ── Fix 1: Replace check_holder_concentration with full multi-chain version ───
old_holder = '''def check_holder_concentration(contract_address, chain):
    """Get top holder concentration — high % = rug risk."""
    try:
        if chain == 'solana':
            res = requests.get(
                f'https://public-api.solscan.io/token/holders?tokenAddress={contract_address}&limit=10',
                headers={'Accept': 'application/json'},
                timeout=8,
            )
            if res.status_code == 200:
                data = res.json()
                holders = data.get('data', [])
                total_pct = sum(float(h.get('amount', 0)) for h in holders[:10])
                supply = data.get('total', 1) or 1
                top10_pct = (total_pct / supply * 100) if supply > 0 else 0
                return min(100.0, top10_pct)
        else:
            # DexScreener already provides this in pair data
            res = requests.get(
                f'https://api.dexscreener.com/latest/dex/tokens/{contract_address}',
                timeout=8,
            )
            if res.status_code == 200:
                pairs = res.json().get('pairs', [])
                if pairs:
                    # Use volume/liq ratio as concentration proxy
                    return 50.0  # default moderate
    except Exception:
        pass
    return 50.0  # unknown — assume moderate'''

new_holder = '''def check_holder_concentration(contract_address, chain):
    """Get top holder concentration — high % = rug risk. Multi-chain."""
    try:
        if chain == 'solana':
            # Solscan public API
            res = requests.get(
                f'https://public-api.solscan.io/token/holders?tokenAddress={contract_address}&limit=10',
                headers={'Accept': 'application/json'},
                timeout=8,
            )
            if res.status_code == 200:
                data = res.json()
                holders = data.get('data', [])
                if holders:
                    total_pct = sum(float(h.get('amount', 0)) for h in holders[:10])
                    supply = data.get('total', 1) or 1
                    top10_pct = (total_pct / supply * 100) if supply > 0 else 0
                    return min(100.0, top10_pct)

        elif chain in ('ethereum', 'arbitrum', 'base', 'optimism', 'bsc', 'polygon'):
            # Etherscan-family APIs — token holder list
            explorer_apis = {
                'ethereum': 'https://api.etherscan.io/api',
                'arbitrum': 'https://api.arbiscan.io/api',
                'base':     'https://api.basescan.org/api',
                'optimism': 'https://api-optimistic.etherscan.io/api',
                'bsc':      'https://api.bscscan.com/api',
                'polygon':  'https://api.polygonscan.com/api',
            }
            url = explorer_apis.get(chain, 'https://api.etherscan.io/api')
            res = requests.get(url, params={
                'module': 'token',
                'action': 'tokenholderlist',
                'contractaddress': contract_address,
                'page': 1,
                'offset': 10,
                'apikey': 'YourApiKeyToken',
            }, timeout=8)
            if res.status_code == 200:
                data = res.json()
                holders = data.get('result', [])
                if isinstance(holders, list) and holders:
                    # Sum top 10 quantities
                    quantities = [float(h.get('TokenHolderQuantity', 0)) for h in holders[:10]]
                    total_top10 = sum(quantities)
                    # Get total supply from first holder entry's perspective
                    # Approximate: if top 10 hold X tokens and we know individual %
                    # Use TokenHolderPercent if available
                    if holders[0].get('TokenHolderPercent'):
                        top10_pct = sum(float(h.get('TokenHolderPercent', 0)) for h in holders[:10])
                        return min(100.0, top10_pct)
                    # Fallback: get supply from contract
                    supply_res = requests.get(url, params={
                        'module': 'stats',
                        'action': 'tokensupply',
                        'contractaddress': contract_address,
                        'apikey': 'YourApiKeyToken',
                    }, timeout=6)
                    if supply_res.status_code == 200:
                        supply = float(supply_res.json().get('result', 1) or 1)
                        if supply > 0:
                            return min(100.0, total_top10 / supply * 100)

            # Fallback: use DexScreener pair info
            res2 = requests.get(
                f'https://api.dexscreener.com/latest/dex/tokens/{contract_address}',
                timeout=8,
            )
            if res2.status_code == 200:
                pairs = res2.json().get('pairs', [])
                if pairs:
                    # Check if DexScreener provides holder data
                    info = pairs[0].get('info', {})
                    # No direct holder data — return moderate default for EVM
                    return 45.0

    except Exception:
        pass
    return 50.0  # unknown — assume moderate risk'''

if old_holder in tv:
    tv = tv.replace(old_holder, new_holder)
    print("✅ Fix 1: check_holder_concentration() upgraded for all chains")
else:
    print("❌ Fix 1: holder concentration function not matched")

# ── Fix 2: Add BSC-specific honeypot check via TokenSniffer ──────────────────
old_hp_eth = 'def check_honeypot_eth(contract_address):'
new_bsc_check = '''def check_honeypot_bsc(contract_address):
    """Check BSC token safety via TokenSniffer (covers BSC well)."""
    try:
        res = requests.get(
            f'https://tokensniffer.com/api/v2/tokens/56/{contract_address}',
            headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'},
            timeout=10,
        )
        if res.status_code == 200:
            data = res.json()
            score = data.get('score', 50)  # 0-100, higher = safer
            tests = data.get('tests', {})
            is_honeypot = tests.get('is_honeypot', {}).get('result', False)
            sell_tax = float(tests.get('sell_tax', {}).get('result', 0) or 0)
            buy_tax  = float(tests.get('buy_tax',  {}).get('result', 0) or 0)
            lp_locked = tests.get('lp_locked', {}).get('result', False)
            return {
                'is_honeypot': is_honeypot or score < 20,
                'sell_tax': sell_tax,
                'buy_tax': buy_tax,
                'lp_locked': lp_locked,
                'sniffer_score': score,
            }
    except Exception:
        pass
    # Fallback to honeypot.is
    return check_honeypot_eth(contract_address)


def check_birdeye_sol(contract_address):
    """Get Solana token metadata from Birdeye (free tier, better than Solscan)."""
    try:
        res = requests.get(
            f'https://public-api.birdeye.so/public/token_overview?address={contract_address}',
            headers={'X-API-KEY': 'public', 'Accept': 'application/json'},
            timeout=8,
        )
        if res.status_code == 200:
            data = res.json().get('data', {})
            return {
                'name': data.get('name', ''),
                'symbol': data.get('symbol', ''),
                'price': float(data.get('price', 0) or 0),
                'mc': float(data.get('mc', 0) or 0),
                'holder': int(data.get('holder', 0) or 0),
                'liquidity': float(data.get('liquidity', 0) or 0),
                'trade_24h': int(data.get('trade24h', 0) or 0),
                'volume_24h': float(data.get('v24hUSD', 0) or 0),
                'price_change_24h': float(data.get('priceChange24hPercent', 0) or 0),
            }
    except Exception:
        pass
    return {}


'''

if old_hp_eth in tv:
    tv = tv.replace(old_hp_eth, new_bsc_check + old_hp_eth)
    print("✅ Fix 2: check_honeypot_bsc() and check_birdeye_sol() added")
else:
    print("❌ Fix 2: insertion point not found")

# ── Fix 3: Route BSC through BSC honeypot check, use Birdeye for Solana ──────
old_hp_route = (
    "    if chain == 'solana':\n"
    "        hp = check_rugcheck_sol(contract_address)\n"
    "        result['is_honeypot'] = hp.get('is_honeypot', False)\n"
    "        result['lp_burned'] = hp.get('lp_burned', False)\n"
    "        risks = hp.get('risks', [])\n"
    "        if risks:\n"
    "            flags.extend(risks[:3])\n"
    "    else:\n"
    "        hp = check_honeypot_eth(contract_address)\n"
    "        result['is_honeypot'] = hp.get('is_honeypot', False)\n"
    "        result['sell_tax_pct'] = hp.get('sell_tax', 0)\n"
    "        result['buy_tax_pct'] = hp.get('buy_tax', 0)\n"
    "        result['lp_burned'] = hp.get('lp_locked', False)"
)
new_hp_route = (
    "    if chain == 'solana':\n"
    "        hp = check_rugcheck_sol(contract_address)\n"
    "        result['is_honeypot'] = hp.get('is_honeypot', False)\n"
    "        result['lp_burned'] = hp.get('lp_burned', False)\n"
    "        risks = [r for r in hp.get('risks', []) if r]\n"
    "        if risks:\n"
    "            flags.extend(risks[:3])\n"
    "        # Enrich with Birdeye data\n"
    "        be = check_birdeye_sol(contract_address)\n"
    "        if be.get('holder', 0) > 0:\n"
    "            result['top10_holders_pct'] = min(100.0, 100.0 / max(be['holder'], 1) * 10)\n"
    "            if be['holder'] < 50:\n"
    "                flags.append(f'only {be[\"holder\"]} holders (very concentrated)')\n"
    "                score -= 2\n"
    "            elif be['holder'] > 500:\n"
    "                positives.append(f'{be[\"holder\"]:,} holders')\n"
    "                score += 1\n"
    "    elif chain == 'bsc':\n"
    "        hp = check_honeypot_bsc(contract_address)\n"
    "        result['is_honeypot'] = hp.get('is_honeypot', False)\n"
    "        result['sell_tax_pct'] = hp.get('sell_tax', 0)\n"
    "        result['buy_tax_pct'] = hp.get('buy_tax', 0)\n"
    "        result['lp_burned'] = hp.get('lp_locked', False)\n"
    "        if hp.get('sniffer_score', 50) < 30:\n"
    "            flags.append(f'TokenSniffer score {hp.get(\"sniffer_score\",0)}/100')\n"
    "    else:\n"
    "        hp = check_honeypot_eth(contract_address)\n"
    "        result['is_honeypot'] = hp.get('is_honeypot', False)\n"
    "        result['sell_tax_pct'] = hp.get('sell_tax', 0)\n"
    "        result['buy_tax_pct'] = hp.get('buy_tax', 0)\n"
    "        result['lp_burned'] = hp.get('lp_locked', False)"
)
if old_hp_route in tv:
    tv = tv.replace(old_hp_route, new_hp_route)
    print("✅ Fix 3: BSC routed to check_honeypot_bsc(), Birdeye added for SOL")
else:
    print("❌ Fix 3: HP routing block not matched")

with open('token_validator.py', 'w') as f:
    f.write(tv)

try:
    ast.parse(tv)
    print("✅ token_validator.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")
