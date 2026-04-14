"""
AlphaScope — Data Fetcher v0.2
Fetches crypto market data from free APIs and stores in SQLite.
"""

import requests
import sqlite3
import time
from datetime import datetime
from telegram_monitor import fetch_all_telegram
from x_sentiment import fetch_x_sentiment

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
    except Exception as e:
        print(f"  ✗ CryptoPanic failed: {e}")
        return None

# ============================================================
# RUN ALL
# ============================================================
# ============================================================
# NEW LISTINGS — Recently added coins (potential early alpha)
# ============================================================
def fetch_new_listings():
    """Fetch recently listed coins from CoinGecko."""
    try:
        res = requests.get(
            'https://api.coingecko.com/api/v3/coins/list/new',
            timeout=10
        )
        if res.status_code != 200:
            print(f"  ✗ New listings: API returned {res.status_code}")
            return None
        
        coins = res.json()[:15]
        
        conn = sqlite3.connect('alphascope.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS new_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coin_id TEXT,
            name TEXT,
            symbol TEXT,
            activated_at TEXT,
            fetched_at TEXT
        )''')
        now = datetime.now().isoformat()
        
        for coin in coins:
            c.execute('INSERT INTO new_listings (coin_id, name, symbol, activated_at, fetched_at) VALUES (?, ?, ?, ?, ?)',
                      (coin.get('id'), coin.get('name'), coin.get('symbol', '').upper(),
                       coin.get('activated_at', ''), now))
        
        conn.commit()
        conn.close()
        print(f"  ✓ New listings: {len(coins)} coins ({', '.join(c.get('symbol','?').upper() for c in coins[:5])})")
        return coins
    except Exception as e:
        print(f"  ✗ New listings: {e}")
        return None

# ============================================================
# HIDDEN GEMS — Low-cap trending coins (alpha signal)
# ============================================================
def detect_hidden_gems():
    """Find coins that are trending but have low market cap — potential gems."""
    conn = sqlite3.connect('alphascope.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS hidden_gems (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        symbol TEXT,
        market_cap_rank INTEGER,
        signal_type TEXT,
        signal_detail TEXT,
        fetched_at TEXT
    )''')
    
    # Get trending coins
    c.execute("SELECT name, symbol, market_cap_rank FROM trending ORDER BY fetched_at DESC LIMIT 10")
    trending = c.fetchall()
    
    now = datetime.now().isoformat()
    gems = []
    
    for name, symbol, rank in trending:
        if rank and rank > 100:  # Outside top 100 = potential gem
            signal = "LOW_CAP_TRENDING"
            detail = f"Rank #{rank} but trending on CoinGecko — early attention signal"
            c.execute('INSERT INTO hidden_gems (name, symbol, market_cap_rank, signal_type, signal_detail, fetched_at) VALUES (?, ?, ?, ?, ?, ?)',
                      (name, symbol, rank, signal, detail, now))
            gems.append(f"{symbol}(#{rank})")
    
    conn.commit()
    conn.close()
    
    if gems:
        print(f"  ✓ Hidden gems: {', '.join(gems)}")
    else:
        print(f"  ✓ Hidden gems: none detected this cycle")
    return gems

# ============================================================
# AIRDROP & ICO SCANNER — from Reddit + Telegram
# ============================================================
AIRDROP_KEYWORDS = ['airdrop', 'free mint', 'token launch', 'ico', 'ido', 'presale', 
                     'fair launch', 'testnet reward', 'points program', 'claim', 
                     'eligibility', 'snapshot', 'tge', 'token generation']

