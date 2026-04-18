"""
AlphaScope v2.2 — Executive Dashboard
Clean, actionable. Three boxes + alerts. Details one click away.
"""

import sqlite3
import pandas as pd
from dash import Dash, html, dcc, callback_context
from dash.dependencies import Input, Output, State
import plotly.graph_objects as go
from datetime import datetime
import requests
import json

app = Dash(__name__, suppress_callback_exceptions=True)
app.title = "AlphaScope"

def get_db():
    return sqlite3.connect('alphascope.db', timeout=30)

# ============================================================
# DATA LOADERS
# ============================================================
def load_fear_greed():
    conn = get_db()
    df = pd.read_sql_query("SELECT value, label, timestamp FROM fear_greed ORDER BY timestamp DESC LIMIT 30", conn)
    conn.close()
    return df

def load_coin_buzz():
    conn = get_db()
    df = pd.read_sql_query("SELECT coin, mention_count, total_engagement, avg_sentiment, sources FROM coin_buzz ORDER BY fetched_at DESC, mention_count DESC LIMIT 20", conn)
    conn.close()
    return df

def load_prices():
    conn = get_db()
    df = pd.read_sql_query(
        """SELECT coin_id, name, symbol, price_usd, change_24h, change_7d, market_cap
           FROM token_data WHERE fetched_at >= (SELECT datetime(MAX(fetched_at), '-5 minutes') FROM token_data)
           AND name IS NOT NULL ORDER BY market_cap DESC""", conn)
    conn.close()
    return df

def load_hidden_gems():
    conn = get_db()
    df = pd.read_sql_query("SELECT name, symbol, market_cap_rank, signal_type, signal_detail FROM hidden_gems ORDER BY fetched_at DESC LIMIT 10", conn)
    conn.close()
    return df

def load_narratives():
    conn = get_db()
    df = pd.read_sql_query("SELECT narrative, mention_count FROM narratives ORDER BY fetched_at DESC, mention_count DESC LIMIT 10", conn)
    conn.close()
    return df

def load_trending():
    conn = get_db()
    df = pd.read_sql_query("SELECT name, symbol, market_cap_rank FROM trending ORDER BY fetched_at DESC LIMIT 10", conn)
    conn.close()
    return df

def load_airdrops():
    try:
        conn = get_db()
        df = pd.read_sql_query(
            """SELECT project_name, category, qualification_steps, effort_level, cost_estimate,
                      time_required, reward_estimate, deadline, legitimacy_score, legitimacy_reasons, status
               FROM airdrop_projects WHERE status IN ('AI_SUGGESTED','USER_APPROVED','ACTIVE')
               ORDER BY CASE effort_level WHEN 'FREE_EASY' THEN 1 WHEN 'LOW_COST' THEN 2
               WHEN 'MEDIUM_COST' THEN 3 WHEN 'HIGH_COST' THEN 4 ELSE 5 END,
               legitimacy_score DESC""", conn)
        conn.close()
        return df
    except:
        return pd.DataFrame()

def load_signals(signal_type, limit=10):
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT source, source_detail, title, content, coin, sentiment_score, sentiment_label, engagement, url FROM signals WHERE signal_type=? ORDER BY fetched_at DESC, engagement DESC LIMIT ?",
        conn, params=(signal_type, limit))
    conn.close()
    return df

def load_exchange_listings():
    try:
        conn = get_db()
        df = pd.read_sql_query("SELECT exchange, exchange_tier, coin, title, url FROM exchange_listings ORDER BY fetched_at DESC LIMIT 15", conn)
        conn.close()
        return df
    except:
        return pd.DataFrame()

def load_macro_summary():
    try:
        from macro_calendar import load_macro_summary as lms
        return lms()
    except:
        return ""

def load_macro_indicators():
    try:
        conn = get_db()
        df = pd.read_sql_query("SELECT indicator, value, change_pct, date FROM macro_indicators ORDER BY fetched_at DESC", conn)
        conn.close()
        return df.drop_duplicates(subset=['indicator'], keep='first')
    except:
        return pd.DataFrame()

def load_macro_events():
    try:
        conn = get_db()
        df = pd.read_sql_query(
            "SELECT event_name, category, impact, crypto_impact, actual FROM macro_events ORDER BY CASE impact WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END", conn)
        conn.close()
        return df.drop_duplicates(subset=['event_name'], keep='first')
    except:
        return pd.DataFrame()

