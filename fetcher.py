"""
AlphaScope v2.0 — Dynamic Alpha Discovery
No static watchlists. System discovers what matters.
Sources: X/Twitter, Reddit, Telegram, CoinGecko
"""

import requests
import sqlite3
import time
import re
import json
from datetime import datetime
from coin_registry import registry

# ============================================================
# CONFIG
# ============================================================
CASHTAGS = ['$BTC', '$ETH', '$SOL', '$LINK', '$ARB', '$SUI', '$DOGE', '$AVAX']

TELEGRAM_CHANNELS = ['whale_alert_io', 'crypto', 'blockchain', 'CoinTelegraph', 'AirdropOfficial', 'FatPigSignals']

REDDIT_SUBS = ['cryptocurrency', 'bitcoin', 'ethereum', 'solana', 'CryptoMarkets',
               'ethtrader', 'SatoshiStreetBets', 'CryptoMoonShots', 'altcoin', 'defi',
               'cosmosnetwork', 'algorand', 'cardano']

AIRDROP_SUBS = ['CryptoAirdrop', 'airdropalert', 'CryptoAirdrops']

TWITTER_API_KEY = "new1_1597ef833361479ba82c88ff32b2fb8c"

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

AIRDROP_KEYWORDS = ['airdrop', 'free mint', 'token launch', 'ico', 'ido', 'presale',
                     'fair launch', 'testnet reward', 'points program', 'claim',
                     'eligibility', 'snapshot', 'tge', 'whitelist']

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
    conn.commit()
    conn.close()
    print("Database ready")

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

    print(f"  Fetching prices for {len(coins_to_fetch)} buzzing coins...")
    for ticker in coins_to_fetch:
        coin_id = registry.coingecko_map.get(ticker)
        if not coin_id:
            continue
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
            print(f"    {data.get('name')}: ${price:,.2f} ({change:+.1f}%)")
        except Exception as e:
            print(f"    {ticker} failed: {e}")
        time.sleep(6)

# ============================================================
# REDDIT
# ============================================================
def fetch_reddit_data():
    print("  Fetching Reddit...")
    headers = {'User-Agent': 'AlphaScope/2.0'}
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

    conn = get_db()
    c = conn.cursor()
    for coin, data in sorted(coin_mentions.items(), key=lambda x: -x[1]['count']):
        avg_sent = data['sentiment_sum'] / max(data['count'], 1)
        subs_list = ','.join(list(data['subs'])[:5])
        c.execute('INSERT INTO coin_buzz (coin, mention_count, total_engagement, avg_sentiment, sources, fetched_at) VALUES (?,?,?,?,?,?)',
            (coin, data['count'], data['engagement'], round(avg_sent, 2), f"reddit:{subs_list}", now))

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

def detect_hidden_gems():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT name, symbol, market_cap_rank FROM trending ORDER BY fetched_at DESC LIMIT 10")
    trending = c.fetchall()
    now = datetime.now().isoformat()
    gems = []
    for name, symbol, rank in trending:
        if rank and rank > 50:
            c.execute('INSERT INTO hidden_gems (name, symbol, market_cap_rank, signal_type, signal_detail, fetched_at) VALUES (?,?,?,?,?,?)',
                (name, symbol, rank, 'LOW_CAP_TRENDING', f'Rank #{rank} but trending', now))
            gems.append(f"{symbol}(#{rank})")
    conn.commit()
    conn.close()
    if gems:
        print(f"  Hidden gems: {', '.join(gems)}")

def fetch_all():
    print(f"\n{'='*60}")
    print(f"  AlphaScope v2.0 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    fetch_fear_greed()
    fetch_trending()
    fetch_reddit_data()
    fetch_telegram_data()
    detect_hidden_gems()
    fetch_buzzing_prices()
    registry.save()
    stats = registry.get_stats()
    print(f"  Registry: {stats['total_known']} known, {stats['graduated']} learned, {stats['pending']} pending")
    print(f"{'='*60}\n")

if __name__ == '__main__':
    init_db()
    fetch_all()
