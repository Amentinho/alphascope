"""
AlphaScope Executor v2.1 — Multi-chain
=======================================
Chains:
  SOL  — Jupiter + Jito MEV bundles
  BASE — Uniswap v3 Universal Router (no API key needed)
  ETH  — Uniswap v3 Universal Router + Flashbots Protect RPC

Phase 1: EXECUTOR_DRY_RUN=true   — Telegram alerts only
Phase 2: EXECUTOR_DRY_RUN=false  — Real swaps on SOL + BASE + ETH

.env keys:
    SOL_PRIVATE_KEY=<base58>
    EVM_PRIVATE_KEY=<0x hex>
    EVM_WALLET_ADDRESS=<0x address>
    TELEGRAM_BOT_TOKEN=<token>
    TELEGRAM_CHAT_ID=<id>
    EXECUTOR_DRY_RUN=true
    EXECUTOR_MAX_SOL_PER_TRADE=0.5    # max SOL per trade
    EXECUTOR_MAX_ETH_PER_TRADE=0.02   # max ETH per trade (~$48)
    EXECUTOR_SLIPPAGE_BPS=300         # 3% slippage

Install for Phase 2:
    pip install web3 solana solders base58
"""

import os, json, time, threading, requests
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
def _env(key, default=''):
    val = os.environ.get(key, '')
    if val: return val
    try:
        last = default
        for line in open('.env'):
            line = line.strip()
            if line.startswith(f'{key}='):
                last = line.split('=', 1)[1].strip()
        return last if last != default or True else default
    except Exception:
        pass
    return default

def _is_dry_run():
    """Read DRY_RUN fresh every call — never cached."""
    val = _env('EXECUTOR_DRY_RUN', 'true').lower().strip()
    return val != 'false'

DRY_RUN = _is_dry_run()
MAX_SOL_PER_TRADE = float(_env('EXECUTOR_MAX_SOL_PER_TRADE','0.02'))
MAX_ETH_PER_TRADE = float(_env('EXECUTOR_MAX_ETH_PER_TRADE','0.001'))
SLIPPAGE_BPS      = int(_env('EXECUTOR_SLIPPAGE_BPS','300'))
EVM_SLIPPAGE_BPS  = int(_env('EXECUTOR_EVM_SLIPPAGE_BPS', str(SLIPPAGE_BPS)))
MAX_GAS_COST_PCT  = float(_env('EXECUTOR_MAX_GAS_COST_PCT', '15'))
MAX_GAS_COST_USD  = float(_env('EXECUTOR_MAX_GAS_COST_USD', '1.00'))
PUMPFUN_SLIPPAGE  = int(_env('EXECUTOR_PUMPFUN_SLIPPAGE', '15'))
PUMPFUN_PRIORITY_FEE_SOL = float(_env('EXECUTOR_PUMPFUN_PRIORITY_FEE_SOL', '0.0001'))
TELEGRAM_TOKEN    = _env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT     = _env('TELEGRAM_CHAT_ID')
SOL_PRIVATE_KEY   = _env('SOL_PRIVATE_KEY')
EVM_PRIVATE_KEY   = _env('EVM_PRIVATE_KEY')
EVM_WALLET        = _env('EVM_WALLET_ADDRESS')

# Chain config
CHAIN_IDS = {'ethereum':1, 'base':8453, 'arbitrum':42161, 'bsc':56}
# Primary RPCs. Use private paid endpoints via env vars; never hardcode keys.
RPCS = {
    'ethereum': _env('ETH_RPC_URL', 'https://ethereum-rpc.publicnode.com'),
    'base':     _env('BASE_RPC_URL', 'https://mainnet.base.org'),
    'arbitrum': _env('ARBITRUM_RPC_URL', 'https://arb1.arbitrum.io/rpc'),
}
RPCS_FALLBACK = {
    'ethereum': [
        'https://cloudflare-eth.com',
        'https://rpc.flashbots.net',
        'https://rpc.ankr.com/eth',
    ],
    'base': [
        'https://base-rpc.publicnode.com',
        'https://1rpc.io/base',
        'https://base.llamarpc.com',
        'https://base-mainnet.public.blastapi.io',
    ],
}

# Uniswap v3 — same address on ETH and BASE
UNISWAP_ROUTER = '0xE592427A0AEce92De3Edee1F18E0157C05861564'  # SwapRouter02
UNISWAP_QUOTER = '0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6'  # ETH Quoter v1
UNISWAP_QUOTERS = {
    'ethereum': '0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6',
    'base':     '0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a',
}

# WETH address per chain
WETH = {
    'ethereum': '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
    'base':     '0x4200000000000000000000000000000000000006',
}

# Uniswap v3 pool fees (try 0.3% first, then 1%, then 0.05%)
POOL_FEES = [3000, 10000, 500]

# Comprehensive DEX router registry — all major legit DEXes
# Ordered by volume/reliability — tried in sequence until one works
DEX_ROUTERS = {
    'ethereum': [
        # Uniswap — largest by volume, try all fee tiers
        {'name': 'Uniswap v3',      'type': 'v3',  'router': '0xE592427A0AEce92De3Edee1F18E0157C05861564'},
        {'name': 'Uniswap v2',      'type': 'v2',  'router': '0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D'},
        # SushiSwap — 2nd largest, many long-tail tokens
        {'name': 'SushiSwap v2',    'type': 'v2',  'router': '0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F'},
        {'name': 'SushiSwap v3',    'type': 'v3',  'router': '0x2c7a51A357d5739C5C74Bf3C96816849d2c9F726'},
        # PancakeSwap v3 on ETH — growing fast
        {'name': 'PancakeSwap v3',  'type': 'v3',  'router': '0x1b81D678ffb9C0263b24A97847620C99d213eB14'},
        {'name': 'PancakeSwap v2',  'type': 'v2',  'router': '0xEfF92A263d31888d860bD50809A8D171709b7b1c'},
        # Balancer v2 — good for multi-token pools
        {'name': 'Balancer v2',     'type': 'balancer', 'router': '0xBA12222222228d8Ba445958a75a0704d566BF2C8'},
    ],
    'base': [
        # Aerodrome FIRST — dominant DEX on Base by far, most BASE tokens live here
        {'name': 'Aerodrome v2',    'type': 'aero', 'router': '0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43'},
        {'name': 'Aerodrome v1',    'type': 'v2',   'router': '0x420DD381b31aEf6683db6B902084cB0FFECe40Da'},
        # Uniswap v3 on Base — second choice
        {'name': 'Uniswap v3',      'type': 'v3',  'router': '0x2626664c2603336E57B271c5C0b26F421741e481'},
        {'name': 'Uniswap v2',      'type': 'v2',  'router': '0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24'},
        # NOTE: SushiSwap/PancakeSwap removed — ghost pools, tokens not received
    ],
    'arbitrum': [
        {'name': 'Uniswap v3',      'type': 'v3',  'router': '0xE592427A0AEce92De3Edee1F18E0157C05861564'},
        {'name': 'Camelot',         'type': 'v2',  'router': '0xc873fEcbd354f5A56E00E710B90EF4201db2448d'},
        {'name': 'SushiSwap v3',    'type': 'v3',  'router': '0x2c7a51A357d5739C5C74Bf3C96816849d2c9F726'},
        {'name': 'PancakeSwap v3',  'type': 'v3',  'router': '0x1b81D678ffb9C0263b24A97847620C99d213eB14'},
    ],
}

# Aerodrome router ABI (same as Uniswap v2 but with different function names)
AERODROME_ABI = [
    {
        "inputs": [
            {"name": "amountOutMin", "type": "uint256"},
            {"components": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "stable", "type": "bool"},
                {"name": "factory", "type": "address"},
            ], "name": "routes", "type": "tuple[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactETHForTokens",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "payable", "type": "function"
    },
    {
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"components": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "stable", "type": "bool"},
                {"name": "factory", "type": "address"},
            ], "name": "routes", "type": "tuple[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForETH",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable", "type": "function"
    },
    {
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"components": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "stable", "type": "bool"},
                {"name": "factory", "type": "address"},
            ], "name": "routes", "type": "tuple[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view", "type": "function"
    },
]
# Aerodrome factory address on Base
AERODROME_FACTORY = '0x420DD381b31aEf6683db6B902084cB0FFECe40Da'

# Uniswap v2 router ABI (minimal)
UNISWAP_V2_ABI = [
    # Buy: ETH -> Token (fee-on-transfer safe)
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    },
    # Buy: ETH -> Token (standard, fallback)
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "name": "swapExactETHForTokens",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "payable",
        "type": "function"
    },
    # Sell: Token -> ETH (fee-on-transfer safe)
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    # Sell: Token -> ETH (standard, fallback)
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "name": "swapExactTokensForETH",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function"
    },
]

# Jupiter / Jito
JUPITER_QUOTE = 'https://api.jup.ag/swap/v1/quote'
JUPITER_SWAP  = 'https://api.jup.ag/swap/v1/swap'
# Jito endpoints — multiple regions, fallback to direct RPC
JITO_ENDPOINTS = [
    'https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles',
    'https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/bundles',
    'https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/bundles',
    'https://tokyo.mainnet.block-engine.jito.wtf/api/v1/bundles',
]
JITO_ENDPOINT = JITO_ENDPOINTS[0]  # kept for compatibility
SOL_RPC_DIRECT = _env('SOL_RPC_URL', 'https://api.mainnet-beta.solana.com')
SOL_RPC_FALLBACKS = [r for r in [
    _env('SOL_RPC_URL', ''),
    'https://solana-rpc.publicnode.com',
    'https://rpc.ankr.com/solana',
    'https://api.mainnet-beta.solana.com',
] if r]

def _sol_rpc_send(signed_b64, skip_preflight=True):
    """Submit signed SOL tx to best available RPC. Tries SOL_RPC_FALLBACKS in order."""
    last_err = None
    for rpc in SOL_RPC_FALLBACKS:
        try:
            r = requests.post(rpc, json={
                'jsonrpc': '2.0', 'id': 1, 'method': 'sendTransaction',
                'params': [signed_b64, {
                    'encoding': 'base64', 'skipPreflight': skip_preflight,
                    'maxRetries': 3, 'preflightCommitment': 'confirmed',
                }]
            }, headers={'Content-Type': 'application/json'}, timeout=15)
            if r.status_code == 200:
                result = r.json()
                if 'result' in result and result['result']:
                    return result['result']
                last_err = result.get('error', '')
        except Exception as e:
            last_err = str(e)
    raise Exception(f"All SOL RPCs failed: {last_err}")
