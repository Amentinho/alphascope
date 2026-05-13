"""
AlphaScope Token Intelligence v1.0
=====================================
Builds a real-time composite sentiment score for established tokens
from multiple data sources:

  1. Fear & Greed Index (macro risk-on/off)
  2. CoinGecko price momentum (24h/7d change)
  3. CoinGecko trending (volume spike signal)
  4. CryptoCompare news sentiment (AI-scored headlines)
  5. Reddit mention velocity (r/cryptocurrency, r/ethfinance etc.)
  6. Twitter/X sentiment (via social_monitor signals table)
  7. On-chain metrics (price vs 7d MA from Binance)
  8. Funding rates proxy (extreme funding = mean revert signal)

Each source produces a score in [-1, +1].
Final composite = weighted average → stored in coin_buzz table.

Run standalone: python3 token_intelligence.py
Run from sim:   from token_intelligence import run_token_intelligence
"""

import os, time, json, sqlite3, requests
from datetime import datetime, timedelta

MAIN_DB = 'alphascope.db'
ENABLE_EXTERNAL_NEWS_FETCH = os.environ.get('ENABLE_EXTERNAL_NEWS_FETCH', 'false').lower() == 'true'
try:
    if not ENABLE_EXTERNAL_NEWS_FETCH:
        with open('.env') as _env_f:
            for _line in _env_f:
                if _line.strip().startswith('ENABLE_EXTERNAL_NEWS_FETCH='):
                    ENABLE_EXTERNAL_NEWS_FETCH = _line.split('=', 1)[1].strip().lower() == 'true'
                    break
except Exception:
    pass

# ── Established tokens to track ───────────────────────────────────────────────
# Tokens marked 'macro_only=True' are scored for regime filtering but never
# traded (BTC has its own chain; HYPE is Hyperliquid L1 native).
# All others must have a matching entry in simulation.py _CONTRACT_REGISTRY.
TRACKED_TOKENS = {
    # ── SOL ecosystem ─────────────────────────────────────────────────────────
    'SOL':  {'cg_id': 'solana',          'binance': 'SOLUSDT',  'reddit': 'solana'},
    'JUP':  {'cg_id': 'jupiter-ag',      'binance': 'JUPUSDT',  'reddit': 'solana'},
    'RAY':  {'cg_id': 'raydium',         'binance': 'RAYUSDT',  'reddit': 'raydium'},
    'BONK': {'cg_id': 'bonk',            'binance': 'BONKUSDT', 'reddit': 'solana'},
    'WIF':  {'cg_id': 'dogwifcoin',      'binance': 'WIFUSDT',  'reddit': 'solana'},
    'PYTH': {'cg_id': 'pyth-network',    'binance': 'PYTHUSDT', 'reddit': 'solana'},
    # ── ETH mainnet ───────────────────────────────────────────────────────────
    'ETH':  {'cg_id': 'ethereum',        'binance': 'ETHUSDT',  'reddit': 'ethfinance'},
    'LINK': {'cg_id': 'chainlink',       'binance': 'LINKUSDT', 'reddit': 'Chainlink'},
    'AAVE': {'cg_id': 'aave',            'binance': 'AAVEUSDT', 'reddit': 'Aave'},
    'UNI':  {'cg_id': 'uniswap',         'binance': 'UNIUSDT',  'reddit': 'UniSwap'},
    'ONDO': {'cg_id': 'ondo-finance',    'binance': 'ONDOUSDT', 'reddit': 'CryptoCurrency'},
    'ENA':  {'cg_id': 'ethena',          'binance': 'ENAUSDT',  'reddit': 'ethfinance'},
    'LDO':  {'cg_id': 'lido-dao',        'binance': 'LDOUSDT',  'reddit': 'ethfinance'},
    'PENDLE': {'cg_id': 'pendle',        'binance': 'PENDLEUSDT','reddit': 'defi'},
    'CRV':  {'cg_id': 'curve-dao-token', 'binance': 'CRVUSDT',  'reddit': 'defi'},
    'FET':  {'cg_id': 'fetch-ai',        'binance': 'FETUSDT',  'reddit': 'artificial'},
    'NEAR': {'cg_id': 'near',            'binance': 'NEARUSDT', 'reddit': 'nearprotocol'},
    'PEPE': {'cg_id': 'pepe',            'binance': 'PEPEUSDT', 'reddit': 'CryptoMoonShots'},
    'SHIB': {'cg_id': 'shiba-inu',       'binance': 'SHIBUSDT', 'reddit': 'SHIBArmy'},
    # ── BASE ecosystem ────────────────────────────────────────────────────────
    'AERO': {'cg_id': 'aerodrome-finance','binance': 'AEROUSDT', 'reddit': 'base'},
    'VIRTUAL': {'cg_id': 'virtual-protocol','binance': 'VIRTUALUSDT', 'reddit': 'base'},
    # ── Macro regime signals only — not traded on any supported chain ─────────
    'BTC':  {'cg_id': 'bitcoin',         'binance': 'BTCUSDT',  'reddit': 'Bitcoin',
             'macro_only': True},   # BTC lives on its own chain — regime signal only
    'HYPE': {'cg_id': 'hyperliquid',     'binance': 'HYPEUSDT', 'reddit': 'hyperliquid',
             'macro_only': True},   # Hyperliquid L1 native — no DEX on SOL/BASE/ETH
    'SUI':  {'cg_id': 'sui',             'binance': 'SUIUSDT',  'reddit': 'sui',
             'macro_only': True},
    'AVAX': {'cg_id': 'avalanche-2',     'binance': 'AVAXUSDT', 'reddit': 'avalanche',
             'macro_only': True},
    'INJ':  {'cg_id': 'injective-protocol','binance': 'INJUSDT','reddit': 'injective',
             'macro_only': True},
}