def scan_airdrops():
    """Scan Reddit and Telegram messages for airdrop/ICO mentions."""
    conn = sqlite3.connect('alphascope.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS airdrops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        source TEXT,
        source_detail TEXT,
        keyword_matched TEXT,
        score INTEGER,
        fetched_at TEXT
    )''')
    
    now = datetime.now().isoformat()
    found = []
    
    # Scan Reddit posts
    c.execute("SELECT title, score, subreddit FROM reddit_posts ORDER BY fetched_at DESC LIMIT 100")
    for title, score, sub in c.fetchall():
        title_lower = title.lower()
        for keyword in AIRDROP_KEYWORDS:
            if keyword in title_lower:
                c.execute('INSERT INTO airdrops (title, source, source_detail, keyword_matched, score, fetched_at) VALUES (?, ?, ?, ?, ?, ?)',
                          (title, 'reddit', f'r/{sub}', keyword, score, now))
                found.append(f"[Reddit] {title[:50]}...")
                break
    
    # Scan Telegram messages
    c.execute("SELECT message, channel FROM telegram_messages ORDER BY fetched_at DESC LIMIT 100")
    for message, channel in c.fetchall():
        msg_lower = message.lower()
        for keyword in AIRDROP_KEYWORDS:
            if keyword in msg_lower:
                c.execute('INSERT INTO airdrops (title, source, source_detail, keyword_matched, score, fetched_at) VALUES (?, ?, ?, ?, ?, ?)',
                          (message[:200], 'telegram', f'@{channel}', keyword, 0, now))
                found.append(f"[Telegram] {message[:50]}...")
                break
    
    conn.commit()
    conn.close()
    
    if found:
        print(f"  ✓ Airdrops/ICOs: {len(found)} mentions found")
    else:
        print(f"  ✓ Airdrops/ICOs: no mentions this cycle")
    return found

# Also fetch from dedicated airdrop subreddits
def fetch_airdrop_reddit():
    """Fetch from airdrop-specific subreddits."""
    import requests
    headers = {'User-Agent': 'AlphaScope/1.0'}
    
    subs = ['CryptoAirdrop', 'airdropalert', 'CryptoAirdrops']
    all_posts = []
    
    for sub in subs:
        try:
            res = requests.get(f'https://www.reddit.com/r/{sub}/hot.json?limit=10',
                               headers=headers, timeout=10)
            if res.status_code == 200:
                posts = res.json()['data']['children']
                conn = sqlite3.connect('alphascope.db')
                c = conn.cursor()
                now = datetime.now().isoformat()
                for post in posts:
                    d = post['data']
                    c.execute('INSERT INTO airdrops (title, source, source_detail, keyword_matched, score, fetched_at) VALUES (?, ?, ?, ?, ?, ?)',
                              (d.get('title'), 'reddit', f'r/{sub}', 'airdrop_sub', d.get('score', 0), now))
                conn.commit()
                conn.close()
                all_posts.extend(posts)
            time.sleep(2)
        except:
            continue
    
    if all_posts:
        print(f"  ✓ Airdrop subs: {len(all_posts)} posts from {len(subs)} subs")
    return all_posts



# ============================================================
# X/TWITTER SENTIMENT — Via SentiCrypt (free, no API key)
# ============================================================
def fetch_twitter_sentiment():
    """Fetch Bitcoin Twitter sentiment from SentiCrypt (free API)."""
    try:
        res = requests.get('https://api.senticrypt.com/v2/latest.json', timeout=10)
        if res.status_code != 200:
            print(f"  ✗ Twitter sentiment: API returned {res.status_code}")
            return None
        
        data = res.json()
        
        conn = sqlite3.connect('alphascope.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS twitter_sentiment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            mean_sentiment REAL,
            median_sentiment REAL,
            std_sentiment REAL,
            count INTEGER,
            btc_price REAL,
            volume REAL,
            fetched_at TEXT
        )''')
        now = datetime.now().isoformat()
        
        if isinstance(data, list):
            for entry in data[-7:]:  # Last 7 days
                c.execute('''INSERT INTO twitter_sentiment 
                    (date, mean_sentiment, median_sentiment, std_sentiment, count, btc_price, volume, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                    (entry.get('date'), entry.get('mean'), entry.get('median'),
                     entry.get('std'), entry.get('count'), entry.get('btc_price'),
                     entry.get('volume'), now))
        elif isinstance(data, dict):
            c.execute('''INSERT INTO twitter_sentiment 
                (date, mean_sentiment, median_sentiment, std_sentiment, count, btc_price, volume, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (data.get('date'), data.get('mean'), data.get('median'),
                 data.get('std'), data.get('count'), data.get('btc_price'),
                 data.get('volume'), now))
        
        conn.commit()
        conn.close()
        
        # Display latest
        if isinstance(data, list) and data:
            latest = data[-1]
        else:
            latest = data
        
        mean = latest.get('mean', 0) or 0
        mood = "BULLISH" if mean > 0.05 else "BEARISH" if mean < -0.05 else "NEUTRAL"
        count = latest.get('count', 0) or 0
        print(f"  ✓ X/Twitter BTC sentiment: {mean:+.3f} ({mood}) from {count:,} tweets")
        return data
    except Exception as e:
        print(f"  ✗ Twitter sentiment: {e}")
        return None

# ============================================================
# CRYPTO NEWS SENTIMENT — Via cryptocurrency.cv (free, no key)
# ============================================================
def fetch_crypto_news():
    """Fetch latest crypto news with sentiment from cryptocurrency.cv."""
    try:
        res = requests.get('https://cryptocurrency.cv/api/news?limit=20', timeout=10)
        if res.status_code != 200:
            print(f"  ✗ Crypto news: API returned {res.status_code}")
            return None
        
        articles = res.json()
        if isinstance(articles, dict):
            articles = articles.get('articles', articles.get('data', []))
        
        conn = sqlite3.connect('alphascope.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS crypto_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            source TEXT,
            tickers TEXT,
            sentiment TEXT,
            url TEXT,
            pub_date TEXT,
            fetched_at TEXT
        )''')
        now = datetime.now().isoformat()
        
        stored = 0
        for article in articles[:15]:
            title = article.get('title', '')
            if not title:
                continue
            tickers = ','.join(article.get('tickers', [])) if isinstance(article.get('tickers'), list) else str(article.get('tickers', ''))
            c.execute('''INSERT INTO crypto_news (title, source, tickers, sentiment, url, pub_date, fetched_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      (title, article.get('source', article.get('source_key', '')),
                       tickers, article.get('sentiment', ''),
                       article.get('link', article.get('url', '')),
                       article.get('pub_date', ''), now))
            stored += 1
        
        conn.commit()
        conn.close()
        print(f"  ✓ Crypto news: {stored} articles")
        return articles
    except Exception as e:
        print(f"  ✗ Crypto news: {e}")
        return None


def fetch_all():
    print(f"\n{'='*60}")
    print(f"  🔍 AlphaScope — Data Fetch at {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    
    fetch_fear_greed()
    fetch_trending()
    fetch_all_watchlist()
    fetch_reddit_and_detect_narratives()
    fetch_all_telegram()
    fetch_new_listings()
    time.sleep(6)
    detect_hidden_gems()
    scan_airdrops()
    fetch_airdrop_reddit()

    fetch_x_sentiment()
    
    print(f"{'='*60}")
    print(f"  ✓ All fetches complete!")
    print(f"{'='*60}\n")

if __name__ == '__main__':
    init_db()
    fetch_all()


