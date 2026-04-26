"""
Wire token_validator and social_monitor into fetcher.py and wallet_agent.py
"""
import ast

# ── fetcher.py — add Phase 5d social monitoring ──────────────────────────────
with open('fetcher.py', 'r') as f:
    ft = f.read()

old = (
    "    # Phase 5c: Security monitoring\n"
    "    try:\n"
    "        from security_monitor import fetch_security_data\n"
    "        fetch_security_data()\n"
    "    except ImportError:\n"
    "        print(\"  security_monitor.py not found — skipping\")\n"
    "    except Exception as e:\n"
    "        print(f\"  Security monitor failed: {e}\")"
)
new = (
    "    # Phase 5c: Security monitoring\n"
    "    try:\n"
    "        from security_monitor import fetch_security_data\n"
    "        fetch_security_data()\n"
    "    except ImportError:\n"
    "        print(\"  security_monitor.py not found — skipping\")\n"
    "    except Exception as e:\n"
    "        print(f\"  Security monitor failed: {e}\")\n"
    "\n"
    "    # Phase 5d: Social monitoring (tiered, cached)\n"
    "    try:\n"
    "        from social_monitor import run_social_monitoring\n"
    "        run_social_monitoring()\n"
    "    except ImportError:\n"
    "        print(\"  social_monitor.py not found — skipping\")\n"
    "    except Exception as e:\n"
    "        print(f\"  Social monitor failed: {e}\")"
)
if old in ft:
    ft = ft.replace(old, new)
    print("✅ fetcher.py: Phase 5d social monitor added")
else:
    print("❌ fetcher.py: Phase 5c block not matched")

with open('fetcher.py', 'w') as f:
    f.write(ft)
try:
    ast.parse(ft)
    print("✅ fetcher.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")

# ── wallet_agent.py — block BUY if validation fails ──────────────────────────
with open('wallet_agent.py', 'r') as f:
    wa = f.read()

# Add validation check after liquidity check, before gas check
old_gas = (
    "        # Gas check\n"
    "        gas = estimate_gas_price(chain)"
)
new_gas = (
    "        # Token validation — block BUY for unvalidated gems\n"
    "        if action == 'BUY' and not is_holding and c.get('contract_address'):\n"
    "            try:\n"
    "                from token_validator import validate_token, get_cached\n"
    "                cached_val = get_cached(c.get('contract_address',''), chain, max_age_minutes=30)\n"
    "                if cached_val:\n"
    "                    verdict = cached_val.get('verdict', 'UNKNOWN')\n"
    "                else:\n"
    "                    verdict = 'UNKNOWN'  # not yet validated — downgrade to WATCH\n"
    "                if verdict == 'AVOID':\n"
    "                    proposals.append({'action':'SKIP','symbol':sym,'category':c.get('category',''),\n"
    "                                      'reason':'VALIDATION FAILED — honeypot/scam detected',\n"
    "                                      'trade_usd':trade_usd,'alpha_score':0})\n"
    "                    continue\n"
    "                elif verdict in ('UNKNOWN', 'WATCH'):\n"
    "                    action = 'WATCH'  # downgrade — needs validation first\n"
    "                    trade_usd = 0\n"
    "            except ImportError:\n"
    "                pass\n"
    "\n"
    "        # Social signal boost/block for DEX gems\n"
    "        if c.get('category') == 'DEX_GEM' and action in ('BUY', 'WATCH'):\n"
    "            try:\n"
    "                from social_monitor import get_social_signal\n"
    "                social = get_social_signal(sym, chain)\n"
    "                if social:\n"
    "                    sig = social.get('signal', 'NEUTRAL')\n"
    "                    sent = social.get('sentiment', 0)\n"
    "                    velocity = social.get('velocity', 'UNKNOWN')\n"
    "                    if sig == 'STRONG_BUY' and velocity == 'ACCELERATING':\n"
    "                        trade_usd = min(MAX_POSITION_USD, trade_usd * 1.5)  # size up\n"
    "                        confidence = min(90, confidence + 15)\n"
    "                        c['reasons'].append(f'social STRONG_BUY accelerating')\n"
    "                    elif sig in ('SELL', 'WATCH_OUT') or sent < -0.3:\n"
    "                        action = 'SKIP'\n"
    "                        proposals.append({'action':'SKIP','symbol':sym,'category':c.get('category',''),\n"
    "                                          'reason':f'social signal {sig} (sent:{sent:+.2f})',\n"
    "                                          'trade_usd':trade_usd,'alpha_score':alpha_score})\n"
    "                        continue\n"
    "            except ImportError:\n"
    "                pass\n"
    "\n"
    "        if action == 'WATCH' and trade_usd == 0:\n"
    "            continue  # pure watch — no trade\n"
    "\n"
    "        # Gas check\n"
    "        gas = estimate_gas_price(chain)"
)
if old_gas in wa:
    wa = wa.replace(old_gas, new_gas)
    print("✅ wallet_agent.py: validation + social gate added before BUY")
else:
    print("❌ wallet_agent.py: gas check block not matched")

# Add contract_address to _load_all_candidates DEX section
old_dex_item = (
    "            if sym not in candidates:\n"
    "                candidates[sym] = {\n"
    "                    'symbol': sym, 'coin_id': r.get('dex_url', ''),\n"
    "                    'chain': chain, 'price_usd': float(r.get('price_usd', 0) or 0),"
)
new_dex_item = (
    "            if sym not in candidates:\n"
    "                candidates[sym] = {\n"
    "                    'symbol': sym, 'coin_id': r.get('dex_url', ''),\n"
    "                    'contract_address': r.get('contract_address', ''),\n"
    "                    'chain': chain, 'price_usd': float(r.get('price_usd', 0) or 0),"
)
if old_dex_item in wa:
    wa = wa.replace(old_dex_item, new_dex_item)
    print("✅ wallet_agent.py: contract_address passed through to validation")
else:
    print("❌ wallet_agent.py: dex candidates block not matched")

with open('wallet_agent.py', 'w') as f:
    f.write(wa)
try:
    ast.parse(wa)
    print("✅ wallet_agent.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")

print("\n✅ All done.")
print("Run: python3 fetcher.py")
print("Then check: python3 wallet_agent.py")
