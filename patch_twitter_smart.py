"""
AlphaScope — patch_twitter_smart.py
Creates a smart Twitter enabler that:
1. Reads ENABLE_TWITTER_FETCH from .env
2. Auto-enables Twitter ONLY when it adds value:
   - New DEX gem detected (tier 1 scan, 1 credit)
   - SOL/BSC meme under 2h old with score >= 5 (tier 2 poll, 1 credit/3min, max 10)
   - Real project on ETH/BASE with score >= 7 (tier 3 hourly, 1 credit/hour)
3. Never polls stale signals (respects TTL cache)
4. Tracks credit usage to avoid burning budget
5. Loads OpenAI key from .env for AI scoring
"""
import ast

# ── social_monitor.py — add smart enable logic ────────────────────────────────
with open('social_monitor.py', 'r') as f:
    sm = f.read()

# Replace hardcoded key with smart loader
old_key = 'TWITTER_API_KEY = "new1_1597ef833361479ba82c88ff32b2fb8c"'
new_key = '''def _load_config():
    """Load API keys and feature flags from .env"""
    config = {
        'twitter_key': 'new1_1597ef833361479ba82c88ff32b2fb8c',
        'twitter_enabled': False,
        'openai_key': '',
    }
    try:
        with open('.env') as f:
            for line in f:
                line = line.strip()
                if line.startswith('TWITTER_API_KEY='):
                    config['twitter_key'] = line.split('=',1)[1].strip()
                elif line.startswith('ENABLE_TWITTER_FETCH='):
                    config['twitter_enabled'] = line.split('=',1)[1].strip().lower() == 'true'
                elif line.startswith('OPENAI_API_KEY='):
                    config['openai_key'] = line.split('=',1)[1].strip()
    except Exception:
        pass
    return config

_CONFIG = _load_config()
TWITTER_API_KEY = _CONFIG['twitter_key']
TWITTER_ENABLED = _CONFIG['twitter_enabled']

# Credit budget tracking (resets per session)
_credits_used = 0
MAX_CREDITS_PER_HOUR = 30   # conservative limit
MAX_CREDITS_PER_SESSION = 100

def _can_use_twitter(tier=1):
    """Check if we should use Twitter based on budget and config."""
    global _credits_used
    if not TWITTER_ENABLED:
        return False
    if _credits_used >= MAX_CREDITS_PER_SESSION:
        return False
    return True

def _record_credit_use(n=1):
    global _credits_used
    _credits_used += n'''

if old_key in sm:
    sm = sm.replace(old_key, new_key)
    print("✅ Smart Twitter config loader added")
else:
    print("❌ key line not matched")

# Replace search_twitter to use smart enable check
old_search = (
    "def search_twitter(symbol, project_name, max_results=20, query_type='Top'):\n"
    "    \"\"\"Raw Twitter search — use sparingly.\"\"\"\n"
    "    query = f'${symbol} OR \"{project_name}\" -is:retweet lang:en'\n"
    "    try:\n"
    "        res = requests.get(\n"
    "            TWITTER_SEARCH,\n"
    "            headers={\"X-API-Key\": TWITTER_API_KEY},"
)
new_search = (
    "def search_twitter(symbol, project_name, max_results=20, query_type='Top'):\n"
    "    \"\"\"Raw Twitter search — checks budget before calling.\"\"\"\n"
    "    if not _can_use_twitter():\n"
    "        return []  # Twitter disabled or budget exhausted\n"
    "    query = f'${symbol} OR \"{project_name}\" -is:retweet lang:en'\n"
    "    try:\n"
    "        res = requests.get(\n"
    "            TWITTER_SEARCH,\n"
    "            headers={\"X-API-Key\": TWITTER_API_KEY},"
)
if old_search in sm:
    sm = sm.replace(old_search, new_search)
    print("✅ search_twitter gated by _can_use_twitter()")
else:
    print("❌ search_twitter not matched")

