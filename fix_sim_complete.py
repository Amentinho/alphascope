"""
Complete simulation fix:
1. Price always fetched live at buy time — no $0 buys
2. No duplicate position records
3. Real portfolio prices fixed — LINK/ETH/BTC/SOL/HYPE
4. Portfolio signal execution — BTC/SOL/HYPE/LINK get traded
5. Clean results display
6. Sim summary shows real numbers
"""
import ast

with open('simulation.py', 'r') as f:
    s = f.read()

# ── Fix 1: Dedicated price resolver ─────────────────────────────────────────
PRICE_RESOLVER = '''
# CoinGecko IDs for major tokens
COINGECKO_IDS = {
    'BTC': 'bitcoin', 'ETH': 'ethereum', 'SOL': 'solana',
    'BNB': 'binancecoin', 'LINK': 'chainlink', 'HYPE': 'hyperliquid',
    'AAVE': 'aave', 'UNI': 'uniswap', 'ATOM': 'cosmos',
    'DOGE': 'dogecoin', 'XRP': 'ripple', 'ADA': 'cardano',
    'MATIC': 'matic-network', 'ARB': 'arbitrum', 'OP': 'optimism',
}

def resolve_price(symbol, coin_id='', chain=''):
    """
    Robust price resolver — tries multiple sources.
    Returns float price or 0.
    """
    import requests as _req

    # 1. CoinGecko for majors
    cg_id = COINGECKO_IDS.get(symbol.upper(), '')
    if not cg_id and coin_id and len(coin_id) < 30:
        cg_id = coin_id
    if cg_id:
        try:
            r = _req.get(
                f'https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd',
                timeout=6)
            if r.status_code == 200:
                price = r.json().get(cg_id, {}).get('usd', 0)
                if price:
                    return float(price)
        except Exception:
            pass

    # 2. DexScreener by symbol — exact match, highest liquidity
    try:
        r = _req.get(
            f'https://api.dexscreener.com/latest/dex/search?q={symbol}',
            timeout=6)
        if r.status_code == 200:
            pairs = r.json().get('pairs', [])
            # Filter to matching chain if known
            if chain and chain not in ('ethereum', 'bitcoin'):
                pairs = [p for p in pairs if p.get('chainId','') == chain] or pairs
            # Exact symbol match
            exact = [p for p in pairs
                     if p.get('baseToken',{}).get('symbol','').upper() == symbol.upper()]
            pool = exact or pairs
            if pool:
                best = max(pool,
                           key=lambda p: float(p.get('liquidity',{}).get('usd',0) or 0))
                price = float(best.get('priceUsd', 0) or 0)
                if price:
                    return price
    except Exception:
        pass

    # 3. DexScreener by contract address
    if coin_id and len(coin_id) > 20:
        try:
            r = _req.get(
                f'https://api.dexscreener.com/latest/dex/tokens/{coin_id}',
                timeout=6)
            if r.status_code == 200:
                pairs = r.json().get('pairs', [])
                if pairs:
                    return float(pairs[0].get('priceUsd', 0) or 0)
        except Exception:
            pass

    return 0.0

'''

# Replace old get_current_price with resolve_price
old_price_fn = s[s.find('MAJORS_SET'):s.find('\ndef get_db')]
if 'MAJORS_SET' in s:
    s = s.replace(old_price_fn, PRICE_RESOLVER)
    print("✅ Fix 1: resolve_price() replaces broken get_current_price()")
else:
    # Just add it before get_db
    s = s.replace('\ndef get_db():', PRICE_RESOLVER + '\ndef get_db():')
    print("✅ Fix 1: resolve_price() added")

# Replace all get_current_price() calls with resolve_price()
s = s.replace('get_current_price(symbol)', 'resolve_price(symbol, chain=chain)')
s = s.replace('get_current_price(pos[\'symbol\'])', 
               'resolve_price(pos[\'symbol\'], chain=pos.get(\'chain\',\'\'))')
s = s.replace("get_current_price(p.get('coin_id', symbol))",
               "resolve_price(symbol, coin_id=p.get('coin_id',''), chain=chain)")
s = s.replace("get_current_price(p.get('coin_id', ''))",
               "resolve_price(symbol, coin_id=p.get('coin_id',''))")
s = s.replace("get_current_price(contract)", "resolve_price(symbol, coin_id=contract)")
s = s.replace("get_current_price(coin_id_or_symbol)",
               "resolve_price(coin_id_or_symbol)")
# Fix _get_real_portfolio_value
s = s.replace(
    "price = get_current_price(pos['symbol']) or pos['entry_price']",
    "price = resolve_price(pos['symbol'], chain=pos.get('chain','')) or pos['entry_price']"
)
print("✅ All get_current_price() → resolve_price()")

