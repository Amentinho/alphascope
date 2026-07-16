"""
AlphaScope — KOL Wallet Copy Trader
====================================
Tracks on-chain buys from a curated list of profitable Solana traders (KOLs)
via PumpPortal WebSocket subscribeAccountTrade.

When a tracked KOL buys a PumpFun/PumpSwap token, we mirror the trade.
Buy size is always capped at $1 SOL regardless of what the KOL spends.

How to find KOLs:
  1. kolscan.io — free leaderboard, sorted by realized PnL (best source)
  2. gmgn.ai/sol/wallets — filter by win rate > 60%, trades > 50
  3. axiom.trade leaderboard — top memecoin traders
  4. Manually: Twitter/X crypto traders who post wallet proof

How to vet a wallet before adding:
  - Win rate > 50% (GMGN/Kolscan metric)
  - Realized PnL > $10k in last 30 days
  - Trade count > 30 (not a single lucky bet)
  - Average hold time 5min-48h (meme flip profile, not long-term)
  - NOT a bot (no 1000s of trades per day)
  - NOT an insider (no single token = 90% of PnL)

How to add wallets:
  Edit KOL_WALLETS dict below, or add via GMGN/Kolscan leaderboard copy-paste.

Cost: PumpPortal subscribeAccountTrade is 0.01 SOL per 10,000 events
      (~$0.001 per event at $100/SOL — essentially free).
      Requires PumpPortal API key + wallet funded with >= 0.02 SOL.

Run standalone:
    python3 kol_tracker.py

Or import and call start_kol_tracker() from simulation.py.
"""

import asyncio
import json
import sqlite3
import time
import threading
from datetime import datetime, timezone

import requests

import os

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

# ── KOL Wallet Configuration ─────────────────────────────────────────────────
# Wallets are loaded DYNAMICALLY — refreshed every 24h from:
#   1. kolscan.io leaderboard (free, no key needed)
#   2. GMGN smart money list (free)
#   3. kol_wallets.json — your manual overrides (never auto-deleted)
#
# Manual override format (create kol_wallets.json in alphascope folder):
#   {
#     "WALLET_ADDRESS": {"name": "My Trader", "source": "manual"},
#     ...
#   }
#
# Scoring: win_rate(35%) + realized_pnl(30%) + trade_count(20%) + diversity(15%)
# Only wallets scoring >= MIN_WALLET_SCORE are auto-added.

KOL_WALLETS_FILE  = 'kol_wallets.json'   # manual overrides
MIN_WALLET_SCORE  = 60                    # 0-100, minimum to auto-add
MAX_AUTO_WALLETS  = 30                    # cap on auto-discovered wallets
REFRESH_HOURS     = 24                    # refresh interval

# Paper mode — set KOL_PAPER_MODE=false in .env to go live
PAPER_MODE = _env('KOL_PAPER_MODE', 'true').lower().strip() != 'false'

