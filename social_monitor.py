"""
AlphaScope — Social Monitor v1.0
Tiered X/Twitter monitoring with aggressive caching to minimize API credits.

Tier 1 — one-time scan on new gem detection (1 credit per gem)
Tier 2 — fast 3-min polling for SOL memes under 2h old that passed Tier 1 (max 10 polls)
Tier 3 — hourly for real projects on ETH/ARB/BASE

Cache: token_social_cache table — never re-query within TTL
Expected usage: ~30-50 API calls/day
"""

import sqlite3
import requests
import json
import re
import time
from datetime import datetime, timezone

def _load_config():
    """Load API keys and feature flags from .env"""
    config = {
        'twitter_key': 'new1_1597ef833361479ba82c88ff32b2fb8c',
        'twitter_enabled': False,
        'openai_key': '',
    }
    try:
        with open('.env') as f:
            for line in f:
                line = line.strip()
                if line.startswith('TWITTER_API_KEY='):
                    config['twitter_key'] = line.split('=',1)[1].strip()
                elif line.startswith('ENABLE_TWITTER_FETCH='):
                    config['twitter_enabled'] = line.split('=',1)[1].strip().lower() == 'true'
                elif line.startswith('OPENAI_API_KEY='):
                    config['openai_key'] = line.split('=',1)[1].strip()
    except Exception:
        pass
    return config

_CONFIG = _load_config()
TWITTER_API_KEY = _CONFIG['twitter_key']
TWITTER_ENABLED = _CONFIG['twitter_enabled']

# Credit budget tracking (resets per session)
_credits_used = 0
MAX_CREDITS_PER_HOUR = 30   # conservative limit
MAX_CREDITS_PER_SESSION = 100

def _can_use_twitter(tier=1):
    """Check if we should use Twitter based on budget and config."""
    global _credits_used
    if not TWITTER_ENABLED:
        return False
    if _credits_used >= MAX_CREDITS_PER_SESSION:
        return False
    return True

def _record_credit_use(n=1):
    global _credits_used
    _credits_used += n
TWITTER_SEARCH  = "https://api.twitterapi.io/twitter/tweet/advanced_search"

# TTL by tier (minutes)
TTL_TIER1 = 30    # initial scan — cache 30 min
TTL_TIER2 = 3     # fast poll — refresh every 3 min
TTL_TIER3 = 60    # slow poll — refresh every hour

# Tier 2 limits
TIER2_MAX_POLLS = 10    # max 10 polls per gem (= 30 min window)
TIER2_MAX_AGE_H = 2     # only poll gems under 2h old
TIER2_CHAINS    = {'solana', 'bsc'}  # meme chains only


