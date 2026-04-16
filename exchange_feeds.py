"""
AlphaScope — Exchange listing intelligence
Monitors 14+ exchanges including the listing-first ones (KuCoin, Gate, MEXC)
where 10-50x alpha signals originate.
"""

import requests
import sqlite3
import re
import time
from datetime import datetime
from coin_registry import registry

# Exchange feeds organized by alpha potential tier
EXCHANGE_FEEDS = [
    # Tier 1 — Major exchanges (high confidence, lower upside)
    ('Binance', 'https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query?catalogId=48&pageNo=1&pageSize=20', 'json_binance'),
    ('Coinbase', 'https://blog.coinbase.com/feed', 'rss'),
    ('Kraken', 'https://blog.kraken.com/feed', 'rss'),
    
    # Tier 2 — Listing-first exchanges (highest alpha potential!)
    ('KuCoin', 'https://www.kucoin.com/_api/cms/articles?page=1&pageSize=20&category=listing', 'json_kucoin'),
    ('Gate.io', 'https://www.gate.com/api/v3/announcement_html/list?type=newlistings&page=1&limit=20', 'json_gate'),
    ('MEXC', 'https://www.mexc.com/api/operateactivity/article/list?page=1&pageSize=20&type=2', 'json_mexc'),
    ('OKX', 'https://www.okx.com/api/v5/support/announcements?annType=announcements-new-listings&page=1', 'json_okx'),
    ('Bitget', 'https://www.bitget.com/v1/cms/helpCenter/content/section/articles?firstSectionId=27&secondarySectionId=148&language=en_US&pageNo=1&pageSize=20', 'json_bitget'),
    ('Bybit', 'https://api2.bybit.com/announcements/api/search/v1/index/announcement-result?category=new_crypto&page_no=1&page_size=20&locale=en-US', 'json_bybit'),
    
    # Tier 3 — Asia-first (very early signals)
    ('Upbit KR', 'https://api-manager.upbit.com/api/v1/announcements?os=web&per_page=20&category=trade', 'json_upbit'),
    ('Bithumb KR', 'https://feed.bithumb.com/notice', 'rss'),
    ('BingX', 'https://bingx.com/en-us/support/notice-center/articles/feed.xml', 'rss'),
    
    # Tier 4 — Specialty  
    ('LBank', 'https://www.lbank.com/cms-api/v1/articles?categoryCode=announcement&pageNum=1&pageSize=20', 'json_lbank'),
]

LISTING_KEYWORDS = ['list', 'listing', 'new trading pair', 'launches', 'adds support',
                    'supports', 'now available', 'trading open', 'will list',
                    'innovation zone', 'kickstarter', 'launchpad', 'launchpool',
                    '신규', '상장', '新币上线', '上線', 'spot', 'futures', 'usdt']

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