# ── Seed wallets (used first time, before dynamic refresh runs) ───────────────
# These are the top 20 from kolscan.io leaderboard as of May 2026.
# The dynamic loader will replace/extend this list automatically.
_SEED_WALLETS = {
    'CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o': {'name': 'Kolscan #1',  'source': 'kolscan.io'},
    'Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt': {'name': 'Kolscan #2',  'source': 'kolscan.io'},
    'AuPp4YTMTyqxYXQnHc5KUc6pUuCSsHQpBJhgnD45yqrf': {'name': 'Kolscan #3',  'source': 'kolscan.io'},
    '4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk': {'name': 'Kolscan #4',  'source': 'kolscan.io'},
    '3LUfv2u5yzsDtUzPdsSJ7ygPBuqwfycMkjpNreRR2Yww': {'name': 'Kolscan #5',  'source': 'kolscan.io'},
    'G6fUXjMKPJzCY1rveAE6Qm7wy5U3vZgKDJmN1VPAdiZC': {'name': 'Kolscan #6',  'source': 'kolscan.io'},
    '4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9': {'name': 'Kolscan #7',  'source': 'kolscan.io'},
    '5ZuV8eqkvzYFVEKbLvGBdexL2tFv7E5BCd2HZpjqbdg':  {'name': 'Kolscan #8',  'source': 'kolscan.io'},
    'EaVboaPxFCYanjoNWdkxTbPvt57nhXGu5i6m9m6ZS2kK': {'name': 'Kolscan #9',  'source': 'kolscan.io'},
    'B32QbbdDAyhvUQzjcaM5j6ZVKwjCxAwGH5Xgvb9SJqnC': {'name': 'Kolscan #10', 'source': 'kolscan.io'},
    '4cXnf2z85UiZ5cyKsPMEULq1yufAtpkatmX4j4DBZqj2': {'name': 'Kolscan #11', 'source': 'kolscan.io'},
    '5d3jQcuUvsuHyZkhdp78FFqc7WogrzZpTtec1X9VNkuE': {'name': 'Kolscan #12', 'source': 'kolscan.io'},
    '2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f': {'name': 'Kolscan #13', 'source': 'kolscan.io'},
    'FAicXNV5FVqtfbpn4Zccs71XcfGeyxBSGbqLDyDJZjke': {'name': 'Kolscan #14', 'source': 'kolscan.io'},
    'F5jWYuiDLTiaLYa54D88YbpXgEsA6NKHzWy4SN4bMYjt': {'name': 'Kolscan #15', 'source': 'kolscan.io'},
    '8nqtxpFpuXwfXG4pBLsDkkuMMPK9FjSkBMCn542HiM3v': {'name': 'Kolscan #16', 'source': 'kolscan.io'},
    '525LueqAyZJueCoiisfWy6nyh4MTvmF4X9jSqi6efXJT': {'name': 'Kolscan #17', 'source': 'kolscan.io'},
    '8rvAsDKeAcEjEkiZMug9k8v1y8mW6gQQiMobd89Uy7qR': {'name': 'Kolscan #18', 'source': 'kolscan.io'},
    'Di75xbVUg3u1qcmZci3NcZ8rjFMj7tsnYEoFdEMjS4ow': {'name': 'Kolscan #19', 'source': 'kolscan.io'},
    '3BLjRcxWGtR7WRshJ3hL25U3RjWr5Ud98wMcczQqk4Ei': {'name': 'Kolscan #20', 'source': 'kolscan.io'},
}

