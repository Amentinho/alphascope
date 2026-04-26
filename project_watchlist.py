"""
AlphaScope — Project Watchlist v1.0
Tracks promising projects that aren't tradeable yet.
Monitors for trigger events: DEX launch, presale, exchange listing, liquidity spike.

Auto-populated from:
  - token_validator: high GitHub/fundamentals but $0 liquidity
  - pre_launch_gems: real projects with score >= 6
  - manual additions

Trigger events that fire alerts:
  1. Liquidity appears on DexScreener (project goes live)
  2. Exchange listing announced
  3. Presale/IDO announced on ICOdrops/ICOholder
  4. Social velocity spike (launch imminent)
  5. CoinGecko rank appears for first time
"""

import sqlite3
import requests
import json
import time
from datetime import datetime, timezone

def get_db():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_watchlist_table():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS project_watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT UNIQUE,
        symbol TEXT,
        category TEXT,
        why_watching TEXT,
        github_url TEXT,
        github_stars INTEGER DEFAULT 0,
        twitter_handle TEXT,
        twitter_followers INTEGER DEFAULT 0,
        website_url TEXT,
        contract_address TEXT,
        chain TEXT,
        coingecko_id TEXT,
        known_backers TEXT,
        audit_status TEXT DEFAULT 'unknown',
        fundamentals_score INTEGER DEFAULT 0,
        trigger_conditions TEXT,
        status TEXT DEFAULT 'WATCHING',
        alert_fired TEXT,
        alert_notes TEXT,
        added_at TEXT,
        last_checked TEXT,
        went_live_at TEXT,
        notes TEXT)''')
    # Alerts table
    c.execute('''CREATE TABLE IF NOT EXISTS watchlist_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT,
        alert_type TEXT,
        alert_detail TEXT,
        action_recommended TEXT,
        urgency TEXT DEFAULT 'MEDIUM',
        seen INTEGER DEFAULT 0,
        created_at TEXT)''')
    conn.commit()
    conn.close()


def add_to_watchlist(project_name, symbol='', why='', github_url='',
                     github_stars=0, twitter_handle='', website='',
                     contract='', chain='', category='', fundamentals_score=0,
                     trigger_conditions='', notes=''):
    """Add a project to the watchlist."""
    init_watchlist_table()
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    try:
        c.execute('''INSERT OR IGNORE INTO project_watchlist
            (project_name, symbol, category, why_watching, github_url, github_stars,
             twitter_handle, twitter_followers, website_url, contract_address, chain,
             fundamentals_score, trigger_conditions, status, added_at, last_checked, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (project_name, symbol.upper(), category, why, github_url, github_stars,
             twitter_handle, 0, website, contract, chain,
             fundamentals_score, trigger_conditions, 'WATCHING', now, now, notes))
        if c.rowcount:
            print(f"    👁 Added to watchlist: {project_name} (score:{fundamentals_score}) — {why[:50]}")
        conn.commit()
    except Exception as e:
        print(f"    Watchlist add failed: {e}")
    conn.close()


def fire_alert(project_name, alert_type, detail, action, urgency='MEDIUM'):
    """Fire a watchlist alert."""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute('''INSERT INTO watchlist_alerts
        (project_name, alert_type, alert_detail, action_recommended, urgency, created_at)
        VALUES (?,?,?,?,?,?)''',
        (project_name, alert_type, detail, action, urgency, now))
    # Update project status
    c.execute('''UPDATE project_watchlist SET alert_fired=?, alert_notes=?,
                 last_checked=? WHERE project_name=?''',
              (alert_type, detail, now, project_name))
    conn.commit()
    conn.close()
    urgency_emoji = {'HIGH': '🚨', 'MEDIUM': '⚡', 'LOW': '📌'}.get(urgency, '📌')
    print(f"    {urgency_emoji} WATCHLIST ALERT: {project_name} — {alert_type}")
    print(f"       {detail}")
    print(f"       Action: {action}")


def check_dexscreener_live(project_name, symbol, contract=''):
    """Check if project has appeared on DexScreener with real liquidity."""
    try:
        # Try by contract first
        if contract:
            res = requests.get(
                f'https://api.dexscreener.com/latest/dex/tokens/{contract}',
                timeout=8,
            )
            if res.status_code == 200:
                pairs = res.json().get('pairs', [])
                if pairs:
                    liq = float(pairs[0].get('liquidity', {}).get('usd', 0) or 0)
                    if liq >= 10_000:
                        return True, liq, pairs[0].get('url', '')
        # Try by symbol
        res = requests.get(
            f'https://api.dexscreener.com/latest/dex/search?q={symbol or project_name}',
            timeout=8,
        )
        if res.status_code == 200:
            pairs = res.json().get('pairs', [])
            for p in pairs[:3]:
                base = p.get('baseToken', {})
                if (base.get('symbol', '').upper() == (symbol or '').upper() or
                        project_name.lower() in base.get('name', '').lower()):
                    liq = float(p.get('liquidity', {}).get('usd', 0) or 0)
                    if liq >= 10_000:
                        return True, liq, p.get('url', '')
    except Exception:
        pass
    return False, 0, ''


