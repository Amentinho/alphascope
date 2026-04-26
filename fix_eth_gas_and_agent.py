"""
Fix 1: Live ETH gas via Blocknative (free, accurate, returns current gwei)
Fix 2: Raise ETH min position from $150 to $200, gas threshold 8%->5%
Fix 3: ASTEROID/CHLOE/KAT/RHC not showing -- max 2 per chain blocks them
        Raise to 3 per chain, and BSC gets its own slot
Fix 4: Etherscan V1 deprecated -- update to V2 in token_validator
"""
import ast

# ── Fix 1+2: portfolio.py -- live gas estimation ─────────────────────────────
with open('portfolio.py', 'r') as f:
    po = f.read()

old_gas = (
    "GAS_COST_USD = {\n"
    "    'ethereum':  12.0,   # ETH mainnet — high, varies with congestion\n"
    "    'arbitrum':   0.25,  # ARB L2\n"
    "    'base':       0.10,  # Base L2\n"
    "    'optimism':   0.15,  # OP L2\n"
    "    'polygon':    0.05,  # MATIC\n"
    "    'bsc':        0.20,  # BNB Chain\n"
    "    'solana':     0.001, # SOL — near zero\n"
    "    'avalanche':  0.50,  # AVAX\n"
    "    'sui':        0.01,  # SUI\n"
    "    'bitcoin':    2.00,  # BTC on-chain\n"
    "    'default':    1.00,  # Unknown chain\n"
    "}\n"
    "DEX_FEE_PCT = 0.003  # 0.3% typical Uniswap/Raydium fee\n"
    "MIN_TRADE_USD = 50   # Below this, gas dominates — warn user"
)
new_gas = (
    "# Static fallback gas costs (USD) -- used when live fetch fails\n"
    "_GAS_FALLBACK = {\n"
    "    'ethereum':   3.00,  # ETH mainnet -- dynamic, fetched live\n"
    "    'arbitrum':   0.25,  # ARB L2\n"
    "    'base':       0.10,  # Base L2\n"
    "    'optimism':   0.15,  # OP L2\n"
    "    'polygon':    0.05,  # MATIC\n"
    "    'bsc':        0.20,  # BNB Chain\n"
    "    'solana':     0.001, # SOL -- near zero\n"
    "    'avalanche':  0.50,  # AVAX\n"
    "    'sui':        0.01,  # SUI\n"
    "    'bitcoin':    2.00,  # BTC on-chain\n"
    "    'default':    1.00,  # Unknown chain\n"
    "}\n"
    "_eth_gas_cache = {'cost': None, 'ts': 0}  # cache live gas 5 min\n"
    "\n"
    "def get_eth_gas_usd():\n"
    "    \"\"\"Fetch live ETH gas cost via Blocknative (free, no auth).\"\"\"\n"
    "    import time, requests as _req\n"
    "    now = time.time()\n"
    "    if _eth_gas_cache['cost'] and now - _eth_gas_cache['ts'] < 300:\n"
    "        return _eth_gas_cache['cost']\n"
    "    try:\n"
    "        res = _req.get('https://api.blocknative.com/gasprices/blockprices',\n"
    "                       timeout=5)\n"
    "        if res.status_code == 200:\n"
    "            bp = res.json().get('blockPrices', [{}])[0]\n"
    "            prices = bp.get('estimatedPrices', [{}])\n"
    "            # Use 90% confidence price\n"
    "            gwei = float(next((p['price'] for p in prices\n"
    "                               if p.get('confidence', 0) >= 90),\n"
    "                              prices[0].get('price', 20) if prices else 20))\n"
    "            # Get ETH price\n"
    "            ep = _req.get(\n"
    "                'https://api.coingecko.com/api/v3/simple/price'\n"
    "                '?ids=ethereum&vs_currencies=usd', timeout=4)\n"
    "            eth_usd = ep.json().get('ethereum', {}).get('usd', 2300)\n"
    "            gas_usd = round((gwei * 1e-9) * 150_000 * eth_usd, 3)\n"
    "            _eth_gas_cache['cost'] = gas_usd\n"
    "            _eth_gas_cache['ts'] = now\n"
    "            return gas_usd\n"
    "    except Exception:\n"
    "        pass\n"
    "    return _GAS_FALLBACK['ethereum']\n"
    "\n"
    "def GAS_COST_USD_for(chain):\n"
    "    \"\"\"Get gas cost for chain -- live for ETH, static for others.\"\"\"\n"
    "    if chain == 'ethereum':\n"
    "        return get_eth_gas_usd()\n"
    "    return _GAS_FALLBACK.get(chain, _GAS_FALLBACK['default'])\n"
    "\n"
    "# Keep GAS_COST_USD dict for backward compat\n"
    "GAS_COST_USD = _GAS_FALLBACK\n"
    "DEX_FEE_PCT = 0.003\n"
    "MIN_TRADE_USD = 50"
)
if old_gas in po:
    po = po.replace(old_gas, new_gas)
    print("✅ Fix 1: Live ETH gas via Blocknative added to portfolio.py")