def init_listings_table():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS exchange_listings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exchange TEXT, exchange_tier INTEGER,
        coin TEXT, title TEXT, listing_date TEXT,
        status TEXT, url TEXT, fetched_at TEXT,
        UNIQUE(exchange, title))''')
    conn.commit()
    conn.close()

def extract_tickers(title):
    """Pull ticker symbols from listing announcements."""
    tickers = set()
    # Patterns: (BTC), $BTC, BTC/USDT, BTCUSDT
    for match in re.findall(r'\(([A-Z][A-Z0-9]{1,5})\)', title):
        tickers.add(match)
    for match in re.findall(r'\$([A-Z][A-Z0-9]{1,5})\b', title):
        tickers.add(match)
    for match in re.findall(r'\b([A-Z][A-Z0-9]{1,5})/USDT\b', title):
        tickers.add(match)
    for match in re.findall(r'\b([A-Z][A-Z0-9]{1,5})USDT\b', title):
        tickers.add(match)
    # Filter false positives
    return [t for t in tickers if t not in {'USDT', 'USDC', 'USD', 'NEW', 'NFT', 'API', 'KYC'}]

def parse_rss(content):
    items = re.findall(r'<item[^>]*>(.*?)</item>', content, re.DOTALL)
    parsed = []
    for item in items[:20]:
        t = re.search(r'<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item, re.DOTALL)
        l = re.search(r'<link[^>]*>(.*?)</link>', item, re.DOTALL)
        if t:
            parsed.append({
                'title': re.sub(r'<[^>]+>', '', t.group(1)).strip(),
                'url': l.group(1).strip() if l else ''
            })
    return parsed

def parse_atom(content):
    """Atom feed parser."""
    items = re.findall(r'<entry[^>]*>(.*?)</entry>', content, re.DOTALL)
    parsed = []
    for item in items[:20]:
        t = re.search(r'<title[^>]*>(.*?)</title>', item, re.DOTALL)
        l = re.search(r'<link[^>]*href="([^"]*)"', item)
        if t:
            parsed.append({
                'title': re.sub(r'<[^>]+>', '', t.group(1)).strip(),
                'url': l.group(1) if l else ''
            })
    return parsed

def parse_exchange_response(exchange, src_type, response_text):
    """Each exchange returns different JSON shapes — parse accordingly."""
    if src_type == 'rss':
        return parse_rss(response_text)
    if src_type == 'atom':
        return parse_atom(response_text)
    
    try:
        data = response_text if isinstance(response_text, dict) else __import__('json').loads(response_text)
    except:
        return []
    
    items = []
    if src_type == 'json_binance':
        articles = data.get('data', {}).get('articles', [])
        for a in articles:
            items.append({'title': a.get('title', ''),
                         'url': f"https://www.binance.com/en/support/announcement/{a.get('code', '')}"})
    elif src_type == 'json_kucoin':
        for a in data.get('items', data.get('data', [])):
            items.append({'title': a.get('title', a.get('annTitle', '')),
                         'url': a.get('annUrl', a.get('url', ''))})
    elif src_type == 'json_gate':
        for a in data.get('data', {}).get('list', []):
            items.append({'title': a.get('title', ''),
                         'url': f"https://www.gate.com/article/{a.get('id', '')}"})
    elif src_type == 'json_mexc':
        for a in data.get('data', {}).get('results', data.get('data', {}).get('list', [])):
            items.append({'title': a.get('title', ''),
                         'url': a.get('link', '')})
    elif src_type == 'json_okx':
        for cat in data.get('data', []):
            for a in cat.get('details', []):
                items.append({'title': a.get('title', ''),
                             'url': a.get('url', '')})
    elif src_type == 'json_bitget':
        for a in data.get('data', {}).get('items', []):
            items.append({'title': a.get('title', ''),
                         'url': a.get('contentUrl', '')})
    elif src_type == 'json_bybit':
        for a in data.get('result', {}).get('list', []):
            items.append({'title': a.get('title', ''),
                         'url': a.get('url', '')})
    elif src_type == 'json_upbit':
        for n in data.get('data', {}).get('notices', []):
            items.append({'title': n.get('title', ''),
                         'url': f"https://upbit.com/service_center/notice?id={n.get('id', '')}"})
    elif src_type == 'json_lbank':
        for a in data.get('data', {}).get('list', []):
            items.append({'title': a.get('title', ''),
                         'url': a.get('url', '')})
    
    return items

def get_tier(exchange):
    tier1 = {'Binance', 'Coinbase', 'Kraken'}
    tier2 = {'KuCoin', 'Gate.io', 'MEXC', 'OKX', 'Bitget', 'Bybit'}
    tier3 = {'Upbit KR', 'Bithumb KR', 'BingX'}
    if exchange in tier1: return 1
    if exchange in tier2: return 2
    if exchange in tier3: return 3
    return 4

def fetch_exchange_listings():
    """Pull announcements from all exchanges, detect new listings."""
    init_listings_table()
    print("  Fetching exchange listings...")
    now = datetime.now().isoformat()
    
    total_found = 0
    
    for exchange, url, src_type in EXCHANGE_FEEDS:
        try:
            res = requests.get(url, headers=UA, timeout=15)
            if res.status_code != 200:
                print(f"    {exchange}: HTTP {res.status_code}")
                continue
            
            items = parse_exchange_response(exchange, src_type, res.text)
            tier = get_tier(exchange)
            new_count = 0
            
            conn = sqlite3.connect('alphascope.db', timeout=30)
            c = conn.cursor()
            
            for item in items[:15]:
                title = item.get('title', '')
                if not title:
                    continue
                
                title_lower = title.lower()
                if not any(kw in title_lower for kw in LISTING_KEYWORDS):
                    continue
                
                tickers = extract_tickers(title)
                
                # Insert (UNIQUE constraint prevents duplicates)
                try:
                    c.execute('''INSERT INTO exchange_listings 
                                 (exchange, exchange_tier, coin, title, listing_date, status, url, fetched_at)
                                 VALUES (?,?,?,?,?,?,?,?)''',
                        (exchange, tier, ','.join(tickers), title[:300], now, 'NEW',
                         item.get('url', ''), now))
                    
                    # Also signal with appropriate priority
                    priority = 200 if tier == 2 else 100  # Tier 2 = highest alpha
                    c.execute('''INSERT INTO signals (source, source_detail, signal_type, title, content, coin,
                                 sentiment_score, sentiment_label, engagement, url, fetched_at)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                        ('exchange', f'{exchange} (T{tier})', 'LISTING', title, '',
                         ','.join(tickers), 0.5, 'BULLISH', priority, item.get('url', ''), now))
                    new_count += 1
                except sqlite3.IntegrityError:
                    pass  # Already have it
            
            conn.commit()
            conn.close()
            total_found += new_count
            
            tier_label = ['', '🥇', '🥈', '🥉', '4️⃣'][tier]
            print(f"    {tier_label} {exchange}: {len(items)} announcements scanned, {new_count} new listings")
        except Exception as e:
            print(f"    {exchange} failed: {e}")
        time.sleep(1)
    
    print(f"  Total new listings: {total_found}")

if __name__ == '__main__':
    fetch_exchange_listings()