# Source weights in composite score
WEIGHTS = {
    'fear_greed':    0.20,  # macro context — strongest single signal
    'price_momentum':0.25,  # price action doesn't lie
    'trending':      0.10,  # coingecko trending = volume spike
    'news':          0.20,  # headline sentiment
    'reddit':        0.10,  # community mood
    'twitter':       0.15,  # fastest signal
}

# ── DB helpers ─────────────────────────────────────────────────────────────────
def _db():
    conn = sqlite3.connect(MAIN_DB, timeout=10)
    conn.execute('PRAGMA journal_mode=WAL')
    # Ensure token_intelligence table exists
    conn.execute('''CREATE TABLE IF NOT EXISTS token_intelligence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        composite_score REAL,
        fear_greed_score REAL,
        momentum_score REAL,
        trending_score REAL,
        news_score REAL,
        reddit_score REAL,
        twitter_score REAL,
        fear_greed_value INTEGER,
        price_24h_change REAL,
        price_7d_change REAL,
        volume_24h REAL,
        signal TEXT,
        confidence INTEGER,
        notes TEXT,
        fetched_at TEXT DEFAULT (datetime('now')))''')
    conn.commit()
    return conn


def _write_coin_buzz(conn, symbol, composite, signal, notes):
    """Write composite score to coin_buzz so _load_established_proposals can read it."""
    now = datetime.now().isoformat()
    # Convert composite [-1,1] to sentiment-compatible values
    mention_count = 10 if abs(composite) > 0.3 else 5
    # Use INSERT OR IGNORE to handle missing 'source' column gracefully
    if not ENABLE_EXTERNAL_NEWS_FETCH:
        return 0.0
    try:
        conn.execute("""
            INSERT INTO coin_buzz (coin, mention_count, avg_sentiment, source, fetched_at)
            VALUES (?,?,?,'token_intelligence',?)
        """, (symbol, mention_count, composite, now))
    except Exception:
        conn.execute("""
            INSERT INTO coin_buzz (coin, mention_count, avg_sentiment, fetched_at)
            VALUES (?,?,?,?)
        """, (symbol, mention_count, composite, now))
    conn.commit()


# ── Source 1: Fear & Greed ─────────────────────────────────────────────────────
def _score_fear_greed() -> tuple:
    """Returns (score, fg_value). Score in [-1, +1]."""
    try:
        r = requests.get('https://api.alternative.me/fng/?limit=1', timeout=6)
        if r.status_code == 200:
            fg = int(r.json()['data'][0]['value'])
            # 0-100 → normalize: <25 fear (buy signal), >75 greed (sell signal)
            if fg < 25:
                score = 0.4   # extreme fear = buy dip
            elif fg < 40:
                score = 0.1   # fear = mild positive
            elif fg < 60:
                score = 0.0   # neutral
            elif fg < 75:
                score = -0.1  # greed = mild caution
            else:
                score = -0.4  # extreme greed = sell signal
            return score, fg
    except Exception as e:
        print(f"    [intel] F&G error: {e}")
    return 0.0, 50


