"""
AlphaScope v1.0 — Unified Data Fetcher
All data sources feed into one signal engine.
Sources: X/Twitter, Reddit, Telegram, CoinGecko
Purpose: Find alphas, track airdrops, measure sentiment
"""

import requests
import sqlite3
import time
import re
from datetime import datetime

# ============================================================
# CONFIG — Edit these to customize your AlphaScope
# ============================================================
WATCHLIST = ['bitcoin', 'ethereum', 'solana', 'chainlink', 'arbitrum', 'sui', 'ondo-finance']

CASHTAGS = ['$BTC', '$ETH', '$SOL', '$LINK', '$ARB', '$SUI', '$DOGE', '$AVAX']

TELEGRAM_CHANNELS = ['whale_alert_io', 'crypto', 'blockchain']

REDDIT_SUBS = ['cryptocurrency', 'bitcoin', 'ethtrader', 'altcoin']
AIRDROP_SUBS = ['CryptoAirdrop', 'airdropalert', 'CryptoAirdrops']

TWITTER_API_KEY = "new1_1597ef833361479ba82c88ff32b2fb8c"

NARRATIVE_KEYWORDS = {
    'AI': ['ai ', 'artificial intelligence', 'machine learning', 'gpu', 'compute', 'render', 'bittensor'],
    'RWA': ['rwa', 'real world asset', 'tokeniz', 'blackrock', 'ondo'],
    'L2': ['layer 2', 'l2', 'rollup', 'arbitrum', 'optimism', 'base chain', 'zk'],
    'DeFi': ['defi', 'dex', 'lending', 'yield', 'liquidity', 'amm', 'pendle', 'aave'],
    'Memecoins': ['meme', 'doge', 'pepe', 'shib', 'bonk', 'frog', 'pump'],
    'Bitcoin': ['bitcoin', 'btc', 'halving', 'etf', 'saylor', 'strategy buys'],
    'Ethereum': ['ethereum', 'eth', 'vitalik', 'eip', 'blob', 'staking'],
    'Regulation': ['sec', 'regulation', 'congress', 'ban', 'legal', 'lawsuit'],
    'Gaming': ['gaming', 'gamefi', 'nft', 'metaverse'],
    'DePIN': ['depin', 'decentralized physical', 'helium', 'hivemapper'],
}

AIRDROP_KEYWORDS = ['airdrop', 'free mint', 'token launch', 'ico', 'ido', 'presale',
                     'fair launch', 'testnet reward', 'points program', 'claim',
                     'eligibility', 'snapshot', 'tge', 'token generation']

POSITIVE_WORDS = ['bull', 'moon', 'pump', 'buy', 'long', 'breakout', 'surge', 'rally',
                  'green', 'up', 'ath', 'accumulate', 'bullish', 'send it', 'alpha']
