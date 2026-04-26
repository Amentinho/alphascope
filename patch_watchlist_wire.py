"""
Wire project_watchlist into:
1. token_validator.py — auto_add_from_validator() after scoring
2. fetcher.py — monitor_watchlist() as Phase 5e
3. dashboard.py — Watchlist tab in the UI
"""
import ast

# ── 1. token_validator.py ─────────────────────────────────────────────────────
with open('token_validator.py', 'r') as f:
    tv = f.read()

old_print = (
    "    flag_str = ' | '.join(flags[:4])\n"
    "    pos_str = ' | '.join(positives[:3])\n"
    "    verdict_emoji = {'AVOID': '🚫', 'WATCH': '👁', 'CAUTION': '⚠️', 'BUY_OK': '✅'}.get(verdict, '?')\n"
    "    print(f\"      {verdict_emoji} {symbol}: {verdict} (score:{total}/20)\")\n"
    "    if flags: print(f\"         ⛔ {flag_str}\")\n"
    "    if positives: print(f\"         ✅ {pos_str}\")\n"
    "\n"
    "    return result"
)
new_print = (
    "    flag_str = ' | '.join(flags[:4])\n"
    "    pos_str = ' | '.join(positives[:3])\n"
    "    verdict_emoji = {'AVOID': '🚫', 'WATCH': '👁', 'CAUTION': '⚠️', 'BUY_OK': '✅'}.get(verdict, '?')\n"
    "    print(f\"      {verdict_emoji} {symbol}: {verdict} (score:{total}/20)\")\n"
    "    if flags: print(f\"         ⛔ {flag_str}\")\n"
    "    if positives: print(f\"         ✅ {pos_str}\")\n"
    "\n"
    "    # Auto-add to watchlist if strong fundamentals but not yet tradeable\n"
    "    try:\n"
    "        from project_watchlist import auto_add_from_validator\n"
    "        auto_add_from_validator(result)\n"
    "    except ImportError:\n"
    "        pass\n"
    "\n"
    "    return result"
)
if old_print in tv:
    tv = tv.replace(old_print, new_print)
    print("✅ token_validator: auto_add_from_validator() wired in")
else:
    print("❌ token_validator: print block not matched")

with open('token_validator.py', 'w') as f:
    f.write(tv)
try:
    ast.parse(tv)
    print("✅ token_validator.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")


# ── 2. fetcher.py — Phase 5e ──────────────────────────────────────────────────
with open('fetcher.py', 'r') as f:
    ft = f.read()

old_9c = (
    "    # Phase 9c: Agent evaluation (paper trading)\n"
    "    try:\n"
    "        from wallet_agent import run_agent\n"
    "        run_agent(dry_run=False)\n"
    "    except ImportError:\n"
    "        pass\n"
    "    except Exception as e:\n"
    "        print(f\"  Agent failed: {e}\")"
)
new_9c = (
    "    # Phase 5e: Watchlist monitoring\n"
    "    try:\n"
    "        from project_watchlist import monitor_watchlist\n"
    "        monitor_watchlist()\n"
    "    except ImportError:\n"
    "        pass\n"
    "    except Exception as e:\n"
    "        print(f\"  Watchlist monitor failed: {e}\")\n"
    "\n"
    "    # Phase 9c: Agent evaluation (paper trading)\n"
    "    try:\n"
    "        from wallet_agent import run_agent\n"
    "        run_agent(dry_run=False)\n"
    "    except ImportError:\n"
    "        pass\n"
    "    except Exception as e:\n"
    "        print(f\"  Agent failed: {e}\")"
)
if old_9c in ft:
    ft = ft.replace(old_9c, new_9c)
    print("✅ fetcher.py: Phase 5e watchlist monitor added")
else:
    print("❌ fetcher.py: Phase 9c not matched")

with open('fetcher.py', 'w') as f:
    f.write(ft)
try:
    ast.parse(ft)
    print("✅ fetcher.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")


# ── 3. dashboard.py — Watchlist tab ──────────────────────────────────────────
with open('dashboard.py', 'r') as f:
    db = f.read()

# Add loader
old_load = "def load_agent_trades():"
new_load = (
    "def load_watchlist():\n"
    "    try:\n"
    "        from project_watchlist import get_watchlist_summary, get_unseen_alerts\n"
    "        return get_watchlist_summary(), get_unseen_alerts()\n"
    "    except Exception:\n"
    "        import pandas as pd\n"
    "        return pd.DataFrame(), pd.DataFrame()\n"
    "\n\n"
    "def load_agent_trades():"
)
if old_load in db:
    db = db.replace(old_load, new_load)
    print("✅ dashboard: load_watchlist() added")

# Add tab button
old_btn = "            ('portfolio', '💼 Portfolio'), ('agent', '🤖 Agent'), ('alpha', '💎 Alpha'),"
new_btn = "            ('portfolio', '💼 Portfolio'), ('agent', '🤖 Agent'), ('watchlist', '👁 Watchlist'), ('alpha', '💎 Alpha'),"
if old_btn in db:
    db = db.replace(old_btn, new_btn)
    print("✅ dashboard: Watchlist tab button added")

# Add to callback inputs
old_cb = "['portfolio','agent','alpha','buzz','airdrops_tab','narratives','listings','whales','news','macro','reddit']]"
new_cb = "['portfolio','agent','watchlist','alpha','buzz','airdrops_tab','narratives','listings','whales','news','macro','reddit']]"
if old_cb in db:
    db = db.replace(old_cb, new_cb)
    print("✅ dashboard: watchlist in callback inputs")

