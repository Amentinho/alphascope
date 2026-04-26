"""
Update simulation to reflect real portfolio structure:
- ETH chain: NO new cash — only manages existing LINK+ETH positions via signals
- SOL/BSC/BASE/ARB: $200 native trading capital each for new gem buys
- Real portfolio positions imported and tracked with real entry prices
"""
import ast, sqlite3

# ── simulation.py — update chains, balances, and portfolio seeding ────────────
with open('simulation.py', 'r') as f:
    s = f.read()

# Update chains and starting balances
old_chains = (
    "CHAINS = ['ethereum', 'solana', 'bsc', 'base']\n"
    "NATIVE_TOKENS = {\n"
    "    'ethereum': ('ETH',  'ethereum'),\n"
    "    'solana':   ('SOL',  'solana'),\n"
    "    'bsc':      ('BNB',  'binancecoin'),\n"
    "    'base':     ('ETH',  'ethereum'),\n"
    "}"
)
new_chains = (
    "# Chains with trading capital (native tokens)\n"
    "# ETH is excluded — we use real portfolio positions there, no extra cash\n"
    "CHAINS = ['solana', 'bsc', 'base', 'arbitrum']\n"
    "NATIVE_TOKENS = {\n"
    "    'solana':   ('SOL',  'solana'),\n"
    "    'bsc':      ('BNB',  'binancecoin'),\n"
    "    'base':     ('ETH',  'ethereum'),\n"
    "    'arbitrum': ('ETH',  'ethereum'),\n"
    "}\n"
    "# Real portfolio positions on ETH — signals only, no new cash\n"
    "REAL_PORTFOLIO = {\n"
    "    'ethereum': [\n"
    "        {'symbol': 'LINK', 'coin_id': 'chainlink', 'amount': 90.9252,\n"
    "         'entry_price': 9.33, 'chain': 'ethereum'},\n"
    "        {'symbol': 'ETH',  'coin_id': 'ethereum',  'amount': 0.0338,\n"
    "         'entry_price': 2333.18, 'chain': 'ethereum'},\n"
    "    ],\n"
    "    'bitcoin': [\n"
    "        {'symbol': 'BTC', 'coin_id': 'bitcoin', 'amount': 0.1,\n"
    "         'entry_price': 75000, 'chain': 'bitcoin'},\n"
    "    ],\n"
    "    'solana': [\n"
    "        {'symbol': 'SOL', 'coin_id': 'solana', 'amount': 20,\n"
    "         'entry_price': 85, 'chain': 'solana'},\n"
    "    ],\n"
    "    'arbitrum': [\n"
    "        {'symbol': 'HYPE', 'coin_id': 'hyperliquid', 'amount': 10,\n"
    "         'entry_price': 38, 'chain': 'arbitrum'},\n"
    "    ],\n"
    "}"
)
if old_chains in s:
    s = s.replace(old_chains, new_chains)
    print("✅ Chains updated — ETH excluded from trading capital")
else:
    print("❌ chains block not matched")

# Update SimPortfolio.__init__ to seed real portfolio positions
old_init = (
    "    def __init__(self, sim_id, starting_usd=STARTING_BALANCE_USD):\n"
    "        self.sim_id = sim_id\n"
    "        self.cash = {chain: starting_usd for chain in CHAINS}\n"
    "        self.holdings = {}  # symbol -> {amount, buy_price, chain, buy_time, source}\n"
    "        self.trades = []\n"
    "        self.starting_total = starting_usd * len(CHAINS)"
)
new_init = (
    "    def __init__(self, sim_id, starting_usd=STARTING_BALANCE_USD):\n"
    "        self.sim_id = sim_id\n"
    "        # Trading capital — native tokens on each chain\n"
    "        self.cash = {chain: starting_usd for chain in CHAINS}\n"
    "        # ETH chain: no trading cash, only real portfolio\n"
    "        self.cash['ethereum'] = 0\n"
    "        self.holdings = {}  # symbol -> {amount, buy_price, chain, buy_time, source}\n"
    "        self.trades = []\n"
    "        # Seed real portfolio positions (tracked but not counted as cash)\n"
    "        self._seed_real_portfolio()\n"
    "        # Starting total = trading capital + real portfolio value\n"
    "        real_value = self._get_real_portfolio_value()\n"
    "        self.starting_total = starting_usd * len(CHAINS) + real_value\n"
    "        self.starting_real_value = real_value\n"
    "        self.starting_trading_capital = starting_usd * len(CHAINS)"
)
if old_init in s:
    s = s.replace(old_init, new_init)
    print("✅ SimPortfolio init updated with real portfolio seeding")
