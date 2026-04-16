"""
AlphaScope — Airdrop Intelligence (Option C Hybrid)
AI extracts qualification steps, classifies by effort/cost, scores legitimacy.
User reviews and approves in dashboard.

Effort levels:
  FREE_EASY    - Social tasks, testnet, connect wallet (5 min)
  LOW_COST     - Gas fees only $5-20, bridge small amount (30 min)
  MEDIUM_COST  - Need $100-500 in protocol, staking (1 hour+)
  HIGH_COST    - Need $1000+, exchange tokens, nodes (ongoing)
  INVITE_ONLY  - KOL allocation, whitelist, lottery

Priority: FREE_EASY > LOW_COST > rest (we want actionable alpha)
"""

import requests
import sqlite3
import json
from datetime import datetime


def init_airdrop_tables():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS airdrop_projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT,
        category TEXT,
        website TEXT,
        twitter TEXT,
        qualification_steps TEXT,
        effort_level TEXT,
        cost_estimate TEXT,
        time_required TEXT,
        reward_estimate TEXT,
        deadline TEXT,
        legitimacy_score INTEGER,
        legitimacy_reasons TEXT,
        status TEXT,
        user_notes TEXT,
        progress TEXT,
        sources TEXT,
        created_at TEXT,
        updated_at TEXT,
        UNIQUE(project_name))''')
    conn.commit()
    conn.close()


def get_openai_key():
    try:
        with open('.env') as f:
            for line in f:
                if line.startswith('OPENAI_API_KEY='):
                    return line.strip().split('=', 1)[1]
    except:
        pass
    return None


def analyze_single_airdrop(mention_text, source_info):
    """Use GPT-4o-mini to extract structured airdrop data from a mention."""
    api_key = get_openai_key()
    if not api_key:
        return None

    prompt = f"""Analyze this crypto airdrop mention. Return ONLY a valid JSON object.

{{
  "project_name": "Name of the project (or 'Unknown' if can't determine)",
  "category": "DeFi/L2/Gaming/AI/Infrastructure/Memecoin/Exchange/Other",
  "website": "project website URL if mentioned (or '')",
  "twitter": "project Twitter/X handle if mentioned (or '')",
  "qualification_steps": "Specific numbered steps to qualify. Be concrete:\n1. Go to [website]\n2. Connect wallet\n3. [specific action]\nIf unclear, write 'Research needed - check project website'",
  "effort_level": "FREE_EASY / LOW_COST / MEDIUM_COST / HIGH_COST / INVITE_ONLY",
  "cost_estimate": "$0 (free) / $5-20 (gas) / $100-500 / $1000+ / Unknown",
  "time_required": "5 min / 30 min / 1 hour / ongoing daily / Unknown",
  "reward_estimate": "Estimated $ value based on similar past airdrops, or 'Unknown'",
  "deadline": "YYYY-MM-DD if mentioned, or 'Unknown'",
  "legitimacy_score": 1-10,
  "legitimacy_reasons": "Explain score. Mention: team doxxed?, VC backed?, audit?, red flags?",
  "action_recommendation": "PARTICIPATE_NOW / RESEARCH_FIRST / WAIT / SKIP and brief reason"
}}

Scoring guide:
- 1-3: Likely scam (unknown team, requires sending tokens, too-good-to-be-true rewards, fake project)
- 4-5: Unverified (new project, no audit, unclear team, but no obvious red flags)
- 6-7: Promising (real product, some track record, community exists)
- 8-9: Legit (established project, VC backed, audited, team doxxed)
- 10: Confirmed (official announcement from verified project)

Effort level guide:
- FREE_EASY: Just social tasks, testnet usage, connect wallet, Galxe/Zealy quests
- LOW_COST: Only gas fees needed ($5-20), bridge a small amount, do a swap
- MEDIUM_COST: Need $100-500 in the protocol, staking required, LP provision
- HIGH_COST: Need $1000+, hold exchange tokens (BNB/KCS/GT), run a node
- INVITE_ONLY: Whitelist required, KOL allocation, lottery system

Red flags to check:
- Asks you to send tokens to an address = SCAM
- "Send 0.1 ETH to receive 10 ETH" = SCAM
- No website, no GitHub, no Twitter = suspicious
- "Guaranteed" returns = suspicious
- Requires connecting wallet to unknown site = risky

Source: {source_info}
Mention: {mention_text[:600]}

Return ONLY valid JSON, no markdown, no explanation."""

    try:
        res = requests.post('https://api.openai.com/v1/chat/completions',
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
            json={
                'model': 'gpt-4o-mini',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 600,
                'temperature': 0.2
            },
            timeout=30)

        content = res.json()['choices'][0]['message']['content'].strip()
        if content.startswith('```'):
            content = content.split('\n', 1)[1].rsplit('```', 1)[0]
        return json.loads(content)
    except Exception as e:
        print(f"      AI analysis failed: {e}")
        return None


def analyze_batch_airdrops(mentions):
    """Batch analyze multiple airdrop mentions in one API call (cheaper)."""
    api_key = get_openai_key()
    if not api_key:
        return []

    mentions_text = ""
    for i, (text, source) in enumerate(mentions[:8]):
        mentions_text += f"\n--- MENTION {i+1} [{source}] ---\n{text[:200]}\n"

    prompt = f"""Analyze these crypto airdrop mentions. For each UNIQUE project (merge duplicates), return a JSON array.

Each object must have:
{{
  "project_name": "Name",
  "category": "DeFi/L2/Gaming/AI/Infrastructure/Memecoin/Exchange/Other",
  "qualification_steps": "Numbered steps to qualify",
  "effort_level": "FREE_EASY / LOW_COST / MEDIUM_COST / HIGH_COST / INVITE_ONLY",
  "cost_estimate": "$0 / $5-20 / $100-500 / $1000+ / Unknown",
  "time_required": "5 min / 30 min / 1 hour / ongoing daily / Unknown",
  "reward_estimate": "Expected $ value or Unknown",
  "deadline": "YYYY-MM-DD or Unknown",
  "legitimacy_score": 1-10,
  "legitimacy_reasons": "Why this score",
  "action_recommendation": "PARTICIPATE_NOW / RESEARCH_FIRST / WAIT / SKIP"
}}

Effort levels:
- FREE_EASY: Social tasks, testnet, connect wallet, Galxe/Zealy
- LOW_COST: Gas fees only ($5-20), bridge small amount
- MEDIUM_COST: Need $100-500 in protocol
- HIGH_COST: Need $1000+, exchange tokens, nodes
- INVITE_ONLY: Whitelist, KOL allocation, lottery

Red flags = SCAM:
- "Send X tokens to receive Y" = always scam
- No website/GitHub/Twitter = suspicious
- "Guaranteed returns" = suspicious

Mentions:{mentions_text}

Return ONLY valid JSON array. Merge duplicates. Skip mentions that aren't real airdrops."""

    try:
        res = requests.post('https://api.openai.com/v1/chat/completions',
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
            json={
                'model': 'gpt-4o-mini',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 1200,
                'temperature': 0.2
            },
            timeout=45)

        content = res.json()['choices'][0]['message']['content'].strip()
        if content.startswith('```'):
            content = content.split('\n', 1)[1].rsplit('```', 1)[0]
        return json.loads(content)
    except Exception as e:
        print(f"    Batch AI analysis failed: {e}")
        return []


def process_new_airdrops():
    """
    Main function: scan signals for airdrop mentions,
    analyze with AI, store in airdrop_projects.
    """
    init_airdrop_tables()

    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()

    # Get airdrop signals from last 24h
    c.execute("""
        SELECT DISTINCT title, content, source, source_detail
        FROM signals
        WHERE signal_type = 'AIRDROP'
          AND fetched_at >= datetime('now', '-24 hours')
        ORDER BY engagement DESC
        LIMIT 15
    """)
    raw_airdrops = c.fetchall()

    # Also check exchange listings for launchpad/launchpool (flag as HIGH_COST)
    c.execute("""
        SELECT title, '', 'exchange', exchange
        FROM exchange_listings
        WHERE (title LIKE '%launchpad%' OR title LIKE '%launchpool%'
               OR title LIKE '%kickstarter%' OR title LIKE '%startup%')
          AND fetched_at >= datetime('now', '-7 days')
        LIMIT 5
    """)
    exchange_airdrops = c.fetchall()

    conn.close()

    all_mentions = raw_airdrops + exchange_airdrops

    if not all_mentions:
        print("  No new airdrops to analyze")
        return

    print(f"  Analyzing {len(all_mentions)} airdrop mentions...")

    # Prepare mentions for batch analysis
    batch = []
    for title, content, source, detail in all_mentions:
        text = f"{title} {content[:200]}".strip()
        if len(text) > 20:  # Skip very short/empty mentions
            batch.append((text, f"{source}/{detail}"))

    if not batch:
        print("  No substantial mentions to analyze")
        return

    # Use batch API call (cheaper than individual)
    results = analyze_batch_airdrops(batch)

    if not results:
        print("  AI returned no results")
        return

    # Store results
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    now = datetime.now().isoformat()
    new_count = 0
    updated_count = 0

    for a in results:
        project = a.get('project_name', '')
        if not project or project == 'Unknown':
            continue

        # Check if we already track this project
        c.execute("SELECT id, status FROM airdrop_projects WHERE project_name = ?", (project,))
        existing = c.fetchone()

        if existing:
            # Don't overwrite user-approved projects
            if existing[1] in ('USER_APPROVED', 'ACTIVE', 'COMPLETED'):
                continue
            # Update AI-suggested ones with fresh data
            c.execute("""UPDATE airdrop_projects SET
                         category = ?, qualification_steps = ?,
                         effort_level = ?, cost_estimate = ?, time_required = ?,
                         reward_estimate = ?, deadline = ?,
                         legitimacy_score = ?, legitimacy_reasons = ?,
                         updated_at = ?
                         WHERE id = ?""",
                (a.get('category', ''),
                 a.get('qualification_steps', ''),
                 a.get('effort_level', 'Unknown'),
                 a.get('cost_estimate', 'Unknown'),
                 a.get('time_required', 'Unknown'),
                 a.get('reward_estimate', 'Unknown'),
                 a.get('deadline', 'Unknown'),
                 a.get('legitimacy_score', 5),
                 a.get('legitimacy_reasons', ''),
                 now, existing[0]))
            updated_count += 1
        else:
            # Insert new project
            try:
                c.execute("""INSERT INTO airdrop_projects
                             (project_name, category, qualification_steps,
                              effort_level, cost_estimate, time_required,
                              reward_estimate, deadline,
                              legitimacy_score, legitimacy_reasons,
                              status, sources, created_at, updated_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (project,
                     a.get('category', ''),
                     a.get('qualification_steps', ''),
                     a.get('effort_level', 'Unknown'),
                     a.get('cost_estimate', 'Unknown'),
                     a.get('time_required', 'Unknown'),
                     a.get('reward_estimate', 'Unknown'),
                     a.get('deadline', 'Unknown'),
                     a.get('legitimacy_score', 5),
                     a.get('legitimacy_reasons', ''),
                     'AI_SUGGESTED',
                     '',
                     now, now))
                new_count += 1
            except sqlite3.IntegrityError:
                pass  # Duplicate project name

        # Print result
        score = a.get('legitimacy_score', 5)
        effort = a.get('effort_level', '?')
        action = a.get('action_recommendation', '?')
        if score >= 7:
            emoji = '✅'
        elif score >= 4:
            emoji = '⚠️'
        else:
            emoji = '🚫'

        effort_emoji = {
            'FREE_EASY': '🟢',
            'LOW_COST': '🟡',
            'MEDIUM_COST': '🟠',
            'HIGH_COST': '🔴',
            'INVITE_ONLY': '🔒'
        }.get(effort, '❓')

        print(f"    {emoji} {project}: {score}/10 | {effort_emoji} {effort} | {action}")

    conn.commit()
    conn.close()
    print(f"  Airdrop intel: {new_count} new, {updated_count} updated")


def get_actionable_airdrops():
    """Get airdrops sorted by actionability (free/easy first, legit first)."""
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()

    # Priority: high legitimacy + low effort = best
    c.execute("""
        SELECT project_name, category, qualification_steps,
               effort_level, cost_estimate, time_required,
               reward_estimate, deadline,
               legitimacy_score, legitimacy_reasons,
               status, user_notes
        FROM airdrop_projects
        WHERE status IN ('AI_SUGGESTED', 'USER_APPROVED', 'ACTIVE')
          AND legitimacy_score >= 4
        ORDER BY
            CASE effort_level
                WHEN 'FREE_EASY' THEN 1
                WHEN 'LOW_COST' THEN 2
                WHEN 'MEDIUM_COST' THEN 3
                WHEN 'HIGH_COST' THEN 4
                WHEN 'INVITE_ONLY' THEN 5
                ELSE 6
            END,
            legitimacy_score DESC
    """)
    rows = c.fetchall()
    conn.close()

    return [{
        'project_name': r[0], 'category': r[1],
        'qualification_steps': r[2], 'effort_level': r[3],
        'cost_estimate': r[4], 'time_required': r[5],
        'reward_estimate': r[6], 'deadline': r[7],
        'legitimacy_score': r[8], 'legitimacy_reasons': r[9],
        'status': r[10], 'user_notes': r[11],
    } for r in rows]


def approve_airdrop(project_name, notes=''):
    """User approves an AI-suggested airdrop — moves to ACTIVE tracking."""
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute("""UPDATE airdrop_projects
                 SET status = 'ACTIVE', user_notes = ?, updated_at = ?
                 WHERE project_name = ?""",
              (notes, datetime.now().isoformat(), project_name))
    conn.commit()
    conn.close()
    print(f"  ✅ {project_name} approved and tracked")


def dismiss_airdrop(project_name, reason=''):
    """User dismisses an airdrop — won't show again."""
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute("""UPDATE airdrop_projects
                 SET status = 'DISMISSED', user_notes = ?, updated_at = ?
                 WHERE project_name = ?""",
              (reason, datetime.now().isoformat(), project_name))
    conn.commit()
    conn.close()


def complete_airdrop(project_name):
    """Mark an airdrop as completed — claimed successfully."""
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute("""UPDATE airdrop_projects
                 SET status = 'COMPLETED', updated_at = ?
                 WHERE project_name = ?""",
              (datetime.now().isoformat(), project_name))
    conn.commit()
    conn.close()


if __name__ == '__main__':
    print("AlphaScope — Airdrop Intelligence")
    print("=" * 50)
    process_new_airdrops()
    print()
    print("Actionable Airdrops (sorted by effort):")
    print("-" * 50)
    for a in get_actionable_airdrops():
        effort_emoji = {'FREE_EASY': '🟢', 'LOW_COST': '🟡', 'MEDIUM_COST': '🟠',
                        'HIGH_COST': '🔴', 'INVITE_ONLY': '🔒'}.get(a['effort_level'], '❓')
        legit_emoji = '✅' if a['legitimacy_score'] >= 7 else '⚠️' if a['legitimacy_score'] >= 4 else '🚫'
        print(f"\n  {legit_emoji} {a['project_name']} ({a['category']})")
        print(f"     Effort: {effort_emoji} {a['effort_level']} | Cost: {a['cost_estimate']} | Time: {a['time_required']}")
        print(f"     Reward: {a['reward_estimate']} | Deadline: {a['deadline']}")
        print(f"     Score: {a['legitimacy_score']}/10 — {a['legitimacy_reasons'][:80]}")
        print(f"     Steps: {a['qualification_steps'][:120]}")
        print(f"     Status: {a['status']}")