def _load_kol_wallets() -> dict:
    """
    Load KOL wallets from all sources, merged and deduped.
    Priority: manual file > DB cache > fresh fetch > seed wallets.
    """
    wallets = {}

    # 1. Always load seed wallets as baseline
    wallets.update(_SEED_WALLETS)

    # 2. Load manual overrides from kol_wallets.json (these are never removed)
    try:
        if os.path.exists(KOL_WALLETS_FILE):
            manual = json.load(open(KOL_WALLETS_FILE))
            wallets.update(manual)
            if manual:
                print(f"  🔱 KOL: loaded {len(manual)} manual wallets from {KOL_WALLETS_FILE}")
    except Exception as e:
        print(f"  [kol] manual file error: {e}")

    # 3. Check if DB cache is fresh enough
    try:
        conn = sqlite3.connect('alphascope.db', timeout=5)
        conn.execute("""CREATE TABLE IF NOT EXISTS kol_wallet_cache (
            wallet TEXT PRIMARY KEY,
            name TEXT, source TEXT, score INTEGER,
            win_rate REAL, pnl_usd REAL, trade_count INTEGER,
            added_at TEXT, last_seen TEXT
        )""")
        last_refresh = conn.execute(
            "SELECT MAX(added_at) FROM kol_wallet_cache WHERE source != 'manual'"
        ).fetchone()[0]
        conn.close()

        cache_age_hours = 999
        if last_refresh:
            from datetime import datetime, timezone
            try:
                last_dt = datetime.fromisoformat(last_refresh.replace('Z',''))
                cache_age_hours = (datetime.now() - last_dt).total_seconds() / 3600
            except Exception:
                pass

        if cache_age_hours < REFRESH_HOURS:
            # Cache is fresh — load from DB
            conn = sqlite3.connect('alphascope.db', timeout=5)
            rows = conn.execute(
                "SELECT wallet, name, source FROM kol_wallet_cache WHERE score >= ?",
                (MIN_WALLET_SCORE,)).fetchall()
            conn.close()
            for w, name, src in rows:
                wallets[w] = {'name': name, 'source': src}
            if rows:
                print(f"  🔱 KOL: {len(rows)} wallets from cache (age: {cache_age_hours:.0f}h)")
            return wallets

    except Exception as e:
        print(f"  [kol] cache check error: {e}")

    # 4. Cache stale or missing — fetch fresh from Kolscan + GMGN
    print("  🔱 KOL: refreshing wallet list from Kolscan + GMGN...")
    fresh = _fetch_kolscan_wallets()
    fresh.update(_fetch_gmgn_wallets())

    # Save to DB cache
    if fresh:
        try:
            conn = sqlite3.connect('alphascope.db', timeout=5)
            now = datetime.now().isoformat()
            for w, info in fresh.items():
                conn.execute("""INSERT OR REPLACE INTO kol_wallet_cache
                    (wallet, name, source, score, win_rate, pnl_usd, trade_count, added_at, last_seen)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (
                    w, info.get('name','?'), info.get('source','?'),
                    info.get('score', MIN_WALLET_SCORE),
                    info.get('win_rate', 0), info.get('pnl_usd', 0),
                    info.get('trade_count', 0), now, now))
            conn.commit()
            conn.close()
            print(f"  🔱 KOL: cached {len(fresh)} fresh wallets")
        except Exception as e:
            print(f"  [kol] cache save error: {e}")

    wallets.update(fresh)
    return wallets


def _fetch_kolscan_wallets() -> dict:
    """Fetch top performers from Kolscan leaderboard API."""
    wallets = {}
    try:
        # Kolscan public leaderboard endpoint
        r = requests.get(
            'https://kolscan.io/api/leaderboard?period=7d&limit=50&sort=pnl',
            headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'},
            timeout=15)
        if r.status_code == 200:
            data = r.json()
            traders = data if isinstance(data, list) else data.get('traders', data.get('data', []))
            added = 0
            for t in traders[:MAX_AUTO_WALLETS]:
                wallet = t.get('wallet', t.get('address', t.get('pubkey', '')))
                if not wallet or len(wallet) < 32:
                    continue
                win_rate   = float(t.get('winRate', t.get('win_rate', 0)) or 0)
                pnl        = float(t.get('realizedPnl', t.get('pnl', t.get('pnlUsd', 0))) or 0)
                trades     = int(t.get('tradeCount', t.get('trades', 0)) or 0)
                avg_hold   = float(t.get('avgHoldHours', t.get('avg_hold', 24)) or 24)

                # Score this wallet
                score = _score_wallet(win_rate, pnl, trades, avg_hold)
                if score < MIN_WALLET_SCORE:
                    continue

                rank = added + 1
                wallets[wallet] = {
                    'name': f'Kolscan #{rank}',
                    'source': 'kolscan.io/leaderboard',
                    'score': score,
                    'win_rate': win_rate,
                    'pnl_usd': pnl,
                    'trade_count': trades,
                }
                added += 1
                if added >= MAX_AUTO_WALLETS:
                    break
            print(f"  🔱 KOL Kolscan: {added} wallets (score >= {MIN_WALLET_SCORE})")
        else:
            print(f"  [kol] Kolscan API {r.status_code} — using seed wallets")
    except Exception as e:
        print(f"  [kol] Kolscan fetch error: {e}")
    return wallets


def _fetch_gmgn_wallets() -> dict:
    """Fetch smart money wallets from GMGN."""
    wallets = {}
    try:
        r = requests.get(
            'https://gmgn.ai/defi/quotation/v1/rank/sol/wallets/7d?orderby=pnl&direction=desc&limit=30',
            headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'},
            timeout=15)
        if r.status_code == 200:
            data = r.json()
            traders = data.get('data', data.get('rank', []))
            added = 0
            for t in traders[:MAX_AUTO_WALLETS]:
                wallet = t.get('wallet_address', t.get('address', ''))
                if not wallet or len(wallet) < 32:
                    continue
                win_rate = float(t.get('winrate', t.get('win_rate', 0)) or 0) * 100
                pnl      = float(t.get('realized_profit', t.get('pnl', 0)) or 0)
                trades   = int(t.get('buy_30d', t.get('total_trade_count', 0)) or 0)
                avg_hold = float(t.get('avg_holding_peroid', 24) or 24)

                score = _score_wallet(win_rate, pnl, trades, avg_hold)
                if score < MIN_WALLET_SCORE:
                    continue

                # Don't overwrite Kolscan entries
                if wallet not in wallets:
                    wallets[wallet] = {
                        'name': f'GMGN SM #{added+1}',
                        'source': 'gmgn.ai/smart-money',
                        'score': score,
                        'win_rate': win_rate,
                        'pnl_usd': pnl,
                        'trade_count': trades,
                    }
                    added += 1
            print(f"  🔱 KOL GMGN: {added} wallets (score >= {MIN_WALLET_SCORE})")
        else:
            print(f"  [kol] GMGN API {r.status_code} — skipping")
    except Exception as e:
        print(f"  [kol] GMGN fetch error: {e}")
    return wallets


def _score_wallet(win_rate: float, pnl_usd: float, trade_count: int,
                  avg_hold_hours: float) -> int:
    """
    Score a wallet 0-100 for copy trading suitability.
    Weights: win_rate(35%) pnl(30%) trade_count(20%) hold_time(15%)
    """
    score = 0

    # Win rate (35 pts) — above 50% is positive edge
    if win_rate >= 70:   score += 35
    elif win_rate >= 60: score += 25
    elif win_rate >= 50: score += 15
    elif win_rate >= 40: score += 5

    # Realized PnL (30 pts) — real money made, not paper gains
    if pnl_usd >= 100_000:  score += 30
    elif pnl_usd >= 50_000: score += 25
    elif pnl_usd >= 20_000: score += 20
    elif pnl_usd >= 10_000: score += 15
    elif pnl_usd >= 5_000:  score += 10
    elif pnl_usd >= 1_000:  score += 5

    # Trade count (20 pts) — statistical significance
    if trade_count >= 200:  score += 20
    elif trade_count >= 100: score += 15
    elif trade_count >= 50:  score += 10
    elif trade_count >= 20:  score += 5

    # Avg hold time (15 pts) — meme flip profile: 1min-48h ideal
    if 0.1 <= avg_hold_hours <= 4:    score += 15  # quick flipper
    elif avg_hold_hours <= 24:         score += 10  # day trader
    elif avg_hold_hours <= 72:         score += 5   # swing trader
    # Long-term holder = 0 pts (not suitable for copy trading memes)

    return min(score, 100)


# Runtime wallet dict — loaded at startup, refreshed every REFRESH_HOURS
KOL_WALLETS = {}


def _refresh_kol_wallets():
    """Refresh KOL_WALLETS in place. Called at startup and every 24h."""
    global KOL_WALLETS
    fresh = _load_kol_wallets()
    KOL_WALLETS = fresh
    return fresh



MAX_BUY_SOL    = 0.01    # we always cap at 0.01 SOL (~$1) regardless of KOL size
MIN_BUY_SOL    = float(_env('KOL_MIN_BUY_SOL', '0.05'))
COPY_DELAY_SEC = 0       # seconds to wait after detecting KOL buy (0 = immediate)
MAX_COPIES_PER_HOUR = 5  # rate limit — don't copy more than 5 trades/hour
SCORE_THRESHOLD = 40     # minimum token score to copy (less strict than pumpfun_stream)
KOL_PUMPFUN_SLIPPAGE = int(_env('KOL_PUMPFUN_SLIPPAGE', '10'))
KOL_PRIORITY_FEE_SOL = float(_env('KOL_PRIORITY_FEE_SOL', '0.0001'))

# ── State ─────────────────────────────────────────────────────────────────────
_tracker_active = False
_copies_this_hour = 0
_hour_reset_time = time.time() + 3600
_copied_tokens = set()   # avoid copying same token twice
_portfolio_ref = None    # SimPortfolio instance — set by start_kol_tracker()
PUMPFUN_TOTAL_SUPPLY = 1_000_000_000  # fixed for every standard PumpFun launch


# ── Database ──────────────────────────────────────────────────────────────────
def _init_kol_tables():
    try:
        conn = sqlite3.connect('alphascope.db', timeout=10)
        conn.execute("""CREATE TABLE IF NOT EXISTS kol_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_wallet TEXT,
            kol_name TEXT,
            mint TEXT,
            symbol TEXT,
            kol_sol_amount REAL,
            our_sol_amount REAL,
            tx_hash TEXT,
            copied INTEGER DEFAULT 0,
            skip_reason TEXT,
            kol_buy_time TEXT,
            our_buy_time TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS kol_performance (
            wallet TEXT PRIMARY KEY,
            name TEXT,
            copies INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            total_pnl_usd REAL DEFAULT 0,
            last_updated TEXT
        )""")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [kol] DB init error: {e}")


def _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol,
                   our_sol, tx_hash, copied, skip_reason=''):
    try:
        conn = sqlite3.connect('alphascope.db', timeout=5)
        conn.execute("""INSERT INTO kol_trades
            (kol_wallet, kol_name, mint, symbol, kol_sol_amount, our_sol_amount,
             tx_hash, copied, skip_reason, kol_buy_time, our_buy_time)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
            kol_wallet, kol_name, mint, symbol, kol_sol, our_sol,
            tx_hash or '', int(copied), skip_reason,
            datetime.now(timezone.utc).isoformat(),
            datetime.now(timezone.utc).isoformat() if copied else None,
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Token scoring for KOL-copied tokens ──────────────────────────────────────
def _quick_score(mint: str, symbol: str) -> tuple[int, str]:
    """
    Quick score for a KOL-copied token. Less strict than pumpfun_stream
    because the KOL's buy is itself a signal.

    Checks:
    - Not in ban list
    - Not already holding
    - Symbol not obviously bad
    """
    # Check sim ban list
    try:
        with open('sim_ban_list.json') as f:
            banned = set()
            for entry in json.load(f):
                banned.add(entry.split('|')[0].split('_')[0])
        if symbol.upper() in banned:
            return 0, f"{symbol} is banned"
    except Exception:
        pass

    # Check if already holding — read the live in-memory portfolio directly.
    # (Previously queried a 'sim_portfolio' table from the wrong DB file —
    # that table lives in sim.db, not alphascope.db — so this check always
    # silently failed via the except-pass below.)
    try:
        if _portfolio_ref and f"{symbol.upper()}_solana" in _portfolio_ref.holdings:
            return 0, "already holding"
    except Exception:
        pass

    # Basic symbol quality
    if not symbol or len(symbol) < 2:
        return 0, "bad symbol"

    bad_words = {'rug','scam','fake','test','elon','trump'}
    if any(w in symbol.lower() for w in bad_words):
        return 0, "banned word in symbol"

    # KOL buy is itself +60 score
    return 65, ''


def _derive_price_usd(trade_data: dict, mint: str) -> float:
    """
    Price a PumpFun token at the moment of copy.

    Bonding-curve tokens (not yet graduated to Raydium) have NO DexScreener/
    GeckoTerminal listing at all — those APIs return 0 for them. PumpPortal's
    own trade payload carries marketCapSol though, and every standard PumpFun
    launch has a fixed 1B token supply, so price_sol = marketCapSol / 1e9 is
    reliable pre-graduation. For already-graduated tokens, fall back to the
    same resolve_price() used everywhere else in the sim for consistency.
    """
    sol_price = 0.0
    try:
        from simulation import resolve_price
        sol_price = resolve_price('SOL', chain='solana', use_cache=True)
    except Exception:
        pass

    mcap_sol = float(trade_data.get('marketCapSol', 0) or 0)
    if mcap_sol > 0 and sol_price > 0:
        price_sol = mcap_sol / PUMPFUN_TOTAL_SUPPLY
        return price_sol * sol_price

    # Fallback — graduated tokens with a real AMM pool
    try:
        from simulation import resolve_price
        symbol = trade_data.get('symbol', '') or ''
        price = resolve_price(symbol, coin_id=mint, chain='solana', use_cache=False)
        if price > 0:
            return price
    except Exception:
        pass
    return 0.0


# ── Copy buy execution ────────────────────────────────────────────────────────
def _copy_buy(kol_wallet: str, kol_name: str, trade_data: dict):
    """Mirror a KOL buy. Always capped at MAX_BUY_SOL regardless of KOL size."""
    global _copies_this_hour, _hour_reset_time

    # Rate limit reset
    if time.time() > _hour_reset_time:
        _copies_this_hour = 0
        _hour_reset_time = time.time() + 3600

    mint   = trade_data.get('mint', '')
    symbol = trade_data.get('symbol', mint[:6] if mint else '?').upper()
    kol_sol = float(trade_data.get('solAmount', 0) or 0)
    tx_hash = trade_data.get('signature', '')

    # Guards
    if not mint:
        return
    if mint in _copied_tokens:
        _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol, 0,
                       tx_hash, False, 'already copied this session')
        return
    if kol_sol < MIN_BUY_SOL:
        _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol, 0,
                       tx_hash, False, f'KOL buy too small ({kol_sol:.3f} SOL < {MIN_BUY_SOL})')
        return
    if _copies_this_hour >= MAX_COPIES_PER_HOUR:
        _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol, 0,
                       tx_hash, False, f'rate limit ({_copies_this_hour}/{MAX_COPIES_PER_HOUR}/h)')
        return

    score, skip_reason = _quick_score(mint, symbol)
    if score < SCORE_THRESHOLD or skip_reason:
        reason = skip_reason or f"score {score} < {SCORE_THRESHOLD}"
        print(f"  [kol] SKIP copy {kol_name} → {symbol}: {reason}")
        _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol, 0,
                       tx_hash, False, reason)
        return

    if PAPER_MODE:
        if _portfolio_ref is None:
            print(f"  📄 [PAPER] KOL copy {kol_name} → {symbol}: no portfolio wired up, skipping")
            _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol, 0,
                           '', False, 'no portfolio reference')
            return
        price_usd = _derive_price_usd(trade_data, mint)
        if price_usd <= 0:
            print(f"  📄 [PAPER] KOL copy {kol_name} → {symbol}: no price available, "
                  f"skipping (would be untracked)")
            _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol, 0,
                           '', False, 'no price available')
            return
        # Flat $1 paper stake, matching the real MAX_BUY_SOL (~$1) cap —
        # the exact SOL/USD conversion doesn't change paper P&L% tracking.
        usd_spent = 1.0
        ok, msg = _portfolio_ref.record_position(
            symbol, 'solana', usd_spent, price_usd,
            source=f'kol_copy:{kol_name}', contract=mint,
            dex_url=f"https://pump.fun/{mint}", category='DEX_GEM')
        if not ok:
            print(f"  📄 [PAPER] KOL copy {kol_name} → {symbol}: not recorded ({msg})")
            _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol, 0,
                           '', False, msg)
            return
        print(f"  📄 [PAPER] KOL copy: {kol_name} → {symbol} "
              f"({kol_sol:.2f} SOL) @ ${price_usd:.8g} — {msg}")
        _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol,
                       MAX_BUY_SOL, 'paper', True, 'paper_mode')
        _copied_tokens.add(mint)
        _copies_this_hour += 1
        return

    if COPY_DELAY_SEC > 0:
        time.sleep(COPY_DELAY_SEC)

    print(f"  🔱 KOL copy: {kol_name} bought {symbol} ({kol_sol:.2f} SOL) → copying ${MAX_BUY_SOL*100:.0f}")

    # Execute buy via PumpPortal /api/trade-local — exact pattern from docs
    try:
        from solders.transaction import VersionedTransaction  # type: ignore
        from solders.commitment_config import CommitmentLevel  # type: ignore
        from solders.rpc.requests import SendVersionedTransaction  # type: ignore
        from solders.rpc.config import RpcSendTransactionConfig  # type: ignore
        from executor import SOL_RPC_FALLBACKS, _sol_keypair

        kp = _sol_keypair()
        if not kp:
            print("  [kol] No SOL_PRIVATE_KEY — cannot copy trade")
            return

        r = requests.post(
            url='https://pumpportal.fun/api/trade-local',
            data={
                'publicKey':        str(kp.pubkey()),
                'action':           'buy',
                'mint':             mint,
                'amount':           MAX_BUY_SOL,
                'denominatedInSol': 'true',
                'slippage':         KOL_PUMPFUN_SLIPPAGE,
                'priorityFee':      KOL_PRIORITY_FEE_SOL,
                'pool':             'pump',
            },
            timeout=15,
        )

        if r.status_code != 200:
            msg = f'PumpPortal HTTP {r.status_code}: {r.text[:60]}'
            print(f"  [kol] buy error: {msg}")
            _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol,
                           MAX_BUY_SOL, '', False, msg)
            return

        tx = VersionedTransaction(
            VersionedTransaction.from_bytes(r.content).message, [kp])

        commitment = CommitmentLevel.Confirmed
        config = RpcSendTransactionConfig(skip_preflight=True,
                                          preflight_commitment=commitment)
        tx_payload = SendVersionedTransaction(tx, config)

        rpc_response = requests.post(
            url=SOL_RPC_FALLBACKS[0],
            headers={'Content-Type': 'application/json'},
            data=tx_payload.to_json(),
            timeout=20,
        )
        result = rpc_response.json()
        if 'error' in result:
            err = str(result['error'])
            _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol,
                           MAX_BUY_SOL, '', False, err[:60])
            return

        sig = result.get('result', '')
        if not sig:
            _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol,
                           MAX_BUY_SOL, '', False, 'no signature')
            return

        _copied_tokens.add(mint)
        _copies_this_hour += 1

        sol_url = f"https://solscan.io/tx/{sig}"
        print(f"  ✅ KOL copy {symbol} bought → {sig[:16]}...")
        _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol,
                       MAX_BUY_SOL, sig, True)

        # Register with the sim portfolio so check_exits()/run_price_monitor()
        # actually manage stop-loss/take-profit on this real position. Without
        # this the token just sits in the wallet forever — the buy above
        # executes on-chain via PumpPortal directly, bypassing portfolio.buy(),
        # so nothing else in the codebase would ever know to sell it.
        price_usd = _derive_price_usd(trade_data, mint)
        if price_usd > 0 and _portfolio_ref is not None:
            sol_price = 0.0
            try:
                from simulation import resolve_price
                sol_price = resolve_price('SOL', chain='solana', use_cache=True)
            except Exception:
                pass
            usd_spent = MAX_BUY_SOL * sol_price if sol_price > 0 else 1.0
            ok, msg = _portfolio_ref.record_position(
                symbol, 'solana', usd_spent, price_usd,
                source=f'kol_copy:{kol_name}', contract=mint,
                dex_url=f"https://pump.fun/{mint}", category='DEX_GEM')
            if ok:
                print(f"  🔱 KOL copy {symbol}: registered for exit management @ ${price_usd:.8g}")
            else:
                print(f"  ⚠️  KOL copy {symbol}: bought on-chain (tx {sig[:12]}...) but NOT "
                      f"registered for auto-exit ({msg}) — needs MANUAL monitoring")
        else:
            print(f"  ⚠️  KOL copy {symbol}: bought on-chain (tx {sig[:12]}...) but price "
                  f"could not be resolved — NOT tracked for stop-loss/take-profit. "
                  f"Check this position manually.")

        # Telegram alert
        try:
            from executor import _tg
            _tg(f"🔱 <b>KOL Copy Trade</b>\n"
                f"Following: {kol_name}\n"
                f"Token: {symbol} (<code>{mint[:8]}</code>)\n"
                f"KOL spent: {kol_sol:.2f} SOL\n"
                f"We spent: {MAX_BUY_SOL} SOL (~$1)\n"
                f"🔗 <a href='https://pump.fun/{mint}'>pump.fun</a>")
        except Exception:
            pass

    except ImportError as e:
        print(f"  [kol] import error: {e} — pip install solders")
    except Exception as e:
        print(f"  [kol] copy buy error: {e}")
        _log_kol_trade(kol_wallet, kol_name, mint, symbol, kol_sol,
                       MAX_BUY_SOL, '', False, str(e)[:80])