# Add tab handler before agent handler
old_agent_handler = "    if tab == 'agent':"
new_wl_handler = (
    "    if tab == 'watchlist':\n"
    "        wl, alerts = load_watchlist()\n"
    "        items = [\n"
    "            html.H3('👁 Project Watchlist', style={'color':'#00d4ff','marginBottom':'4px'}),\n"
    "            html.P('Promising projects not yet tradeable. Monitoring for DEX launch, presale, listing.',\n"
    "                   style={'color':'#555','fontSize':'11px','marginBottom':'12px'}),\n"
    "        ]\n"
    "        # Unseen alerts first\n"
    "        if not alerts.empty:\n"
    "            items.append(html.Div('🚨 New alerts', style={'color':'#ff4444','fontSize':'12px',\n"
    "                                                           'fontWeight':'bold','marginBottom':'8px'}))\n"
    "            for _, a in alerts.iterrows():\n"
    "                urg_col = '#ff4444' if a['urgency']=='HIGH' else '#ff8c00' if a['urgency']=='MEDIUM' else '#888'\n"
    "                items.append(html.Div(style={'background':'#1a0a0a','border':f'1px solid {urg_col}',\n"
    "                                             'borderRadius':'6px','padding':'10px','marginBottom':'8px'}, children=[\n"
    "                    html.Div(style={'display':'flex','justifyContent':'space-between'}, children=[\n"
    "                        html.Span(f\"{a['project_name']} — {a['alert_type']}\",\n"
    "                                 style={'color':urg_col,'fontWeight':'bold','fontSize':'13px'}),\n"
    "                        html.Span(a['urgency'], style={'color':urg_col,'fontSize':'11px'}),\n"
    "                    ]),\n"
    "                    html.P(a['alert_detail'], style={'color':'#aaa','fontSize':'11px','margin':'4px 0 2px'}),\n"
    "                    html.P(f\"→ {a['action_recommended']}\",\n"
    "                           style={'color':'#00d4ff','fontSize':'11px','margin':'0'}),\n"
    "                ]))\n"
    "        # Watchlist projects\n"
    "        if wl.empty:\n"
    "            items.append(html.P('No projects being watched yet. High-scoring unlistable tokens will appear here automatically.',\n"
    "                               style={'color':'#555','fontSize':'12px'}))\n"
    "        else:\n"
    "            status_order = {'LIVE':0,'PRESALE_DETECTED':1,'WATCHING':2}\n"
    "            for _, r in wl.iterrows():\n"
    "                status = r.get('status','WATCHING')\n"
    "                status_colors = {'LIVE':'#00cc44','PRESALE_DETECTED':'#ff8c00','WATCHING':'#888'}\n"
    "                status_col = status_colors.get(status,'#888')\n"
    "                went_live = r.get('went_live_at','')\n"
    "                items.append(html.Div(style={'padding':'10px 0','borderBottom':'1px solid #1a1a3e'}, children=[\n"
    "                    html.Div(style={'display':'flex','justifyContent':'space-between','alignItems':'center'}, children=[\n"
    "                        html.Span(f\"{r['project_name']} {('$'+r['symbol']) if r.get('symbol') else ''}\",\n"
    "                                 style={'color':'#00d4ff','fontWeight':'bold','fontSize':'13px'}),\n"
    "                        html.Span(status, style={'color':status_col,'fontSize':'11px',\n"
    "                                                 'border':f'1px solid {status_col}',\n"
    "                                                 'padding':'2px 6px','borderRadius':'3px'}),\n"
    "                    ]),\n"
    "                    html.Div(style={'display':'flex','gap':'12px','fontSize':'11px','color':'#666','marginTop':'3px'}, children=[\n"
    "                        html.Span(f\"score:{r.get('fundamentals_score',0)}/20\"),\n"
    "                        html.Span(f\"⭐{r.get('github_stars',0)} GitHub\") if r.get('github_stars',0) > 0 else html.Span(''),\n"
    "                        html.Span(f\"🐦{r.get('twitter_followers',0):,}\") if r.get('twitter_followers',0) > 0 else html.Span(''),\n"
    "                        html.Span(f\"went live: {str(went_live)[:10]}\",\n"
    "                                 style={'color':'#00cc44'}) if went_live else html.Span(''),\n"
    "                    ]),\n"
    "                    html.P(r.get('why_watching','')[:80],\n"
    "                           style={'color':'#555','fontSize':'11px','margin':'3px 0 0'}),\n"
    "                    html.P(f\"Alert: {r.get('alert_notes','')[:80]}\",\n"
    "                           style={'color':'#ff8c00','fontSize':'11px','margin':'2px 0 0'})\n"
    "                    if r.get('alert_notes') else html.Span(''),\n"
    "                ]))\n"
    "        return items, style\n"
    "\n"
    "    elif tab == 'agent':"
)
if old_agent_handler in db:
    db = db.replace(old_agent_handler, new_wl_handler)
    print("✅ dashboard: Watchlist tab handler added")
else:
    print("❌ dashboard: agent handler not matched for watchlist insertion")

with open('dashboard.py', 'w') as f:
    f.write(db)
try:
    ast.parse(db)
    print("✅ dashboard.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")

print("\n✅ Watchlist wired. Run: python3 project_watchlist.py then python3 fetcher.py")