def load_sentiment():
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT source_detail, coin, sentiment_score, sentiment_label FROM signals WHERE signal_type='SENTIMENT' ORDER BY fetched_at DESC LIMIT 10", conn)
    conn.close()
    return df

# ============================================================
# AI BRIEF
# ============================================================
def generate_ai_brief():
    api_key = ''
    try:
        with open('.env') as f:
            for line in f:
                if line.startswith('OPENAI_API_KEY='): api_key = line.strip().split('=',1)[1]
    except: pass
    if not api_key: return "Add OPENAI_API_KEY to .env"

    fg = load_fear_greed()
    prices = load_prices()
    buzz = load_coin_buzz()
    gems = load_hidden_gems()
    airdrops = load_airdrops()
    whales = load_signals('WHALE', 3)
    listings = load_exchange_listings()
    macro = load_macro_indicators()
    events = load_macro_events()
    narratives = load_narratives()

    prompt = f"""You are AlphaScope, a crypto alpha intelligence analyst. Give me a concise brief (300 words max).

MARKET: {f"Crypto Fear & Greed: {fg.iloc[0]['value']}/100 ({fg.iloc[0]['label']})" if not fg.empty else "N/A"}

MACRO: {chr(10).join(f"- {r['indicator']}: {r['value']:.2f}" for _,r in macro.iterrows()) if not macro.empty else "N/A"}

UPCOMING EVENTS: {', '.join(f"{r['event_name']}({r['impact']})" for _,r in events.head(3).iterrows()) if not events.empty else "N/A"}

COIN BUZZ (social mentions): {', '.join(f"{r['coin']}({r['mention_count']}, sent:{r['avg_sentiment']:+.2f})" for _,r in buzz.head(8).iterrows()) if not buzz.empty else "N/A"}

PRICES: {chr(10).join(f"- {r['symbol']}: ${r['price_usd']:,.2f} ({r['change_24h']:+.1f}%)" for _,r in prices.iterrows()) if not prices.empty else "N/A"}

NARRATIVES: {', '.join(f"{r['narrative']}({r['mention_count']})" for _,r in narratives.iterrows()) if not narratives.empty else "N/A"}

HIDDEN GEMS: {chr(10).join(f"- {r['symbol']} — {r['signal_detail']}" for _,r in gems.head(5).iterrows()) if not gems.empty else "None"}

EXCHANGE LISTINGS: {chr(10).join(f"- {r['exchange']}: {r['title'][:60]}" for _,r in listings.head(3).iterrows()) if not listings.empty else "None"}

WHALES: {chr(10).join(f"- {r['title'][:80]}" for _,r in whales.iterrows()) if not whales.empty else "N/A"}

AIRDROPS: {chr(10).join(f"- {r['project_name']} ({r['effort_level']}, {r['legitimacy_score']}/10)" for _,r in airdrops.head(3).iterrows()) if not airdrops.empty else "None"}

Output exactly:
## TOP 3 ALPHA PICKS (coins to watch for gains)
## TOP 3 AIRDROPS (actionable, free/low-cost only)  
## TOP 3 INVESTMENT SIGNALS (what to buy/sell right now)
## MACRO WARNING (if any upcoming events could move markets)
## ACTION ITEMS (3 specific things to do today)

Be brutally direct. Confidence levels for each pick. No fluff."""

    try:
        res = requests.post('https://api.openai.com/v1/chat/completions',
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
            json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': prompt}],
                  'max_tokens': 700, 'temperature': 0.7}, timeout=30)
        return res.json()['choices'][0]['message']['content']
    except Exception as e:
        return f"AI brief failed: {e}"

# ============================================================
# HELPERS
# ============================================================
def clean_html(text):
    if not text: return ''
    return str(text).replace('&#036;', '$').replace('&quot;', '"').replace('&#39;', "'").replace('&amp;', '&')

def effort_badge(level):
    m = {'FREE_EASY': ('🟢', '#00cc44'), 'LOW_COST': ('🟡', '#ffdd00'),
         'MEDIUM_COST': ('🟠', '#ff8c00'), 'HIGH_COST': ('🔴', '#ff4444'),
         'INVITE_ONLY': ('🔒', '#888')}
    emoji, color = m.get(level, ('❓', '#888'))
    return html.Span(f"{emoji} {level}", style={'color': color, 'fontSize': '11px', 'fontWeight': 'bold'})