def check_coingecko_listed(symbol, coingecko_id=''):
    """Check if token has appeared on CoinGecko."""
    try:
        if coingecko_id:
            res = requests.get(
                f'https://api.coingecko.com/api/v3/coins/{coingecko_id}',
                params={'localization': 'false', 'tickers': 'false',
                        'market_data': 'true', 'community_data': 'false'},
                timeout=8,
            )
            if res.status_code == 200:
                data = res.json()
                rank = data.get('market_cap_rank')
                price = data.get('market_data', {}).get('current_price', {}).get('usd', 0)
                return bool(price), rank, price
        # Search by symbol
        res = requests.get(
            f'https://api.coingecko.com/api/v3/search?query={symbol}',
            timeout=8,
        )
        if res.status_code == 200:
            coins = res.json().get('coins', [])
            for coin in coins[:3]:
                if coin.get('symbol', '').upper() == (symbol or '').upper():
                    return True, coin.get('market_cap_rank'), 0
    except Exception:
        pass
    return False, None, 0


def check_exchange_listing(project_name, symbol):
    """Check if project appears in recent exchange listings."""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""SELECT exchange, title, fetched_at FROM exchange_listings
                     WHERE (UPPER(coin) LIKE UPPER(?) OR title LIKE ?)
                     AND fetched_at >= datetime('now', '-7 days')
                     ORDER BY fetched_at DESC LIMIT 3""",
                  (f'%{symbol}%', f'%{project_name}%'))
        rows = c.fetchall()
        conn.close()
        if rows:
            return True, rows[0][0], rows[0][1]
    except Exception:
        pass
    return False, '', ''


def check_presale_announced(project_name):
    """Check if presale/IDO has been announced in pre_launch_gems or signals."""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""SELECT sale_type, launchpad, date_info, url
                     FROM pre_launch_gems
                     WHERE LOWER(project_name) LIKE LOWER(?)
                     AND sale_type NOT IN ('', 'unknown')
                     ORDER BY fetched_at DESC LIMIT 1""",
                  (f'%{project_name[:15]}%',))
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            return True, row[0], row[1], row[3]
    except Exception:
        pass
    return False, '', '', ''


