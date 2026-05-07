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
    val = os.environ.get(key, default)
    if val: return val
    try:
        for line in open('.env'):
            if line.strip().startswith(f'{key}='):
                return line.strip().split('=',1)[1].strip()
    except Exception: pass
    return default

DRY_RUN           = _env('EXECUTOR_DRY_RUN','true').lower() != 'false'
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
RPCS = {
    'ethereum': 'https://rpc.flashbots.net',  # MEV protection
    'base':     'https://mainnet.base.org',
    'arbitrum': 'https://arb1.arbitrum.io/rpc',
}

# Uniswap v3 — same address on ETH and BASE
UNISWAP_ROUTER = '0xE592427A0AEce92De3Edee1F18E0157C05861564'  # SwapRouter02
UNISWAP_QUOTER = '0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6'  # Quoter v1

# WETH address per chain
WETH = {
    'ethereum': '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
    'base':     '0x4200000000000000000000000000000000000006',
}

# Uniswap pool fees (try 3000 = 0.3% first, then 10000 = 1%)
POOL_FEES = [3000, 10000, 500]

# Jupiter / Jito
JUPITER_QUOTE = 'https://api.jup.ag/swap/v1/quote'
JUPITER_SWAP  = 'https://api.jup.ag/swap/v1/swap'
JITO_ENDPOINT = 'https://mainnet.block-engine.jito.labs.io/api/v1/bundles'
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

def _jupiter_quote(input_mint, output_mint, amount_raw):
    try:
        r = requests.get(JUPITER_QUOTE, params={
            'inputMint': input_mint, 'outputMint': output_mint,
            'amount': amount_raw, 'slippageBps': SLIPPAGE_BPS,
        }, timeout=10)
        if r.status_code == 200: return r.json()
        print(f"    Jupiter {r.status_code}: {r.text[:200]}")
    except Exception as e: print(f"    Jupiter: {e}")
    return None

def _jito_submit(signed_b64):
    r = requests.post(JITO_ENDPOINT, json={
        'jsonrpc': '2.0', 'id': 1,
        'method': 'sendBundle', 'params': [[signed_b64]]
    }, headers={'Content-Type': 'application/json'}, timeout=15)
    if r.status_code == 200: return r.json().get('result', '')
    raise Exception(f"Jito {r.status_code}: {r.text[:200]}")