# ============================================================
# LAYOUT — Clean executive view
# ============================================================
CARD = {'backgroundColor': '#1a1a2e', 'borderRadius': '12px', 'padding': '20px',
        'border': '1px solid #2a2a4a', 'marginBottom': '0'}

app.layout = html.Div(style={
    'backgroundColor': '#0f0f23', 'minHeight': '100vh', 'padding': '20px 20px 40px',
    'fontFamily': "'JetBrains Mono', 'SF Mono', 'Fira Code', monospace",
    'color': '#fff', 'maxWidth': '1200px', 'margin': '0 auto'
}, children=[

    # ── HEADER ──
    html.Div(style={'textAlign': 'center', 'marginBottom': '15px'}, children=[
        html.H1("ALPHASCOPE", style={'fontSize': '28px', 'letterSpacing': '6px', 'color': '#00d4ff',
                                      'marginBottom': '5px', 'fontWeight': '300'}),
        html.P(id='macro-bar', style={'color': '#888', 'fontSize': '12px', 'letterSpacing': '1px'}),
        html.P(id='updated', style={'color': '#444', 'fontSize': '10px'}),
    ]),

    # ── MARKET PULSE BAR ──
    html.Div(id='pulse-bar', style={'display': 'flex', 'gap': '10px', 'marginBottom': '15px',
        'justifyContent': 'center', 'flexWrap': 'wrap'}),

    # ── AI BRIEF BUTTON ──
    html.Div(style={'textAlign': 'center', 'marginBottom': '20px'}, children=[
        html.Button("🤖 AI ALPHA BRIEF", id='ai-btn', n_clicks=0,
            style={'padding': '8px 20px', 'backgroundColor': 'transparent', 'border': '1px solid #00d4ff',
                   'borderRadius': '4px', 'color': '#00d4ff', 'cursor': 'pointer', 'fontSize': '12px',
                   'letterSpacing': '2px', 'fontFamily': 'inherit'}),
    ]),
    html.Div(id='ai-box', style={**CARD, 'display': 'none', 'marginBottom': '20px', 'borderColor': '#00d4ff'}, children=[
        dcc.Markdown(id='ai-text', style={'color': '#ccc', 'lineHeight': '1.6', 'fontSize': '13px'}),
    ]),

    dcc.Interval(id='refresh', interval=1800*1000, n_intervals=0),

    # ══════════════════════════════════════════════════════════
    # THREE MAIN BOXES
    # ══════════════════════════════════════════════════════════
    html.Div(style={'display': 'flex', 'gap': '15px', 'marginBottom': '20px', 'flexWrap': 'wrap'}, children=[

        # ── BOX 1: ALPHA PICKS ──
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'borderColor': '#00d4ff', 'borderWidth': '2px'}, children=[
            html.H3("🎯 ALPHA PICKS", style={'color': '#00d4ff', 'fontSize': '14px', 'letterSpacing': '2px', 'marginBottom': '12px'}),
            html.P("Rising buzz + positive sentiment + catalysts", style={'color': '#555', 'fontSize': '10px', 'marginBottom': '12px'}),
            html.Div(id='box-alpha'),
        ]),

        # ── BOX 2: AIRDROPS ──
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'borderColor': '#cc44ff', 'borderWidth': '2px'}, children=[
            html.H3("🪂 AIRDROPS", style={'color': '#cc44ff', 'fontSize': '14px', 'letterSpacing': '2px', 'marginBottom': '12px'}),
            html.P("AI-analyzed, sorted by effort. Free first.", style={'color': '#555', 'fontSize': '10px', 'marginBottom': '12px'}),
            html.Div(id='box-airdrops'),
        ]),

        # ── BOX 3: INVEST NOW ──
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'borderColor': '#88cc00', 'borderWidth': '2px'}, children=[
            html.H3("📈 INVEST NOW", style={'color': '#88cc00', 'fontSize': '14px', 'letterSpacing': '2px', 'marginBottom': '12px'}),
            html.P("Momentum + macro alignment + buzz", style={'color': '#555', 'fontSize': '10px', 'marginBottom': '12px'}),
            html.Div(id='box-invest'),
        ]),
    ]),

    # ── ALERTS BAR ──
    html.Div(id='alerts-bar', style={**CARD, 'marginBottom': '20px', 'borderColor': '#ff8c00',
        'padding': '12px 20px'}),

    # ══════════════════════════════════════════════════════════
    # DETAIL SECTIONS (collapsible)
    # ══════════════════════════════════════════════════════════
    html.Div(style={'display': 'flex', 'gap': '8px', 'marginBottom': '15px', 'flexWrap': 'wrap', 'justifyContent': 'center'}, children=[
        html.Button(label, id=f'tab-{tab}', n_clicks=0,
            style={'padding': '6px 14px', 'backgroundColor': 'transparent', 'border': '1px solid #2a2a4a',
                   'borderRadius': '4px', 'color': '#888', 'cursor': 'pointer', 'fontSize': '11px',
                   'fontFamily': 'inherit'})
        for tab, label in [
            ('buzz', '🔥 Coin Buzz'), ('gems', '💎 Gems'), ('narratives', '📡 Narratives'),
            ('listings', '🏦 Listings'), ('whales', '🐋 Whales'), ('news', '📰 News'),
            ('macro', '🏦 Macro'), ('reddit', '💬 Reddit'), ('x', '🐦 X/Twitter'),
        ]
    ]),

    html.Div(id='detail-panel', style={**CARD, 'marginBottom': '20px', 'display': 'none'}),

    # Footer
    html.Div(style={'textAlign': 'center', 'color': '#333', 'fontSize': '10px', 'letterSpacing': '2px'}, children=[
        html.P("ALPHASCOPE v2.2 — AMENTINHO"),
    ]),
])

