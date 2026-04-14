"""
AlphaScope — Data Fetcher v0.2
Fetches crypto market data from free APIs and stores in SQLite.
"""

import requests
import sqlite3
import time
from datetime import datetime

# ============================================================
# YOUR WATCHLIST — Edit this anytime!
# ============================================================
WATCHLIST = [
    'bitcoin', 'ethereum', 'solana', 'chainlink', 'arbitrum',
    
    'sui', 'ondo-finance',
]

# ============================================================
# DATABASE SETUP
# ============================================================
def init_db():
    conn = sqlite3.connect('alphascope.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS fear_greed (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        value INTEGER,
        label TEXT,
        timestamp TEXT,
        fetched_at TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS trending (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        symbol TEXT,
        market_cap_rank INTEGER,
        price_btc REAL,
        score INTEGER,
        fetched_at TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS token_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coin_id TEXT,
        name TEXT,
        symbol TEXT,
        price_usd REAL,
        change_24h REAL,
        change_7d REAL,
        change_30d REAL,
        market_cap REAL,
        volume_24h REAL,
        sentiment_up REAL,
        sentiment_down REAL,
        twitter_followers INTEGER,
        reddit_subscribers INTEGER,
        fetched_at TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS reddit_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        score INTEGER,
        num_comments INTEGER,
        subreddit TEXT,
        url TEXT,
        created_utc REAL,
        fetched_at TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS narratives (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        narrative TEXT,
        mention_count INTEGER,
        source TEXT,
        fetched_at TEXT
    )''')
    
    conn.commit()
    conn.close()
    print("✓ Database ready")

# ============================================================
# FEAR & GREED INDEX
# ============================================================
def fetch_fear_greed():
    try:
        res = requests.get('https://api.alternative.me/fng/?limit=30', timeout=10)
        data = res.json()['data']
        
        conn = sqlite3.connect('alphascope.db')
        c = conn.cursor()
        now = datetime.now().isoformat()
        
        for entry in data:
            c.execute('INSERT INTO fear_greed (value, label, timestamp, fetched_at) VALUES (?, ?, ?, ?)',
                      (int(entry['value']), entry['value_classification'],
                       datetime.fromtimestamp(int(entry['timestamp'])).isoformat(), now))
        
        conn.commit()
        conn.close()
        
        today = data[0]
        print(f"  ✓ Fear & Greed: {today['value']}/100 ({today['value_classification']})")
        return data
    except Exception as e:
        print(f"  ✗ Fear & Greed failed: {e}")
        return None

# ============================================================
# TRENDING COINS
# ============================================================
def fetch_trending():
    try:
        res = requests.get('https://api.coingecko.com/api/v3/search/trending', timeout=10)
        coins = res.json().get('coins', [])[:10]
        
        conn = sqlite3.connect('alphascope.db')
        c = conn.cursor()
        now = datetime.now().isoformat()
        
        for coin in coins:
            item = coin['item']
            c.execute('INSERT INTO trending (name, symbol, market_cap_rank, price_btc, score, fetched_at) VALUES (?, ?, ?, ?, ?, ?)',
                      (item['name'], item['symbol'], item.get('market_cap_rank'),
                       item.get('price_btc', 0), item.get('score', 0), now))
        
        conn.commit()
        conn.close()
        
        names = ', '.join(coin['item']['symbol'] for coin in coins[:5])
        print(f"  ✓ Trending: {names}")
        return coins
    except Exception as e:
        print(f"  ✗ Trending failed: {e}")
        return None

# ============================================================
# TOKEN DATA
# ============================================================
def fetch_token_data(coin_id):
    try:
        res = requests.get(
            f'https://api.coingecko.com/api/v3/coins/{coin_id}',
            params={'localization': 'false', 'tickers': 'false',
                    'community_data': 'true', 'developer_data': 'false'},
            timeout=10
        )
        if res.status_code != 200:
            print(f"    ✗ {coin_id}: API returned {res.status_code}")
            return None
            
        data = res.json()
        md = data.get('market_data', {})
        cd = data.get('community_data', {})
        
        conn = sqlite3.connect('alphascope.db')
        c = conn.cursor()
        now = datetime.now().isoformat()
        
        c.execute('''INSERT INTO token_data 
                     (coin_id, name, symbol, price_usd, change_24h, change_7d, change_30d,
                      market_cap, volume_24h, sentiment_up, sentiment_down,
                      twitter_followers, reddit_subscribers, fetched_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (coin_id, data.get('name'), data.get('symbol', '').upper(),
                   md.get('current_price', {}).get('usd'),
                   md.get('price_change_percentage_24h'),
                   md.get('price_change_percentage_7d'),
                   md.get('price_change_percentage_30d'),
                   md.get('market_cap', {}).get('usd'),
                   md.get('total_volume', {}).get('usd'),
                   data.get('sentiment_votes_up_percentage'),
                   data.get('sentiment_votes_down_percentage'),
                   cd.get('twitter_followers'),
                   cd.get('reddit_subscribers'), now))
        
        conn.commit()
        conn.close()
        
        price = md.get('current_price', {}).get('usd', 0)
        change = md.get('price_change_percentage_24h', 0) or 0
        print(f"    ✓ {data.get('name')}: ${price:,.2f} ({change:+.1f}%)")
        return data
    except Exception as e:
        print(f"    ✗ {coin_id}: {e}")
        return None

def fetch_all_watchlist():
    print("  Fetching watchlist...")
    for coin in WATCHLIST:
        fetch_token_data(coin)
        time.sleep(6)

# ============================================================
# REDDIT + NARRATIVE DETECTION
# ============================================================
NARRATIVE_KEYWORDS = {
    'AI': ['ai ', 'artificial intelligence', 'machine learning', 'gpu', 'compute', 'render', 'bittensor'],
    'RWA': ['rwa', 'real world asset', 'tokeniz', 'blackrock', 'ondo'],
    'L2': ['layer 2', 'l2', 'rollup', 'arbitrum', 'optimism', 'base chain', 'zk'],
    'DeFi': ['defi', 'dex', 'lending', 'yield', 'liquidity', 'amm', 'pendle', 'aave'],
    'Memecoins': ['meme', 'doge', 'pepe', 'shib', 'bonk', 'frog', 'pump'],
    'Bitcoin': ['bitcoin', 'btc', 'halving', 'etf', 'saylor', 'strategy buys'],
    'Ethereum': ['ethereum', 'eth', 'vitalik', 'eip', 'blob', 'staking'],
    'Regulation': ['sec', 'regulation', 'congress', 'ban', 'legal', 'lawsuit', 'gensler'],
    'Gaming': ['gaming', 'gamefi', 'nft', 'metaverse', 'play to earn'],
    'DePIN': ['depin', 'decentralized physical', 'helium', 'hivemapper', 'nosana'],
}

def fetch_reddit_and_detect_narratives():
    try:
        headers = {'User-Agent': 'AlphaScope/1.0'}
        
        # Fetch from multiple crypto subreddits
        subreddits = ['cryptocurrency', 'bitcoin', 'ethtrader', 'altcoin']
        all_posts = []
        
        for sub in subreddits:
            try:
                res = requests.get(f'https://www.reddit.com/r/{sub}/hot.json?limit=25',
                                   headers=headers, timeout=10)
                posts = res.json()['data']['children']
                all_posts.extend(posts)
                time.sleep(1)  # Reddit rate limit
            except:
                continue
        
        conn = sqlite3.connect('alphascope.db')
        c = conn.cursor()
        now = datetime.now().isoformat()
        
        # Store posts
        for post in all_posts:
            d = post['data']
            c.execute('''INSERT INTO reddit_posts 
                         (title, score, num_comments, subreddit, url, created_utc, fetched_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      (d.get('title'), d.get('score', 0), d.get('num_comments', 0),
                       d.get('subreddit'), d.get('url'), d.get('created_utc'), now))
        
        # Detect narratives
        narrative_counts = {}
        for post in all_posts:
            title = post['data'].get('title', '').lower()
            for narrative, keywords in NARRATIVE_KEYWORDS.items():
                for keyword in keywords:
                    if keyword in title:
                        narrative_counts[narrative] = narrative_counts.get(narrative, 0) + 1
                        break
        
        # Store narratives
        for narrative, count in sorted(narrative_counts.items(), key=lambda x: -x[1]):
            c.execute('INSERT INTO narratives (narrative, mention_count, source, fetched_at) VALUES (?, ?, ?, ?)',
                      (narrative, count, 'reddit', now))
        
        conn.commit()
        conn.close()
        
        print(f"  ✓ Reddit: {len(all_posts)} posts from {len(subreddits)} subs")
        if narrative_counts:
            top = sorted(narrative_counts.items(), key=lambda x: -x[1])[:3]
            print(f"  ✓ Top narratives: {', '.join(f'{n}({c})' for n,c in top)}")
        return all_posts
    except Exception as e:
        print(f"  ✗ Reddit failed: {e}")
        return None

# ============================================================
# CRYPTOPANIC — Free News Sentiment
# ============================================================
def fetch_cryptopanic():
    """Fetch trending crypto news from CryptoPanic (no API key for basic)."""
    try:
        res = requests.get(
            'https://cryptopanic.com/api/free/v1/posts/?auth_token=free&public=true&kind=news',
            timeout=10
        )
        if res.status_code == 200:
            posts = res.json().get('results', [])[:15]
            print(f"  ✓ CryptoPanic: {len(posts)} news items")
            return posts
        else:
            print(f"  ✗ CryptoPanic: status {res.status_code}")
            return None
    except Exception as e:
        print(f"  ✗ CryptoPanic failed: {e}")
        return None

# ============================================================
# RUN ALL
# ============================================================
def fetch_all():
    print(f"\n{'='*60}")
    print(f"  🔍 AlphaScope — Data Fetch at {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    
    fetch_fear_greed()
    fetch_trending()
    fetch_all_watchlist()
    fetch_reddit_and_detect_narratives()
    fetch_cryptopanic()
    
    print(f"{'='*60}")
    print(f"  ✓ All fetches complete!")
    print(f"{'='*60}\n")

if __name__ == '__main__':
    init_db()
    fetch_all()
