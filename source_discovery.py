"""
AlphaScope Source Discovery v1.0
==================================
Automatically discovers and validates new signal sources:
  - Telegram channels (via t.me scraping + cross-references)
  - Reddit subreddits (via Reddit API subscriber counts)
  - Twitter accounts (stored for manual Twitter API use)

Runs weekly, adds validated sources to fetcher.py dynamically.
New sources are stored in sources.db and gradually promoted to fetcher.py.

Run standalone: python3 source_discovery.py
"""

import os
import re
import json
import time
import sqlite3
import requests
import threading
from datetime import datetime

DISCOVERY_DB = 'sources.db'
MAIN_DB = 'alphascope.db'
MIN_TELEGRAM_MEMBERS = 1000   # minimum channel size
MIN_REDDIT_SUBSCRIBERS = 5000

# Seed channels — used to find cross-referenced channels
SEED_TELEGRAM = [
    'CoinTelegraph', 'whale_alert_io', 'lookonchain', 'solana',
    'ethereum', 'AirdropOfficial', 'dexscreener', 'JupiterExchange',
    'defisignals', 'cryptopanic', 'glassnode', 'arbitrum',
]

# Known good Twitter accounts to track (for future Twitter API integration)
SEED_TWITTER_ACCOUNTS = [
    # On-chain analysts
    'lookonchain', 'spotonchain', 'ZachXBT', 'WuBlockchain',
    'ChainNewsOfficial', 'BanklessHQ', 'TheDefiant',
    # DeFi
    'Uniswap', 'AaveAave', 'CurveFinance', 'PancakeSwap',
    'GMX_IO', 'HyperliquidX', 'dydxprotocol',
    # SOL ecosystem
    'solana', 'JupiterExchange', 'RaydiumProtocol', 'JitoLabs',
    'heliuslabs', 'MagicEden', 'tensor_hq',
    # BASE ecosystem
    'base', 'AerodromeFinance', 'MorphoLabs',
    # ETH
    'ethereum', 'VitalikButerin', 'EigenLayer', 'LidoFinance',
    # AI & DePIN
    'Fetch_ai', 'SingularityNET', 'rendernetwork', 'akashnet',
    # News & research
    'CoinDesk', 'Decrypt', 'TheBlock_', 'MessariCrypto',
    'glassnode', 'CoinMetrics', 'CryptoQuant_Official',
    # Alpha hunters
    'CryptoGodJohn', 'inversebrah', 'cobie', 'hsaka',
    'CryptoCred', 'pentosh1', 'SmokeyJoe_',
    # RWA & institutional
    'Ondo_Finance', 'ChainlinkOfficial', 'goldfinch_finance',
    # Launchpads & gems
    'PinkSaleBSC', 'dx_sale', 'GemHuntersHQ',
]