else:
    print("❌ init block not matched")

# Add _seed_real_portfolio and _get_real_portfolio_value methods
old_can_buy = "    def can_buy(self, chain, usd_amount):"
new_methods = (
    "    def _seed_real_portfolio(self):\n"
    "        \"\"\"Seed holdings with real portfolio positions for signal tracking.\"\"\"\n"
    "        for chain, positions in REAL_PORTFOLIO.items():\n"
    "            for pos in positions:\n"
    "                key = f\"{pos['symbol']}_{pos['chain']}\"\n"
    "                self.holdings[key] = {\n"
    "                    'symbol': pos['symbol'],\n"
    "                    'chain': pos['chain'],\n"
    "                    'amount': pos['amount'],\n"
    "                    'buy_price': pos['entry_price'],\n"
    "                    'buy_time': 'real_portfolio',\n"
    "                    'usd_spent': pos['amount'] * pos['entry_price'],\n"
    "                    'source': 'real_portfolio',\n"
    "                    'is_real': True,  # flag — don't count as sim trade\n"
    "                }\n"
    "\n"
    "    def _get_real_portfolio_value(self):\n"
    "        \"\"\"Get current value of real portfolio positions.\"\"\"\n"
    "        total = 0\n"
    "        for chain, positions in REAL_PORTFOLIO.items():\n"
    "            for pos in positions:\n"
    "                price = get_current_price(pos['symbol']) or pos['entry_price']\n"
    "                total += pos['amount'] * price\n"
    "        return total\n"
    "\n"
    "    def can_buy(self, chain, usd_amount):"
)
if old_can_buy in s:
    s = s.replace(old_can_buy, new_methods)
    print("✅ _seed_real_portfolio() and _get_real_portfolio_value() added")
else:
    print("❌ can_buy not matched")

# Update get_total_value to include real portfolio
old_total = (
    "    def get_total_value(self, prices=None):\n"
    "        \"\"\"Calculate total portfolio value.\"\"\"\n"
    "        total = sum(self.cash.values())\n"
    "        for key, pos in self.holdings.items():\n"
    "            symbol = pos['symbol']\n"
    "            chain = pos['chain']\n"
    "            price = 0\n"
    "            if prices and symbol in prices:\n"
    "                price = prices[symbol]\n"
    "            else:\n"
    "                price = get_current_price(symbol) or pos['buy_price']\n"
    "            total += pos['amount'] * price\n"
    "        return total"
)
new_total = (
    "    def get_total_value(self, prices=None):\n"
    "        \"\"\"Calculate total portfolio value (trading capital + real portfolio).\"\"\"\n"
    "        total = sum(self.cash.values())\n"
    "        for key, pos in self.holdings.items():\n"
    "            symbol = pos['symbol']\n"
    "            price = get_current_price(symbol) or pos['buy_price']\n"
    "            total += pos['amount'] * price\n"
    "        return total\n"
    "\n"
    "    def get_trading_value(self):\n"
    "        \"\"\"Trading capital only — excludes real portfolio.\"\"\"\n"
    "        total = sum(self.cash.values())\n"
    "        for key, pos in self.holdings.items():\n"
    "            if pos.get('is_real'):\n"
    "                continue\n"
    "            price = get_current_price(pos['symbol']) or pos['buy_price']\n"
    "            total += pos['amount'] * price\n"
    "        return total"
)
if old_total in s:
    s = s.replace(old_total, new_total)
    print("✅ get_total_value updated, get_trading_value added")
else:
    print("❌ get_total_value not matched")

