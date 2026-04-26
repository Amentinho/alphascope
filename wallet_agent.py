"""
AlphaScope — Wallet Agent v1.0
Phase 2 agent: reads signals, executes swaps, manages positions.

Architecture:
  - Read-only mode (default): reads signals, simulates trades, tracks paper P&L
  - Live mode (opt-in): signs and broadcasts transactions via connected wallet
  - Safety rails: position limits, gas limits, slippage limits, daily loss limit

Supported actions:
  - Swap on DEX (Uniswap v3 on ETH/ARB/BASE, Jupiter on SOL)
  - Airdrop qualification (contract interactions, bridges, LP positions)
  - Portfolio rebalancing based on signals

Current status: READ-ONLY / PAPER TRADING
Live execution requires: private key or hardware wallet signer (Phase 2b)
"""

import sqlite3
import requests
import json
from datetime import datetime, timezone
from typing import Optional

# ── Safety limits (paper trading respects these too) ──────────────────────────
MAX_POSITION_USD     = 500       # Never put more than $500 in a single trade
MAX_GAS_USD          = 20        # Never pay more than $20 gas for a single tx
MAX_SLIPPAGE_PCT     = 2.0       # Max 2% slippage tolerance
DAILY_LOSS_LIMIT_USD = 200       # Stop trading if daily P&L drops below -$200
MIN_SIGNAL_CONFIDENCE = 65       # Only act on signals with >= 65% confidence
MIN_LIQUIDITY_USD    = 50_000    # Only trade tokens with >= $50k DEX liquidity

# ── DEX router addresses (for future live execution) ──────────────────────────
ROUTERS = {
    'ethereum':  '0xE592427A0AEce92De3Edee1F18E0157C05861564',  # Uniswap v3
    'arbitrum':  '0xE592427A0AEce92De3Edee1F18E0157C05861564',  # Uniswap v3
    'base':      '0x2626664c2603336E57B271c5C0b26F421741e481',  # Uniswap v3
    'optimism':  '0xE592427A0AEce92De3Edee1F18E0157C05861564',  # Uniswap v3
    'bsc':       '0x10ED43C718714eb63d5aA57B78B54704E256024E',  # PancakeSwap v2
    'polygon':   '0xE592427A0AEce92De3Edee1F18E0157C05861564',  # Uniswap v3
}

# ── DEX aggregator APIs (free, no auth) ───────────────────────────────────────
AGGREGATORS = {
    'ethereum': 'https://api.1inch.dev/swap/v6.0/1',
    'arbitrum': 'https://api.1inch.dev/swap/v6.0/42161',
    'base':     'https://api.1inch.dev/swap/v6.0/8453',
    'solana':   'https://quote-api.jup.ag/v6',  # Jupiter
}

# ── Gas estimation endpoints ──────────────────────────────────────────────────
GAS_ORACLES = {
    'ethereum': 'https://api.etherscan.io/api?module=gastracker&action=gasoracle',
    'polygon':  'https://api.polygonscan.com/api?module=gastracker&action=gasoracle',
}


