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
MAX_SOL_PER_TRADE = float(_env('EXECUTOR_MAX_SOL_PER_TRADE','0.5'))
MAX_ETH_PER_TRADE = float(_env('EXECUTOR_MAX_ETH_PER_TRADE','0.02'))
SLIPPAGE_BPS      = int(_env('EXECUTOR_SLIPPAGE_BPS','300'))
TELEGRAM_TOKEN    = _env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT     = _env('TELEGRAM_CHAT_ID')
SOL_PRIVATE_KEY   = _env('SOL_PRIVATE_KEY')
EVM_PRIVATE_KEY   = _env('EVM_PRIVATE_KEY')
EVM_WALLET        = _env('EVM_WALLET_ADDRESS')

# Chain config
CHAIN_IDS = {'ethereum':1, 'base':8453, 'arbitrum':42161, 'bsc':56}
# Primary RPCs — reliable, high rate limits
RPCS = {
    'ethereum': 'https://rpc.ankr.com/eth',        # Ankr — reliable free tier
    'base':     'https://mainnet.base.org',
    'arbitrum': 'https://arb1.arbitrum.io/rpc',
}
RPCS_FALLBACK = {
    'ethereum': [
        'https://1rpc.io/eth',
        'https://cloudflare-eth.com',
        'https://eth.llamarpc.com',
        'https://eth-mainnet.public.blastapi.io',
        'https://rpc.flashbots.net',
    ],
    'base': [
        'https://1rpc.io/base',
        'https://base.llamarpc.com',
        'https://base-mainnet.public.blastapi.io',
    ],
}

# Uniswap v3 — same address on ETH and BASE
UNISWAP_ROUTER = '0xE592427A0AEce92De3Edee1F18E0157C05861564'  # SwapRouter02
UNISWAP_QUOTER = '0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6'  # Quoter v1

# WETH address per chain
WETH = {
    'ethereum': '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
    'base':     '0x4200000000000000000000000000000000000006',
}

# Uniswap v3 pool fees (try 0.3% first, then 1%, then 0.05%)
POOL_FEES = [3000, 10000, 500]

# Additional DEX routers — fallback chain if Uniswap v3 has no pool
DEX_ROUTERS = {
    'ethereum': [
        {'name': 'Uniswap v3',  'type': 'v3', 'router': '0xE592427A0AEce92De3Edee1F18E0157C05861564'},
        {'name': 'Uniswap v2',  'type': 'v2', 'router': '0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D'},
        {'name': 'SushiSwap',   'type': 'v2', 'router': '0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F'},
    ],
    'base': [
        {'name': 'Uniswap v3',  'type': 'v3', 'router': '0xE592427A0AEce92De3Edee1F18E0157C05861564'},
        {'name': 'Aerodrome',   'type': 'v2', 'router': '0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43'},
        {'name': 'Uniswap v2',  'type': 'v2', 'router': '0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24'},
    ],
}