def get_db():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_social_tables():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS token_social_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        chain TEXT,
        tier INTEGER,
        tweet_count INTEGER DEFAULT 0,
        tweets_per_min REAL DEFAULT 0,
        bullish_count INTEGER DEFAULT 0,
        bearish_count INTEGER DEFAULT 0,
        neutral_count INTEGER DEFAULT 0,
        top_engagement INTEGER DEFAULT 0,
        unique_authors INTEGER DEFAULT 0,
        sentiment_score REAL DEFAULT 0,
        velocity_trend TEXT DEFAULT 'FLAT',
        signal TEXT DEFAULT 'NEUTRAL',
        poll_count INTEGER DEFAULT 0,
        raw_snapshot TEXT,
        cached_at TEXT,
        UNIQUE(symbol, chain, tier))''')
    c.execute('''CREATE TABLE IF NOT EXISTS social_velocity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        chain TEXT,
        tweets_per_min REAL,
        sentiment REAL,
        sampled_at TEXT)''')
    conn.commit()
    conn.close()


def get_cached_social(symbol, chain, tier, max_age_minutes=None):
    if max_age_minutes is None:
        max_age_minutes = {1: TTL_TIER1, 2: TTL_TIER2, 3: TTL_TIER3}.get(tier, 30)
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""SELECT * FROM token_social_cache
                     WHERE symbol=? AND chain=? AND tier=?
                     AND cached_at >= datetime('now', ?)""",
                  (symbol, chain, tier, f'-{max_age_minutes} minutes'))
        row = c.fetchone()
        conn.close()
        if row:
            cols = [d[0] for d in c.description]
            return dict(zip(cols, row))
    except Exception:
        pass
    return None


def get_poll_count(symbol, chain):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT poll_count FROM token_social_cache WHERE symbol=? AND chain=? AND tier=2",
                  (symbol, chain))
        row = c.fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


POSITIVE_WORDS = ['bull', 'moon', 'pump', 'buy', 'long', 'breakout', 'surge', 'rally',
                  'green', 'ath', 'accumulate', 'bullish', 'alpha', 'gem', 'undervalued',
                  'launch', 'listed', 'partnership', 'mainnet', 'audit', 'legit',
                  'lfg', '100x', '1000x', 'wagmi', 'banger', 'fire', 'degen',
                  'early', 'opportunity', 'potential', 'strong', 'solid', 'great',
                  'amazing', 'love', 'best', 'top', 'huge', 'massive', 'nice',
                  'win', 'profit', 'gain', 'up', 'rising', 'flying', 'mooning']
NEGATIVE_WORDS = ['bear', 'dump', 'sell', 'short', 'crash', 'drop', 'red', 'rekt',
                  'scam', 'rug', 'bearish', 'dead', 'rugpull', 'honeypot', 'avoid',
                  'warning', 'fake', 'fraud', 'exit', 'exploit', 'hack']


def analyse_tweets(tweets):
    """Extract sentiment, velocity, engagement from tweet list."""
    if not tweets:
        return {'tweet_count': 0, 'sentiment': 0, 'signal': 'NEUTRAL',
                'bullish': 0, 'bearish': 0, 'engagement': 0, 'authors': 0}

    bullish = bearish = neutral = 0
    total_engagement = 0
    authors = set()

    for t in tweets:
        text = t.get('text', '').lower()
        likes = t.get('likeCount', 0) or 0
        rts   = t.get('retweetCount', 0) or 0
        replies = t.get('replyCount', 0) or 0
        total_engagement += likes + rts + replies
        author = t.get('author', {}).get('userName', '')
        if author: authors.add(author)

        pos = sum(1 for w in POSITIVE_WORDS if w in text)
        neg = sum(1 for w in NEGATIVE_WORDS if w in text)
        # Emoji signals — common in meme coin tweets
        pos += text.count('🚀') + text.count('🔥') + text.count('💎') + text.count('🌙')
        neg += text.count('💀') + text.count('🪦') + text.count('🚨') + text.count('⚠️')
        if pos > neg: bullish += 1
        elif neg > pos: bearish += 1
        else: neutral += 1

    total = len(tweets)
    sentiment = (bullish - bearish) / total if total > 0 else 0

    # Signal logic
    if sentiment > 0.6 and total >= 15:
        signal = 'STRONG_BUY'
    elif sentiment > 0.3 and total >= 8:
        signal = 'BUY'
    elif sentiment < -0.3 and bearish >= 3:
        signal = 'SELL'
    elif sentiment < -0.1:
        signal = 'WATCH_OUT'
    else:
        signal = 'NEUTRAL'

    return {
        'tweet_count': total,
        'sentiment': round(sentiment, 3),
        'signal': signal,
        'bullish': bullish,
        'bearish': bearish,
        'neutral': neutral,
        'engagement': total_engagement,
        'authors': len(authors),
    }


def search_twitter(symbol, project_name, max_results=20, query_type='Top'):
    """Raw Twitter search — checks budget before calling."""
    if not _can_use_twitter():
        return []  # Twitter disabled or budget exhausted
    query = f'${symbol} OR "{project_name}" -is:retweet lang:en'
    try:
        res = requests.get(
            TWITTER_SEARCH,
            headers={"X-API-Key": TWITTER_API_KEY},
            params={"query": query, "queryType": query_type, "cursor": ""},
            timeout=12,
        )
        if res.status_code == 429:
            print(f"        Twitter rate limit hit — backing off")
            time.sleep(5)
            return []
        if res.status_code != 200:
            return []
        tweets = res.json().get('tweets', [])[:max_results]
        if tweets:
            _record_credit_use(1)
        return tweets
    except Exception:
        return []


def store_social_cache(symbol, chain, tier, analysis, poll_count=0, raw=None):
    now = datetime.now().isoformat()
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO token_social_cache
            (symbol, chain, tier, tweet_count, bullish_count, bearish_count,
             neutral_count, top_engagement, unique_authors, sentiment_score,
             signal, poll_count, raw_snapshot, cached_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (symbol, chain, tier,
             analysis.get('tweet_count', 0),
             analysis.get('bullish', 0), analysis.get('bearish', 0),
             analysis.get('neutral', 0), analysis.get('engagement', 0),
             analysis.get('authors', 0), analysis.get('sentiment', 0),
             analysis.get('signal', 'NEUTRAL'), poll_count,
             json.dumps(raw) if raw else None, now))
        # Record velocity datapoint
        c.execute('''INSERT INTO social_velocity (symbol, chain, tweets_per_min, sentiment, sampled_at)
                     VALUES (?,?,?,?,?)''',
                  (symbol, chain,
                   analysis.get('tweet_count', 0) / 60,  # rough tweets/min
                   analysis.get('sentiment', 0), now))
        conn.commit()
        conn.close()
    except Exception:
        pass


def tier1_scan(symbol, chain, project_name=''):
    """
    Tier 1: one-time scan on gem detection.
    Uses 1 API credit. Cached 30 min.
    """
    cached = get_cached_social(symbol, chain, tier=1, max_age_minutes=TTL_TIER1)
    if cached:
        return cached

    name = project_name or symbol
    tweets = search_twitter(symbol, name, max_results=20, query_type='Top')
    analysis = analyse_tweets(tweets)
    store_social_cache(symbol, chain, tier=1, analysis=analysis, poll_count=1, raw=tweets[:3])
    return analysis


def tier2_poll(symbol, chain, project_name='', age_hours=0):
    """
    Tier 2: fast 3-min polling for SOL memes under 2h old.
    Uses 1 credit per call. Max TIER2_MAX_POLLS per gem.
    Returns analysis + velocity trend.
    """
    if chain not in TIER2_CHAINS:
        return None
    if age_hours > TIER2_MAX_AGE_H:
        return None

    poll_count = get_poll_count(symbol, chain)
    if poll_count >= TIER2_MAX_POLLS:
        return None  # exhausted polls for this gem

    cached = get_cached_social(symbol, chain, tier=2, max_age_minutes=TTL_TIER2)
    if cached:
        return cached

    name = project_name or symbol
    # Use Latest for real-time velocity
    tweets = search_twitter(symbol, name, max_results=15, query_type='Latest')
    analysis = analyse_tweets(tweets)

    # Compute velocity trend vs previous sample
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""SELECT tweets_per_min, sentiment FROM social_velocity
                     WHERE symbol=? AND chain=?
                     ORDER BY sampled_at DESC LIMIT 3""",
                  (symbol, chain))
        prev = c.fetchall()
        conn.close()
        if prev and len(prev) >= 2:
            prev_tpm = prev[1][0] if len(prev) > 1 else 0
            curr_tpm = analysis['tweet_count'] / 60
            if curr_tpm > prev_tpm * 1.5:
                analysis['velocity_trend'] = 'ACCELERATING'
            elif curr_tpm < prev_tpm * 0.5:
                analysis['velocity_trend'] = 'COOLING'
            else:
                analysis['velocity_trend'] = 'STABLE'
        else:
            analysis['velocity_trend'] = 'NEW'
    except Exception:
        analysis['velocity_trend'] = 'UNKNOWN'

    store_social_cache(symbol, chain, tier=2, analysis=analysis,
                      poll_count=poll_count + 1, raw=tweets[:3])
    return analysis


def tier3_scan(symbol, chain, project_name=''):
    """
    Tier 3: hourly scan for real projects.
    1 credit per hour per token.
    """
    cached = get_cached_social(symbol, chain, tier=3, max_age_minutes=TTL_TIER3)
    if cached:
        return cached

    name = project_name or symbol
    tweets = search_twitter(symbol, name, max_results=25, query_type='Top')
    analysis = analyse_tweets(tweets)
    store_social_cache(symbol, chain, tier=3, analysis=analysis, poll_count=1)
    return analysis


def run_social_monitoring():
    """
    Main function: scan pending gems, run appropriate tier.
    Called from fetcher.py.
    """
    init_social_tables()
    _CONFIG = _load_config()  # reload config each cycle
    global TWITTER_ENABLED, _credits_used
    TWITTER_ENABLED = _CONFIG['twitter_enabled']

    if TWITTER_ENABLED:
        print(f"  Social monitoring... (Twitter ON | credits used: {_credits_used}/{MAX_CREDITS_PER_SESSION})")
    else:
        print("  Social monitoring... (Twitter OFF — set ENABLE_TWITTER_FETCH=true in .env to enable)")

    conn = get_db()
    import pandas as pd

    # Tier 2: fast poll for fresh SOL memes
    try:
        fresh_sol = pd.read_sql_query(
            """SELECT symbol, name, chain, age_hours, cross_score
               FROM dex_gems
               WHERE chain IN ('solana','bsc')
               AND age_hours <= 2
               AND cross_score >= 4
               AND fetched_at >= datetime('now', '-4 hours')
               ORDER BY cross_score DESC LIMIT 5""", conn)
        t2_count = 0
        for _, r in fresh_sol.iterrows():
            result = tier2_poll(r['symbol'], r['chain'], r.get('name',''), r['age_hours'])
            if result and result.get('signal') not in ('NEUTRAL', None):
                emoji = '🚀' if result['signal'] == 'STRONG_BUY' else '📈' if result['signal'] == 'BUY' else '⚠️'
                print(f"    {emoji} {r['symbol']} ({r['chain']}): {result['signal']} "
                      f"| {result.get('tweet_count',0)} tweets "
                      f"| sent:{result.get('sentiment',0):+.2f} "
                      f"| trend:{result.get('velocity_trend','?')}")
                t2_count += 1
            time.sleep(1)  # don't hammer API
        if t2_count == 0 and not fresh_sol.empty:
            print(f"    Tier 2: {len(fresh_sol)} gems monitored (all cached)")
    except Exception as e:
        print(f"    Tier 2 failed: {e}")

    # Tier 1: initial scan for gems not yet scanned
    try:
        new_gems = pd.read_sql_query(
            """SELECT dg.symbol, dg.name, dg.chain, dg.contract_address, dg.cross_score
               FROM dex_gems dg
               LEFT JOIN token_social_cache tsc
                 ON tsc.symbol = dg.symbol AND tsc.chain = dg.chain AND tsc.tier = 1
               WHERE tsc.id IS NULL
               AND dg.cross_score >= 5
               AND dg.fetched_at >= datetime('now', '-24 hours')
               ORDER BY dg.cross_score DESC LIMIT 8""", conn)
        for _, r in new_gems.iterrows():
            result = tier1_scan(r['symbol'], r['chain'], r.get('name', ''))
            if result:
                print(f"    Tier 1 scan: {r['symbol']} — {result.get('signal','?')} "
                      f"({result.get('tweet_count',0)} tweets, "
                      f"sent:{result.get('sentiment',0):+.2f})")
            time.sleep(1.5)
    except Exception as e:
        print(f"    Tier 1 failed: {e}")

    conn.close()
    print("  Social monitoring done")


def get_social_signal(symbol, chain):
    """Get latest social signal for a token (for agent use)."""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""SELECT signal, sentiment_score, tweet_count, bullish_count,
                            bearish_count, velocity_trend, cached_at
                     FROM token_social_cache
                     WHERE symbol=? AND chain=?
                     ORDER BY tier ASC, cached_at DESC LIMIT 1""",
                  (symbol, chain))
        row = c.fetchone()
        conn.close()
        if row:
            return {
                'signal': row[0], 'sentiment': row[1], 'tweets': row[2],
                'bullish': row[3], 'bearish': row[4],
                'velocity': row[5], 'age': row[6],
            }
    except Exception:
        pass
    return None


if __name__ == '__main__':
    print("AlphaScope — Social Monitor v1.0")
    init_social_tables()
    run_social_monitoring()
