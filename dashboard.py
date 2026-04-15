"""
AlphaScope v1.0 — Unified Alpha Intelligence Dashboard
All signals from X/Twitter, Reddit, Telegram in one view.
"""

import sqlite3
import pandas as pd
from dash import Dash, html, dcc
from dash.dependencies import Input, Output
import plotly.graph_objects as go
from datetime import datetime
import requests
import re

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

def load_watchlist():
    conn = get_db()
    df = pd.read_sql_query(
        """SELECT coin_id, name, symbol, price_usd, change_24h, change_7d, change_30d,
                  market_cap, volume_24h, sentiment_up, sentiment_down
           FROM token_data WHERE fetched_at >= (SELECT datetime(MAX(fetched_at), '-5 minutes') FROM token_data)
           AND name IS NOT NULL ORDER BY market_cap DESC""", conn)
    conn.close()
    return df

def load_narratives():
    conn = get_db()
    df = pd.read_sql_query("SELECT narrative, mention_count FROM narratives ORDER BY fetched_at DESC, mention_count DESC LIMIT 10", conn)
    conn.close()
    return df

def load_hidden_gems():
    conn = get_db()
    df = pd.read_sql_query("SELECT name, symbol, market_cap_rank, signal_detail FROM hidden_gems ORDER BY fetched_at DESC LIMIT 10", conn)
    conn.close()
    return df

def load_signals(signal_type, limit=15):
    conn = get_db()
    df = pd.read_sql_query(
        f"SELECT source, source_detail, title, content, coin, sentiment_score, sentiment_label, engagement, url, fetched_at FROM signals WHERE signal_type=? ORDER BY fetched_at DESC, engagement DESC LIMIT ?",
        conn, params=(signal_type, limit))
    conn.close()
    return df

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
              'Gaming':'#cc44ff','DePIN':'#44ffcc'}
    fig = go.Figure(go.Bar(x=df['mention_count'], y=df['narrative'], orientation='h',
        marker_color=[colors.get(n,'#888') for n in df['narrative']],
        text=df['mention_count'], textposition='outside', textfont=dict(color='#fff', size=12)))
    fig.update_layout(paper_bgcolor='#0f0f23', plot_bgcolor='#1a1a2e', height=250,
        margin=dict(t=10,b=10,l=80,r=40), xaxis=dict(showgrid=False, showticklabels=False),
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
    watchlist = load_watchlist()
    narratives = load_narratives()
    gems = load_hidden_gems()
    sentiment = load_sentiment_signals()
    alphas = load_signals('ALPHA', 5)
    airdrops = load_signals('AIRDROP', 5)
    whales = load_signals('WHALE', 5)

    prompt = f"""You are AlphaScope, a crypto alpha intelligence analyst. Write a market brief (300 words max):

## MARKET MOOD
{f"Fear & Greed: {fg.iloc[0]['value']}/100 ({fg.iloc[0]['label']})" if not fg.empty else "N/A"}
Watchlist: {chr(10).join(f"- {r['name']}: ${r['price_usd']:,.2f} ({r['change_24h']:+.1f}%)" for _,r in watchlist.iterrows()) if not watchlist.empty else "N/A"}

## X/TWITTER SENTIMENT
{chr(10).join(f"- {r['source_detail']}: {r['sentiment_label']} ({r['sentiment_score']:+.2f})" for _,r in sentiment.iterrows()) if not sentiment.empty else "No X data"}

## NARRATIVES (from Reddit)
{", ".join(f"{r['narrative']}({r['mention_count']})" for _,r in narratives.iterrows()) if not narratives.empty else "N/A"}

## HIDDEN GEMS
{chr(10).join(f"- {r['name']}({r['symbol']}) Rank #{r['market_cap_rank']}" for _,r in gems.iterrows()) if not gems.empty else "None"}

## WHALE MOVEMENTS
{chr(10).join(f"- {r['title'][:100]}" for _,r in whales.head(3).iterrows()) if not whales.empty else "N/A"}

## ALPHA SIGNALS FROM X
{chr(10).join(f"- {r['title'][:80]}: {r['content'][:100]}" for _,r in alphas.head(3).iterrows()) if not alphas.empty else "N/A"}

## AIRDROPS
{chr(10).join(f"- [{r['source']}] {r['title'][:100]}" for _,r in airdrops.head(5).iterrows()) if not airdrops.empty else "None"}

Rules:
1. Start with the #1 most actionable signal
2. For hidden gems, explain WHY they might be worth watching
3. Flag suspicious airdrops vs legitimate ones
4. Rate each insight: HIGH/MEDIUM/LOW confidence
5. End with "WATCH TODAY:" — 3 specific things to monitor
6. Be direct, no hype"""

    try:
        res = requests.post('https://api.openai.com/v1/chat/completions',
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
            json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': prompt}],
                  'max_tokens': 600, 'temperature': 0.7}, timeout=30)
        return res.json()['choices'][0]['message']['content']
    except Exception as e:
        return f"AI brief failed: {e}"

