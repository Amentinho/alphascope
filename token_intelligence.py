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

def _env(key, default=''):
    val = os.environ.get(key, '')
    if val:
        return val
    try:
        with open('.env') as _env_f:
            for _line in _env_f:
                _line = _line.strip()
                if _line.startswith(f'{key}='):
                    return _line.split('=', 1)[1].strip()
    except Exception:
        pass
    return default

ENABLE_EXTERNAL_NEWS_FETCH = _env('ENABLE_EXTERNAL_NEWS_FETCH', 'true').lower() == 'true'
ENABLE_ESTABLISHED_TWITTER_FETCH = _env('ENABLE_ESTABLISHED_TWITTER_FETCH',
                                        _env('ENABLE_TWITTER_FETCH', 'false')).lower() == 'true'
ENABLE_ESTABLISHED_AI = _env('ENABLE_ESTABLISHED_AI', 'false').lower() == 'true'
ESTABLISHED_TWITTER_MAX_PER_RUN = int(_env('ESTABLISHED_TWITTER_MAX_PER_RUN', '8'))
ESTABLISHED_AI_MAX_PER_RUN = int(_env('ESTABLISHED_AI_MAX_PER_RUN', '4'))
OPENAI_API_KEY = _env('OPENAI_API_KEY', '')
TWITTER_API_KEY = _env('TWITTER_API_KEY', '')

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
    if not ENABLE_EXTERNAL_NEWS_FETCH:
        return 0.0
    # CryptoCompare news is unauthenticated and usually has broader coverage
    # than CryptoPanic's free endpoint.
    try:
        rcc = requests.get(
            'https://min-api.cryptocompare.com/data/v2/news/',
            params={'lang': 'EN', 'categories': symbol},
            timeout=8)
        if rcc.status_code == 200:
            articles = rcc.json().get('Data', [])[:20]
            if articles:
                pos_words = ('surge', 'rally', 'partnership', 'upgrade', 'launch',
                             'adoption', 'record', 'bullish', 'growth', 'integrat')
                neg_words = ('hack', 'exploit', 'lawsuit', 'sec ', 'crash', 'dump',
                             'bearish', 'outage', 'fraud', 'delay')
                score = 0
                counted = 0
                for a in articles:
                    txt = f"{a.get('title','')} {a.get('body','')}".lower()
                    if symbol.lower() not in txt and cg_id.replace('-', ' ') not in txt:
                        continue
                    pos = sum(1 for w in pos_words if w in txt)
                    neg = sum(1 for w in neg_words if w in txt)
                    if pos or neg:
                        score += (pos - neg) / max(pos + neg, 1)
                        counted += 1
                if counted:
                    return max(-1.0, min(1.0, score / counted))
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
def _score_twitter(conn, symbol: str, project_name='') -> float:
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
    if ENABLE_ESTABLISHED_TWITTER_FETCH and TWITTER_API_KEY:
        try:
            # Use the repo's social monitor so cache/credit behavior is shared.
            from social_monitor import tier3_scan, _load_config
            import social_monitor as _sm
            cfg = _load_config()
            _sm.TWITTER_API_KEY = cfg.get('twitter_key') or TWITTER_API_KEY
            _sm.TWITTER_ENABLED = True
            result = tier3_scan(symbol, chain='established',
                                project_name=project_name or symbol)
            if result and int(result.get('tweet_count') or 0) >= 3:
                return max(-1.0, min(1.0, float(result.get('sentiment') or 0)))
        except Exception:
            pass
    return 0.0


def _ai_cache_get(conn, symbol):
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS established_ai_cache (
            symbol TEXT PRIMARY KEY,
            ai_score REAL,
            summary TEXT,
            cached_at TEXT
        )''')
        row = conn.execute("""
            SELECT ai_score, summary FROM established_ai_cache
            WHERE symbol=? AND cached_at >= datetime('now', '-2 hours')
        """, (symbol,)).fetchone()
        if row:
            return float(row[0] or 0), row[1] or 'cached'
    except Exception:
        pass
    return None


def _ai_cache_set(conn, symbol, score, summary):
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS established_ai_cache (
            symbol TEXT PRIMARY KEY,
            ai_score REAL,
            summary TEXT,
            cached_at TEXT
        )''')
        conn.execute("""
            INSERT OR REPLACE INTO established_ai_cache
            (symbol, ai_score, summary, cached_at) VALUES (?,?,?,datetime('now'))
        """, (symbol, float(score), summary[:500]))
        conn.commit()
    except Exception:
        pass