WSOL_MINT     = 'So11111111111111111111111111111111111111112'


# ── Telegram ──────────────────────────────────────────────────────────────────
def _tg(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT: return
    def _send():
        try:
            requests.post(
                f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
                json={'chat_id': TELEGRAM_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                timeout=8)
        except Exception: pass
    threading.Thread(target=_send, daemon=True).start()

def alert_buy(symbol, chain, usd, price, reason='', dry=True, cash_left=None):
    mode = '🔵 DRY' if dry else '✅ LIVE'
    # Show real wallet balance for live trades
    if not dry:
        wallet_bal = _get_real_wallet_balance(chain)
        budget_line = f"💼 Wallet: {wallet_bal}\n" if wallet_bal else ""
    else:
        budget_line = f"💼 Sim cash: ${cash_left:.0f}\n" if cash_left is not None else ""
    _tg(f"{mode} <b>BUY {symbol}</b> ({chain.upper()})\n"
        f"💵 ${usd:.0f} @ ${price:.6g}\n"
        f"{budget_line}"
        f"📋 {reason[:80]}\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}")
    print(f"    📱 TG: BUY {symbol} ${usd:.0f} @ ${price:.6g} {'[DRY]' if dry else '[LIVE]'}")

def alert_sell(symbol, chain, price, pnl_pct, reason, dry=True,
               pnl_usd=None, trading_total=None, trading_pct=None):
    emoji = '🟢' if pnl_pct >= 0 else '🔴'
    mode  = '🔵 DRY' if dry else '✅ LIVE'
    pnl_line = f"💰 P&L: ${pnl_usd:+.2f} ({pnl_pct:+.1f}%)\n" if pnl_usd is not None else f"📊 {pnl_pct:+.1f}%\n"
    portfolio_line = f"📈 Portfolio: ${trading_total:,.0f} ({trading_pct:+.1f}%)\n" if trading_total is not None else ""
    _tg(f"{mode} {emoji} <b>SELL {symbol}</b> ({chain.upper()})\n"
        f"{pnl_line}"
        f"{portfolio_line}"
        f"📋 {reason.upper()}\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}")
    print(f"    📱 TG: SELL {symbol} {pnl_pct:+.1f}% {reason} {'[DRY]' if dry else '[LIVE]'}")

def alert_error(msg):
    _tg(f"⚠️ <b>ERROR</b>\n{msg[:300]}")
    print(f"    ⚠️  {msg}")

def alert_start(sim_id, hours, capital):
    dry = _is_dry_run()
    mode = '🔵 DRY RUN' if dry else '🚀 LIVE'
    if not dry:
        # Show real wallet balances
        sol_line = ''
        eth_line = ''
        base_line = ''
        try:
            kp = _sol_keypair()
            if kp:
                r = requests.post('https://api.mainnet-beta.solana.com', json={
                    'jsonrpc':'2.0','id':1,'method':'getBalance',
                    'params':[str(kp.pubkey())]}, timeout=6)
                sol = r.json().get('result',{}).get('value',0)/1e9
                sol_line = f"\n🟣 SOL: {sol:.3f} SOL (${sol*_sol_price():.0f})"
        except Exception: pass
        try:
            if EVM_WALLET:
                from web3 import Web3
                eth_price = _eth_price()
                # Try multiple RPCs per chain
                chain_rpcs = {
                    'ethereum': ['https://rpc.ankr.com/eth','https://cloudflare-eth.com',
                                 'https://1rpc.io/eth','https://eth.llamarpc.com',
                                 'https://eth-mainnet.public.blastapi.io'],
                    'base':     ['https://mainnet.base.org','https://base.llamarpc.com',
                                 'https://1rpc.io/base'],
                }
                emojis = {'ethereum': '🔵', 'base': '🔷'}
                for chain in ['ethereum', 'base']:
                    for rpc in chain_rpcs[chain]:
                        try:
                            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 6}))
                            bal = w3.eth.get_balance(w3.to_checksum_address(EVM_WALLET))/1e18
                            emoji = emojis[chain]
                            label = 'ETH' if chain == 'ethereum' else 'BASE'
                            line = f"\n{emoji} {label}: {bal:.4f} ETH (${bal*eth_price:.0f})"
                            if chain == 'ethereum':
                                eth_line = line
                            else:
                                base_line = line
                            break
                        except Exception:
                            continue
        except Exception: pass
        lines = [f"🤖 <b>AlphaScope {mode}</b>", f"📋 {sim_id} | {hours}h"]
        if sol_line: lines.append(sol_line.strip())
        if eth_line: lines.append(eth_line.strip())
        if base_line: lines.append(base_line.strip())
        _tg('\n'.join(lines))
    else:
        _tg(f"🤖 <b>AlphaScope {mode}</b>\n📋 {sim_id} | {hours}h\n💰 ${capital:.0f} paper")

def alert_complete(sim_id, pnl_pct, wins, losses, best):
    emoji = '🟢' if pnl_pct >= 0 else '🔴'
    _tg(f"{emoji} <b>Complete {'[DRY]' if _is_dry_run() else '[LIVE]'}</b>\n"
        f"📋 {sim_id}\n💰 {pnl_pct:+.1f}% | {wins}W/{losses}L\n🏆 {best}")


# ── Price helpers ─────────────────────────────────────────────────────────────
def _sol_price():
    try:
        return float(requests.get(
            'https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT',
            timeout=5).json().get('price', 89))
    except Exception: return 89.0


def _get_real_wallet_balance(chain) -> str:
    """Get real wallet balance for display in Telegram."""
    try:
        if chain == 'solana':
            kp = _sol_keypair()
            if not kp: return ''
            r = requests.post('https://api.mainnet-beta.solana.com', json={
                'jsonrpc':'2.0','id':1,'method':'getBalance',
                'params':[str(kp.pubkey())]}, timeout=5)
            bal = r.json().get('result',{}).get('value',0)/1e9
            return f"{bal:.3f} SOL (${bal*_sol_price():.0f})"
        elif chain in ('ethereum','base'):
            if not EVM_WALLET: return ''
            from web3 import Web3
            w3 = _w3(chain)
            if not w3: return ''
            bal = w3.eth.get_balance(w3.to_checksum_address(EVM_WALLET))/1e18
            return f"{bal:.4f} ETH (${bal*_eth_price():.0f})"
    except Exception:
        pass
    return 

def _eth_price():
    try:
        return float(requests.get(
            'https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT',
            timeout=5).json().get('price', 2400))
    except Exception: return 2400.0


# ── SOL: Jupiter + Jito ───────────────────────────────────────────────────────
def _sol_keypair():
    if not SOL_PRIVATE_KEY: return None
    try:
        from solders.keypair import Keypair
        import base58
        try:
            return Keypair.from_base58_string(SOL_PRIVATE_KEY)
        except Exception:
            return Keypair.from_bytes(base58.b58decode(SOL_PRIVATE_KEY))
    except ImportError:
        print("  executor: pip install solana solders base58")
        return None
    except Exception as e:
        print(f"  SOL keypair error: {e}")
        return None

PUMP_FUN_PROGRAM = 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'
PUMP_FUN_GRADUATION_MCAP = 69000  # ~$69k market cap to graduate to Raydium


def _is_pumpfun_graduated(mint_address) -> bool:
    """
    Check if a PumpFun token has graduated to Raydium.
    Ungraduated tokens can't be bought via Jupiter — they need PumpFun's own UI.
    Returns True if safe to trade via Jupiter, False if still on bonding curve,
    or None if the check is inconclusive.
    """
    try:
        # Check DexScreener — graduated tokens show Raydium as DEX
        r = requests.get(
            f'https://api.dexscreener.com/latest/dex/tokens/{mint_address}',
            timeout=6)
        if r.status_code == 200:
            pairs = r.json().get('pairs', [])
            for pair in pairs:
                if pair.get('chainId') != 'solana':
                    continue
                dex = pair.get('dexId', '').lower()
                # If on Raydium or Orca = graduated, safe to trade
                if dex in ('raydium', 'orca', 'meteora', 'lifinity'):
                    return True
                # If only on pump.fun = not graduated
                if dex in ('pump.fun', 'pumpfun', 'pump'):
                    return False
        # Check Jupiter token list — graduated tokens appear here
        r2 = requests.get(
            f'https://api.jup.ag/tokens/v1/token/{mint_address}',
            timeout=6)
        if r2.status_code == 200:
            return True  # In Jupiter's list = tradeable
    except Exception:
        pass
    return None  # Unknown is not safe enough for live routing decisions.


def _resolve_sol_mint(symbol) -> str:
    """Look up SOL token mint address. Tries Jupiter, DexScreener, CoinGecko."""
    sym_upper = symbol.upper()

    # 1. Jupiter token search
    try:
        r = requests.get(
            f'https://api.jup.ag/tokens/v1/search?query={symbol}',
            timeout=8)
        if r.status_code == 200:
            tokens = r.json() if isinstance(r.json(), list) else r.json().get('tokens', [])
            for t in tokens[:5]:
                if t.get('symbol', '').upper() == sym_upper:
                    addr = t.get('address', '') or t.get('mint', '')
                    if addr and len(addr) > 30:
                        print(f"    mint found via Jupiter: {symbol} = {addr[:16]}...")
                        return addr
    except Exception:
        pass

    # 2. DexScreener search on Solana
    try:
        r = requests.get(
            f'https://api.dexscreener.com/latest/dex/search?q={symbol}',
            timeout=8)
        if r.status_code == 200:
            pairs = r.json().get('pairs', [])
            for pair in pairs[:10]:
                if pair.get('chainId') != 'solana':
                    continue
                base = pair.get('baseToken', {})
                if base.get('symbol', '').upper() == sym_upper:
                    addr = base.get('address', '')
                    if addr and len(addr) > 30:
                        print(f"    mint found via DexScreener: {symbol} = {addr[:16]}...")
                        return addr
    except Exception:
        pass

    # 3. CoinGecko — for established tokens like PENGU
    try:
        r = requests.get(
            f'https://api.coingecko.com/api/v3/search?query={symbol}',
            timeout=8)
        if r.status_code == 200:
            coins = r.json().get('coins', [])
            for coin in coins[:3]:
                if coin.get('symbol', '').upper() == sym_upper:
                    cid = coin.get('id', '')
                    r2 = requests.get(
                        f'https://api.coingecko.com/api/v3/coins/{cid}',
                        timeout=8)
                    if r2.status_code == 200:
                        platforms = r2.json().get('platforms', {})
                        addr = platforms.get('solana', '')
                        if addr and len(addr) > 30:
                            print(f"    mint found via CoinGecko: {symbol} = {addr[:16]}...")
                            return addr
    except Exception:
        pass

    print(f"    mint not found for {symbol}")
    return ''