# ============================================================
# HELPERS
# ============================================================
def source_icon(source):
    return {'twitter': '🐦', 'reddit': '💬', 'telegram': '📡'}.get(source, '📌')

def signal_card(row, show_source=True):
    score = row.get('sentiment_score') or 0
    eng = row.get('engagement') or 0
    eng_str = f"{eng//1000}K" if eng >= 1000 else str(eng)
    
    children = []
    if show_source:
        children.append(html.Span(f"{source_icon(row['source'])} {row['source_detail']}", 
            style={'color': '#888', 'fontSize': '11px', 'fontWeight': 'bold'}))
        if eng > 0:
            children.append(html.Span(f"  ⚡{eng_str}", style={'color': '#666', 'fontSize': '11px'}))
    
    title = row.get('title', '')
    # Clean HTML entities
    title = title.replace('&#036;', '$').replace('&quot;', '"').replace('&#39;', "'").replace('&amp;', '&')
    children.append(html.P(title[:200], style={'color': '#ccc', 'fontSize': '13px', 'marginTop': '4px', 'marginBottom': '0', 'lineHeight': '1.4'}))
    
    if row.get('url') and row['url'].startswith('http'):
        children.append(html.A("→ link", href=row['url'], target='_blank', style={'color': '#00d4ff', 'fontSize': '11px', 'textDecoration': 'none'}))
    
    return html.Div(style={'padding': '10px 12px', 'borderBottom': '1px solid #2a2a4a'}, children=children)

# ============================================================
# LAYOUT
# ============================================================
CARD = {'backgroundColor': '#1a1a2e', 'borderRadius': '12px', 'padding': '20px',
        'border': '1px solid #2a2a4a', 'marginBottom': '20px'}