def _get_db():
    conn = sqlite3.connect(DISCOVERY_DB, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''CREATE TABLE IF NOT EXISTS telegram_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT UNIQUE,
        category TEXT,
        members INTEGER DEFAULT 0,
        status TEXT DEFAULT 'NEW',  -- NEW/VALIDATED/ACTIVE/REJECTED
        quality_score INTEGER DEFAULT 0,
        last_checked TEXT,
        added_to_fetcher INTEGER DEFAULT 0,
        notes TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS reddit_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subreddit TEXT UNIQUE,
        category TEXT,
        subscribers INTEGER DEFAULT 0,
        status TEXT DEFAULT 'NEW',
        quality_score INTEGER DEFAULT 0,
        last_checked TEXT,
        added_to_fetcher INTEGER DEFAULT 0)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS twitter_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        category TEXT,
        followers INTEGER DEFAULT 0,
        status TEXT DEFAULT 'TRACKED',
        added_at TEXT)''')
    conn.commit()
    return conn


# ── Telegram discovery ────────────────────────────────────────────────────────
def _scrape_telegram_channel(channel: str) -> dict:
    """
    Scrape public t.me/s/channel page for basic info.
    Returns {'members': int, 'description': str, 'valid': bool}
    """
    try:
        r = requests.get(f'https://t.me/s/{channel}',
                         headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        if r.status_code != 200:
            return {'valid': False, 'members': 0}

        text = r.text
        # Extract member count
        members = 0
        m = re.search(r'(\d[\d\s,]+)\s*(members|subscribers)', text, re.I)
        if m:
            members = int(re.sub(r'[^\d]', '', m.group(1)))

        # Extract description
        desc = ''
        m = re.search(r'<meta name="description" content="([^"]*)"', text)
        if m:
            desc = m.group(1)[:200]

        # Check if it mentions crypto
        crypto_keywords = ['crypto', 'bitcoin', 'ethereum', 'solana', 'defi',
                          'token', 'blockchain', 'nft', 'web3', 'airdrop',
                          'trading', 'altcoin', 'whale', 'signal']
        is_crypto = any(kw in (desc + text[:500]).lower() for kw in crypto_keywords)

        return {
            'valid': True,
            'members': members,
            'description': desc,
            'is_crypto': is_crypto,
        }
    except Exception as e:
        return {'valid': False, 'members': 0, 'error': str(e)}


def _find_related_channels(channel: str) -> list:
    """Find channels mentioned/recommended in a channel's posts."""
    try:
        r = requests.get(f'https://t.me/s/{channel}',
                         headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        if r.status_code != 200:
            return []
        # Find @mentions that look like channel names
        mentions = re.findall(r't\.me/([a-zA-Z][a-zA-Z0-9_]{4,})', r.text)
        mentions += re.findall(r'@([a-zA-Z][a-zA-Z0-9_]{4,})', r.text)
        # Filter to likely crypto channels
        filtered = []
        skip = {'telegram', 'joinchat', 'share', 'username', 'channel',
                 'group', 'bot', 'support', 'help', 'admin'}
        for m in set(mentions):
            if m.lower() not in skip and len(m) > 4:
                filtered.append(m)
        return filtered[:20]
    except Exception:
        return []


def discover_telegram_channels(max_new=50):
    """
    BFS discovery: start from seed channels, find cross-references,
    validate each one, store in DISCOVERY_DB.
    """
    conn = _get_db()
    now = datetime.now().isoformat()

    # Seed initial channels
    for ch in SEED_TELEGRAM:
        conn.execute(
            "INSERT OR IGNORE INTO telegram_sources (channel, category, added_to_fetcher) VALUES (?,?,0)",
            (ch, 'seed'))
    conn.commit()

    # Get all NEW channels to process
    to_process = conn.execute(
        "SELECT channel FROM telegram_sources WHERE status='NEW' LIMIT 100"
    ).fetchall()

    discovered = 0
    validated = 0

    for (channel,) in to_process:
        if discovered >= max_new:
            break
        time.sleep(1)  # rate limit

        info = _scrape_telegram_channel(channel)
        if not info['valid']:
            conn.execute(
                "UPDATE telegram_sources SET status='REJECTED', last_checked=? WHERE channel=?",
                (now, channel))
            conn.commit()
            continue

        members = info.get('members', 0)
        is_crypto = info.get('is_crypto', False)

        if members >= MIN_TELEGRAM_MEMBERS and is_crypto:
            score = min(10, members // 10000 + (5 if members > 50000 else 0))
            conn.execute(
                """UPDATE telegram_sources
                   SET status='VALIDATED', members=?, quality_score=?, last_checked=?
                   WHERE channel=?""",
                (members, score, now, channel))
            validated += 1
            print(f"    ✅ t.me/{channel}: {members:,} members (score:{score})")
        else:
            conn.execute(
                "UPDATE telegram_sources SET status='REJECTED', members=?, last_checked=? WHERE channel=?",
                (members, now, channel))

        conn.commit()

        # Find related channels from this one
        if is_crypto:
            related = _find_related_channels(channel)
            for rel in related:
                conn.execute(
                    "INSERT OR IGNORE INTO telegram_sources (channel, category, status) VALUES (?,'discovered','NEW')",
                    (rel,))
            conn.commit()
            discovered += len(related)

    conn.close()
    print(f"  📡 Telegram: {validated} validated, {discovered} new candidates queued")
    return validated


# ── Reddit discovery ──────────────────────────────────────────────────────────
def discover_reddit_subs(max_new=100):
    """
    Find related subreddits via Reddit's recommendation API.
    Validates subscriber count and crypto relevance.
    """
    conn = _get_db()
    now = datetime.now().isoformat()

    # Start from known crypto subs
    seed_subs = [
        'cryptocurrency', 'bitcoin', 'ethereum', 'solana', 'defi',
        'CryptoMoonShots', 'altcoin', 'CryptoMarkets', 'SatoshiStreetBets',
    ]

    headers = {'User-Agent': 'AlphaScope/2.0 (+https://github.com/amentinho/alphascope)'}
    validated = 0

    for seed in seed_subs:
        try:
            time.sleep(2)
            r = requests.get(
                f'https://www.reddit.com/r/{seed}/about.json',
                headers=headers, timeout=10)
            if r.status_code != 200:
                continue

            data = r.json().get('data', {})
            subs = data.get('subscribers', 0)

            # Get related subs from sidebar
            r2 = requests.get(
                f'https://www.reddit.com/r/{seed}/wiki/related.json',
                headers=headers, timeout=8)

            # Also get from Reddit's recommendation
            r3 = requests.get(
                f'https://www.reddit.com/r/{seed}/about/sidebar.json',
                headers=headers, timeout=8)

            # Search for crypto subs explicitly
            r4 = requests.get(
                'https://www.reddit.com/subreddits/search.json',
                headers=headers,
                params={'q': f'crypto {seed}', 'limit': 25, 'type': 'sr'},
                timeout=10)

            if r4.status_code == 200:
                results = r4.json().get('data', {}).get('children', [])
                for item in results:
                    sub_data = item.get('data', {})
                    name = sub_data.get('display_name', '')
                    subscribers = sub_data.get('subscribers', 0)
                    desc = (sub_data.get('public_description', '') or '').lower()

                    if not name or subscribers < MIN_REDDIT_SUBSCRIBERS:
                        continue

                    crypto_kw = ['crypto','bitcoin','ethereum','blockchain',
                                 'defi','nft','web3','trading','altcoin']
                    is_crypto = any(kw in desc or kw in name.lower() for kw in crypto_kw)

                    if not is_crypto:
                        continue

                    conn.execute(
                        """INSERT OR IGNORE INTO reddit_sources
                           (subreddit, subscribers, status, quality_score, last_checked)
                           VALUES (?,?,?,?,?)""",
                        (name, subscribers,
                         'VALIDATED' if subscribers > MIN_REDDIT_SUBSCRIBERS else 'REJECTED',
                         min(10, subscribers // 100000), now))
                    conn.commit()
                    validated += 1

        except Exception as e:
            print(f"    Reddit {seed}: {e}")
            continue

    conn.close()
    print(f"  📱 Reddit: {validated} subreddits discovered/updated")
    return validated


# ── Twitter seed storage ──────────────────────────────────────────────────────
def seed_twitter_accounts():
    """Store seed Twitter accounts for future API use."""
    conn = _get_db()
    now = datetime.now().isoformat()
    added = 0

    # Categorize accounts
    categories = {
        'onchain': ['lookonchain','spotonchain','ZachXBT','WuBlockchain'],
        'defi': ['Uniswap','AaveAave','CurveFinance','GMX_IO','HyperliquidX'],
        'sol': ['solana','JupiterExchange','RaydiumProtocol','JitoLabs','heliuslabs'],
        'base': ['base','AerodromeFinance','MorphoLabs'],
        'eth': ['ethereum','VitalikButerin','EigenLayer','LidoFinance'],
        'ai': ['Fetch_ai','SingularityNET','rendernetwork'],
        'news': ['CoinDesk','Decrypt','TheBlock_','MessariCrypto'],
        'macro': ['glassnode','CoinMetrics','CryptoQuant_Official'],
        'alpha': ['CryptoGodJohn','inversebrah','cobie','hsaka','CryptoCred'],
        'rwa': ['Ondo_Finance','ChainlinkOfficial'],
        'gems': ['PinkSaleBSC','dx_sale','GemHuntersHQ'],
    }

    for cat, accounts in categories.items():
        for acc in accounts:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO twitter_sources (username, category, added_at) VALUES (?,?,?)",
                    (acc, cat, now))
                added += 1
            except Exception:
                pass
    conn.commit()
    conn.close()
    print(f"  🐦 Twitter: {added} accounts seeded")
    return added


# ── Export to fetcher.py ──────────────────────────────────────────────────────
def export_to_fetcher(min_score=3, max_new_channels=30):
    """
    Export validated sources from DISCOVERY_DB to fetcher.py.
    Only adds sources not already in fetcher.py.
    """
    conn = _get_db()

    # Get validated Telegram channels not yet exported
    tg_rows = conn.execute("""
        SELECT channel, category, members FROM telegram_sources
        WHERE status='VALIDATED'
        AND added_to_fetcher=0
        AND quality_score >= ?
        ORDER BY members DESC
        LIMIT ?
    """, (min_score, max_new_channels)).fetchall()

    # Get validated Reddit subs not yet exported
    reddit_rows = conn.execute("""
        SELECT subreddit, subscribers FROM reddit_sources
        WHERE status='VALIDATED'
        AND added_to_fetcher=0
        AND subscribers >= ?
        ORDER BY subscribers DESC
        LIMIT 30
    """, (MIN_REDDIT_SUBSCRIBERS,)).fetchall()

    if not tg_rows and not reddit_rows:
        print("  No new sources to export")
        conn.close()
        return 0

    # Read fetcher.py
    try:
        fetcher = open('fetcher.py').read()
    except Exception as e:
        print(f"  Cannot read fetcher.py: {e}")
        conn.close()
        return 0

    added = 0

    # Add new Telegram channels
    for channel, cat, members in tg_rows:
        # Check not already in fetcher
        if f"'{channel}'" in fetcher or f'"{channel}"' in fetcher:
            conn.execute("UPDATE telegram_sources SET added_to_fetcher=1 WHERE channel=?", (channel,))
            continue
        # Insert before closing bracket of TELEGRAM_CHANNELS
        fetcher = fetcher.replace(
            "    # Gems & meme\n    'dexscreener'",
            f"    # {cat}\n    '{channel}',\n    # Gems & meme\n    'dexscreener'"
        )
        conn.execute("UPDATE telegram_sources SET added_to_fetcher=1 WHERE channel=?", (channel,))
        added += 1
        print(f"    + Telegram: {channel} ({members:,} members)")

    # Add new Reddit subs
    for subreddit, subscribers in reddit_rows:
        if f"'{subreddit}'" in fetcher or f'"{subreddit}"' in fetcher:
            conn.execute("UPDATE reddit_sources SET added_to_fetcher=1 WHERE subreddit=?", (subreddit,))
            continue
        fetcher = fetcher.replace(
            "    # RWA\n    'RealWorldAssets',",
            f"    '{subreddit}',\n    # RWA\n    'RealWorldAssets',"
        )
        conn.execute("UPDATE reddit_sources SET added_to_fetcher=1 WHERE subreddit=?", (subreddit,))
        added += 1
        print(f"    + Reddit: r/{subreddit} ({subscribers:,} subscribers)")

    if added > 0:
        open('fetcher.py', 'w').write(fetcher)
        print(f"  ✅ Exported {added} new sources to fetcher.py")

    conn.commit()
    conn.close()
    return added


# ── Stats ─────────────────────────────────────────────────────────────────────
def print_stats():
    conn = _get_db()
    tg_total = conn.execute("SELECT COUNT(*) FROM telegram_sources").fetchone()[0]
    tg_valid = conn.execute("SELECT COUNT(*) FROM telegram_sources WHERE status='VALIDATED'").fetchone()[0]
    tg_active = conn.execute("SELECT COUNT(*) FROM telegram_sources WHERE added_to_fetcher=1").fetchone()[0]
    rd_total = conn.execute("SELECT COUNT(*) FROM reddit_sources").fetchone()[0]
    rd_valid = conn.execute("SELECT COUNT(*) FROM reddit_sources WHERE status='VALIDATED'").fetchone()[0]
    tw_total = conn.execute("SELECT COUNT(*) FROM twitter_sources").fetchone()[0]
    conn.close()

    print(f"\n  📊 Source Discovery Stats:")
    print(f"     Telegram: {tg_active} active | {tg_valid} validated | {tg_total} total")
    print(f"     Reddit:   {rd_valid} validated | {rd_total} total")
    print(f"     Twitter:  {tw_total} seeded")


# ── Background thread ─────────────────────────────────────────────────────────
def run_discovery(interval_hours=24):
    """Run weekly discovery as background thread."""
    while True:
        try:
            print(f"\n  🔍 Source discovery running...")
            discover_telegram_channels(max_new=50)
            discover_reddit_subs(max_new=100)
            seed_twitter_accounts()
            export_to_fetcher(min_score=3, max_new_channels=20)
            print_stats()
        except Exception as e:
            print(f"  Source discovery error: {e}")
        time.sleep(interval_hours * 3600)


def start_discovery_thread(interval_hours=24):
    t = threading.Thread(target=run_discovery, args=(interval_hours,),
                         daemon=True, name='source_discovery')
    t.start()
    return t


# ── Standalone ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("\n=== AlphaScope Source Discovery ===")
    print("Discovering Telegram channels...")
    discover_telegram_channels(max_new=100)
    print("\nDiscovering Reddit subreddits...")
    discover_reddit_subs(max_new=100)
    print("\nSeeding Twitter accounts...")
    seed_twitter_accounts()
    print("\nExporting to fetcher.py...")
    export_to_fetcher(min_score=2, max_new_channels=50)
    print_stats()