def monitor_watchlist():
    """
    Main monitoring function — checks all WATCHING projects for trigger events.
    Called from fetcher.py each cycle.
    """
    init_watchlist_table()
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT project_name, symbol, contract_address, chain,
                        coingecko_id, fundamentals_score, twitter_handle,
                        website_url, github_url
                 FROM project_watchlist
                 WHERE status IN ('WATCHING', 'PRESALE_DETECTED')
                 ORDER BY fundamentals_score DESC""")
    projects = c.fetchall()
    conn.close()

    if not projects:
        return

    print(f"  Watchlist: monitoring {len(projects)} projects...")
    alerts_fired = 0

    for proj in projects:
        name, sym, contract, chain, cg_id, score, twitter, website, github = proj
        time.sleep(0.5)  # rate limit

        # Check 1: DEX listing appeared
        live, liq, dex_url = check_dexscreener_live(name, sym, contract)
        if live:
            fire_alert(
                name, 'DEX_LIVE',
                f'${sym} now live on DEX with ${liq/1000:.0f}k liquidity — {dex_url}',
                f'Check DexScreener immediately. Consider entry if liquidity > $50k and no honeypot.',
                urgency='HIGH'
            )
            conn = get_db()
            conn.execute("UPDATE project_watchlist SET status='LIVE', went_live_at=? WHERE project_name=?",
                        (datetime.now().isoformat(), name))
            conn.commit()
            conn.close()
            alerts_fired += 1
            continue

        # Check 2: CoinGecko listing
        cg_live, rank, price = check_coingecko_listed(sym, cg_id)
        if cg_live and price > 0:
            fire_alert(
                name, 'COINGECKO_LISTED',
                f'${sym} now on CoinGecko — rank #{rank}, price ${price}',
                f'Token is now publicly listed. Research entry point.',
                urgency='MEDIUM'
            )
            alerts_fired += 1

        # Check 3: Exchange listing
        listed, exchange, title = check_exchange_listing(name, sym)
        if listed:
            fire_alert(
                name, 'EXCHANGE_LISTING',
                f'${sym} listing announced on {exchange}: {title[:60]}',
                f'Exchange listing pump likely. Check for presale/IEO opportunity.',
                urgency='HIGH'
            )
            alerts_fired += 1

        # Check 4: Presale announced
        presale, sale_type, launchpad, url = check_presale_announced(name)
        if presale:
            fire_alert(
                name, 'PRESALE_ANNOUNCED',
                f'{sale_type} on {launchpad} — {url}',
                f'Presale detected. Research allocation size and participation steps.',
                urgency='MEDIUM'
            )
            conn = get_db()
            conn.execute("UPDATE project_watchlist SET status='PRESALE_DETECTED' WHERE project_name=?",
                        (name,))
            conn.commit()
            conn.close()
            alerts_fired += 1

        # Update last_checked
        conn = get_db()
        conn.execute("UPDATE project_watchlist SET last_checked=? WHERE project_name=?",
                    (datetime.now().isoformat(), name))
        conn.commit()
        conn.close()

    if alerts_fired:
        print(f"  Watchlist: {alerts_fired} alerts fired")


def auto_add_from_validator(validation_result):
    """
    Called by token_validator after scoring.
    If a token scores high on fundamentals but low on liquidity/trading,
    add to watchlist automatically.
    """
    symbol = validation_result.get('symbol', '')
    name = symbol  # use symbol as name if no project name
    github_stars = validation_result.get('github_stars', 0)
    ai_score = validation_result.get('ai_score', 0)
    total_score = validation_result.get('total_score', 0)
    website_ok = validation_result.get('website_ok', False)
    twitter_followers = validation_result.get('twitter_followers', 0)

    # Conditions to add to watchlist:
    # High GitHub stars but no liquidity
    # OR AI score >= 7 but verdict is WATCH (not tradeable yet)
    # OR Twitter followers > 5000 but no DEX listing
    should_watch = (
        (github_stars >= 100 and total_score < 15) or
        (ai_score >= 7 and validation_result.get('verdict') in ('WATCH', 'UNKNOWN')) or
        (twitter_followers >= 5000 and total_score < 13)
    )

    if not should_watch:
        return

    reasons = []
    if github_stars >= 100:
        reasons.append(f'GitHub {github_stars} stars')
    if ai_score >= 7:
        reasons.append(f'AI score {ai_score}/10')
    if twitter_followers >= 5000:
        reasons.append(f'Twitter {twitter_followers:,} followers')

    triggers = []
    if not validation_result.get('website_ok'):
        triggers.append('website launch')
    triggers.append('DEX liquidity > $20k')
    triggers.append('exchange listing')

    add_to_watchlist(
        project_name=name,
        symbol=symbol,
        why=', '.join(reasons),
        github_url=validation_result.get('github_url', ''),
        github_stars=github_stars,
        twitter_handle='',
        website=validation_result.get('website_url', ''),
        contract=validation_result.get('contract_address', ''),
        chain=validation_result.get('chain', ''),
        fundamentals_score=total_score,
        trigger_conditions=', '.join(triggers),
        notes=f"Auto-added: {', '.join(reasons)}. Verdict was {validation_result.get('verdict')}",
    )


def get_watchlist_summary():
    """Get watchlist for dashboard display."""
    try:
        import pandas as pd
        conn = get_db()
        df = pd.read_sql_query(
            """SELECT project_name, symbol, why_watching, github_stars,
                      twitter_followers, fundamentals_score, status,
                      alert_fired, alert_notes, added_at, went_live_at
               FROM project_watchlist
               ORDER BY
                 CASE status WHEN 'LIVE' THEN 0 WHEN 'PRESALE_DETECTED' THEN 1
                             WHEN 'WATCHING' THEN 2 ELSE 3 END,
                 fundamentals_score DESC""", conn)
        conn.close()
        return df
    except Exception:
        import pandas as pd
        return pd.DataFrame()


def get_unseen_alerts():
    """Get alerts not yet shown to user."""
    try:
        import pandas as pd
        conn = get_db()
        df = pd.read_sql_query(
            """SELECT project_name, alert_type, alert_detail,
                      action_recommended, urgency, created_at
               FROM watchlist_alerts
               WHERE seen=0
               ORDER BY
                 CASE urgency WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
                 created_at DESC""", conn)
        conn.close()
        return df
    except Exception:
        import pandas as pd
        return pd.DataFrame()


if __name__ == '__main__':
    print("AlphaScope — Project Watchlist v1.0")
    print("=" * 50)
    init_watchlist_table()

    # Add ECASH as example (high GitHub stars, not yet tradeable)
    add_to_watchlist(
        project_name='eCash',
        symbol='XEC',
        why='1372 GitHub stars, established project, low DEX liquidity',
        github_url='https://github.com/Bitcoin-ABC/bitcoin-abc',
        github_stars=1372,
        category='cryptocurrency',
        fundamentals_score=14,
        trigger_conditions='DEX liquidity > $50k, exchange listing, presale',
        notes='Formerly Bitcoin Cash ABC. Strong fundamentals but low DeFi presence.',
    )

    print("\nCurrent watchlist:")
    df = get_watchlist_summary()
    if not df.empty:
        for _, r in df.iterrows():
            print(f"  {r['status']:<20} {r['project_name']:<20} "
                  f"score:{r['fundamentals_score']} | {r['why_watching'][:50]}")

    print("\nRunning monitor check...")
    monitor_watchlist()