def get_db():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_agent_tables():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS agent_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        coin_id TEXT,
        chain TEXT,
        action TEXT,
        amount_usd REAL,
        amount_tokens REAL,
        price_usd REAL,
        gas_usd REAL,
        slippage_pct REAL,
        signal TEXT,
        signal_confidence INTEGER,
        mode TEXT DEFAULT 'PAPER',
        tx_hash TEXT,
        status TEXT DEFAULT 'PENDING',
        pnl_usd REAL DEFAULT 0,
        notes TEXT,
        created_at TEXT,
        executed_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS agent_config (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT)''')
    # Default config
    defaults = [
        ('mode', 'PAPER'),
        ('max_position_usd', str(MAX_POSITION_USD)),
        ('max_gas_usd', str(MAX_GAS_USD)),
        ('max_slippage_pct', str(MAX_SLIPPAGE_PCT)),
        ('daily_loss_limit_usd', str(DAILY_LOSS_LIMIT_USD)),
        ('min_signal_confidence', str(MIN_SIGNAL_CONFIDENCE)),
        ('enabled', 'false'),
        ('wallet_address', ''),
    ]
    for k, v in defaults:
        c.execute("INSERT OR IGNORE INTO agent_config (key, value, updated_at) VALUES (?,?,?)",
                  (k, v, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_config(key, default=None):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT value FROM agent_config WHERE key=?", (key,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default


def set_config(key, value):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO agent_config (key, value, updated_at) VALUES (?,?,?)",
              (key, str(value), datetime.now().isoformat()))
    conn.commit()
    conn.close()


def estimate_gas_price(chain='ethereum'):
    """Get current gas price in USD for a standard swap."""
    from portfolio import GAS_COST_USD
    # Static estimates as baseline
    base_gas = GAS_COST_USD.get(chain.lower(), GAS_COST_USD['default'])

    # Try live gas oracle for ETH
    if chain == 'ethereum':
        try:
            res = requests.get(
                'https://api.etherscan.io/api?module=gastracker&action=gasoracle&apikey=YourApiKeyToken',
                timeout=5
            )
            data = res.json().get('result', {})
            fast_gwei = float(data.get('FastGasPrice', 0))
            if fast_gwei > 0:
                eth_price = 2300  # fallback
                try:
                    ep = requests.get(
                        'https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd',
                        timeout=5
                    ).json()
                    eth_price = ep.get('ethereum', {}).get('usd', 2300)
                except Exception:
                    pass
                gas_units = 150_000  # typical swap
                return (fast_gwei * 1e-9) * gas_units * eth_price
        except Exception:
            pass

    return base_gas


def get_quote(token_in, token_out, amount_usd, chain='ethereum'):
    """
    Get a swap quote from DEX aggregator.
    Returns: {price_impact_pct, output_amount, gas_estimate_usd, route}
    Paper trading mode returns simulated quote.
    """
    mode = get_config('mode', 'PAPER')

    if mode == 'PAPER':
        # Simulate quote with realistic slippage based on liquidity
        simulated_impact = 0.1 if amount_usd < 1000 else 0.3 if amount_usd < 5000 else 1.0
        return {
            'price_impact_pct': simulated_impact,
            'output_amount_usd': amount_usd * (1 - simulated_impact/100),
            'gas_estimate_usd': estimate_gas_price(chain),
            'route': f'{token_in} → {token_out} (simulated)',
            'mode': 'PAPER',
        }

    # Live quote via 1inch (ETH/ARB/BASE) or Jupiter (SOL)
    if chain == 'solana':
        # Jupiter quote API
        try:
            res = requests.get(
                'https://quote-api.jup.ag/v6/quote',
                params={
                    'inputMint': token_in,
                    'outputMint': token_out,
                    'amount': int(amount_usd * 1e6),  # USDC decimals
                    'slippageBps': int(MAX_SLIPPAGE_PCT * 100),
                },
                timeout=10,
            )
            if res.status_code == 200:
                data = res.json()
                return {
                    'price_impact_pct': float(data.get('priceImpactPct', 0)) * 100,
                    'output_amount_usd': amount_usd,
                    'gas_estimate_usd': 0.001,
                    'route': 'Jupiter aggregator',
                    'raw': data,
                }
        except Exception as e:
            return {'error': str(e)}
    else:
        return {'error': 'Live EVM quotes require 1inch API key — use PAPER mode'}

    return {'error': 'Quote failed'}


def _load_all_candidates():
    """
    Pull investment candidates from ALL AlphaScope intelligence sources.
    Returns list of dicts with unified fields.
    """
    import pandas as pd
    candidates = {}  # keyed by symbol to dedup

    conn = get_db()
    now_str = "datetime('now', '-24 hours')"

    # ── 1. Existing portfolio — HOLD/SELL decisions ───────────────────────────
    try:
        port = pd.read_sql_query(
            """SELECT ps.coin_id, ps.symbol, ps.signal, ps.confidence,
                      ps.reasons, ps.price_usd, ps.change_24h, ps.score,
                      p.amount, p.entry_price_usd, p.chain
               FROM portfolio_signals ps
               JOIN portfolio p ON p.coin_id = ps.coin_id
               WHERE ps.generated_at >= datetime('now', '-2 hours')
               ORDER BY ps.score DESC""", conn)
        for _, r in port.iterrows():
            sym = r['symbol'].upper()
            price = float(r['price_usd'] or 0)
            candidates[sym] = {
                'symbol': sym, 'coin_id': r['coin_id'],
                'chain': r.get('chain', 'ethereum') or 'ethereum',
                'price_usd': price,
                'alpha_score': min(100, max(0, int(r.get('score', 0) or 0) * 8 + 40)),
                'signal': r['signal'], 'confidence': int(r['confidence'] or 50),
                'reasons': [r.get('reasons', '')],
                'sources': ['portfolio'],
                'is_holding': True,
                'current_amount': float(r.get('amount', 0) or 0),
                'entry_price': float(r.get('entry_price_usd', 0) or 0),
                'trade_usd': 0,
                'category': 'PORTFOLIO',
            }
    except Exception:
        pass

    # ── 2. Coin buzz — social momentum plays ─────────────────────────────────
    MAJORS = {'BTC','ETH','SOL','BNB','XRP','ADA','DOGE','SHIB','AVAX',
               'DOT','MATIC','LINK','LTC','UNI','ATOM','NEAR','ALGO',
               'USDT','USDC','DAI','WBTC','WETH'}
    try:
        buzz = pd.read_sql_query(
            """SELECT coin, mention_count, total_engagement, avg_sentiment
               FROM coin_buzz WHERE fetched_at >= datetime('now', '-6 hours')
               ORDER BY mention_count DESC LIMIT 20""", conn)
        for _, r in buzz.iterrows():
            sym = r['coin'].upper()
            if sym in MAJORS:
                continue
            sent = float(r['avg_sentiment'] or 0)
            mentions = int(r['mention_count'] or 0)
            score = min(100, mentions * 4 + (sent * 20) + 20)
            if sym not in candidates:
                candidates[sym] = {
                    'symbol': sym, 'coin_id': sym.lower(),
                    'chain': 'ethereum', 'price_usd': 0,
                    'alpha_score': score, 'signal': 'WATCH', 'confidence': 45,
                    'reasons': [], 'sources': [], 'is_holding': False,
                    'current_amount': 0, 'entry_price': 0,
                    'trade_usd': 0, 'category': 'BUZZ',
                }
            candidates[sym]['sources'].append('buzz')
            candidates[sym]['reasons'].append(f'{mentions} social mentions, sentiment:{sent:+.2f}')
            candidates[sym]['alpha_score'] = max(candidates[sym]['alpha_score'], score)
            if mentions >= 8 and sent > 0.1:
                candidates[sym]['signal'] = 'ACCUMULATE'
                candidates[sym]['confidence'] = max(candidates[sym]['confidence'], 60)
    except Exception:
        pass

    # ── 3. DEX gems — new on-chain pairs ─────────────────────────────────────
    try:
        dex = pd.read_sql_query(
            """SELECT symbol, name, chain, dex, price_usd, liquidity_usd,
                      volume_24h, price_change_24h, age_hours, cross_score,
                      social_buzz, pre_launch_match, dex_url, contract_address
               FROM dex_gems
               WHERE fetched_at >= datetime('now', '-24 hours')
               AND cross_score >= 5
               ORDER BY cross_score DESC, liquidity_usd DESC LIMIT 25""", conn)
        for _, r in dex.iterrows():
            sym = r['symbol'].upper()
            if sym in MAJORS:
                continue
            score = min(100, int(r.get('cross_score', 0) or 0) * 12 + 20)
            # Age bonus — very new = higher upside potential
            age = float(r.get('age_hours', 99) or 99)
            if age < 4: score += 20
            elif age < 24: score += 10
            liq = float(r.get('liquidity_usd', 0) or 0)
            vol = float(r.get('volume_24h', 0) or 0)
            chain = (r.get('chain') or 'ethereum').lower()
            reasons = [f"DEX {r.get('dex','')} liq:${liq/1000:.0f}k age:{age:.0f}h"]
            if r.get('social_buzz'): reasons.append('social buzz match')
            if r.get('pre_launch_match'): reasons.append('ICO listing match')

            # Dedup — if same symbol already seen with higher score, skip
            if sym in candidates:
                existing_score = candidates[sym].get('alpha_score', 0)
                if score <= existing_score:
                    candidates[sym]['sources'].append('dex')
                    continue
            if sym not in candidates or score > candidates[sym].get('alpha_score', 0):
                candidates[sym] = {
                    'symbol': sym,
                    'coin_id': r.get('contract_address', '') or r.get('dex_url', ''),
                    'contract_address': r.get('contract_address', ''),
                    'chain': chain, 'price_usd': float(r.get('price_usd', 0) or 0),
                    'alpha_score': score, 'signal': 'WATCH', 'confidence': 50,
                    'reasons': [], 'sources': [], 'is_holding': False,
                    'current_amount': 0, 'entry_price': 0,
                    'trade_usd': 0, 'category': 'DEX_GEM',
                    'dex_url': r.get('dex_url', ''),
                    'liquidity_usd': liq,
                }
            candidates[sym]['sources'].append('dex')
            candidates[sym]['reasons'].extend(reasons)
            candidates[sym]['alpha_score'] = max(candidates[sym]['alpha_score'], score)
            if score >= 70:
                candidates[sym]['signal'] = 'BUY'
                candidates[sym]['confidence'] = max(candidates[sym]['confidence'], 70)
            elif score >= 50:
                candidates[sym]['signal'] = 'ACCUMULATE'
                candidates[sym]['confidence'] = max(candidates[sym]['confidence'], 60)
    except Exception:
        pass

    # ── 4. Hidden gems — CoinGecko trending low-cap ───────────────────────────
    try:
        hg = pd.read_sql_query(
            """SELECT symbol, name, market_cap_rank, signal_type, signal_detail
               FROM hidden_gems
               WHERE fetched_at >= datetime('now', '-2 hours')
               GROUP BY symbol HAVING MAX(fetched_at)""", conn)
        for _, r in hg.iterrows():
            sym = r['symbol'].upper()
            if sym in MAJORS:
                continue
            rank = int(r.get('market_cap_rank') or 999)
            score = max(30, 80 - rank // 10)  # rank 100 = 70, rank 500 = 30
            if sym not in candidates:
                candidates[sym] = {
                    'symbol': sym, 'coin_id': sym.lower(),
                    'chain': 'ethereum', 'price_usd': 0,
                    'alpha_score': score, 'signal': 'WATCH', 'confidence': 45,
                    'reasons': [], 'sources': [], 'is_holding': False,
                    'current_amount': 0, 'entry_price': 0,
                    'trade_usd': 0, 'category': 'HIDDEN_GEM',
                }
            candidates[sym]['sources'].append('hidden_gem')
            candidates[sym]['reasons'].append(f'rank #{rank} trending on CoinGecko')
            candidates[sym]['alpha_score'] = max(candidates[sym]['alpha_score'], score)
    except Exception:
        pass

    # ── 5. Pre-launch gems — high-score ICO/IDO ───────────────────────────────
    try:
        plg = pd.read_sql_query(
            """SELECT project_name, sale_type, source, total_score,
                      social_mentions, launchpad_score, url
               FROM pre_launch_gems
               WHERE total_score >= 6 AND status != 'DISMISSED'
               ORDER BY total_score DESC LIMIT 10""", conn)
        for _, r in plg.iterrows():
            sym = r['project_name'].upper()[:8].replace(' ', '')
            score = min(100, int(r.get('total_score', 0) or 0) * 5 + 10)
            lp = int(r.get('launchpad_score', 0) or 0)
            if lp >= 8: score += 20
            if sym not in candidates:
                candidates[sym] = {
                    'symbol': sym, 'coin_id': r['project_name'].lower(),
                    'chain': 'ethereum', 'price_usd': 0,
                    'alpha_score': score, 'signal': 'WATCH', 'confidence': 40,
                    'reasons': [], 'sources': [], 'is_holding': False,
                    'current_amount': 0, 'entry_price': 0,
                    'trade_usd': 0, 'category': 'PRE_LAUNCH',
                    'url': r.get('url', ''),
                }
            candidates[sym]['sources'].append('pre_launch')
            candidates[sym]['reasons'].append(
                f"{r.get('sale_type','')} | launchpad:{lp}/10 | {r.get('source','')[:20]}")
            candidates[sym]['alpha_score'] = max(candidates[sym]['alpha_score'], score)
            if lp >= 8:
                candidates[sym]['signal'] = 'ACCUMULATE'
                candidates[sym]['confidence'] = max(candidates[sym]['confidence'], 55)
    except Exception:
        pass

    # ── 6. Exchange listings — tier 2 new listings ───────────────────────────
    try:
        listings = pd.read_sql_query(
            """SELECT coin, exchange, exchange_tier, title
               FROM exchange_listings
               WHERE fetched_at >= datetime('now', '-7 days')
               AND exchange_tier = 2
               ORDER BY fetched_at DESC LIMIT 10""", conn)
        for _, r in listings.iterrows():
            coins = (r.get('coin') or '').split(',')
            for coin in coins:
                sym = coin.strip().upper()
                if not sym or sym in MAJORS or len(sym) < 2:
                    continue
                score = 65  # Tier 2 listing = high alpha signal
                if sym not in candidates:
                    candidates[sym] = {
                        'symbol': sym, 'coin_id': sym.lower(),
                        'chain': 'ethereum', 'price_usd': 0,
                        'alpha_score': score, 'signal': 'ACCUMULATE', 'confidence': 60,
                        'reasons': [], 'sources': [], 'is_holding': False,
                        'current_amount': 0, 'entry_price': 0,
                        'trade_usd': 0, 'category': 'NEW_LISTING',
                    }
                candidates[sym]['sources'].append(f'listing:{r["exchange"]}')
                candidates[sym]['reasons'].append(f'listed on {r["exchange"]} (tier {r["exchange_tier"]})')
                candidates[sym]['alpha_score'] = max(candidates[sym]['alpha_score'], score)
                candidates[sym]['signal'] = 'ACCUMULATE'
                candidates[sym]['confidence'] = max(candidates[sym]['confidence'], 60)
    except Exception:
        pass

    # ── 7. Airdrop opportunities — small position to qualify ──────────────────
    try:
        airdrops = pd.read_sql_query(
            """SELECT project_name, effort_level, legitimacy_score, cost_estimate
               FROM airdrop_projects
               WHERE status IN ('AI_SUGGESTED','USER_APPROVED','ACTIVE')
               AND legitimacy_score >= 7
               AND effort_level IN ('FREE_EASY','LOW_COST')
               ORDER BY legitimacy_score DESC LIMIT 5""", conn)
        for _, r in airdrops.iterrows():
            sym = r['project_name'].upper()[:8].replace(' ', '')
            if sym not in candidates:
                candidates[sym] = {
                    'symbol': sym, 'coin_id': r['project_name'].lower(),
                    'chain': 'ethereum', 'price_usd': 0,
                    'alpha_score': 45, 'signal': 'WATCH', 'confidence': 40,
                    'reasons': [], 'sources': [], 'is_holding': False,
                    'current_amount': 0, 'entry_price': 0,
                    'trade_usd': 0, 'category': 'AIRDROP',
                }
            candidates[sym]['sources'].append('airdrop')
            candidates[sym]['reasons'].append(
                f"airdrop opportunity | {r['effort_level']} | legitimacy:{r['legitimacy_score']}/10")
    except Exception:
        pass

    conn.close()

    # ── Boost score for multi-source hits ─────────────────────────────────────
    for sym, c in candidates.items():
        src_count = len(set(c['sources']))
        if src_count >= 3:
            c['alpha_score'] = min(100, c['alpha_score'] + 20)
            c['confidence'] = min(90, c['confidence'] + 15)
            c['reasons'].append(f'cross-source: {src_count} signals')
        elif src_count >= 2:
            c['alpha_score'] = min(100, c['alpha_score'] + 10)
            c['confidence'] = min(85, c['confidence'] + 8)

    return candidates


def evaluate_signals():
    """
    Unified signal evaluation across ALL intelligence sources.
    Returns prioritised list of proposed trades.
    """
    init_agent_tables()
    mode = get_config('mode', 'PAPER')
    enabled = get_config('enabled', 'false') == 'true'
    min_conf = int(get_config('min_signal_confidence', str(MIN_SIGNAL_CONFIDENCE)))
    daily_pnl = get_daily_pnl()

    if daily_pnl <= -float(get_config('daily_loss_limit_usd', str(DAILY_LOSS_LIMIT_USD))):
        print(f"  Agent: daily loss limit hit (${daily_pnl:.0f}) — pausing all trades")
        return []

    candidates = _load_all_candidates()
    if not candidates:
        return []

    # Security check — filter out hacked tokens
    try:
        from security_monitor import get_security_flags
        for sym in list(candidates.keys()):
            c = candidates[sym]
            sec = get_security_flags(coin_id=c['coin_id'], protocol_name=sym)
            if sec.get('hacked') and not sec.get('resolved') and sec.get('amount_usd', 0) >= 100_000:
                c['signal'] = 'SELL' if c['is_holding'] else 'AVOID'
                c['confidence'] = 95
                days = sec.get('days_ago', 0)
                amt = sec.get('amount_usd', 0)
                c['reasons'] = [f'HACKED {days}d ago (${amt/1e6:.1f}M) — unresolved']
                c['alpha_score'] = 0
    except ImportError:
        pass

    # Sort by alpha_score descending
    sorted_candidates = sorted(candidates.values(), key=lambda x: -x['alpha_score'])

    proposals = []
    seen_symbols = set()

    for c in sorted_candidates:
        sym = c['symbol']
        if sym in seen_symbols:
            continue
        seen_symbols.add(sym)

        signal = c['signal']
        confidence = c['confidence']
        alpha_score = c['alpha_score']
        chain = c.get('chain', 'ethereum')
        is_holding = c.get('is_holding', False)
        price = c.get('price_usd', 0)
        amount = c.get('current_amount', 0)
        position_value = price * amount

        # Skip low confidence
        if confidence < min_conf and signal not in ('SELL',):
            continue

        # Skip AVOID signals (hacked non-holdings)
        if signal == 'AVOID':
            continue

        # Symbol sanity check — skip non-ASCII, too long, or scam patterns
        if not sym.isascii() or len(sym) > 10:
            continue
        SCAM_PATTERNS = ['SAFEMOON', 'SQUID', 'ELON', 'SCAM', 'RUG']
        if any(s in sym for s in SCAM_PATTERNS):
            continue

        # Determine action and trade size
        if signal == 'SELL' and is_holding:
            action = 'SELL'
            trade_usd = position_value
        elif signal == 'REDUCE' and is_holding:
            action = 'REDUCE'
            trade_usd = position_value * 0.5
        elif signal in ('BUY', 'ACCUMULATE') and is_holding:
            action = 'ACCUMULATE'
            trade_usd = min(MAX_POSITION_USD, max(50, position_value * 0.25))
        elif signal in ('BUY', 'ACCUMULATE') and not is_holding and alpha_score >= 68:
            action = 'BUY'
            # Size based on alpha score + chain gas awareness
            base_size = min(MAX_POSITION_USD, max(25, alpha_score * 3))
            # ETH mainnet: size based on live gas cost
            if chain == 'ethereum':
                try:
                    from portfolio import get_eth_gas_usd
                    live_gas = get_eth_gas_usd()
                    # Min position so gas is < 5%
                    min_eth = max(200, live_gas / 0.05)
                except Exception:
                    min_eth = 200
                trade_usd = max(min_eth, base_size)
            else:
                trade_usd = base_size
        elif not is_holding and alpha_score >= 75:
            action = 'BUY'
            trade_usd = min(MAX_POSITION_USD, max(25, alpha_score * 2))
        else:
            continue

        if trade_usd < 10:
            continue

        # Liquidity check for DEX gems — don't buy illiquid tokens
        liq = c.get('liquidity_usd', 0)
        if c.get('category') == 'DEX_GEM' and action == 'BUY':
            # Chain-specific liquidity thresholds
            # SOL/BSC: low gas so lower liq OK
            # ETH/BASE: high gas needs real liquidity
            liq_min = {
                'solana':   20_000,
                'bsc':      25_000,
                'base':     30_000,
                'arbitrum': 30_000,
                'ethereum': 40_000,  # lowered — avg ETH gem is $46k
            }.get(chain, 30_000)
            liq_watch = liq_min * 1.5
            if liq < liq_min:
                continue  # skip — too illiquid for this chain
            elif liq < liq_watch:
                trade_usd = min(trade_usd, 25)  # small position for marginal liq

        # Token validation — block BUY for unvalidated or failed gems
        if action == 'BUY' and not is_holding:
            try:
                import sqlite3 as _sq
                _vc = _sq.connect('alphascope.db', timeout=10)
                _vcur = _vc.cursor()
                # Check by contract_address OR symbol
                _contract = c.get('contract_address', '')
                if _contract:
                    _vcur.execute("SELECT verdict, total_score FROM token_validation "
                                  "WHERE contract_address=? AND chain=? "
                                  "AND cached_at >= datetime('now','-2 hours')",
                                  (_contract, chain))
                else:
                    _vcur.execute("SELECT verdict, total_score FROM token_validation "
                                  "WHERE UPPER(symbol)=UPPER(?) AND chain=? "
                                  "AND cached_at >= datetime('now','-2 hours')",
                                  (sym, chain))
                _vrow = _vcur.fetchone()
                _vc.close()
                if _vrow:
                    verdict, val_score = _vrow
                    if verdict == 'AVOID':
                        proposals.append({'action':'SKIP','symbol':sym,
                                          'category':c.get('category',''),
                                          'reason':f'VALIDATION FAILED — AVOID (score:{val_score}/20)',
                                          'trade_usd':trade_usd,'alpha_score':0})
                        continue
                    elif verdict == 'WATCH':
                        trade_usd = min(trade_usd, 25)
                        c['reasons'].append(f'WATCH val:{val_score}/20')
                    elif verdict == 'CAUTION':
                        # For SOL memes: require LP burned for CAUTION
                        if chain == 'solana':
                            try:
                                _lp = _vc.execute(
                                    'SELECT lp_burned FROM token_validation '
                                    'WHERE contract_address=? AND chain=?',
                                    (_contract, chain)).fetchone()
                                if _lp and not _lp[0]:
                                    trade_usd = min(trade_usd, 25)  # LP not burned -- tiny position
                                    c['reasons'].append('CAUTION: LP not burned')
                                else:
                                    trade_usd = min(trade_usd, 75)
                            except Exception:
                                trade_usd = min(trade_usd, 50)
                        else:
                            trade_usd = min(trade_usd, 75)
                        c['reasons'].append(f'CAUTION val:{val_score}/20')
                    # BUY_OK: full size, no cap
                else:
                    # Not validated — run quick check now
                    _contract = c.get('contract_address', '')
                    if _contract and get_config('mode', 'PAPER') == 'PAPER':
                        try:
                            from token_validator import validate_token, init_validation_table
                            init_validation_table()
                            _vr = validate_token(
                                symbol=sym,
                                contract_address=_contract,
                                chain=chain,
                                use_ai=False,  # fast check only
                            )
                            if _vr.get('verdict') == 'AVOID':
                                proposals.append({'action':'SKIP','symbol':sym,
                                                  'category':c.get('category',''),
                                                  'reason':'AVOID — honeypot/scam detected',
                                                  'trade_usd':trade_usd,'alpha_score':0})
                                continue
                            elif _vr.get('verdict') in ('WATCH','CAUTION'):
                                trade_usd = min(trade_usd, 50)
                                c['reasons'].append(f"{_vr['verdict']} val:{_vr['total_score']}/20")
                        except Exception:
                            trade_usd = min(trade_usd, 25)
                            c['reasons'].append('unvalidated — capped $25')
                    else:
                        trade_usd = min(trade_usd, 25)
                        c['reasons'].append('unvalidated — capped $25')
            except Exception:
                pass

        # Social signal boost/block for DEX gems
        if c.get('category') == 'DEX_GEM' and action in ('BUY', 'WATCH'):
            try:
                from social_monitor import get_social_signal
                import sqlite3 as _sq2
                # For SOL memes: only act if signal is fresh (< 15 min)
                if chain in ('solana', 'bsc'):
                    _sc = _sq2.connect('alphascope.db', timeout=10)
                    _sr = _sc.execute(
                        '''SELECT cached_at FROM token_social_cache
                           WHERE symbol=? AND chain=?
                           ORDER BY cached_at DESC LIMIT 1''',
                        (sym, chain)).fetchone()
                    _sc.close()
                    if _sr:
                        from datetime import datetime
                        age_min = (datetime.now() - datetime.fromisoformat(_sr[0])).seconds / 60
                        if age_min > 360:
                            continue  # signal too stale (> 6h)
                social = get_social_signal(sym, chain)
                if social:
                    sig = social.get('signal', 'NEUTRAL')
                    sent = social.get('sentiment', 0)
                    velocity = social.get('velocity', 'UNKNOWN')
                    if sig == 'STRONG_BUY' and velocity == 'ACCELERATING':
                        trade_usd = min(MAX_POSITION_USD, trade_usd * 1.5)  # size up
                        confidence = min(90, confidence + 15)
                        c['reasons'].append(f'social STRONG_BUY accelerating')
                    elif sig in ('SELL', 'WATCH_OUT') or sent < -0.3:
                        action = 'SKIP'
                        proposals.append({'action':'SKIP','symbol':sym,'category':c.get('category',''),
                                          'reason':f'social signal {sig} (sent:{sent:+.2f})',
                                          'trade_usd':trade_usd,'alpha_score':alpha_score})
                        continue
            except ImportError:
                pass

        if action == 'WATCH' and trade_usd == 0:
            continue  # pure watch — no trade

        # Gas check
        # Use live gas for ETH, static for others
        if chain == 'ethereum':
            try:
                from portfolio import get_eth_gas_usd
                gas = get_eth_gas_usd()
            except Exception:
                gas = estimate_gas_price(chain)
        else:
            gas = estimate_gas_price(chain)
        gas_pct = (gas / trade_usd * 100) if trade_usd > 0 else 100

        if gas > float(get_config('max_gas_usd', str(MAX_GAS_USD))) and action != 'SELL':
            proposals.append({
                'action': 'SKIP', 'symbol': sym, 'category': c.get('category', ''),
                'reason': f'gas ${gas:.2f} exceeds limit ${MAX_GAS_USD}',
                'trade_usd': trade_usd, 'alpha_score': alpha_score,
            })
            continue

        # For small CAUTION positions ($50), allow higher gas % threshold
        gas_limit_pct = 20 if trade_usd <= 50 else 8
        if gas_pct > gas_limit_pct and action not in ('SELL', 'REDUCE'):
            proposals.append({
                'action': 'SKIP', 'symbol': sym, 'category': c.get('category', ''),
                'reason': f'gas {gas_pct:.0f}% of trade — use L2 or wait',
                'trade_usd': trade_usd, 'alpha_score': alpha_score,
            })
            continue

        reasons_str = ' | '.join(c['reasons'][:3])
        sources_str = ','.join(set(c['sources']))

        proposals.append({
            'action': action,
            'symbol': sym,
            'coin_id': c.get('coin_id', sym.lower()),
            'chain': chain,
            'category': c.get('category', 'UNKNOWN'),
            'trade_usd': round(trade_usd, 2),
            'price_usd': price,
            'gas_usd': round(gas, 2),
            'confidence': confidence,
            'alpha_score': alpha_score,
            'reasons': reasons_str,
            'sources': sources_str,
            'is_holding': is_holding,
            'url': c.get('url', c.get('dex_url', '')),
            'mode': mode,
            'executable': enabled and mode == 'LIVE',
        })

        if len(proposals) >= 20:  # cap proposals list
            break

    # Sort final list: SELL first (risk management), then by alpha_score
    proposals.sort(key=lambda x: (
        0 if x['action'] == 'SELL' else 1 if x['action'] == 'REDUCE' else 2,
        -x.get('alpha_score', 0)
    ))

    return proposals



def get_daily_pnl():
    """Calculate today's realized P&L from agent trades."""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""SELECT COALESCE(SUM(pnl_usd), 0) FROM agent_trades
                     WHERE created_at >= datetime('now', '-24 hours')
                     AND status = 'EXECUTED'""")
        pnl = c.fetchone()[0] or 0
        conn.close()
        return float(pnl)
    except Exception:
        return 0


def record_trade(proposal, status='PAPER_EXECUTED', tx_hash='', pnl=0):
    """Record a trade (paper or live) to the agent_trades table."""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute('''INSERT INTO agent_trades
        (symbol, coin_id, chain, action, amount_usd, amount_tokens,
         price_usd, gas_usd, slippage_pct, signal, signal_confidence,
         mode, tx_hash, status, pnl_usd, notes, created_at, executed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (proposal.get('symbol'), proposal.get('coin_id'), proposal.get('chain'),
         proposal.get('action'), proposal.get('trade_usd', 0),
         proposal.get('trade_usd', 0) / max(proposal.get('price_usd', 1), 0.0001),
         proposal.get('price_usd', 0), proposal.get('gas_usd', 0),
         0.1, proposal.get('action'), proposal.get('confidence', 0),
         proposal.get('mode', 'PAPER'), tx_hash, status,
         pnl, proposal.get('reasons', ''), now, now))
    conn.commit()
    conn.close()