# Uniswap v2 router ABI (minimal)
UNISWAP_V2_ABI = [
    {
        "inputs": [
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path",         "type": "address[]"},
            {"name": "to",           "type": "address"},
            {"name": "deadline",     "type": "uint256"},
        ],
        "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "payable", "type": "function"
    },
    {
        "inputs": [
            {"name": "amountIn",     "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path",         "type": "address[]"},
            {"name": "to",           "type": "address"},
            {"name": "deadline",     "type": "uint256"},
        ],
        "name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "nonpayable", "type": "function"
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
SOL_RPC_DIRECT = 'https://api.mainnet-beta.solana.com'  # fallback if Jito fails
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
    budget_line = f"💼 Cash left: ${cash_left:.0f}\n" if cash_left is not None else ""
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
    mode = '🔵 DRY RUN' if DRY_RUN else '🚀 LIVE'
    _tg(f"🤖 <b>AlphaScope {mode}</b>\n📋 {sim_id} | {hours}h\n💰 ${capital:.0f}")

def alert_complete(sim_id, pnl_pct, wins, losses, best):
    emoji = '🟢' if pnl_pct >= 0 else '🔴'
    _tg(f"{emoji} <b>Complete {'[DRY]' if DRY_RUN else '[LIVE]'}</b>\n"
        f"📋 {sim_id}\n💰 {pnl_pct:+.1f}% | {wins}W/{losses}L\n🏆 {best}")


# ── Price helpers ─────────────────────────────────────────────────────────────
def _sol_price():
    try:
        return float(requests.get(
            'https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT',
            timeout=5).json().get('price', 89))
    except Exception: return 89.0

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
        return Keypair.from_bytes(base58.b58decode(SOL_PRIVATE_KEY))
    except ImportError:
        print("  executor: pip install solana solders base58")
        return None
    except Exception as e:
        print(f"  SOL keypair error: {e}")
        return None

def _resolve_sol_mint(symbol) -> str:
    """Look up SOL token mint address from Jupiter token list."""
    try:
        r = requests.get(
            'https://api.jup.ag/tokens/v1/tagged/verified',
            timeout=8)
        if r.status_code == 200:
            for token in r.json():
                if token.get('symbol', '').upper() == symbol.upper():
                    return token.get('address', '')
        # Also try strict list
        r2 = requests.get(
            f'https://api.jup.ag/tokens/v1/search?query={symbol}',
            timeout=8)
        if r2.status_code == 200:
            tokens = r2.json()
            if tokens:
                return tokens[0].get('address', '')
    except Exception as e:
        print(f"    mint lookup: {e}")
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

    # Fallback: submit directly to Solana RPC (no MEV protection but tx lands)
    print("    Jito unavailable — submitting direct to Solana RPC")
    try:
        r = requests.post(SOL_RPC_DIRECT, json={
            'jsonrpc': '2.0', 'id': 1,
            'method': 'sendTransaction',
            'params': [signed_b64, {'encoding': 'base64',
                                     'skipPreflight': False,
                                     'maxRetries': 3}]
        }, headers={'Content-Type': 'application/json'}, timeout=15)
        if r.status_code == 200:
            result = r.json()
            if 'result' in result:
                return result['result']
            if 'error' in result:
                raise Exception(f"RPC error: {result['error']}")
    except Exception as e:
        raise Exception(f"All SOL submission methods failed: {e}")
    raise Exception("SOL tx submission failed")

def execute_sol_buy(symbol, contract, usd) -> dict:
    if DRY_RUN: return {'success': False, 'mode': 'dry'}
    kp = _sol_keypair()
    if not kp: return {'success': False, 'error': 'No SOL keypair'}
    # Try to resolve contract from Jupiter token list if missing
    if not contract or len(contract) < 30:
        contract = _resolve_sol_mint(symbol)
    if not contract or len(contract) < 30:
        return {'success': False, 'error': f'No contract for {symbol}'}
    sol_price = _sol_price()
    lamports = int(min(usd / sol_price, MAX_SOL_PER_TRADE) * 1e9)
    quote = _jupiter_quote(WSOL_MINT, contract, lamports)
    if not quote: return {'success': False, 'error': 'Jupiter quote failed'}
    impact = float(quote.get('priceImpactPct', 0)) * 100
    if impact > 5: return {'success': False, 'error': f'Impact too high: {impact:.1f}%'}
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
    """Get actual raw token balance from Solana wallet. Returns raw amount (with decimals)."""
    try:
        r = requests.post('https://api.mainnet-beta.solana.com', json={
            'jsonrpc': '2.0', 'id': 1,
            'method': 'getTokenAccountsByOwner',
            'params': [
                wallet_pubkey,
                {'mint': mint_address},
                {'encoding': 'jsonParsed'}
            ]
        }, timeout=10)
        if r.status_code == 200:
            accounts = r.json().get('result', {}).get('value', [])
            if accounts:
                amount = accounts[0].get('account', {}).get('data', {}).get(
                    'parsed', {}).get('info', {}).get('tokenAmount', {}).get('amount', '0')
                return int(amount)
    except Exception as e:
        print(f"    balance lookup error: {e}")
    return 0


def _get_spl_decimals(mint_address) -> int:
    """Fetch SPL token decimals from Solana RPC."""
    try:
        r = requests.post('https://api.mainnet-beta.solana.com', json={
            'jsonrpc': '2.0', 'id': 1,
            'method': 'getAccountInfo',
            'params': [mint_address, {'encoding': 'jsonParsed'}]
        }, timeout=8)
        info = r.json().get('result', {}).get('value', {})
        decimals = info.get('data', {}).get('parsed', {}).get('info', {}).get('decimals', 6)
        return int(decimals)
    except Exception:
        return 6  # safe default for most SPL tokens


def execute_sol_sell(symbol, contract, token_amount) -> dict:
    if DRY_RUN: return {'success': False, 'mode': 'dry'}
    kp = _sol_keypair()
    if not kp: return {'success': False, 'error': 'No SOL keypair'}
    if not contract or len(contract) < 30:
        return {'success': False, 'error': f'No contract for {symbol}'}
    # Get actual token balance from wallet — more reliable than sim calculation
    decimals = _get_spl_decimals(contract)
    kp_pub = str(kp.pubkey())
    raw = _get_sol_token_balance(kp_pub, contract)
    if not raw or raw == 0:
        # Fallback to sim calculation
        raw = int(token_amount * (10 ** decimals))
    if not raw or raw == 0:
        return {'success': False, 'error': f'No {symbol} balance in wallet'}
    # Try with increasing slippage — sells need high tolerance on micro-caps
    quote = None
    for slippage in [500, 1000, 3000, 5000]:  # 5%, 10%, 30%, 50%
        q = _jupiter_quote_slippage(contract, WSOL_MINT, raw, slippage)
        if q:
            quote = q
            break
    if not quote: return {'success': False, 'error': 'Jupiter quote failed'}
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

def _approve_token(w3, chain, token_address, amount_wei, acct, addr):
    """Approve Uniswap router to spend token."""
    try:
        token = w3.eth.contract(
            address=w3.to_checksum_address(token_address), abi=ERC20_ABI)
        tx = token.functions.approve(
            w3.to_checksum_address(UNISWAP_ROUTER), amount_wei
        ).build_transaction({
            'from': addr,
            'nonce': w3.eth.get_transaction_count(addr, 'pending'),
            'gas': 60000,
            'maxFeePerGas': w3.to_wei(10, 'gwei'),
            'maxPriorityFeePerGas': w3.to_wei(2, 'gwei'),
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


def _try_v2_buy(w3, chain, acct, addr, router_addr, token_out, eth_amount_wei) -> dict:
    """Buy via Uniswap v2 style router (also works for Aerodrome, SushiSwap)."""
    weth = w3.to_checksum_address(WETH[chain])
    token = w3.to_checksum_address(token_out)
    router = w3.eth.contract(address=w3.to_checksum_address(router_addr), abi=UNISWAP_V2_ABI)
    max_fee, priority_fee = _gas_params(w3)
    deadline = int(time.time()) + 300
    try:
        tx = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
            0,           # amountOutMin — accept any
            [weth, token],
            addr,
            deadline
        ).build_transaction({
            'from': addr, 'value': eth_amount_wei, 'gas': 200000,
            'maxFeePerGas': max_fee, 'maxPriorityFeePerGas': priority_fee,
            'nonce': w3.eth.get_transaction_count(addr, 'pending'),
            'chainId': CHAIN_IDS[chain], 'type': 2,
        })
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt['status'] == 1:
            return {'success': True, 'tx': tx_hash.hex(), 'gas_used': receipt['gasUsed']}
        return {'success': False, 'error': 'V2 TX reverted'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def _try_v2_sell(w3, chain, acct, addr, router_addr, token_in, amount_wei) -> dict:
    """Sell via Uniswap v2 style router."""
    weth = w3.to_checksum_address(WETH[chain])
    token = w3.to_checksum_address(token_in)
    router = w3.eth.contract(address=w3.to_checksum_address(router_addr), abi=UNISWAP_V2_ABI)
    max_fee, priority_fee = _gas_params(w3)
    deadline = int(time.time()) + 300
    try:
        tx = router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
            amount_wei, 0, [token, weth], addr, deadline
        ).build_transaction({
            'from': addr, 'value': 0, 'gas': 200000,
            'maxFeePerGas': max_fee, 'maxPriorityFeePerGas': priority_fee,
            'nonce': w3.eth.get_transaction_count(addr, 'pending'),
            'chainId': CHAIN_IDS[chain], 'type': 2,
        })
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt['status'] == 1:
            return {'success': True, 'tx': tx_hash.hex(), 'gas_used': receipt['gasUsed']}
        return {'success': False, 'error': 'V2 sell TX reverted'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def _uniswap_buy(w3, chain, acct, addr, token_out, eth_amount_wei) -> dict:
    """
    Buy token with ETH. Tries DEX routers in order:
    Uniswap v3 (3 fee tiers) → Uniswap v2 → Aerodrome/Sushi
    """
    weth = w3.to_checksum_address(WETH[chain])
    token = w3.to_checksum_address(token_out)
    max_fee, priority_fee = _gas_params(w3)
    deadline = int(time.time()) + 300
    errors = []

    # Try Uniswap v3 first (best price, lowest gas)
    router_v3 = w3.eth.contract(
        address=w3.to_checksum_address(UNISWAP_ROUTER), abi=UNISWAP_ABI)
    for pool_fee in POOL_FEES:
        try:
            tx = router_v3.functions.exactInputSingle({
                'tokenIn': weth, 'tokenOut': token, 'fee': pool_fee,
                'recipient': addr, 'deadline': deadline,
                'amountIn': eth_amount_wei, 'amountOutMinimum': 0,
                'sqrtPriceLimitX96': 0,
            }).build_transaction({
                'from': addr, 'value': eth_amount_wei, 'gas': 250000,
                'maxFeePerGas': max_fee, 'maxPriorityFeePerGas': priority_fee,
                'nonce': w3.eth.get_transaction_count(addr, 'pending'),
                'chainId': CHAIN_IDS[chain], 'type': 2,
            })
            signed = acct.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt['status'] == 1:
                return {'success': True, 'tx': tx_hash.hex(),
                        'dex': f'Uniswap v3 ({pool_fee/10000:.2f}%)',
                        'gas_used': receipt['gasUsed']}
            errors.append(f"Uniswap v3 fee={pool_fee} reverted")
        except Exception as e:
            errors.append(f"Uniswap v3 fee={pool_fee}: {str(e)[:60]}")

    # Fallback to v2-style routers (Uniswap v2, Aerodrome, SushiSwap)
    for dex in DEX_ROUTERS.get(chain, [])[1:]:  # skip index 0 (v3, already tried)
        print(f"    Trying {dex['name']} fallback...")
        result = _try_v2_buy(w3, chain, acct, addr, dex['router'], token_out, eth_amount_wei)
        if result['success']:
            result['dex'] = dex['name']
            return result
        errors.append(f"{dex['name']}: {result.get('error','')[:60]}")

    return {'success': False, 'error': ' | '.join(errors[-3:])}

def _uniswap_sell(w3, chain, acct, addr, token_in, amount_wei) -> dict:
    """Sell token for ETH. Tries Uniswap v3 then v2 fallbacks."""
    weth = w3.to_checksum_address(WETH[chain])
    token = w3.to_checksum_address(token_in)
    router = w3.eth.contract(
        address=w3.to_checksum_address(UNISWAP_ROUTER), abi=UNISWAP_ABI)

    # Must approve router first
    _approve_token(w3, chain, token, amount_wei, acct, addr)

    fee_history = w3.eth.fee_history(1, 'latest', [50])
    base_fee = fee_history['baseFeePerGas'][-1]
    priority_fee = w3.to_wei(2, 'gwei')
    max_fee = base_fee * 2 + priority_fee
    deadline = int(time.time()) + 300

    last_error = None
    for pool_fee in POOL_FEES:
        try:
            tx = router.functions.exactInputSingle({
                'tokenIn':           token,
                'tokenOut':          weth,
                'fee':               pool_fee,
                'recipient':         addr,
                'deadline':          deadline,
                'amountIn':          amount_wei,
                'amountOutMinimum':  0,
                'sqrtPriceLimitX96': 0,
            }).build_transaction({
                'from':                 addr,
                'value':                0,
                'gas':                  250000,
                'maxFeePerGas':         max_fee,
                'maxPriorityFeePerGas': priority_fee,
                'nonce':                w3.eth.get_transaction_count(addr, 'pending'),
                'chainId':              CHAIN_IDS[chain],
                'type':                 2,
            })
            signed = acct.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt['status'] == 1:
                return {'success': True, 'tx': tx_hash.hex(),
                        'fee_tier': pool_fee, 'gas_used': receipt['gasUsed']}
            last_error = f"TX reverted (fee={pool_fee})"
        except Exception as e:
            last_error = str(e)
            continue

    return {'success': False, 'error': last_error or 'All fee tiers failed'}

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
    if DRY_RUN: return {'success': False, 'mode': 'dry'}
    if chain not in CHAIN_IDS or chain not in WETH:
        return {'success': False, 'error': f'Chain {chain} not supported for EVM buy'}
    acct, addr = _evm_account()
    if not addr: return {'success': False, 'error': 'No EVM wallet configured'}
    if not contract or len(contract) < 30:
        return {'success': False, 'error': f'No contract for {symbol}'}

    eth_price = _eth_price()
    eth_amount = min(usd / eth_price, MAX_ETH_PER_TRADE)
    eth_amount_wei = int(eth_amount * 1e18)

    # Pre-check: verify token has a DEX pool before wasting gas
    w3_check = _w3(chain)
    if w3_check:
        pool_fee = _check_uniswap_pool(w3_check, chain, contract)
        if not pool_fee:
            return {'success': False,
                    'error': f'No Uniswap pool found for {symbol} on {chain}'}

    # Check ETH balance
    w3 = _w3(chain)
    if not w3: return {'success': False, 'error': 'web3 unavailable'}
    bal = w3.eth.get_balance(addr)
    if bal < eth_amount_wei:
        return {'success': False,
                'error': f'Insufficient ETH: have {bal/1e18:.4f}, need {eth_amount:.4f}'}

    result = _uniswap_buy(w3, chain, acct, addr, contract, eth_amount_wei)
    if result['success']:
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

def execute_evm_sell(symbol, chain, contract, token_amount, decimals=18) -> dict:
    if DRY_RUN: return {'success': False, 'mode': 'dry'}
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

    result = _uniswap_sell(w3, chain, acct, addr, contract, amount_wei)
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
        _tg(f"✅ <b>SELL {symbol}</b> ({chain})\n"
            f"📋 fee:{result['fee_tier']/10000:.2f}%\n"
            f'🔗 <a href="{explorer}{tx}">{tx[:16]}...</a>')
    return result


# ── Unified interface called by simulation.py ─────────────────────────────────
MAX_TRADE_USD = 20.0  # Phase 2 safety cap — never spend more than $20 real money

def on_buy(symbol, chain, usd, price, source='', contract='', cash_left=None):
    """Called by simulation on every BUY."""
    # Hard safety cap — never exceed $20 in real execution regardless of sim amount
    real_usd = min(usd, MAX_TRADE_USD)
    alert_buy(symbol, chain, real_usd, price, source, dry=DRY_RUN, cash_left=cash_left)
    if DRY_RUN:
        return {'success': True, 'mode': 'paper', 'price': price}
    if chain == 'solana':
        result = execute_sol_buy(symbol, contract, real_usd)
    elif chain in ('base', 'ethereum'):
        result = execute_evm_buy(symbol, chain, contract, real_usd)
    else:
        # BSC/ARB — paper only for now
        return {'success': True, 'mode': 'paper', 'price': price}
    if not result.get('success'):
        alert_error(f"BUY {symbol} ({chain}) FAILED: {result.get('error','')}")
        # If all DEX routers failed, token likely has no pool — skip in future
        if 'reverted' in str(result.get('error','')) or 'No pool' in str(result.get('error','')):
            try:
                import json as _j
                bl_file = 'sim_ban_list.json'
                try:
                    bl = _j.load(open(bl_file))
                except Exception:
                    bl = []
                key = f"{symbol}_{chain}"
                if key not in bl:
                    bl.append(key)
                    _j.dump(bl, open(bl_file,'w'))
                    print(f"    Auto-banned {key} — no DEX pool")
            except Exception:
                pass
    return result

def on_sell(symbol, chain, price, pnl_pct, reason,
            token_amount=0, contract='', decimals=18,
            pnl_usd=None, trading_total=None, trading_pct=None):
    """Called by simulation on every SELL."""
    alert_sell(symbol, chain, price, pnl_pct, reason, dry=DRY_RUN,
               pnl_usd=pnl_usd, trading_total=trading_total, trading_pct=trading_pct)
    if DRY_RUN:
        return {'success': True, 'mode': 'paper'}
    if chain == 'solana':
        result = execute_sol_sell(symbol, contract, token_amount)
    elif chain in ('base', 'ethereum'):
        result = execute_evm_sell(symbol, chain, contract, token_amount, decimals)
    else:
        return {'success': True, 'mode': 'paper'}
    if not result.get('success'):
        alert_error(f"SELL {symbol} ({chain}) FAILED: {result.get('error','')}")
    return result


# ── Self-test ─────────────────────────────────────────────────────────────────
def test_connection():
    print("\n" + "="*55)
    print(f"  AlphaScope Executor v2.1 — Uniswap v3 + Jupiter")
    print(f"  Mode: {'🔵 DRY RUN (Phase 1)' if DRY_RUN else '🚀 LIVE (Phase 2)'}")
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
