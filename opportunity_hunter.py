"""
AlphaScope Opportunity Hunter v1.0
===================================
Monitors and acts on:
  1. AIRDROPS    — alerts with steps + deadline, tracks completion
  2. PRESALES    — detects simple contract presales (auto-buy) and website presales (alert)
  3. LAUNCHPAD   — PinkSale, DXSale new launches above quality threshold

Runs as background thread inside simulation, or standalone via:
    python3 opportunity_hunter.py

Telegram alerts for every opportunity found.
"""

import os
import json
import time
import sqlite3
import threading
import requests
from datetime import datetime, timedelta

# ── Config ────────────────────────────────────────────────────────────────────
def _env(key, default=''):
    val = os.environ.get(key, '')
    if val: return val
    try:
        last = default
        for line in open('.env'):
            line = line.strip()
            if line.startswith(f'{key}='):
                last = line.split('=', 1)[1].strip()
        return last
    except Exception: pass
    return default

TELEGRAM_TOKEN         = _env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT          = _env('TELEGRAM_CHAT_ID')
TELEGRAM_AIRDROP_TOKEN = _env('TELEGRAM_AIRDROP_TOKEN', '')
_AIRDROP_TOKEN         = TELEGRAM_AIRDROP_TOKEN or TELEGRAM_TOKEN
DRY_RUN         = _env('EXECUTOR_DRY_RUN', 'true').lower() != 'false'
MAIN_DB         = 'alphascope.db'
HUNTER_DB       = 'hunter.db'

# Minimum scores to alert
MIN_AIRDROP_SCORE   = 4   # out of 10
MIN_PRESALE_SCORE   = 5   # stricter — real money at stake
MIN_LAUNCHPAD_SCORE = 6