# ============================================================
# MAIN CALLBACK — Three boxes + alerts
# ============================================================
@app.callback(
    [Output('pulse-bar', 'children'), Output('macro-bar', 'children'),
     Output('box-alpha', 'children'), Output('box-airdrops', 'children'),
     Output('box-invest', 'children'), Output('alerts-bar', 'children'),
     Output('updated', 'children')],
    [Input('refresh', 'n_intervals')]
)
def update_main(_):
    fg = load_fear_greed()
    fg_val = int(fg.iloc[0]['value']) if not fg.empty else 50
    fg_label = fg.iloc[0]['label'] if not fg.empty else "N/A"
    fg_color = '#ff4444' if fg_val < 25 else '#ff8c00' if fg_val < 45 else '#ffdd00' if fg_val < 55 else '#88cc00' if fg_val < 75 else '#00cc44'

    # ── PULSE BAR ──
    pulse = [
        html.Span(f"F&G: {fg_val}", style={'color': fg_color, 'fontSize': '13px', 'fontWeight': 'bold',
            'padding': '4px 10px', 'border': f'1px solid {fg_color}', 'borderRadius': '4px'}),
    ]
    prices = load_prices()
    for _, r in prices.head(5).iterrows():
        c = r['change_24h'] or 0
        col = '#00cc44' if c >= 0 else '#ff4444'
        pulse.append(html.Span(f"{r['symbol']} ${r['price_usd']:,.0f} {c:+.1f}%",
            style={'color': col, 'fontSize': '11px', 'padding': '4px 8px', 'border': f'1px solid #2a2a4a', 'borderRadius': '4px'}))

    # ── MACRO BAR ──
    macro_text = load_macro_summary()

    # ── BOX 1: ALPHA PICKS ──
    buzz = load_coin_buzz()
    gems = load_hidden_gems()
    price_map = {r['symbol']: dict(r) for _, r in prices.iterrows()} if not prices.empty else {}
    
    alpha_items = []
    seen_alpha = set()
    
    # Hidden gems first (highest alpha potential)
    for _, r in gems.iterrows():
        sym = r['symbol']
        if sym in seen_alpha: continue
        seen_alpha.add(sym)
        p = price_map.get(sym, {})
        price_str = f"${p.get('price_usd', 0):,.2f}" if p.get('price_usd') else ""
        change = p.get('change_24h', 0) or 0
        change_col = '#00cc44' if change >= 0 else '#ff4444'
        
        alpha_items.append(html.Div(style={'padding': '8px 0', 'borderBottom': '1px solid #1a1a3e'}, children=[
            html.Div(style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'}, children=[
                html.Span(f"💎 {sym}", style={'color': '#ff6b6b', 'fontWeight': 'bold', 'fontSize': '14px'}),
                html.Span(price_str, style={'color': '#fff', 'fontSize': '13px'}) if price_str else html.Span(""),
            ]),
            html.P(r['signal_detail'][:80], style={'color': '#666', 'fontSize': '10px', 'margin': '2px 0 0'}),
        ]))
        if len(alpha_items) >= 5: break
    
    # Then buzzing non-major coins
    majors = {'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE'}
    for _, r in buzz.iterrows():
        coin = r['coin']
        if coin in seen_alpha or coin in majors: continue
        seen_alpha.add(coin)
        p = price_map.get(coin, {})
        price_str = f"${p.get('price_usd', 0):,.2f}" if p.get('price_usd') else ""
        sent = r['avg_sentiment'] or 0
        sent_emoji = '🟢' if sent > 0.1 else '🔴' if sent < -0.1 else '🟡'
        
        alpha_items.append(html.Div(style={'padding': '8px 0', 'borderBottom': '1px solid #1a1a3e'}, children=[
            html.Div(style={'display': 'flex', 'justifyContent': 'space-between'}, children=[
                html.Span(f"{sent_emoji} {coin}", style={'color': '#00d4ff', 'fontWeight': 'bold', 'fontSize': '14px'}),
                html.Span(f"{r['mention_count']} mentions", style={'color': '#888', 'fontSize': '11px'}),
            ]),
            html.P(f"Buzz across {r['sources'][:30]}" if r.get('sources') else "", style={'color': '#666', 'fontSize': '10px', 'margin': '2px 0 0'}),
        ]))
        if len(alpha_items) >= 8: break
    
    if not alpha_items:
        alpha_items = [html.P("Scanning for alpha...", style={'color': '#555', 'fontSize': '12px'})]

    # ── BOX 2: AIRDROPS ──
    airdrops = load_airdrops()
    airdrop_items = []
    for _, r in airdrops.iterrows():
        score = r.get('legitimacy_score', 5) or 5
        legit = '✅' if score >= 7 else '⚠️' if score >= 4 else '🚫'
        
        airdrop_items.append(html.Div(style={'padding': '8px 0', 'borderBottom': '1px solid #1a1a3e'}, children=[
            html.Div(style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'}, children=[
                html.Span(f"{legit} {r['project_name']}", style={'color': '#cc44ff', 'fontWeight': 'bold', 'fontSize': '13px'}),
                effort_badge(r.get('effort_level', '')),
            ]),
            html.Div(style={'display': 'flex', 'gap': '10px', 'fontSize': '10px', 'color': '#666', 'marginTop': '3px'}, children=[
                html.Span(f"💰{r.get('cost_estimate', '?')}"),
                html.Span(f"⏱{r.get('time_required', '?')}"),
                html.Span(f"🎁{r.get('reward_estimate', '?')}"),
                html.Span(f"{score}/10", style={'color': '#ffdd00'}),
            ]),
            html.Details(style={'marginTop': '4px'}, children=[
                html.Summary("Steps →", style={'color': '#888', 'fontSize': '10px', 'cursor': 'pointer'}),
                html.P(r.get('qualification_steps', 'Check project website')[:200],
                       style={'color': '#aaa', 'fontSize': '11px', 'marginTop': '4px', 'lineHeight': '1.4'}),
            ]),
        ]))
        if len(airdrop_items) >= 5: break
    
    if not airdrop_items:
        airdrop_items = [html.P("No airdrops detected yet", style={'color': '#555', 'fontSize': '12px'})]

    # ── BOX 3: INVEST NOW ──
    invest_items = []
    sentiment = load_sentiment()
    sent_map = {}
    for _, r in sentiment.iterrows():
        coin = r.get('coin', '')
        if coin:
            sent_map[coin] = r

    for _, r in prices.iterrows():
        if r['name'] is None: continue
        sym = r['symbol'] or ''
        c24 = r['change_24h'] or 0
        c7 = r['change_7d'] or 0
        mcap = r['market_cap'] or 0
        mcap_s = f"${mcap/1e9:.0f}B" if mcap >= 1e9 else f"${mcap/1e6:.0f}M" if mcap >= 1e6 else ""
        
        # Sentiment from X
        s = sent_map.get(sym, {})
        x_sent = ''
        if isinstance(s, dict) and s.get('sentiment_label'):
            x_sent = s['sentiment_label']
        elif hasattr(s, 'get') and s.get('sentiment_label'):
            x_sent = s['sentiment_label']

        # Buzz count
        b = buzz[buzz['coin'] == sym] if not buzz.empty else pd.DataFrame()
        buzz_n = int(b.iloc[0]['mention_count']) if not b.empty else 0

        c24_col = '#00cc44' if c24 >= 0 else '#ff4444'
        
        invest_items.append(html.Div(style={'padding': '8px 0', 'borderBottom': '1px solid #1a1a3e'}, children=[
            html.Div(style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'}, children=[
                html.Span(f"{sym}", style={'color': '#88cc00', 'fontWeight': 'bold', 'fontSize': '14px'}),
                html.Span(f"${r['price_usd']:,.2f}", style={'color': '#fff', 'fontSize': '13px'}),
            ]),
            html.Div(style={'display': 'flex', 'gap': '10px', 'fontSize': '11px', 'marginTop': '2px'}, children=[
                html.Span(f"24h: {c24:+.1f}%", style={'color': c24_col}),
                html.Span(f"7d: {c7:+.1f}%", style={'color': '#00cc44' if c7 >= 0 else '#ff4444'}),
                html.Span(mcap_s, style={'color': '#666'}),
                html.Span(f"🔥{buzz_n}", style={'color': '#ff6b6b'}) if buzz_n else html.Span(""),
                html.Span(x_sent, style={'color': '#1da1f2', 'fontSize': '10px'}) if x_sent else html.Span(""),
            ]),
        ]))
    
    if not invest_items:
        invest_items = [html.P("Fetching prices...", style={'color': '#555', 'fontSize': '12px'})]

    # ── ALERTS BAR ──
    alerts = []
    
    # Whale alerts
    whales = load_signals('WHALE', 3)
    for _, r in whales.head(2).iterrows():
        alerts.append(html.Span(f"🐋 {clean_html(r['title'][:80])}", style={'fontSize': '11px', 'color': '#44ffcc'}))
        alerts.append(html.Br())
    
    # Exchange listings
    listings = load_exchange_listings()
    for _, r in listings.head(2).iterrows():
        alerts.append(html.Span(f"🏦 {r['exchange']}: {clean_html(r['title'][:60])}", style={'fontSize': '11px', 'color': '#ffdd00'}))
        alerts.append(html.Br())
    
    # Macro events
    events = load_macro_events()
    high_impact = events[events['impact'] == 'HIGH'] if not events.empty else pd.DataFrame()
    for _, r in high_impact.head(1).iterrows():
        alerts.append(html.Span(f"🔴 MACRO: {r['event_name']} — {r['crypto_impact'][:60]}", style={'fontSize': '11px', 'color': '#ff8c00'}))
    
    # Geo risk
    geo = events[events['category'] == 'GEOPOLITICAL'] if not events.empty else pd.DataFrame()
    for _, r in geo.head(1).iterrows():
        alerts.append(html.Span(f"⚠️ {clean_html(r['event_name'][:80])}", style={'fontSize': '11px', 'color': '#ff4444'}))
    
    if not alerts:
        alerts = [html.Span("No critical alerts", style={'color': '#555', 'fontSize': '11px'})]

    return (pulse, macro_text or "Loading macro data...",
            alpha_items, airdrop_items, invest_items, alerts,
            f"Updated: {datetime.now().strftime('%H:%M')}")

