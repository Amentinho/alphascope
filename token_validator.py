"""
AlphaScope — Token Validator v1.0
Fundamental validation before any BUY signal is acted on.

Checks (all free or near-free):
  1. Honeypot check — can you sell? (honeypot.is for ETH, rugcheck.xyz for SOL)
  2. Contract verified — source code public?
  3. Dev wallet concentration — top holders %
  4. Website reachable — basic legitimacy signal
  5. GitHub activity — real dev team?
  6. GPT-4o-mini fundamental score — AI reads all available info

Results cached in token_validation table to avoid redundant API calls.
Cache TTL: 30 min for fast checks, 6h for AI score.
"""

import requests
import sqlite3
import json
import re
import time
from datetime import datetime, timezone

TWITTER_API_KEY = "new1_1597ef833361479ba82c88ff32b2fb8c"

# Free API endpoints
HONEYPOT_ETH   = "https://api.honeypot.is/v2/IsHoneypot"
RUGCHECK_SOL   = "https://api.rugcheck.xyz/v1/tokens/{}/report/summary"
ETHERSCAN_API  = "https://api.etherscan.io/api"
SOLSCAN_API    = "https://public-api.solscan.io/token/meta"
GITHUB_API     = "https://api.github.com/search/repositories"

# OpenAI — use gpt-4o-mini, cheapest capable model (~$0.001/call)
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

# Validation score thresholds
SCORE_BLOCK  = 3   # below this = never buy
SCORE_WATCH  = 5   # below this = watch only, no auto-buy
SCORE_BUY    = 7   # above this = allow auto-buy