def _jupiter_quote(input_mint, output_mint, amount_raw):
    return _jupiter_quote_slippage(input_mint, output_mint, amount_raw, SLIPPAGE_BPS)

def _jupiter_quote_slippage(input_mint, output_mint, amount_raw, slippage_bps):
    try:
        r = requests.get(JUPITER_QUOTE, params={
            'inputMint': input_mint, 'outputMint': output_mint,
            'amount': amount_raw, 'slippageBps': slippage_bps,
        }, timeout=10)
        if r.status_code == 200: return r.json()
        print(f"    Jupiter {r.status_code}: {r.text[:200]}")
    except Exception as e: print(f"    Jupiter: {e}")
    return None

def _pumpfun_buy(kp, contract, sol_amount) -> dict:
    """
    Buy a PumpFun bonding curve token directly via pumpportal trade-local API.
    Used for tokens not yet graduated to Raydium — Jupiter can't route these.
    sol_amount: float, amount of SOL to spend (e.g. 0.01)
    """
    try:
        import base64
        from solders.transaction import VersionedTransaction

        pubkey = str(kp.pubkey())

        r = requests.post('https://pumpportal.fun/api/trade-local', json={
            'publicKey':         pubkey,
            'action':            'buy',
            'mint':              contract,
            'amount':            sol_amount,       # SOL amount to spend
            'denominatedInSol':  'true',
        'slippage':          PUMPFUN_SLIPPAGE,
        'priorityFee':       PUMPFUN_PRIORITY_FEE_SOL,
            'pool':              'pump',
        }, timeout=15)

        if r.status_code != 200:
            return {'success': False, 'error': f'PumpFun API {r.status_code}: {r.text[:100]}'}

        tx_bytes = r.content
        if not tx_bytes:
            return {'success': False, 'error': 'PumpFun returned empty transaction'}

        tx = VersionedTransaction.from_bytes(tx_bytes)
        try:
            signed_tx = VersionedTransaction(tx.message, [kp])
        except Exception:
            signed_tx = tx

        import base64 as _b64
        encoded = _b64.b64encode(bytes(signed_tx)).decode()
        r2 = requests.post('https://api.mainnet-beta.solana.com', json={
            'jsonrpc': '2.0', 'id': 1,
            'method': 'sendTransaction',
            'params': [encoded, {
                'encoding':            'base64',
                'skipPreflight':       False,
                'maxRetries':          3,
                'preflightCommitment': 'confirmed',
            }]
        }, headers={'Content-Type': 'application/json'}, timeout=20)

        result = r2.json()
        if 'result' in result and result['result']:
            tx_hash = result['result']
            print(f"    PumpFun buy: {tx_hash[:16]}...")
            return {'success': True, 'tx': tx_hash, 'method': 'pumpfun_bonding_curve'}
        elif 'error' in result:
            return {'success': False, 'error': f"PumpFun RPC: {result['error']}"}

    except ImportError:
        return {'success': False, 'error': 'pip install solana solders'}
    except Exception as e:
        return {'success': False, 'error': f'PumpFun buy error: {e}'}

    return {'success': False, 'error': 'PumpFun buy failed'}


def _pumpfun_sell(kp, contract, raw_amount) -> dict:
    """
    Sell a PumpFun bonding curve token directly via PumpFun's trade API.
    Used when token hasn't graduated to Raydium yet.
    """
    try:
        import base64
        from solders.transaction import VersionedTransaction

        pubkey = str(kp.pubkey())

        # Step 1: Get quote from PumpFun API
        r = requests.post('https://pumpportal.fun/api/trade-local', json={
            'publicKey': pubkey,
            'action': 'sell',
            'mint': contract,
            'amount': str(raw_amount),  # exact token amount to sell
            'denominatedInSol': 'false',  # we're selling tokens not SOL
            'slippage': PUMPFUN_SLIPPAGE,
            'priorityFee': PUMPFUN_PRIORITY_FEE_SOL,
            'pool': 'pump',
        }, timeout=15)

        if r.status_code != 200:
            return {'success': False, 'error': f'PumpFun API {r.status_code}: {r.text[:100]}'}

        # Step 2: Sign and submit the transaction
        tx_bytes = r.content
        if not tx_bytes:
            return {'success': False, 'error': 'PumpFun returned empty transaction'}

        tx = VersionedTransaction.from_bytes(tx_bytes)
        try:
            signed_tx = VersionedTransaction(tx.message, [kp])
        except Exception:
            signed_tx = tx

        import base64 as _b64
        encoded = _b64.b64encode(bytes(signed_tx)).decode()
        sig = _sol_rpc_send(encoded, skip_preflight=True)
        sol_url = f"https://solscan.io/tx/{sig}"
        print(f"    PumpFun sell submitted: {sig[:16]}...")
        return {'success': True, 'tx': sig, 'url': sol_url, 'method': 'pumpfun'}

    except ImportError:
        return {'success': False, 'error': 'pip install solana solders'}
    except Exception as e:
        return {'success': False, 'error': f'PumpFun sell error: {e}'}

    return {'success': False, 'error': 'PumpFun sell failed'}


def _jito_submit(signed_b64):
    """Submit tx bundle. Tries all Jito endpoints, falls back to direct Solana RPC."""
    # Try all Jito endpoints
    for endpoint in JITO_ENDPOINTS:
        try:
            r = requests.post(endpoint, json={
                'jsonrpc': '2.0', 'id': 1,
                'method': 'sendBundle', 'params': [[signed_b64]]
            }, headers={'Content-Type': 'application/json'}, timeout=10)
            if r.status_code == 200:
                return r.json().get('result', 'submitted')
        except Exception:
            continue

    return _sol_rpc_send(signed_b64, skip_preflight=False)

def _can_sell_sol_token(contract: str) -> tuple:
    """Pre-check: can Jupiter route a sell? Prevents buying honeypots."""
    try:
        r = requests.get(JUPITER_QUOTE, params={
            'inputMint': contract, 'outputMint': WSOL_MINT,
            'amount': '1000000', 'slippageBps': '5000',
        }, timeout=6)
        if r.status_code == 200 and r.json().get('outAmount'):
            return True, 'route_ok'
        return False, f'no_sell_route: {r.text[:60]}'
    except Exception as e:
        return False, f'check_failed_blocking: {str(e)[:60]}'