app.layout = html.Div(style={
    'backgroundColor': '#0f0f23', 'minHeight': '100vh', 'padding': '20px',
    'fontFamily': '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
    'color': '#fff', 'maxWidth': '1400px', 'margin': '0 auto'
}, children=[
    # Header
    html.Div(style={'textAlign': 'center', 'marginBottom': '30px'}, children=[
        html.H1("🔍 AlphaScope", style={'fontSize': '36px', 'marginBottom': '5px', 'color': '#00d4ff'}),
        html.P("Crypto Alpha Intelligence — X · Reddit · Telegram · On-Chain", style={'color': '#888'}),
        html.P(id='updated', style={'color': '#555', 'fontSize': '12px'}),
        html.Button("🤖 Generate AI Alpha Brief", id='ai-btn', n_clicks=0,
            style={'marginTop': '10px', 'padding': '10px 24px', 'backgroundColor': '#00d4ff',
                   'border': 'none', 'borderRadius': '8px', 'color': '#0f0f23',
                   'fontWeight': 'bold', 'cursor': 'pointer', 'fontSize': '14px'}),
    ]),
    dcc.Interval(id='refresh', interval=1800*1000, n_intervals=0),

    # AI Brief
    html.Div(id='ai-box', style={**CARD, 'display': 'none'}, children=[
        html.H3("🤖 AI Alpha Brief", style={'color': '#00d4ff', 'marginBottom': '10px'}),
        dcc.Markdown(id='ai-text', style={'color': '#ccc', 'lineHeight': '1.6', 'fontSize': '14px'}),
    ]),

    # Row 1: Fear & Greed + Narratives
    html.Div(style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap', 'marginBottom': '20px'}, children=[
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0'}, children=[
            dcc.Graph(id='fg-gauge', config={'displayModeBar': False}),
            html.H4("30-Day Trend", style={'color': '#888', 'fontSize': '12px', 'textAlign': 'center'}),
            dcc.Graph(id='fg-chart', config={'displayModeBar': False}),
        ]),
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0'}, children=[
            html.H3("📡 Narrative Radar", style={'color': '#ff8c00', 'marginBottom': '5px'}),
            html.P("Trending topics across crypto Reddit", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            dcc.Graph(id='narratives', config={'displayModeBar': False}),
        ]),
    ]),

    # Row 2: X Sentiment + Hidden Gems
    html.Div(style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap', 'marginBottom': '20px'}, children=[
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0', 'borderColor': '#1da1f2'}, children=[
            html.H3("🐦 X/Twitter Sentiment", style={'color': '#1da1f2', 'marginBottom': '5px'}),
            html.P("Live cashtag sentiment from crypto Twitter", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='x-sentiment'),
        ]),
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0', 'borderColor': '#ff6b6b'}, children=[
            html.H3("💎 Hidden Gems", style={'color': '#ff6b6b', 'marginBottom': '5px'}),
            html.P("Low-cap coins appearing in trending", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='gems'),
        ]),
    ]),

    # Row 3: Trending + Watchlist
    html.Div(style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap', 'marginBottom': '20px'}, children=[
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '280px', 'marginBottom': '0'}, children=[
            html.H3("🔥 Trending", style={'color': '#ff6b6b', 'marginBottom': '5px'}),
            html.P("CoinGecko 24h most searched", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='trending'),
        ]),
        html.Div(style={**CARD, 'flex': '2', 'minWidth': '400px', 'marginBottom': '0'}, children=[
            html.H3("📊 Watchlist", style={'color': '#00d4ff', 'marginBottom': '15px'}),
            html.Div(style={'display': 'flex', 'padding': '8px 12px', 'borderBottom': '2px solid #2a2a4a',
                'fontSize': '11px', 'color': '#666', 'textTransform': 'uppercase', 'letterSpacing': '1px'}, children=[
                html.Span("Token", style={'flex': '2'}), html.Span("Price", style={'flex': '1', 'textAlign': 'right'}),
                html.Span("24h", style={'flex': '1', 'textAlign': 'right'}), html.Span("7d", style={'flex': '1', 'textAlign': 'right'}),
                html.Span("MCap", style={'flex': '1', 'textAlign': 'right'}), html.Span("Mood", style={'flex': '1', 'textAlign': 'right'}),
            ]),
            html.Div(id='watchlist'),
        ]),
    ]),

    # Row 4: Alpha Signals + Airdrops
    html.Div(style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap', 'marginBottom': '20px'}, children=[
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0', 'borderColor': '#00d4ff'}, children=[
            html.H3("🔍 Alpha Signals", style={'color': '#00d4ff', 'marginBottom': '5px'}),
            html.P("High-engagement alpha from X/Twitter", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='alphas'),
        ]),
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0', 'borderColor': '#cc44ff'}, children=[
            html.H3("🪂 Airdrops & Launches", style={'color': '#cc44ff', 'marginBottom': '5px'}),
            html.P("From X, Reddit & Telegram", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='airdrops'),
        ]),
    ]),

    # Row 5: Whale Alerts + News
    html.Div(style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap', 'marginBottom': '20px'}, children=[
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0', 'borderColor': '#44ffcc'}, children=[
            html.H3("🐋 Whale Movements", style={'color': '#44ffcc', 'marginBottom': '5px'}),
            html.P("Large transactions from Telegram whale alerts", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='whales'),
        ]),
        html.Div(style={**CARD, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0'}, children=[
            html.H3("📰 News Feed", style={'color': '#ff8c00', 'marginBottom': '5px'}),
            html.P("From Reddit & Telegram channels", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='news'),
        ]),
    ]),

    # Footer
    html.Div(style={'textAlign': 'center', 'padding': '20px', 'color': '#444', 'fontSize': '12px'}, children=[
        html.P("AlphaScope v1.0 — Built by Amentinho 🚀"),
        html.P("Sources: X/Twitter · Reddit · Telegram · CoinGecko · Alternative.me", style={'fontSize': '10px'}),
    ]),
])

