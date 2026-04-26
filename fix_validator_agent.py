"""
Fix 1: token_validator.py — ensure DB writes actually commit
Fix 2: wallet_agent.py — in PAPER mode, show unvalidated gems as proposals
         with a warning flag instead of silently dropping them
"""
import ast

# ── Fix 1: token_validator.py ─────────────────────────────────────────────────
with open('token_validator.py', 'r') as f:
    tv = f.read()

# The store block uses a local conn — make sure it commits and closes properly
old_store = (
    "    # Cache to DB\n"
    "    now = datetime.now().isoformat()\n"
    "    try:\n"
    "        conn = get_db()\n"
    "        c = conn.cursor()\n"
    "        c.execute('''INSERT OR REPLACE INTO token_validation\n"
    "            (symbol, contract_address, chain, is_honeypot, sell_tax_pct, buy_tax_pct,\n"
    "             contract_verified, dev_wallet_pct, top10_holders_pct, lp_burned,\n"
    "             website_ok, website_url, github_stars, github_commits_30d, github_url,\n"
    "             twitter_followers, twitter_account_age_days, twitter_engagement_rate,\n"
    "             ai_score, ai_flags, ai_positives, total_score, verdict, cached_at)\n"
    "            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',\n"
    "            (symbol, contract_address, chain,\n"
    "             int(result['is_honeypot']), result['sell_tax_pct'], result['buy_tax_pct'],\n"
    "             int(result['contract_verified']), result['dev_wallet_pct'],\n"
    "             result['top10_holders_pct'], int(result['lp_burned']),\n"
    "             int(result['website_ok']), result['website_url'],\n"
    "             result['github_stars'], result['github_commits_30d'], result['github_url'],\n"
    "             result['twitter_followers'], result['twitter_account_age_days'],\n"
    "             result['twitter_engagement_rate'],\n"
    "             result['ai_score'], result['ai_flags'], result['ai_positives'],\n"
    "             total_score, verdict, now))\n"
    "        conn.commit()\n"
    "        conn.close()\n"
    "    except Exception:\n"
    "        pass"
)
new_store = (
    "    # Cache to DB\n"
    "    now = datetime.now().isoformat()\n"
    "    try:\n"
    "        conn = sqlite3.connect('alphascope.db', timeout=30)\n"
    "        conn.execute('PRAGMA journal_mode=WAL')\n"
    "        c = conn.cursor()\n"
    "        # Ensure table exists\n"
    "        c.execute('''CREATE TABLE IF NOT EXISTS token_validation (\n"
    "            id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "            symbol TEXT, contract_address TEXT, chain TEXT,\n"
    "            is_honeypot INTEGER DEFAULT 0, sell_tax_pct REAL DEFAULT 0,\n"
    "            buy_tax_pct REAL DEFAULT 0, contract_verified INTEGER DEFAULT 0,\n"
    "            dev_wallet_pct REAL DEFAULT 0, top10_holders_pct REAL DEFAULT 0,\n"
    "            lp_burned INTEGER DEFAULT 0, website_ok INTEGER DEFAULT 0,\n"
    "            website_url TEXT, github_stars INTEGER DEFAULT 0,\n"
    "            github_commits_30d INTEGER DEFAULT 0, github_url TEXT,\n"
    "            twitter_followers INTEGER DEFAULT 0,\n"
    "            twitter_account_age_days INTEGER DEFAULT 0,\n"
    "            twitter_engagement_rate REAL DEFAULT 0,\n"
    "            ai_score INTEGER DEFAULT 0, ai_flags TEXT, ai_positives TEXT,\n"
    "            total_score INTEGER DEFAULT 0, verdict TEXT DEFAULT ''UNKNOWN'',\n"
    "            cached_at TEXT, UNIQUE(contract_address, chain))''')\n"
    "        c.execute('''INSERT OR REPLACE INTO token_validation\n"
    "            (symbol, contract_address, chain, is_honeypot, sell_tax_pct, buy_tax_pct,\n"
    "             contract_verified, dev_wallet_pct, top10_holders_pct, lp_burned,\n"
    "             website_ok, website_url, github_stars, github_commits_30d, github_url,\n"
    "             twitter_followers, twitter_account_age_days, twitter_engagement_rate,\n"
    "             ai_score, ai_flags, ai_positives, total_score, verdict, cached_at)\n"
    "            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',\n"
    "            (symbol, contract_address, chain,\n"
    "             int(result['is_honeypot']), result['sell_tax_pct'], result['buy_tax_pct'],\n"
    "             int(result['contract_verified']), result['dev_wallet_pct'],\n"
    "             result['top10_holders_pct'], int(result['lp_burned']),\n"
    "             int(result['website_ok']), result['website_url'],\n"
    "             result['github_stars'], result['github_commits_30d'], result['github_url'],\n"
    "             result['twitter_followers'], result['twitter_account_age_days'],\n"
    "             result['twitter_engagement_rate'],\n"
    "             result['ai_score'], result['ai_flags'], result['ai_positives'],\n"
    "             total_score, verdict, now))\n"
    "        conn.commit()\n"
    "        conn.close()\n"
    "        # Verify write succeeded\n"
    "        vc = sqlite3.connect('alphascope.db', timeout=10)\n"
    "        vr = vc.execute('SELECT id FROM token_validation WHERE contract_address=? AND chain=?',\n"
    "                        (contract_address, chain)).fetchone()\n"
    "        vc.close()\n"
    "        if not vr:\n"
    "            print(f'      ⚠️  DB write verify failed for {symbol}')\n"
    "    except Exception as e:\n"
    "        print(f'      ⚠️  DB store failed for {symbol}: {e}')"
)
if old_store in tv:
    tv = tv.replace(old_store, new_store)
    print("✅ Fix 1: token_validator DB store hardened with explicit sqlite3 + verify")
