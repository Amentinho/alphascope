"""
AlphaScope v2.1 — Alpha Intelligence Dashboard
Three dynamic watchlists: Alpha Radar, Airdrop Tracker, Investment Radar
All data auto-populated from unified signals table.
"""

import sqlite3
import pandas as pd
from dash import Dash, html, dcc
from dash.dependencies import Input, Output, State
import plotly.graph_objects as go
from datetime import datetime
import requests

app = Dash(__name__)
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

def load_trending():
    conn = get_db()
    df = pd.read_sql_query("SELECT name, symbol, market_cap_rank FROM trending ORDER BY fetched_at DESC LIMIT 10", conn)
    conn.close()
    return df

def load_narratives():
    conn = get_db()
    df = pd.read_sql_query("SELECT narrative, mention_count FROM narratives ORDER BY fetched_at DESC, mention_count DESC LIMIT 10", conn)
    conn.close()
    return df

def load_coin_buzz():
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT coin, mention_count, total_engagement, avg_sentiment, sources FROM coin_buzz ORDER BY fetched_at DESC, mention_count DESC LIMIT 20", conn)
    conn.close()
    return df

def load_investment_radar():
    conn = get_db()
    df = pd.read_sql_query(
        """SELECT coin_id, name, symbol, price_usd, change_24h, change_7d, change_30d,
                  market_cap, volume_24h, sentiment_up, sentiment_down
           FROM token_data WHERE fetched_at >= (SELECT datetime(MAX(fetched_at), '-5 minutes') FROM token_data)
           AND name IS NOT NULL ORDER BY market_cap DESC""", conn)
    conn.close()
    return df

def load_hidden_gems():
    conn = get_db()
    df = pd.read_sql_query("SELECT name, symbol, market_cap_rank, signal_type, signal_detail FROM hidden_gems ORDER BY fetched_at DESC LIMIT 10", conn)
    conn.close()
    return df

def load_signals(signal_type, limit=15):
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT source, source_detail, title, content, coin, sentiment_score, sentiment_label, engagement, url, fetched_at FROM signals WHERE signal_type=? ORDER BY fetched_at DESC, engagement DESC LIMIT ?",
        conn, params=(signal_type, limit))
    conn.close()
    return df

def load_exchange_listings():
    try:
        conn = get_db()
        df = pd.read_sql_query(
            "SELECT exchange, exchange_tier, coin, title, url, fetched_at FROM exchange_listings ORDER BY fetched_at DESC LIMIT 15", conn)
        conn.close()
        return df
    except:
        return pd.DataFrame()

def load_airdrop_projects():
    try:
        conn = get_db()
        df = pd.read_sql_query(
            """SELECT project_name, category, qualification_steps, effort_level, cost_estimate,
                      time_required, reward_estimate, deadline, legitimacy_score, legitimacy_reasons, status
               FROM airdrop_projects
               WHERE status IN ('AI_SUGGESTED', 'USER_APPROVED', 'ACTIVE')
               ORDER BY
                   CASE effort_level
                       WHEN 'FREE_EASY' THEN 1 WHEN 'LOW_COST' THEN 2
                       WHEN 'MEDIUM_COST' THEN 3 WHEN 'HIGH_COST' THEN 4
                       WHEN 'INVITE_ONLY' THEN 5 ELSE 6 END,
                   legitimacy_score DESC""", conn)
        conn.close()
        return df
    except:
        return pd.DataFrame()

def load_sentiment_signals():
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT source, source_detail, coin, sentiment_score, sentiment_label, engagement FROM signals WHERE signal_type='SENTIMENT' ORDER BY fetched_at DESC LIMIT 10", conn)
    conn.close()
    return df

# ============================================================
# CHARTS
# ============================================================
def create_fg_gauge(value, label):
    color = "#ff4444"
    for t, c in [(25, "#ff8c00"), (45, "#ffdd00"), (55, "#88cc00"), (75, "#00cc44")]:
        if value >= t: color = c
    fig = go.Figure(go.Indicator(mode="gauge+number", value=value,
        title={'text': f"Fear & Greed: {label}", 'font': {'size': 18, 'color': '#fff'}},
        number={'font': {'size': 44, 'color': '#fff'}},
        gauge={'axis': {'range': [0, 100], 'tickcolor': '#666'}, 'bar': {'color': color}, 'bgcolor': '#1a1a2e',
               'steps': [{'range': [0,25], 'color': '#3d0000'}, {'range': [25,45], 'color': '#3d2600'},
                         {'range': [45,55], 'color': '#3d3d00'}, {'range': [55,75], 'color': '#1a3d00'},
                         {'range': [75,100], 'color': '#003d1a'}]}))
    fig.update_layout(paper_bgcolor='#0f0f23', plot_bgcolor='#0f0f23', height=250, margin=dict(t=50,b=10,l=20,r=20))
    return fig