def run_agent(dry_run=True):
    """
    Main agent loop. Evaluates signals and proposes/executes trades.
    dry_run=True: print proposals only, do not record
    dry_run=False: record paper trades to DB
    """
    init_agent_tables()
    mode = get_config('mode', 'PAPER')
    enabled = get_config('enabled', 'false') == 'true'

    print(f"\n  Agent mode: {mode} | enabled: {enabled}")

    proposals = evaluate_signals()
    if not proposals:
        print("  Agent: nothing to do")
        return

    print(f"  Agent: {len(proposals)} proposals")
    for p in proposals:
        action = p['action']
        sym = p['symbol']
        if action == 'SKIP':
            print(f"    ⏭  SKIP {sym} — {p['reason']}")
            continue

        emoji = {'BUY': '🟢', 'ACCUMULATE': '🟩', 'SELL': '🔴', 'REDUCE': '🔶'}.get(action, '⚪')
        exec_tag = '→ WOULD EXECUTE' if p.get('executable') else '→ PAPER'
        print(f"    {emoji} {action} {sym} ${p['trade_usd']:.0f} | "
              f"gas:${p['gas_usd']:.2f} | conf:{p['confidence']}% | {exec_tag}")
        print(f"       Reasons: {p.get('reasons','')[:80]}")

        if not dry_run:
            record_trade(p, status='PAPER_EXECUTED' if mode == 'PAPER' else 'PENDING')

    if mode == 'PAPER':
        print(f"\n  Running in PAPER mode. To enable live trading:")
        print(f"    from wallet_agent import set_config")
        print(f"    set_config('wallet_address', '0xYourAddress')")
        print(f"    set_config('mode', 'LIVE')")
        print(f"    set_config('enabled', 'true')  # ← only when ready")