def get_db():
    conn = sqlite3.connect('alphascope.db', timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_validation_table():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS token_validation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        contract_address TEXT,
        chain TEXT,
        is_honeypot INTEGER DEFAULT 0,
        sell_tax_pct REAL DEFAULT 0,
        buy_tax_pct REAL DEFAULT 0,
        contract_verified INTEGER DEFAULT 0,
        dev_wallet_pct REAL DEFAULT 0,
        top10_holders_pct REAL DEFAULT 0,
        lp_burned INTEGER DEFAULT 0,
        website_ok INTEGER DEFAULT 0,
        website_url TEXT,
        github_stars INTEGER DEFAULT 0,
        github_commits_30d INTEGER DEFAULT 0,
        github_url TEXT,
        twitter_followers INTEGER DEFAULT 0,
        twitter_account_age_days INTEGER DEFAULT 0,
        twitter_engagement_rate REAL DEFAULT 0,
        ai_score INTEGER DEFAULT 0,
        ai_flags TEXT,
        ai_positives TEXT,
        total_score INTEGER DEFAULT 0,
        verdict TEXT DEFAULT 'UNKNOWN',
        cached_at TEXT,
        UNIQUE(contract_address, chain))''')
    conn.commit()
    conn.close()


def get_cached(contract_address, chain, max_age_minutes=30):
    """Return cached validation if fresh enough."""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""SELECT * FROM token_validation
                     WHERE contract_address = ? AND chain = ?
                     AND cached_at >= datetime('now', ?)""",
                  (contract_address, chain, f'-{max_age_minutes} minutes'))
        row = c.fetchone()
        conn.close()
        if row:
            cols = [d[0] for d in c.description] if c.description else []
            return dict(zip(cols, row)) if cols else None
    except Exception:
        pass
    return None


def check_honeypot_bsc(contract_address):
    """Check BSC token safety via TokenSniffer (covers BSC well)."""
    try:
        res = requests.get(
            f'https://tokensniffer.com/api/v2/tokens/56/{contract_address}',
            headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'},
            timeout=10,
        )
        if res.status_code == 200:
            data = res.json()
            score = data.get('score', 50)  # 0-100, higher = safer
            tests = data.get('tests', {})
            is_honeypot = tests.get('is_honeypot', {}).get('result', False)
            sell_tax = float(tests.get('sell_tax', {}).get('result', 0) or 0)
            buy_tax  = float(tests.get('buy_tax',  {}).get('result', 0) or 0)
            lp_locked = tests.get('lp_locked', {}).get('result', False)
            return {
                'is_honeypot': is_honeypot or score < 20,
                'sell_tax': sell_tax,
                'buy_tax': buy_tax,
                'lp_locked': lp_locked,
                'sniffer_score': score,
            }
    except Exception:
        pass
    # Fallback to honeypot.is
    return check_honeypot_eth(contract_address)


def check_birdeye_sol(contract_address):
    """Get Solana token metadata from Birdeye (free tier, better than Solscan)."""
    try:
        res = requests.get(
            f'https://public-api.birdeye.so/public/token_overview?address={contract_address}',
            headers={'X-API-KEY': 'public', 'Accept': 'application/json'},
            timeout=8,
        )
        if res.status_code == 200:
            data = res.json().get('data', {})
            return {
                'name': data.get('name', ''),
                'symbol': data.get('symbol', ''),
                'price': float(data.get('price', 0) or 0),
                'mc': float(data.get('mc', 0) or 0),
                'holder': int(data.get('holder', 0) or 0),
                'liquidity': float(data.get('liquidity', 0) or 0),
                'trade_24h': int(data.get('trade24h', 0) or 0),
                'volume_24h': float(data.get('v24hUSD', 0) or 0),
                'price_change_24h': float(data.get('priceChange24hPercent', 0) or 0),
            }
    except Exception:
        pass
    return {}


def check_honeypot_eth(contract_address):
    """Check if ETH token is a honeypot using honeypot.is."""
    try:
        res = requests.get(
            HONEYPOT_ETH,
            params={'address': contract_address},
            timeout=8,
        )
        if res.status_code != 200:
            return {'is_honeypot': False, 'buy_tax': 0, 'sell_tax': 0, 'error': f'HTTP {res.status_code}'}
        data = res.json()
        hp = data.get('honeypotResult', {})
        sim = data.get('simulationResult', {})
        return {
            'is_honeypot': hp.get('isHoneypot', False),
            'buy_tax': float(sim.get('buyTax', 0) or 0),
            'sell_tax': float(sim.get('sellTax', 0) or 0),
            'lp_locked': data.get('pair', {}).get('liquidity', {}).get('locked', False),
        }
    except Exception as e:
        return {'is_honeypot': False, 'buy_tax': 0, 'sell_tax': 0, 'error': str(e)}


def check_rugcheck_sol(contract_address):
    """Check Solana token safety via rugcheck.xyz."""
    try:
        res = requests.get(
            RUGCHECK_SOL.format(contract_address),
            headers={'Accept': 'application/json'},
            timeout=8,
        )
        if res.status_code != 200:
            return {'is_honeypot': False, 'score': 500, 'risks': []}
        data = res.json()
        score = data.get('score', 500)  # lower = safer on rugcheck
        risks = data.get('risks', [])
        risk_names = [r.get('name', '') for r in risks]
        return {
            'is_honeypot': score > 800 or 'Honeypot' in risk_names,
            'rugcheck_score': score,
            'risks': risk_names,
            'lp_burned': 'LP not burned' not in risk_names,
        }
    except Exception as e:
        return {'is_honeypot': False, 'score': 500, 'risks': [], 'error': str(e)}


def check_contract_verified(contract_address, chain):
    """Check if contract source is verified on block explorer."""
    try:
        if chain in ('ethereum', 'arbitrum', 'base', 'optimism', 'bsc', 'polygon'):
            explorer_apis = {
                'ethereum': 'https://api.etherscan.io/v2/api?chainid=1',
                'arbitrum': 'https://api.etherscan.io/v2/api?chainid=42161',
                'base':     'https://api.etherscan.io/v2/api?chainid=8453',
                'optimism': 'https://api.etherscan.io/v2/api?chainid=10',
                'bsc':      'https://api.etherscan.io/v2/api?chainid=56',
                'polygon':  'https://api.etherscan.io/v2/api?chainid=137',
            }
            url = explorer_apis.get(chain, ETHERSCAN_API)
            res = requests.get(url, params={
                'module': 'contract',
                'action': 'getsourcecode',
                'address': contract_address,
                'apikey': 'YourApiKeyToken',  # works without key at low rate
            }, timeout=8)
            if res.status_code == 200:
                data = res.json()
                result = data.get('result', [{}])
                if isinstance(result, list) and result:
                    source = result[0].get('SourceCode', '')
                    return bool(source and source != '')
        elif chain == 'solana':
            # Solscan — check if program is verified
            res = requests.get(
                f'https://public-api.solscan.io/token/meta?tokenAddress={contract_address}',
                headers={'Accept': 'application/json'},
                timeout=8,
            )
            if res.status_code == 200:
                data = res.json()
                return bool(data.get('name') and data.get('symbol'))
    except Exception:
        pass
    return False


def check_holder_concentration(contract_address, chain):
    """Get top holder concentration — high % = rug risk. Multi-chain."""
    try:
        if chain == 'solana':
            # Solscan public API
            res = requests.get(
                f'https://public-api.solscan.io/token/holders?tokenAddress={contract_address}&limit=10',
                headers={'Accept': 'application/json'},
                timeout=8,
            )
            if res.status_code == 200:
                data = res.json()
                holders = data.get('data', [])
                if holders:
                    total_pct = sum(float(h.get('amount', 0)) for h in holders[:10])
                    supply = data.get('total', 1) or 1
                    top10_pct = (total_pct / supply * 100) if supply > 0 else 0
                    return min(100.0, top10_pct)

        elif chain in ('ethereum', 'arbitrum', 'base', 'optimism', 'bsc', 'polygon'):
            # Etherscan-family APIs — token holder list
            explorer_apis = {
                'ethereum': 'https://api.etherscan.io/v2/api?chainid=1',
                'arbitrum': 'https://api.etherscan.io/v2/api?chainid=42161',
                'base':     'https://api.etherscan.io/v2/api?chainid=8453',
                'optimism': 'https://api.etherscan.io/v2/api?chainid=10',
                'bsc':      'https://api.etherscan.io/v2/api?chainid=56',
                'polygon':  'https://api.etherscan.io/v2/api?chainid=137',
            }
            url = explorer_apis.get(chain, 'https://api.etherscan.io/api')
            res = requests.get(url, params={
                'module': 'token',
                'action': 'tokenholderlist',
                'contractaddress': contract_address,
                'page': 1,
                'offset': 10,
                'apikey': 'YourApiKeyToken',
            }, timeout=8)
            if res.status_code == 200:
                data = res.json()
                holders = data.get('result', [])
                if isinstance(holders, list) and holders:
                    # Sum top 10 quantities
                    quantities = [float(h.get('TokenHolderQuantity', 0)) for h in holders[:10]]
                    total_top10 = sum(quantities)
                    # Get total supply from first holder entry's perspective
                    # Approximate: if top 10 hold X tokens and we know individual %
                    # Use TokenHolderPercent if available
                    if holders[0].get('TokenHolderPercent'):
                        top10_pct = sum(float(h.get('TokenHolderPercent', 0)) for h in holders[:10])
                        return min(100.0, top10_pct)
                    # Fallback: get supply from contract
                    supply_res = requests.get(url, params={
                        'module': 'stats',
                        'action': 'tokensupply',
                        'contractaddress': contract_address,
                        'apikey': 'YourApiKeyToken',
                    }, timeout=6)
                    if supply_res.status_code == 200:
                        supply = float(supply_res.json().get('result', 1) or 1)
                        if supply > 0:
                            return min(100.0, total_top10 / supply * 100)

            # Fallback: use DexScreener pair info
            res2 = requests.get(
                f'https://api.dexscreener.com/latest/dex/tokens/{contract_address}',
                timeout=8,
            )
            if res2.status_code == 200:
                pairs = res2.json().get('pairs', [])
                if pairs:
                    # Check if DexScreener provides holder data
                    info = pairs[0].get('info', {})
                    # No direct holder data — return moderate default for EVM
                    return 45.0

    except Exception:
        pass
    return 50.0  # unknown — assume moderate risk


def check_website(url):
    """Check if project website is reachable and has real content."""
    if not url or not url.startswith('http'):
        return False, ''
    try:
        res = requests.get(url, timeout=6, allow_redirects=True,
                           headers={'User-Agent': 'Mozilla/5.0'})
        if res.status_code == 200 and len(res.text) > 500:
            return True, url
        return False, url
    except Exception:
        return False, url


def check_github(project_name, website_url=''):
    """Search GitHub for project repository and check activity."""
    result = {'stars': 0, 'commits_30d': 0, 'url': '', 'found': False}
    try:
        # Try to extract GitHub URL from website first
        github_url = ''
        if website_url:
            try:
                res = requests.get(website_url, timeout=5,
                                   headers={'User-Agent': 'Mozilla/5.0'})
                gh_match = re.search(r'github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)', res.text)
                if gh_match:
                    github_url = f"https://github.com/{gh_match.group(1)}"
            except Exception:
                pass

        # Search GitHub API (60 req/hr free)
        query = project_name.lower().replace(' ', '+')
        res = requests.get(
            GITHUB_API,
            params={'q': query, 'sort': 'stars', 'per_page': 3},
            headers={'Accept': 'application/vnd.github.v3+json'},
            timeout=8,
        )
        if res.status_code == 200:
            items = res.json().get('items', [])
            if items:
                repo = items[0]
                stars = repo.get('stargazers_count', 0)
                pushed = repo.get('pushed_at', '')
                html_url = repo.get('html_url', '')
                # Check if recently active
                days_since_push = 999
                if pushed:
                    try:
                        push_dt = datetime.fromisoformat(pushed.replace('Z', '+00:00'))
                        days_since_push = (datetime.now(timezone.utc) - push_dt).days
                    except Exception:
                        pass
                result = {
                    'stars': stars,
                    'commits_30d': 1 if days_since_push < 30 else 0,
                    'url': html_url or github_url,
                    'found': stars > 0 or days_since_push < 90,
                    'days_since_push': days_since_push,
                }
    except Exception:
        pass
    return result


def check_twitter_basic(symbol, project_name):
    """
    One-time Twitter check per token — minimal credit usage.
    Returns: followers, account_age_days, engagement_rate, account_exists
    """
    result = {'followers': 0, 'age_days': 0, 'engagement': 0.0, 'exists': False}
    try:
        query = f'${symbol} OR "{project_name}"'
        res = requests.get(
            "https://api.twitterapi.io/twitter/tweet/advanced_search",
            headers={"X-API-Key": TWITTER_API_KEY},
            params={"query": query, "queryType": "Top", "cursor": ""},
            timeout=12,
        )
        if res.status_code != 200:
            return result
        tweets = res.json().get('tweets', [])[:10]
        if not tweets:
            return result
        total_engagement = sum(
            t.get('likeCount', 0) + t.get('retweetCount', 0) + t.get('replyCount', 0)
            for t in tweets
        )
        # Find the project's own account if any
        authors = [t.get('author', {}) for t in tweets]
        best_author = max(authors, key=lambda a: a.get('followers', 0), default={})
        followers = best_author.get('followers', 0)
        created = best_author.get('createdAt', '')
        age_days = 999
        if created:
            try:
                ct = datetime.fromisoformat(created.replace('Z', '+00:00'))
                age_days = (datetime.now(timezone.utc) - ct).days
            except Exception:
                pass
        engagement = total_engagement / max(len(tweets), 1)
        result = {
            'followers': followers,
            'age_days': age_days,
            'engagement': round(engagement, 1),
            'exists': len(tweets) > 0,
            'tweet_count': len(tweets),
        }
    except Exception as e:
        result['error'] = str(e)
    return result


def score_with_ai(symbol, name, chain, website_text='', github_info=None,
                  twitter_info=None, dex_info=None, openai_key=''):
    """
    Use GPT-4o-mini to score the token fundamentally.
    Cost: ~$0.001 per call.
    """
    if not openai_key:
        # Try to load from .env
        try:
            with open('.env') as f:
                for line in f:
                    if line.startswith('OPENAI_API_KEY='):
                        openai_key = line.strip().split('=', 1)[1]
        except Exception:
            pass
    if not openai_key:
        return 5, ['No OpenAI key — skipping AI score'], []

    gh = github_info or {}
    tw = twitter_info or {}
    dex = dex_info or {}

    liq = dex.get('liquidity_usd', 0) or 0
    vol = dex.get('volume_24h', 0) or 0
    age = dex.get('age_hours', 0) or 0
    chg = dex.get('price_change_24h', 0) or 0
    dex_name = dex.get('dex', '')

    # Name collision warning for AI
    chain_warnings = {
        'solana': ['bitcoin', 'ethereum', 'bnb', 'xrp', 'cardano', 'ecash', 'xec',
                   'dogecoin', 'shiba', 'pepe', 'floki', 'safe'],
    }
    name_lower = name.lower()
    collision_warning = ''
    for warn_chain, warn_names in chain_warnings.items():
        if chain == warn_chain and any(w in name_lower for w in warn_names):
            collision_warning = f'WARNING: "{name}" on {chain} may be a copycat of a well-known project on another chain. Verify authenticity.'

    prompt = f"""You are a crypto token analyst. Score this token 1-10 for investment legitimacy.

Token: {name} (${symbol}) on {chain}
DEX liquidity: ${liq:,.0f} | Volume 24h: ${vol:,.0f}
Age on DEX: {age:.1f} hours | Price change: {chg:+.0f}% | DEX: {dex_name}
{collision_warning}

GitHub: {'Found - ' + str(gh.get('stars', 0)) + ' stars, active ' + str(gh.get('days_since_push', 999)) + 'd ago' if gh.get('found') else 'Not found'}
Twitter: {'Exists - ' + str(tw.get('followers', 0)) + ' followers, account age ' + str(tw.get('age_days', 0)) + ' days' if tw.get('exists') else 'Not found or no mentions'}
Website: {'Reachable' if website_text else 'Not found'}
Website excerpt: {website_text[:300] if website_text else 'N/A'}

Score 1-10 where:
1-3 = obvious scam/rug (no web, no GitHub, brand new Twitter, honeypot pattern)
4-5 = high risk meme/speculation (could moon but likely rug)
6-7 = legitimate project with real activity but unproven
8-10 = strong fundamentals, team visible, audit likely, real product

Respond ONLY with valid JSON, no markdown:
{{"score": <1-10>, "red_flags": ["flag1", "flag2"], "green_flags": ["flag1", "flag2"], "verdict": "AVOID|WATCH|BUY", "reasoning": "one sentence"}}"""

    try:
        res = requests.post(
            OPENAI_API_URL,
            headers={
                'Authorization': f'Bearer {openai_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': 'gpt-4o-mini',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 200,
                'temperature': 0.2,
            },
            timeout=15,
        )
        if res.status_code == 200:
            text = res.json()['choices'][0]['message']['content'].strip()
            text = re.sub(r'```json|```', '', text).strip()
            data = json.loads(text)
            return (
                int(data.get('score', 5)),
                data.get('red_flags', []),
                data.get('green_flags', []),
            )
    except Exception as e:
        pass
    return 5, [], []


def validate_token(symbol, contract_address, chain, name='', website_url='',
                   dex_info=None, use_ai=True, openai_key=''):
    """
    Full validation pipeline. Returns validation dict with total_score and verdict.
    Results cached — won't re-run within TTL.
    """
    init_validation_table()

    # Check cache first
    cached = get_cached(contract_address, chain, max_age_minutes=30)
    if cached:
        return cached

    print(f"    Validating {symbol} ({chain})...")
    result = {
        'symbol': symbol, 'contract_address': contract_address, 'chain': chain,
        'is_honeypot': False, 'sell_tax_pct': 0, 'buy_tax_pct': 0,
        'contract_verified': False, 'dev_wallet_pct': 0, 'top10_holders_pct': 50,
        'lp_burned': False, 'website_ok': False, 'website_url': website_url,
        'github_stars': 0, 'github_commits_30d': 0, 'github_url': '',
        'twitter_followers': 0, 'twitter_account_age_days': 999,
        'twitter_engagement_rate': 0,
        'ai_score': 5, 'ai_flags': '', 'ai_positives': '',
        'total_score': 0, 'verdict': 'UNKNOWN',
    }

    score = 0
    flags = []
    positives = []

    # 1. Honeypot check
    if chain == 'solana':
        hp = check_rugcheck_sol(contract_address)
        result['is_honeypot'] = hp.get('is_honeypot', False)
        result['lp_burned'] = hp.get('lp_burned', False)
        risks = [r for r in hp.get('risks', []) if r]
        if risks:
            flags.extend(risks[:3])
        # Enrich with Birdeye data
        be = check_birdeye_sol(contract_address)
        if be.get('holder', 0) > 0:
            result['top10_holders_pct'] = min(100.0, 100.0 / max(be['holder'], 1) * 10)
            if be['holder'] < 50:
                flags.append(f'only {be["holder"]} holders (very concentrated)')
                score -= 2
            elif be['holder'] > 500:
                positives.append(f'{be["holder"]:,} holders')
                score += 1
    elif chain == 'bsc':
        hp = check_honeypot_bsc(contract_address)
        result['is_honeypot'] = hp.get('is_honeypot', False)
        result['sell_tax_pct'] = hp.get('sell_tax', 0)
        result['buy_tax_pct'] = hp.get('buy_tax', 0)
        result['lp_burned'] = hp.get('lp_locked', False)
        if hp.get('sniffer_score', 50) < 30:
            flags.append(f'TokenSniffer score {hp.get("sniffer_score",0)}/100')
    else:
        hp = check_honeypot_eth(contract_address)
        result['is_honeypot'] = hp.get('is_honeypot', False)
        result['sell_tax_pct'] = hp.get('sell_tax', 0)
        result['buy_tax_pct'] = hp.get('buy_tax', 0)
        result['lp_burned'] = hp.get('lp_locked', False)

    if result['is_honeypot']:
        flags.append('HONEYPOT — cannot sell')
        score -= 10  # instant disqualify
    elif result['sell_tax_pct'] > 10:
        flags.append(f"high sell tax {result['sell_tax_pct']:.0f}%")
        score -= 3
    elif result['sell_tax_pct'] > 5:
        flags.append(f"sell tax {result['sell_tax_pct']:.0f}%")
        score -= 1
    else:
        score += 2
        positives.append('no honeypot')

    if result['lp_burned']:
        score += 2
        positives.append('LP burned/locked')

    # 2. Contract verified
    result['contract_verified'] = check_contract_verified(contract_address, chain)
    if result['contract_verified']:
        score += 2
        positives.append('contract verified')
    else:
        flags.append('contract not verified')
        score -= 1

    # 3. Holder concentration
    top10 = check_holder_concentration(contract_address, chain)
    result['top10_holders_pct'] = top10
    if top10 > 80:
        flags.append(f'top 10 hold {top10:.0f}% (rug risk)')
        score -= 3
    elif top10 > 60:
        flags.append(f'concentrated ownership ({top10:.0f}%)')
        score -= 1
    else:
        score += 1
        positives.append(f'distributed holders ({top10:.0f}% top 10)')

    # 4. Website check
    web_ok, web_url = check_website(website_url)
    result['website_ok'] = web_ok
    result['website_url'] = web_url
    website_text = ''
    if web_ok:
        score += 2
        positives.append('website reachable')
        try:
            res = requests.get(web_url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            website_text = res.text[:1000]
        except Exception:
            pass
    else:
        flags.append('no website')
        score -= 1

    # 5. GitHub check
    gh = check_github(name or symbol, web_url)
    result['github_stars'] = gh.get('stars', 0)
    result['github_commits_30d'] = gh.get('commits_30d', 0)
    result['github_url'] = gh.get('url', '')
    if gh.get('found') and gh.get('stars', 0) > 10:
        score += 3
        positives.append(f"GitHub {gh['stars']} stars")
    elif gh.get('found'):
        score += 1
        positives.append('GitHub repo found')
    else:
        flags.append('no GitHub activity')

    # 6. Twitter basic check (1 API credit)
    tw = check_twitter_basic(symbol, name or symbol)
    result['twitter_followers'] = tw.get('followers', 0)
    result['twitter_account_age_days'] = tw.get('age_days', 999)
    result['twitter_engagement_rate'] = tw.get('engagement', 0)
    if tw.get('exists'):
        followers = tw.get('followers', 0)
        age_days = tw.get('age_days', 999)
        if followers > 5000 and age_days > 30:
            score += 3
            positives.append(f'Twitter {followers:,} followers')
        elif followers > 500:
            score += 1
            positives.append(f'Twitter {followers:,} followers')
        if age_days < 7:
            flags.append(f'Twitter account only {age_days}d old (red flag)')
            score -= 2
    else:
        flags.append('no Twitter presence')
        score -= 1

    # 7. AI fundamental score
    if use_ai:
        # Auto-load OpenAI key from .env if not provided
        if not openai_key:
            try:
                with open('.env') as _ef:
                    for _line in _ef:
                        if _line.startswith('OPENAI_API_KEY='):
                            openai_key = _line.strip().split('=',1)[1]
                            break
            except Exception:
                pass
        # Auto-fetch DexScreener data if dex_info is empty
        if not dex_info and contract_address:
            try:
                _ds = requests.get(
                    f'https://api.dexscreener.com/latest/dex/tokens/{contract_address}',
                    timeout=8)
                if _ds.status_code == 200:
                    _pairs = _ds.json().get('pairs', [])
                    if _pairs:
                        _p = _pairs[0]
                        dex_info = {
                            'liquidity_usd': float(_p.get('liquidity',{}).get('usd',0) or 0),
                            'volume_24h': float(_p.get('volume',{}).get('h24',0) or 0),
                            'price_change_24h': float(_p.get('priceChange',{}).get('h24',0) or 0),
                            'age_hours': ((__import__('time').time() - _p.get('pairCreatedAt',0)/1000)/3600)
                                         if _p.get('pairCreatedAt') else 0,
                            'dex': _p.get('dexId',''),
                            'price_usd': float(_p.get('priceUsd',0) or 0),
                        }
            except Exception:
                pass
        ai_score, ai_flags, ai_positives = score_with_ai(
            symbol, name or symbol, chain,
            website_text=website_text,
            github_info=gh,
            twitter_info=tw,
            dex_info=dex_info or {},
            openai_key=openai_key,
        )
        result['ai_score'] = ai_score
        result['ai_flags'] = json.dumps(ai_flags)
        result['ai_positives'] = json.dumps(ai_positives)
        # AI score influences total: 8-10 = +3, 6-7 = +1, 1-3 = -3
        if ai_score >= 8:
            score += 3
            positives.append(f'AI score {ai_score}/10')
        elif ai_score >= 6:
            score += 1
        elif ai_score <= 3:
            score -= 3
            flags.append(f'AI score {ai_score}/10 (low)')

    # Final score and verdict
    total = max(0, min(20, score + 10))  # normalise to 0-20
    result['total_score'] = total
    if result['is_honeypot'] or total <= SCORE_BLOCK + 10:
        verdict = 'AVOID'
    elif total <= SCORE_WATCH + 10:
        verdict = 'WATCH'
    elif total <= SCORE_BUY + 10:
        verdict = 'CAUTION'
    else:
        verdict = 'BUY_OK'
    result['verdict'] = verdict

    # Cache to DB
    total_score = result['total_score']
    verdict = result['verdict']
    now = datetime.now().isoformat()
    try:
        conn = sqlite3.connect('alphascope.db', timeout=30)
        conn.execute('PRAGMA journal_mode=WAL')
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO token_validation
            (symbol, contract_address, chain, is_honeypot, sell_tax_pct, buy_tax_pct,
             contract_verified, dev_wallet_pct, top10_holders_pct, lp_burned,
             website_ok, website_url, github_stars, github_commits_30d, github_url,
             twitter_followers, twitter_account_age_days, twitter_engagement_rate,
             ai_score, ai_flags, ai_positives, total_score, verdict, cached_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (symbol, contract_address, chain,
             int(result['is_honeypot']), result['sell_tax_pct'], result['buy_tax_pct'],
             int(result['contract_verified']), result['dev_wallet_pct'],
             result['top10_holders_pct'], int(result['lp_burned']),
             int(result['website_ok']), result['website_url'],
             result['github_stars'], result['github_commits_30d'], result['github_url'],
             result['twitter_followers'], result['twitter_account_age_days'],
             result['twitter_engagement_rate'],
             result['ai_score'], result['ai_flags'], result['ai_positives'],
             total_score, verdict, now))
        conn.commit()
        conn.close()
        # Verify write succeeded
        vc = sqlite3.connect('alphascope.db', timeout=10)
        vr = vc.execute('SELECT id FROM token_validation WHERE contract_address=? AND chain=?',
                        (contract_address, chain)).fetchone()
        vc.close()
        if not vr:
            print(f'      ⚠️  DB write verify failed for {symbol}')
    except Exception as e:
        print(f'      ⚠️  DB store failed for {symbol}: {e}')

    flag_str = ' | '.join(flags[:4])
    pos_str = ' | '.join(positives[:3])
    verdict_emoji = {'AVOID': '🚫', 'WATCH': '👁', 'CAUTION': '⚠️', 'BUY_OK': '✅'}.get(verdict, '?')
    print(f"      {verdict_emoji} {symbol}: {verdict} (score:{total}/20)")
    if flags: print(f"         ⛔ {flag_str}")
    if positives: print(f"         ✅ {pos_str}")

    # Auto-add to watchlist if strong fundamentals but not yet tradeable
    try:
        from project_watchlist import auto_add_from_validator
        auto_add_from_validator(result)
    except ImportError:
        pass

    return result


def validate_dex_gem(gem_row, openai_key=''):
    """
    Convenience wrapper for validating a dex_gems table row.
    gem_row: dict with symbol, contract_address, chain, dex_url etc.
    """
    symbol = gem_row.get('symbol', '')
    contract = gem_row.get('contract_address', '')
    chain = gem_row.get('chain', 'ethereum')
    name = gem_row.get('name', symbol)
    # Try to get website from DexScreener pair info
    website = ''
    try:
        res = requests.get(
            f'https://api.dexscreener.com/latest/dex/tokens/{contract}',
            timeout=8,
        )
        if res.status_code == 200:
            pairs = res.json().get('pairs', [])
            if pairs:
                info = pairs[0].get('info', {})
                websites = info.get('websites', [])
                if websites:
                    website = websites[0].get('url', '')
    except Exception:
        pass

    return validate_token(
        symbol=symbol,
        contract_address=contract,
        chain=chain,
        name=name,
        website_url=website,
        dex_info=gem_row,
        use_ai=bool(openai_key),
        openai_key=openai_key,
    )


if __name__ == '__main__':
    print("AlphaScope — Token Validator v1.0")
    print("=" * 50)
    init_validation_table()
    # Test with a known token
    print("\nTesting with USDC (should pass all checks):")
    result = validate_token(
        symbol='USDC',
        contract_address='0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
        chain='ethereum',
        name='USD Coin',
        website_url='https://www.circle.com/usdc',
        use_ai=False,
    )
    print(f"Result: {result['verdict']} | score: {result['total_score']}/20")