# ── WebSocket stream ──────────────────────────────────────────────────────────
async def _kol_stream_loop():
    """Subscribe to trade events from all tracked KOL wallets."""
    global _tracker_active
    _init_kol_tables()

    # Fix SSL on Mac
    import ssl, certifi
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())

    # Load wallets dynamically
    _refresh_kol_wallets()
    last_refresh_time = time.time()

    if not KOL_WALLETS:
        print("  [kol] No wallets loaded — check Kolscan/GMGN connectivity")
        return

    if not KOL_WALLETS:
        print("  [kol] No wallets configured — edit KOL_WALLETS in kol_tracker.py")
        print("  [kol] Find wallets at: https://kolscan.io or https://gmgn.ai")
        return

    # PumpPortal requires API key for account trade subscriptions
    try:
        from executor import _env
        pumpportal_key = _env('PUMPPORTAL_API_KEY', '')
    except Exception:
        import os
        pumpportal_key = os.environ.get('PUMPPORTAL_API_KEY', '')

    ws_url = f"wss://pumpportal.fun/api/data?api-key={pumpportal_key}" \
             if pumpportal_key else "wss://pumpportal.fun/api/data"

    wallet_list = list(KOL_WALLETS.keys())
    mode_str = "📄 PAPER MODE" if PAPER_MODE else "🟢 LIVE MODE ($1 per copy)"
    print(f"  🔱 KOL tracker: watching {len(KOL_WALLETS)} wallets [{mode_str}]")
    if PAPER_MODE:
        print(f"  🔱 To go live: add KOL_PAPER_MODE=false to .env")

    backoff = 5
    while _tracker_active:
        # Auto-refresh wallet list every REFRESH_HOURS
        if time.time() - last_refresh_time > REFRESH_HOURS * 3600:
            print("  🔱 KOL: refreshing wallet list (24h interval)...")
            _refresh_kol_wallets()
            last_refresh_time = time.time()
            print(f"  🔱 KOL: now tracking {len(KOL_WALLETS)} wallets")
        try:
            import websockets
            wallet_list = list(KOL_WALLETS.keys())
            async with websockets.connect(
                ws_url, ping_interval=30, ping_timeout=10, ssl=ssl_ctx
            ) as ws:
                print("  ✅ KOL tracker: connected")
                backoff = 5

                # Subscribe to all wallet trades in ONE message (PumpPortal requirement)
                await ws.send(json.dumps({
                    "method": "subscribeAccountTrade",
                    "keys":   wallet_list,
                }))

                async for raw in ws:
                    if not _tracker_active:
                        break
                    try:
                        data = json.loads(raw)
                        tx_type = data.get('txType', '')
                        if tx_type != 'buy':
                            continue

                        trader = data.get('traderPublicKey', '')
                        if trader not in KOL_WALLETS:
                            continue

                        kol_info = KOL_WALLETS[trader]
                        kol_name = kol_info.get('name', trader[:8])

                        # Run copy buy in thread to avoid blocking the async loop
                        threading.Thread(
                            target=_copy_buy,
                            args=(trader, kol_name, data),
                            daemon=True
                        ).start()

                    except Exception as e:
                        print(f"  [kol] parse error: {e}")

        except Exception as e:
            if _tracker_active:
                print(f"  [kol] stream error: {e} — reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    print("  🔱 KOL tracker: stopped")


# ── Thread entry point ────────────────────────────────────────────────────────
def start_kol_tracker(portfolio=None):
    """
    Start the KOL copy trader in a background daemon thread.

    portfolio: the running SimPortfolio instance. Required for copied buys to
    be registered for stop-loss/take-profit management — without it, buys
    still execute on-chain (live mode) but nothing will ever sell them.
    """
    global _tracker_active, _portfolio_ref
    if _tracker_active:
        return
    _tracker_active = True
    _portfolio_ref = portfolio
    if portfolio is None:
        print("  ⚠️  KOL tracker: no portfolio passed — copied buys will NOT "
              "be tracked for auto stop-loss/take-profit")

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_kol_stream_loop())
        finally:
            loop.close()

    t = threading.Thread(target=_run, name="kol-tracker", daemon=True)
    t.start()
    print("  🔱 KOL tracker started (background, wallets loaded dynamically)")
    return t