else:
    print("❌ Fix 1: store block not matched — applying targeted fix")
    # Targeted: just replace get_db() with explicit sqlite3.connect in the cache block
    tv = tv.replace(
        "        conn = get_db()\n        c = conn.cursor()\n        c.execute('''INSERT OR REPLACE INTO token_validation",
        "        conn = sqlite3.connect('alphascope.db', timeout=30)\n        conn.execute('PRAGMA journal_mode=WAL')\n        c = conn.cursor()\n        c.execute('''INSERT OR REPLACE INTO token_validation"
    )
    print("✅ Fix 1b: replaced get_db() with explicit sqlite3.connect in store block")

# Also ensure sqlite3 is imported in token_validator
if 'import sqlite3' not in tv:
    tv = tv.replace('import requests', 'import sqlite3\nimport requests', 1)
    print("✅ Fix 1c: added import sqlite3")

with open('token_validator.py', 'w') as f:
    f.write(tv)
try:
    ast.parse(tv)
    print("✅ token_validator.py syntax OK")
except SyntaxError as e:
    print(f"❌ token_validator.py {e.lineno}: {e.msg}")


# ── Fix 2: wallet_agent.py — paper mode shows unvalidated gems ───────────────
with open('wallet_agent.py', 'r') as f:
    wa = f.read()

old_unknown = (
    "                else:\n"
    "                    # Not validated yet — downgrade to WATCH, don't buy blind\n"
    "                    action = 'WATCH'\n"
    "                    trade_usd = 0\n"
    "                    c['reasons'].append('not yet validated — skipping auto-buy')\n"
    "            except Exception:\n"
    "                pass"
)
new_unknown = (
    "                else:\n"
    "                    # Not validated yet\n"
    "                    _mode = get_config('mode', 'PAPER')\n"
    "                    if _mode == 'LIVE':\n"
    "                        # Live mode: never buy unvalidated\n"
    "                        action = 'WATCH'\n"
    "                        trade_usd = 0\n"
    "                    else:\n"
    "                        # Paper mode: show proposal but flag it and cap size\n"
    "                        trade_usd = min(trade_usd, 25)\n"
    "                        c['reasons'].append('⚠️ unvalidated — paper only, capped $25')\n"
    "            except Exception:\n"
    "                pass"
)
if old_unknown in wa:
    wa = wa.replace(old_unknown, new_unknown)
    print("✅ Fix 2: unvalidated gems show in PAPER mode (capped $25), blocked in LIVE")
else:
    print("❌ Fix 2: unknown block not matched")

with open('wallet_agent.py', 'w') as f:
    f.write(wa)
try:
    ast.parse(wa)
    print("✅ wallet_agent.py syntax OK")
except SyntaxError as e:
    print(f"❌ wallet_agent.py {e.lineno}: {e.msg}")

print("\n✅ Done. Run: python3 -c \"from wallet_agent import run_agent; run_agent(dry_run=True)\"")