def execute_sol_buy(symbol, contract, usd) -> dict:
    if _is_dry_run(): return {'success': False, 'mode': 'dry'}
    kp = _sol_keypair()
    if not kp: return {'success': False, 'error': 'No SOL keypair'}
    if contract and len(contract) > 30:
        # Check graduation status
        graduated = _is_pumpfun_graduated(contract)
        if graduated is None:
            return {'success': False,
                    'error': f'Graduation status unknown for {symbol}; refusing live buy'}
        if graduated is False:
            # Token still on bonding curve — try PumpFun direct buy
            print(f"    PumpFun direct buy: {symbol} (bonding curve)")
            sol_price = _sol_price() or 150
            sol_amount = round(usd / sol_price, 4)
            sol_amount = min(sol_amount, MAX_SOL_PER_TRADE)
            pf_result = _pumpfun_buy(kp, contract, sol_amount)
            if pf_result.get('success'):
                return pf_result
            # PumpFun failed — reject, don't try Jupiter (will fail anyway)
            return {'success': False,
                    'error': f'PumpFun bonding curve buy failed: {pf_result.get("error","")}'}
        # Graduated token — verify Jupiter can sell it before buying
        can_sell, reason = _can_sell_sol_token(contract)
        if not can_sell:
            return {'success': False, 'error': f'Pre-sell check failed: {reason}'}
        print(f"    SOL sell-check: {symbol} ✅ ({reason})")
    # Try to resolve contract from Jupiter token list if missing
    if not contract or len(contract) < 30:
        contract = _resolve_sol_mint(symbol)
    if not contract or len(contract) < 30:
        return {'success': False, 'error': f'No contract for {symbol}'}
    sol_price = _sol_price()
    lamports = int(min(usd / sol_price, MAX_SOL_PER_TRADE) * 1e9)
    # Pre-check: can Jupiter actually route this token?
    quote = None
    for slippage in [SLIPPAGE_BPS, 500, 1000, 3000]:
        try:
            r = requests.get(JUPITER_QUOTE, params={
                'inputMint': WSOL_MINT, 'outputMint': contract,
                'amount': lamports, 'slippageBps': slippage,
                'onlyDirectRoutes': 'false',
                'maxAccounts': '64',
            }, timeout=10)
            if r.status_code == 200:
                q = r.json()
                err = q.get('error','') or q.get('errorCode','')
                if err:
                    print(f"    Jupiter: {err}")
                    if 'liquidity' in str(err).lower() or 'route' in str(err).lower():
                        return {'success': False, 'error': f'No Jupiter route for {symbol} — token illiquid'}
                elif int(q.get('outAmount', 0)) > 0:
                    quote = q
                    break
        except Exception:
            pass
    if not quote:
        return {'success': False, 'error': f'Jupiter quote failed — no route for {symbol}'}
    # Check if PumpFun token has graduated to Raydium
    # Ungraduated tokens fail with 0x1771 on Jupiter — skip buy entirely
    graduated = _is_pumpfun_graduated(contract)
    if graduated is None:
        return {'success': False,
                'error': f'Graduation status unknown for {symbol}; refusing live buy'}
    if graduated is False:
        return {'success': False,
                'error': f'{symbol} still on PumpFun bonding curve — wait for Raydium graduation'}

    impact = float(quote.get('priceImpactPct', 0)) * 100
    if impact > 25:
        # Truly illiquid — skip entirely
        return {'success': False, 'error': f'Impact too high: {impact:.1f}% — token illiquid'}
    if impact > 3:
        # Reduce trade size to bring impact to ~3%
        reduction = 3.0 / impact
        new_lamports = int(lamports * reduction)
        min_lamports = int(0.02 * 1e9)  # minimum $2 trade
        if new_lamports < min_lamports:
            return {'success': False, 'error': f'Impact {impact:.1f}% — trade too small after resize'}
        lamports = new_lamports
        print(f"    Impact {impact:.1f}% — resizing trade to ${lamports/1e9*_sol_price():.1f}")
        quote = _jupiter_quote(WSOL_MINT, contract, lamports)
        if not quote: return {'success': False, 'error': 'Jupiter quote failed after resize'}
        impact = float(quote.get('priceImpactPct', 0)) * 100
    try:
        import base64
        from solders.transaction import VersionedTransaction
        swap = requests.post(JUPITER_SWAP, json={
            'quoteResponse': quote, 'userPublicKey': str(kp.pubkey()),
            'wrapAndUnwrapSol': True, 'useJitoBundle': True, 'jitoTipLamports': 2000,
        }, timeout=15).json()
        tx_b64 = swap.get('swapTransaction', '')
        if not tx_b64: return {'success': False, 'error': 'No swap tx from Jupiter'}
        raw_tx = base64.b64decode(tx_b64)
        tx = VersionedTransaction.from_bytes(raw_tx)
        # solders VersionedTransaction signing
        try:
            # New solders API
            signed_tx = VersionedTransaction(tx.message, [kp])
        except Exception:
            try:
                # Fallback: use keypair to sign message directly
                from solders.keypair import Keypair as _KP
                msg_bytes = bytes(tx.message)
                sig = kp.sign_message(msg_bytes)
                tx.signatures[0] = sig
                signed_tx = tx
            except Exception as _se:
                return {'success': False, 'error': f'SOL signing failed: {_se}'}
        bundle = _jito_submit(base64.b64encode(bytes(signed_tx)).decode())
        out = int(quote.get('outAmount', 0))
        price = (lamports / 1e9 * sol_price) / max(out, 1)
        sol_url = f"https://solscan.io/tx/{bundle}"
        _tg(f"✅ <b>SOL BUY {symbol}</b>\n"
            f'🔗 <a href="{sol_url}">{bundle[:16]}...</a>')
        return {'success': True, 'tx': bundle, 'price': price,
                'sol_spent': lamports/1e9, 'impact': impact, 'url': sol_url}
    except ImportError:
        return {'success': False, 'error': 'pip install solana solders base58'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def _get_sol_token_balance(wallet_pubkey: str, mint_address: str) -> int:
    """Get actual raw token balance from Solana wallet. Tries multiple RPCs."""
    sol_rpcs = [
        'https://api.mainnet-beta.solana.com',
        'https://solana-mainnet.g.alchemy.com/v2/demo',
        'https://rpc.ankr.com/solana',
    ]
    for rpc in sol_rpcs:
        try:
            r = requests.post(rpc, json={
                'jsonrpc': '2.0', 'id': 1,
                'method': 'getTokenAccountsByOwner',
                'params': [
                    wallet_pubkey,
                    {'mint': mint_address},
                    {'encoding': 'jsonParsed'}
                ]
            }, timeout=8)
            if r.status_code == 200:
                result = r.json()
                if 'error' in result:
                    continue
                accounts = result.get('result', {}).get('value', [])
                if accounts:
                    amount = (accounts[0].get('account', {})
                              .get('data', {}).get('parsed', {})
                              .get('info', {}).get('tokenAmount', {})
                              .get('amount', '0'))
                    bal = int(amount)
                    if bal > 0:
                        print(f"    Token balance: {bal} raw units")
                        return bal
        except Exception as e:
            continue
    print(f"    Token balance: 0 (not found in wallet)")
    return 0


def _get_spl_decimals(mint_address) -> int:
    """Fetch SPL token decimals from Solana RPC. Handles both SPL and Token-2022."""
    for rpc in ['https://api.mainnet-beta.solana.com', 'https://rpc.ankr.com/solana']:
        try:
            r = requests.post(rpc, json={
                'jsonrpc': '2.0', 'id': 1,
                'method': 'getAccountInfo',
                'params': [mint_address, {'encoding': 'jsonParsed'}]
            }, timeout=8)
            if r.status_code == 200:
                info = r.json().get('result', {}).get('value', {})
                decimals = (info.get('data', {}).get('parsed', {})
                           .get('info', {}).get('decimals'))
                if decimals is not None:
                    return int(decimals)
        except Exception:
            continue
    return 6  # default for most SPL tokens


def execute_sol_sell(symbol, contract, token_amount) -> dict:
    if _is_dry_run(): return {'success': False, 'mode': 'dry'}
    kp = _sol_keypair()
    if not kp: return {'success': False, 'error': 'No SOL keypair'}
    if not contract or len(contract) < 30:
        return {'success': False, 'error': f'No contract for {symbol}'}
    # Get actual token balance from wallet — NEVER use sim calculation
    decimals = _get_spl_decimals(contract)
    kp_pub = str(kp.pubkey())
    raw = _get_sol_token_balance(kp_pub, contract)
    if not raw or raw == 0:
        # Double-check with decimals from chain
        decimals = _get_spl_decimals(contract)
        raw = _get_sol_token_balance(kp_pub, contract)
    if not raw or raw == 0:
        return {'success': False,
                'error': f'No {symbol} balance found in wallet — already sold or wrong account'}
    # Safety check: don't send more than we have (causes 0x11 InsufficientFunds)
    # Use 99% of balance to account for fees
    raw = int(raw * 0.999)
    # Try sell routes with increasing slippage
    quote = None
    USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'

    # Try direct SOL route first
    for slippage in [300, 500, 1000, 3000, 5000]:
        try:
            r = requests.get(JUPITER_QUOTE, params={
                'inputMint': contract, 'outputMint': WSOL_MINT,
                'amount': raw, 'slippageBps': slippage,
                'onlyDirectRoutes': 'false',
                'asLegacyTransaction': 'false',
            }, timeout=10)
            if r.status_code == 200:
                q = r.json()
                if q and not q.get('error') and int(q.get('outAmount', 0)) > 0:
                    quote = q
                    break
        except Exception:
            pass

    # Try USDC route if SOL fails
    if not quote:
        for slippage in [1000, 5000]:
            try:
                r = requests.get(JUPITER_QUOTE, params={
                    'inputMint': contract, 'outputMint': USDC_MINT,
                    'amount': raw, 'slippageBps': slippage,
                    'onlyDirectRoutes': 'false',
                }, timeout=10)
                if r.status_code == 200:
                    q = r.json()
                    if q and not q.get('error') and int(q.get('outAmount', 0)) > 0:
                        quote = q
                        break
            except Exception:
                pass

    if not quote:
        return {'success': False, 'error': 'Jupiter: no route found — token may be fully illiquid'}
    try:
        import base64
        from solders.transaction import VersionedTransaction
        swap = requests.post(JUPITER_SWAP, json={
            'quoteResponse': quote, 'userPublicKey': str(kp.pubkey()),
            'wrapAndUnwrapSol': True, 'useJitoBundle': True, 'jitoTipLamports': 5000,
            'dynamicSlippage': True,  # let Jupiter optimize slippage
        }, timeout=15).json()
        tx_b64 = swap.get('swapTransaction', '')
        if not tx_b64: return {'success': False, 'error': 'No swap tx'}
        raw_tx = base64.b64decode(tx_b64)
        tx = VersionedTransaction.from_bytes(raw_tx)
        try:
            signed_tx = VersionedTransaction(tx.message, [kp])
        except Exception:
            try:
                msg_bytes = bytes(tx.message)
                sig = kp.sign_message(msg_bytes)
                tx.signatures[0] = sig
                signed_tx = tx
            except Exception as _se:
                return {'success': False, 'error': f'SOL signing failed: {_se}'}
        bundle = _jito_submit(base64.b64encode(bytes(signed_tx)).decode())
        sol_out = int(quote.get('outAmount', 0)) / 1e9
        sol_url = f"https://solscan.io/tx/{bundle}"
        _tg(f"✅ <b>SOL SELL {symbol}</b>\n"
            f"💵 {sol_out:.4f} SOL received\n"
            f'🔗 <a href="{sol_url}">{bundle[:16]}...</a>')
        return {'success': True, 'tx': bundle,
                'sol_received': sol_out, 'usd_received': sol_out * _sol_price(), 'url': sol_url}
    except Exception as e:
        err = str(e)
        # PumpFun bonding curve error — token not graduated, use PumpFun direct sell
        if '6001' in err or '0x1771' in err or 'NotEnoughLiquidity' in err.lower():
            print(f"    Jupiter failed (PumpFun token) — trying PumpFun direct sell")
            pf_result = _pumpfun_sell(kp, contract, raw)
            if pf_result.get('success'):
                tx = pf_result['tx']
                sol_url = f"https://solscan.io/tx/{tx}"
                _tg(f"✅ <b>SOL SELL {symbol}</b> (PumpFun direct)\n"
                    f'🔗 <a href="{sol_url}">{tx[:16]}...</a>')
                return pf_result
            return {'success': False,
                    'error': f'PumpFun sell failed: {pf_result.get("error","")}'}
        # InvalidTimestamp (0x1786) — Whirlpool pool clock issue, retry once
        if '0x1786' in err or '6022' in err or 'InvalidTimestamp' in err:
            print(f"    Whirlpool timestamp error — retrying with fresh quote + skipPrecheck")
            try:
                import time as _t; _t.sleep(2)
                fresh_q = requests.get(JUPITER_QUOTE, params={
                    'inputMint': contract, 'outputMint': WSOL_MINT,
                    'amount': raw, 'slippageBps': '5000',
                    'onlyDirectRoutes': 'false',
                }, timeout=10).json()
                if fresh_q and int(fresh_q.get('outAmount', 0)) > 0:
                    swap_r = requests.post(JUPITER_SWAP, json={
                        'quoteResponse': fresh_q,
                        'userPublicKey': str(kp.pubkey()),
                        'wrapAndUnwrapSol': True,
                        'useJitoBundle': False,   # skip Jito on retry
                        'skipUserAccountsRpcCalls': True,
                        'dynamicSlippage': True,
                    }, timeout=15).json()
                    tx_b64_r = swap_r.get('swapTransaction', '')
                    if tx_b64_r:
                        tx_r = VersionedTransaction.from_bytes(base64.b64decode(tx_b64_r))
                        try:
                            signed_r = VersionedTransaction(tx_r.message, [kp])
                        except Exception:
                            signed_r = tx_r
                        bundle_r = _jito_submit(base64.b64encode(bytes(signed_r)).decode())
                        sol_out_r = int(fresh_q.get('outAmount', 0)) / 1e9
                        return {'success': True, 'tx': bundle_r,
                                'sol_received': sol_out_r,
                                'usd_received': sol_out_r * _sol_price()}
            except Exception as _te:
                return {'success': False, 'error': f'Timestamp retry failed: {_te}'}
        # Slippage error — quote is stale, retry with fresh quote
        if '6025' in err or '6024' in err or 'slippage' in err.lower():
            print(f"    Slippage error on sell — retrying with fresh quote")
            try:
                fresh_quote = _jupiter_quote_slippage(contract, WSOL_MINT, raw, 5000)
                if fresh_quote:
                    swap2 = requests.post(JUPITER_SWAP, json={
                        'quoteResponse': fresh_quote,
                        'userPublicKey': str(kp.pubkey()),
                        'wrapAndUnwrapSol': True,
                        'useJitoBundle': True,
                        'jitoTipLamports': 10000,
                        'dynamicSlippage': True,
                    }, timeout=15).json()
                    tx_b64_2 = swap2.get('swapTransaction', '')
                    if tx_b64_2:
                        tx2 = VersionedTransaction.from_bytes(base64.b64decode(tx_b64_2))
                        try:
                            signed2 = VersionedTransaction(tx2.message, [kp])
                        except Exception:
                            signed2 = tx2
                        bundle2 = _jito_submit(base64.b64encode(bytes(signed2)).decode())
                        sol_out2 = int(fresh_quote.get('outAmount', 0)) / 1e9
                        return {'success': True, 'tx': bundle2,
                                'sol_received': sol_out2,
                                'usd_received': sol_out2 * _sol_price()}
            except Exception as e2:
                return {'success': False, 'error': f'Retry failed: {e2}'}
        return {'success': False, 'error': err}


# ── EVM: Uniswap v3 (BASE + ETH, no API key) ─────────────────────────────────
# Uniswap v3 SwapRouter ABI — only the functions we need
UNISWAP_ABI = [
    {
        "inputs": [{
            "components": [
                {"name": "tokenIn",           "type": "address"},
                {"name": "tokenOut",          "type": "address"},
                {"name": "fee",               "type": "uint24"},
                {"name": "recipient",         "type": "address"},
                {"name": "deadline",          "type": "uint256"},
                {"name": "amountIn",          "type": "uint256"},
                {"name": "amountOutMinimum",  "type": "uint256"},
                {"name": "sqrtPriceLimitX96", "type": "uint160"},
            ],
            "name": "params", "type": "tuple"
        }],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [{
            "components": [
                {"name": "tokenIn",          "type": "address"},
                {"name": "tokenOut",         "type": "address"},
                {"name": "fee",              "type": "uint24"},
                {"name": "recipient",        "type": "address"},
                {"name": "deadline",         "type": "uint256"},
                {"name": "amountIn",         "type": "uint256"},
                {"name": "amountOutMinimum", "type": "uint256"},
            ],
            "name": "params", "type": "tuple"
        }],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

ERC20_ABI = [
    {"inputs": [{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],
     "name": "approve", "outputs": [{"name":"","type":"bool"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name":"account","type":"address"}],
     "name": "balanceOf", "outputs": [{"name":"","type":"uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [],
     "name": "decimals", "outputs": [{"name":"","type":"uint8"}],
     "stateMutability": "view", "type": "function"},
]

def _w3(chain):
    try:
        from web3 import Web3
        # Try primary RPC first, then fallbacks
        rpcs_to_try = [RPCS[chain]] + RPCS_FALLBACK.get(chain, [])
        for rpc_url in rpcs_to_try:
            try:
                w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 15}))
                if w3.is_connected():
                    return w3
            except Exception:
                continue
        print(f"  All RPCs failed for {chain}")
        return None
    except ImportError:
        print("  executor: pip install web3")
        return None

def _evm_account():
    if not EVM_PRIVATE_KEY: return None, None
    try:
        from web3 import Web3
        acct = Web3().eth.account.from_key(EVM_PRIVATE_KEY)
        return acct, acct.address
    except Exception as e:
        print(f"  EVM account error: {e}")
        return None, None

def _get_decimals(w3, token_address) -> int:
    try:
        c = w3.eth.contract(
            address=w3.to_checksum_address(token_address), abi=ERC20_ABI)
        return c.functions.decimals().call()
    except Exception:
        return 18

def _approve_token(w3, chain, token_address, amount_wei, acct, addr,
                   router_override=None, trade_usd=0):
    """Approve router to spend token. Uses router_override or chain primary router."""
    try:
        primary_router = router_override or DEX_ROUTERS.get(chain, [{}])[0].get('router', UNISWAP_ROUTER)
        token = w3.eth.contract(
            address=w3.to_checksum_address(token_address), abi=ERC20_ABI)
        # Check existing allowance — skip tx if already approved
        try:
            allowance = token.functions.allowance(
                addr, w3.to_checksum_address(primary_router)).call()
            if allowance > amount_wei:
                return True
        except Exception:
            pass
        MAX_UINT256 = 2**256 - 1
        max_fee, priority_fee = _gas_params(w3)
        ok, gas_err = _guard_evm_gas(trade_usd, 60000, max_fee, 'approve')
        if not ok:
            print(f"    approve skipped: {gas_err}")
            return False
        tx = token.functions.approve(
            w3.to_checksum_address(primary_router), MAX_UINT256
        ).build_transaction({
            'from': addr,
            'nonce': w3.eth.get_transaction_count(addr, 'pending'),
            'gas': 60000,
            'maxFeePerGas': max_fee,
            'maxPriorityFeePerGas': priority_fee,
            'chainId': CHAIN_IDS[chain],
            'type': 2,
        })
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return True
    except Exception as e:
        print(f"    approve error: {e}")
        return False

def _gas_params(w3):
    """EIP-1559 gas params."""
    fee_history = w3.eth.fee_history(1, 'latest', [50])
    base_fee = fee_history['baseFeePerGas'][-1]
    priority_fee = w3.to_wei(2, 'gwei')
    return base_fee * 2 + priority_fee, priority_fee


def _min_out(amount_out: int) -> int:
    """Apply configured EVM slippage to a quoted output amount."""
    if amount_out <= 0:
        return 0
    return int(amount_out * max(0, 10_000 - EVM_SLIPPAGE_BPS) / 10_000)


def _quote_v2_min_out(router, amount_in: int, path: list) -> int:
    try:
        amounts = router.functions.getAmountsOut(amount_in, path).call()
        return _min_out(int(amounts[-1])) if amounts else 0
    except Exception:
        return 0


def _quote_aero_min_out(router, amount_in: int, routes: list) -> int:
    try:
        amounts = router.functions.getAmountsOut(amount_in, routes).call()
        return _min_out(int(amounts[-1])) if amounts else 0
    except Exception:
        return 0


QUOTER_ABI = [{
    "inputs": [
        {"name": "tokenIn", "type": "address"},
        {"name": "tokenOut", "type": "address"},
        {"name": "fee", "type": "uint24"},
        {"name": "amountIn", "type": "uint256"},
        {"name": "sqrtPriceLimitX96", "type": "uint160"},
    ],
    "name": "quoteExactInputSingle",
    "outputs": [{"name": "amountOut", "type": "uint256"}],
    "stateMutability": "nonpayable",
    "type": "function",
}]


def _quote_v3_min_out(w3, chain: str, token_in: str, token_out: str,
                      fee: int, amount_in: int) -> int:
    try:
        quoter_addr = UNISWAP_QUOTERS.get(chain, UNISWAP_QUOTER)
        quoter = w3.eth.contract(address=w3.to_checksum_address(quoter_addr),
                                 abi=QUOTER_ABI)
        out = quoter.functions.quoteExactInputSingle(
            token_in, token_out, fee, amount_in, 0).call()
        return _min_out(int(out))
    except Exception:
        return 0


def _gas_cost_usd(gas_units: int, max_fee_per_gas: int) -> float:
    return (gas_units * max_fee_per_gas / 1e18) * _eth_price()


def _guard_evm_gas(trade_usd: float, gas_units: int, max_fee_per_gas: int,
                   label: str) -> tuple:
    """
    Refuse transactions where worst-case gas is too large for the trade.
    This prevents tiny $1-$2 trades from paying irrational mainnet fees.
    """
    trade_usd = float(trade_usd or 0)
    gas_usd = _gas_cost_usd(gas_units, max_fee_per_gas)
    pct_limit_usd = (trade_usd * MAX_GAS_COST_PCT / 100) if trade_usd > 0 else 0
    limit_usd = min(MAX_GAS_COST_USD, pct_limit_usd) if pct_limit_usd else MAX_GAS_COST_USD
    if gas_usd > limit_usd:
        return False, (f'{label}: gas ${gas_usd:.2f} exceeds limit '
                       f'${limit_usd:.2f} ({MAX_GAS_COST_PCT:.0f}%/${MAX_GAS_COST_USD:.2f})')
    return True, ''


def _try_aerodrome_buy(w3, chain, acct, addr, router_addr, token_out,
                       eth_amount_wei, trade_usd=0) -> dict:
    """Buy via Aerodrome router on Base — uses routes struct instead of path array."""
    weth = w3.to_checksum_address(WETH[chain])
    token = w3.to_checksum_address(token_out)
    router = w3.eth.contract(address=w3.to_checksum_address(router_addr), abi=AERODROME_ABI)
    max_fee, priority_fee = _gas_params(w3)
    deadline = int(time.time()) + 300
    # Try volatile pool first (stable=False), then stable pool
    for stable in [False, True]:
        try:
            routes = [{'from': weth, 'to': token, 'stable': stable, 'factory': AERODROME_FACTORY}]
            amount_out_min = _quote_aero_min_out(router, eth_amount_wei, routes)
            if amount_out_min <= 0:
                continue
            ok, gas_err = _guard_evm_gas(trade_usd, 300000, max_fee, 'Aerodrome buy')
            if not ok:
                return {'success': False, 'error': gas_err}
            tx = router.functions.swapExactETHForTokens(
                amount_out_min, routes, addr, deadline
            ).build_transaction({
                'from': addr, 'value': eth_amount_wei, 'gas': 300000,
                'maxFeePerGas': max_fee, 'maxPriorityFeePerGas': priority_fee,
                'nonce': w3.eth.get_transaction_count(addr, 'pending'),
                'chainId': CHAIN_IDS[chain], 'type': 2,
            })
            try:  # eth_call dry-run
                w3.eth.call({'to': tx['to'], 'from': addr,
                             'value': eth_amount_wei, 'data': tx['data'], 'gas': 300000})
            except Exception:
                continue
            signed = acct.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt['status'] == 1:
                return {'success': True, 'tx': tx_hash.hex(), 'gas_used': receipt['gasUsed']}
        except Exception as e:
            continue
    return {'success': False, 'error': 'Aerodrome: both stable/volatile pools failed'}


def _try_v2_sell(w3, chain, acct, addr, router_addr, token_in, amount_wei,
                 trade_usd=0) -> dict:
    """Sell via Uniswap v2-style router.
    Order: approve → dry-run → send.
    Approve must come BEFORE dry-run: eth_call simulates transferFrom which
    needs allowance set or it always reverts (false negative).
    _approve_token has its own allowance check — skips if already approved."""
    weth = w3.to_checksum_address(WETH[chain])
    token = w3.to_checksum_address(token_in)
    router = w3.eth.contract(address=w3.to_checksum_address(router_addr),
                             abi=UNISWAP_V2_ABI)
    approved = _approve_token(w3, chain, token_in, amount_wei, acct, addr,
                              router_override=router_addr, trade_usd=trade_usd)
    if not approved:
        return {'success': False, 'error': 'approve failed'}
    max_fee, priority_fee = _gas_params(w3)
    deadline = int(time.time()) + 300
    try:
        path = [token, weth]
        amount_out_min = _quote_v2_min_out(router, amount_wei, path)
        if amount_out_min <= 0:
            return {'success': False, 'error': 'v2 sell quote failed'}
        ok, gas_err = _guard_evm_gas(trade_usd, 200000, max_fee, 'v2 sell')
        if not ok:
            return {'success': False, 'error': gas_err}
        try:
            swap_fn = router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                amount_wei, amount_out_min, path, addr, deadline)
        except Exception:
            swap_fn = router.functions.swapExactTokensForETH(
                amount_wei, amount_out_min, path, addr, deadline)
        tx = swap_fn.build_transaction({
            'from': addr, 'value': 0, 'gas': 200000,
            'maxFeePerGas': max_fee, 'maxPriorityFeePerGas': priority_fee,
            'nonce': w3.eth.get_transaction_count(addr, 'pending'),
            'chainId': CHAIN_IDS[chain], 'type': 2,
        })
        try:
            w3.eth.call({'to': tx['to'], 'from': addr,
                         'value': 0, 'data': tx['data'], 'gas': 200000})
        except Exception as _dry:
            return {'success': False, 'error': f'dry-run: {str(_dry)[:60]}'}
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt['status'] == 1:
            return {'success': True, 'tx': tx_hash.hex(), 'dex': 'v2'}
        return {'success': False, 'error': 'v2 sell reverted'}
    except Exception as e:
        return {'success': False, 'error': str(e)[:80]}


def _try_aerodrome_sell(w3, chain, acct, addr, router_addr, token_in, amount_wei,
                        trade_usd=0) -> dict:
    """Sell via Aerodrome router on Base.
    Order: approve → dry-run → send (same reason as _try_v2_sell)."""
    weth = w3.to_checksum_address(WETH[chain])
    token = w3.to_checksum_address(token_in)
    router = w3.eth.contract(address=w3.to_checksum_address(router_addr), abi=AERODROME_ABI)
    approved = _approve_token(w3, chain, token_in, amount_wei, acct, addr,
                              router_override=router_addr, trade_usd=trade_usd)
    if not approved:
        return {'success': False, 'error': 'Aerodrome approve failed'}
    max_fee, priority_fee = _gas_params(w3)
    deadline = int(time.time()) + 300
    for stable in [False, True]:
        try:
            routes = [{'from': token, 'to': weth, 'stable': stable, 'factory': AERODROME_FACTORY}]
            amount_out_min = _quote_aero_min_out(router, amount_wei, routes)
            if amount_out_min <= 0:
                continue
            ok, gas_err = _guard_evm_gas(trade_usd, 300000, max_fee, 'Aerodrome sell')
            if not ok:
                return {'success': False, 'error': gas_err}
            tx = router.functions.swapExactTokensForETH(
                amount_wei, amount_out_min, routes, addr, deadline
            ).build_transaction({
                'from': addr, 'value': 0, 'gas': 300000,
                'maxFeePerGas': max_fee, 'maxPriorityFeePerGas': priority_fee,
                'nonce': w3.eth.get_transaction_count(addr, 'pending'),
                'chainId': CHAIN_IDS[chain], 'type': 2,
            })
            try:
                w3.eth.call({'to': tx['to'], 'from': addr,
                             'value': 0, 'data': tx['data'], 'gas': 300000})
            except Exception:
                continue
            signed = acct.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt['status'] == 1:
                return {'success': True, 'tx': tx_hash.hex(), 'dex': 'Aerodrome'}
        except Exception:
            continue
    return {'success': False, 'error': 'Aerodrome sell failed (both pools)'}


def _try_v2_buy(w3, chain, acct, addr, router_addr, token_out, eth_amount_wei,
                trade_usd=0) -> dict:
    """Buy via Uniswap v2 style router (also works for SushiSwap, PancakeSwap, BaseSwap)."""
    weth = w3.to_checksum_address(WETH[chain])
    token = w3.to_checksum_address(token_out)
    router = w3.eth.contract(address=w3.to_checksum_address(router_addr), abi=UNISWAP_V2_ABI)
    max_fee, priority_fee = _gas_params(w3)
    deadline = int(time.time()) + 300
    try:
        path = [weth, token]
        amount_out_min = _quote_v2_min_out(router, eth_amount_wei, path)
        if amount_out_min <= 0:
            return {'success': False, 'error': 'v2 buy quote failed'}
        ok, gas_err = _guard_evm_gas(trade_usd, 200000, max_fee, 'v2 buy')
        if not ok:
            return {'success': False, 'error': gas_err}
        # Try fee-on-transfer version first (works for tax tokens), then standard
        try:
            swap_fn = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                amount_out_min, path, addr, deadline)
        except Exception:
            swap_fn = router.functions.swapExactETHForTokens(
                amount_out_min, path, addr, deadline)
        tx = swap_fn.build_transaction({
            'from': addr, 'value': eth_amount_wei, 'gas': 200000,
            'maxFeePerGas': max_fee, 'maxPriorityFeePerGas': priority_fee,
            'nonce': w3.eth.get_transaction_count(addr, 'pending'),
            'chainId': CHAIN_IDS[chain], 'type': 2,
        })
        # eth_call dry-run — if it reverts here, zero ETH spent
        try:
            w3.eth.call({'to': tx['to'], 'from': addr,
                         'value': eth_amount_wei, 'data': tx['data'], 'gas': 200000})
        except Exception as _dry:
            return {'success': False, 'error': f'dry-run revert: {str(_dry)[:80]}'}
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt['status'] == 1:
            return {'success': True, 'tx': tx_hash.hex(), 'gas_used': receipt['gasUsed']}
        return {'success': False, 'error': 'V2 TX reverted'}
    except Exception as e:
        return {'success': False, 'error': str(e)}





# DexScreener dexId → router name in DEX_ROUTERS
_DEXID_TO_ROUTER = {
    'uniswap_v3': 'Uniswap v3', 'uniswap_v2': 'Uniswap v2',
    'uniswap':    'Uniswap v3',
    'sushiswap':  'SushiSwap v2', 'sushiswap_v3': 'SushiSwap v3',
    'pancakeswap_v3': 'PancakeSwap v3', 'pancakeswap': 'PancakeSwap v2',
    'aerodrome':  'Aerodrome v2', 'aerodrome_v2': 'Aerodrome v2',
    'baseswap':   'Uniswap v2',
}

def _resolve_dex_router(chain, dex_id):
    """Map DexScreener dexId to the matching router in DEX_ROUTERS — one router only."""
    if not dex_id:
        return None
    key = dex_id.lower().replace('-', '_').replace(' ', '_')
    router_name = _DEXID_TO_ROUTER.get(key)
    if not router_name:
        # prefix match
        for k, v in _DEXID_TO_ROUTER.items():
            if key.startswith(k) or k.startswith(key):
                router_name = v
                break
    if not router_name:
        return None
    for r in DEX_ROUTERS.get(chain, []):
        if r['name'] == router_name:
            return r
    return None


def _uniswap_buy(w3, chain, acct, addr, token_out, eth_amount_wei,
                 target_router=None) -> dict:
    """Buy token with ETH.
    If target_router is set (from DexScreener), tries ONLY that router.
    Falls back to full scan when unknown or failed.
    For BASE: DEX_ROUTERS[0] is Aerodrome — handled correctly as 'aero' type."""
    weth = w3.to_checksum_address(WETH[chain])
    token = w3.to_checksum_address(token_out)
    max_fee, priority_fee = _gas_params(w3)
    deadline = int(time.time()) + 300
    errors = []
    trade_usd = (eth_amount_wei / 1e18) * _eth_price()

    def _try_v3_buy(router_addr, name, mf=None, pf=None):
        mf = mf or max_fee
        pf = pf or priority_fee
        r = w3.eth.contract(address=w3.to_checksum_address(router_addr), abi=UNISWAP_ABI)
        for fee in POOL_FEES:
            try:
                amount_out_min = _quote_v3_min_out(w3, chain, weth, token, fee, eth_amount_wei)
                if amount_out_min <= 0:
                    errors.append(f"{name} fee={fee}: quote failed")
                    continue
                ok, gas_err = _guard_evm_gas(trade_usd, 250000, mf, f'{name} buy')
                if not ok:
                    return {'success': False, 'error': gas_err}
                tx = r.functions.exactInputSingle({
                    'tokenIn': weth, 'tokenOut': token, 'fee': fee,
                    'recipient': addr, 'deadline': deadline,
                    'amountIn': eth_amount_wei, 'amountOutMinimum': amount_out_min, 'sqrtPriceLimitX96': 0,
                }).build_transaction({
                    'from': addr, 'value': eth_amount_wei, 'gas': 250000,
                    'maxFeePerGas': mf, 'maxPriorityFeePerGas': pf,
                    'nonce': w3.eth.get_transaction_count(addr, 'pending'),
                    'chainId': CHAIN_IDS[chain], 'type': 2,
                })
                try:
                    w3.eth.call({'to': tx['to'], 'from': addr,
                                 'value': eth_amount_wei, 'data': tx['data'], 'gas': 300000})
                except Exception as _dry:
                    errors.append(f"{name} fee={fee}: {str(_dry)[:50]}")
                    continue
                signed = acct.sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                if receipt['status'] == 1:
                    return {'success': True, 'tx': tx_hash.hex(),
                            'dex': f'{name} ({fee/10000:.2f}%)', 'gas_used': receipt['gasUsed']}
                errors.append(f"{name} fee={fee} reverted")
            except Exception as e:
                errors.append(f"{name} fee={fee}: {str(e)[:50]}")
        return {'success': False, 'error': ' | '.join(errors[-2:])}

    # ── Single router (DexScreener identified the pool) ────────────
    if target_router:
        dex_type = target_router.get('type', 'v2')
        router_addr = target_router['router']
        dex_name = target_router['name']
        if dex_type == 'aero':
            result = _try_aerodrome_buy(w3, chain, acct, addr, router_addr, token_out,
                                        eth_amount_wei, trade_usd=trade_usd)
        elif dex_type == 'v3':
            result = _try_v3_buy(router_addr, dex_name)
        else:
            result = _try_v2_buy(w3, chain, acct, addr, router_addr, token_out,
                                 eth_amount_wei, trade_usd=trade_usd)
        if result.get('success'):
            result.setdefault('dex', dex_name)
            return result
        # Target failed — fall through to full scan

    # ── Full scan — try all routers in DEX_ROUTERS order ──────────
    # Correct order: respect each router's type (aero vs v3 vs v2)
    tried = set()
    for dex in DEX_ROUTERS.get(chain, []):
        if dex['router'] in tried:
            continue
        tried.add(dex['router'])
        dex_type = dex.get('type', 'v2')
        dex_name = dex['name']
        try:
            if dex_type == 'aero':
                result = _try_aerodrome_buy(w3, chain, acct, addr, dex['router'], token_out,
                                            eth_amount_wei, trade_usd=trade_usd)
            elif dex_type == 'v3':
                result = _try_v3_buy(dex['router'], dex_name)
            else:
                result = _try_v2_buy(w3, chain, acct, addr, dex['router'], token_out,
                                     eth_amount_wei, trade_usd=trade_usd)
            if result.get('success'):
                result.setdefault('dex', dex_name)
                return result
            errors.append(f"{dex_name}: {result.get('error','')[:60]}")
        except Exception as e:
            errors.append(f"{dex_name}: {str(e)[:60]}")

    return {'success': False, 'error': ' | '.join(errors[-4:])}

def _uniswap_sell(w3, chain, acct, addr, token_in, amount_wei,
                  target_router=None, trade_usd=0) -> dict:
    """Sell token for ETH/native.
    If target_router is set (from DexScreener), approves and tries ONLY that router.
    Falls back to full scan when unknown or failed.
    Order in all paths: approve → dry-run → send.
    (approve must precede dry-run: transferFrom needs allowance to simulate correctly)"""
    weth = w3.to_checksum_address(WETH[chain])
    token = w3.to_checksum_address(token_in)
    max_fee, priority_fee = _gas_params(w3)
    deadline = int(time.time()) + 300
    errors = []
    approved_routers = set()

    def _approve_once(router_addr):
        if router_addr not in approved_routers:
            ok = _approve_token(w3, chain, token_in, amount_wei, acct, addr,
                                router_override=router_addr, trade_usd=trade_usd)
            if ok:
                approved_routers.add(router_addr)
            return ok
        return True

    def _try_v3(router_addr, name):
        if not _approve_once(router_addr):
            return {'success': False, 'error': f'{name} approve failed'}
        r = w3.eth.contract(address=w3.to_checksum_address(router_addr), abi=UNISWAP_ABI)
        for pool_fee in POOL_FEES:
            try:
                amount_out_min = _quote_v3_min_out(w3, chain, token, weth, pool_fee, amount_wei)
                if amount_out_min <= 0:
                    errors.append(f"{name} fee={pool_fee}: quote failed")
                    continue
                ok, gas_err = _guard_evm_gas(trade_usd, 250000, max_fee, f'{name} sell')
                if not ok:
                    return {'success': False, 'error': gas_err}
                tx = r.functions.exactInputSingle({
                    'tokenIn': token, 'tokenOut': weth, 'fee': pool_fee,
                    'recipient': addr, 'deadline': deadline,
                    'amountIn': amount_wei, 'amountOutMinimum': amount_out_min, 'sqrtPriceLimitX96': 0,
                }).build_transaction({
                    'from': addr, 'value': 0, 'gas': 250000,
                    'maxFeePerGas': max_fee, 'maxPriorityFeePerGas': priority_fee,
                    'nonce': w3.eth.get_transaction_count(addr, 'pending'),
                    'chainId': CHAIN_IDS[chain], 'type': 2,
                })
                try:
                    w3.eth.call({'to': tx['to'], 'from': addr,
                                 'value': 0, 'data': tx['data'], 'gas': 250000})
                except Exception as _dry:
                    errors.append(f"{name} fee={pool_fee} dry-run: {str(_dry)[:50]}")
                    continue
                signed = acct.sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                if receipt['status'] == 1:
                    return {'success': True, 'tx': tx_hash.hex(),
                            'fee_tier': pool_fee, 'dex': name, 'gas_used': receipt['gasUsed']}
                errors.append(f"{name} fee={pool_fee} reverted")
            except Exception as e:
                errors.append(f"{name} fee={pool_fee}: {str(e)[:50]}")
        return {'success': False, 'error': ' | '.join(errors[-2:])}

    # ── Single router path (DexScreener told us exactly which DEX) ─
    if target_router:
        dex_type = target_router.get('type', 'v2')
        router_addr = target_router['router']
        dex_name = target_router['name']
        if dex_type == 'v3':
            result = _try_v3(router_addr, dex_name)
        elif dex_type == 'aero':
            result = _try_aerodrome_sell(w3, chain, acct, addr, router_addr, token_in,
                                         amount_wei, trade_usd=trade_usd)
        else:
            result = _try_v2_sell(w3, chain, acct, addr, router_addr, token_in,
                                  amount_wei, trade_usd=trade_usd)
        if result.get('success'):
            return result
        # Target failed — fall through silently to full scan

    # ── Full scan: v3 first ────────────────────────────────────────
    # For BASE: DEX_ROUTERS[0] is Aerodrome (aero type) — check type before v3 call
    primary = DEX_ROUTERS.get(chain, [{}])[0]
    if primary.get('type') == 'v3':
        result = _try_v3(primary['router'], primary.get('name', 'Uniswap v3'))
        if result.get('success'):
            return result

    # ── Aerodrome (BASE primary) ───────────────────────────────────
    for dex in DEX_ROUTERS.get(chain, []):
        if dex.get('type') != 'aero':
            continue
        result = _try_aerodrome_sell(w3, chain, acct, addr, dex['router'], token_in,
                                     amount_wei, trade_usd=trade_usd)
        if result.get('success'):
            return result
        errors.append(f"aerodrome: {result.get('error','')[:50]}")

    # ── v2 fallbacks ───────────────────────────────────────────────
    for dex in DEX_ROUTERS.get(chain, []):
        if dex.get('type') != 'v2':
            continue
        result = _try_v2_sell(w3, chain, acct, addr, dex['router'], token_in,
                              amount_wei, trade_usd=trade_usd)
        if result.get('success'):
            result['dex'] = dex['name']
            return result
        errors.append(f"{dex['name']}: {result.get('error','')[:50]}")

    return {'success': False, 'error': ' | '.join(errors[-4:])}

def _get_token_dex_info(contract, chain) -> dict:
    """Get DEX info for a token via DexScreener. Returns dex name, pair address etc."""
    try:
        chain_map = {'ethereum': 'ethereum', 'base': 'base', 'solana': 'solana', 'bsc': 'bsc'}
        chain_id = chain_map.get(chain, chain)
        r = requests.get(
            f'https://api.dexscreener.com/latest/dex/tokens/{contract}',
            timeout=8)
        if r.status_code == 200:
            pairs = r.json().get('pairs', [])
            for p in pairs:
                if p.get('chainId', '').lower() == chain_id:
                    return {
                        'dex': p.get('dexId', 'unknown'),
                        'pair': p.get('pairAddress', ''),
                        'liq': p.get('liquidity', {}).get('usd', 0),
                    }
    except Exception:
        pass
    return {}


def _check_uniswap_pool(w3, chain, token_address) -> str:
    """Check if token has a Uniswap v3 pool. Returns fee tier or '' if none."""
    try:
        # Uniswap v3 Factory
        factory_addr = {
            'ethereum': '0x1F98431c8aD98523631AE4a59f267346ea31F984',
            'base':     '0x33128a8fC17869897dcE68Ed026d694621f6FDfD',
        }.get(chain, '')
        if not factory_addr:
            return '3000'  # assume pool exists for other chains
        factory_abi = [{"inputs":[{"name":"tokenA","type":"address"},
                                   {"name":"tokenB","type":"address"},
                                   {"name":"fee","type":"uint24"}],
                        "name":"getPool","outputs":[{"name":"","type":"address"}],
                        "stateMutability":"view","type":"function"}]
        factory = w3.eth.contract(
            address=w3.to_checksum_address(factory_addr), abi=factory_abi)
        weth = w3.to_checksum_address(WETH[chain])
        token = w3.to_checksum_address(token_address)
        for fee in [3000, 10000, 500]:
            try:
                pool = factory.functions.getPool(weth, token, fee).call()
                if pool and pool != '0x' + '0'*40:
                    return str(fee)
            except Exception:
                continue
        return ''  # no pool found
    except Exception:
        return '3000'  # optimistic fallback


def execute_evm_buy(symbol, chain, contract, usd) -> dict:
    if _is_dry_run(): return {'success': False, 'mode': 'dry'}
    if chain not in CHAIN_IDS or chain not in WETH:
        return {'success': False, 'error': f'Chain {chain} not supported for EVM buy'}
    acct, addr = _evm_account()
    if not addr: return {'success': False, 'error': 'No EVM wallet configured'}
    if not contract or len(contract) < 30:
        return {'success': False, 'error': f'No contract for {symbol}'}

    eth_price = _eth_price()
    eth_amount = min(usd / eth_price, MAX_ETH_PER_TRADE)
    eth_amount_wei = int(eth_amount * 1e18)

    # DexScreener pre-check: find real pool and pick ONE router
    dex_info = _get_token_dex_info(contract, chain)
    if dex_info:
        liq = float(dex_info.get('liq', 0) or 0)
        dex_name = dex_info.get('dex', '?')
        print(f"    {symbol} DEX: {dex_name} liq:${liq/1000:.0f}k")
        if liq < 5000:
            return {'success': False, 'error': f'{symbol} pool liq too low (${liq:.0f})'}
    else:
        return {'success': False, 'error': f'{symbol} — no pool found on {chain} (DexScreener)'}

    target_router = _resolve_dex_router(chain, dex_info.get('dex', ''))
    if target_router:
        print(f"    {symbol} → {target_router['name']} only")

    w3 = _w3(chain)
    if not w3: return {'success': False, 'error': 'web3 unavailable'}
    bal = w3.eth.get_balance(addr)
    if bal < eth_amount_wei:
        return {'success': False,
                'error': f'Insufficient ETH: have {bal/1e18:.4f}, need {eth_amount:.4f}'}

    try:
        tok = w3.eth.contract(address=w3.to_checksum_address(contract), abi=ERC20_ABI)
        bal_before = tok.functions.balanceOf(addr).call()
    except Exception:
        bal_before = 0

    result = _uniswap_buy(w3, chain, acct, addr, contract, eth_amount_wei,
                          target_router=target_router)
    if result['success']:
        # Verify tokens actually arrived — if not, it's a ghost pool
        try:
            bal_after = tok.functions.balanceOf(addr).call()
            if bal_after <= bal_before:
                return {'success': False,
                        'error': f'Swap succeeded but no tokens received (ghost pool)'}
        except Exception:
            pass
        # Estimate price from amount spent
        decimals = _get_decimals(w3, contract)
        price = (eth_amount * eth_price)  # rough — actual price from logs
        result['price'] = price
        result['eth_spent'] = eth_amount
        dex_used = result.get('dex', 'DEX')
        tx = result['tx']
        explorer = {'ethereum': 'https://etherscan.io/tx/',
                    'base': 'https://basescan.org/tx/'}.get(chain, 'https://basescan.org/tx/')
        _tg(f"✅ <b>BUY {symbol}</b> ({chain})\n"
            f"💵 ${usd:.0f} via {dex_used}\n"
            f'🔗 <a href="{explorer}{tx}">{tx[:16]}...</a>')
    return result

def execute_evm_sell(symbol, chain, contract, token_amount, decimals=18,
                     reference_price_usd=0) -> dict:
    if _is_dry_run(): return {'success': False, 'mode': 'dry'}
    if chain not in CHAIN_IDS or chain not in WETH:
        return {'success': False, 'error': f'Chain {chain} not supported'}
    acct, addr = _evm_account()
    if not addr: return {'success': False, 'error': 'No EVM wallet'}
    if not contract or len(contract) < 30:
        return {'success': False, 'error': f'No contract for {symbol}'}

    w3 = _w3(chain)
    if not w3: return {'success': False, 'error': 'web3 unavailable'}

    # Get actual decimals from chain
    actual_decimals = _get_decimals(w3, contract)
    amount_wei = int(token_amount * (10 ** actual_decimals))

    dex_info_s = _get_token_dex_info(contract, chain)
    target_router_s = _resolve_dex_router(chain, dex_info_s.get('dex', '')) if dex_info_s else None
    if target_router_s:
        print(f"    {symbol} sell → {target_router_s['name']} only")
    estimated_trade_usd = max(0, float(token_amount or 0) * float(reference_price_usd or 0))
    result = _uniswap_sell(w3, chain, acct, addr, contract, amount_wei,
                           target_router=target_router_s,
                           trade_usd=estimated_trade_usd)
    if result['success']:
        # Try to get ETH received from receipt logs (approximate)
        eth_out = 0
        try:
            receipt = w3.eth.get_transaction_receipt(result['tx'])
            # ETH out is hard to read from logs without full ABI decode
            # Use balance diff as fallback
        except Exception:
            pass
        usd_out = eth_out * _eth_price()
        result['usd_received'] = usd_out
        tx = result['tx']
        explorer = {'ethereum': 'https://etherscan.io/tx/',
                    'base': 'https://basescan.org/tx/'}.get(chain, 'https://basescan.org/tx/')
        dex_label = result.get('dex') or (
            f"fee:{result.get('fee_tier', 0)/10000:.2f}%"
            if result.get('fee_tier') else 'DEX')
        _tg(f"✅ <b>SELL {symbol}</b> ({chain})\n"
            f"📋 {dex_label}\n"
            f'🔗 <a href="{explorer}{tx}">{tx[:16]}...</a>')
    return result


# ── Unified interface called by simulation.py ─────────────────────────────────
MAX_TRADE_USD = float(_env('EXECUTOR_MAX_TRADE_USD', '2.0'))  # live hard cap

# BASE/ETH live execution disabled — RPC unreliable, tokens not received
# Set to True only after confirming RPC works end-to-end
EVM_LIVE_ENABLED = True   # Alchemy RPC — reliable
BASE_LIVE_ENABLED = True   # BASE live — Aerodrome first, ghost pool check added

def on_buy(symbol, chain, usd, price, source='', contract='', cash_left=None):
    """Called by simulation on every BUY."""
    real_usd = min(usd, MAX_TRADE_USD)
    if _is_dry_run():
        alert_buy(symbol, chain, real_usd, price, source, dry=True, cash_left=cash_left)
        return {'success': True, 'mode': 'paper', 'price': price}
    if chain == 'solana':
        result = execute_sol_buy(symbol, contract, real_usd)
        if result.get('success'):
            alert_buy(symbol, chain, real_usd, price, source, dry=False, cash_left=cash_left)
        else:
            err = result.get('error', '')
            _expected = any(x in err for x in [
                'no pool', 'liq too low', 'dry-run', 'No route', 'no route',
                'execution reverted', 'no data', 'Impact too high', 'illiquid'])
            if not _expected:
                alert_error(f"BUY {symbol} (solana) FAILED: {err}")
        return result
    elif chain in ('base', 'ethereum'):
        if not EVM_LIVE_ENABLED:
            print(f"    [EVM disabled] {symbol} ({chain}) — paper trade only")
            return {'success': True, 'mode': 'paper', 'price': price}
        if chain == 'base' and not BASE_LIVE_ENABLED:
            print(f"    [BASE disabled] {symbol} — paper trade only")
            return {'success': True, 'mode': 'paper', 'price': price}
        result = execute_evm_buy(symbol, chain, contract, real_usd)
        if result.get('success'):
            alert_buy(symbol, chain, real_usd, price, source, dry=False, cash_left=cash_left)
        else:
            err = result.get('error', '')
            _expected = any(x in err for x in [
                'no pool', 'liq too low', 'dry-run revert', 'dry-run fee',
                'execution reverted', 'no data', 'ghost pool', 'Insufficient ETH',
                'DexScreener'])
            if not _expected:
                alert_error(f"BUY {symbol} ({chain}) FAILED: {err}")
        return result
    return {'success': True, 'mode': 'paper', 'price': price}

def on_sell(symbol, chain, price, pnl_pct, reason,
            token_amount=0, contract='', decimals=18,
            pnl_usd=None, trading_total=None, trading_pct=None):
    """Called by simulation on every SELL."""
    if _is_dry_run():
        alert_sell(symbol, chain, price, pnl_pct, reason, dry=True,
                   pnl_usd=pnl_usd, trading_total=trading_total, trading_pct=trading_pct)
        return {'success': True, 'mode': 'paper'}
    # LIVE: execute first, alert only on success
    if chain == 'solana':
        result = execute_sol_sell(symbol, contract, token_amount)
    elif chain in ('base', 'ethereum'):
        result = execute_evm_sell(symbol, chain, contract, token_amount, decimals,
                                  reference_price_usd=price)
    else:
        return {'success': True, 'mode': 'paper'}
    if result.get('success'):
        alert_sell(symbol, chain, price, pnl_pct, reason, dry=False,
                   pnl_usd=pnl_usd, trading_total=trading_total, trading_pct=trading_pct)
    else:
        err = result.get('error', '')
        _expected = any(x in err for x in [
            'dry-run', 'execution reverted', 'no data', 'No route', 'no route'])
        if not _expected:
            alert_error(f"SELL {symbol} ({chain}) FAILED: {err}")
    return result


# ── Self-test ─────────────────────────────────────────────────────────────────
def test_connection():
    print("\n" + "="*55)
    print(f"  AlphaScope Executor v2.1 — Uniswap v3 + Jupiter")
    print(f"  Mode: {'🔵 DRY RUN (Phase 1)' if _is_dry_run() else '🚀 LIVE (Phase 2)'}")
    print(f"  EVM router: Uniswap v3 (no API key needed)")
    print(f"  SOL router: Jupiter + Jito MEV protection")
    print("="*55)

    # Telegram
    if TELEGRAM_TOKEN and TELEGRAM_CHAT:
        _tg("🤖 AlphaScope executor v2.1 — connection test")
        print("  ✅ Telegram configured")
    else:
        print("  ⚠️  Telegram not configured")

    # Jupiter
    try:
        r = requests.get('https://api.jup.ag/tokens/v1/tagged/verified', timeout=8)
        print(f"  ✅ Jupiter reachable ({r.status_code})")
    except Exception as e:
        print(f"  ❌ Jupiter: {e}")

    # web3 + Uniswap
    try:
        from web3 import Web3
        for chain in ['base', 'ethereum']:
            w3 = Web3(Web3.HTTPProvider(RPCS[chain]))
            connected = w3.is_connected()
            status = '✅' if connected else '❌'
            print(f"  {status} {chain.upper()} RPC {'connected' if connected else 'FAILED'}")
    except ImportError:
        print("  ⚠️  web3 not installed: pip install web3")
    except Exception as e:
        print(f"  ❌ web3: {e}")

    # SOL wallet
    if SOL_PRIVATE_KEY:
        kp = _sol_keypair()
        if kp:
            try:
                r = requests.post('https://api.mainnet-beta.solana.com', json={
                    'jsonrpc':'2.0','id':1,'method':'getBalance',
                    'params':[str(kp.pubkey())]}, timeout=8)
                bal = r.json().get('result',{}).get('value',0)/1e9
                print(f"  ✅ SOL wallet: {str(kp.pubkey())[:16]}... | {bal:.4f} SOL")
            except Exception:
                print(f"  ✅ SOL wallet loaded: {str(kp.pubkey())[:16]}...")
        else:
            print("  ❌ SOL keypair failed to load")
    else:
        print("  ⚠️  SOL_PRIVATE_KEY not set (required for Phase 2 SOL)")

    # EVM wallet
    if EVM_PRIVATE_KEY and EVM_WALLET:
        try:
            from web3 import Web3
            for chain in ['base', 'ethereum']:
                w3 = Web3(Web3.HTTPProvider(RPCS[chain]))
                bal = w3.eth.get_balance(w3.to_checksum_address(EVM_WALLET))
                print(f"  ✅ {chain.upper()} wallet: {EVM_WALLET[:16]}... | {bal/1e18:.4f} ETH")
        except ImportError:
            print("  ⚠️  web3 not installed: pip install web3")
        except Exception as e:
            print(f"  ❌ EVM wallet: {e}")
    else:
        print("  ⚠️  EVM_PRIVATE_KEY / EVM_WALLET_ADDRESS not set (required for Phase 2 BASE/ETH)")

    # Prices
    print(f"  ✅ SOL: ${_sol_price():.2f} | ETH: ${_eth_price():.2f}")
    print("="*55 + "\n")


if __name__ == '__main__':
    test_connection()