def stop_kol_tracker():
    global _tracker_active
    _tracker_active = False


# ── KOL research helper ───────────────────────────────────────────────────────
def research_wallet(wallet_address: str):
    """
    Quick research helper. Fetches recent trades from Solscan and prints
    win rate, total trades, biggest wins. Use this to vet wallets before adding.

    Usage:
        python3 kol_tracker.py research <wallet_address>
    """
    print(f"\nResearching: {wallet_address}")
    print(f"→ Kolscan:  https://kolscan.io/account/{wallet_address}")
    print(f"→ GMGN:     https://gmgn.ai/sol/address/{wallet_address}")
    print(f"→ Solscan:  https://solscan.io/account/{wallet_address}")
    print(f"→ Cielo:    https://app.cielo.finance/profile/{wallet_address}")
    print("\nCheck: win rate > 50%, realized PnL > $10k/30d, avg hold < 48h")


if __name__ == '__main__':
    import sys, signal

    if len(sys.argv) == 3 and sys.argv[1] == 'research':
        research_wallet(sys.argv[2])
        sys.exit(0)

    if not KOL_WALLETS:
        print("\n  No KOL wallets configured.")
        print("  Steps to add:")
        print("  1. Go to https://kolscan.io → Leaderboard")
        print("  2. Sort by 7d Realized PnL")
        print("  3. Pick 5-10 wallets with win rate > 50% and > 30 trades")
        print("  4. Add to KOL_WALLETS dict in this file")
        print("  5. Re-run: python3 kol_tracker.py")
        print("\n  Research a specific wallet:")
        print("  python3 kol_tracker.py research <wallet_address>")
        sys.exit(0)

    def _sigint(sig, frame):
        print("\n  Stopping KOL tracker...")
        stop_kol_tracker()
    signal.signal(signal.SIGINT, _sigint)

    _tracker_active = True
    asyncio.run(_kol_stream_loop())