# ============================================================
# DETAIL PANEL CALLBACK
# ============================================================
@app.callback(
    [Output('detail-panel', 'children'), Output('detail-panel', 'style')],
    [Input(f'tab-{t}', 'n_clicks') for t in ['buzz','gems','narratives','listings','whales','news','macro','reddit','x']],
    prevent_initial_call=True
)
def show_detail(*clicks):
    ctx = callback_context
    if not ctx.triggered:
        return [], {**CARD, 'display': 'none'}
    
    tab = ctx.triggered[0]['prop_id'].split('.')[0].replace('tab-', '')
    style = {**CARD, 'display': 'block', 'marginBottom': '20px'}
    
    if tab == 'buzz':
        buzz = load_coin_buzz()
        items = [html.H3("🔥 Coin Buzz Rankings", style={'color': '#ff6b6b', 'marginBottom': '10px'})]
        for _, r in buzz.iterrows():
            sent = r['avg_sentiment'] or 0
            col = '#00cc44' if sent > 0.1 else '#ff4444' if sent < -0.1 else '#ffdd00'
            items.append(html.Div(style={'display': 'flex', 'justifyContent': 'space-between', 'padding': '6px 0', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(r['coin'], style={'color': '#fff', 'fontWeight': 'bold'}),
                html.Span(f"{r['mention_count']} mentions", style={'color': '#888'}),
                html.Span(f"sent: {sent:+.2f}", style={'color': col}),
            ]))
        return items, style
    
    elif tab == 'gems':
        gems = load_hidden_gems()
        items = [html.H3("💎 Hidden Gems", style={'color': '#ff6b6b', 'marginBottom': '10px'})]
        for _, r in gems.iterrows():
            items.append(html.Div(style={'padding': '8px 0', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(f"{r['name']} ({r['symbol']})", style={'color': '#ff6b6b', 'fontWeight': 'bold'}),
                html.Span(f" #{int(r['market_cap_rank'])}" if pd.notna(r['market_cap_rank']) else "", style={'color': '#ffdd00', 'fontSize': '12px'}),
                html.P(r['signal_detail'], style={'color': '#888', 'fontSize': '12px', 'margin': '2px 0 0'}),
            ]))
        return items, style
    
    elif tab == 'narratives':
        narr = load_narratives()
        items = [html.H3("📡 Narrative Radar", style={'color': '#ff8c00', 'marginBottom': '10px'})]
        colors = {'Bitcoin':'#f7931a','Ethereum':'#627eea','AI':'#00d4ff','DeFi':'#88cc00','L2':'#ff6b6b','Memecoins':'#ffdd00','Regulation':'#ff4444','Solana':'#9945ff'}
        for _, r in narr.iterrows():
            col = colors.get(r['narrative'], '#888')
            items.append(html.Div(style={'display': 'flex', 'justifyContent': 'space-between', 'padding': '6px 0', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(r['narrative'], style={'color': col, 'fontWeight': 'bold'}),
                html.Span(f"{r['mention_count']} mentions", style={'color': '#888'}),
            ]))
        return items, style
    
    elif tab == 'listings':
        listings = load_exchange_listings()
        items = [html.H3("🏦 Exchange Listings", style={'color': '#ffdd00', 'marginBottom': '10px'})]
        for _, r in listings.iterrows():
            tier = r.get('exchange_tier', 4)
            emoji = ['', '🥇', '🥈', '🥉', '4️⃣'][min(tier, 4)]
            items.append(html.Div(style={'padding': '6px 0', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(f"{emoji} {r['exchange']}", style={'color': '#ffdd00', 'fontWeight': 'bold', 'fontSize': '12px'}),
                html.Span(f" {r.get('coin', '')}", style={'color': '#00d4ff', 'fontSize': '12px'}),
                html.P(clean_html(r['title'][:120]), style={'color': '#ccc', 'fontSize': '12px', 'margin': '2px 0 0'}),
            ]))
        return items, style
    
    elif tab == 'whales':
        whales = load_signals('WHALE', 10)
        items = [html.H3("🐋 Whale Movements", style={'color': '#44ffcc', 'marginBottom': '10px'})]
        for _, r in whales.iterrows():
            items.append(html.P(f"📡 {r['source_detail']}: {clean_html(r['title'][:150])}",
                style={'color': '#ccc', 'fontSize': '12px', 'borderBottom': '1px solid #2a2a4a', 'padding': '6px 0'}))
        return items, style
    
    elif tab == 'news':
        news = load_signals('NEWS', 15)
        items = [html.H3("📰 News Feed", style={'color': '#ff8c00', 'marginBottom': '10px'})]
        seen = set()
        for _, r in news.iterrows():
            title = clean_html(r['title'][:120])
            if title[:30] in seen: continue
            seen.add(title[:30])
            items.append(html.Div(style={'padding': '6px 0', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(f"{r['source_detail']}", style={'color': '#888', 'fontSize': '10px'}),
                html.P(title, style={'color': '#ccc', 'fontSize': '12px', 'margin': '2px 0 0'}),
                html.A("→", href=r.get('url', ''), target='_blank', style={'color': '#00d4ff', 'fontSize': '10px', 'textDecoration': 'none'}) if r.get('url') and str(r['url']).startswith('http') else html.Span(""),
            ]))
            if len(items) > 12: break
        return items, style
    
    elif tab == 'macro':
        indicators = load_macro_indicators()
        events = load_macro_events()
        items = [html.H3("🏦 Macro & Geopolitical", style={'color': '#ffdd00', 'marginBottom': '10px'})]
        items.append(html.H4("Indicators", style={'color': '#888', 'fontSize': '12px', 'marginTop': '10px'}))
        for _, r in indicators.iterrows():
            items.append(html.Div(style={'display': 'flex', 'justifyContent': 'space-between', 'padding': '4px 0', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(r['indicator'], style={'color': '#ccc', 'fontSize': '12px'}),
                html.Span(f"{r['value']:.2f}", style={'color': '#fff', 'fontWeight': 'bold', 'fontSize': '13px'}),
            ]))
        items.append(html.H4("Events", style={'color': '#888', 'fontSize': '12px', 'marginTop': '15px'}))
        for _, r in events.iterrows():
            emoji = {'HIGH': '🔴', 'MEDIUM': '🟡', 'LOW': '🟢'}.get(r.get('impact', ''), '⚪')
            items.append(html.Div(style={'padding': '6px 0', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(f"{emoji} {r['event_name']}", style={'color': '#ccc', 'fontSize': '12px', 'fontWeight': 'bold'}),
                html.P(r.get('crypto_impact', '')[:100], style={'color': '#666', 'fontSize': '11px', 'margin': '2px 0 0'}),
            ]))
        return items, style
    
    elif tab == 'reddit':
        reddit = load_signals('NEWS', 15)
        reddit = reddit[reddit['source'] == 'reddit'] if not reddit.empty else pd.DataFrame()
        alphas = load_signals('ALPHA', 10)
        items = [html.H3("💬 Reddit Signals", style={'color': '#ff8c00', 'marginBottom': '10px'})]
        for _, r in pd.concat([alphas, reddit]).head(12).iterrows():
            if r['source'] != 'reddit': continue
            items.append(html.Div(style={'padding': '6px 0', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(f"{r['source_detail']} ⬆{r['engagement']}", style={'color': '#888', 'fontSize': '10px'}),
                html.P(clean_html(r['title'][:120]), style={'color': '#ccc', 'fontSize': '12px', 'margin': '2px 0 0'}),
            ]))
        return items, style
    
    elif tab == 'x':
        sent = load_sentiment()
        items = [html.H3("🐦 X/Twitter Sentiment", style={'color': '#1da1f2', 'marginBottom': '10px'})]
        if sent.empty:
            items.append(html.P("Enable: ENABLE_TWITTER_FETCH=true in .env", style={'color': '#666'}))
        for _, r in sent.iterrows():
            score = r['sentiment_score'] or 0
            col = '#00cc44' if score > 0.1 else '#ff4444' if score < -0.1 else '#ffdd00'
            items.append(html.Div(style={'display': 'flex', 'justifyContent': 'space-between', 'padding': '6px 0', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(r['source_detail'], style={'color': col, 'fontWeight': 'bold'}),
                html.Span(f"{r['sentiment_label']} ({score:+.2f})", style={'color': col}),
            ]))
        return items, style
    
    return [], {**CARD, 'display': 'none'}

# ── AI BRIEF ──
@app.callback([Output('ai-text', 'children'), Output('ai-box', 'style')],
    [Input('ai-btn', 'n_clicks')], prevent_initial_call=True)
def brief(n):
    if n > 0: return generate_ai_brief(), {**CARD, 'display': 'block', 'marginBottom': '20px', 'borderColor': '#00d4ff'}
    return "", {**CARD, 'display': 'none'}

if __name__ == '__main__':
    print("\n  ALPHASCOPE v2.2")
    print("  http://localhost:8050\n")
    app.run(debug=True, port=8050)