def create_fg_chart(df):
    if df.empty: return go.Figure()
    d = df.sort_values('timestamp')
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d['timestamp'], y=d['value'], mode='lines+markers',
        line=dict(color='#00d4ff', width=2), marker=dict(size=4),
        fill='tozeroy', fillcolor='rgba(0,212,255,0.1)'))
    fig.add_hrect(y0=0, y1=25, fillcolor="rgba(255,68,68,0.1)", line_width=0)
    fig.add_hrect(y0=75, y1=100, fillcolor="rgba(0,204,68,0.1)", line_width=0)
    fig.add_hline(y=25, line_dash="dash", line_color="#ff4444", opacity=0.3)
    fig.add_hline(y=75, line_dash="dash", line_color="#00cc44", opacity=0.3)
    fig.update_layout(paper_bgcolor='#0f0f23', plot_bgcolor='#1a1a2e', height=200,
        margin=dict(t=10,b=30,l=40,r=10), xaxis=dict(showgrid=False, color='#666', tickformat='%b %d'),
        yaxis=dict(showgrid=True, gridcolor='#2a2a4a', color='#666', range=[0,100]), showlegend=False)
    return fig

def create_narratives_chart(df):
    if df.empty: return go.Figure()
    colors = {'Bitcoin':'#f7931a','Ethereum':'#627eea','AI':'#00d4ff','DeFi':'#88cc00',
              'L2':'#ff6b6b','RWA':'#ff8c00','Memecoins':'#ffdd00','Regulation':'#ff4444',
              'Gaming':'#cc44ff','DePIN':'#44ffcc','Solana':'#9945ff'}
    fig = go.Figure(go.Bar(x=df['mention_count'], y=df['narrative'], orientation='h',
        marker_color=[colors.get(n,'#888') for n in df['narrative']],
        text=df['mention_count'], textposition='outside', textfont=dict(color='#fff', size=12)))
    fig.update_layout(paper_bgcolor='#0f0f23', plot_bgcolor='#1a1a2e', height=280,
        margin=dict(t=10,b=10,l=80,r=40), xaxis=dict(showgrid=False, showticklabels=False),
        yaxis=dict(color='#fff', autorange='reversed'), bargap=0.3)
    return fig

def create_buzz_chart(df):
    if df.empty: return go.Figure()
    df_top = df.head(10)
    colors = ['#00d4ff' if s >= 0 else '#ff4444' for s in df_top['avg_sentiment']]
    fig = go.Figure(go.Bar(x=df_top['mention_count'], y=df_top['coin'], orientation='h',
        marker_color=colors, text=df_top['mention_count'], textposition='outside',
        textfont=dict(color='#fff', size=12)))
    fig.update_layout(paper_bgcolor='#0f0f23', plot_bgcolor='#1a1a2e', height=300,
        margin=dict(t=10,b=10,l=60,r=40), xaxis=dict(showgrid=False, showticklabels=False),
        yaxis=dict(color='#fff', autorange='reversed'), bargap=0.3)
    return fig

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
    radar = load_investment_radar()
    narratives = load_narratives()
    gems = load_hidden_gems()
    buzz = load_coin_buzz()
    sentiment = load_sentiment_signals()
    alphas = load_signals('ALPHA', 5)
    whales = load_signals('WHALE', 3)
    listings = load_exchange_listings()
    airdrops = load_airdrop_projects()

    prompt = f"""You are AlphaScope, a crypto alpha intelligence analyst. Write a market brief (350 words max):

MARKET: {f"Fear & Greed: {fg.iloc[0]['value']}/100 ({fg.iloc[0]['label']})" if not fg.empty else "N/A"}

TOP BUZZING COINS (by social mentions):
{chr(10).join(f"- {r['coin']}: {r['mention_count']} mentions, sentiment {r['avg_sentiment']:+.2f}" for _,r in buzz.head(8).iterrows()) if not buzz.empty else "N/A"}

PRICE DATA:
{chr(10).join(f"- {r['name']}: ${r['price_usd']:,.2f} ({r['change_24h']:+.1f}% 24h, {r['change_7d']:+.1f}% 7d)" for _,r in radar.iterrows()) if not radar.empty else "N/A"}

NARRATIVES: {", ".join(f"{r['narrative']}({r['mention_count']})" for _,r in narratives.iterrows()) if not narratives.empty else "N/A"}

HIDDEN GEMS: {chr(10).join(f"- {r['name']}({r['symbol']}) — {r['signal_detail']}" for _,r in gems.head(5).iterrows()) if not gems.empty else "None"}

EXCHANGE LISTINGS: {chr(10).join(f"- {r['exchange']}: {r['title'][:80]}" for _,r in listings.head(3).iterrows()) if not listings.empty else "None"}

WHALE MOVES: {chr(10).join(f"- {r['title'][:100]}" for _,r in whales.head(3).iterrows()) if not whales.empty else "N/A"}

AIRDROPS ({len(airdrops)} tracked): {chr(10).join(f"- {r['project_name']} ({r['effort_level']}, {r['legitimacy_score']}/10)" for _,r in airdrops.head(3).iterrows()) if not airdrops.empty else "None"}

Rules:
1. Start with the #1 most actionable signal right now
2. For hidden gems, explain WHY — what narrative, what catalyst
3. Flag any price/sentiment divergences (price up but sentiment down = warning)
4. For airdrops, only mention FREE_EASY or LOW_COST ones
5. Rate each insight: HIGH/MEDIUM/LOW confidence
6. End with "ACTION ITEMS:" — 3 specific things to do today
7. Be direct, no hype, data-driven"""

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
def source_icon(source):
    return {'twitter': '🐦', 'reddit': '💬', 'telegram': '📡', 'news': '📰', 'exchange': '🏦', 'defi': '🔗'}.get(source, '📌')

