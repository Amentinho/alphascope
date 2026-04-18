"""
AlphaScope — Pre-Launch Gem Scanner
Detects projects BEFORE they hit exchanges.
Sources: ICOdrops, Foundico, CryptoTotem, Reddit, Telegram, X/Twitter
No AI costs — just detection and ranking. You decide what to deep-dive.

Flow:
1. Scan ICO listing sites for upcoming presales/IDOs
2. Scan Reddit/Telegram/X for project names + presale keywords
3. Cross-reference: project mentioned on ICO site AND social media = high signal
4. Rank by: social buzz + launchpad quality + category
5. You mark projects for AI deep-dive (or skip)
"""

import requests
import sqlite3
import re
import time
from datetime import datetime

# ============================================================
# PRE-LAUNCH KEYWORDS — signals a project is about to launch
# ============================================================
LAUNCH_KEYWORDS = [
    'presale', 'pre-sale', 'token sale', 'public sale', 'private sale',
    'ido', 'ico', 'ieo', 'tge', 'token generation',
    'launching soon', 'launch date', 'mainnet launch',
    'whitelist open', 'whitelist live', 'wl open',
    'mint live', 'mint date', 'free mint',
    'seed round', 'strategic round', 'funding round',
    'binance launchpad', 'binance launchpool', 'kucoin spotlight',
    'bybit launchpad', 'gate startup', 'seedify', 'dao maker',
    'fjord foundry', 'coinlist', 'polkastarter',
    'testnet live', 'incentivized testnet',
    'early access', 'beta launch', 'waitlist',
]

# Quality launchpads (projects on these are more legit)
QUALITY_LAUNCHPADS = {
    'binance launchpad': 10, 'binance launchpool': 10, 'binance hodler': 9,
    'coinlist': 9, 'bybit launchpad': 8, 'kucoin spotlight': 8,
    'okx jumpstart': 8, 'gate startup': 7, 'seedify': 7,
    'dao maker': 7, 'fjord foundry': 7, 'polkastarter': 6,
    'trustpad': 5, 'gamestarter': 5,
}


