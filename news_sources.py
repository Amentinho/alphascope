"""
AlphaScope — Multilingual News Sources
Scans 10+ crypto news sources across 6 languages.
No translation needed — uses $TICKER detection which is universal.
Tags language in source_detail so user can review non-English signals manually.
"""

import requests
import sqlite3
import re
import time
from datetime import datetime
from coin_registry import registry

# Sources: (name, url, language, feed_type)
# All free, no API keys needed
NEWS_SOURCES = [
    # English — Major outlets
    ('CoinDesk', 'https://www.coindesk.com/arc/outboundfeeds/rss/', 'EN', 'rss'),
    ('CoinTelegraph', 'https://cointelegraph.com/rss', 'EN', 'rss'),
    ('The Block', 'https://www.theblock.co/rss.xml', 'EN', 'rss'),
    ('Decrypt', 'https://decrypt.co/feed', 'EN', 'rss'),
    ('Bitcoin Magazine', 'https://bitcoinmagazine.com/.rss/full/', 'EN', 'rss'),
    
    # Chinese — Moves markets early
    ('8btc', 'https://www.8btc.com/feed', 'CN', 'rss'),
    
    # Japanese
    ('CoinPost JP', 'https://coinpost.jp/?feed=rss2', 'JP', 'rss'),
    
    # Russian
    ('Forklog', 'https://forklog.com/feed/', 'RU', 'rss'),
    
    # Spanish
    ('CoinTelegraph ES', 'https://es.cointelegraph.com/rss', 'ES', 'rss'),
    
    # Portuguese (Brazil — large crypto market)
    ('CoinTelegraph BR', 'https://br.cointelegraph.com/rss', 'BR', 'rss'),
]

# DeFi-specific data sources (JSON APIs, no key needed)
DEFI_SOURCES = [
    ('DeFi Llama', 'https://api.llama.fi/protocols', 'defi_llama'),
    ('DeFi Llama Yields', 'https://yields.llama.fi/pools', 'defi_yields'),
]

# Listing-related keywords across languages
LISTING_KEYWORDS_MULTI = [
    # English
    'listing', 'listed', 'launches', 'now available', 'trading open',
    # Chinese
    '上线', '上線', '新币',
    # Korean
    '상장', '신규',
    # Japanese
    '上場', '取扱開始',
]


