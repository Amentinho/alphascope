"""
AlphaScope — Telegram Monitor
Monitors public crypto Telegram channels for alpha signals.

SETUP:
1. Go to https://my.telegram.org/auth
2. Log in with your phone number
3. Click "API development tools"
4. Create an app — get your api_id and api_hash
5. Add them to .env:
   TELEGRAM_API_ID=your_id
   TELEGRAM_API_HASH=your_hash
"""

import sqlite3
import asyncio
import os
from datetime import datetime

# ============================================================
# DATABASE
# ============================================================
def init_telegram_db():
    conn = sqlite3.connect('alphascope.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS telegram_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT,
        message TEXT,
        sender TEXT,
        date TEXT,
        views INTEGER,
        fetched_at TEXT
    )''')
    conn.commit()
    conn.close()

# ============================================================
# PUBLIC CHANNEL SCRAPER (no API key needed!)
# ============================================================
def fetch_telegram_public(channel_username, limit=20):
    """
    Fetch recent messages from a public Telegram channel.
    Uses Telegram's public web preview — no API key needed!
    """
    import requests
    
    try:
        # Telegram's public preview endpoint
        url = f"https://t.me/s/{channel_username}"
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code != 200:
            print(f"  ✗ @{channel_username}: HTTP {res.status_code}")
            return []
        
        # Parse messages from HTML
        from html.parser import HTMLParser
        
        messages = []
        
        class TelegramParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_message = False
                self.in_text = False
                self.in_views = False
                self.current_text = ""
                self.current_views = ""
                
            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                cls = attrs_dict.get('class', '')
                if 'tgme_widget_message_text' in cls:
                    self.in_text = True
                    self.current_text = ""
                if 'tgme_widget_message_views' in cls:
                    self.in_views = True
                    self.current_views = ""
                    
            def handle_endtag(self, tag):
                if self.in_text and tag in ('div', 'span'):
                    self.in_text = False
                    if self.current_text.strip():
                        messages.append({
                            'text': self.current_text.strip()[:500],
                            'views': 0
                        })
                if self.in_views:
                    self.in_views = False
                    if messages and self.current_views.strip():
                        try:
                            views_str = self.current_views.strip().replace('K', '000').replace('M', '000000').replace('.', '')
                            messages[-1]['views'] = int(float(views_str))
                        except:
                            pass
                    
            def handle_data(self, data):
                if self.in_text:
                    self.current_text += data
                if self.in_views:
                    self.current_views += data
        
        parser = TelegramParser()
        parser.feed(res.text)
        
        # Store in database
        conn = sqlite3.connect('alphascope.db')
        c = conn.cursor()
        now = datetime.now().isoformat()
        
        stored = 0
        for msg in messages[-limit:]:
            if len(msg['text']) > 10:  # Skip very short messages
                c.execute('''INSERT INTO telegram_messages 
                             (channel, message, sender, date, views, fetched_at)
                             VALUES (?, ?, ?, ?, ?, ?)''',
                          (channel_username, msg['text'], '', now, msg['views'], now))
                stored += 1
        
        conn.commit()
        conn.close()
        
        print(f"  ✓ @{channel_username}: {stored} messages")
        return messages
    except Exception as e:
        print(f"  ✗ @{channel_username}: {e}")
        return []

# ============================================================
# CRYPTO ALPHA CHANNELS (public channels you can customize)
# ============================================================
CHANNELS = [
    'whale_alert_io',
    'crypto',
    'blockchain',
]

def fetch_all_telegram():
    """Fetch messages from all monitored Telegram channels."""
    import time
    
    print("  Fetching Telegram channels...")
    init_telegram_db()
    
    for channel in CHANNELS:
        fetch_telegram_public(channel, limit=10)
        time.sleep(2)  # Be polite

def load_telegram_messages(limit=20):
    """Load recent Telegram messages from database."""
    conn = sqlite3.connect('alphascope.db')
    import pandas as pd
    df = pd.read_sql_query(
        f"SELECT channel, message, views, fetched_at FROM telegram_messages ORDER BY fetched_at DESC LIMIT {limit}",
        conn
    )
    conn.close()
    return df

# ============================================================
# TEST
# ============================================================
if __name__ == '__main__':
    print("🔍 AlphaScope — Telegram Monitor Test")
    print("="*50)
    fetch_all_telegram()
    
    print("\nRecent messages:")
    df = load_telegram_messages(5)
    for _, row in df.iterrows():
        print(f"\n[@{row['channel']}] (👁 {row['views']})")
        print(f"  {row['message'][:100]}...")