def init_gem_table():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS pre_launch_gems (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT,
        category TEXT,
        sale_type TEXT,
        launchpad TEXT,
        date_info TEXT,
        raised TEXT,
        social_mentions INTEGER,
        social_sources TEXT,
        launchpad_score INTEGER,
        total_score INTEGER,
        status TEXT,
        user_action TEXT,
        source TEXT,
        url TEXT,
        fetched_at TEXT,
        UNIQUE(project_name))''')
    conn.commit()
    conn.close()


def scrape_icodrops():
    """Scrape ICOdrops for upcoming and active presales."""
    print("    ICOdrops...")
    projects = []
    
    try:
        res = requests.get('https://icodrops.com/', 
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}, 
            timeout=15)
        if res.status_code != 200:
            print(f"      HTTP {res.status_code}")
            return projects
        
        html = res.text
        
        # Extract project cards — look for project names and sale types
        # ICOdrops uses specific HTML patterns for each project
        
        # Pattern: project name + category + sale type + date
        # They use structured divs with project info
        
        # Simple regex to find project entries
        entries = re.findall(
            r'<a[^>]*href="(/[^"]+)"[^>]*>.*?<div[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</div>.*?'
            r'(?:<div[^>]*class="[^"]*categ[^"]*"[^>]*>(.*?)</div>)?',
            html, re.DOTALL | re.IGNORECASE
        )
        
        # Also try simpler pattern for project names
        names = re.findall(r'<h3[^>]*>(.*?)</h3>', html)
        
        # Extract any project-like entries from the page
        # Look for patterns: "ProjectName TICKER · Category · Sale Type Date"
        lines = re.findall(r'([A-Z][a-zA-Z\s]{2,30})\s+([A-Z]{2,6})\s*·\s*([\w\s-]+)\s*·\s*([\w\s]+(?:Sale|Airdrop|IDO|ICO|IEO)[^<]*)', html)
        
        for name, ticker, category, sale_info in lines:
            projects.append({
                'name': name.strip(),
                'ticker': ticker.strip(),
                'category': category.strip(),
                'sale_type': sale_info.strip()[:50],
                'source': 'ICOdrops',
                'url': f'https://icodrops.com/',
            })
        
        # Fallback: extract any text that looks like a presale project
        # Search for common ICOdrops patterns
        presale_matches = re.findall(
            r'(?:Presale|Public Sale|IDO|Airdrop|Wallet Sale|Launchpad)[^<]{0,100}',
            html
        )
        
        # Also extract from structured data if available
        script_data = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        
        if not projects:
            # Try getting project names from title patterns
            title_matches = re.findall(r'title="([^"]{3,40})"[^>]*>.*?(?:ICO|IDO|Presale|Sale|Airdrop)', html, re.DOTALL)
            for name in title_matches[:20]:
                name_clean = re.sub(r'<[^>]+>', '', name).strip()
                if len(name_clean) > 2 and len(name_clean) < 30:
                    projects.append({
                        'name': name_clean,
                        'ticker': '',
                        'category': '',
                        'sale_type': 'Upcoming',
                        'source': 'ICOdrops',
                        'url': 'https://icodrops.com/',
                    })
        
        print(f"      Found {len(projects)} projects")
    except Exception as e:
        print(f"      Failed: {e}")
    
    return projects


def scrape_cryptototem():
    """Scrape CryptoTotem for upcoming ICOs/IDOs."""
    print("    CryptoTotem...")
    projects = []
    
    try:
        res = requests.get('https://cryptototem.com/ico-list/upcoming-ico/',
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'},
            timeout=15)
        if res.status_code != 200:
            print(f"      HTTP {res.status_code}")
            return projects
        
        # Extract project names and details
        # CryptoTotem lists projects with names, categories, dates
        entries = re.findall(r'<h\d[^>]*>\s*<a[^>]*>([^<]{3,40})</a>\s*</h\d>', res.text)
        
        for name in entries[:20]:
            name = name.strip()
            if len(name) > 2:
                projects.append({
                    'name': name,
                    'ticker': '',
                    'category': '',
                    'sale_type': 'Upcoming ICO/IDO',
                    'source': 'CryptoTotem',
                    'url': 'https://cryptototem.com/ico-list/upcoming-ico/',
                })
        
        print(f"      Found {len(projects)} projects")
    except Exception as e:
        print(f"      Failed: {e}")
    
    return projects


def scrape_foundico():
    """Scrape Foundico RSS for upcoming ICOs."""
    print("    Foundico...")
    projects = []
    
    try:
        res = requests.get('https://foundico.com/blog/feed',
            headers={'User-Agent': 'AlphaScope/2.2'},
            timeout=10)
        if res.status_code != 200:
            return projects
        
        items = re.findall(r'<item[^>]*>(.*?)</item>', res.text, re.DOTALL)
        for item in items[:15]:
            title = re.search(r'<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item, re.DOTALL)
            link = re.search(r'<link[^>]*>(.*?)</link>', item, re.DOTALL)
            if title:
                t = re.sub(r'<[^>]+>', '', title.group(1)).strip()
                if any(kw in t.lower() for kw in ['ico', 'ido', 'presale', 'token', 'launch']):
                    projects.append({
                        'name': t[:40],
                        'ticker': '',
                        'category': '',
                        'sale_type': 'ICO/IDO',
                        'source': 'Foundico',
                        'url': link.group(1).strip() if link else '',
                    })
        
        print(f"      Found {len(projects)} projects")
    except Exception as e:
        print(f"      Failed: {e}")
    
    return projects


def scan_social_for_launches():
    """Scan existing signals (Reddit, Telegram, X) for pre-launch project mentions."""
    print("    Scanning social signals for launches...")
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    
    # Get all recent signals
    c.execute("""SELECT title, content, source, source_detail 
                 FROM signals WHERE fetched_at >= datetime('now', '-6 hours')""")
    signals = c.fetchall()
    conn.close()
    
    project_mentions = {}
    
    for title, content, source, detail in signals:
        text = (title + ' ' + (content or '')).lower()
        
        # Check if it mentions any launch keywords
        has_launch_kw = False
        matched_kw = ''
        for kw in LAUNCH_KEYWORDS:
            if kw in text:
                has_launch_kw = True
                matched_kw = kw
                break
        
        if not has_launch_kw:
            continue
        
        # Try to extract project name — look for capitalized words near the keyword
        # Pattern: "ProjectName presale is live" or "Join the ProjectName IDO"
        original_text = title + ' ' + (content or '')
        
        # Find capitalized multi-word names (likely project names)
        name_candidates = re.findall(r'(?:^|\s)([A-Z][a-z]+(?:\s[A-Z][a-z]+){0,2})(?:\s|$|\.|\,)', original_text)
        
        # Also find ALL-CAPS words that could be project names/tickers
        ticker_candidates = re.findall(r'\b([A-Z]{3,10})\b', original_text)
        
        # Filter out common words
        skip_words = {'The', 'This', 'That', 'What', 'When', 'Where', 'How', 'Why',
                      'Just', 'Join', 'Check', 'New', 'Big', 'Top', 'Best', 'Get',
                      'Bitcoin', 'Ethereum', 'Crypto', 'Token', 'Coin', 'BTC', 'ETH',
                      'SOL', 'USD', 'USDT', 'FREE', 'NOW', 'LIVE', 'NEW', 'HOT'}
        
        names = [n for n in name_candidates if n not in skip_words and len(n) > 2]
        tickers = [t for t in ticker_candidates if t not in skip_words and len(t) >= 3 and len(t) <= 8]
        
        for name in (names[:2] + tickers[:2]):
            name_key = name.strip()
            if len(name_key) < 3:
                continue
            
            if name_key not in project_mentions:
                project_mentions[name_key] = {
                    'count': 0,
                    'sources': set(),
                    'keywords': set(),
                    'sample_text': title[:100],
                }
            project_mentions[name_key]['count'] += 1
            project_mentions[name_key]['sources'].add(source)
            project_mentions[name_key]['keywords'].add(matched_kw)
    
    # Filter to only projects mentioned 2+ times or from 2+ sources
    results = []
    for name, data in sorted(project_mentions.items(), key=lambda x: -x[1]['count']):
        if data['count'] >= 2 or len(data['sources']) >= 2:
            results.append({
                'name': name,
                'ticker': '',
                'category': '',
                'sale_type': ', '.join(list(data['keywords'])[:3]),
                'source': 'Social',
                'social_mentions': data['count'],
                'social_sources': ','.join(data['sources']),
                'sample': data['sample_text'],
            })
    
    print(f"      Found {len(results)} potential launches in social signals")
    return results


def scan_exchange_launchpads():
    """Check exchange listings for launchpad/launchpool entries."""
    print("    Scanning exchange launchpads...")
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    
    c.execute("""SELECT exchange, coin, title, url FROM exchange_listings
                 WHERE fetched_at >= datetime('now', '-7 days')""")
    listings = c.fetchall()
    conn.close()
    
    projects = []
    for exchange, coin, title, url in listings:
        title_lower = title.lower()
        # Check for launchpad-related keywords
        for pad_name, score in QUALITY_LAUNCHPADS.items():
            if pad_name in title_lower or any(kw in title_lower for kw in 
                ['launchpad', 'launchpool', 'spotlight', 'startup', 'kickstarter', 'hodler airdrop']):
                projects.append({
                    'name': coin or title[:30],
                    'ticker': coin,
                    'category': '',
                    'sale_type': f'{exchange} Launchpad',
                    'source': f'Exchange:{exchange}',
                    'launchpad_score': score,
                    'url': url,
                })
                break
    
    print(f"      Found {len(projects)} launchpad projects")
    return projects


def score_project(project, social_data=None):
    """Score a pre-launch project based on available signals."""
    score = 0
    
    # Launchpad quality (0-10)
    lp_score = project.get('launchpad_score', 0)
    if not lp_score:
        sale_lower = project.get('sale_type', '').lower()
        for pad, s in QUALITY_LAUNCHPADS.items():
            if pad in sale_lower:
                lp_score = s
                break
    score += lp_score
    
    # Social buzz (0-5)
    mentions = project.get('social_mentions', 0)
    if mentions >= 10: score += 5
    elif mentions >= 5: score += 3
    elif mentions >= 2: score += 1
    
    # Multi-source (0-3)
    sources = project.get('social_sources', '')
    src_count = len(sources.split(',')) if sources else 0
    if src_count >= 3: score += 3
    elif src_count >= 2: score += 2
    
    # Listed on ICO tracking site (0-2)
    if project.get('source') in ('ICOdrops', 'CryptoTotem', 'Foundico'):
        score += 2
    
    return min(score, 20)  # Cap at 20


def fetch_pre_launch_gems():
    """Main function: scan all sources for pre-launch projects."""
    init_gem_table()
    print("  Scanning for pre-launch gems...")
    now = datetime.now().isoformat()
    
    # Gather from all sources
    all_projects = []
    
    # ICO listing sites
    all_projects.extend(scrape_icodrops())
    time.sleep(2)
    all_projects.extend(scrape_cryptototem())
    time.sleep(2)
    all_projects.extend(scrape_foundico())
    
    # Social media signals
    social_projects = scan_social_for_launches()
    all_projects.extend(social_projects)
    
    # Exchange launchpads
    all_projects.extend(scan_exchange_launchpads())
    
    if not all_projects:
        print("  No pre-launch gems found this cycle")
        return
    
    # Deduplicate by name (case-insensitive)
    seen = {}
    for p in all_projects:
        key = p['name'].lower().strip()
        if key in seen:
            # Merge data
            existing = seen[key]
            existing['social_mentions'] = max(
                existing.get('social_mentions', 0), p.get('social_mentions', 0))
            if p.get('social_sources'):
                old_src = existing.get('social_sources', '')
                existing['social_sources'] = f"{old_src},{p['social_sources']}" if old_src else p['social_sources']
            if p.get('launchpad_score', 0) > existing.get('launchpad_score', 0):
                existing['launchpad_score'] = p['launchpad_score']
            if p.get('source') not in existing.get('all_sources', ''):
                existing['all_sources'] = f"{existing.get('all_sources', '')},{p['source']}"
        else:
            p['all_sources'] = p.get('source', '')
            seen[key] = p
    
    # Score and store
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    stored = 0
    
    for key, project in sorted(seen.items(), key=lambda x: -score_project(x[1])):
        total_score = score_project(project)
        
        try:
            c.execute('''INSERT OR REPLACE INTO pre_launch_gems 
                         (project_name, category, sale_type, launchpad, date_info, raised,
                          social_mentions, social_sources, launchpad_score, total_score,
                          status, source, url, fetched_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (project['name'], project.get('category', ''),
                 project.get('sale_type', ''), '',
                 '', '',
                 project.get('social_mentions', 0),
                 project.get('social_sources', ''),
                 project.get('launchpad_score', 0),
                 total_score,
                 'DETECTED',
                 project.get('all_sources', project.get('source', '')),
                 project.get('url', ''),
                 now))
            stored += 1
        except:
            pass
    
    conn.commit()
    conn.close()
    
    # Print top gems
    top = sorted(seen.values(), key=lambda x: -score_project(x))[:8]
    for p in top:
        score = score_project(p)
        if score >= 2:
            emoji = '💎' if score >= 8 else '🔍' if score >= 4 else '📌'
            print(f"    {emoji} {p['name']} — score:{score} | {p.get('sale_type', '?')} | src:{p.get('all_sources', '?')[:30]}")
    
    print(f"  Pre-launch gems: {stored} projects detected")