# ── DB ─────────────────────────────────────────────────────────────────────────
def _get_db():
    conn = sqlite3.connect(HUNTER_DB, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''CREATE TABLE IF NOT EXISTS tracked_opportunities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT,          -- AIRDROP / PRESALE / LAUNCHPAD
        name TEXT UNIQUE,
        score INTEGER,
        status TEXT DEFAULT 'NEW',  -- NEW / ALERTED / PARTICIPATING / DONE / SKIP
        deadline TEXT,
        reward_estimate TEXT,
        steps TEXT,         -- JSON list of action steps
        url TEXT,
        contract TEXT,
        chain TEXT,
        auto_buy_eligible INTEGER DEFAULT 0,
        amount_invested REAL DEFAULT 0,
        notes TEXT,
        detected_at TEXT,
        alerted_at TEXT,
        updated_at TEXT)''')
    conn.commit()
    return conn


# ── Telegram ──────────────────────────────────────────────────────────────────
def _tg(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT: return
    def _send():
        try:
            requests.post(
                f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
                json={'chat_id': TELEGRAM_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                timeout=8)
        except Exception: pass
    threading.Thread(target=_send, daemon=True).start()


def _tg_airdrop(msg):
    """Send to dedicated airdrop bot. Falls back to main bot if not configured."""
    token = _AIRDROP_TOKEN
    if not token or not TELEGRAM_CHAT: return
    def _send():
        try:
            requests.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={'chat_id': TELEGRAM_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                timeout=8)
        except Exception: pass
    threading.Thread(target=_send, daemon=True).start()


def _alert_airdrop(name, score, deadline, reward, steps, url):
    steps_text = '\n'.join(f"  {i+1}. {s}" for i, s in enumerate(steps[:5]))
    deadline_str = f"⏰ Deadline: {deadline}" if deadline else ""
    _tg_airdrop(f"🪂 <b>AIRDROP ALERT: {name}</b>\n"
        f"⭐ Score: {score}/10 | 💰 {reward}\n"
        f"{deadline_str}\n"
        f"📋 Steps:\n{steps_text}\n"
        f"🔗 {url}\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}")
    print(f"  🪂 AIRDROP: {name} (score:{score}) — alerted")


def _alert_presale(name, chain, contract, score, price, hardcap, url, auto_buy):
    mode = '🤖 AUTO-BUY eligible' if auto_buy else '👆 Manual — visit website'
    _tg(f"🚀 <b>PRESALE: {name}</b> ({chain.upper()})\n"
        f"⭐ Score: {score}/10\n"
        f"💵 Price: {price} | Cap: {hardcap}\n"
        f"{mode}\n"
        f"🔗 {url}\n"
        f"{'📄 Contract: ' + contract[:20] + '...' if contract else ''}\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}")
    print(f"  🚀 PRESALE: {name} ({chain}) score:{score} auto:{auto_buy}")


def _alert_launchpad(name, chain, platform, score, launch_time, url):
    _tg(f"🏦 <b>LAUNCHPAD: {name}</b>\n"
        f"📍 {platform} | {chain.upper()}\n"
        f"⭐ Score: {score}/10\n"
        f"⏰ Launch: {launch_time}\n"
        f"🔗 {url}\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}")
    print(f"  🏦 LAUNCHPAD: {name} on {platform}")


# ── Airdrop scanner ───────────────────────────────────────────────────────────
def alert_new_airdrop_signals():
    """
    Fast path — reads signals table directly and fires Telegram immediately.
    Called every cycle from fetcher --quick mode.
    No score threshold — all AIRDROP signals alert.
    """
    try:
        main_conn = sqlite3.connect(MAIN_DB, timeout=10)
        rows = main_conn.execute("""
            SELECT DISTINCT title, content, source_detail, engagement, url
            FROM signals
            WHERE signal_type = 'AIRDROP'
            AND fetched_at >= datetime('now', '-35 minutes')
            ORDER BY engagement DESC
            LIMIT 10
        """).fetchall()
        main_conn.close()
    except Exception as e:
        print(f"  airdrop signal check: {e}")
        return

    if not rows:
        return

    conn = _get_db()
    now = datetime.now().isoformat()
    alerted = 0

    for title, content_txt, source, engagement, url in rows:
        name = (title or '')[:80]
        if not name:
            continue
        # Skip if already alerted
        existing = conn.execute(
            "SELECT id FROM tracked_opportunities WHERE name=?", (name,)).fetchone()
        if existing:
            continue

        # Fire Telegram immediately
        snippet = (content_txt or '')[:200]
        link_line = ("\n\U0001f517 " + url) if url else ""
        _tg_airdrop("\U0001fa82 <b>AIRDROP SIGNAL</b>\n"
            + "\U0001f4cb " + name + "\n"
            + "\U0001f4e1 Source: " + (source or "") + "\n"
            + "\U0001f4ac " + snippet + link_line + "\n"
            + "\U0001f550 " + datetime.now().strftime("%H:%M:%S"))
        print(f"  Airdrop alert: {name[:50]} ({source})")

        conn.execute("""
            INSERT OR IGNORE INTO tracked_opportunities
            (type, name, score, url, detected_at, alerted_at, updated_at, status)
            VALUES ('AIRDROP', ?, 5, ?, ?, ?, ?, 'ALERTED')
        """, (name, url or '', now, now, now))
        conn.commit()
        alerted += 1
        time.sleep(0.5)

    conn.close()
    if alerted:
        print(f"  🪂 Fired {alerted} airdrop alerts")


def alert_new_airdrop_signals():
    """Fast path — reads signals table and fires Telegram immediately."""
    try:
        main_conn = sqlite3.connect(MAIN_DB, timeout=10)
        rows = main_conn.execute(
            """SELECT DISTINCT title, content, source_detail, engagement, url
               FROM signals
               WHERE signal_type = 'AIRDROP'
               AND fetched_at >= datetime('now', '-35 minutes')
               ORDER BY engagement DESC LIMIT 10""").fetchall()
        main_conn.close()
    except Exception as e:
        print(f"  airdrop signal check: {e}")
        return

    if not rows:
        return

    conn = _get_db()
    now = datetime.now().isoformat()
    alerted = 0

    for title, content_txt, source, engagement, url in rows:
        name = (title or '')[:80]
        if not name:
            continue
        existing = conn.execute(
            "SELECT id FROM tracked_opportunities WHERE name=?", (name,)).fetchone()
        if existing:
            continue
        snippet = (content_txt or '')[:200]
        link = ("\n🔗 " + url) if url else ""
        msg = (
            "\U0001fa82 <b>AIRDROP SIGNAL</b>\n"
            "\U0001f4cb " + name + "\n"
            "\U0001f4e1 Source: " + (source or "") + "\n"
            "\U0001f4ac " + snippet + link + "\n"
            "\U0001f550 " + datetime.now().strftime("%H:%M:%S")
        )
        _tg_airdrop(msg)
        print(f"  \U0001fa82 Airdrop alert: {name[:50]} ({source})")
        conn.execute(
            """INSERT OR IGNORE INTO tracked_opportunities
               (type, name, score, url, detected_at, alerted_at, updated_at, status)
               VALUES ('AIRDROP', ?, 5, ?, ?, ?, ?, 'ALERTED')""",
            (name, url or '', now, now, now))
        conn.commit()
        alerted += 1
        time.sleep(0.5)

    conn.close()
    if alerted:
        print(f"  \U0001fa82 Fired {alerted} airdrop alerts")


def scan_airdrops():
    """Read airdrop_intel data from DB and alert on high-score new ones."""
    try:
        main_conn = sqlite3.connect(MAIN_DB, timeout=10)
        # Try airdrop_projects first (airdrop_intel.py output), then signals fallback
        table = 'airdrop_projects'
        try:
            main_conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
        except Exception:
            table = None

        if table:
            # Get column names first to handle schema differences
            cols = [c[1] for c in main_conn.execute(f"PRAGMA table_info({table})").fetchall()]
            url_col = 'website_url' if 'website_url' in cols else ('project_url' if 'project_url' in cols else 'NULL')
            rows = main_conn.execute(f"""
                SELECT project_name, legitimacy_score, deadline, reward_estimate,
                       qualification_steps, {url_col}, status
                FROM {table}
                WHERE legitimacy_score >= ?
                AND (status IS NULL OR status NOT IN ('DONE','SKIP','EXPIRED'))
                ORDER BY legitimacy_score DESC
                LIMIT 20
            """, (MIN_AIRDROP_SCORE,)).fetchall()
        else:
            # Fallback: read AIRDROP signals directly
            rows_raw = main_conn.execute("""
                SELECT DISTINCT title, 7, NULL, 'Unknown reward',
                       content, url, 'NEW'
                FROM signals
                WHERE signal_type = 'AIRDROP'
                AND fetched_at >= datetime('now', '-48 hours')
                ORDER BY engagement DESC
                LIMIT 15
            """).fetchall()
            rows = [(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows_raw]
        main_conn.close()
    except Exception as e:
        print(f"  airdrop scan error: {e}")
        return

    conn = _get_db()
    now = datetime.now().isoformat()
    alerted = 0

    for name, score, deadline, reward, steps_raw, url, status in rows:
        # Skip if already alerted
        existing = conn.execute(
            "SELECT status FROM tracked_opportunities WHERE name=? AND type='AIRDROP'",
            (name,)).fetchone()
        if existing:
            continue

        # Parse steps
        try:
            steps = json.loads(steps_raw) if steps_raw else []
        except Exception:
            steps = [steps_raw] if steps_raw else ['Visit project website']

        if not steps:
            steps = ['Connect wallet', 'Complete tasks', 'Claim airdrop']

        # Check deadline not passed
        if deadline:
            try:
                dl = datetime.fromisoformat(deadline.replace('Z', ''))
                if dl < datetime.now():
                    continue
            except Exception:
                pass

        # Store and alert
        conn.execute("""
            INSERT OR IGNORE INTO tracked_opportunities
            (type, name, score, deadline, reward_estimate, steps, url,
             detected_at, alerted_at, updated_at, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,'ALERTED')
        """, ('AIRDROP', name, score, deadline, reward,
              json.dumps(steps), url or '', now, now, now))
        conn.commit()
        _alert_airdrop(name, score, deadline or 'TBD', reward or 'TBD', steps, url or '')
        alerted += 1
        time.sleep(1)  # don't spam Telegram

    # Also scan signals table for recent AIRDROP signal types
    try:
        main_conn2 = sqlite3.connect(MAIN_DB, timeout=10)
        sig_rows = main_conn2.execute("""
            SELECT DISTINCT title, 7, NULL, 'Check details',
                   content, url, 'NEW'
            FROM signals
            WHERE signal_type = 'AIRDROP'
            AND fetched_at >= datetime('now', '-24 hours')
            ORDER BY engagement DESC LIMIT 10
        """).fetchall()
        main_conn2.close()
        for row in sig_rows:
            name = row[0][:60] if row[0] else 'Unknown'
            if not name: continue
            existing = conn.execute(
                "SELECT id FROM tracked_opportunities WHERE name=? AND type='AIRDROP'",
                (name,)).fetchone()
            if existing: continue
            steps = ['Connect wallet', 'Complete tasks on project website', 'Claim airdrop']
            conn.execute("""
                INSERT OR IGNORE INTO tracked_opportunities
                (type,name,score,steps,url,detected_at,alerted_at,updated_at,status)
                VALUES ('AIRDROP',?,7,?,?,?,?,?,'ALERTED')
            """, (name, json.dumps(steps), row[5] or '', now, now, now))
            conn.commit()
            _alert_airdrop(name, 7, 'TBD', 'TBD', steps, row[5] or '')
            alerted += 1
            time.sleep(1)
    except Exception as e:
        print(f"  airdrop signals scan: {e}")

    conn.close()
    if alerted:
        print(f"  🪂 Alerted {alerted} new airdrops")


# ── Presale scanner ───────────────────────────────────────────────────────────
def _check_simple_presale(contract, chain) -> dict:
    """
    Check if a contract address looks like a simple presale:
    - Has a payable receive() or buy() function
    - Is verified on-chain
    Returns {'is_simple': bool, 'price': str, 'hardcap': str}
    """
    # For now, check via DexScreener if token already has a pair forming
    try:
        r = requests.get(
            f'https://api.dexscreener.com/latest/dex/tokens/{contract}',
            timeout=8)
        if r.status_code == 200:
            pairs = r.json().get('pairs', [])
            # If pairs exist, presale might already be over
            if pairs:
                return {'is_simple': False, 'reason': 'Already on DEX'}
    except Exception:
        pass
    # Conservative: mark all presales as manual for now
    # Phase 3 can add actual contract ABI parsing
    return {'is_simple': False, 'reason': 'Requires manual verification'}


def scan_presales():
    """Scan project_watchlist and pre-launch gems for presale opportunities."""
    try:
        main_conn = sqlite3.connect(MAIN_DB, timeout=10)
        # Check project_watchlist for presale flags (table may not exist)
        rows = []
        try:
            try:
                rows = main_conn.execute("""
                    SELECT symbol, name, '?' as chain,
                           current_price, presale_found,
                           alert_detail, updated_at
                    FROM project_watchlist
                    WHERE presale_found = 1
                    AND updated_at >= datetime('now', '-24 hours')
                    ORDER BY updated_at DESC LIMIT 10
                """).fetchall()
            except Exception:
                rows = []
        except Exception:
            pass

        # Also check pre-launch gems
        pre_rows = main_conn.execute("""
            SELECT project_name, chain, score, status, website_url,
                   contract_address, presale_link, description
            FROM pre_launch_gems
            WHERE score >= ?
            AND status IN ('presale', 'Presale', 'PRESALE', 'upcoming')
            AND detected_at >= datetime('now', '-48 hours')
            ORDER BY score DESC
            LIMIT 10
        """, (MIN_PRESALE_SCORE,)).fetchall() if _table_exists(main_conn, 'pre_launch_gems') else []

        main_conn.close()
    except Exception as e:
        print(f"  presale scan error: {e}")
        return

    conn = _get_db()
    now = datetime.now().isoformat()

    for row in rows:
        sym, name, chain, price, _, detail, _ = row
        existing = conn.execute(
            "SELECT id FROM tracked_opportunities WHERE name=? AND type='PRESALE'",
            (name or sym,)).fetchone()
        if existing: continue
        conn.execute("""
            INSERT OR IGNORE INTO tracked_opportunities
            (type, name, chain, score, url, detected_at, alerted_at, updated_at,
             notes, status, auto_buy_eligible)
            VALUES ('PRESALE',?,?,5,?,?,?,?,?,'ALERTED',0)
        """, (name or sym, chain or '?', '', now, now, now, detail or ''))
        conn.commit()
        _alert_presale(name or sym, chain or '?', '', 5, 'TBD', 'TBD', '', False)
        time.sleep(1)

    for row in pre_rows:
        proj_name, chain, score, status, url, contract, presale_link, desc = row
        existing = conn.execute(
            "SELECT id FROM tracked_opportunities WHERE name=? AND type='PRESALE'",
            (proj_name,)).fetchone()
        if existing: continue

        # Check if this could be a simple auto-buy presale
        auto_check = _check_simple_presale(contract or '', chain or 'ethereum')
        auto_buy = auto_check.get('is_simple', False)

        conn.execute("""
            INSERT OR IGNORE INTO tracked_opportunities
            (type, name, chain, score, url, contract, detected_at, alerted_at,
             updated_at, notes, status, auto_buy_eligible)
            VALUES ('PRESALE',?,?,?,?,?,?,?,?,?,'ALERTED',?)
        """, (proj_name, chain or '?', score, presale_link or url or '',
              contract or '', now, now, now, desc or '', 1 if auto_buy else 0))
        conn.commit()
        _alert_presale(proj_name, chain or '?', contract or '', score,
                       auto_check.get('price', 'TBD'), auto_check.get('hardcap', 'TBD'),
                       presale_link or url or '', auto_buy)
        time.sleep(1)

    conn.close()


# ── PinkSale / launchpad scanner ──────────────────────────────────────────────
def scan_launchpads():
    """Check PinkSale API for new high-quality launches."""
    try:
        # PinkSale public API
        r = requests.get(
            'https://api.pinksale.finance/api/launchpads',
            params={'status': 'upcoming', 'limit': 20, 'sort': 'created_desc'},
            timeout=10)
        if r.status_code != 200:
            return
        launches = r.json().get('data', {}).get('launchpads', [])
    except Exception as e:
        print(f"  PinkSale scan error: {e}")
        launches = []

    conn = _get_db()
    now = datetime.now().isoformat()

    for launch in launches:
        try:
            name       = launch.get('token', {}).get('name', '')
            chain      = launch.get('chain', 'bsc').lower()
            softcap    = float(launch.get('softcap', 0) or 0)
            hardcap    = float(launch.get('hardcap', 0) or 0)
            start_time = launch.get('start_time', '')
            url        = f"https://www.pinksale.finance/launchpad/{launch.get('id','')}"
            liquidity_pct = float(launch.get('liquidity_percent', 0) or 0)
            lock_days  = int(launch.get('lock_time_days', 0) or 0)

            # Score the launch
            score = 0
            if liquidity_pct >= 60: score += 3
            elif liquidity_pct >= 40: score += 1
            if lock_days >= 180: score += 3
            elif lock_days >= 30: score += 1
            if softcap >= 10: score += 1
            if hardcap <= 500: score += 2  # smaller cap = more upside potential
            if chain in ('solana', 'base', 'ethereum'): score += 1

            if score < MIN_LAUNCHPAD_SCORE:
                continue

            existing = conn.execute(
                "SELECT id FROM tracked_opportunities WHERE name=? AND type='LAUNCHPAD'",
                (name,)).fetchone()
            if existing: continue

            conn.execute("""
                INSERT OR IGNORE INTO tracked_opportunities
                (type, name, chain, score, url, detected_at, alerted_at, updated_at, status)
                VALUES ('LAUNCHPAD',?,?,?,?,?,?,?,'ALERTED')
            """, (name, chain, score, url, now, now, now))
            conn.commit()
            _alert_launchpad(name, chain, 'PinkSale', score, start_time, url)
            time.sleep(1)
        except Exception:
            continue

    conn.close()


# ── Opportunity summary ───────────────────────────────────────────────────────
def send_daily_summary():
    """Send a daily summary of all tracked opportunities."""
    try:
        conn = _get_db()
        rows = conn.execute("""
            SELECT type, name, score, status, deadline, reward_estimate
            FROM tracked_opportunities
            WHERE status IN ('ALERTED', 'NEW', 'PARTICIPATING')
            ORDER BY score DESC
            LIMIT 15
        """).fetchall()
        conn.close()

        if not rows:
            return

        lines = ["📊 <b>Daily Opportunity Summary</b>\n"]
        for type_, name, score, status, deadline, reward in rows:
            emoji = {'AIRDROP':'🪂','PRESALE':'🚀','LAUNCHPAD':'🏦'}.get(type_,'📌')
            dl = f" | ⏰{deadline[:10]}" if deadline else ""
            lines.append(f"{emoji} {name} (⭐{score}){dl}")
            if reward: lines.append(f"   💰 {reward}")

        _tg_airdrop('\n'.join(lines))
    except Exception as e:
        print(f"  summary error: {e}")


# ── Helper ────────────────────────────────────────────────────────────────────
def _table_exists(conn, table_name) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)).fetchone()
    return bool(row)


# ── Main loop (background thread) ─────────────────────────────────────────────
def run_opportunity_hunter(interval_minutes=60):
    """
    Background thread — scans for opportunities every hour.
    Also sends a daily summary at 09:00.
    """
    last_summary = None
    print(f"  🔍 Opportunity hunter started (every {interval_minutes}min)")

    while True:
        try:
            print(f"  🔍 Scanning opportunities...")
            scan_airdrops()
            scan_presales()
            scan_launchpads()

            # Daily summary at 09:00
            now = datetime.now()
            if now.hour == 9 and (last_summary is None or last_summary.date() < now.date()):
                send_daily_summary()
                last_summary = now

        except Exception as e:
            print(f"  opportunity hunter error: {e}")

        time.sleep(interval_minutes * 60)


def start_hunter_thread(interval_minutes=60):
    """Start the opportunity hunter as a daemon thread."""
    t = threading.Thread(
        target=run_opportunity_hunter,
        args=(interval_minutes,),
        daemon=True,
        name='opportunity_hunter')
    t.start()
    return t


# ── Standalone run ────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("\n=== AlphaScope Opportunity Hunter ===")
    print(f"  Scanning airdrops (min score: {MIN_AIRDROP_SCORE})")
    print(f"  Scanning presales (min score: {MIN_PRESALE_SCORE})")
    print(f"  Scanning launchpads (min score: {MIN_LAUNCHPAD_SCORE})")
    print(f"  Telegram: {'✅' if TELEGRAM_TOKEN else '⚠️ not configured'}")
    print("=====================================\n")
    scan_airdrops()
    scan_presales()
    scan_launchpads()
    send_daily_summary()
    print("\nDone. Run continuously with start_hunter_thread() from simulation.")