def parse_rss(content):
    """Extract items from RSS/Atom feed."""
    # Try RSS format first
    items = re.findall(r'<item[^>]*>(.*?)</item>', content, re.DOTALL)
    if not items:
        # Try Atom format
        items = re.findall(r'<entry[^>]*>(.*?)</entry>', content, re.DOTALL)
    
    parsed = []
    for item in items[:20]:
        title = re.search(r'<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item, re.DOTALL)
        link = re.search(r'<link[^>]*(?:href="([^"]*)"[^>]*/?>|>(.*?)</link>)', item, re.DOTALL)
        desc = re.search(r'<description[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>', item, re.DOTALL)
        pub_date = re.search(r'<pubDate[^>]*>(.*?)</pubDate>', item, re.DOTALL)
        
        if title:
            link_url = ''
            if link:
                link_url = link.group(1) or link.group(2) or ''
            
            parsed.append({
                'title': re.sub(r'<[^>]+>', '', title.group(1)).strip()[:300],
                'link': link_url.strip(),
                'description': re.sub(r'<[^>]+>', '', desc.group(1)).strip()[:500] if desc else '',
                'pub_date': pub_date.group(1).strip() if pub_date else '',
            })
    return parsed


def detect_coins_in_text(text, source='news'):
    """Detect coin tickers in any language text."""
    found = set()
    
    # $TICKER format (universal across all languages)
    for match in re.findall(r'\$([A-Za-z]{2,6})\b', text):
        ticker = match.upper()
        if ticker.lower() in registry.tickers:
            found.add(registry.tickers[ticker.lower()])
        elif len(ticker) <= 5:
            found.add(ticker)
            registry.record_ticker(ticker, source)
    
    # TICKER/USDT format (exchange pairs mentioned in articles)
    for match in re.findall(r'\b([A-Z][A-Z0-9]{1,5})/(?:USDT|USD|BTC|ETH)\b', text):
        if match.lower() in registry.tickers:
            found.add(registry.tickers[match.lower()])
        else:
            found.add(match)
            registry.record_ticker(match, source)
    
    # Known coin keywords (works across languages since project names are English)
    text_lower = text.lower()
    for keyword, ticker in registry.tickers.items():
        if len(keyword) >= 3:  # Skip very short keywords to avoid false positives
            if f' {keyword} ' in f' {text_lower} ' or f' {keyword}.' in f' {text_lower}':
                found.add(ticker)
    
    return list(found)


def classify_article(title, description=''):
    """Classify article as NEWS, ALPHA, AIRDROP, or LISTING."""
    text = (title + ' ' + description).lower()
    
    # Check for airdrop
    airdrop_kw = ['airdrop', 'free mint', 'token launch', 'presale', 'claim',
                  'eligibility', 'snapshot', 'tge', 'whitelist', 'testnet reward']
    if any(kw in text for kw in airdrop_kw):
        return 'AIRDROP'
    
    # Check for listing
    if any(kw in text for kw in LISTING_KEYWORDS_MULTI):
        return 'LISTING'
    
    # Check for alpha signals
    alpha_kw = ['partnership', 'integration', 'mainnet', 'launch', 'upgrade',
                'breaking', 'exclusive', 'first', 'revolutionary', 'billion']
    if any(kw in text for kw in alpha_kw):
        return 'ALPHA'
    
    return 'NEWS'


def fetch_news_sources():
    """Scan all news sources, detect tickers, classify articles."""
    print("  Fetching news (multilingual)...")
    now = datetime.now().isoformat()
    total = 0
    coin_mentions = {}
    
    for name, url, lang, feed_type in NEWS_SOURCES:
        try:
            res = requests.get(url, headers={'User-Agent': 'AlphaScope/2.1'}, timeout=15)
            if res.status_code != 200:
                print(f"    {name} [{lang}]: HTTP {res.status_code}")
                continue
            
            articles = parse_rss(res.text)
            if not articles:
                print(f"    {name} [{lang}]: 0 articles parsed")
                continue
            
            conn = sqlite3.connect('alphascope.db', timeout=30)
            c = conn.cursor()
            
            for article in articles:
                text = article['title'] + ' ' + article.get('description', '')
                coins = detect_coins_in_text(text, f'news:{name}')
                signal_type = classify_article(article['title'], article.get('description', ''))
                
                # Track coin mentions from news
                for coin in coins:
                    coin_mentions[coin] = coin_mentions.get(coin, 0) + 1
                
                c.execute('''INSERT INTO signals (source, source_detail, signal_type, title, content, coin,
                             sentiment_score, sentiment_label, engagement, url, fetched_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    ('news', f'{name} [{lang}]', signal_type,
                     article['title'], article.get('description', '')[:300],
                     ','.join(coins), 0.0, 'NEUTRAL', 0,
                     article.get('link', ''), now))
                total += 1
            
            conn.commit()
            conn.close()
            print(f"    {name} [{lang}]: {len(articles)} articles")
        except Exception as e:
            print(f"    {name} [{lang}] failed: {e}")
        time.sleep(1)
    
    print(f"  News total: {total} articles across {len(NEWS_SOURCES)} sources")
    if coin_mentions:
        top = sorted(coin_mentions.items(), key=lambda x: -x[1])[:5]
        print(f"  News mentions: {', '.join(f'{c}({n})' for c, n in top)}")


def fetch_defi_data():
    """Fetch DeFi protocol data from DeFi Llama (TVL changes = alpha signal)."""
    print("  Fetching DeFi data...")
    now = datetime.now().isoformat()
    
    try:
        res = requests.get('https://api.llama.fi/protocols', timeout=15)
        if res.status_code != 200:
            print(f"    DeFi Llama: HTTP {res.status_code}")
            return
        
        protocols = res.json()
        
        # Find protocols with significant TVL changes (potential alpha)
        conn = sqlite3.connect('alphascope.db', timeout=30)
        c = conn.cursor()
        
        interesting = []
        for p in protocols[:200]:  # Top 200 by TVL
            name = p.get('name', '')
            symbol = p.get('symbol', '').upper()
            tvl = p.get('tvl', 0) or 0
            change_1d = p.get('change_1d', 0) or 0
            change_7d = p.get('change_7d', 0) or 0
            
            # Flag protocols with big TVL moves
            if abs(change_1d) >= 10 or abs(change_7d) >= 30:
                direction = 'BULLISH' if change_1d > 0 else 'BEARISH'
                title = f"{name} ({symbol}): TVL {change_1d:+.1f}% 24h, {change_7d:+.1f}% 7d — ${tvl/1e6:.0f}M TVL"
                
                c.execute('''INSERT INTO signals (source, source_detail, signal_type, title, content, coin,
                             sentiment_score, sentiment_label, engagement, url, fetched_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    ('defi', 'DeFi Llama', 'ALPHA', title, '',
                     symbol, 0.5 if change_1d > 0 else -0.5, direction,
                     int(tvl / 1000), f"https://defillama.com/protocol/{p.get('slug', '')}", now))
                
                interesting.append(f"{symbol}({change_1d:+.0f}%)")
                
                # Register ticker
                if symbol and len(symbol) <= 6:
                    registry.record_ticker(symbol, 'defi:llama')
        
        conn.commit()
        conn.close()
        
        if interesting:
            print(f"    TVL movers: {', '.join(interesting[:8])}")
        else:
            print(f"    No significant TVL changes detected")
    except Exception as e:
        print(f"    DeFi Llama failed: {e}")


def fetch_defi_yields():
    """Fetch top DeFi yields — useful for airdrop farming opportunities."""
    try:
        res = requests.get('https://yields.llama.fi/pools', timeout=15)
        if res.status_code != 200:
            return
        
        pools = res.json().get('data', [])
        
        # Find unusually high yields (possible airdrop farming or new protocols)
        conn = sqlite3.connect('alphascope.db', timeout=30)
        c = conn.cursor()
        now = datetime.now().isoformat()
        
        high_yield = []
        for pool in pools[:500]:
            apy = pool.get('apy', 0) or 0
            tvl = pool.get('tvlUsd', 0) or 0
            project = pool.get('project', '')
            symbol = pool.get('symbol', '')
            chain = pool.get('chain', '')
            
            # High APY + decent TVL = interesting
            if apy >= 50 and tvl >= 100000:
                title = f"{project}: {symbol} on {chain} — {apy:.0f}% APY (${tvl/1e6:.1f}M TVL)"
                c.execute('''INSERT INTO signals (source, source_detail, signal_type, title, content, coin,
                             sentiment_score, sentiment_label, engagement, url, fetched_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    ('defi', f'DeFi Yields [{chain}]', 'ALPHA', title, '',
                     project.upper()[:6], 0.3, 'BULLISH', int(tvl / 1000),
                     f"https://defillama.com/yields/pool/{pool.get('pool', '')}", now))
                high_yield.append(f"{project}({apy:.0f}%)")
        
        conn.commit()
        conn.close()
        
        if high_yield:
            print(f"    High yields: {', '.join(high_yield[:5])}")
    except Exception as e:
        print(f"    DeFi yields failed: {e}")


if __name__ == '__main__':
    print("AlphaScope — News & DeFi Sources Test")
    print("=" * 50)
    fetch_news_sources()
    print()
    fetch_defi_data()
    fetch_defi_yields()