def load_pre_launch_gems():
    """Load pre-launch gems for dashboard."""
    conn = sqlite3.connect('alphascope.db', timeout=30)
    import pandas as pd
    df = pd.read_sql_query(
        """SELECT project_name, category, sale_type, social_mentions, 
                  social_sources, launchpad_score, total_score, status, 
                  user_action, source, url
           FROM pre_launch_gems 
           WHERE status IN ('DETECTED', 'MARKED_FOR_REVIEW', 'APPROVED')
           ORDER BY total_score DESC""", conn)
    conn.close()
    return df


def mark_for_review(project_name):
    """Mark a project for AI deep-dive analysis."""
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute("UPDATE pre_launch_gems SET status='MARKED_FOR_REVIEW', user_action='REVIEW' WHERE project_name=?",
              (project_name,))
    conn.commit()
    conn.close()
    print(f"  Marked '{project_name}' for AI review")


def approve_gem(project_name, notes=''):
    """Approve a gem — you're interested in this project."""
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute("UPDATE pre_launch_gems SET status='APPROVED', user_action=? WHERE project_name=?",
              (notes, project_name))
    conn.commit()
    conn.close()


def dismiss_gem(project_name):
    """Dismiss a project — not interested."""
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute("UPDATE pre_launch_gems SET status='DISMISSED' WHERE project_name=?",
              (project_name,))
    conn.commit()
    conn.close()


if __name__ == '__main__':
    print("AlphaScope — Pre-Launch Gem Scanner")
    print("=" * 50)
    fetch_pre_launch_gems()
    print()
    print("Top gems detected:")
    for _, r in load_pre_launch_gems().head(10).iterrows():
        emoji = '💎' if r['total_score'] >= 8 else '🔍' if r['total_score'] >= 4 else '📌'
        print(f"  {emoji} {r['project_name']} (score: {r['total_score']}) | {r['sale_type']} | {r['source'][:30]}")