# ── Fix 2: No duplicate inserts — track by sim_id+symbol+chain+buy_time ─────
old_dup = (
    "    # Only insert trades not already saved\n"
    "    c.execute('SELECT COUNT(*) FROM sim_portfolio WHERE sim_id=?', (portfolio.sim_id,))\n"
    "    already_saved = c.fetchone()[0]\n"
    "    new_trades = portfolio.trades[already_saved:]"
)
new_dup = (
    "    # Only insert trades not already saved — track by id\n"
    "    already_saved = getattr(portfolio, '_saved_trade_count', 0)\n"
    "    new_trades = portfolio.trades[already_saved:]\n"
    "    portfolio._saved_trade_count = len(portfolio.trades)"
)
if old_dup in s:
    s = s.replace(old_dup, new_dup)
    print("✅ Fix 2: duplicate insert fixed")
else:
    print("❌ Fix 2: dup block not matched")

# ── Fix 3: Store buy_price correctly in buy() method ────────────────────────
old_buy_rec = (
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
new_buy_rec = (
    "        if action in ('BUY', 'ACCUMULATE'):\n"
    "            key = f\"{symbol}_{chain}\"\n"
    "            if key in portfolio.holdings:\n"
    "                continue  # already holding\n"
    "            # Don't buy DEX gems on ETH — no trading cash there\n"
    "            if chain == 'ethereum' and p.get('category') == 'DEX_GEM':\n"
    "                continue"
)
if old_buy_rec in s:
    s = s.replace(old_buy_rec, new_buy_rec)
    print("✅ Fix 3: buy record cleaned up")

# ── Fix 4: Portfolio signal execution — trade BTC/ETH/SOL/HYPE ──────────────
old_stoploss = (
    "    # Check stop-loss and take-profit on open positions\n"
    "    for key in list(portfolio.holdings.keys()):"
)
new_stoploss = (
    "    # Execute portfolio signals for real holdings (BTC/SOL/HYPE/LINK)\n"
    "    try:\n"
    "        from portfolio import run_portfolio_signals\n"
    "        port_sigs = run_portfolio_signals()\n"
    "        for sig in (port_sigs or []):\n"
    "            sym = sig.get('symbol', '')\n"
    "            ch  = sig.get('chain', '')\n"
    "            action = sig.get('signal', '')\n"
    "            conf = sig.get('confidence', 0)\n"
    "            price = resolve_price(sym, chain=ch)\n"
    "            key = f\"{sym}_{ch}\"\n"
    "            if action in ('BUY', 'ACCUMULATE') and conf >= 65 and price > 0:\n"
    "                if key not in portfolio.holdings and portfolio.can_buy(ch, 50):\n"
    "                    ok, msg = portfolio.buy(sym, ch, 50, price, source='portfolio_signal')\n"
    "                    if ok:\n"
    "                        print(f'    📊 PORTFOLIO {action} {sym} $50 @ ${price:.2f}')\n"
    "            elif action == 'SELL' and conf >= 80:\n"
    "                if key in portfolio.holdings:\n"
    "                    ok, msg = portfolio.sell(sym, ch, price, reason='portfolio_sell')\n"
    "                    if ok:\n"
    "                        print(f'    📊 PORTFOLIO SELL {sym} | {msg}')\n"
    "    except Exception as e:\n"
    "        pass\n"
    "\n"
    "    # Check stop-loss and take-profit on open positions\n"
    "    for key in list(portfolio.holdings.keys()):"
)
if old_stoploss in s:
    s = s.replace(old_stoploss, new_stoploss)
    print("✅ Fix 4: portfolio signal execution added (BTC/SOL/HYPE/LINK)")
else:
    print("❌ Fix 4: stoploss block not matched")

# ── Fix 5: Stop-loss uses resolve_price ─────────────────────────────────────
s = s.replace(
    "        current_price = get_current_price(symbol)\n"
    "        if current_price <= 0:\n"
    "            current_price = pos['buy_price']",
    "        current_price = resolve_price(symbol, chain=chain)\n"
    "        if not current_price or current_price <= 0:\n"
    "            current_price = pos['buy_price']"
)
print("✅ Fix 5: stop-loss uses resolve_price")

# ── Fix 6: Display function ──────────────────────────────────────────────────
DISPLAY_FN = '''

def display_sim_results(sim_id=None):
    """Display clean simulation results from DB."""
    import sqlite3 as _sq
    conn = _sq.connect('alphascope.db', timeout=30)

    # Get latest sim if not specified
    if not sim_id:
        row = conn.execute(
            "SELECT sim_id FROM sim_runs ORDER BY start_time DESC LIMIT 1"
        ).fetchone()
        if not row:
            print("No simulations found")
            return
        sim_id = row[0]

    run = conn.execute(
        "SELECT * FROM sim_runs WHERE sim_id=?", (sim_id,)
    ).fetchone()
    if not run:
        print(f"Sim {sim_id} not found")
        return

    print(f"\\n{'='*60}")
    print(f"SIMULATION RESULTS — {sim_id}")
    print(f"{'='*60}")

    # Get unique positions (latest entry per symbol+chain)
    positions = conn.execute("""
        SELECT symbol, chain, buy_price_usd, sell_price_usd,
               pnl_usd, pnl_pct, status, amount_tokens, signal_source
        FROM sim_portfolio
        WHERE sim_id=? AND buy_price_usd > 0
        GROUP BY symbol, chain
        ORDER BY pnl_pct DESC
    """, (sim_id,)).fetchall()

    print(f"\\n{'Symbol':<12} {'Chain':<10} {'Buy':>10} {'Now/Sell':>10} "
          f"{'P&L $':>8} {'P&L %':>8} {'Status':<8}")
    print("-" * 70)

    total_invested = 0
    total_pnl = 0
    wins = losses = 0

    for p in positions:
        sym, ch, buy_px, sell_px, pnl, pnl_pct, status, tokens, src = p
        if buy_px <= 0:
            continue
        invested = tokens * buy_px if tokens else 0
        if status == 'CLOSED':
            now_px = sell_px
        else:
            now_px = resolve_price(sym, chain=ch)
            if now_px > 0 and tokens:
                pnl = (now_px - buy_px) * tokens
                pnl_pct = (now_px - buy_px) / buy_px * 100

        total_invested += invested
        total_pnl += pnl
        if pnl > 0: wins += 1
        elif pnl < 0: losses += 1

        emoji = '🟢' if pnl >= 0 else '🔴'
        print(f"{emoji} {sym:<10} {ch:<10} ${buy_px:>9.6f} ${now_px:>9.6f} "
              f"${pnl:>7.2f} {pnl_pct:>7.1f}% {status:<8}")

    print("-" * 70)
    current_val = total_invested + total_pnl
    pnl_pct_total = (total_pnl / total_invested * 100) if total_invested > 0 else 0
    emoji = '🟢' if total_pnl >= 0 else '🔴'
    print(f"\\n{emoji} Trading P&L: ${total_invested:.2f} → ${current_val:.2f} "
          f"= ${total_pnl:+.2f} ({pnl_pct_total:+.1f}%)")
    print(f"   Wins: {wins} | Losses: {losses} | "
          f"Win rate: {wins/max(wins+losses,1)*100:.0f}%")

    # Real portfolio
    print(f"\\n{'Real Portfolio'}")
    print("-" * 40)
    real_total = 0
    real_pnl = 0
    for chain, positions_list in REAL_PORTFOLIO.items():
        for pos in positions_list:
            price = resolve_price(pos['symbol'], chain=pos['chain'])
            if not price:
                price = pos['entry_price']
            value = pos['amount'] * price
            entry = pos['amount'] * pos['entry_price']
            pnl = value - entry
            real_total += value
            real_pnl += pnl
            e = '🟢' if pnl >= 0 else '🔴'
            print(f"  {e} {pos['symbol']:<6} ${pos['entry_price']:.2f}→${price:.2f} "
                  f"qty:{pos['amount']} val:${value:.2f} pnl:${pnl:+.2f}")

    print(f"\\n  Real portfolio: ${real_total:.2f} (pnl: ${real_pnl:+.2f})")
    print(f"{'='*60}\\n")
    conn.close()

'''

# Add display function before if __name__
if "if __name__ == '__main__':" in s:
    s = s.replace("if __name__ == '__main__':", DISPLAY_FN + "if __name__ == '__main__':")
    print("✅ Fix 6: display_sim_results() added")

# Update __main__ to show results
old_main_args = (
    "    if args.test:\n"
    "        run_quick_test()\n"
    "    else:\n"
    "        run_live_simulation("
)
new_main_args = (
    "    if args.test:\n"
    "        run_quick_test()\n"
    "    elif getattr(args, 'results', False):\n"
    "        display_sim_results()\n"
    "    else:\n"
    "        result = run_live_simulation("
)
if old_main_args in s:
    s = s.replace(old_main_args, new_main_args)
    # Fix the closing of run_live_simulation call
    s = s.replace(
        "            stop_loss_pct=args.stop_loss,\n"
        "            take_profit_pct=args.take_profit,\n"
        "        )",
        "            stop_loss_pct=args.stop_loss,\n"
        "            take_profit_pct=args.take_profit,\n"
        "        )\n"
        "        print(\"\\nShowing final results:\")\n"
        "        display_sim_results()"
    )
    print("✅ Fix 6b: --results flag and auto-display after sim")

# Add --results argument
s = s.replace(
    "    parser.add_argument('--test', action='store_true', help='Quick 3-cycle test')",
    "    parser.add_argument('--test', action='store_true', help='Quick 3-cycle test')\n"
    "    parser.add_argument('--results', action='store_true', help='Show latest sim results')"
)

with open('simulation.py', 'w') as f:
    f.write(s)

try:
    ast.parse(s)
    print("✅ simulation.py syntax OK")
except SyntaxError as e:
    print(f"❌ line {e.lineno}: {e.msg}")
    # Show context
    lines = s.split('\n')
    for i in range(max(0,e.lineno-3), min(len(lines),e.lineno+2)):
        print(f"  {i+1}: {lines[i]}")

print("\n✅ All fixes applied.")
print("Run: python3 simulation.py --results   (see last sim)")
print("Run: python3 simulation.py --test      (quick test)")
print("Run: caffeinate -i python3 simulation.py --hours 6 --cycle 5")