NEGATIVE_WORDS = ['bear', 'dump', 'sell', 'short', 'crash', 'drop', 'red', 'down',
                  'rekt', 'scam', 'rug', 'bearish', 'dead', 'rugpull']

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
        engagement INTEGER, url TEXT,
        fetched_at TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS narratives (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        narrative TEXT, mention_count INTEGER, source TEXT, fetched_at TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS hidden_gems (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, symbol TEXT, market_cap_rank INTEGER,
        signal_type TEXT, signal_detail TEXT, fetched_at TEXT)''')

    conn.commit()
    conn.close()
    print("✓ Database ready")

# ============================================================
# MARKET DATA — CoinGecko
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
        print(f"  ✓ Fear & Greed: {data[0]['value']}/100 ({data[0]['value_classification']})")
    except Exception as e:
        print(f"  ✗ Fear & Greed: {e}")

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
        print(f"  ✓ Trending: {', '.join(c['item']['symbol'] for c in coins[:5])}")
    except Exception as e:
        print(f"  ✗ Trending: {e}")

def fetch_watchlist():
    print("  Fetching watchlist...")
    for coin_id in WATCHLIST:
        try:
            data = requests.get(f'https://api.coingecko.com/api/v3/coins/{coin_id}',
                params={'localization': 'false', 'tickers': 'false', 'community_data': 'true', 'developer_data': 'false'},
                timeout=10).json()
            md = data.get('market_data', {})
            cd = data.get('community_data', {})
            conn = get_db()
            c = conn.cursor()
            c.execute('''INSERT INTO token_data (coin_id, name, symbol, price_usd, change_24h, change_7d, change_30d,
                         market_cap, volume_24h, sentiment_up, sentiment_down, twitter_followers, reddit_subscribers, fetched_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (coin_id, data.get('name'), data.get('symbol', '').upper(),
                 md.get('current_price', {}).get('usd'), md.get('price_change_percentage_24h'),
                 md.get('price_change_percentage_7d'), md.get('price_change_percentage_30d'),
                 md.get('market_cap', {}).get('usd'), md.get('total_volume', {}).get('usd'),
                 data.get('sentiment_votes_up_percentage'), data.get('sentiment_votes_down_percentage'),
                 cd.get('twitter_followers'), cd.get('reddit_subscribers'),
                 datetime.now().isoformat()))
            conn.commit()
            conn.close()
            price = md.get('current_price', {}).get('usd', 0)
            change = md.get('price_change_percentage_24h', 0) or 0
            print(f"    ✓ {data.get('name')}: ${price:,.2f} ({change:+.1f}%)")
        except Exception as e:
            print(f"    ✗ {coin_id}: {e}")
        time.sleep(6)

# ============================================================
# X/TWITTER — Real sentiment + alpha + airdrop detection
# ============================================================
def fetch_x_data():
    print("  Fetching X/Twitter...")
    now = datetime.now().isoformat()

    # Search cashtags for sentiment
    for cashtag in CASHTAGS:
        try:
            res = requests.get("https://api.twitterapi.io/twitter/tweet/advanced_search",
                headers={"X-API-Key": TWITTER_API_KEY},
                params={"query": cashtag, "queryType": "Latest", "cursor": ""}, timeout=15)
            if res.status_code == 429:
                print(f"    ⏳ {cashtag}: rate limited")
                time.sleep(3)
                continue
            if res.status_code != 200:
                continue
            tweets = res.json().get("tweets", [])[:20]
            if not tweets:
                continue

            conn = get_db()
            c = conn.cursor()

            # Sentiment
            pos = sum(1 for t in tweets if any(w in t.get('text', '').lower() for w in POSITIVE_WORDS))
            neg = sum(1 for t in tweets if any(w in t.get('text', '').lower() for w in NEGATIVE_WORDS))
            score = (pos - neg) / len(tweets)
            label = "BULLISH" if score > 0.15 else "BEARISH" if score < -0.15 else "NEUTRAL"
            engagement = sum(t.get('likeCount', 0) + t.get('retweetCount', 0) for t in tweets)

            # Store as signal
            top = max(tweets, key=lambda t: t.get('likeCount', 0) + t.get('retweetCount', 0))
            c.execute('''INSERT INTO signals (source, source_detail, signal_type, title, content, coin, 
                         sentiment_score, sentiment_label, engagement, url, fetched_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                ('twitter', cashtag, 'SENTIMENT', f'{cashtag} sentiment: {label}',
                 top.get('text', '')[:300], cashtag.replace('$', ''),
                 score, label, engagement, '', now))

            # Check for airdrop mentions in tweets
            for t in tweets:
                text = t.get('text', '').lower()
                for kw in AIRDROP_KEYWORDS:
                    if kw in text:
                        c.execute('''INSERT INTO signals (source, source_detail, signal_type, title, content, coin,
                                     sentiment_score, sentiment_label, engagement, url, fetched_at)
                                     VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                            ('twitter', f"@{t.get('author',{}).get('userName','')}", 'AIRDROP',
                             f'Airdrop mention: {cashtag}', t.get('text', '')[:300],
                             cashtag.replace('$', ''), 0, 'N/A',
                             t.get('likeCount', 0) + t.get('retweetCount', 0), '', now))
                        break

            conn.commit()
            conn.close()

            emoji = "🟢" if score > 0.1 else "🔴" if score < -0.1 else "🟡"
            print(f"    {emoji} {cashtag}: {label} ({score:+.2f}) | {len(tweets)} tweets")
        except Exception as e:
            print(f"    ✗ {cashtag}: {e}")
        time.sleep(2)

    # Search for alpha keywords on X
    alpha_queries = ['crypto alpha', 'next 100x crypto', 'crypto gem 2026', 'airdrop crypto confirmed']
    for query in alpha_queries:
        try:
            res = requests.get("https://api.twitterapi.io/twitter/tweet/advanced_search",
                headers={"X-API-Key": TWITTER_API_KEY},
                params={"query": query, "queryType": "Top", "cursor": ""}, timeout=15)
            if res.status_code != 200:
                continue
            tweets = res.json().get("tweets", [])[:10]
            conn = get_db()
            c = conn.cursor()
            for t in tweets:
                eng = t.get('likeCount', 0) + t.get('retweetCount', 0)
                if eng >= 5:  # Only store tweets with some engagement
                    c.execute('''INSERT INTO signals (source, source_detail, signal_type, title, content, coin,
                                 sentiment_score, sentiment_label, engagement, url, fetched_at)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                        ('twitter', f"@{t.get('author',{}).get('userName','')}",
                         'ALPHA', f'Alpha signal: {query}', t.get('text', '')[:300],
                         '', 0, 'N/A', eng, '', now))
            conn.commit()
            conn.close()
            print(f"    🔍 '{query}': {len(tweets)} tweets")
        except:
            pass
        time.sleep(2)

# ============================================================
# REDDIT — Sentiment, narratives, alphas, airdrops
# ============================================================
def fetch_reddit_data():
    print("  Fetching Reddit...")
    headers = {'User-Agent': 'AlphaScope/1.0'}
    now = datetime.now().isoformat()
    all_titles = []

    # Main crypto subs
    for sub in REDDIT_SUBS:
        try:
            posts = requests.get(f'https://www.reddit.com/r/{sub}/hot.json?limit=25',
                headers=headers, timeout=10).json()['data']['children']
            conn = get_db()
            c = conn.cursor()
            for post in posts:
                d = post['data']
                title = d.get('title', '')
                all_titles.append(title)
                score = d.get('score', 0)

                # Classify signal type
                title_lower = title.lower()
                signal_type = 'NEWS'
                for kw in AIRDROP_KEYWORDS:
                    if kw in title_lower:
                        signal_type = 'AIRDROP'
                        break

                # Sentiment
                pos = sum(1 for w in POSITIVE_WORDS if w in title_lower)
                neg = sum(1 for w in NEGATIVE_WORDS if w in title_lower)
                sent_score = (pos - neg) / max(pos + neg, 1)
                sent_label = "BULLISH" if sent_score > 0 else "BEARISH" if sent_score < 0 else "NEUTRAL"

                c.execute('''INSERT INTO signals (source, source_detail, signal_type, title, content, coin,
                             sentiment_score, sentiment_label, engagement, url, fetched_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    ('reddit', f'r/{sub}', signal_type, title, '',
                     '', sent_score, sent_label, score, d.get('url', ''), now))
            conn.commit()
            conn.close()
            time.sleep(1)
        except:
            continue

    # Airdrop-specific subs
    for sub in AIRDROP_SUBS:
        try:
            posts = requests.get(f'https://www.reddit.com/r/{sub}/hot.json?limit=10',
                headers=headers, timeout=10).json()['data']['children']
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
            continue

    # Detect narratives
    conn = get_db()
    c = conn.cursor()
    narrative_counts = {}
    for title in all_titles:
        title_lower = title.lower()
        for narrative, keywords in NARRATIVE_KEYWORDS.items():
            for kw in keywords:
                if kw in title_lower:
                    narrative_counts[narrative] = narrative_counts.get(narrative, 0) + 1
                    break
    for narrative, count in sorted(narrative_counts.items(), key=lambda x: -x[1]):
        c.execute('INSERT INTO narratives (narrative, mention_count, source, fetched_at) VALUES (?,?,?,?)',
            (narrative, count, 'reddit', now))
    conn.commit()
    conn.close()

    print(f"  ✓ Reddit: {len(all_titles)} posts from {len(REDDIT_SUBS)} subs + {len(AIRDROP_SUBS)} airdrop subs")
    if narrative_counts:
        top = sorted(narrative_counts.items(), key=lambda x: -x[1])[:3]
        print(f"  ✓ Narratives: {', '.join(f'{n}({c})' for n,c in top)}")

# ============================================================
# TELEGRAM — Whale alerts, alphas, airdrops
# ============================================================
def fetch_telegram_data():
    print("  Fetching Telegram...")
    now = datetime.now().isoformat()

    for channel in TELEGRAM_CHANNELS:
        try:
            url = f"https://t.me/s/{channel}"
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            if res.status_code != 200:
                continue

            # Extract messages
            messages = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', res.text, re.DOTALL)
            clean = [re.sub(r'<[^>]+>', '', m).strip() for m in messages if len(re.sub(r'<[^>]+>', '', m).strip()) > 10]

            # Extract views
            views_list = re.findall(r'<span class="tgme_widget_message_views">([\d.KMB]+)</span>', res.text)

            conn = get_db()
            c = conn.cursor()

            for i, msg in enumerate(clean[-10:]):
                msg_lower = msg.lower()

                # Classify
                signal_type = 'NEWS'
                if any(kw in msg_lower for kw in ['whale', 'transferred', 'million', 'billion']):
                    signal_type = 'WHALE'
                elif any(kw in msg_lower for kw in AIRDROP_KEYWORDS):
                    signal_type = 'AIRDROP'

                # Parse views
                views = 0
                if i < len(views_list):
                    v = views_list[i].replace('K', '000').replace('M', '000000').replace('.', '')
                    try: views = int(v)
                    except: pass

                c.execute('''INSERT INTO signals (source, source_detail, signal_type, title, content, coin,
                             sentiment_score, sentiment_label, engagement, url, fetched_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    ('telegram', f'@{channel}', signal_type, msg[:200], msg[:500],
                     '', 0, 'N/A', views, '', now))

            conn.commit()
            conn.close()
            print(f"    ✓ @{channel}: {len(clean[-10:])} messages")
        except Exception as e:
            print(f"    ✗ @{channel}: {e}")
        time.sleep(2)

# ============================================================
# HIDDEN GEMS DETECTOR
# ============================================================
def detect_hidden_gems():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT name, symbol, market_cap_rank FROM trending ORDER BY fetched_at DESC LIMIT 10")
    trending = c.fetchall()
    now = datetime.now().isoformat()
    gems = []
    for name, symbol, rank in trending:
        if rank and rank > 100:
            c.execute('INSERT INTO hidden_gems (name, symbol, market_cap_rank, signal_type, signal_detail, fetched_at) VALUES (?,?,?,?,?,?)',
                (name, symbol, rank, 'LOW_CAP_TRENDING', f'Rank #{rank} but trending — early attention signal', now))
            gems.append(f"{symbol}(#{rank})")
    conn.commit()
    conn.close()
    if gems:
        print(f"  ✓ Hidden gems: {', '.join(gems)}")

# ============================================================
# FETCH ALL
# ============================================================
def fetch_all():
    print(f"\n{'='*60}")
    print(f"  🔍 AlphaScope v1.0 — Data Fetch at {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    fetch_fear_greed()
    fetch_trending()
    fetch_watchlist()
    fetch_reddit_data()
    fetch_telegram_data()
    fetch_x_data()
    detect_hidden_gems()

    print(f"{'='*60}")
    print(f"  ✓ All sources fetched!")
    print(f"{'='*60}\n")

if __name__ == '__main__':
    init_db()
    fetch_all()
