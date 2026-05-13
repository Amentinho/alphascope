"""
AlphaScope — PumpFun Real-Time Stream + Buyer
=============================================
Connects to PumpPortal WebSocket (free, no key) and streams every new token
the moment it launches on PumpFun. Scores each token instantly and buys the
ones that pass ALL filters.

Also subscribes to graduation events (migration to PumpSwap) — a graduation
is a strong momentum signal; we can buy those too via Jupiter.

How to run (standalone):
    python3 pumpfun_stream.py

Or import and call start_pumpfun_stream() in a background thread from simulation.py.

Cost: PumpPortal charges 0.5% per trade (on top of Solana ~$0.001 fee).
Buy size: $1 SOL (hardcoded, same as sim cap).
"""

import asyncio
import json
import sqlite3
import time
import threading
from datetime import datetime, timezone

import os
import requests

# ── Config ────────────────────────────────────────────────────────────────────
PUMPPORTAL_WS   = "wss://pumpportal.fun/api/data"
PUMPPORTAL_BUY  = "https://pumpportal.fun/api/trade-local"
MAIN_DB         = "alphascope.db"

def _env(key, default=''):
    val = os.environ.get(key, '')
    if val:
        return val
    try:
        with open('.env') as f:
            for line in f:
                if line.strip().startswith(f'{key}='):
                    return line.split('=', 1)[1].strip()
    except Exception:
        pass
    return default

# Paper mode — set PUMPFUN_PAPER_MODE=false in .env to go live
# Defaults to TRUE (paper) so you never accidentally spend real money
PAPER_MODE = _env('PUMPFUN_PAPER_MODE', 'true').lower().strip() != 'false'

BUY_SOL_AMOUNT  = min(float(_env('PUMPFUN_BUY_SOL_AMOUNT', '0.005')),
                      float(_env('PUMPFUN_MAX_BUY_SOL', '0.01')))
MAX_BUY_USD     = 1.0           # display cap for logs
SCORE_THRESHOLD = 55            # minimum score 0-100 to buy
MIN_TWITTER_MENTIONS = 3        # must have ≥ 3 recent tweets
PUMPFUN_SLIPPAGE = int(_env('PUMPFUN_SLIPPAGE', '10'))
PUMPFUN_PRIORITY_FEE_SOL = float(_env('PUMPFUN_PRIORITY_FEE_SOL', '0.0001'))
BAN_WORDS = {
    'rug', 'scam', 'fake', 'ponzi', 'elon', 'trump', 'maga', 'safe', 'moon',
    'porn', 'nude', 'penis', 'rape', 'nazi', 'isis', 'squid', 'shib2',
    'test', 'doge2',
}
RUG_PATTERNS = ['v2', 'v3', 'inu2', 'classic', 'old', 'real', 'original']

_stream_active = False
_bought_this_session = set()    # avoid double-buying same token


# ── Database ──────────────────────────────────────────────────────────────────
def _init_pumpfun_table():
    try:
        conn = sqlite3.connect(MAIN_DB, timeout=10)
        conn.execute("""CREATE TABLE IF NOT EXISTS pumpfun_launches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint TEXT UNIQUE,
            name TEXT,
            symbol TEXT,
            description TEXT,
            twitter TEXT,
            telegram TEXT,
            website TEXT,
            creator TEXT,
            market_cap_sol REAL,
            score INTEGER,
            bought INTEGER DEFAULT 0,
            skip_reason TEXT,
            launched_at TEXT,
            fetched_at TEXT DEFAULT (datetime('now'))
        )""")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [pumpfun] DB init error: {e}")