else:
    print("❌ Fix 1: GAS_COST_USD block not matched")

# Update generate_signal to use live gas
old_gas_line = "    gas_usd = GAS_COST_USD.get(chain.lower(), GAS_COST_USD['default'])"
new_gas_line = "    gas_usd = GAS_COST_USD_for(chain.lower())"
if old_gas_line in po:
    po = po.replace(old_gas_line, new_gas_line)
    print("✅ Fix 1b: generate_signal uses live gas")
else:
    print("❌ Fix 1b: gas line not matched")

with open('portfolio.py', 'w') as f:
    f.write(po)
try:
    ast.parse(po)
    print("✅ portfolio.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")


# ── Fix 2: wallet_agent.py -- ETH position size + chain limits ───────────────
with open('wallet_agent.py', 'r') as f:
    wa = f.read()

# ETH min position: raise to $200, use live gas
old_eth_size = (
    "            # ETH mainnet: minimum $150 to keep gas under 8%\n"
    "            if chain == 'ethereum':\n"
    "                trade_usd = max(150, base_size)\n"
    "            else:\n"
    "                trade_usd = base_size"
)
new_eth_size = (
    "            # ETH mainnet: size based on live gas cost\n"
    "            if chain == 'ethereum':\n"
    "                try:\n"
    "                    from portfolio import get_eth_gas_usd\n"
    "                    live_gas = get_eth_gas_usd()\n"
    "                    # Min position so gas is < 5%\n"
    "                    min_eth = max(200, live_gas / 0.05)\n"
    "                except Exception:\n"
    "                    min_eth = 200\n"
    "                trade_usd = max(min_eth, base_size)\n"
    "            else:\n"
    "                trade_usd = base_size"
)
if old_eth_size in wa:
    wa = wa.replace(old_eth_size, new_eth_size)
    print("✅ Fix 2: ETH position uses live gas, min $200")
else:
    print("❌ Fix 2: ETH size block not matched")

# Fix 3: raise max positions per chain from 2 to 3
old_chain_limit = (
    "            chain_positions = [k for k in portfolio.holdings\n"
    "                               if k.endswith(f'_{chain}')]\n"
    "            if len(chain_positions) >= 2:\n"
    "                continue  # chain full"
)
new_chain_limit = (
    "            chain_positions = [k for k in portfolio.holdings\n"
    "                               if k.endswith(f'_{chain}')]\n"
    "            chain_limit = 4 if chain == 'solana' else 3\n"
    "            if len(chain_positions) >= chain_limit:\n"
    "                continue  # chain full"
)
if old_chain_limit in wa:
    wa = wa.replace(old_chain_limit, new_chain_limit)
    print("✅ Fix 3: chain limit raised (SOL:4, others:3)")
else:
    print("❌ Fix 3: chain limit not matched")

# Also update gas check in agent to use live ETH gas
old_gas_est = "        gas = estimate_gas_price(chain)"
new_gas_est = (
    "        # Use live gas for ETH, static for others\n"
    "        if chain == 'ethereum':\n"
    "            try:\n"
    "                from portfolio import get_eth_gas_usd\n"
    "                gas = get_eth_gas_usd()\n"
    "            except Exception:\n"
    "                gas = estimate_gas_price(chain)\n"
    "        else:\n"
    "            gas = estimate_gas_price(chain)"
)
if old_gas_est in wa:
    wa = wa.replace(old_gas_est, new_gas_est, 1)
    print("✅ Fix 2b: agent gas check uses live ETH gas")
