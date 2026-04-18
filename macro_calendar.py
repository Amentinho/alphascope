"""
AlphaScope — Macroeconomic & Geopolitical Intelligence
Tracks: Fed, CPI, jobs, gold, oil, S&P, DXY, geopolitical events
All free APIs, no keys needed.
"""

import requests
import sqlite3
import re
from datetime import datetime, timedelta

def init_macro_table():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS macro_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_name TEXT, category TEXT, date TEXT,
        actual TEXT, forecast TEXT, previous TEXT,
        impact TEXT, crypto_impact TEXT, source TEXT, fetched_at TEXT,
        UNIQUE(event_name, date))''')
    c.execute('''CREATE TABLE IF NOT EXISTS macro_indicators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        indicator TEXT, value REAL, change_pct REAL,
        date TEXT, source TEXT, fetched_at TEXT,
        UNIQUE(indicator, date))''')
    conn.commit()
    conn.close()


def fetch_fred_data():
    """Key economic indicators from FRED (Federal Reserve)."""
    now = datetime.now().isoformat()
    indicators = {
        'DGS10': '10Y Treasury Yield',
        'DGS2': '2Y Treasury Yield',
        'T10Y2Y': '10Y-2Y Spread',
        'VIXCLS': 'VIX Volatility',
    }
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    for series_id, name in indicators.items():
        try:
            url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")}'
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                lines = res.text.strip().split('\n')
                if len(lines) >= 2:
                    parts = lines[-1].split(',')
                    if len(parts) >= 2 and parts[1] != '.':
                        try:
                            value = float(parts[1])
                            # Get previous for change calc
                            prev = float(lines[-2].split(',')[1]) if len(lines) >= 3 and lines[-2].split(',')[1] != '.' else value
                            change = ((value - prev) / prev * 100) if prev != 0 else 0
                            c.execute('''INSERT OR REPLACE INTO macro_indicators
                                         (indicator, value, change_pct, date, source, fetched_at)
                                         VALUES (?,?,?,?,?,?)''',
                                (name, value, round(change, 2), parts[0], 'FRED', now))
                            print(f"    {name}: {value:.2f} ({change:+.1f}%)")
                        except ValueError:
                            pass
        except:
            pass
    conn.commit()
    conn.close()


def fetch_commodities():
    """Gold, Oil, S&P 500 — all correlate with crypto."""
    print("  Fetching commodities...")
    now = datetime.now().isoformat()
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()

    # Gold via FRED (London PM Fix)
    try:
        res = requests.get(
            f'https://fred.stlouisfed.org/graph/fredgraph.csv?id=GOLDPMGBD228NLBM&cosd={(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")}',
            timeout=10)
        if res.status_code == 200:
            lines = [l for l in res.text.strip().split('\n')[1:] if '.' in l.split(',')[1] if len(l.split(',')) > 1]
            if lines:
                val = float(lines[-1].split(',')[1])
                prev = float(lines[-2].split(',')[1]) if len(lines) >= 2 else val
                change = ((val - prev) / prev * 100) if prev else 0
                c.execute('INSERT OR REPLACE INTO macro_indicators (indicator, value, change_pct, date, source, fetched_at) VALUES (?,?,?,?,?,?)',
                    ('Gold (XAU/USD)', val, round(change, 2), lines[-1].split(',')[0], 'FRED', now))
                print(f"    Gold: ${val:,.0f} ({change:+.1f}%)")
    except Exception as e:
        print(f"    Gold failed: {e}")

    # Oil via FRED (WTI Crude)
    try:
        res = requests.get(
            f'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILWTICO&cosd={(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")}',
            timeout=10)
        if res.status_code == 200:
            lines = [l for l in res.text.strip().split('\n')[1:] if len(l.split(',')) > 1 and l.split(',')[1] != '.']
            if lines:
                val = float(lines[-1].split(',')[1])
                prev = float(lines[-2].split(',')[1]) if len(lines) >= 2 else val
                change = ((val - prev) / prev * 100) if prev else 0
                c.execute('INSERT OR REPLACE INTO macro_indicators (indicator, value, change_pct, date, source, fetched_at) VALUES (?,?,?,?,?,?)',
                    ('Oil (WTI Crude)', val, round(change, 2), lines[-1].split(',')[0], 'FRED', now))
                print(f"    Oil: ${val:,.2f} ({change:+.1f}%)")
    except Exception as e:
        print(f"    Oil failed: {e}")

    # S&P 500 via FRED
    try:
        res = requests.get(
            f'https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500&cosd={(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")}',
            timeout=10)
        if res.status_code == 200:
            lines = [l for l in res.text.strip().split('\n')[1:] if len(l.split(',')) > 1 and l.split(',')[1] != '.']
            if lines:
                val = float(lines[-1].split(',')[1])
                prev = float(lines[-2].split(',')[1]) if len(lines) >= 2 else val
                change = ((val - prev) / prev * 100) if prev else 0
                c.execute('INSERT OR REPLACE INTO macro_indicators (indicator, value, change_pct, date, source, fetched_at) VALUES (?,?,?,?,?,?)',
                    ('S&P 500', val, round(change, 2), lines[-1].split(',')[0], 'FRED', now))
                print(f"    S&P 500: {val:,.0f} ({change:+.1f}%)")
    except Exception as e:
        print(f"    S&P 500 failed: {e}")

    conn.commit()
    conn.close()


def fetch_currency():
    """USD strength — inverse correlation with crypto."""
    now = datetime.now().isoformat()
    try:
        res = requests.get('https://api.exchangerate-api.com/v4/latest/USD', timeout=10)
        if res.status_code == 200:
            rates = res.json()['rates']
            conn = sqlite3.connect('alphascope.db', timeout=30)
            c = conn.cursor()
            eur_rate = 1 / rates.get('EUR', 1)
            jpy_rate = rates.get('JPY', 0)
            c.execute('INSERT OR REPLACE INTO macro_indicators (indicator, value, change_pct, date, source, fetched_at) VALUES (?,?,?,?,?,?)',
                ('USD/EUR', round(eur_rate, 4), 0, datetime.now().strftime('%Y-%m-%d'), 'ExchangeRate', now))
            c.execute('INSERT OR REPLACE INTO macro_indicators (indicator, value, change_pct, date, source, fetched_at) VALUES (?,?,?,?,?,?)',
                ('USD/JPY', round(jpy_rate, 2), 0, datetime.now().strftime('%Y-%m-%d'), 'ExchangeRate', now))
            conn.commit()
            conn.close()
            print(f"    USD/EUR: {eur_rate:.4f} | USD/JPY: {jpy_rate:.2f}")
    except Exception as e:
        print(f"    Currency failed: {e}")


def fetch_geopolitical_risk():
    """Scan news signals for geopolitical events that impact crypto."""
    print("  Scanning geopolitical risk...")
    now = datetime.now().isoformat()

    # Keywords that signal geopolitical risk affecting markets
    risk_keywords = {
        'WAR': ['war ', 'military strike', 'invasion', 'missile', 'troops deployed', 'armed conflict'],
        'SANCTIONS': ['sanctions', 'embargo', 'trade ban', 'blacklist', 'asset freeze'],
        'TRADE_WAR': ['tariff', 'trade war', 'import ban', 'export control', 'trade restriction'],
        'POLITICAL': ['impeach', 'coup', 'election crisis', 'government shutdown', 'political instability'],
        'BANKING': ['bank run', 'bank failure', 'banking crisis', 'liquidity crisis', 'credit crunch'],
        'ENERGY': ['oil crisis', 'energy crisis', 'opec cut', 'pipeline', 'gas shortage'],
    }

    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()

    # Scan existing news signals for geo risk keywords
    c.execute("SELECT title, content, source_detail FROM signals WHERE source='news' AND fetched_at >= datetime('now', '-24 hours')")
    news = c.fetchall()

    # Also scan telegram
    c.execute("SELECT title, content, source_detail FROM signals WHERE source='telegram' AND fetched_at >= datetime('now', '-24 hours')")
    telegram = c.fetchall()

    all_signals = news + telegram
    risk_detected = {}

    for title, content, source in all_signals:
        text = (title + ' ' + (content or '')).lower()
        for risk_type, keywords in risk_keywords.items():
            for kw in keywords:
                if kw in text:
                    if risk_type not in risk_detected:
                        risk_detected[risk_type] = []
                    risk_detected[risk_type].append({
                        'headline': title[:150],
                        'source': source,
                    })
                    break

    # Store as macro events
    for risk_type, events in risk_detected.items():
        crypto_impact = {
            'WAR': 'Military conflict = risk-off initially, then BTC as safe haven if prolonged',
            'SANCTIONS': 'Sanctions = crypto adoption in sanctioned countries, mixed for prices',
            'TRADE_WAR': 'Tariffs = market uncertainty = bearish short-term, BTC hedge narrative long-term',
            'POLITICAL': 'Political instability = risk-off = bearish, but can drive crypto adoption',
            'BANKING': 'Banking crisis = extremely bullish BTC (2023 SVB proved this)',
            'ENERGY': 'Energy crisis = higher mining costs = bearish miners, neutral for BTC price',
        }.get(risk_type, 'Monitor closely')

        try:
            c.execute('''INSERT OR REPLACE INTO macro_events
                         (event_name, category, date, actual, forecast, previous,
                          impact, crypto_impact, source, fetched_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (f'GEO RISK: {risk_type} ({len(events)} signals)',
                 'GEOPOLITICAL', datetime.now().strftime('%Y-%m-%d'),
                 events[0]['headline'][:100], '', '',
                 'HIGH', crypto_impact, events[0]['source'], now))
        except:
            pass

    conn.commit()
    conn.close()

    if risk_detected:
        for rtype, events in risk_detected.items():
            print(f"    ⚠️ {rtype}: {len(events)} signals — {events[0]['headline'][:60]}...")
    else:
        print(f"    No elevated geopolitical risk detected")


