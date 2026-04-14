"""
AlphaScope — Dashboard
Visual dashboard for crypto sentiment and alpha signals.
Open http://localhost:8050 in your browser.
"""

import sqlite3
import pandas as pd
from dash import Dash, html, dcc, dash_table
from dash.dependencies import Input, Output
import plotly.graph_objects as go
from datetime import datetime

app = Dash(__name__)
app.title = "AlphaScope — Crypto Alpha Dashboard"

# ============================================================
# DATA LOADING
# ============================================================
def get_db():
    return sqlite3.connect('alphascope.db')

def load_fear_greed():
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT value, label, timestamp FROM fear_greed ORDER BY timestamp DESC LIMIT 7",
        conn
    )
    conn.close()
    return df

def load_trending():
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT name, symbol, market_cap_rank, score FROM trending ORDER BY fetched_at DESC LIMIT 10",
        conn
    )
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
           ORDER BY market_cap DESC""",
        conn
    )
    conn.close()
    return df

def load_reddit():
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT title, score, num_comments, url FROM reddit_posts ORDER BY fetched_at DESC, score DESC LIMIT 15",
        conn
    )
    conn.close()
    return df

# ============================================================
# FEAR & GREED GAUGE
# ============================================================
def create_fear_greed_gauge(value, label):
    if value <= 25:
        color = "#ff4444"
    elif value <= 45:
        color = "#ff8c00"
    elif value <= 55:
        color = "#ffdd00"
    elif value <= 75:
        color = "#88cc00"
    else:
        color = "#00cc44"
    
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        title={'text': f"Fear & Greed: {label}", 'font': {'size': 20, 'color': '#ffffff'}},
        number={'font': {'size': 48, 'color': '#ffffff'}},
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
    fig.update_layout(
        paper_bgcolor='#0f0f23',
        plot_bgcolor='#0f0f23',
        height=280,
        margin=dict(t=60, b=20, l=30, r=30),
    )
    return fig

# ============================================================
# LAYOUT
# ============================================================
app.layout = html.Div(style={
    'backgroundColor': '#0f0f23',
    'minHeight': '100vh',
    'padding': '20px',
    'fontFamily': 'Segoe UI, Roboto, sans-serif',
    'color': '#ffffff'
}, children=[
    
    # Header
    html.Div(style={'textAlign': 'center', 'marginBottom': '30px'}, children=[
        html.H1("🔍 AlphaScope", style={'fontSize': '36px', 'marginBottom': '5px', 'color': '#00d4ff'}),
        html.P("Crypto Sentiment Intelligence Dashboard", style={'color': '#888', 'fontSize': '16px'}),
        html.P(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", 
               style={'color': '#555', 'fontSize': '12px'}),
    ]),
    
    # Auto refresh every 30 minutes
    dcc.Interval(id='refresh', interval=1800*1000, n_intervals=0),
    
    # Top row: Fear & Greed + Trending
    html.Div(style={'display': 'flex', 'gap': '20px', 'marginBottom': '20px', 'flexWrap': 'wrap'}, children=[
        
        # Fear & Greed Gauge
        html.Div(style={
            'flex': '1', 'minWidth': '300px',
            'backgroundColor': '#1a1a2e', 'borderRadius': '12px', 'padding': '15px',
            'border': '1px solid #2a2a4a'
        }, children=[
            dcc.Graph(id='fear-greed-gauge', config={'displayModeBar': False})
        ]),
        
        # Trending Coins
        html.Div(style={
            'flex': '1', 'minWidth': '300px',
            'backgroundColor': '#1a1a2e', 'borderRadius': '12px', 'padding': '20px',
            'border': '1px solid #2a2a4a'
        }, children=[
            html.H3("🔥 Trending Coins", style={'color': '#ff6b6b', 'marginBottom': '15px'}),
            html.Div(id='trending-list')
        ]),
    ]),
    
    # Watchlist
    html.Div(style={
        'backgroundColor': '#1a1a2e', 'borderRadius': '12px', 'padding': '20px',
        'border': '1px solid #2a2a4a', 'marginBottom': '20px'
    }, children=[
        html.H3("📊 Watchlist", style={'color': '#00d4ff', 'marginBottom': '15px'}),
        html.Div(id='watchlist-table')
    ]),
    
    # Reddit
    html.Div(style={
        'backgroundColor': '#1a1a2e', 'borderRadius': '12px', 'padding': '20px',
        'border': '1px solid #2a2a4a'
    }, children=[
        html.H3("💬 Reddit r/cryptocurrency — Hot Posts", style={'color': '#ff8c00', 'marginBottom': '15px'}),
        html.Div(id='reddit-list')
    ]),
])

# ============================================================
# CALLBACKS
# ============================================================
@app.callback(
    [Output('fear-greed-gauge', 'figure'),
     Output('trending-list', 'children'),
     Output('watchlist-table', 'children'),
     Output('reddit-list', 'children')],
    [Input('refresh', 'n_intervals')]
)
def update_dashboard(_):
    # Fear & Greed
    fg = load_fear_greed()
    if not fg.empty:
        fg_fig = create_fear_greed_gauge(int(fg.iloc[0]['value']), fg.iloc[0]['label'])
    else:
        fg_fig = create_fear_greed_gauge(50, "N/A")
    
    # Trending
    trending = load_trending()
    trending_items = []
    for i, row in trending.iterrows():
        rank_text = f"#{row['market_cap_rank']}" if pd.notna(row['market_cap_rank']) else "New"
        trending_items.append(
            html.Div(style={
                'display': 'flex', 'justifyContent': 'space-between',
                'padding': '8px 12px', 'borderBottom': '1px solid #2a2a4a',
                'fontSize': '14px'
            }, children=[
                html.Span(f"{i+1}. {row['name']} ({row['symbol']})", style={'color': '#ddd'}),
                html.Span(rank_text, style={'color': '#888'}),
            ])
        )
    
    # Watchlist
    watchlist = load_watchlist()
    watchlist_rows = []
    for _, row in watchlist.iterrows():
        if row['name'] is None:
            continue
        change_24h = row['change_24h'] or 0
        change_color = '#00cc44' if change_24h >= 0 else '#ff4444'
        
        mcap = row['market_cap'] or 0
        if mcap >= 1e9:
            mcap_str = f"${mcap/1e9:.1f}B"
        elif mcap >= 1e6:
            mcap_str = f"${mcap/1e6:.0f}M"
        else:
            mcap_str = "N/A"
        
        sentiment = ""
        if pd.notna(row['sentiment_up']) and row['sentiment_up'] > 0:
            sentiment = f"👍 {row['sentiment_up']:.0f}%"
        
        watchlist_rows.append(
            html.Div(style={
                'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center',
                'padding': '12px', 'borderBottom': '1px solid #2a2a4a',
            }, children=[
                html.Div(style={'flex': '2'}, children=[
                    html.Span(f"{row['name']} ", style={'fontWeight': 'bold', 'color': '#fff'}),
                    html.Span(f"({row['symbol']})", style={'color': '#888'}),
                ]),
                html.Div(style={'flex': '1', 'textAlign': 'right'}, children=[
                    html.Span(f"${row['price_usd']:,.2f}", style={'color': '#fff', 'fontWeight': 'bold'}),
                ]),
                html.Div(style={'flex': '1', 'textAlign': 'right'}, children=[
                    html.Span(f"{change_24h:+.1f}%", style={'color': change_color, 'fontWeight': 'bold'}),
                ]),
                html.Div(style={'flex': '1', 'textAlign': 'right'}, children=[
                    html.Span(mcap_str, style={'color': '#888'}),
                ]),
                html.Div(style={'flex': '1', 'textAlign': 'right'}, children=[
                    html.Span(sentiment, style={'color': '#88cc00'}),
                ]),
            ])
        )
    
    # Reddit
    reddit = load_reddit()
    reddit_items = []
    for _, row in reddit.iterrows():
        reddit_items.append(
            html.Div(style={
                'padding': '10px 12px', 'borderBottom': '1px solid #2a2a4a',
            }, children=[
                html.A(row['title'], href=row['url'], target='_blank',
                       style={'color': '#ddd', 'textDecoration': 'none', 'fontSize': '14px'}),
                html.Span(f"  ⬆ {row['score']}  💬 {row['num_comments']}", 
                          style={'color': '#666', 'fontSize': '12px', 'marginLeft': '10px'}),
            ])
        )
    
    return fg_fig, trending_items, watchlist_rows, reddit_items

# ============================================================
# RUN
# ============================================================
if __name__ == '__main__':
    print("\n🔍 AlphaScope Dashboard starting...")
    print("   Open http://localhost:8050 in your browser\n")
    app.run(debug=True, port=8050)