def clean_html(text):
    if not text: return ''
    return text.replace('&#036;', '$').replace('&quot;', '"').replace('&#39;', "'").replace('&amp;', '&')

def effort_badge(level):
    badges = {
        'FREE_EASY': ('🟢', '#00cc44', 'Free'),
        'LOW_COST': ('🟡', '#ffdd00', '$5-20'),
        'MEDIUM_COST': ('🟠', '#ff8c00', '$100+'),
        'HIGH_COST': ('🔴', '#ff4444', '$1K+'),
        'INVITE_ONLY': ('🔒', '#888', 'Invite'),
    }
    emoji, color, label = badges.get(level, ('❓', '#888', level))
    return html.Span(f"{emoji} {label}", style={'color': color, 'fontSize': '11px', 'fontWeight': 'bold',
        'padding': '2px 8px', 'borderRadius': '4px', 'backgroundColor': f'{color}22'})

# ============================================================
# LAYOUT
# ============================================================
CARD = {'backgroundColor': '#1a1a2e', 'borderRadius': '12px', 'padding': '20px',
        'border': '1px solid #2a2a4a', 'marginBottom': '20px'}
ACCENT_CARD = lambda color: {**CARD, 'borderColor': color, 'borderWidth': '2px'}

app.layout = html.Div(style={
    'backgroundColor': '#0f0f23', 'minHeight': '100vh', 'padding': '20px',
    'fontFamily': '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
    'color': '#fff', 'maxWidth': '1400px', 'margin': '0 auto'
}, children=[

    # ── HEADER ──
    html.Div(style={'textAlign': 'center', 'marginBottom': '30px'}, children=[
        html.H1("🔍 AlphaScope", style={'fontSize': '36px', 'marginBottom': '5px', 'color': '#00d4ff'}),
        html.P("Crypto Alpha Intelligence — Reddit · Telegram · News · Exchanges · DeFi · X", style={'color': '#888'}),
        html.P(id='updated', style={'color': '#555', 'fontSize': '12px'}),
        html.Button("🤖 Generate AI Alpha Brief", id='ai-btn', n_clicks=0,
            style={'marginTop': '10px', 'padding': '10px 24px', 'backgroundColor': '#00d4ff',
                   'border': 'none', 'borderRadius': '8px', 'color': '#0f0f23',
                   'fontWeight': 'bold', 'cursor': 'pointer', 'fontSize': '14px'}),
    ]),
    dcc.Interval(id='refresh', interval=1800*1000, n_intervals=0),

    # ── AI BRIEF ──
    html.Div(id='ai-box', style={**CARD, 'display': 'none'}, children=[
        html.H3("🤖 AI Alpha Brief", style={'color': '#00d4ff', 'marginBottom': '10px'}),
        dcc.Markdown(id='ai-text', style={'color': '#ccc', 'lineHeight': '1.6', 'fontSize': '14px'}),
    ]),

    # ── ROW 1: Fear & Greed + Narratives ──
    html.Div(style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap', 'marginBottom': '20px'}, children=[
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0'}, children=[
            dcc.Graph(id='fg-gauge', config={'displayModeBar': False}),
            html.H4("30-Day Trend", style={'color': '#888', 'fontSize': '12px', 'textAlign': 'center'}),
            dcc.Graph(id='fg-chart', config={'displayModeBar': False}),
        ]),
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0'}, children=[
            html.H3("📡 Narrative Radar", style={'color': '#ff8c00', 'marginBottom': '5px'}),
            html.P("What crypto is talking about across all sources", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            dcc.Graph(id='narratives', config={'displayModeBar': False}),
        ]),
    ]),

    # ── ROW 2: Coin Buzz + Hidden Gems ──
    html.Div(style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap', 'marginBottom': '20px'}, children=[
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0'}, children=[
            html.H3("🔥 Coin Buzz", style={'color': '#ff6b6b', 'marginBottom': '5px'}),
            html.P("Most mentioned coins across Reddit + Telegram + News", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            dcc.Graph(id='buzz-chart', config={'displayModeBar': False}),
        ]),
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0', 'borderColor': '#ff6b6b'}, children=[
            html.H3("💎 Hidden Gems", style={'color': '#ff6b6b', 'marginBottom': '5px'}),
            html.P("Cross-source validated: trending + buzz + outside top 50", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='gems'),
        ]),
    ]),

    # ══════════════════════════════════════════════════════════
    # THREE DYNAMIC WATCHLISTS
    # ══════════════════════════════════════════════════════════

    html.H2("Dynamic Watchlists", style={'color': '#00d4ff', 'textAlign': 'center',
        'marginTop': '30px', 'marginBottom': '20px', 'fontSize': '24px'}),

    # ── WATCHLIST 1: ALPHA RADAR ──
    html.Div(style=ACCENT_CARD('#00d4ff'), children=[
        html.H3("🎯 Alpha Radar", style={'color': '#00d4ff', 'marginBottom': '5px'}),
        html.P("Coins with rising buzz, positive sentiment, and catalysts. Auto-populated from all sources.", 
               style={'color': '#666', 'fontSize': '12px', 'marginBottom': '15px'}),
        html.Div(style={'display': 'flex', 'padding': '8px 12px', 'borderBottom': '2px solid #2a2a4a',
            'fontSize': '11px', 'color': '#666', 'textTransform': 'uppercase', 'letterSpacing': '1px'}, children=[
            html.Span("Token", style={'flex': '2'}), html.Span("Price", style={'flex': '1', 'textAlign': 'right'}),
            html.Span("24h", style={'flex': '1', 'textAlign': 'right'}), html.Span("7d", style={'flex': '1', 'textAlign': 'right'}),
            html.Span("MCap", style={'flex': '1', 'textAlign': 'right'}), html.Span("Buzz", style={'flex': '1', 'textAlign': 'right'}),
            html.Span("Mood", style={'flex': '1', 'textAlign': 'right'}),
        ]),
        html.Div(id='alpha-radar'),
    ]),

    # ── WATCHLIST 2: AIRDROP TRACKER ──
    html.Div(style=ACCENT_CARD('#cc44ff'), children=[
        html.H3("🪂 Airdrop Tracker", style={'color': '#cc44ff', 'marginBottom': '5px'}),
        html.P("AI-analyzed airdrops sorted by effort level. Free/Easy first. Review and approve to track.",
               style={'color': '#666', 'fontSize': '12px', 'marginBottom': '15px'}),
        html.Div(id='airdrop-tracker'),
    ]),

    # ── WATCHLIST 3: INVESTMENT RADAR ──
    html.Div(style=ACCENT_CARD('#88cc00'), children=[
        html.H3("📈 Investment Radar", style={'color': '#88cc00', 'marginBottom': '5px'}),
        html.P("Momentum shifts detected: price movement + sentiment + narrative alignment.",
               style={'color': '#666', 'fontSize': '12px', 'marginBottom': '15px'}),
        html.Div(style={'display': 'flex', 'padding': '8px 12px', 'borderBottom': '2px solid #2a2a4a',
            'fontSize': '11px', 'color': '#666', 'textTransform': 'uppercase', 'letterSpacing': '1px'}, children=[
            html.Span("Token", style={'flex': '2'}), html.Span("Price", style={'flex': '1', 'textAlign': 'right'}),
            html.Span("24h", style={'flex': '1', 'textAlign': 'right'}), html.Span("7d", style={'flex': '1', 'textAlign': 'right'}),
            html.Span("MCap", style={'flex': '1', 'textAlign': 'right'}), html.Span("CoinGecko Mood", style={'flex': '1', 'textAlign': 'right'}),
        ]),
        html.Div(id='investment-radar'),
    ]),

    # ══════════════════════════════════════════════════════════
    # SIGNAL FEEDS
    # ══════════════════════════════════════════════════════════

    html.H2("Signal Feeds", style={'color': '#888', 'textAlign': 'center',
        'marginTop': '30px', 'marginBottom': '20px', 'fontSize': '20px'}),

    # ── Exchange Listings + Whale Alerts ──
    html.Div(style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap', 'marginBottom': '20px'}, children=[
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0', 'borderColor': '#ffdd00'}, children=[
            html.H3("🏦 Exchange Listings", style={'color': '#ffdd00', 'marginBottom': '5px'}),
            html.P("New listings from 14 exchanges — Tier 2 = highest alpha", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='listings'),
        ]),
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0', 'borderColor': '#44ffcc'}, children=[
            html.H3("🐋 Whale Movements", style={'color': '#44ffcc', 'marginBottom': '5px'}),
            html.P("Large transactions from Telegram whale alerts", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='whales'),
        ]),
    ]),

    # ── Alpha Signals + News ──
    html.Div(style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap', 'marginBottom': '20px'}, children=[
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0'}, children=[
            html.H3("🔍 Alpha Signals", style={'color': '#00d4ff', 'marginBottom': '5px'}),
            html.P("High-engagement signals from all sources", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='alphas'),
        ]),
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0'}, children=[
            html.H3("📰 News Feed", style={'color': '#ff8c00', 'marginBottom': '5px'}),
            html.P("Multilingual crypto news — EN/CN/JP/RU/ES/BR", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='news'),
        ]),
    ]),

    # ── Trending + X Sentiment ──
    html.Div(style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap', 'marginBottom': '20px'}, children=[
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '280px', 'marginBottom': '0'}, children=[
            html.H3("🔥 CoinGecko Trending", style={'color': '#ff6b6b', 'marginBottom': '5px'}),
            html.Div(id='trending'),
        ]),
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '280px', 'marginBottom': '0', 'borderColor': '#1da1f2'}, children=[
            html.H3("🐦 X/Twitter Sentiment", style={'color': '#1da1f2', 'marginBottom': '5px'}),
            html.P("Enable in .env: ENABLE_TWITTER_FETCH=true", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='x-sentiment'),
        ]),
    ]),

    # Footer
    html.Div(style={'textAlign': 'center', 'padding': '20px', 'color': '#444', 'fontSize': '12px'}, children=[
        html.P("AlphaScope v2.1 — Built by Amentinho"),
        html.P("Sources: Reddit · Telegram · CoinGecko · DeFi Llama · 14 Exchanges · 10 News Sources · X/Twitter",
               style={'fontSize': '10px'}),
    ]),
])