# ── Source 2: Price momentum from Binance ─────────────────────────────────────
def _score_price_momentum(symbol: str, binance_pair: str) -> tuple:
    """Returns (score, 24h_change, 7d_change).
    Uses Binance 24h ticker + 7d klines for MA comparison."""
    try:
        # 24h stats
        r = requests.get(
            f'https://api.binance.com/api/v3/ticker/24hr?symbol={binance_pair}',
            timeout=5)
        if r.status_code == 200:
            d = r.json()
            chg_24h = float(d.get('priceChangePercent', 0) or 0)
            volume = float(d.get('quoteVolume', 0) or 0)
            # 7d klines for trend
            r2 = requests.get(
                'https://api.binance.com/api/v3/klines',
                params={'symbol': binance_pair, 'interval': '1d', 'limit': 7},
                timeout=5)
            chg_7d = 0
            if r2.status_code == 200:
                klines = r2.json()
                if len(klines) >= 2:
                    open_7d = float(klines[0][1])
                    close_now = float(klines[-1][4])
                    chg_7d = (close_now - open_7d) / open_7d * 100 if open_7d > 0 else 0

            # Score: momentum is good up to a point, then reversal risk
            if chg_24h > 15:
                score = -0.2  # overbought short term
            elif chg_24h > 5:
                score = 0.3   # strong uptrend
            elif chg_24h > 1:
                score = 0.2   # mild uptrend
            elif chg_24h > -1:
                score = 0.0   # flat
            elif chg_24h > -5:
                score = -0.1  # mild downtrend
            elif chg_24h > -15:
                score = -0.3  # downtrend — potential buy if fear also low
            else:
                score = -0.5  # crash

            # 7d trend modulates: rising 7d softens negative 24h (dip in uptrend)
            if chg_7d > 10 and chg_24h < -3:
                score += 0.2  # dip in strong uptrend = buy signal
            elif chg_7d < -10 and chg_24h > 3:
                score -= 0.2  # bounce in downtrend = sell signal

            score = max(-1.0, min(1.0, score))
            return score, chg_24h, chg_7d
    except Exception as e:
        print(f"    [intel] momentum error {symbol}: {e}")
    return 0.0, 0.0, 0.0


# ── Source 3: CoinGecko trending ──────────────────────────────────────────────
def _get_trending_symbols() -> set:
    """Returns set of symbols currently trending on CoinGecko."""
    try:
        r = requests.get(
            'https://api.coingecko.com/api/v3/search/trending',
            timeout=8)
        if r.status_code == 200:
            coins = r.json().get('coins', [])
            return {c['item']['symbol'].upper() for c in coins[:15]}
    except Exception:
        pass
    return set()


# ── Source 4: News sentiment via CryptoPanic ──────────────────────────────────
def _score_news(symbol: str, cg_id: str, conn=None) -> float:
    """Score news sentiment from CryptoPanic (free, no key needed for basic)."""
    if conn is not None:
        try:
            rows = conn.execute("""
                SELECT sentiment_score, engagement
                FROM signals
                WHERE signal_type IN ('NEWS','WHALE','PARTNERSHIP','LISTING')
                AND (UPPER(coin)=? OR UPPER(coin) LIKE ? OR UPPER(title) LIKE ?)
                AND fetched_at >= datetime('now', '-12 hours')
                ORDER BY engagement DESC LIMIT 20
            """, (symbol, f'%{symbol}%', f'%{symbol}%')).fetchall()
            if rows:
                weighted = 0.0
                total_w = 0.0
                for sent, engagement in rows:
                    w = max(float(engagement or 0), 1.0)
                    weighted += float(sent or 0) * w
                    total_w += w
                return max(-1.0, min(1.0, weighted / max(total_w, 1)))
        except Exception:
            pass
    try:
        r = requests.get(
            'https://cryptopanic.com/api/free/v1/posts/',
            params={'auth_token': 'free', 'currencies': symbol,
                    'filter': 'important', 'kind': 'news'},
            timeout=8)
        if r.status_code == 200:
            results = r.json().get('results', [])[:10]
            if not results:
                return 0.0
            # Each post has votes: positive, negative, important, liked, disliked
            pos = sum(r.get('votes', {}).get('positive', 0) for r in results)
            neg = sum(r.get('votes', {}).get('negative', 0) for r in results)
            liked = sum(r.get('votes', {}).get('liked', 0) for r in results)
            disliked = sum(r.get('votes', {}).get('disliked', 0) for r in results)
            total = pos + neg + liked + disliked
            if total == 0:
                return 0.0
            score = (pos + liked - neg - disliked) / total
            return max(-1.0, min(1.0, score))
    except Exception as e:
        print(f"    [intel] news error {symbol}: {e}")
    return 0.0