def fetch_economic_calendar():
    """Store known high-impact economic events."""
    now = datetime.now().isoformat()
    events = [
        ('FOMC Rate Decision', 'FED', 'HIGH',
         'Rate hikes = bearish crypto. Rate cuts = bullish. Hold = check dot plot.'),
        ('CPI Inflation', 'INFLATION', 'HIGH',
         'Higher than expected = bearish (rate hike fear). Lower = bullish (rate cut hope).'),
        ('Non-Farm Payrolls', 'JOBS', 'HIGH',
         'Strong jobs = bearish (Fed stays tight). Weak = bullish (Fed may cut).'),
        ('PCE Price Index', 'INFLATION', 'HIGH',
         "Fed's preferred inflation gauge. Same direction as CPI but carries more weight."),
        ('GDP Growth', 'GROWTH', 'MEDIUM',
         'Strong GDP + low inflation = best scenario. Recession = bearish then bullish (QE hope).'),
        ('Jobless Claims', 'JOBS', 'LOW',
         'Weekly. Rising claims = weakening economy = potential Fed pivot = bullish long-term.'),
        ('ISM Manufacturing PMI', 'GROWTH', 'MEDIUM',
         'Above 50 = expansion. Below 50 = contraction. Contraction + inflation = worst combo.'),
    ]
    conn = sqlite3.connect('alphascope.db', timeout=30)
    c = conn.cursor()
    for event, cat, impact, note in events:
        try:
            c.execute('''INSERT OR IGNORE INTO macro_events
                         (event_name, category, date, actual, forecast, previous,
                          impact, crypto_impact, source, fetched_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (event, cat, 'Recurring', '', '', '', impact, note, 'AlphaScope', now))
        except:
            pass
    conn.commit()
    conn.close()


def fetch_macro_data():
    """Main entry — fetch all macro + geopolitical data."""
    init_macro_table()
    print("  Fetching macro & geopolitical data...")
    fetch_fred_data()
    fetch_commodities()
    fetch_currency()
    fetch_economic_calendar()
    fetch_geopolitical_risk()


def load_macro_indicators():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    import pandas as pd
    df = pd.read_sql_query("SELECT indicator, value, change_pct, date FROM macro_indicators ORDER BY fetched_at DESC", conn)
    conn.close()
    return df.drop_duplicates(subset=['indicator'], keep='first')

def load_macro_events():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    import pandas as pd
    df = pd.read_sql_query(
        "SELECT event_name, category, impact, crypto_impact, actual FROM macro_events ORDER BY CASE impact WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END", conn)
    conn.close()
    return df.drop_duplicates(subset=['event_name'], keep='first')

def load_macro_summary():
    """Generate a one-line macro summary for the dashboard header."""
    df = load_macro_indicators()
    if df.empty:
        return "Macro data not loaded"

    parts = []
    for _, r in df.iterrows():
        name = r['indicator']
        val = r['value']
        if 'VIX' in name:
            emoji = '🟢' if val < 20 else '🔴' if val > 30 else '🟡'
            parts.append(f"VIX {val:.0f}{emoji}")
        elif 'Gold' in name:
            parts.append(f"Gold ${val:,.0f}")
        elif 'Oil' in name:
            parts.append(f"Oil ${val:.0f}")
        elif 'S&P' in name:
            parts.append(f"S&P {val:,.0f}")
        elif name == '10Y Treasury Yield':
            parts.append(f"10Y {val:.2f}%")

    # Check for geopolitical risk
    events = load_macro_events()
    geo = events[events['category'] == 'GEOPOLITICAL'] if not events.empty else pd.DataFrame()
    if not geo.empty:
        parts.append(f"⚠️GEO RISK")

    return " | ".join(parts[:6])


if __name__ == '__main__':
    print("AlphaScope — Macro & Geopolitical Test")
    print("=" * 50)
    fetch_macro_data()
    print()
    print("Summary:", load_macro_summary())
    print()
    print("Indicators:")
    for _, r in load_macro_indicators().iterrows():
        print(f"  {r['indicator']}: {r['value']:.2f}")
    print()
    print("Events:")
    for _, r in load_macro_events().iterrows():
        emoji = {'HIGH': '🔴', 'MEDIUM': '🟡', 'LOW': '🟢'}.get(r['impact'], '⚪')
        print(f"  {emoji} {r['event_name']}: {r['crypto_impact'][:80]}")
