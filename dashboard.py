"""
AlphaScope — Dashboard v0.3
Crypto sentiment intelligence dashboard with narratives and charts.
"""

import sqlite3
import pandas as pd
from dash import Dash, html, dcc
from dash.dependencies import Input, Output
import plotly.graph_objects as go
from datetime import datetime
import requests
import json
import os

app = Dash(__name__)
app.title = "AlphaScope — Crypto Alpha Dashboard"

def get_db():
    return sqlite3.connect('alphascope.db')

def load_fear_greed():
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT value, label, timestamp FROM fear_greed ORDER BY timestamp DESC LIMIT 30", conn)
    conn.close()
    return df

def load_trending():
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT name, symbol, market_cap_rank FROM trending ORDER BY fetched_at DESC LIMIT 10", conn)
    conn.close()
    return df

def load_watchlist():
    conn = get_db()
    df = pd.read_sql_query(
        """SELECT coin_id, name, symbol, price_usd, change_24h, change_7d, change_30d,
                  market_cap, volume_24h, sentiment_up, sentiment_down
           FROM token_data 
           WHERE fetched_at >= (SELECT datetime(MAX(fetched_at), '-5 minutes') FROM token_data)
           AND name IS NOT NULL
           ORDER BY market_cap DESC""", conn)
    conn.close()
    return df

def load_reddit():
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT title, score, num_comments, url, subreddit FROM reddit_posts ORDER BY fetched_at DESC, score DESC LIMIT 15", conn)
    conn.close()
    return df

def load_narratives():
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT narrative, mention_count FROM narratives ORDER BY fetched_at DESC, mention_count DESC LIMIT 10", conn)
    conn.close()
    return df

# ============================================================
# CHARTS
# ============================================================
def create_fear_greed_gauge(value, label):
    colors = {0: "#ff4444", 25: "#ff8c00", 45: "#ffdd00", 55: "#88cc00", 75: "#00cc44"}
    color = "#ff4444"
    for threshold, c in sorted(colors.items()):
        if value >= threshold:
            color = c

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        title={'text': f"Fear & Greed: {label}", 'font': {'size': 18, 'color': '#ffffff'}},
        number={'font': {'size': 44, 'color': '#ffffff'}},
        gauge={
            'axis': {'range': [0, 100], 'tickcolor': '#666'},
            'bar': {'color': color},
            'bgcolor': '#1a1a2e',
            'steps': [
                {'range': [0, 25], 'color': '#3d0000'},
                {'range': [25, 45], 'color': '#3d2600'},
                {'range': [45, 55], 'color': '#3d3d00'},
                {'range': [55, 75], 'color': '#1a3d00'},
                {'range': [75, 100], 'color': '#003d1a'},
            ],
        }
    ))
    fig.update_layout(paper_bgcolor='#0f0f23', plot_bgcolor='#0f0f23',
                      height=250, margin=dict(t=50, b=10, l=20, r=20))
    return fig

def create_fear_greed_chart(df):
    if df.empty:
        return go.Figure()
    
    df_sorted = df.sort_values('timestamp')
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df_sorted['timestamp'],
        y=df_sorted['value'],
        mode='lines+markers',
        line=dict(color='#00d4ff', width=2),
        marker=dict(size=4),
        fill='tozeroy',
        fillcolor='rgba(0, 212, 255, 0.1)',
        name='Fear & Greed'
    ))
    
    # Add zones
    fig.add_hrect(y0=0, y1=25, fillcolor="rgba(255,68,68,0.1)", line_width=0)
    fig.add_hrect(y0=75, y1=100, fillcolor="rgba(0,204,68,0.1)", line_width=0)
    fig.add_hline(y=25, line_dash="dash", line_color="#ff4444", opacity=0.3)
    fig.add_hline(y=75, line_dash="dash", line_color="#00cc44", opacity=0.3)
    
    fig.update_layout(
        paper_bgcolor='#0f0f23', plot_bgcolor='#1a1a2e',
        height=200, margin=dict(t=10, b=30, l=40, r=10),
        xaxis=dict(showgrid=False, color='#666', tickformat='%b %d'),
        yaxis=dict(showgrid=True, gridcolor='#2a2a4a', color='#666', range=[0, 100]),
        showlegend=False,
    )
    return fig