def _score_established_ai(conn, symbol, meta, context):
    """Optional paid OpenAI score for established tokens, cached for 2h."""
    if not ENABLE_ESTABLISHED_AI or not OPENAI_API_KEY:
        return 0.0, 'AI off'
    cached = _ai_cache_get(conn, symbol)
    if cached:
        return cached
    prompt = f"""You are a crypto portfolio risk analyst. Score {symbol} for a small short-term allocation.

Context:
- 24h change: {context.get('chg_24h', 0):+.2f}%
- 7d change: {context.get('chg_7d', 0):+.2f}%
- Fear & Greed: {context.get('fear_greed', 50)}
- News sentiment score: {context.get('news_score', 0):+.2f}
- Twitter sentiment score: {context.get('twitter_score', 0):+.2f}
- Reddit sentiment score: {context.get('reddit_score', 0):+.2f}
- CoinGecko trending: {context.get('trending', False)}

Return JSON only:
{{"score": <number from -1 to 1>, "summary": "one short reason"}}
Positive means likely better risk-adjusted upside soon; negative means avoid."""
    try:
        r = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers={'Authorization': f'Bearer {OPENAI_API_KEY}',
                     'Content-Type': 'application/json'},
            json={
                'model': _env('OPENAI_MODEL', 'gpt-4o-mini'),
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.1,
                'max_tokens': 120,
            },
            timeout=20)
        if r.status_code == 200:
            text = r.json()['choices'][0]['message']['content'].strip()
            text = text.replace('```json', '').replace('```', '').strip()
            data = json.loads(text)
            score = max(-1.0, min(1.0, float(data.get('score', 0) or 0)))
            summary = str(data.get('summary', 'AI scored'))
            _ai_cache_set(conn, symbol, score, summary)
            return score, summary
    except Exception as e:
        return 0.0, f'AI error: {str(e)[:60]}'
    return 0.0, 'AI unavailable'


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
    twitter_fetches = 0
    ai_fetches = 0
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
            allow_twitter_fetch = (
                ENABLE_ESTABLISHED_TWITTER_FETCH
                and twitter_fetches < ESTABLISHED_TWITTER_MAX_PER_RUN
            )
            if not allow_twitter_fetch:
                old = globals().get('ENABLE_ESTABLISHED_TWITTER_FETCH', False)
                globals()['ENABLE_ESTABLISHED_TWITTER_FETCH'] = False
                twitter_score = _score_twitter(conn, symbol, meta.get('cg_id', symbol))
                globals()['ENABLE_ESTABLISHED_TWITTER_FETCH'] = old
            else:
                twitter_score = _score_twitter(conn, symbol, meta.get('cg_id', symbol))
                twitter_fetches += 1

            ai_score, ai_summary = 0.0, 'AI off'
            if ENABLE_ESTABLISHED_AI and ai_fetches < ESTABLISHED_AI_MAX_PER_RUN:
                ai_score, ai_summary = _score_established_ai(conn, symbol, meta, {
                    'chg_24h': chg_24h,
                    'chg_7d': chg_7d,
                    'fear_greed': fg_value,
                    'news_score': news_score,
                    'twitter_score': twitter_score,
                    'reddit_score': reddit_score,
                    'trending': symbol in trending_syms,
                })
                ai_fetches += 1

            # Composite weighted score
            base_composite = (
                WEIGHTS['fear_greed']     * fg_score +
                WEIGHTS['price_momentum'] * mom_score +
                WEIGHTS['trending']       * trend_score +
                WEIGHTS['news']           * news_score +
                WEIGHTS['reddit']         * reddit_score +
                WEIGHTS['twitter']        * twitter_score
            )
            composite = base_composite
            if ENABLE_ESTABLISHED_AI and OPENAI_API_KEY:
                composite = 0.85 * base_composite + 0.15 * ai_score
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
                     f"news:{news_score:+.2f} reddit:{reddit_score:+.2f} "
                     f"tw:{twitter_score:+.2f} ai:{ai_score:+.2f} {ai_summary[:50]}"
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
                  f"({chg_24h:+.1f}%/24h, news:{news_score:+.2f}, "
                  f"tw:{twitter_score:+.2f}, ai:{ai_score:+.2f})")
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