def execute_sol_buy(symbol, contract, usd) -> dict:
    if DRY_RUN: return {'success': False, 'mode': 'dry'}
    kp = _sol_keypair()
    if not kp: return {'success': False, 'error': 'No SOL keypair'}
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
        tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        tx.sign([kp])
        bundle = _jito_submit(base64.b64encode(bytes(tx)).decode())
        out = int(quote.get('outAmount', 0))
        price = (lamports / 1e9 * sol_price) / max(out, 1)
        return {'success': True, 'tx': bundle, 'price': price,
                'sol_spent': lamports/1e9, 'impact': impact}
    except ImportError:
        return {'success': False, 'error': 'pip install solana solders base58'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def execute_sol_sell(symbol, contract, token_amount) -> dict:
    if DRY_RUN: return {'success': False, 'mode': 'dry'}
    kp = _sol_keypair()
    if not kp: return {'success': False, 'error': 'No SOL keypair'}
    if not contract or len(contract) < 30:
        return {'success': False, 'error': f'No contract for {symbol}'}
    raw = int(token_amount * 1e6)  # assume 6 decimals for SPL
    quote = _jupiter_quote(contract, WSOL_MINT, raw)
    if not quote: return {'success': False, 'error': 'Jupiter quote failed'}
    try:
        import base64
        from solders.transaction import VersionedTransaction
        swap = requests.post(JUPITER_SWAP, json={
            'quoteResponse': quote, 'userPublicKey': str(kp.pubkey()),
            'wrapAndUnwrapSol': True, 'useJitoBundle': True, 'jitoTipLamports': 3000,
        }, timeout=15).json()
        tx_b64 = swap.get('swapTransaction', '')
        if not tx_b64: return {'success': False, 'error': 'No swap tx'}
        tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        tx.sign([kp])
        bundle = _jito_submit(base64.b64encode(bytes(tx)).decode())
        sol_out = int(quote.get('outAmount', 0)) / 1e9
        return {'success': True, 'tx': bundle,
                'sol_received': sol_out, 'usd_received': sol_out * _sol_price()}
    except Exception as e:
        return {'success': False, 'error': str(e)}


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
        w3 = Web3(Web3.HTTPProvider(RPCS[chain]))
        if not w3.is_connected():
            raise Exception(f"Cannot connect to {chain} RPC")
        return w3
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
            'nonce': w3.eth.get_transaction_count(addr),
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

def _uniswap_buy(w3, chain, acct, addr, token_out, eth_amount_wei) -> dict:
    """
    Buy token with ETH via Uniswap v3 exactInputSingle.
    Tries fee tiers 0.3%, 1%, 0.05% in order.
    """
    weth = w3.to_checksum_address(WETH[chain])
    token = w3.to_checksum_address(token_out)
    router = w3.eth.contract(
        address=w3.to_checksum_address(UNISWAP_ROUTER), abi=UNISWAP_ABI)

    slippage = SLIPPAGE_BPS / 10000
    deadline = int(time.time()) + 300  # 5 min

    # Estimate gas with EIP-1559
    fee_history = w3.eth.fee_history(1, 'latest', [50])
    base_fee = fee_history['baseFeePerGas'][-1]
    priority_fee = w3.to_wei(2, 'gwei')
    max_fee = base_fee * 2 + priority_fee

    last_error = None
    for pool_fee in POOL_FEES:
        try:
            # Build swap tx — ETH → token (payable, no approval needed)
            tx = router.functions.exactInputSingle({
                'tokenIn':           weth,
                'tokenOut':          token,
                'fee':               pool_fee,
                'recipient':         addr,
                'deadline':          deadline,
                'amountIn':          eth_amount_wei,
                'amountOutMinimum':  0,  # accept any amount (slippage via deadline)
                'sqrtPriceLimitX96': 0,
            }).build_transaction({
                'from':                 addr,
                'value':                eth_amount_wei,
                'gas':                  250000,
                'maxFeePerGas':         max_fee,
                'maxPriorityFeePerGas': priority_fee,
                'nonce':                w3.eth.get_transaction_count(addr),
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

def _uniswap_sell(w3, chain, acct, addr, token_in, amount_wei) -> dict:
    """Sell token for ETH via Uniswap v3."""
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
                'nonce':                w3.eth.get_transaction_count(addr),
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
        _tg(f"✅ <b>BUY {symbol}</b> ({chain})\n"
            f"💵 ${usd:.0f} | fee:{result['fee_tier']/10000:.2f}%\n"
            f"🔗 {result['tx'][:16]}...")
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
        _tg(f"✅ <b>SELL {symbol}</b> ({chain})\n"
            f"📋 fee:{result['fee_tier']/10000:.2f}%\n"
            f"🔗 {result['tx'][:16]}...")
    return result


# ── Unified interface called by simulation.py ─────────────────────────────────
def on_buy(symbol, chain, usd, price, source='', contract='', cash_left=None):
    """Called by simulation on every BUY."""
    alert_buy(symbol, chain, usd, price, source, dry=DRY_RUN, cash_left=cash_left)
    if DRY_RUN:
        return {'success': True, 'mode': 'paper', 'price': price}
    if chain == 'solana':
        result = execute_sol_buy(symbol, contract, usd)
    elif chain in ('base', 'ethereum'):
        result = execute_evm_buy(symbol, chain, contract, usd)
    else:
        # BSC/ARB — paper only for now
        return {'success': True, 'mode': 'paper', 'price': price}
    if not result.get('success'):
        alert_error(f"BUY {symbol} ({chain}) FAILED: {result.get('error','')}")
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