# ── Source 5: Reddit mention velocity ────────────────────────────────────────
def _score_reddit(conn, symbol: str, subreddit: str) -> float:
    """Read recent Reddit mentions from signals table (already fetched by fetcher.py)."""
    try:
        rows = conn.execute("""
            SELECT sentiment_score, engagement FROM signals
            WHERE source='reddit'
            AND (UPPER(coin) LIKE ? OR source_detail LIKE ?)
            AND fetched_at >= datetime('now', '-6 hours')
            ORDER BY engagement DESC LIMIT 20
        """, (f'%{symbol}%', f'%{subreddit}%')).fetchall()

        if not rows:
            return 0.0
        avg_sent = sum(float(r[0] or 0) for r in rows) / len(rows)
        return max(-1.0, min(1.0, avg_sent))
    except Exception:
        return 0.0


# ── Source 6: Twitter sentiment ───────────────────────────────────────────────
def _score_twitter(conn, symbol: str) -> float:
    """Read Twitter sentiment from signals table."""
    try:
        row = conn.execute("""
            SELECT sentiment_score, engagement FROM signals
            WHERE source='twitter' AND UPPER(coin)=?
            AND fetched_at >= datetime('now', '-3 hours')
            ORDER BY fetched_at DESC LIMIT 1
        """, (symbol,)).fetchone()
        if row:
            return max(-1.0, min(1.0, float(row[0] or 0)))
    except Exception:
        pass
    try:
        row = conn.execute("""
            SELECT sentiment_score, tweet_count, total_engagement
            FROM x_sentiment
            WHERE cashtag=?
            AND fetched_at >= datetime('now', '-3 hours')
            ORDER BY fetched_at DESC LIMIT 1
        """, (f'${symbol}',)).fetchone()
        if row:
            sent = float(row[0] or 0)
            tweets = int(row[1] or 0)
            if tweets >= 3:
                return max(-1.0, min(1.0, sent))
    except Exception:
        pass
    try:
        row = conn.execute("""
            SELECT sentiment_score, tweet_count
            FROM token_social_cache
            WHERE symbol=?
            AND cached_at >= datetime('now', '-3 hours')
            ORDER BY cached_at DESC LIMIT 1
        """, (symbol,)).fetchone()
        if row and int(row[1] or 0) >= 3:
            return max(-1.0, min(1.0, float(row[0] or 0)))
    except Exception:
        pass
    return 0.0