def get_airdrop_actions():
    """
    Returns a list of concrete on-chain actions needed to qualify for tracked airdrops.
    This is the input to the future airdrop executor agent.
    """
    try:
        conn = get_db()
        import pandas as pd
        df = pd.read_sql_query(
            """SELECT project_name, qualification_steps, effort_level,
                      cost_estimate, deadline, legitimacy_score
               FROM airdrop_projects
               WHERE status IN ('AI_SUGGESTED', 'USER_APPROVED', 'ACTIVE')
               AND legitimacy_score >= 7
               AND effort_level IN ('FREE_EASY', 'LOW_COST')
               ORDER BY legitimacy_score DESC""",
            conn
        )
        conn.close()

        actions = []
        for _, row in df.iterrows():
            steps = row.get('qualification_steps', '') or ''
            # Parse numbered steps into discrete actions
            step_list = [s.strip() for s in steps.split('\n') if re.match(r'^\d+\.', s.strip())]
            actions.append({
                'project': row['project_name'],
                'effort': row['effort_level'],
                'score': row['legitimacy_score'],
                'deadline': row.get('deadline', ''),
                'steps': step_list,
                'raw_steps': steps,
            })
        return actions
    except Exception as e:
        return []


def print_airdrop_queue():
    """Print actionable airdrop queue for manual execution."""
    import re
    actions = get_airdrop_actions()
    if not actions:
        print("  No qualifying airdrops in queue")
        return
    print(f"\n  Airdrop queue ({len(actions)} projects):")
    for a in actions:
        print(f"\n  {'✅' if a['score'] >= 8 else '⭐'} {a['project']} "
              f"[{a['effort']}] score:{a['score']}/10")
        if a['deadline']:
            print(f"    Deadline: {a['deadline']}")
        for i, step in enumerate(a['steps'][:5], 1):
            print(f"    {step}")
        if not a['steps'] and a['raw_steps']:
            print(f"    {a['raw_steps'][:150]}")


if __name__ == '__main__':
    import re
    print("AlphaScope — Wallet Agent v1.0")
    print("=" * 50)
    init_agent_tables()
    print(f"Mode: {get_config('mode', 'PAPER')}")
    print(f"Enabled: {get_config('enabled', 'false')}")
    print()
    run_agent(dry_run=True)
    print()
    print_airdrop_queue()
