"""
AlphaScope Executor v1.0
========================
Phase 1: DRY_RUN=True  — logs every trade, no real transactions
Phase 2: DRY_RUN=False — executes real swaps via Jupiter + Jito

SOL chain only for Phase 2. ETH/BSC/BASE remain paper-traded until Phase 3.

Setup:
    pip install solana solders base58 requests

.env required:
    SOL_PRIVATE_KEY=<base58 private key>   # never commit this
    TELEGRAM_BOT_TOKEN=<token>
    TELEGRAM_CHAT_ID=<your chat id>
    EXECUTOR_DRY_RUN=true                  # set false for Phase 2
    EXECUTOR_MAX_SOL_PER_TRADE=0.5         # max SOL per trade in Phase 2
    EXECUTOR_SLIPPAGE_BPS=300              # 3% slippage tolerance
"""

import os
import json
import time
import requests
import threading
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
def _env(key, default=''):
    val = os.environ.get(key, default)
    if not val:
        try:
            with open('.env') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(f'{key}='):
                        val = line.split('=', 1)[1].strip()
                        break
        except Exception:
            pass
    return val

DRY_RUN          = _env('EXECUTOR_DRY_RUN', 'true').lower() != 'false'
MAX_SOL_PER_TRADE = float(_env('EXECUTOR_MAX_SOL_PER_TRADE', '0.5'))
SLIPPAGE_BPS     = int(_env('EXECUTOR_SLIPPAGE_BPS', '300'))
TELEGRAM_TOKEN   = _env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT    = _env('TELEGRAM_CHAT_ID')
SOL_PRIVATE_KEY  = _env('SOL_PRIVATE_KEY')

# Jupiter & Jito endpoints
JUPITER_QUOTE    = 'https://api.jup.ag/swap/v1/quote'
JUPITER_SWAP     = 'https://api.jup.ag/swap/v1/swap'
JITO_ENDPOINT    = 'https://mainnet.block-engine.jito.labs.io/api/v1/bundles'
WSOL_MINT        = 'So11111111111111111111111111111111111111112'
SOL_DECIMALS     = 9