# ============================================================
# MAIN CALLBACK
# ============================================================
@app.callback(
    [Output('fg-gauge', 'figure'), Output('fg-chart', 'figure'),
     Output('narratives', 'figure'), Output('buzz-chart', 'figure'),
     Output('gems', 'children'),
     Output('alpha-radar', 'children'), Output('airdrop-tracker', 'children'),
     Output('investment-radar', 'children'),
     Output('listings', 'children'), Output('whales', 'children'),
     Output('alphas', 'children'), Output('news', 'children'),
     Output('trending', 'children'), Output('x-sentiment', 'children'),
     Output('updated', 'children')],
    [Input('refresh', 'n_intervals')]
)
def update(_):
    fg = load_fear_greed()
    fg_val = int(fg.iloc[0]['value']) if not fg.empty else 50
    fg_label = fg.iloc[0]['label'] if not fg.empty else "N/A"

    # ── HIDDEN GEMS ──
    gems = load_hidden_gems()
    gem_items = []
    for _, r in gems.iterrows():
        gem_items.append(html.Div(style={'padding': '10px 12px', 'borderBottom': '1px solid #2a2a4a'}, children=[
            html.Span(f"💎 {r['name']} ", style={'fontWeight': 'bold', 'color': '#ff6b6b'}),
            html.Span(f"({r['symbol']})", style={'color': '#888'}),
            html.Span(f"  #{int(r['market_cap_rank'])}" if pd.notna(r['market_cap_rank']) else "", 
                      style={'color': '#ffdd00', 'fontSize': '12px', 'marginLeft': '8px'}),
            html.P(r['signal_detail'], style={'color': '#999', 'fontSize': '12px', 'marginTop': '4px', 'marginBottom': '0'}),
        ]))
    if not gem_items: gem_items = [html.P("No hidden gems detected", style={'color': '#666'})]

    # ── ALPHA RADAR (dynamic watchlist 1) ──
    radar = load_investment_radar()
    buzz = load_coin_buzz()
    buzz_map = {r['coin']: dict(r) for _, r in buzz.iterrows()} if not buzz.empty else {}
    
    alpha_rows = []
    for _, r in radar.iterrows():
        if r['name'] is None: continue
        c24 = r['change_24h'] or 0
        c7 = r['change_7d'] or 0
        mcap = r['market_cap'] or 0
        mcap_s = f"${mcap/1e9:.1f}B" if mcap >= 1e9 else f"${mcap/1e6:.0f}M" if mcap >= 1e6 else "N/A"
        sym = r['symbol'] or ''
        b = buzz_map.get(sym, {})
        buzz_count = int(b['mention_count']) if isinstance(b, dict) and 'mention_count' in b else 0
        mood = ""
        if pd.notna(r.get('sentiment_up')) and r['sentiment_up'] > 0:
            e = "🟢" if r['sentiment_up'] >= 60 else "🟡" if r['sentiment_up'] >= 40 else "🔴"
            mood = f"{e} {r['sentiment_up']:.0f}%"
        alpha_rows.append(html.Div(style={'display': 'flex', 'alignItems': 'center', 'padding': '10px 12px',
            'borderBottom': '1px solid #2a2a4a'}, children=[
            html.Div(style={'flex': '2'}, children=[html.Span(f"{r['name']} ", style={'fontWeight': 'bold'}),
                html.Span(f"({sym})", style={'color': '#888', 'fontSize': '12px'})]),
            html.Span(f"${r['price_usd']:,.2f}", style={'flex': '1', 'textAlign': 'right'}),
            html.Span(f"{c24:+.1f}%", style={'flex': '1', 'textAlign': 'right', 'color': '#00cc44' if c24>=0 else '#ff4444', 'fontWeight': 'bold'}),
            html.Span(f"{c7:+.1f}%", style={'flex': '1', 'textAlign': 'right', 'color': '#00cc44' if c7>=0 else '#ff4444'}),
            html.Span(mcap_s, style={'flex': '1', 'textAlign': 'right', 'color': '#888'}),
            html.Span(f"🔥{buzz_count}" if buzz_count else "", style={'flex': '1', 'textAlign': 'right', 'color': '#ff6b6b'}),
            html.Span(mood, style={'flex': '1', 'textAlign': 'right'}),
        ]))
    if not alpha_rows: alpha_rows = [html.P("Run fetcher to populate", style={'color': '#666'})]

    # ── AIRDROP TRACKER (dynamic watchlist 2) ──
    airdrops = load_airdrop_projects()
    airdrop_items = []
    for _, r in airdrops.iterrows():
        score = r['legitimacy_score'] or 5
        legit_emoji = '✅' if score >= 7 else '⚠️' if score >= 4 else '🚫'
        status_color = {'AI_SUGGESTED': '#888', 'USER_APPROVED': '#00d4ff', 'ACTIVE': '#00cc44'}.get(r['status'], '#888')
        
        airdrop_items.append(html.Div(style={'padding': '12px', 'borderBottom': '1px solid #2a2a4a'}, children=[
            html.Div(style={'display': 'flex', 'alignItems': 'center', 'gap': '10px', 'marginBottom': '6px'}, children=[
                html.Span(f"{legit_emoji} {r['project_name']}", style={'fontWeight': 'bold', 'color': '#cc44ff', 'fontSize': '15px'}),
                effort_badge(r.get('effort_level', '')),
                html.Span(f"{score}/10", style={'color': '#ffdd00', 'fontSize': '12px'}),
                html.Span(f"[{r['status']}]", style={'color': status_color, 'fontSize': '11px'}),
            ]),
            html.Div(style={'display': 'flex', 'gap': '15px', 'fontSize': '12px', 'color': '#888', 'marginBottom': '4px'}, children=[
                html.Span(f"💰 {r.get('cost_estimate', '?')}"),
                html.Span(f"⏱ {r.get('time_required', '?')}"),
                html.Span(f"🎁 {r.get('reward_estimate', '?')}"),
                html.Span(f"📅 {r.get('deadline', '?')}"),
            ]),
            html.P(r.get('qualification_steps', 'No steps yet')[:200],
                   style={'color': '#aaa', 'fontSize': '12px', 'marginTop': '4px', 'marginBottom': '2px', 'lineHeight': '1.4'}),
            html.P(r.get('legitimacy_reasons', '')[:120],
                   style={'color': '#666', 'fontSize': '11px', 'fontStyle': 'italic', 'marginBottom': '0'}),
        ]))
    if not airdrop_items:
        airdrop_items = [html.P("No airdrops detected yet. Run fetcher + airdrop_intel.py", style={'color': '#666'})]

    # ── INVESTMENT RADAR (dynamic watchlist 3) ──
    inv_rows = []
    for _, r in radar.iterrows():
        if r['name'] is None: continue
        c24 = r['change_24h'] or 0
        c7 = r['change_7d'] or 0
        mcap = r['market_cap'] or 0
        mcap_s = f"${mcap/1e9:.1f}B" if mcap >= 1e9 else f"${mcap/1e6:.0f}M" if mcap >= 1e6 else "N/A"
        mood = ""
        if pd.notna(r.get('sentiment_up')) and r['sentiment_up'] > 0:
            e = "🟢" if r['sentiment_up'] >= 60 else "🟡" if r['sentiment_up'] >= 40 else "🔴"
            mood = f"{e} {r['sentiment_up']:.0f}%"
        inv_rows.append(html.Div(style={'display': 'flex', 'alignItems': 'center', 'padding': '10px 12px',
            'borderBottom': '1px solid #2a2a4a'}, children=[
            html.Div(style={'flex': '2'}, children=[html.Span(f"{r['name']} ", style={'fontWeight': 'bold'}),
                html.Span(f"({r['symbol']})", style={'color': '#888', 'fontSize': '12px'})]),
            html.Span(f"${r['price_usd']:,.2f}", style={'flex': '1', 'textAlign': 'right'}),
            html.Span(f"{c24:+.1f}%", style={'flex': '1', 'textAlign': 'right', 'color': '#00cc44' if c24>=0 else '#ff4444', 'fontWeight': 'bold'}),
            html.Span(f"{c7:+.1f}%", style={'flex': '1', 'textAlign': 'right', 'color': '#00cc44' if c7>=0 else '#ff4444'}),
            html.Span(mcap_s, style={'flex': '1', 'textAlign': 'right', 'color': '#888'}),
            html.Span(mood, style={'flex': '1', 'textAlign': 'right'}),
        ]))
    if not inv_rows: inv_rows = [html.P("Run fetcher to populate", style={'color': '#666'})]

    # ── EXCHANGE LISTINGS ──
    listings = load_exchange_listings()
    listing_items = []
    for _, r in listings.iterrows():
        tier = r.get('exchange_tier', 4) if 'exchange_tier' in r else 4
        tier_label = ['', '🥇', '🥈', '🥉', '4️⃣'][min(tier, 4)]
        listing_items.append(html.Div(style={'padding': '8px 12px', 'borderBottom': '1px solid #2a2a4a'}, children=[
            html.Span(f"{tier_label} {r['exchange']}", style={'color': '#ffdd00', 'fontWeight': 'bold', 'fontSize': '12px'}),
            html.Span(f"  {r.get('coin', '')}", style={'color': '#00d4ff', 'fontSize': '12px', 'marginLeft': '8px'}),
            html.P(clean_html(r['title'][:150]), style={'color': '#ccc', 'fontSize': '13px', 'marginTop': '4px', 'marginBottom': '0'}),
        ]))
    if not listing_items: listing_items = [html.P("No listings detected. Add exchange_feeds.py", style={'color': '#666'})]

    # ── WHALES ──
    whales = load_signals('WHALE', 8)
    whale_items = [
        html.Div(style={'padding': '8px 12px', 'borderBottom': '1px solid #2a2a4a'}, children=[
            html.Span(f"📡 {r['source_detail']}", style={'color': '#44ffcc', 'fontSize': '11px'}),
            html.P(clean_html(r['title'][:200]), style={'color': '#ccc', 'fontSize': '13px', 'marginTop': '4px', 'marginBottom': '0'}),
        ]) for _, r in whales.iterrows()
    ] if not whales.empty else [html.P("No whale movements", style={'color': '#666'})]

    # ── ALPHA SIGNALS ──
    alphas = load_signals('ALPHA', 10)
    alpha_items = []
    seen = set()
    for _, r in alphas.iterrows():
        content = clean_html((r.get('content') or r.get('title') or '')[:150])
        if content[:40] in seen: continue
        seen.add(content[:40])
        eng = r['engagement'] or 0
        alpha_items.append(html.Div(style={'padding': '8px 12px', 'borderBottom': '1px solid #2a2a4a'}, children=[
            html.Span(f"{source_icon(r['source'])} {r['source_detail']}", style={'color': '#888', 'fontSize': '11px'}),
            html.Span(f"  ⚡{eng//1000}K" if eng >= 1000 else f"  ⚡{eng}" if eng > 0 else "", style={'color': '#ffdd00', 'fontSize': '11px'}),
            html.P(content, style={'color': '#ccc', 'fontSize': '13px', 'marginTop': '4px', 'marginBottom': '0'}),
        ]))
        if len(alpha_items) >= 8: break
    if not alpha_items: alpha_items = [html.P("No alpha signals", style={'color': '#666'})]

    # ── NEWS ──
    news = load_signals('NEWS', 12)
    news_items = []
    seen_n = set()
    for _, r in news.iterrows():
        title = clean_html((r.get('title') or '')[:150])
        if title[:40] in seen_n: continue
        seen_n.add(title[:40])
        news_items.append(html.Div(style={'padding': '8px 12px', 'borderBottom': '1px solid #2a2a4a'}, children=[
            html.Span(f"{source_icon(r['source'])} {r['source_detail']}", style={'color': '#888', 'fontSize': '11px'}),
            html.P(title, style={'color': '#ccc', 'fontSize': '13px', 'marginTop': '4px', 'marginBottom': '0'}),
            html.A("→ link", href=r['url'], target='_blank', style={'color': '#00d4ff', 'fontSize': '11px', 'textDecoration': 'none'}) if r.get('url') and str(r['url']).startswith('http') else html.Span(""),
        ]))
        if len(news_items) >= 8: break
    if not news_items: news_items = [html.P("No news yet", style={'color': '#666'})]

    # ── TRENDING ──
    trending = load_trending()
    trending_items = [
        html.Div(style={'display': 'flex', 'justifyContent': 'space-between', 'padding': '8px 12px',
            'borderBottom': '1px solid #2a2a4a', 'fontSize': '14px'}, children=[
            html.Span(f"{i+1}. {r['name']} ({r['symbol']})", style={'color': '#ddd'}),
            html.Span(f"#{int(r['market_cap_rank'])}" if pd.notna(r['market_cap_rank']) else "New", style={'color': '#888'}),
        ]) for i, r in trending.iterrows()
    ]

    # ── X SENTIMENT ──
    sentiment = load_sentiment_signals()
    x_items = []
    for _, r in sentiment.iterrows():
        score = r['sentiment_score'] or 0
        emoji = "🟢" if score > 0.1 else "🔴" if score < -0.1 else "🟡"
        color = '#00cc44' if score > 0.1 else '#ff4444' if score < -0.1 else '#ffdd00'
        x_items.append(html.Div(style={'display': 'flex', 'alignItems': 'center', 'padding': '8px 12px',
            'borderBottom': '1px solid #2a2a4a'}, children=[
            html.Span(f"{emoji} {r['source_detail']}", style={'fontWeight': 'bold', 'color': color, 'width': '80px'}),
            html.Span(f"{r['sentiment_label']}", style={'color': color, 'width': '90px', 'fontSize': '13px'}),
            html.Span(f"({score:+.2f})", style={'color': '#888', 'fontSize': '12px'}),
        ]))
    if not x_items: x_items = [html.P("Enable: ENABLE_TWITTER_FETCH=true in .env", style={'color': '#666'})]

    return (create_fg_gauge(fg_val, fg_label), create_fg_chart(fg),
            create_narratives_chart(load_narratives()), create_buzz_chart(load_coin_buzz()),
            gem_items, alpha_rows, airdrop_items, inv_rows,
            listing_items, whale_items, alpha_items, news_items,
            trending_items, x_items,
            f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

# ── AI BRIEF CALLBACK ──
@app.callback([Output('ai-text', 'children'), Output('ai-box', 'style')],
    [Input('ai-btn', 'n_clicks')], prevent_initial_call=True)
def brief(n):
    if n > 0: return generate_ai_brief(), {**CARD, 'display': 'block', 'borderColor': '#00d4ff'}
    return "", {**CARD, 'display': 'none'}

if __name__ == '__main__':
    print("\n  AlphaScope v2.1 — Alpha Intelligence Dashboard")
    print("  http://localhost:8050\n")
    app.run(debug=True, port=8050)
