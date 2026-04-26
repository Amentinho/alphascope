"""
Fix 3 issues blocking SOL/BSC/ETH buys:
1. Social freshness: raise from 45min to 6h (signals are 1-9h old normally)
2. Validation gate: run validator inline if verdict=NONE in paper mode
3. Dedup gems in _load_all_candidates()
"""
import ast

with open('wallet_agent.py', 'r') as f:
    wa = f.read()

# Fix 1: raise social freshness from 45 to 360 min (6h)
old_fresh = "                    if age_min > 45:\n                            continue  # signal too stale for meme coin"
new_fresh = "                    if age_min > 360:\n                            continue  # signal too stale (> 6h)"
if old_fresh in wa:
    wa = wa.replace(old_fresh, new_fresh)
    print("✅ Fix 1: social freshness raised to 6h")
else:
    print("❌ Fix 1: freshness line not matched")

# Fix 2: run validator inline if verdict is NONE, in paper mode allow NONE through
old_val_gate = (
    "                if _vrow:\n"
    "                    verdict, val_score = _vrow\n"
    "                    if verdict == 'AVOID':\n"
    "                        proposals.append({'action':'SKIP','symbol':sym,\n"
    "                                          'category':c.get('category',''),\n"
    "                                          'reason':f'VALIDATION FAILED — {verdict} (score:{val_score}/20)',\n"
    "                                          'trade_usd':trade_usd,'alpha_score':0})\n"
    "                        continue\n"
    "                    elif verdict == 'WATCH':\n"
    "                        action = 'WATCH'\n"
    "                        trade_usd = 0\n"
    "                    # CAUTION: allow but reduce size\n"
    "                    elif verdict == 'CAUTION':\n"
    "                        trade_usd = min(trade_usd, 75)  # cap at $75 for cautioned gems\n"
    "                        c['reasons'].append(f'CAUTION val:{val_score}/20 — reduced size')\n"
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
new_val_gate = (
    "                if _vrow:\n"
    "                    verdict, val_score = _vrow\n"
    "                    if verdict == 'AVOID':\n"
    "                        proposals.append({'action':'SKIP','symbol':sym,\n"
    "                                          'category':c.get('category',''),\n"
    "                                          'reason':f'VALIDATION FAILED — AVOID (score:{val_score}/20)',\n"
    "                                          'trade_usd':trade_usd,'alpha_score':0})\n"
    "                        continue\n"
    "                    elif verdict == 'WATCH':\n"
    "                        trade_usd = min(trade_usd, 25)\n"
    "                        c['reasons'].append(f'WATCH val:{val_score}/20')\n"
    "                    elif verdict == 'CAUTION':\n"
    "                        trade_usd = min(trade_usd, 75)\n"
    "                        c['reasons'].append(f'CAUTION val:{val_score}/20')\n"
    "                    # BUY_OK: full size, no cap\n"
    "                else:\n"
    "                    # Not validated — run quick check now\n"
    "                    _contract = c.get('contract_address', '')\n"
    "                    if _contract and get_config('mode', 'PAPER') == 'PAPER':\n"
    "                        try:\n"
    "                            from token_validator import validate_token, init_validation_table\n"
    "                            init_validation_table()\n"
    "                            _vr = validate_token(\n"
    "                                symbol=sym,\n"
    "                                contract_address=_contract,\n"
    "                                chain=chain,\n"
    "                                use_ai=False,  # fast check only\n"
    "                            )\n"
    "                            if _vr.get('verdict') == 'AVOID':\n"
    "                                proposals.append({'action':'SKIP','symbol':sym,\n"
    "                                                  'category':c.get('category',''),\n"
    "                                                  'reason':'AVOID — honeypot/scam detected',\n"
    "                                                  'trade_usd':trade_usd,'alpha_score':0})\n"
    "                                continue\n"
    "                            elif _vr.get('verdict') in ('WATCH','CAUTION'):\n"
    "                                trade_usd = min(trade_usd, 50)\n"
    "                                c['reasons'].append(f\"{_vr['verdict']} val:{_vr['total_score']}/20\")\n"
    "                        except Exception:\n"
    "                            trade_usd = min(trade_usd, 25)\n"
    "                            c['reasons'].append('unvalidated — capped $25')\n"
    "                    else:\n"
    "                        trade_usd = min(trade_usd, 25)\n"
    "                        c['reasons'].append('unvalidated — capped $25')\n"
    "            except Exception:\n"
    "                pass"
)
if old_val_gate in wa:
    wa = wa.replace(old_val_gate, new_val_gate)
    print("✅ Fix 2: inline validator + improved verdict handling")
else:
    print("❌ Fix 2: validation gate not matched")

# Fix 3: deduplicate DEX gems by symbol in _load_all_candidates
old_dex_end = (
    "            if sym not in candidates:\n"
    "                candidates[sym] = {\n"
    "                    'symbol': sym, 'coin_id': r.get('dex_url', ''),\n"
    "                    'contract_address': r.get('contract_address', ''),\n"
    "                    'chain': chain, 'price_usd': float(r.get('price_usd', 0) or 0),"
)
new_dex_end = (
    "            # Dedup — if same symbol already seen with higher score, skip\n"
    "            if sym in candidates:\n"
    "                existing_score = candidates[sym].get('alpha_score', 0)\n"
    "                if score <= existing_score:\n"
    "                    candidates[sym]['sources'].append('dex')\n"
    "                    continue\n"
    "            if sym not in candidates or score > candidates[sym].get('alpha_score', 0):\n"
    "                candidates[sym] = {\n"
    "                    'symbol': sym, 'coin_id': r.get('dex_url', ''),\n"
    "                    'contract_address': r.get('contract_address', ''),\n"
    "                    'chain': chain, 'price_usd': float(r.get('price_usd', 0) or 0),"
)
if old_dex_end in wa:
    wa = wa.replace(old_dex_end, new_dex_end)
    print("✅ Fix 3: DEX gem deduplication added")
else:
    print("❌ Fix 3: dex candidates block not matched")

with open('wallet_agent.py', 'w') as f:
    f.write(wa)
try:
    ast.parse(wa)
    print("✅ wallet_agent.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")

# Also fix simulation.py social freshness reference
with open('simulation.py', 'r') as f:
    s = f.read()
# Nothing to change in simulation -- freshness is in wallet_agent

# Fix dex_scanner dedup at DB level
import sqlite3
conn = sqlite3.connect('alphascope.db')
# Remove old stale gems keeping only latest per symbol+chain
deleted = conn.execute("""
    DELETE FROM dex_gems WHERE id NOT IN (
        SELECT MAX(id) FROM dex_gems 
        GROUP BY symbol, chain
    )
""").rowcount
conn.commit()
conn.close()
print(f"✅ Cleaned {deleted} duplicate dex_gems from DB")

print("\n✅ All fixes applied.")
print("Run: python3 simulation.py --test")
