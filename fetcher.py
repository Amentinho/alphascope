"""
AlphaScope — Data Fetcher
Fetches crypto market data from free APIs and stores in SQLite.
"""

import requests
import sqlite3
import json
from datetime import datetime

# ============================================================
# DATABASE SETUP
# ============================================================
def init_db():
    """Create the database tables if they don't exist."""
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
    
    conn.commit()
    conn.close()
    print("✓ Database initialized")

# ============================================================
# FEAR & GREED INDEX
# ============================================================
def fetch_fear_greed():
    """Fetch the Crypto Fear & Greed Index."""
    try:
        res = requests.get('https://api.alternative.me/fng/?limit=7', timeout=10)
        data = res.json()['data']
        
        conn = sqlite3.connect('alphascope.db')
        c = conn.cursor()
        now = datetime.now().isoformat()
        
        for entry in data:
            c.execute('''INSERT INTO fear_greed (value, label, timestamp, fetched_at)
                         VALUES (?, ?, ?, ?)''',
                      (int(entry['value']), 
                       entry['value_classification'],
                       datetime.fromtimestamp(int(entry['timestamp'])).isoformat(),
                       now))
        
        conn.commit()
        conn.close()
        
        today = data[0]
        print(f"✓ Fear & Greed: {today['value']}/100 ({today['value_classification']})")
        return data
    except Exception as e:
        print(f"✗ Fear & Greed fetch failed: {e}")
        return None

# ============================================================
# TRENDING COINS
# ============================================================
def fetch_trending():
    """Fetch trending coins from CoinGecko."""
    try:
        res = requests.get('https://api.coingecko.com/api/v3/search/trending', timeout=10)
        coins = res.json().get('coins', [])[:10]
        
        conn = sqlite3.connect('alphascope.db')
        c = conn.cursor()
        now = datetime.now().isoformat()
        
        for coin in coins:
            item = coin['item']
            c.execute('''INSERT INTO trending (name, symbol, market_cap_rank, price_btc, score, fetched_at)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      (item['name'],
                       item['symbol'],
                       item.get('market_cap_rank'),
                       item.get('price_btc', 0),
                       item.get('score', 0),
                       now))
        
        conn.commit()
        conn.close()
        
        print(f"✓ Trending: {', '.join(coin['item']['symbol'] for coin in coins[:5])}")
        return coins
    except Exception as e:
        print(f"✗ Trending fetch failed: {e}")
        return None

# ============================================================
# TOKEN DATA (for watchlist)
# ============================================================
WATCHLIST = ['bitcoin', 'ethereum', 'solana', 'chainlink', 'arbitrum', 'nosana']

def fetch_token_data(coin_id):
    """Fetch detailed data for a specific token."""
    try:
        res = requests.get(
            f'https://api.coingecko.com/api/v3/coins/{coin_id}',
            params={'localization': 'false', 'tickers': 'false', 
                    'community_data': 'true', 'developer_data': 'false'},
            timeout=10
        )
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
                  (coin_id,
                   data.get('name'),
                   data.get('symbol', '').upper(),
                   md.get('current_price', {}).get('usd'),
                   md.get('price_change_percentage_24h'),
                   md.get('price_change_percentage_7d'),
                   md.get('price_change_percentage_30d'),
                   md.get('market_cap', {}).get('usd'),
                   md.get('total_volume', {}).get('usd'),
                   data.get('sentiment_votes_up_percentage'),
                   data.get('sentiment_votes_down_percentage'),
                   cd.get('twitter_followers'),
                   cd.get('reddit_subscribers'),
                   now))
        
        conn.commit()
        conn.close()
        
        price = md.get('current_price', {}).get('usd', 0)
        change = md.get('price_change_percentage_24h', 0)
        print(f"  ✓ {data.get('name')}: ${price:,.2f} ({change:+.1f}%)")
        return data
    except Exception as e:
        print(f"  ✗ {coin_id} fetch failed: {e}")
        return None

def fetch_all_watchlist():
    """Fetch data for all watchlist tokens with rate limiting."""
    import time
    print("Fetching watchlist tokens...")
    for coin in WATCHLIST:
        fetch_token_data(coin)
        time.sleep(1.5)  # CoinGecko rate limit: ~30 req/min

# ============================================================
# REDDIT POSTS
# ============================================================
def fetch_reddit_hot():
    """Fetch hot posts from r/cryptocurrency (no API key needed)."""
    try:
        headers = {'User-Agent': 'AlphaScope/1.0'}
        res = requests.get(
            'https://www.reddit.com/r/cryptocurrency/hot.json?limit=25',
            headers=headers, timeout=10
        )
        posts = res.json()['data']['children']
        
        conn = sqlite3.connect('alphascope.db')
        c = conn.cursor()
        now = datetime.now().isoformat()
        
        for post in posts:
            d = post['data']
            c.execute('''INSERT INTO reddit_posts 
                         (title, score, num_comments, subreddit, url, created_utc, fetched_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      (d.get('title'),
                       d.get('score', 0),
                       d.get('num_comments', 0),
                       d.get('subreddit'),
                       d.get('url'),
                       d.get('created_utc'),
                       now))
        
        conn.commit()
        conn.close()
        
        top = posts[0]['data']
        print(f"✓ Reddit: {len(posts)} posts fetched (top: {top['title'][:60]}...)")
        return posts
    except Exception as e:
        print(f"✗ Reddit fetch failed: {e}")
        return None

# ============================================================
# RUN ALL FETCHES
# ============================================================
def fetch_all():
    """Run all data fetches."""
    print(f"\n{'='*50}")
    print(f"AlphaScope — Fetching data at {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")
    
    fetch_fear_greed()
    fetch_trending()
    fetch_all_watchlist()
    fetch_reddit_hot()
    
    print(f"{'='*50}")
    print("✓ All fetches complete!")
    print(f"{'='*50}\n")

# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    init_db()
    fetch_all()
