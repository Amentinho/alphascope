"""
AlphaScope v2.1 — Full Dynamic Alpha Discovery
No static watchlists. System discovers what matters.

Sources:
  - 13 Reddit subs (with comment-depth tracking)
  - 6 Telegram channels  
  - 10 multilingual news sources (EN/CN/JP/RU/ES/BR)
  - 14 exchange listing feeds (Tier 1-4)
  - DeFi Llama (TVL + yields)
  - CoinGecko (trending + prices)
  - Fear & Greed Index
  - X/Twitter (optional, toggle in .env)

Intelligence:
  - Cross-source coin buzz detection
  - Auto-learning ticker registry
  - AI-powered airdrop qualification analysis
  - Exchange listing priority scoring
  - Narrative trend detection
"""

import requests
import sqlite3
import time
import re
import os
from datetime import datetime
from coin_registry import registry

# ============================================================
# CONFIG — Sources only, no fixed coins
# ============================================================
TELEGRAM_CHANNELS = ['whale_alert_io', 'crypto', 'blockchain',
                     'CoinTelegraph', 'AirdropOfficial', 'FatPigSignals']

REDDIT_SUBS = ['cryptocurrency', 'bitcoin', 'ethereum', 'solana',
               'CryptoMarkets', 'ethfinance', 'SatoshiStreetBets',
               'CryptoMoonShots', 'altcoin', 'defi',
               'cosmosnetwork', 'algorand', 'cardano']

AIRDROP_SUBS = ['airdropalert', 'CryptoAirdrops', 'cosmosnetwork']

TWITTER_API_KEY = "new1_1597ef833361479ba82c88ff32b2fb8c"
CASHTAGS = ['$BTC', '$ETH', '$SOL', '$LINK', '$ARB', '$SUI', '$DOGE', '$AVAX']

NARRATIVE_KEYWORDS = {
    'AI': ['ai ', 'artificial intelligence', 'machine learning', 'gpu', 'compute', 'render', 'bittensor'],
    'RWA': ['rwa', 'real world asset', 'tokeniz', 'blackrock', 'ondo'],
    'L2': ['layer 2', 'l2', 'rollup', 'arbitrum', 'optimism', 'zk'],
    'DeFi': ['defi', 'dex', 'lending', 'yield', 'liquidity', 'tvl'],
    'Memecoins': ['meme', 'doge', 'pepe', 'shib', 'bonk', 'pump'],
    'Bitcoin': ['bitcoin', 'btc', 'halving', 'etf', 'saylor'],
    'Ethereum': ['ethereum', 'eth', 'vitalik', 'staking'],
    'Regulation': ['sec', 'regulation', 'congress', 'ban', 'lawsuit'],
    'Gaming': ['gaming', 'gamefi', 'nft', 'metaverse'],
    'DePIN': ['depin', 'helium', 'render'],
    'Solana': ['solana', 'sol', 'jupiter', 'raydium'],
}

AIRDROP_KEYWORDS = ['airdrop', 'free mint', 'token launch', 'ido', 'presale',
                     'fair launch', 'testnet reward', 'points program',
                     'eligibility check', 'snapshot date', 'tge date', 'token generation']

POSITIVE_WORDS = ['bull', 'moon', 'pump', 'buy', 'long', 'breakout', 'surge', 'rally',
                  'green', 'ath', 'accumulate', 'bullish', 'alpha', 'gem', 'undervalued']
NEGATIVE_WORDS = ['bear', 'dump', 'sell', 'short', 'crash', 'drop', 'red', 'rekt',
                  'scam', 'rug', 'bearish', 'dead', 'rugpull', 'overvalued']