def _save_launch(mint, data, score, bought=False, skip_reason=''):
    try:
        conn = sqlite3.connect(MAIN_DB, timeout=5)
        conn.execute("""INSERT OR IGNORE INTO pumpfun_launches
            (mint, name, symbol, description, twitter, telegram, website,
             creator, market_cap_sol, score, bought, skip_reason, launched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            mint,
            data.get('name', '')[:80],
            data.get('symbol', '')[:20],
            (data.get('description', '') or '')[:200],
            data.get('twitter', '') or '',
            data.get('telegram', '') or '',
            data.get('website', '') or '',
            data.get('traderPublicKey', '') or '',
            float(data.get('marketCapSol', 0) or 0),
            score, int(bought), skip_reason,
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Scoring ───────────────────────────────────────────────────────────────────
def score_token(data: dict) -> tuple[int, str]:
    """
    Score a brand-new PumpFun token 0-100.
    Returns (score, skip_reason) — skip_reason is '' if score passes.

    Signals (weights):
      +20  Has Twitter link
      +15  Has Telegram link
      +10  Has website
      +15  Name/symbol quality (no banned words, no numbers, readable)
      +20  Twitter mentions in last 10 min (from our social cache)
      +10  Reasonable market cap (not zero, not already mooned)
      +10  Creator wallet is not a known rugger (from ban list)
    """
    score = 0
    name   = (data.get('name', '') or '').strip()
    symbol = (data.get('symbol', '') or '').strip().upper()
    mint   = data.get('mint', '')

    # ── Hard filters — instant skip ───────────────────────────────
    if not name or not symbol or not mint:
        return 0, "missing name/symbol/mint"

    name_lower = name.lower()
    sym_lower  = symbol.lower()

    if any(w in name_lower or w in sym_lower for w in BAN_WORDS):
        return 0, f"banned word in name/symbol"

    if any(p in name_lower for p in RUG_PATTERNS):
        return 0, f"rug pattern in name"

    if len(symbol) < 2 or len(symbol) > 10:
        return 0, f"symbol length bad ({len(symbol)})"

    # Already bought this session
    if mint in _bought_this_session:
        return 0, "already bought this session"

    # ── Positive signals ──────────────────────────────────────────
    twitter  = data.get('twitter', '') or ''
    telegram = data.get('telegram', '') or ''
    website  = data.get('website', '') or ''

    if twitter:  score += 20
    if telegram: score += 15
    if website:  score += 10

    # Name quality: readable, not just numbers
    readable = sum(1 for c in name if c.isalpha()) / max(len(name), 1)
    if readable > 0.6:
        score += 15
    elif readable > 0.3:
        score += 5

    # Market cap sanity (in SOL)
    mcap = float(data.get('marketCapSol', 0) or 0)
    if 0 < mcap < 30:
        score += 10     # very early, low mcap
    elif mcap < 5:
        score += 5      # basically zero — could go either way

    # Twitter mentions from our social cache (if running)
    tw_mentions = _get_twitter_mentions(symbol)
    if tw_mentions >= 10:   score += 20
    elif tw_mentions >= 5:  score += 15
    elif tw_mentions >= 3:  score += 10
    elif tw_mentions >= 1:  score += 5

    # Creator wallet reputation (check ban list)
    creator = data.get('traderPublicKey', '') or ''
    if creator and _is_banned_creator(creator):
        return 0, f"creator {creator[:8]} is a known rugger"

    return score, ''


def _get_twitter_mentions(symbol: str) -> int:
    """Check recent Twitter/social mentions from our DB."""
    try:
        conn = sqlite3.connect(MAIN_DB, timeout=3)
        row = conn.execute("""
            SELECT tweet_count FROM token_social_cache
            WHERE symbol=? AND cached_at >= datetime('now', '-15 minutes')
            ORDER BY cached_at DESC LIMIT 1
        """, (symbol,)).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _is_banned_creator(wallet: str) -> bool:
    """Check if creator wallet has rugged before (from our auto_ban or manual list)."""
    try:
        conn = sqlite3.connect(MAIN_DB, timeout=3)
        row = conn.execute(
            "SELECT 1 FROM auto_ban WHERE key LIKE ? LIMIT 1",
            (f"%{wallet[:8]}%",)).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


# ── Buy execution ─────────────────────────────────────────────────────────────
def _record_paper_buy(mint: str, symbol: str, sol_amount: float):
    """Record a paper trade in DB so we can track what would have been profitable."""
    try:
        conn = sqlite3.connect(MAIN_DB, timeout=5)
        conn.execute("""CREATE TABLE IF NOT EXISTS pumpfun_paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint TEXT, symbol TEXT, sol_amount REAL,
            bought_at TEXT, outcome TEXT, pnl_pct REAL
        )""")
        conn.execute(
            "INSERT INTO pumpfun_paper_trades (mint, symbol, sol_amount, bought_at) VALUES (?,?,?,?)",
            (mint, symbol, sol_amount, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass


def buy_pumpfun_token(mint: str, symbol: str, sol_amount: float = BUY_SOL_AMOUNT) -> dict:
    """
    Buy a PumpFun token via PumpPortal /api/trade-local.
    Uses exact pattern from PumpPortal docs — signed locally, key never leaves machine.
    Fee: 0.5% to PumpPortal + ~$0.001 Solana network fee.

    pool='pump'  → bonding curve (pre-graduation)
    pool='auto'  → auto-detect (adds ~100ms delay per docs — avoid)
    """
    if PAPER_MODE:
        print(f"  📄 [PAPER] PumpFun BUY {symbol} {sol_amount} SOL "
              f"(~${sol_amount*100:.2f}) — no real tx sent")
        _record_paper_buy(mint, symbol, sol_amount)
        return {'success': True, 'mode': 'paper', 'mint': mint,
                'sol_spent': sol_amount}

    try:
        from solders.transaction import VersionedTransaction  # type: ignore
        from solders.commitment_config import CommitmentLevel  # type: ignore
        from solders.rpc.requests import SendVersionedTransaction  # type: ignore
        from solders.rpc.config import RpcSendTransactionConfig  # type: ignore
        from executor import SOL_RPC_FALLBACKS, _sol_keypair

        kp = _sol_keypair()
        if not kp:
            return {'success': False, 'error': 'No SOL_PRIVATE_KEY in .env'}

        # Step 1: Get unsigned transaction from PumpPortal
        r = requests.post(
            url="https://pumpportal.fun/api/trade-local",
            data={                                  # use data= not json= per docs
                'publicKey':        str(kp.pubkey()),
                'action':           'buy',
                'mint':             mint,
                'amount':           sol_amount,     # SOL amount
                'denominatedInSol': 'true',
                'slippage':         PUMPFUN_SLIPPAGE,
                'priorityFee':      PUMPFUN_PRIORITY_FEE_SOL,
                'pool':             'pump',         # explicit pool — avoids 100ms auto-detect delay
            },
            timeout=15,
        )

        if r.status_code != 200:
            return {'success': False,
                    'error': f'PumpPortal HTTP {r.status_code}: {r.text[:100]}'}

        if not r.content:
            return {'success': False, 'error': 'PumpPortal returned empty transaction'}

        # Step 2: Sign locally using exact solders pattern from docs
        tx = VersionedTransaction(
            VersionedTransaction.from_bytes(r.content).message, [kp])

        # Step 3: Submit via our Alchemy RPC (skipPreflight=true per docs recommendation)
        commitment = CommitmentLevel.Confirmed
        config = RpcSendTransactionConfig(
            skip_preflight=True,              # docs: set True to avoid false preflight fails on new tokens
            preflight_commitment=commitment,
        )
        tx_payload = SendVersionedTransaction(tx, config)

        rpc_url = SOL_RPC_FALLBACKS[0]        # use Alchemy as primary
        rpc_response = requests.post(
            url=rpc_url,
            headers={'Content-Type': 'application/json'},
            data=tx_payload.to_json(),
            timeout=20,
        )

        if rpc_response.status_code != 200:
            return {'success': False,
                    'error': f'RPC HTTP {rpc_response.status_code}'}

        result = rpc_response.json()
        if 'error' in result:
            return {'success': False, 'error': f"RPC error: {result['error']}"}

        sig = result.get('result', '')
        if not sig:
            return {'success': False, 'error': 'No signature in RPC response'}

        sol_url = f"https://solscan.io/tx/{sig}"
        print(f"  🟢 PumpFun BUY {symbol} {sol_amount} SOL → {sig[:16]}...")
        print(f"     {sol_url}")
        return {'success': True, 'tx': sig, 'url': sol_url,
                'sol_spent': sol_amount, 'mint': mint}

    except ImportError as e:
        return {'success': False,
                'error': f'Missing: {e} — pip install solders base58'}
    except Exception as e:
        return {'success': False, 'error': str(e)[:120]}



# ── Graduation handler ────────────────────────────────────────────────────────
def handle_graduation(data: dict):
    """
    A token just graduated from bonding curve → PumpSwap.
    Graduation = hit $69k market cap = sustained community interest.
    In PAPER_MODE, records but does not execute.
    """
    mint   = data.get('mint', '')
    symbol = data.get('symbol', mint[:6])
    if not mint or mint in _bought_this_session:
        return
    if PAPER_MODE:
        print(f"  📄 [PAPER] Graduation buy {symbol} ({mint[:8]}) — no real tx")
        _record_paper_buy(mint, symbol, BUY_SOL_AMOUNT)
        _bought_this_session.add(mint)
        return
    try:
        from executor import on_buy
        result = on_buy(symbol, 'solana', 1.0, 0, source='pumpfun_graduation',
                        contract=mint)
        if result.get('success'):
            _bought_this_session.add(mint)
            print(f"  🎓 Graduation buy {symbol} ({mint[:8]}) ✅")
            _save_launch(mint, data, 80, bought=True)
        else:
            print(f"  🎓 Graduation buy {symbol} failed: {result.get('error','')[:60]}")
    except Exception as e:
        print(f"  [pumpfun] graduation buy error: {e}")


# ── Main WebSocket loop ───────────────────────────────────────────────────────
async def _stream_loop():
    """Persistent WebSocket connection. Reconnects on any failure."""
    global _stream_active
    _init_pumpfun_table()

    # Fix SSL on Mac — use certifi certificates
    import ssl, certifi, os
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
    os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())

    mode_str = "📄 PAPER MODE (no real trades)" if PAPER_MODE else "🟢 LIVE MODE ($1 per buy)"
    print(f"  📡 PumpFun stream: connecting... [{mode_str}]")
    print(f"  📡 Score threshold: {SCORE_THRESHOLD}/100 | Buy size: {BUY_SOL_AMOUNT} SOL (~$1)")
    if PAPER_MODE:
        print(f"  📡 To go live: add PUMPFUN_PAPER_MODE=false to .env")

    backoff = 5
    while _stream_active:
        try:
            import websockets  # import inside loop — works correctly in venv threads
            async with websockets.connect(
                PUMPPORTAL_WS,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
                ssl=ssl_ctx,         # use certifi SSL context
            ) as ws:
                print("  ✅ PumpFun stream: connected")
                backoff = 5  # reset on successful connection

                # Subscribe to new token launches (free)
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                # Subscribe to graduation events (free)
                await ws.send(json.dumps({"method": "subscribeMigration"}))

                async for raw in ws:
                    if not _stream_active:
                        break
                    try:
                        data = json.loads(raw)
                        event_type = data.get('txType', data.get('type', ''))

                        if event_type == 'create':
                            # New token launched
                            _handle_new_token(data)
                        elif event_type in ('migration', 'graduate'):
                            # Token graduated to PumpSwap
                            handle_graduation(data)
                    except Exception as e:
                        print(f"  [pumpfun] parse error: {e}")

        except Exception as e:
            if _stream_active:
                print(f"  [pumpfun] stream error: {e} — reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)  # exponential backoff, max 2 min

    print("  📡 PumpFun stream: stopped")


def _handle_new_token(data: dict):
    """Process a new token launch event."""
    mint   = data.get('mint', '')
    name   = data.get('name', '?')
    symbol = data.get('symbol', '?').upper()

    if not mint:
        return

    score, skip_reason = score_token(data)

    if score < SCORE_THRESHOLD or skip_reason:
        reason = skip_reason or f"score {score} < {SCORE_THRESHOLD}"
        # Only log tokens that were close to passing (score > 30) — suppress noise
        if score > 30:
            print(f"  ⏭  PumpFun SKIP {symbol} ({mint[:8]}) — {reason}")
        _save_launch(mint, data, score, bought=False, skip_reason=reason)
        return

    # Passed all filters — buy
    print(f"  🔥 PumpFun NEW {symbol} ({mint[:8]}) score={score} — buying ${MAX_BUY_USD}")
    result = buy_pumpfun_token(mint, symbol, BUY_SOL_AMOUNT)

    if result.get('success'):
        _bought_this_session.add(mint)
        _save_launch(mint, data, score, bought=True)

        # Write to dex_gems so simulation.py can track it and set stop-loss
        try:
            conn = sqlite3.connect(MAIN_DB, timeout=5)
            conn.execute("""INSERT OR IGNORE INTO dex_gems
                (symbol, chain, contract_address, dex_url, price_usd, liquidity_usd,
                 age_hours, cross_score, price_change_24h, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (
                symbol, 'solana', mint,
                f"https://pump.fun/{mint}",
                0,          # price unknown at launch
                0,          # liquidity unknown at launch
                0,          # brand new
                9,          # high score — manually bought
                0,
                datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()
            conn.close()
        except Exception:
            pass

        # Send Telegram alert
        try:
            from executor import _tg
            _tg(f"🚀 <b>PumpFun LAUNCH BUY</b>\n"
                f"Token: {name} ({symbol})\n"
                f"Mint: <code>{mint}</code>\n"
                f"Score: {score}/100\n"
                f"💰 {BUY_SOL_AMOUNT} SOL (~${MAX_BUY_USD})\n"
                f"🔗 <a href='https://pump.fun/{mint}'>pump.fun</a>")
        except Exception:
            pass
    else:
        print(f"  ❌ PumpFun buy failed: {result.get('error','')[:80]}")
        _save_launch(mint, data, score, bought=False,
                     skip_reason=f"buy_failed: {result.get('error','')[:60]}")


# ── Thread entry point ────────────────────────────────────────────────────────
def start_pumpfun_stream():
    """Start the PumpFun stream in a background daemon thread."""
    global _stream_active
    if _stream_active:
        print("  [pumpfun] stream already running")
        return
    _stream_active = True

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_stream_loop())
        finally:
            loop.close()

    t = threading.Thread(target=_run, name="pumpfun-stream", daemon=True)
    t.start()
    print("  📡 PumpFun stream started (background)")
    return t


def stop_pumpfun_stream():
    global _stream_active
    _stream_active = False


# ── Standalone run ────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import signal

    def _sigint(sig, frame):
        print("\n  Stopping stream...")
        stop_pumpfun_stream()

    signal.signal(signal.SIGINT, _sigint)
    _stream_active = True
    asyncio.run(_stream_loop())