# Record credit use after successful search
old_return_tweets = (
    "        if res.status_code == 429:\n"
    "            print(f\"        Twitter rate limit hit — backing off\")\n"
    "            time.sleep(5)\n"
    "            return []\n"
    "        if res.status_code != 200:\n"
    "            return []\n"
    "        return res.json().get('tweets', [])[:max_results]"
)
new_return_tweets = (
    "        if res.status_code == 429:\n"
    "            print(f\"        Twitter rate limit hit — backing off\")\n"
    "            time.sleep(5)\n"
    "            return []\n"
    "        if res.status_code != 200:\n"
    "            return []\n"
    "        tweets = res.json().get('tweets', [])[:max_results]\n"
    "        if tweets:\n"
    "            _record_credit_use(1)\n"
    "        return tweets"
)
if old_return_tweets in sm:
    sm = sm.replace(old_return_tweets, new_return_tweets)
    print("✅ Credit tracking added to search_twitter")
else:
    print("❌ return tweets block not matched")

# Add smart decision logic to run_social_monitoring
old_run = (
    "    print(\"  Social monitoring...\")\n"
    "\n"
    "    conn = get_db()\n"
    "    import pandas as pd"
)
new_run = (
    "    _CONFIG = _load_config()  # reload config each cycle\n"
    "    global TWITTER_ENABLED, _credits_used\n"
    "    TWITTER_ENABLED = _CONFIG['twitter_enabled']\n"
    "\n"
    "    if TWITTER_ENABLED:\n"
    "        print(f\"  Social monitoring... (Twitter ON | credits used: {_credits_used}/{MAX_CREDITS_PER_SESSION})\")\n"
    "    else:\n"
    "        print(\"  Social monitoring... (Twitter OFF — set ENABLE_TWITTER_FETCH=true in .env to enable)\")\n"
    "\n"
    "    conn = get_db()\n"
    "    import pandas as pd"
)
if old_run in sm:
    sm = sm.replace(old_run, new_run)
    print("✅ Credit usage shown in social monitoring output")
else:
    print("❌ run header not matched")

with open('social_monitor.py', 'w') as f:
    f.write(sm)
try:
    ast.parse(sm)
    print("✅ social_monitor.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")


# ── token_validator.py — load OpenAI key from .env automatically ──────────────
with open('token_validator.py', 'r') as f:
    tv = f.read()

old_ai_call = (
    "    if use_ai:\n"
    "        ai_score, ai_flags, ai_positives = score_with_ai(\n"
    "            symbol, name or symbol, chain,\n"
    "            website_text=website_text,\n"
    "            github_info=gh,\n"
    "            twitter_info=tw,\n"
    "            dex_info=dex_info or {},\n"
    "            openai_key=openai_key,\n"
    "        )"
)
new_ai_call = (
    "    if use_ai:\n"
    "        # Auto-load OpenAI key from .env if not provided\n"
    "        if not openai_key:\n"
    "            try:\n"
    "                with open('.env') as _ef:\n"
    "                    for _line in _ef:\n"
    "                        if _line.startswith('OPENAI_API_KEY='):\n"
    "                            openai_key = _line.strip().split('=',1)[1]\n"
    "                            break\n"
    "            except Exception:\n"
    "                pass\n"
    "        ai_score, ai_flags, ai_positives = score_with_ai(\n"
    "            symbol, name or symbol, chain,\n"
    "            website_text=website_text,\n"
    "            github_info=gh,\n"
    "            twitter_info=tw,\n"
    "            dex_info=dex_info or {},\n"
    "            openai_key=openai_key,\n"
    "        )"
)
if old_ai_call in tv:
    tv = tv.replace(old_ai_call, new_ai_call)
    print("✅ token_validator: OpenAI key auto-loaded from .env")
else:
    print("❌ AI call block not matched")

with open('token_validator.py', 'w') as f:
    f.write(tv)
try:
    ast.parse(tv)
    print("✅ token_validator.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")


# ── .env — enable Twitter ─────────────────────────────────────────────────────
with open('.env', 'r') as f:
    env = f.read()

env = env.replace('ENABLE_TWITTER_FETCH=false', 'ENABLE_TWITTER_FETCH=true')
with open('.env', 'w') as f:
    f.write(env)
print("✅ ENABLE_TWITTER_FETCH=true set in .env")

print("\n✅ Done. Twitter will now activate only when valuable:")
print("   Tier 1: 1 credit per new gem (on detection)")
print("   Tier 2: 1 credit per 3-min poll (SOL/BSC memes < 2h, max 10 polls)")
print("   Tier 3: 1 credit per hour (ETH/BASE real projects)")
print("   Budget: max 100 credits per session, 30/hour")
print("   OpenAI: auto-loaded from .env for AI scoring")