def create_narratives_chart(df):
    if df.empty:
        return go.Figure()
    
    colors = {
        'Bitcoin': '#f7931a', 'Ethereum': '#627eea', 'AI': '#00d4ff',
        'DeFi': '#88cc00', 'L2': '#ff6b6b', 'RWA': '#ff8c00',
        'Memecoins': '#ffdd00', 'Regulation': '#ff4444',
        'Gaming': '#cc44ff', 'DePIN': '#44ffcc',
    }
    
    bar_colors = [colors.get(n, '#888888') for n in df['narrative']]
    
    fig = go.Figure(go.Bar(
        x=df['mention_count'],
        y=df['narrative'],
        orientation='h',
        marker_color=bar_colors,
        text=df['mention_count'],
        textposition='outside',
        textfont=dict(color='#ffffff', size=12),
    ))
    
    fig.update_layout(
        paper_bgcolor='#0f0f23', plot_bgcolor='#1a1a2e',
        height=250, margin=dict(t=10, b=10, l=80, r=40),
        xaxis=dict(showgrid=False, showticklabels=False),
        yaxis=dict(color='#ffffff', autorange='reversed'),
        bargap=0.3,
    )
    return fig

# ============================================================
# AI DAILY BRIEF
# ============================================================
def generate_ai_brief():
    """Generate an AI-powered market brief using OpenAI."""
    api_key = os.environ.get('OPENAI_API_KEY', '')
    if not api_key:
        try:
            with open('.env', 'r') as f:
                for line in f:
                    if line.startswith('OPENAI_API_KEY='):
                        api_key = line.strip().split('=', 1)[1]
        except:
            pass
    
    if not api_key:
        return "Add OPENAI_API_KEY to .env to enable AI briefs."
    
    # Gather context
    fg = load_fear_greed()
    trending = load_trending()
    watchlist = load_watchlist()
    narratives = load_narratives()
    reddit = load_reddit()
    
    fg_text = f"Fear & Greed: {fg.iloc[0]['value']}/100 ({fg.iloc[0]['label']})" if not fg.empty else "N/A"
    
    trending_text = ", ".join(f"{r['name']}({r['symbol']})" for _, r in trending.head(5).iterrows()) if not trending.empty else "N/A"
    
    watchlist_text = "\n".join(
        f"- {r['name']}: ${r['price_usd']:,.2f} ({r['change_24h']:+.1f}% 24h)" 
        for _, r in watchlist.iterrows()
    ) if not watchlist.empty else "N/A"
    
    narrative_text = ", ".join(
        f"{r['narrative']}({r['mention_count']})" 
        for _, r in narratives.iterrows()
    ) if not narratives.empty else "N/A"
    
    reddit_titles = "\n".join(f"- {r['title']}" for _, r in reddit.head(5).iterrows()) if not reddit.empty else "N/A"

    prompt = f"""You are AlphaScope, a crypto market intelligence analyst. Based on the data below, write a concise daily market brief (150 words max). Focus on:
1. Overall market mood and what it signals
2. Any narratives gaining momentum
3. One specific actionable insight or thing to watch

DATA:
{fg_text}
Trending: {trending_text}
Watchlist:
{watchlist_text}
Top narratives from Reddit: {narrative_text}
Top Reddit posts:
{reddit_titles}

Write the brief in a direct, no-hype style. Start with the most important signal."""

    try:
        res = requests.post('https://api.openai.com/v1/chat/completions',
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
            json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': prompt}],
                  'max_tokens': 300, 'temperature': 0.7},
            timeout=30
        )
        data = res.json()
        return data['choices'][0]['message']['content']
    except Exception as e:
        return f"AI brief generation failed: {e}"

# ============================================================
# LAYOUT
# ============================================================
CARD_STYLE = {
    'backgroundColor': '#1a1a2e', 'borderRadius': '12px', 'padding': '20px',
    'border': '1px solid #2a2a4a', 'marginBottom': '20px'
}