# ============================================================
# DATABASE
# ============================================================
def get_db():
    return sqlite3.connect('alphascope.db', timeout=30)

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS fear_greed (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        value INTEGER, label TEXT, timestamp TEXT, fetched_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS trending (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, symbol TEXT, market_cap_rank INTEGER, fetched_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS token_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coin_id TEXT, name TEXT, symbol TEXT, price_usd REAL,
        change_24h REAL, change_7d REAL, change_30d REAL,
        market_cap REAL, volume_24h REAL,
        sentiment_up REAL, sentiment_down REAL,
        twitter_followers INTEGER, reddit_subscribers INTEGER,
        fetched_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT, source_detail TEXT,
        signal_type TEXT, title TEXT, content TEXT,
        coin TEXT, sentiment_score REAL, sentiment_label TEXT,
        engagement INTEGER, url TEXT, fetched_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS narratives (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        narrative TEXT, mention_count INTEGER, source TEXT, fetched_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS hidden_gems (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, symbol TEXT, market_cap_rank INTEGER,
        signal_type TEXT, signal_detail TEXT, fetched_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS coin_buzz (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coin TEXT, mention_count INTEGER, total_engagement INTEGER,
        avg_sentiment REAL, sources TEXT, fetched_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS exchange_listings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exchange TEXT, exchange_tier INTEGER,
        coin TEXT, title TEXT, listing_date TEXT,
        status TEXT, url TEXT, fetched_at TEXT,
        UNIQUE(exchange, title))''')
    c.execute('''CREATE TABLE IF NOT EXISTS airdrop_projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT, category TEXT, website TEXT, twitter TEXT,
        qualification_steps TEXT, effort_level TEXT, cost_estimate TEXT,
        time_required TEXT, reward_estimate TEXT, deadline TEXT,
        legitimacy_score INTEGER, legitimacy_reasons TEXT,
        status TEXT, user_notes TEXT, progress TEXT, sources TEXT,
        created_at TEXT, updated_at TEXT,
        UNIQUE(project_name))''')
    conn.commit()
    conn.close()
    print("Database ready")

# ============================================================
# HELPERS
# ============================================================
def detect_coins(text, source='unknown'):
    text_lower = text.lower()
    found = set()
    for match in re.findall(r'\$([a-zA-Z]{2,6})', text):
        m = match.lower()
        if m in registry.tickers:
            found.add(registry.tickers[m])
        elif len(match) <= 5:
            ticker = match.upper()
            found.add(ticker)
            registry.record_ticker(ticker, source)
    for keyword, ticker in registry.tickers.items():
        if len(keyword) >= 3:
            if f' {keyword} ' in f' {text_lower} ' or f' {keyword}.' in f' {text_lower}':
                found.add(ticker)
    return list(found)

def calc_sentiment(text):
    text_lower = text.lower()
    pos = sum(1 for w in POSITIVE_WORDS if w in text_lower)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text_lower)
    total = pos + neg
    if total == 0:
        return 0.0, "NEUTRAL"
    score = (pos - neg) / total
    label = "BULLISH" if score > 0.2 else "BEARISH" if score < -0.2 else "NEUTRAL"
    return round(score, 2), label

# ============================================================
# MARKET DATA
# ============================================================
def fetch_fear_greed():
    try:
        data = requests.get('https://api.alternative.me/fng/?limit=30', timeout=10).json()['data']
        conn = get_db()
        c = conn.cursor()
        now = datetime.now().isoformat()
        for entry in data:
            c.execute('INSERT INTO fear_greed (value, label, timestamp, fetched_at) VALUES (?,?,?,?)',
                (int(entry['value']), entry['value_classification'],
                 datetime.fromtimestamp(int(entry['timestamp'])).isoformat(), now))
        conn.commit()
        conn.close()
        print(f"  Fear & Greed: {data[0]['value']}/100 ({data[0]['value_classification']})")
    except Exception as e:
        print(f"  Fear & Greed failed: {e}")

def fetch_trending():
    try:
        coins = requests.get('https://api.coingecko.com/api/v3/search/trending', timeout=10).json().get('coins', [])[:10]
        conn = get_db()
        c = conn.cursor()
        now = datetime.now().isoformat()
        for coin in coins:
            item = coin['item']
            c.execute('INSERT INTO trending (name, symbol, market_cap_rank, fetched_at) VALUES (?,?,?,?)',
                (item['name'], item['symbol'], item.get('market_cap_rank'), now))
        conn.commit()
        conn.close()
        print(f"  Trending: {', '.join(c['item']['symbol'] for c in coins[:5])}")
    except Exception as e:
        print(f"  Trending failed: {e}")

def fetch_buzzing_prices():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT coin, mention_count FROM coin_buzz ORDER BY fetched_at DESC, mention_count DESC LIMIT 15")
    buzzing = c.fetchall()
    conn.close()
    must_fetch = {'BTC', 'ETH', 'SOL'}
    coins_to_fetch = must_fetch | {coin for coin, _ in buzzing if coin in registry.coingecko_map}
    
    # Build list of CoinGecko IDs
    cg_ids = []
    ticker_map = {}
    for ticker in coins_to_fetch:
        coin_id = registry.coingecko_map.get(ticker)
        if coin_id:
            cg_ids.append(coin_id)
            ticker_map[coin_id] = ticker
    
    if not cg_ids:
        print("  No coins to fetch prices for")
        return
    
    print(f"  Fetching prices for {len(cg_ids)} buzzing coins...")
    
    # Use simple/price endpoint — fast, reliable, one call for all coins
    try:
        ids_str = ','.join(cg_ids)
        res = requests.get(
            f'https://api.coingecko.com/api/v3/simple/price',
            params={'ids': ids_str, 'vs_currencies': 'usd',
                    'include_24hr_change': 'true', 'include_7d_change': 'true',
                    'include_30d_change': 'true', 'include_market_cap': 'true',
                    'include_24hr_vol': 'true'},
            timeout=15)
        
        if res.status_code != 200:
            print(f"    Simple price API: HTTP {res.status_code}")
            return
        
        prices = res.json()
        conn = get_db()
        c = conn.cursor()
        now = datetime.now().isoformat()
        
        for coin_id, data in prices.items():
            ticker = ticker_map.get(coin_id, coin_id.upper())
            price = data.get('usd', 0)
            c24 = data.get('usd_24h_change', 0) or 0
            c7 = data.get('usd_7d_change', 0) or 0
            c30 = data.get('usd_30d_change', 0) or 0
            mcap = data.get('usd_market_cap', 0) or 0
            vol = data.get('usd_24h_vol', 0) or 0
            
            c.execute('''INSERT INTO token_data (coin_id, name, symbol, price_usd, change_24h, change_7d, change_30d,
                         market_cap, volume_24h, sentiment_up, sentiment_down, twitter_followers, reddit_subscribers, fetched_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (coin_id, coin_id.replace('-', ' ').title(), ticker,
                 price, c24, c7, c30, mcap, vol,
                 None, None, None, None, now))
            
            print(f"    {ticker}: ${price:,.2f} ({c24:+.1f}%)")
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"    Price fetch failed: {e}")

# ============================================================
# REDDIT — Wide scan + coin detection + buzz tracking
# ============================================================
def fetch_reddit_data():
    print("  Fetching Reddit...")
    headers = {'User-Agent': 'AlphaScope/2.1'}
    now = datetime.now().isoformat()
    all_titles = []
    coin_mentions = {}

    for sub in REDDIT_SUBS:
        try:
            res = requests.get(f'https://www.reddit.com/r/{sub}/hot.json?limit=25',
                headers=headers, timeout=10)
            if res.status_code != 200:
                print(f"    r/{sub}: HTTP {res.status_code}")
                continue
            posts = res.json()['data']['children']
            conn = get_db()
            c = conn.cursor()
            for post in posts:
                d = post['data']
                title = d.get('title', '')
                selftext = d.get('selftext', '')[:300]
                all_titles.append(title)
                score = d.get('score', 0)
                comments = d.get('num_comments', 0)
                coins = detect_coins(title + ' ' + selftext, f'reddit:{sub}')
                sent_score, sent_label = calc_sentiment(title + ' ' + selftext)
                for coin in coins:
                    if coin not in coin_mentions:
                        coin_mentions[coin] = {'count': 0, 'engagement': 0, 'sentiment_sum': 0, 'subs': set()}
                    coin_mentions[coin]['count'] += 1
                    coin_mentions[coin]['engagement'] += score + comments
                    coin_mentions[coin]['sentiment_sum'] += sent_score
                    coin_mentions[coin]['subs'].add(sub)
                title_lower = title.lower()
                signal_type = 'NEWS'
                for kw in AIRDROP_KEYWORDS:
                    if kw in title_lower:
                        signal_type = 'AIRDROP'
                        break
                if comments >= 50 and signal_type == 'NEWS':
                    signal_type = 'ALPHA'
                c.execute('''INSERT INTO signals (source, source_detail, signal_type, title, content, coin,
                             sentiment_score, sentiment_label, engagement, url, fetched_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    ('reddit', f'r/{sub}', signal_type, title, selftext,
                     ','.join(coins), sent_score, sent_label, score + comments, d.get('url', ''), now))
            conn.commit()
            conn.close()
            time.sleep(1.5)
        except Exception as e:
            print(f"    r/{sub}: {e}")

    for sub in AIRDROP_SUBS:
        try:
            res = requests.get(f'https://www.reddit.com/r/{sub}/hot.json?limit=10',
                headers=headers, timeout=10)
            if res.status_code != 200:
                continue
            posts = res.json()['data']['children']
            conn = get_db()
            c = conn.cursor()
            for post in posts:
                d = post['data']
                c.execute('''INSERT INTO signals (source, source_detail, signal_type, title, content, coin,
                             sentiment_score, sentiment_label, engagement, url, fetched_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    ('reddit', f'r/{sub}', 'AIRDROP', d.get('title', ''), '',
                     '', 0, 'N/A', d.get('score', 0), d.get('url', ''), now))
            conn.commit()
            conn.close()
            time.sleep(2)
        except:
            pass

    # Store coin buzz
    conn = get_db()
    c = conn.cursor()
    for coin, data in sorted(coin_mentions.items(), key=lambda x: -x[1]['count']):
        avg_sent = data['sentiment_sum'] / max(data['count'], 1)
        subs_list = ','.join(list(data['subs'])[:5])
        c.execute('INSERT INTO coin_buzz (coin, mention_count, total_engagement, avg_sentiment, sources, fetched_at) VALUES (?,?,?,?,?,?)',
            (coin, data['count'], data['engagement'], round(avg_sent, 2), f"reddit:{subs_list}", now))
    # Narratives
    narrative_counts = {}
    for title in all_titles:
        tl = title.lower()
        for narrative, keywords in NARRATIVE_KEYWORDS.items():
            for kw in keywords:
                if kw in tl:
                    narrative_counts[narrative] = narrative_counts.get(narrative, 0) + 1
                    break
    for narrative, count in sorted(narrative_counts.items(), key=lambda x: -x[1]):
        c.execute('INSERT INTO narratives (narrative, mention_count, source, fetched_at) VALUES (?,?,?,?)',
            (narrative, count, 'reddit', now))
    conn.commit()
    conn.close()

    print(f"  Reddit: {len(all_titles)} posts from {len(REDDIT_SUBS)} subs")
    if narrative_counts:
        top = sorted(narrative_counts.items(), key=lambda x: -x[1])[:3]
        print(f"  Narratives: {', '.join(f'{n}({c})' for n,c in top)}")
    if coin_mentions:
        top_coins = sorted(coin_mentions.items(), key=lambda x: -x[1]['count'])[:5]
        buzz_str = ', '.join(f"{c}({d['count']})" for c, d in top_coins)
        print(f"  Coin buzz: {buzz_str}")

# ============================================================
# TELEGRAM
# ============================================================
def fetch_telegram_data():
    print("  Fetching Telegram...")
    now = datetime.now().isoformat()
    for channel in TELEGRAM_CHANNELS:
        try:
            res = requests.get(f"https://t.me/s/{channel}",
                headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            if res.status_code != 200:
                continue
            messages = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', res.text, re.DOTALL)
            clean = [re.sub(r'<[^>]+>', '', m).strip() for m in messages if len(re.sub(r'<[^>]+>', '', m).strip()) > 10]
            conn = get_db()
            c = conn.cursor()
            for msg in clean[-10:]:
                msg_lower = msg.lower()
                coins = detect_coins(msg, f'telegram:{channel}')
                signal_type = 'NEWS'
                if any(kw in msg_lower for kw in ['whale', 'transferred', '🚨']):
                    signal_type = 'WHALE'
                elif any(kw in msg_lower for kw in AIRDROP_KEYWORDS):
                    signal_type = 'AIRDROP'
                sent_score, sent_label = calc_sentiment(msg)
                c.execute('''INSERT INTO signals (source, source_detail, signal_type, title, content, coin,
                             sentiment_score, sentiment_label, engagement, url, fetched_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    ('telegram', f'@{channel}', signal_type, msg[:200], msg[:500],
                     ','.join(coins), sent_score, sent_label, 0, '', now))
            conn.commit()
            conn.close()
            print(f"    @{channel}: {len(clean[-10:])} messages")
        except Exception as e:
            print(f"    @{channel}: {e}")
        time.sleep(2)

# ============================================================
# X/TWITTER — Routed through social_monitor credit budget
# ============================================================
def fetch_x_data():
    """
    Twitter sentiment for portfolio cashtags.
    Routes through social_monitor to respect the session credit budget
    and persist results to token_social_cache (never re-query within TTL).
    Only fires once per session per coin via Tier 3 (hourly cadence).
    """
    enable = os.environ.get('ENABLE_TWITTER_FETCH', '')
    if not enable:
        try:
            with open('.env') as f:
                for line in f:
                    if line.startswith('ENABLE_TWITTER_FETCH='):
                        enable = line.strip().split('=', 1)[1]
        except Exception:
            pass
    if enable.lower() != 'true':
        return

    try:
        from social_monitor import tier3_scan, _can_use_twitter
        if not _can_use_twitter():
            print("  Twitter: session credit budget exhausted — skipping")
            return
        print("  Fetching X/Twitter (via social_monitor Tier 3)...")
        for cashtag in CASHTAGS:
            sym = cashtag.replace('$', '')
            # Tier 3: hourly cadence, cached — won't re-hit API if fresh result exists
            tier3_scan(sym, chain='ethereum', project_name=sym)
    except Exception as e:
        print(f"  Twitter fetch failed: {e}")


def fetch_x_airdrops():
    """
    Search X for airdrop signals. Routed through social_monitor budget.
    Results stored in signals table AND in twitter_project_history
    so we never re-query a project already evaluated.
    """
    enable = os.environ.get('ENABLE_TWITTER_FETCH', '')
    if not enable:
        try:
            with open('.env') as f:
                for line in f:
                    if line.startswith('ENABLE_TWITTER_FETCH='):
                        enable = line.strip().split('=', 1)[1]
        except Exception:
            pass
    if enable.lower() != 'true':
        return

    try:
        from social_monitor import _can_use_twitter, search_twitter, _record_credit_use
        if not _can_use_twitter():
            print("  Twitter airdrops: credit budget exhausted — skipping")
            return
    except ImportError:
        return

    print("  Fetching X airdrop signals...")
    now = datetime.now().isoformat()
    queries = ['crypto airdrop confirmed', 'airdrop live now', 'testnet airdrop reward']

    conn = get_db()
    c = conn.cursor()
    # Ensure project history table exists
    c.execute('''CREATE TABLE IF NOT EXISTS twitter_project_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project TEXT UNIQUE,
        query TEXT,
        tweet_count INTEGER,
        top_tweet TEXT,
        sentiment_score REAL,
        fetched_at TEXT)''')
    conn.commit()

    for query in queries:
        # Skip if we already fetched this query in the last 6 hours
        row = c.execute(
            "SELECT fetched_at FROM twitter_project_history WHERE project=? "
            "AND fetched_at >= datetime('now','-6 hours')", (query,)).fetchone()
        if row:
            print(f"    '{query}': cached ({row[0][:16]}) — skipping")
            continue

        try:
            tweets = search_twitter(query, project_name=query, max_results=10, query_type='Top')
            if not tweets:
                continue
            _record_credit_use(1)
            for t in tweets:
                eng = t.get('likeCount', 0) + t.get('retweetCount', 0)
                if eng >= 3:
                    coins = detect_coins(t.get('text', ''), 'twitter:airdrop')
                    c.execute('''INSERT INTO signals
                        (source, source_detail, signal_type, title, content, coin,
                         sentiment_score, sentiment_label, engagement, url, fetched_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                        ('twitter', f"@{t.get('author',{}).get('userName','')}",
                         'AIRDROP', t.get('text','')[:200], t.get('text','')[:500],
                         ','.join(coins), 0, 'N/A', eng, '', now))
            # Record in project history so we skip next time
            top = max(tweets, key=lambda t: t.get('likeCount',0)+t.get('retweetCount',0), default={})
            c.execute('''INSERT OR REPLACE INTO twitter_project_history
                (project, query, tweet_count, top_tweet, sentiment_score, fetched_at)
                VALUES (?,?,?,?,?,?)''',
                (query, query, len(tweets), top.get('text','')[:300], 0, now))
            conn.commit()
            print(f"    '{query}': {len(tweets)} tweets stored")
        except Exception as e:
            print(f"    '{query}': {e}")
        time.sleep(2)
    conn.close()

def detect_hidden_gems():
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    gems = []

    # Method 1: Trending on CoinGecko but outside top 50
    c.execute("SELECT name, symbol, market_cap_rank FROM trending ORDER BY fetched_at DESC LIMIT 10")
    for name, symbol, rank in c.fetchall():
        if rank and rank > 50:
            c.execute('INSERT INTO hidden_gems (name, symbol, market_cap_rank, signal_type, signal_detail, fetched_at) VALUES (?,?,?,?,?,?)',
                (name, symbol, rank, 'LOW_CAP_TRENDING', f'Rank #{rank} but trending on CoinGecko', now))
            gems.append(f"{symbol}(#{rank})")

    # Method 2: High buzz coins outside majors
    c.execute("""SELECT coin, mention_count, total_engagement FROM coin_buzz 
                 WHERE fetched_at >= datetime('now', '-2 hours') AND mention_count >= 3
                 ORDER BY mention_count DESC LIMIT 10""")
    majors = {'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE', 'DOT'}
    for coin, mentions, engagement in c.fetchall():
        if coin not in majors and coin not in [g.split('(')[0] for g in gems]:
            c.execute('INSERT INTO hidden_gems (name, symbol, market_cap_rank, signal_type, signal_detail, fetched_at) VALUES (?,?,?,?,?,?)',
                (coin, coin, None, 'HIGH_BUZZ', f'{mentions} mentions, {engagement} engagement across Reddit/Telegram', now))
            gems.append(f"{coin}(buzz:{mentions})")

    conn.commit()
    conn.close()
    if gems:
        print(f"  Hidden gems: {', '.join(gems)}")

# ============================================================
# FETCH ALL — The main orchestrator
# ============================================================
def fetch_all():
    print(f"\n{'='*60}")
    print(f"  AlphaScope v2.1 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    # Phase 1: Market pulse
    fetch_fear_greed()
    fetch_trending()

    # Phase 2: Social signals
    fetch_reddit_data()
    fetch_telegram_data()

    # Phase 3: News & DeFi (imported modules)
    try:
        from news_sources import fetch_news_sources, fetch_defi_data
        fetch_news_sources()
        fetch_defi_data()
    except ImportError:
        print("  news_sources.py not found — skipping news/DeFi")
    except Exception as e:
        print(f"  News/DeFi failed: {e}")

    # Phase 4: Exchange listings (imported module)
    try:
        from exchange_feeds import fetch_exchange_listings
        fetch_exchange_listings()
    except ImportError:
        print("  exchange_feeds.py not found — skipping exchange listings")
    except Exception as e:
        print(f"  Exchange listings failed: {e}")

    # Phase 5: X/Twitter (optional)
    fetch_x_data()

    # Phase 5.5: Macroeconomic data
    try:
        from macro_calendar import fetch_macro_data
        fetch_macro_data()
    except ImportError:
        print("  macro_calendar.py not found — skipping macro")
    except Exception as e:
        print(f"  Macro data failed: {e}")

    # Phase 6: Pre-launch gem scanning
    try:
        from gem_scanner import fetch_pre_launch_gems
        fetch_pre_launch_gems()
    except ImportError:
        print("  gem_scanner.py not found — skipping pre-launch scanning")
    except Exception as e:
        print(f"  Gem scanner failed: {e}")

    # Phase 7: Intelligence
    fetch_x_airdrops()
    detect_hidden_gems()

    # Phase 8: AI Airdrop Analysis (imported module)
    try:
        from airdrop_intel import process_new_airdrops
        process_new_airdrops()
    except ImportError:
        print("  airdrop_intel.py not found — skipping airdrop AI")
    except Exception as e:
        print(f"  Airdrop AI failed: {e}")

    # Phase 9: Dynamic price fetching (only buzzing coins)
    fetch_buzzing_prices()

    # Phase 10: Save registry
    registry.save()
    stats = registry.get_stats()
    print(f"  Registry: {stats['total_known']} known, {stats['graduated']} learned, {stats['pending']} pending")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    init_db()
    fetch_all()