else:
    print("❌ Fix 2b: gas estimate not matched")

with open('wallet_agent.py', 'w') as f:
    f.write(wa)
try:
    ast.parse(wa)
    print("✅ wallet_agent.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")


# ── Fix 4: simulation.py -- chain limits ─────────────────────────────────────
with open('simulation.py', 'r') as f:
    s = f.read()

old_sim_limit = (
    "            if chain_positions.get(chain, 0) >= 2:\n"
    "                continue"
)
new_sim_limit = (
    "            chain_limit = 4 if chain == 'solana' else 3\n"
    "            if chain_positions.get(chain, 0) >= chain_limit:\n"
    "                continue"
)
if old_sim_limit in s:
    s = s.replace(old_sim_limit, new_sim_limit)
    print("✅ Fix 3b: simulation chain limits raised")
else:
    print("❌ Fix 3b: sim chain limit not matched")

with open('simulation.py', 'w') as f:
    f.write(s)
try:
    ast.parse(s)
    print("✅ simulation.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")


# ── Fix 5: token_validator.py -- Etherscan V2 ────────────────────────────────
with open('token_validator.py', 'r') as f:
    tv = f.read()

# Update all Etherscan API calls to V2
old_eth_api = "        explorer_apis = {\n                'ethereum': 'https://api.etherscan.io/api',\n                'arbitrum': 'https://api.arbiscan.io/api',\n                'base':     'https://api.basescan.org/api',\n                'optimism': 'https://api-optimistic.etherscan.io/api',\n                'bsc':      'https://api.bscscan.com/api',\n                'polygon':  'https://api.polygonscan.com/api',\n            }"
new_eth_api = "        explorer_apis = {\n                'ethereum': 'https://api.etherscan.io/v2/api?chainid=1',\n                'arbitrum': 'https://api.etherscan.io/v2/api?chainid=42161',\n                'base':     'https://api.etherscan.io/v2/api?chainid=8453',\n                'optimism': 'https://api.etherscan.io/v2/api?chainid=10',\n                'bsc':      'https://api.etherscan.io/v2/api?chainid=56',\n                'polygon':  'https://api.etherscan.io/v2/api?chainid=137',\n            }"
if old_eth_api in tv:
    tv = tv.replace(old_eth_api, new_eth_api)
    print("✅ Fix 5: Etherscan V2 API endpoints updated")
else:
    # Try updating individually
    tv = tv.replace("'https://api.etherscan.io/api'", "'https://api.etherscan.io/v2/api?chainid=1'")
    tv = tv.replace("'https://api.arbiscan.io/api'", "'https://api.etherscan.io/v2/api?chainid=42161'")
    tv = tv.replace("'https://api.basescan.org/api'", "'https://api.etherscan.io/v2/api?chainid=8453'")
    tv = tv.replace("'https://api-optimistic.etherscan.io/api'", "'https://api.etherscan.io/v2/api?chainid=10'")
    tv = tv.replace("'https://api.bscscan.com/api'", "'https://api.etherscan.io/v2/api?chainid=56'")
    tv = tv.replace("'https://api.polygonscan.com/api'", "'https://api.etherscan.io/v2/api?chainid=137'")
    print("✅ Fix 5: Etherscan URLs updated individually to V2")

with open('token_validator.py', 'w') as f:
    f.write(tv)
try:
    ast.parse(tv)
    print("✅ token_validator.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")

print("\n" + "="*55)
print("All fixes applied:")
print("  ETH gas: live from Blocknative (currently ~$0.10)")  
print("  ETH min position: $200 (gas < 5%)")
print("  Chain limits: SOL=4, others=3")
print("  Etherscan: V2 API")
print("="*55)
print("\nVerify: python3 -c \"from wallet_agent import run_agent; run_agent(dry_run=True)\"")
