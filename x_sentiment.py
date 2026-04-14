"""
AlphaScope — X/Twitter Sentiment Monitor
Searches crypto cashtags on X and analyzes sentiment.
"""

import requests
import sqlite3
import time
from datetime import datetime

TWITTER_API_KEY = "new1_1597ef833361479ba82c88ff32b2fb8c"
CASHTAGS = ['$BTC', '$ETH', '$SOL', '$LINK', '$ARB', '$SUI', '$DOGE', '$AVAX']

POSITIVE = ['bull', 'moon', 'pump', 'buy', 'long', 'breakout', 'surge', 'rally', 'green', 'up', 'ath', 'accumulate', 'bullish', 'send it', 'lets go']
NEGATIVE = ['bear', 'dump', 'sell', 'short', 'crash', 'drop', 'red', 'down', 'rekt', 'scam', 'rug', 'bearish', 'dead', 'over']

def init_x_tables():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS x_tweets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cashtag TEXT, username TEXT, text TEXT,
        likes INTEGER, retweets INTEGER, views INTEGER,
        fetched_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS x_sentiment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cashtag TEXT, tweet_count INTEGER, avg_likes REAL,
        total_engagement INTEGER, sentiment_score REAL,
        sentiment_label TEXT, buzz_level TEXT, top_tweet TEXT,
        fetched_at TEXT
    )''')
    conn.commit()
    conn.close()

def save_tweets(cashtag, tweets, now):
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    for t in tweets:
        author = t.get("author", {})
        c.execute('INSERT INTO x_tweets (cashtag, username, text, likes, retweets, views, fetched_at) VALUES (?,?,?,?,?,?,?)',
            (cashtag, author.get("userName", ""), t.get("text", "")[:500],
             t.get("likeCount", 0), t.get("retweetCount", 0), t.get("viewCount", 0), now))
    conn.commit()
    conn.close()

def save_sentiment(cashtag, tweet_count, avg_likes, engagement, score, label, buzz, top_tweet, now):
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute('''INSERT INTO x_sentiment (cashtag, tweet_count, avg_likes, total_engagement,
                 sentiment_score, sentiment_label, buzz_level, top_tweet, fetched_at)
                 VALUES (?,?,?,?,?,?,?,?,?)''',
              (cashtag, tweet_count, avg_likes, engagement, score, label, buzz, top_tweet, now))
    conn.commit()
    conn.close()

def fetch_x_sentiment():
    """Fetch and analyze X sentiment for all monitored cashtags."""
    init_x_tables()
    print("  Fetching X/Twitter sentiment...")
    now = datetime.now().isoformat()

    for cashtag in CASHTAGS:
        try:
            res = requests.get(
                "https://api.twitterapi.io/twitter/tweet/advanced_search",
                headers={"X-API-Key": TWITTER_API_KEY},
                params={"query": cashtag, "queryType": "Latest", "cursor": ""},
                timeout=15
            )

            if res.status_code == 429:
                print(f"    ⏳ {cashtag}: rate limited, skipping")
                time.sleep(3)
                continue

            if res.status_code != 200:
                print(f"    ✗ {cashtag}: HTTP {res.status_code}")
                continue

            tweets = res.json().get("tweets", [])[:20]
            if not tweets:
                print(f"    ✗ {cashtag}: no tweets")
                continue

            # Save raw tweets
            save_tweets(cashtag, tweets, now)

            # Calculate engagement
            total_likes = sum(t.get('likeCount', 0) for t in tweets)
            total_retweets = sum(t.get('retweetCount', 0) for t in tweets)
            total_replies = sum(t.get('replyCount', 0) for t in tweets)
            total_engagement = total_likes + total_retweets + total_replies
            avg_likes = total_likes / len(tweets)

            # Sentiment analysis
            pos = sum(1 for t in tweets if any(w in t.get('text', '').lower() for w in POSITIVE))
            neg = sum(1 for t in tweets if any(w in t.get('text', '').lower() for w in NEGATIVE))
            score = (pos - neg) / len(tweets)
            label = "BULLISH" if score > 0.15 else "BEARISH" if score < -0.15 else "NEUTRAL"
            buzz = "HIGH" if len(tweets) >= 15 else "MEDIUM" if len(tweets) >= 8 else "LOW"

            # Top tweet
            top = max(tweets, key=lambda t: t.get('likeCount', 0) + t.get('retweetCount', 0))
            top_text = top.get('text', '')[:200]

            # Save sentiment
            save_sentiment(cashtag, len(tweets), avg_likes, total_engagement, score, label, buzz, top_text, now)

            emoji = "🟢" if score > 0.1 else "🔴" if score < -0.1 else "🟡"
            print(f"    {emoji} {cashtag}: {label} ({score:+.2f}) | {len(tweets)} tweets | {total_engagement} engagement")

        except Exception as e:
            print(f"    ✗ {cashtag}: {e}")

        time.sleep(2)

def load_x_sentiment():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    import pandas as pd
    df = pd.read_sql_query(
        "SELECT cashtag, tweet_count, sentiment_score, sentiment_label, buzz_level, top_tweet FROM x_sentiment ORDER BY fetched_at DESC LIMIT 10", conn)
    conn.close()
    return df

if __name__ == '__main__':
    print("🔍 AlphaScope — X/Twitter Sentiment")
    print("=" * 50)
    fetch_x_sentiment()
    print("\nResults:")
    df = load_x_sentiment()
    for _, r in df.iterrows():
        emoji = "🟢" if r['sentiment_score'] > 0.1 else "🔴" if r['sentiment_score'] < -0.1 else "🟡"
        print(f"  {emoji} {r['cashtag']}: {r['sentiment_label']} ({r['sentiment_score']:+.2f}) | {r['tweet_count']} tweets | buzz: {r['buzz_level']}")