# ── Main intelligence run ─────────────────────────────────────────────────────
def run_token_intelligence():
    """
    Build composite sentiment scores for all TRACKED_TOKENS.
    Writes to token_intelligence table AND coin_buzz table.
    Establishes the data foundation for established token proposals.
    """
    print("  📡 Token intelligence: fetching multi-source sentiment...")
    conn = _db()
    now = datetime.now().isoformat()

    # Shared data fetched once
    fg_score, fg_value = _score_fear_greed()
    trending_syms = _get_trending_symbols()
    print(f"    F&G: {fg_value}/100 ({'fear' if fg_value<40 else 'greed' if fg_value>60 else 'neutral'})")
    print(f"    Trending: {', '.join(list(trending_syms)[:8])}")

    results = []
    for symbol, meta in TRACKED_TOKENS.items():
        try:
            # Price momentum
            mom_score, chg_24h, chg_7d = _score_price_momentum(symbol, meta['binance'])
            time.sleep(0.05)  # rate limit Binance without blocking quick refresh

            # CoinGecko trending
            trend_score = 0.5 if symbol in trending_syms else 0.0

            # News
            news_score = _score_news(symbol, meta['cg_id'], conn=conn)
            time.sleep(0.05)

            # Reddit + Twitter from DB
            reddit_score = _score_reddit(conn, symbol, meta['reddit'])
            twitter_score = _score_twitter(conn, symbol)

            # Composite weighted score
            composite = (
                WEIGHTS['fear_greed']     * fg_score +
                WEIGHTS['price_momentum'] * mom_score +
                WEIGHTS['trending']       * trend_score +
                WEIGHTS['news']           * news_score +
                WEIGHTS['reddit']         * reddit_score +
                WEIGHTS['twitter']        * twitter_score
            )
            composite = round(max(-1.0, min(1.0, composite)), 3)

            # Signal classification
            if composite >= 0.3:
                signal = 'STRONG_BUY'
            elif composite >= 0.1:
                signal = 'BUY'
            elif composite <= -0.3:
                signal = 'STRONG_SELL'
            elif composite <= -0.1:
                signal = 'SELL'
            else:
                signal = 'NEUTRAL'

            confidence = int(min(90, 40 + abs(composite) * 100))

            notes = (f"F&G:{fg_value} 24h:{chg_24h:+.1f}% 7d:{chg_7d:+.1f}% "
                     f"trend:{'YES' if symbol in trending_syms else 'no'} "
                     f"news:{news_score:+.2f} reddit:{reddit_score:+.2f} tw:{twitter_score:+.2f}"
                     + (" [macro_only]" if meta.get('macro_only') else ""))

            # Write to token_intelligence table
            conn.execute("""
                INSERT INTO token_intelligence
                (symbol, composite_score, fear_greed_score, momentum_score,
                 trending_score, news_score, reddit_score, twitter_score,
                 fear_greed_value, price_24h_change, price_7d_change,
                 signal, confidence, notes, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (symbol, composite, fg_score, mom_score, trend_score,
                  news_score, reddit_score, twitter_score,
                  fg_value, chg_24h, chg_7d, signal, confidence, notes, now))

            # Write to coin_buzz so established proposals can use it
            _write_coin_buzz(conn, symbol, composite, signal, notes)

            arrow = '▲' if composite > 0.1 else ('▼' if composite < -0.1 else '→')
            print(f"    {arrow} {symbol:<5} {signal:<12} score:{composite:+.2f} "
                  f"({chg_24h:+.1f}%/24h, news:{news_score:+.2f})")
            results.append((symbol, composite, signal))

        except Exception as e:
            print(f"    [intel] {symbol} error: {e}")
            continue

    conn.commit()
    conn.close()

    # Summary
    buys = [(s, c) for s, c, sig in results if sig in ('BUY', 'STRONG_BUY')]
    sells = [(s, c) for s, c, sig in results if sig in ('SELL', 'STRONG_SELL')]
    if buys:
        print(f"  📈 Buy signals: {', '.join(f'{s}({c:+.2f})' for s,c in buys)}")
    if sells:
        print(f"  📉 Sell signals: {', '.join(f'{s}({c:+.2f})' for s,c in sells)}")

    return results


def get_token_signal(symbol: str) -> tuple:
    """
    Get latest composite signal for a symbol.
    Returns (composite_score, signal, confidence, notes) or (0, 'NEUTRAL', 50, '')
    """
    try:
        conn = _db()
        row = conn.execute("""
            SELECT composite_score, signal, confidence, notes
            FROM token_intelligence
            WHERE symbol=? AND fetched_at >= datetime('now', '-2 hours')
            ORDER BY fetched_at DESC LIMIT 1
        """, (symbol,)).fetchone()
        conn.close()
        if row:
            return float(row[0]), row[1], int(row[2]), row[3]
    except Exception:
        pass
    return 0.0, 'NEUTRAL', 50, ''


if __name__ == '__main__':
    print("=== AlphaScope Token Intelligence ===")
    results = run_token_intelligence()
    print(f"\nProcessed {len(results)} tokens")