# ── Telegram alerts ───────────────────────────────────────────────────────────
def _tg(msg: str):
    """Send Telegram message. Non-blocking, never raises."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    def _send():
        try:
            requests.post(
                f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
                json={'chat_id': TELEGRAM_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                timeout=5)
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()


def alert_buy(symbol, chain, usd, price, reason='signal', dry=True):
    mode = '🔵 DRY RUN' if dry else '✅ LIVE'
    msg = (f"{mode} <b>BUY {symbol}</b> ({chain})\n"
           f"💵 ${usd:.0f} @ ${price:.8g}\n"
           f"📋 {reason}\n"
           f"🕐 {datetime.now().strftime('%H:%M:%S')}")
    _tg(msg)
    print(f"    📱 TG: BUY {symbol} ${usd:.0f} @ ${price:.8g} {'[DRY]' if dry else '[LIVE]'}")


def alert_sell(symbol, chain, price, pnl_pct, reason, dry=True):
    emoji = '🟢' if pnl_pct >= 0 else '🔴'
    mode  = '🔵 DRY RUN' if dry else '✅ LIVE'
    msg = (f"{mode} {emoji} <b>SELL {symbol}</b> ({chain})\n"
           f"💵 ${price:.8g} | {pnl_pct:+.1f}%\n"
           f"📋 {reason.upper()}\n"
           f"🕐 {datetime.now().strftime('%H:%M:%S')}")
    _tg(msg)
    print(f"    📱 TG: SELL {symbol} {pnl_pct:+.1f}% {reason} {'[DRY]' if dry else '[LIVE]'}")


def alert_error(msg: str):
    _tg(f"⚠️ AlphaScope ERROR\n{msg}\n🕐 {datetime.now().strftime('%H:%M:%S')}")


def alert_start(sim_id, hours, capital):
    mode = '🔵 DRY RUN (Phase 1)' if DRY_RUN else '🚀 LIVE TRADING (Phase 2)'
    _tg(f"🤖 <b>AlphaScope Started</b>\n"
        f"{mode}\n"
        f"📋 {sim_id}\n"
        f"⏱ {hours}h | 💰 ${capital:.0f} trading capital")


def alert_complete(sim_id, pnl_pct, wins, losses, best):
    emoji = '🟢' if pnl_pct >= 0 else '🔴'
    mode  = '[DRY]' if DRY_RUN else '[LIVE]'
    _tg(f"{emoji} <b>Sim Complete {mode}</b>\n"
        f"📋 {sim_id}\n"
        f"💰 {pnl_pct:+.1f}% | {wins}W/{losses}L\n"
        f"🏆 Best: {best}")


# ── SOL wallet ────────────────────────────────────────────────────────────────
def _get_keypair():
    """Load keypair from SOL_PRIVATE_KEY env. Returns None if not set."""
    if not SOL_PRIVATE_KEY:
        return None
    try:
        from solders.keypair import Keypair  # type: ignore
        import base58
        secret = base58.b58decode(SOL_PRIVATE_KEY)
        return Keypair.from_bytes(secret)
    except ImportError:
        print("  executor: install solana+solders: pip install solana solders base58")
        return None
    except Exception as e:
        print(f"  executor: keypair error: {e}")
        return None


def get_sol_balance(pubkey_str: str) -> float:
    """Returns SOL balance of wallet."""
    try:
        r = requests.post('https://api.mainnet-beta.solana.com', json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'getBalance',
            'params': [pubkey_str]}, timeout=10)
        lamports = r.json().get('result', {}).get('value', 0)
        return lamports / 1e9
    except Exception:
        return 0.0


# ── Jupiter quote ─────────────────────────────────────────────────────────────
def get_token_mint(symbol: str, contract_address: str = '') -> str:
    """
    Resolve token mint address. Uses contract_address if available,
    otherwise tries Jupiter token list.
    """
    if contract_address and len(contract_address) > 30:
        return contract_address
    # Try Jupiter token list
    try:
        r = requests.get(
            'https://api.jup.ag/tokens/v1/tagged/verified', timeout=10)
        if r.status_code == 200:
            for token in r.json():
                if token.get('symbol', '').upper() == symbol.upper():
                    return token.get('address', '')
    except Exception:
        pass
    return ''


def get_jupiter_quote(input_mint: str, output_mint: str, amount_lamports: int) -> dict:
    """Get Jupiter swap quote. Returns quote dict or None."""
    try:
        r = requests.get(JUPITER_QUOTE, params={
            'inputMint': input_mint,
            'outputMint': output_mint,
            'amount': amount_lamports,
            'slippageBps': SLIPPAGE_BPS,
            'onlyDirectRoutes': 'false',
        }, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"    Jupiter quote error: {e}")
    return None


def estimate_price_impact(quote: dict) -> float:
    """Returns price impact % from Jupiter quote."""
    try:
        return float(quote.get('priceImpactPct', 0)) * 100
    except Exception:
        return 0.0


# ── Phase 2: Real swap execution ──────────────────────────────────────────────
def execute_sol_buy(symbol: str, contract_address: str, usd_amount: float,
                    sol_price_usd: float) -> dict:
    """
    Execute a real SOL buy via Jupiter + Jito.
    Returns {'success': bool, 'tx': str, 'price': float, 'error': str}
    """
    if DRY_RUN:
        return {'success': False, 'error': 'DRY_RUN mode — no real trade'}

    keypair = _get_keypair()
    if not keypair:
        return {'success': False, 'error': 'No keypair configured'}

    # Cap trade size
    sol_amount = usd_amount / sol_price_usd
    sol_amount = min(sol_amount, MAX_SOL_PER_TRADE)
    lamports   = int(sol_amount * 1e9)

    mint = get_token_mint(symbol, contract_address)
    if not mint:
        return {'success': False, 'error': f'Cannot resolve mint for {symbol}'}

    # Get quote
    quote = get_jupiter_quote(WSOL_MINT, mint, lamports)
    if not quote:
        return {'success': False, 'error': 'Jupiter quote failed'}

    impact = estimate_price_impact(quote)
    if impact > 5.0:
        return {'success': False, 'error': f'Price impact too high: {impact:.1f}%'}

    try:
        from solana.rpc.api import Client  # type: ignore
        from solders.transaction import VersionedTransaction  # type: ignore
        import base64

        client = Client('https://api.mainnet-beta.solana.com')

        # Get swap transaction from Jupiter
        swap_resp = requests.post(JUPITER_SWAP, json={
            'quoteResponse': quote,
            'userPublicKey': str(keypair.pubkey()),
            'wrapAndUnwrapSol': True,
            'useJitoBundle': True,  # use Jito for MEV protection
            'jitoTipLamports': 1000,  # ~$0.0001 tip
        }, timeout=15)

        if swap_resp.status_code != 200:
            return {'success': False, 'error': f'Jupiter swap API: {swap_resp.status_code}'}

        swap_data = swap_resp.json()
        tx_b64    = swap_data.get('swapTransaction', '')
        if not tx_b64:
            return {'success': False, 'error': 'No swap transaction returned'}

        # Sign transaction
        raw_tx = base64.b64decode(tx_b64)
        tx     = VersionedTransaction.from_bytes(raw_tx)
        tx.sign([keypair])
        signed_b64 = base64.b64encode(bytes(tx)).decode()

        # Submit via Jito for MEV protection
        jito_resp = requests.post(JITO_ENDPOINT, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'sendBundle',
            'params': [[signed_b64]]
        }, headers={'Content-Type': 'application/json'}, timeout=15)

        if jito_resp.status_code == 200:
            bundle_id = jito_resp.json().get('result', '')
            # Calculate actual price from quote
            out_amount = int(quote.get('outAmount', 0))
            actual_price = (lamports / 1e9 * sol_price_usd) / max(out_amount, 1) if out_amount else 0
            return {
                'success': True,
                'tx': bundle_id,
                'price': actual_price,
                'impact': impact,
                'sol_spent': sol_amount,
            }
        else:
            return {'success': False, 'error': f'Jito bundle failed: {jito_resp.text[:200]}'}

    except ImportError:
        return {'success': False, 'error': 'solana/solders not installed: pip install solana solders base58'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def execute_sol_sell(symbol: str, contract_address: str, token_amount: float,
                     sol_price_usd: float) -> dict:
    """Execute a real SOL sell via Jupiter + Jito."""
    if DRY_RUN:
        return {'success': False, 'error': 'DRY_RUN mode — no real trade'}

    keypair = _get_keypair()
    if not keypair:
        return {'success': False, 'error': 'No keypair configured'}

    mint = get_token_mint(symbol, contract_address)
    if not mint:
        return {'success': False, 'error': f'Cannot resolve mint for {symbol}'}

    # Get token decimals from on-chain (assume 6 for most SPL tokens)
    token_decimals = 6
    raw_amount = int(token_amount * (10 ** token_decimals))

    quote = get_jupiter_quote(mint, WSOL_MINT, raw_amount)
    if not quote:
        return {'success': False, 'error': 'Jupiter quote failed'}

    impact = estimate_price_impact(quote)
    if impact > 10.0:  # wider tolerance on sell (exit is priority)
        print(f"    WARN: high price impact on sell {symbol}: {impact:.1f}%")

    try:
        from solana.rpc.api import Client  # type: ignore
        from solders.transaction import VersionedTransaction  # type: ignore
        import base64

        swap_resp = requests.post(JUPITER_SWAP, json={
            'quoteResponse': quote,
            'userPublicKey': str(keypair.pubkey()),
            'wrapAndUnwrapSol': True,
            'useJitoBundle': True,
            'jitoTipLamports': 2000,  # higher tip on sell — priority matters
        }, timeout=15)

        if swap_resp.status_code != 200:
            return {'success': False, 'error': f'Jupiter swap API: {swap_resp.status_code}'}

        tx_b64 = swap_resp.json().get('swapTransaction', '')
        raw_tx = base64.b64decode(tx_b64)
        tx = VersionedTransaction.from_bytes(raw_tx)
        tx.sign([keypair])
        signed_b64 = base64.b64encode(bytes(tx)).decode()

        jito_resp = requests.post(JITO_ENDPOINT, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'sendBundle',
            'params': [[signed_b64]]
        }, headers={'Content-Type': 'application/json'}, timeout=15)

        if jito_resp.status_code == 200:
            out_lamports = int(quote.get('outAmount', 0))
            sol_received = out_lamports / 1e9
            usd_received = sol_received * sol_price_usd
            return {
                'success': True,
                'tx': jito_resp.json().get('result', ''),
                'sol_received': sol_received,
                'usd_received': usd_received,
                'impact': impact,
            }
        else:
            return {'success': False, 'error': f'Jito bundle failed: {jito_resp.text[:200]}'}

    except ImportError:
        return {'success': False, 'error': 'solana/solders not installed'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ── Execution bridge — called by simulation.py ─────────────────────────────────
def on_buy(symbol: str, chain: str, usd: float, price: float,
           source: str = '', contract: str = '') -> dict:
    """
    Called by simulation when a BUY fires.
    Phase 1 (DRY_RUN): logs + Telegram alert only.
    Phase 2 (LIVE):    executes real swap on SOL chain, alerts on result.
    Returns execution result dict.
    """
    alert_buy(symbol, chain, usd, price, source, dry=DRY_RUN)

    if DRY_RUN or chain != 'solana':
        # Phase 1: paper trade logged, non-SOL chains always paper
        return {'success': True, 'mode': 'paper', 'price': price}

    # Phase 2: real SOL execution
    sol_price = _get_sol_price()
    result = execute_sol_buy(symbol, contract, usd, sol_price)
    if result['success']:
        _tg(f"✅ <b>EXECUTED BUY {symbol}</b>\n"
            f"💰 ${usd:.0f} | impact: {result.get('impact',0):.1f}%\n"
            f"🔗 Bundle: {result.get('tx','')[:20]}...")
    else:
        alert_error(f"BUY {symbol} FAILED: {result.get('error','')}")
    return result


def on_sell(symbol: str, chain: str, price: float, pnl_pct: float,
            reason: str, token_amount: float = 0, contract: str = '') -> dict:
    """
    Called by simulation when a SELL fires.
    Phase 1: logs + Telegram. Phase 2: real swap.
    """
    alert_sell(symbol, chain, price, pnl_pct, reason, dry=DRY_RUN)

    if DRY_RUN or chain != 'solana':
        return {'success': True, 'mode': 'paper'}

    # Phase 2: real SOL sell
    sol_price = _get_sol_price()
    result = execute_sol_sell(symbol, contract, token_amount, sol_price)
    if result['success']:
        _tg(f"✅ <b>EXECUTED SELL {symbol}</b>\n"
            f"💵 {result.get('usd_received',0):.2f} received\n"
            f"📋 {reason.upper()} | {pnl_pct:+.1f}%\n"
            f"🔗 Bundle: {result.get('tx','')[:20]}...")
    else:
        alert_error(f"SELL {symbol} FAILED: {result.get('error','')}")
    return result


def _get_sol_price() -> float:
    """Quick SOL price fetch."""
    try:
        r = requests.get(
            'https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd',
            timeout=5)
        return float(r.json().get('solana', {}).get('usd', 85))
    except Exception:
        return 85.0


# ── Self-test ─────────────────────────────────────────────────────────────────
def test_connection():
    """Verify Telegram and Jupiter are reachable. Run before Phase 2."""
    print("\n=== Executor connection test ===")
    print(f"  Mode: {'DRY RUN (Phase 1)' if DRY_RUN else 'LIVE (Phase 2)'}")
    print(f"  Max SOL/trade: {MAX_SOL_PER_TRADE}")
    print(f"  Slippage: {SLIPPAGE_BPS/100:.1f}%")

    # Telegram
    if TELEGRAM_TOKEN and TELEGRAM_CHAT:
        _tg("🤖 AlphaScope executor online — connection test")
        print("  ✅ Telegram configured")
    else:
        print("  ⚠️  Telegram not configured (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)")

    # Jupiter
    try:
        r = requests.get('https://api.jup.ag/tokens/v1/tagged/verified', timeout=5)
        print(f"  ✅ Jupiter reachable (status {r.status_code})")
    except Exception as e:
        print(f"  ❌ Jupiter unreachable: {e}")

    # SOL wallet
    if SOL_PRIVATE_KEY:
        kp = _get_keypair()
        if kp:
            bal = get_sol_balance(str(kp.pubkey()))
            print(f"  ✅ Wallet: {str(kp.pubkey())[:12]}... | Balance: {bal:.4f} SOL")
        else:
            print("  ❌ Wallet keypair failed to load")
    else:
        print("  ⚠️  SOL_PRIVATE_KEY not set (required for Phase 2)")

    # SOL price
    p = _get_sol_price()
    print(f"  ✅ SOL price: ${p:.2f}")
    print("=================================\n")


if __name__ == '__main__':
    test_connection()