# Update print_status to show real portfolio separately
old_print = (
    "        pnl_emoji = '🟢' if s['pnl_usd'] >= 0 else '🔴'\n"
    "        print(f\"\\n  {'='*50}\")\n"
    "        print(f\"  SIM {self.sim_id} | {datetime.now().strftime('%H:%M:%S')}\")\n"
    "        print(f\"  Starting: ${s['starting_usd']:.2f} → Current: ${s['current_value']:.2f}\")\n"
    "        print(f\"  {pnl_emoji} P&L: ${s['pnl_usd']:+.2f} ({s['pnl_pct']:+.1f}%)\")"
)
new_print = (
    "        pnl_emoji = '🟢' if s['pnl_usd'] >= 0 else '🔴'\n"
    "        trading_val = self.get_trading_value()\n"
    "        trading_pnl = trading_val - self.starting_trading_capital\n"
    "        real_val = self._get_real_portfolio_value()\n"
    "        real_pnl = real_val - self.starting_real_value\n"
    "        print(f\"\\n  {'='*50}\")\n"
    "        print(f\"  SIM {self.sim_id} | {datetime.now().strftime('%H:%M:%S')}\")\n"
    "        print(f\"  Real portfolio: ${real_val:,.2f} ({real_pnl:+.2f} since start)\")\n"
    "        print(f\"  Trading capital: ${trading_val:.2f} | Start: ${self.starting_trading_capital:.2f}\")\n"
    "        print(f\"  {pnl_emoji} Trading P&L: ${trading_pnl:+.2f} ({trading_pnl/max(self.starting_trading_capital,1)*100:+.1f}%)\")"
)
if old_print in s:
    s = s.replace(old_print, new_print)
    print("✅ print_status shows real portfolio vs trading capital separately")
else:
    print("❌ print_status not matched")

# Don't buy more of real portfolio holdings as new gem trades
# (agent can still ACCUMULATE via portfolio signals, but not as DEX gem)
old_skip_real = (
    "        if action in ('BUY', 'ACCUMULATE'):\n"
    "            if f\"{symbol}_{chain}\" in portfolio.holdings:\n"
    "                continue  # already holding\n"
    "            # Store contract address for later price lookup\n"
    "            p['_contract'] = p.get('coin_id', '') if len(p.get('coin_id','')) > 20 else ''"
)
new_skip_real = (
    "        if action in ('BUY', 'ACCUMULATE'):\n"
    "            key = f\"{symbol}_{chain}\"\n"
    "            if key in portfolio.holdings:\n"
    "                existing = portfolio.holdings[key]\n"
    "                if existing.get('is_real'):\n"
    "                    pass  # real position — allow accumulate signal but skip sim buy\n"
    "                continue  # already holding\n"
    "            # Don't buy on ETH chain — no trading cash there\n"
    "            if chain == 'ethereum' and p.get('category') == 'DEX_GEM':\n"
    "                continue  # ETH chain reserved for real portfolio management\n"
    "            # Store contract address for later price lookup\n"
    "            p['_contract'] = p.get('coin_id', '') if len(p.get('coin_id','')) > 20 else ''"
)
if old_skip_real in s:
    s = s.replace(old_skip_real, new_skip_real)
    print("✅ ETH chain blocked for DEX gem buys (real portfolio only)")
else:
    print("❌ skip real block not matched")

with open('simulation.py', 'w') as f:
    f.write(s)
try:
    ast.parse(s)
    print("✅ simulation.py syntax OK")
except SyntaxError as e:
    print(f"❌ {e.lineno}: {e.msg}")

print("\n" + "="*55)
print("Portfolio structure:")
print("  ETH chain:  LINK $848 + ETH $79 = $927 (signals only)")
print("  SOL chain:  $200 trading capital + 20 SOL held")
print("  BSC chain:  $200 trading capital")
print("  BASE chain: $200 trading capital")
print("  ARB chain:  $200 trading capital + HYPE $380 held")
print("  BTC:        0.1 BTC $7500 (tracked, not traded)")
print(f"  Total tracked: ~$9,907 + $800 trading = ~$10,707")
print("="*55)