# ============================================================
# MAIN CALLBACK
# ============================================================
@app.callback(
    [Output('fg-gauge', 'figure'), Output('fg-chart', 'figure'), Output('narratives', 'figure'),
     Output('x-sentiment', 'children'), Output('gems', 'children'),
     Output('trending', 'children'), Output('watchlist', 'children'),
     Output('alphas', 'children'), Output('airdrops', 'children'),
     Output('whales', 'children'), Output('news', 'children'),
     Output('updated', 'children')],
    [Input('refresh', 'n_intervals')]
)
def update(_):
    fg = load_fear_greed()
    fg_val = int(fg.iloc[0]['value']) if not fg.empty else 50
    fg_label = fg.iloc[0]['label'] if not fg.empty else "N/A"

    # X/Twitter Sentiment
    sentiment = load_sentiment_signals()
    x_items = []
    for _, r in sentiment.iterrows():
        score = r['sentiment_score'] or 0
        emoji = "🟢" if score > 0.1 else "🔴" if score < -0.1 else "🟡"
        color = '#00cc44' if score > 0.1 else '#ff4444' if score < -0.1 else '#ffdd00'
        x_items.append(
            html.Div(style={'display': 'flex', 'alignItems': 'center', 'padding': '8px 12px',
                'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(f"{emoji} {r['source_detail']}", style={'fontWeight': 'bold', 'color': color, 'width': '80px'}),
                html.Span(f"{r['sentiment_label']}", style={'color': color, 'width': '90px', 'fontSize': '13px'}),
                html.Span(f"({score:+.2f})", style={'color': '#888', 'width': '60px', 'fontSize': '12px'}),
                html.Span(f"⚡{r['engagement']}", style={'color': '#666', 'fontSize': '12px'}),
            ])
        )
    if not x_items:
        x_items = [html.P("No X sentiment data yet", style={'color': '#666'})]

    # Hidden Gems
    gems = load_hidden_gems()
    gem_items = [
        html.Div(style={'padding': '10px 12px', 'borderBottom': '1px solid #2a2a4a'}, children=[
            html.Span(f"💎 {r['name']} ", style={'fontWeight': 'bold', 'color': '#ff6b6b'}),
            html.Span(f"({r['symbol']})", style={'color': '#888'}),
            html.Span(f"  Rank #{int(r['market_cap_rank'])}", style={'color': '#ffdd00', 'fontSize': '12px', 'marginLeft': '8px'}),
            html.P(r['signal_detail'], style={'color': '#999', 'fontSize': '12px', 'marginTop': '4px', 'marginBottom': '0'}),
        ]) for _, r in gems.iterrows()
    ] if not gems.empty else [html.P("No hidden gems detected", style={'color': '#666'})]

    # Trending
    trending = load_trending()
    trending_items = [
        html.Div(style={'display': 'flex', 'justifyContent': 'space-between', 'padding': '8px 12px',
            'borderBottom': '1px solid #2a2a4a', 'fontSize': '14px'}, children=[
            html.Span(f"{i+1}. {r['name']} ({r['symbol']})", style={'color': '#ddd'}),
            html.Span(f"#{int(r['market_cap_rank'])}" if pd.notna(r['market_cap_rank']) else "New", style={'color': '#888'}),
        ]) for i, r in trending.iterrows()
    ]

    # Watchlist
    watchlist = load_watchlist()
    wl_rows = []
    for _, r in watchlist.iterrows():
        if r['name'] is None: continue
        c24, c7 = r['change_24h'] or 0, r['change_7d'] or 0
        mcap = r['market_cap'] or 0
        mcap_s = f"${mcap/1e9:.1f}B" if mcap >= 1e9 else f"${mcap/1e6:.0f}M" if mcap >= 1e6 else "N/A"
        mood = ""
        if pd.notna(r['sentiment_up']) and r['sentiment_up'] > 0:
            e = "🟢" if r['sentiment_up'] >= 60 else "🟡" if r['sentiment_up'] >= 40 else "🔴"
            mood = f"{e} {r['sentiment_up']:.0f}%"
        wl_rows.append(html.Div(style={'display': 'flex', 'alignItems': 'center', 'padding': '10px 12px',
            'borderBottom': '1px solid #2a2a4a'}, children=[
            html.Div(style={'flex': '2'}, children=[html.Span(f"{r['name']} ", style={'fontWeight': 'bold'}),
                html.Span(f"({r['symbol']})", style={'color': '#888', 'fontSize': '12px'})]),
            html.Span(f"${r['price_usd']:,.2f}", style={'flex': '1', 'textAlign': 'right'}),
            html.Span(f"{c24:+.1f}%", style={'flex': '1', 'textAlign': 'right', 'color': '#00cc44' if c24>=0 else '#ff4444', 'fontWeight': 'bold'}),
            html.Span(f"{c7:+.1f}%", style={'flex': '1', 'textAlign': 'right', 'color': '#00cc44' if c7>=0 else '#ff4444'}),
            html.Span(mcap_s, style={'flex': '1', 'textAlign': 'right', 'color': '#888'}),
            html.Span(mood, style={'flex': '1', 'textAlign': 'right'}),
        ]))

    # Alpha Signals
    alphas = load_signals('ALPHA', 10)
    alpha_items = []
    seen = set()
    for _, r in alphas.iterrows():
        content = (r.get('content') or r.get('title') or '')[:150]
        content = content.replace('&#036;', '$').replace('&quot;', '"').replace('&#39;', "'")
        if content[:50] in seen: continue
        seen.add(content[:50])
        eng = r['engagement'] or 0
        eng_str = f"⚡{eng//1000}K" if eng >= 1000 else f"⚡{eng}" if eng > 0 else ""
        alpha_items.append(
            html.Div(style={'padding': '8px 12px', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(f"{source_icon(r['source'])} {r['source_detail']}", style={'color': '#888', 'fontSize': '11px'}),
                html.Span(f"  {eng_str}", style={'color': '#ffdd00', 'fontSize': '11px'}) if eng_str else html.Span(""),
                html.P(content, style={'color': '#ccc', 'fontSize': '13px', 'marginTop': '4px', 'marginBottom': '0'}),
            ])
        )
        if len(alpha_items) >= 8: break
    if not alpha_items:
        alpha_items = [html.P("No alpha signals yet", style={'color': '#666'})]

    # Airdrops
    airdrops = load_signals('AIRDROP', 15)
    airdrop_items = []
    seen_ad = set()
    for _, r in airdrops.iterrows():
        title = (r.get('title') or '')[:120]
        title = title.replace('&#036;', '$').replace('&quot;', '"').replace('&#39;', "'")
        if title[:40] in seen_ad: continue
        seen_ad.add(title[:40])
        airdrop_items.append(
            html.Div(style={'padding': '8px 12px', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(f"{source_icon(r['source'])} {r['source_detail']}", style={'color': '#cc44ff', 'fontSize': '11px', 'fontWeight': 'bold'}),
                html.P(title, style={'color': '#ccc', 'fontSize': '13px', 'marginTop': '4px', 'marginBottom': '0'}),
            ])
        )
        if len(airdrop_items) >= 8: break
    if not airdrop_items:
        airdrop_items = [html.P("No airdrops detected", style={'color': '#666'})]

    # Whales
    whales = load_signals('WHALE', 8)
    whale_items = []
    for _, r in whales.iterrows():
        title = (r.get('title') or '')[:200]
        title = title.replace('&#036;', '$').replace('&quot;', '"').replace('&#39;', "'")
        eng = r['engagement'] or 0
        eng_str = f"👁{eng//1000}K" if eng >= 1000 else ""
        whale_items.append(
            html.Div(style={'padding': '8px 12px', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(f"📡 @{r['source_detail']}", style={'color': '#44ffcc', 'fontSize': '11px'}),
                html.Span(f"  {eng_str}", style={'color': '#666', 'fontSize': '11px'}) if eng_str else html.Span(""),
                html.P(title, style={'color': '#ccc', 'fontSize': '13px', 'marginTop': '4px', 'marginBottom': '0'}),
            ])
        )
    if not whale_items:
        whale_items = [html.P("No whale movements detected", style={'color': '#666'})]

    # News
    news = load_signals('NEWS', 10)
    news_items = []
    seen_n = set()
    for _, r in news.iterrows():
        title = (r.get('title') or '')[:150]
        title = title.replace('&#036;', '$').replace('&quot;', '"').replace('&#39;', "'")
        if title[:40] in seen_n: continue
        seen_n.add(title[:40])
        eng = r['engagement'] or 0
        news_items.append(
            html.Div(style={'padding': '8px 12px', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.Span(f"{source_icon(r['source'])} {r['source_detail']}", style={'color': '#888', 'fontSize': '11px'}),
                html.Span(f"  ⬆{eng}" if r['source'] == 'reddit' and eng > 0 else
                          f"  👁{eng//1000}K" if eng >= 1000 else "", style={'color': '#666', 'fontSize': '11px'}),
                html.P(title, style={'color': '#ccc', 'fontSize': '13px', 'marginTop': '4px', 'marginBottom': '0'}),
                html.A("→ link", href=r['url'], target='_blank', style={'color': '#00d4ff', 'fontSize': '11px', 'textDecoration': 'none'}) if r.get('url') and str(r['url']).startswith('http') else html.Span(""),
            ])
        )
        if len(news_items) >= 8: break
    if not news_items:
        news_items = [html.P("No news yet", style={'color': '#666'})]

    return (create_fg_gauge(fg_val, fg_label), create_fg_chart(fg), create_narratives_chart(load_narratives()),
            x_items, gem_items, trending_items, wl_rows, alpha_items, airdrop_items,
            whale_items, news_items, f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

@app.callback([Output('ai-text', 'children'), Output('ai-box', 'style')],
    [Input('ai-btn', 'n_clicks')], prevent_initial_call=True)
def brief(n):
    if n > 0: return generate_ai_brief(), {**CARD, 'display': 'block', 'borderColor': '#00d4ff'}
    return "", {**CARD, 'display': 'none'}

if __name__ == '__main__':
    print("\n🔍 AlphaScope v1.0 — Alpha Intelligence Dashboard")
    print("   http://localhost:8050\n")
    app.run(debug=True, port=8050)