app.layout = html.Div(style={
    'backgroundColor': '#0f0f23', 'minHeight': '100vh', 'padding': '20px',
    'fontFamily': '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
    'color': '#ffffff', 'maxWidth': '1400px', 'margin': '0 auto'
}, children=[
    
    # Header
    html.Div(style={'textAlign': 'center', 'marginBottom': '30px'}, children=[
        html.H1("🔍 AlphaScope", style={'fontSize': '36px', 'marginBottom': '5px', 'color': '#00d4ff'}),
        html.P("Crypto Sentiment Intelligence Dashboard", style={'color': '#888', 'fontSize': '16px'}),
        html.P(id='last-updated', style={'color': '#555', 'fontSize': '12px'}),
        html.Button("🤖 Generate AI Brief", id='ai-brief-btn', n_clicks=0,
                     style={'marginTop': '10px', 'padding': '8px 20px', 'backgroundColor': '#00d4ff',
                            'border': 'none', 'borderRadius': '8px', 'color': '#0f0f23',
                            'fontWeight': 'bold', 'cursor': 'pointer', 'fontSize': '14px'}),
    ]),
    
    dcc.Interval(id='refresh', interval=1800*1000, n_intervals=0),
    
    # AI Brief
    html.Div(id='ai-brief-container', style={**CARD_STYLE, 'display': 'none'}, children=[
        html.H3("🤖 AI Market Brief", style={'color': '#00d4ff', 'marginBottom': '10px'}),
        html.P(id='ai-brief-text', style={'color': '#ccc', 'lineHeight': '1.6', 'fontSize': '14px'}),
    ]),
    
    # Row 1: Fear & Greed + Narratives
    html.Div(style={'display': 'flex', 'gap': '20px', 'marginBottom': '20px', 'flexWrap': 'wrap'}, children=[
        html.Div(style={**CARD_STYLE, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0'}, children=[
            dcc.Graph(id='fear-greed-gauge', config={'displayModeBar': False}),
            html.H4("30-Day Trend", style={'color': '#888', 'fontSize': '12px', 'textAlign': 'center', 'marginTop': '5px'}),
            dcc.Graph(id='fear-greed-chart', config={'displayModeBar': False}),
        ]),
        html.Div(style={**CARD_STYLE, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0'}, children=[
            html.H3("📡 Narrative Radar", style={'color': '#ff8c00', 'marginBottom': '15px'}),
            html.P("What crypto Reddit is talking about right now", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            dcc.Graph(id='narratives-chart', config={'displayModeBar': False}),
        ]),
    ]),
    
    # Row 2: Trending + Watchlist
    html.Div(style={'display': 'flex', 'gap': '20px', 'marginBottom': '20px', 'flexWrap': 'wrap'}, children=[
        html.Div(style={**CARD_STYLE, 'flex': '1', 'minWidth': '300px', 'marginBottom': '0'}, children=[
            html.H3("🔥 Trending Coins", style={'color': '#ff6b6b', 'marginBottom': '15px'}),
            html.P("Most searched on CoinGecko in last 24h", style={'color': '#666', 'fontSize': '12px', 'marginBottom': '10px'}),
            html.Div(id='trending-list'),
        ]),
        html.Div(style={**CARD_STYLE, 'flex': '2', 'minWidth': '400px', 'marginBottom': '0'}, children=[
            html.H3("📊 Watchlist", style={'color': '#00d4ff', 'marginBottom': '15px'}),
            # Column headers
            html.Div(style={
                'display': 'flex', 'justifyContent': 'space-between', 'padding': '8px 12px',
                'borderBottom': '2px solid #2a2a4a', 'fontSize': '11px', 'color': '#666',
                'textTransform': 'uppercase', 'letterSpacing': '1px'
            }, children=[
                html.Span("Token", style={'flex': '2'}),
                html.Span("Price", style={'flex': '1', 'textAlign': 'right'}),
                html.Span("24h", style={'flex': '1', 'textAlign': 'right'}),
                html.Span("7d", style={'flex': '1', 'textAlign': 'right'}),
                html.Span("MCap", style={'flex': '1', 'textAlign': 'right'}),
                html.Span("Mood", style={'flex': '1', 'textAlign': 'right'}),
            ]),
            html.Div(id='watchlist-table'),
        ]),
    ]),
    
    # Row 3: Reddit
    html.Div(style=CARD_STYLE, children=[
        html.H3("💬 Reddit Hot Posts", style={'color': '#ff8c00', 'marginBottom': '15px'}),
        html.Div(id='reddit-list'),
    ]),
])

# ============================================================
# CALLBACKS
# ============================================================
@app.callback(
    [Output('fear-greed-gauge', 'figure'),
     Output('fear-greed-chart', 'figure'),
     Output('narratives-chart', 'figure'),
     Output('trending-list', 'children'),
     Output('watchlist-table', 'children'),
     Output('reddit-list', 'children'),
     Output('last-updated', 'children')],
    [Input('refresh', 'n_intervals')]
)
def update_dashboard(_):
    # Fear & Greed
    fg = load_fear_greed()
    fg_val = int(fg.iloc[0]['value']) if not fg.empty else 50
    fg_label = fg.iloc[0]['label'] if not fg.empty else "N/A"
    fg_gauge = create_fear_greed_gauge(fg_val, fg_label)
    fg_chart = create_fear_greed_chart(fg)
    
    # Narratives
    narratives = load_narratives()
    narratives_chart = create_narratives_chart(narratives)
    
    # Trending
    trending = load_trending()
    trending_items = []
    for i, row in trending.iterrows():
        rank = f"#{int(row['market_cap_rank'])}" if pd.notna(row['market_cap_rank']) else "New"
        trending_items.append(
            html.Div(style={
                'display': 'flex', 'justifyContent': 'space-between',
                'padding': '8px 12px', 'borderBottom': '1px solid #2a2a4a', 'fontSize': '14px'
            }, children=[
                html.Span(f"{i+1}. {row['name']} ({row['symbol']})", style={'color': '#ddd'}),
                html.Span(rank, style={'color': '#888'}),
            ])
        )
    
    # Watchlist
    watchlist = load_watchlist()
    watchlist_rows = []
    for _, row in watchlist.iterrows():
        if row['name'] is None:
            continue
        
        change_24h = row['change_24h'] or 0
        change_7d = row['change_7d'] or 0
        color_24h = '#00cc44' if change_24h >= 0 else '#ff4444'
        color_7d = '#00cc44' if change_7d >= 0 else '#ff4444'
        
        mcap = row['market_cap'] or 0
        mcap_str = f"${mcap/1e9:.1f}B" if mcap >= 1e9 else f"${mcap/1e6:.0f}M" if mcap >= 1e6 else "N/A"
        
        sentiment = ""
        if pd.notna(row['sentiment_up']) and row['sentiment_up'] > 0:
            emoji = "🟢" if row['sentiment_up'] >= 60 else "🟡" if row['sentiment_up'] >= 40 else "🔴"
            sentiment = f"{emoji} {row['sentiment_up']:.0f}%"
        
        watchlist_rows.append(
            html.Div(style={
                'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center',
                'padding': '10px 12px', 'borderBottom': '1px solid #2a2a4a',
            }, children=[
                html.Div(style={'flex': '2'}, children=[
                    html.Span(f"{row['name']} ", style={'fontWeight': 'bold', 'color': '#fff'}),
                    html.Span(f"({row['symbol']})", style={'color': '#888', 'fontSize': '12px'}),
                ]),
                html.Span(f"${row['price_usd']:,.2f}", style={'flex': '1', 'textAlign': 'right', 'color': '#fff'}),
                html.Span(f"{change_24h:+.1f}%", style={'flex': '1', 'textAlign': 'right', 'color': color_24h, 'fontWeight': 'bold'}),
                html.Span(f"{change_7d:+.1f}%", style={'flex': '1', 'textAlign': 'right', 'color': color_7d}),
                html.Span(mcap_str, style={'flex': '1', 'textAlign': 'right', 'color': '#888'}),
                html.Span(sentiment, style={'flex': '1', 'textAlign': 'right'}),
            ])
        )
    
    # Reddit
    reddit = load_reddit()
    reddit_items = []
    for _, row in reddit.iterrows():
        reddit_items.append(
            html.Div(style={'padding': '8px 12px', 'borderBottom': '1px solid #2a2a4a'}, children=[
                html.A(row['title'], href=row['url'], target='_blank',
                       style={'color': '#ddd', 'textDecoration': 'none', 'fontSize': '14px'}),
                html.Span(f"  r/{row['subreddit']}  ⬆{row['score']}  💬{row['num_comments']}", 
                          style={'color': '#666', 'fontSize': '11px', 'marginLeft': '10px'}),
            ])
        )
    
    updated = f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    return fg_gauge, fg_chart, narratives_chart, trending_items, watchlist_rows, reddit_items, updated

# AI Brief callback
@app.callback(
    [Output('ai-brief-text', 'children'),
     Output('ai-brief-container', 'style')],
    [Input('ai-brief-btn', 'n_clicks')],
    prevent_initial_call=True
)
def generate_brief(n_clicks):
    if n_clicks > 0:
        brief = generate_ai_brief()
        return brief, {**CARD_STYLE, 'display': 'block', 'borderColor': '#00d4ff'}
    return "", {**CARD_STYLE, 'display': 'none'}

if __name__ == '__main__':
    print("\n🔍 AlphaScope Dashboard v0.3")
    print("   Open http://localhost:8050 in your browser\n")
    app.run(debug=True, port=8050)
